"""Short-lived raw player snapshots, independent of report IDs and scoring rules."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
import unicodedata

FRESH_SECONDS = 5 * 60
RETAIN_SECONDS = 24 * 60 * 60
CLEANUP_SECONDS = 5 * 60
DEFAULT_CACHE_FILE = Path(__file__).resolve().parents[2] / "data" / "batch_rating_cache.sqlite3"


@dataclass(frozen=True)
class PlayerSnapshot:
    data: dict
    fetched_at: float

    def usable(self, now: float) -> bool:
        return 0 <= now - self.fetched_at < RETAIN_SECONDS

    def fresh(self, now: float) -> bool:
        return 0 <= now - self.fetched_at <= FRESH_SECONDS


class PlayerDataCache:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or os.getenv("OW_RATING_CACHE_FILE") or DEFAULT_CACHE_FILE)
        self._lock = threading.Lock()
        self._initialized = False

    @staticmethod
    def key(battletag: str, mode: str, season: int, limit: int) -> str:
        # Preserve case: the upstream BattleTag search can be case-sensitive.
        tag = unicodedata.normalize("NFKC", battletag).strip()
        raw = json.dumps([1, tag, mode, season, limit], ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute("PRAGMA secure_delete=ON")
            if not self._initialized:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("""CREATE TABLE IF NOT EXISTS player_snapshots (
                    cache_key TEXT PRIMARY KEY,
                    fetched_at REAL NOT NULL,
                    payload TEXT NOT NULL
                )""")
                connection.execute("CREATE INDEX IF NOT EXISTS player_snapshots_age ON player_snapshots(fetched_at)")
                connection.execute("CREATE TABLE IF NOT EXISTS match_snapshots (cache_key TEXT PRIMARY KEY, fetched_at REAL NOT NULL, payload TEXT NOT NULL)")
                connection.execute("CREATE INDEX IF NOT EXISTS match_snapshots_age ON match_snapshots(fetched_at)")
                connection.execute("PRAGMA max_page_count=32768")
                connection.commit()
                if os.name != "nt":
                    self.path.chmod(0o600)
                self._initialized = True
            return connection
        except Exception:
            connection.close()
            raise

    def get(self, key: str) -> PlayerSnapshot | None:
        return self._get("player_snapshots", key)

    def get_match(self, key: str) -> PlayerSnapshot | None:
        return self._get("match_snapshots", key)

    def _get(self, table, key):
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(f"SELECT fetched_at, payload FROM {table} WHERE cache_key=?", (key,)).fetchone()
            if row is None:
                return None
            fetched_at, payload = row
            try:
                snapshot = PlayerSnapshot(json.loads(payload), float(fetched_at))
                if not isinstance(snapshot.data, dict) or not snapshot.usable(time.time()):
                    raise ValueError("Expired or invalid snapshot")
                return snapshot
            except (ValueError, TypeError):
                connection.execute(f"DELETE FROM {table} WHERE cache_key=?", (key,))
                return None

    def put(self, key: str, snapshot: PlayerSnapshot) -> None:
        self._put("player_snapshots", key, snapshot)

    def put_match(self, key: str, snapshot: PlayerSnapshot) -> None:
        self._put("match_snapshots", key, snapshot)

    def _put(self, table, key, snapshot):
        # Serialize before opening a transaction. A failed write leaves the old row intact.
        payload = json.dumps(snapshot.data, ensure_ascii=False, allow_nan=False)
        if len(payload.encode("utf-8")) > 8 * 1024 * 1024:
            raise ValueError("Snapshot exceeds the storage budget")
        if not math.isfinite(snapshot.fetched_at) or not snapshot.usable(time.time()):
            raise ValueError("Cannot save an expired snapshot")
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(f"""INSERT INTO {table}(cache_key, fetched_at, payload) VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET fetched_at=excluded.fetched_at, payload=excluded.payload
                WHERE excluded.fetched_at >= {table}.fetched_at""", (key, snapshot.fetched_at, payload))

    def cleanup(self) -> int:
        now = time.time()
        with self._lock, closing(self._connect()) as connection:
            with connection:
                removed = sum(connection.execute(f"DELETE FROM {table} WHERE fetched_at<=? OR fetched_at>?", (now - RETAIN_SECONDS, now)).rowcount
                              for table in ("player_snapshots", "match_snapshots"))
            if removed:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return removed
