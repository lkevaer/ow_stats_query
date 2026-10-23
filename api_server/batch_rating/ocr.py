from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unicodedata

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PLAYERS = 32
MAX_IMAGE_BYTES = 8 * 1024 * 1024
_DIGITS = str.maketrans({"S": "5", "s": "5", "O": "0", "o": "0", "I": "1", "l": "1", "B": "8", "Z": "2"})


def normalize_tags(tags: list[str]) -> list[str]:
    result = []
    for raw in tags:
        tag = unicodedata.normalize("NFKC", str(raw)).strip()
        if not re.fullmatch(r"[^#\s]{1,32}#[0-9]{3,12}", tag):
            raise ValueError(f"BattleTag 格式无效：{tag[:48]}，应为 昵称#数字")
        if tag not in result:
            result.append(tag)
    if not result:
        raise ValueError("没有识别到完整 BattleTag，请上传清晰截图或输入 昵称#数字")
    if len(result) > MAX_PLAYERS:
        raise ValueError(f"每批最多 {MAX_PLAYERS} 位玩家，请分批查询")
    return result


def extract_tags(text: str) -> tuple[list[str], list[dict]]:
    tags, corrections = [], []
    for line in unicodedata.normalize("NFKC", text).splitlines():
        line = re.sub(r"\s+", "", line)
        match = re.search(r"([^#\s,，;；|]{1,32})#([0-9SOoIlsBZ]{3,12})(?![0-9A-Za-z])", line)
        if not match:
            continue
        name, suffix = match.groups()
        corrected = suffix.translate(_DIGITS)
        tag = name.strip("•·[]【】☐□✓") + "#" + corrected
        if tag not in tags:
            tags.append(tag)
        if suffix != corrected:
            corrections.append({"original": match.group(0), "corrected": tag, "reason": "编号中的 OCR 字母按数字校正"})
    return normalize_tags(tags), corrections


async def recognize_image(encoded: str) -> dict:
    if len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 256:
        raise ValueError("图片不能超过 8 MB")
    try:
        raw = base64.b64decode(encoded.split(",", 1)[-1], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("图片不是有效的 Base64 数据") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("图片不能超过 8 MB")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > 20_000_000:
                raise ValueError("图片像素过大，请裁剪至账号列表")
            image = ImageOps.exif_transpose(image).convert("RGB")
            scale = min(3.0, 2800 / max(image.size))
            if scale != 1:
                image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.Resampling.LANCZOS)
            prepared = io.BytesIO()
            image.save(prepared, format="PNG")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("无法读取图片，请使用 PNG/JPG/WebP 截图") from exc
    with tempfile.TemporaryDirectory(prefix="ow-rating-ocr-") as temporary:
        path = Path(temporary) / "input.png"
        path.write_bytes(prepared.getvalue())
        if sys.platform == "win32":
            script = Path(__file__).with_name("ocr_windows.ps1")
            command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), "-ImagePath", str(path)]
        else:
            executable = shutil.which("tesseract")
            if not executable:
                raise RuntimeError("中文 OCR 不可用；请安装 Tesseract chi_sim+eng 语言包，或直接输入 BattleTag")
            command = [executable, str(path), "stdout", "-l", "chi_sim+eng", "--psm", "6"]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise RuntimeError("图片识别超时，请裁剪截图后重试")
        if process.returncode:
            raise RuntimeError("中文 OCR 不可用，请安装系统中文识别语言包，或直接输入 BattleTag")
        result = json.loads(stdout.decode("utf-8-sig").strip()) if sys.platform == "win32" else {
            "text": stdout.decode("utf-8"), "engine": "Tesseract", "language": "chi_sim+eng"}
    tags, corrections = extract_tags(result["text"])
    return {"battletags": tags, "corrections": corrections, "text": result["text"], "engine": result["engine"], "language": result["language"]}
