from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("overstats.icon_cache_db")

_ICON_CACHE_DB_PATH = None
_ICON_CACHE_TABLE = "icon_cache"
_INIT_LOCK = threading.Lock()
_GLOBAL_WRITE_LOCK = threading.Lock()


def _get_db_path() -> Path:
    global _ICON_CACHE_DB_PATH
    if _ICON_CACHE_DB_PATH is not None:
        return _ICON_CACHE_DB_PATH
    try:
        from overstats.paths import get_overstats_data_dir
        _ICON_CACHE_DB_PATH = get_overstats_data_dir() / "db" / "icon_cache.sqlite3"
    except Exception:
        _ICON_CACHE_DB_PATH = Path.cwd() / "data" / "icon_cache.sqlite3"
    return _ICON_CACHE_DB_PATH


def _get_connection(timeout: float = 30.0) -> sqlite3.Connection:
    path = _get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=timeout, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_icon_cache_db() -> None:
    with _INIT_LOCK:
        conn = _get_connection()
        try:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_ICON_CACHE_TABLE} (
                    url_hash TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    data BLOB NOT NULL,
                    content_type TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_icon_url ON {_ICON_CACHE_TABLE}(url)")
            conn.commit()
        finally:
            conn.close()


def _url_to_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def read_cached_icon_from_db(url: str) -> Optional[bytes]:
    url_hash = _url_to_hash(url)
    conn = _get_connection()
    try:
        cursor = conn.execute(
            f"SELECT data FROM {_ICON_CACHE_TABLE} WHERE url_hash = ? OR url = ?",
            (url_hash, url),
        )
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def write_cached_icon_to_db(url: str, data: bytes, content_type: str = "") -> None:
    url_hash = _url_to_hash(url)
    now = time.time()
    with _GLOBAL_WRITE_LOCK:
        conn = _get_connection()
        try:
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {_ICON_CACHE_TABLE}
                (url_hash, url, data, content_type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (url_hash, url, data, content_type, now, now),
            )
            conn.commit()
        finally:
            conn.close()


def count_icons() -> int:
    conn = _get_connection()
    try:
        cursor = conn.execute(f"SELECT COUNT(*) FROM {_ICON_CACHE_TABLE}")
        row = cursor.fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def clear_icon_cache() -> None:
    with _GLOBAL_WRITE_LOCK:
        conn = _get_connection()
        try:
            conn.execute(f"DELETE FROM {_ICON_CACHE_TABLE}")
            conn.commit()
        finally:
            conn.close()


def sync_filesystem_icons_to_db(batch_size: int = 100) -> dict:
    init_icon_cache_db()
    synced = 0
    skipped = 0
    failed = 0

    try:
        from overstats.src.modules.query_tool.service import ensure_query_tool_assets, get_cached_asset_path
        from overstats.src.modules.query_tool.service import _read_asset_manifest
    except Exception:
        return {"synced": 0, "skipped": 0, "failed": 0, "error": "query_tool module not available"}

    ensure_query_tool_assets()
    manifest = _read_asset_manifest()

    batch = []
    now = time.time()

    for url, info in manifest.items():
        if "error" in info:
            skipped += 1
            continue

        relative_path = info.get("path")
        if not relative_path:
            skipped += 1
            continue

        try:
            local_path = get_cached_asset_path(url, info.get("category", ""))
            if not local_path or not local_path.exists():
                skipped += 1
                continue

            with open(local_path, "rb") as f:
                data = f.read()

            url_hash = _url_to_hash(url)
            batch.append((url_hash, url, data, "", now, now))

            if len(batch) >= batch_size:
                with _GLOBAL_WRITE_LOCK:
                    conn = _get_connection()
                    try:
                        conn.executemany(
                            f"""
                            INSERT OR REPLACE INTO {_ICON_CACHE_TABLE}
                            (url_hash, url, data, content_type, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            batch,
                        )
                        conn.commit()
                    finally:
                        conn.close()
                synced += len(batch)
                batch = []

        except Exception as e:
            failed += 1
            logger.debug(f"Failed to sync icon {url}: {e}")

    if batch:
        with _GLOBAL_WRITE_LOCK:
            conn = _get_connection()
            try:
                conn.executemany(
                    f"""
                    INSERT OR REPLACE INTO {_ICON_CACHE_TABLE}
                    (url_hash, url, data, content_type, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    batch,
                )
                conn.commit()
            finally:
                conn.close()
        synced += len(batch)

    return {
        "synced": synced,
        "skipped": skipped,
        "failed": failed,
        "total_in_db": count_icons(),
    }


def preload_all_icons_to_db() -> dict:
    logger.info("Starting icon preload to database...")
    start = time.time()
    result = sync_filesystem_icons_to_db()
    elapsed = time.time() - start
    logger.info(f"Icon preload completed: {result}, elapsed={elapsed:.2f}s")
    return result


init_icon_cache_db()