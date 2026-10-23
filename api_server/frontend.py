"""Serve one coherent frontend release, even when browsers cached older assets."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path
import re

from fastapi.responses import HTMLResponse


STATIC_DIR = Path(__file__).resolve().parent / "static"
_ASSET_REFERENCE = re.compile(r'(?P<attribute>(?:href|src))="/static/(?P<name>[\w.-]+\.(?:css|js))"')


def rating_page(static_dir: Path = STATIC_DIR) -> HTMLResponse:
    html = (static_dir / "batch_rating.html").read_text(encoding="utf-8")

    def versioned(match: re.Match) -> str:
        data = (static_dir / match["name"]).read_bytes()
        digest = hashlib.sha256(data).digest()
        version = digest.hex()[:16]
        integrity = base64.b64encode(digest).decode("ascii")
        return (f'{match["attribute"]}="/static/{match["name"]}?v={version}" '
                f'integrity="sha256-{integrity}" crossorigin="anonymous"')

    return HTMLResponse(
        _ASSET_REFERENCE.sub(versioned, html),
        headers={"Cache-Control": "no-cache, max-age=0, must-revalidate"},
    )
