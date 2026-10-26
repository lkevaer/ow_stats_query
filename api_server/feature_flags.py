"""Deployment capabilities; local installations keep OCR enabled by default."""
from __future__ import annotations

import asyncio

from fastapi import HTTPException


def is_ocr_enabled(config: dict | None) -> bool:
    return bool(((config or {}).get("features") or {}).get("ocr_enabled", True))


def require_ocr_enabled(config: dict | None) -> None:
    if not is_ocr_enabled(config):
        raise HTTPException(503, "截图识别已关闭，请手动输入战网ID")


def start_ocr_warmup(config: dict | None) -> asyncio.Task | None:
    if not is_ocr_enabled(config):
        return None
    from .batch_rating.ocr import prepare_ocr

    return asyncio.create_task(prepare_ocr(), name="ow-ocr-warmup")
