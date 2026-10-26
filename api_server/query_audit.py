"""Persist successful BattleTags without credentials or request identifiers."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import re
import shutil
import threading
import unicodedata

MAX_LOG_BYTES = 500_000_000
RETAIN_LOG_BYTES = 400_000_000
DEFAULT_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "successful_battletags.txt"
_WRITE_LOCK = threading.Lock()
logger = logging.getLogger("ow_api_server.query_audit")


def _append_successful_ids(tags) -> None:
    if os.getenv("OW_QUERY_ID_LOG_ENABLED", "1").strip().lower() in {"0", "false", "off", "no"}:
        return
    valid, seen = [], set()
    for raw in tags:
        tag = unicodedata.normalize("NFKC", str(raw or "")).strip()
        if not re.fullmatch(r"[^#\s\x00-\x1f\x7f]{1,32}#[0-9]{3,12}", tag):
            continue
        if tag.casefold() not in seen:
            valid.append(tag)
            seen.add(tag.casefold())
    if not valid:
        return
    stamp = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    records = "".join(stamp + "\t" + tag + "\n" for tag in valid).encode("utf-8")
    path = Path(os.getenv("OW_QUERY_ID_LOG_FILE") or DEFAULT_LOG_PATH)
    with _WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "ab") as output:
            output.write(records)
        size = path.stat().st_size
        if size < MAX_LOG_BYTES:
            return
        temporary = path.with_name(path.name + ".trim.tmp")
        try:
            with path.open("rb") as source:
                offset = max(0, size - RETAIN_LOG_BYTES)
                if offset:
                    source.seek(offset - 1)
                    if source.read(1) != b"\n":
                        while chunk := source.readline(4096):
                            if chunk.endswith(b"\n"):
                                break
                with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


async def record_successful_ids(tags) -> None:
    """Write completed queries outside the event loop; keep query failures separate."""
    try:
        await asyncio.to_thread(_append_successful_ids, tuple(tags))
    except (OSError, UnicodeError):
        logger.exception("Could not persist successful BattleTags; check log directory permissions and disk space")
