"""Serve one coherent frontend release, even when browsers cached older assets."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path
import re

from fastapi.responses import HTMLResponse


STATIC_DIR = Path(__file__).resolve().parent / "static"
_ASSET_REFERENCE = re.compile(r'(?P<attribute>(?:href|src))="/static/(?P<name>[\w.-]+\.(?:css|js))"')


def rating_page(static_dir: Path = STATIC_DIR, *, page: str = "batch_rating.html", ocr_enabled: bool = True) -> HTMLResponse:
    html = (static_dir / page).read_text(encoding="utf-8")
    if page == "batch_rating.html" and not ocr_enabled:
        html = re.sub(r'<!-- OCR_INPUT_START -->.*?<!-- OCR_INPUT_END -->', '', html, flags=re.DOTALL)
        html = re.sub(r'<script\b[^>]*src="/static/(?:ocr_crop|batch_rating_ocr)\.js"[^>]*></script>\s*', '', html)
        html = html.replace('class="inputs"', 'class="inputs inputs-manual-only"')
        html = html.replace('2 · 检查账号列表', '输入战网ID')
        html = html.replace('2 · 输入或选择玩家', '输入或选择玩家')
        html = html.replace('请选择截图或输入账号。', '请输入或粘贴战网ID。')
        html = html.replace('输入战网ID、选择常用ID，或上传账号截图。', '输入战网ID或选择常用ID。')
        html = html.replace('截图识别或输入战网ID', '输入战网ID')
        html = html.replace('截图识别多人账号', '输入多个战网ID')
        html = html.replace('识别有误可直接修改。', '支持复制粘贴多个账号。')

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
