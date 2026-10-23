from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("ow_api_server.task_db")

_TASK_DB_PATH = "data/tasks.sqlite3"
_TASK_TABLE = "tasks"
_INIT_LOCK = threading.Lock()
_DB_CONNECTION_LOCK = threading.Lock()


def _get_connection(timeout: float = 30.0) -> sqlite3.Connection:
    with _DB_CONNECTION_LOCK:
        Path(_TASK_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(_TASK_DB_PATH, timeout=timeout, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn


def init_task_db() -> None:
    with _INIT_LOCK:
        conn = _get_connection()
        try:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_TASK_TABLE} (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'pending',
                    progress_pct REAL NOT NULL DEFAULT 0.0,
                    stage TEXT NOT NULL DEFAULT '',
                    result TEXT,
                    error TEXT,
                    error_code INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    completed_at REAL,
                    endpoint TEXT,
                    params TEXT
                )
                """
            )
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_tasks_status ON {_TASK_TABLE}(status)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_tasks_created ON {_TASK_TABLE}(created_at)")
            conn.commit()
            logger.info("Task database initialized")
        finally:
            conn.close()


def create_task(task_id: str, endpoint: str, params: Dict[str, Any]) -> None:
    conn = _get_connection()
    try:
        conn.execute(
            f"""
            INSERT INTO {_TASK_TABLE}
            (task_id, status, progress_pct, stage, created_at, endpoint, params)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, "pending", 0.0, "", time.time(), endpoint, json.dumps(params)),
        )
        conn.commit()
    finally:
        conn.close()


def update_task_progress(task_id: str, stage: str, progress_pct: float) -> None:
    conn = _get_connection()
    try:
        conn.execute(
            f"""
            UPDATE {_TASK_TABLE}
            SET status = 'running', stage = ?, progress_pct = ?
            WHERE task_id = ?
            """,
            (stage, progress_pct, task_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_task_started(task_id: str) -> None:
    conn = _get_connection()
    try:
        conn.execute(
            f"""
            UPDATE {_TASK_TABLE}
            SET started_at = ?
            WHERE task_id = ?
            """,
            (time.time(), task_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_task_done(task_id: str, result: Dict[str, Any]) -> None:
    conn = _get_connection()
    try:
        conn.execute(
            f"""
            UPDATE {_TASK_TABLE}
            SET status = 'done', progress_pct = 100.0, stage = '完成',
                result = ?, completed_at = ?
            WHERE task_id = ?
            """,
            (json.dumps(result), time.time(), task_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_task_error(task_id: str, error: str, error_code: int = 500) -> None:
    conn = _get_connection()
    try:
        conn.execute(
            f"""
            UPDATE {_TASK_TABLE}
            SET status = 'error', error = ?, error_code = ?, completed_at = ?
            WHERE task_id = ?
            """,
            (error, error_code, time.time(), task_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_connection()
    try:
        cursor = conn.execute(
            f"SELECT * FROM {_TASK_TABLE} WHERE task_id = ?",
            (task_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        result = dict(row)
        if result.get("result"):
            try:
                result["result"] = json.loads(result["result"])
            except Exception:
                result["result"] = None
        if result.get("params"):
            try:
                result["params"] = json.loads(result["params"])
            except Exception:
                result["params"] = {}
        return result
    finally:
        conn.close()


def cleanup_expired_tasks(ttl_seconds: float = 300) -> int:
    conn = _get_connection()
    try:
        cutoff = time.time() - ttl_seconds
        cursor = conn.execute(
            f"DELETE FROM {_TASK_TABLE} WHERE created_at < ?",
            (cutoff,),
        )
        deleted = cursor.rowcount
        conn.commit()
        if deleted > 0:
            logger.info("Cleaned up %d expired tasks", deleted)
        return deleted
    finally:
        conn.close()


def list_tasks() -> list:
    conn = _get_connection()
    try:
        cursor = conn.execute(
            f"SELECT task_id, status, progress_pct, stage, created_at FROM {_TASK_TABLE}"
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def delete_task(task_id: str) -> bool:
    conn = _get_connection()
    try:
        cursor = conn.execute(
            f"DELETE FROM {_TASK_TABLE} WHERE task_id = ?",
            (task_id,),
        )
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()
