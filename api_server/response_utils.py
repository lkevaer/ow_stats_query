from __future__ import annotations

import base64
from typing import Any, Dict, List, NoReturn, Optional

from fastapi import HTTPException


def image_response(rendered: Any, fallback_text: str = "") -> Dict[str, Any]:
    content_b64 = ""
    media_type = "image/png"
    if rendered is not None:
        raw = getattr(rendered, "content", b"")
        if isinstance(raw, (bytes, bytearray)):
            content_b64 = base64.b64encode(bytes(raw)).decode("ascii")
        mt = getattr(rendered, "media_type", None)
        if mt:
            media_type = str(mt).lower()
    return {
        "success": True,
        "data": {
            "type": "image",
            "content": content_b64,
            "media_type": media_type,
            "fallback_text": fallback_text or "",
        },
    }


def text_response(text: str) -> Dict[str, Any]:
    return {
        "success": True,
        "data": {
            "type": "text",
            "content": str(text or ""),
            "media_type": "text/plain",
            "fallback_text": str(text or ""),
        },
    }


def multi_response(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "success": True,
        "data": {
            "type": "multi",
            "items": items,
            "media_type": "application/json",
            "fallback_text": "",
        },
    }


def error_response(message: str, status_code: int = 500, details: Optional[Dict[str, Any]] = None) -> NoReturn:
    error = HTTPException(status_code=status_code, detail=message)
    error.details = details or {}
    raise error
