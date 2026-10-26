from __future__ import annotations

import asyncio
import base64
import binascii
import csv
from collections import OrderedDict
from copy import deepcopy
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time

from PIL import Image, ImageOps, UnidentifiedImageError
from ..native_work import bounded_thread

from .ocr_candidates import (
    candidates_from_spans,
    extract_tags,
    normalize_tags as normalize_tags,
    vote_candidates,
)
from .ocr_engine import OCRInputError, image_variants, recognize_neural, warmup

MAX_IMAGE_BYTES = 8 * 1024 * 1024
logger = logging.getLogger("ow_api_server.ocr")
CACHE_SECONDS = 300
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
_REQUEST_LOCK = threading.Lock()


def _cached_neural(image, key):
    # Checking again after acquiring the inference slot coalesces identical
    # simultaneous uploads without sharing event-loop-bound asyncio objects.
    with _REQUEST_LOCK:
        now = time.monotonic()
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached and now - cached[0] < CACHE_SECONDS:
                _CACHE.move_to_end(key)
                return deepcopy(cached[1]), True
            _CACHE.pop(key, None)
        result = recognize_neural(image)
        with _CACHE_LOCK:
            _CACHE[key] = (time.monotonic(), deepcopy(result))
            while len(_CACHE) > 16:
                _CACHE.popitem(last=False)
        return result, False


async def prepare_ocr():
    """Warm both local models without delaying unrelated website startup."""
    try:
        await bounded_thread(warmup)
        logger.info(
            "Local Chinese OCR models ready (fast detection + adaptive row verification)."
        )
    except Exception as exc:
        logger.warning(
            "OCR model warmup failed (%s); native fallback remains available.",
            type(exc).__name__,
        )


def decode_image(encoded: str) -> Image.Image:
    if len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 256:
        raise ValueError("图片不能超过 8 MB")
    try:
        raw = base64.b64decode(encoded.split(",", 1)[-1], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("图片不是有效的 Base64 数据") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("图片不能超过 8 MB")
    try:
        with Image.open(io.BytesIO(raw), formats=("PNG", "JPEG", "WEBP")) as image:
            if image.width * image.height > 20_000_000:
                raise ValueError("图片像素过大，请框选账号列表")
            return ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("无法读取图片，请使用 PNG/JPG/WebP 截图") from exc


async def _native_pass(image: Image.Image, directory: Path, pass_id: int) -> dict:
    path = directory / f"input-{pass_id}.png"
    image.save(path, format="PNG")
    if sys.platform == "win32":
        script = Path(__file__).with_name("ocr_windows.ps1")
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-ImagePath",
            str(path),
        ]
    else:
        executable = shutil.which("tesseract")
        if not executable:
            raise RuntimeError(
                "本地识别组件不可用，请重新安装项目依赖，或直接输入 BattleTag"
            )
        command = [
            executable,
            str(path),
            "stdout",
            "-l",
            "chi_sim+eng",
            "--psm",
            "11",
            "tsv",
        ]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=0x08000000 if os.name == "nt" else 0,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=45)
    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise RuntimeError("识别超时，请框选账号列表后重试") from exc
    if process.returncode:
        raise RuntimeError(
            "备用 OCR 不可用，请安装中文识别语言包，或直接输入 BattleTag"
        )
    text = stdout.decode("utf-8-sig").strip()
    if sys.platform == "win32":
        return json.loads(text)
    regions = []
    if text.startswith("level\t"):
        for row in csv.DictReader(io.StringIO(text), delimiter="\t"):
            if not row.get("text", "").strip():
                continue
            x, y, w, h = (float(row[key]) for key in ("left", "top", "width", "height"))
            regions.append(
                {
                    "text": row["text"],
                    "confidence": max(0, float(row["conf"])) / 100,
                    "box": [x, y, x + w, y + h],
                }
            )
        text = "\n".join(r["text"] for r in regions)
    return {
        "text": text,
        "regions": regions,
        "engine": "Tesseract",
        "language": "chi_sim+eng",
    }


async def _recognize_native(image: Image.Image) -> dict:
    spans, texts, last, legacy = [], [], {}, []
    with tempfile.TemporaryDirectory(prefix="ow-rating-ocr-") as temporary:
        for pass_id, (variant, scale) in enumerate(image_variants(image)):
            last = await _native_pass(variant, Path(temporary), pass_id)
            texts.append(last["text"])
            regions = last.get("regions") or []
            if not regions:
                try:
                    tags, changes = extract_tags(last["text"])
                    legacy.append((tags, changes))
                except ValueError:
                    pass
            for span in regions:
                spans.append(
                    {
                        "text": span["text"],
                        "confidence": span.get("confidence", 0.8),
                        "pass": f"native:{pass_id}",
                        "box": [value / scale for value in span["box"]],
                    }
                )
    if spans:
        candidates, corrections = vote_candidates(candidates_from_spans(spans))
        tags = [c["battletag"] for c in candidates]
    elif legacy:
        tags, corrections = legacy[0]
        candidates = [
            {
                "battletag": t,
                "confidence": None,
                "needs_review": True,
                "reasons": [],
                "alternatives": [],
                "agreement": 1,
            }
            for t in tags
        ]
    else:
        raise ValueError("没有识别到完整 BattleTag，请框选账号列表后重试，或手动输入")
    for candidate in candidates:
        candidate["needs_review"] = True
        candidate["reasons"].append("使用系统备用识别，请核对")
    return {
        "battletags": tags,
        "candidates": candidates,
        "corrections": corrections,
        "text": "\n".join(texts),
        "engine": last["engine"],
        "language": last["language"],
        "review_count": len(candidates),
        "passes": 2,
        "warnings": ["精度增强识别暂不可用，已使用系统备用识别；请核对昵称与编号。"],
    }


async def recognize_image(encoded: str) -> dict:
    started = time.perf_counter()
    image = await bounded_thread(decode_image, encoded)
    try:
        return await _recognize_loaded(image, encoded, started)
    finally:
        image.close()


async def _recognize_loaded(image, encoded, started):
    key = hashlib.sha256(encoded.split(",", 1)[-1].encode("ascii")).hexdigest()
    cache_hit = False
    try:
        # The model and screenshots stay on this host; only public model files download.
        result, cache_hit = await bounded_thread(_cached_neural, image, key)
    except OCRInputError:
        # A readable image without a BattleTag is a validation issue, not an
        # unavailable OCR component. Do not hide it behind a fallback error.
        raise
    except Exception as exc:
        logger.warning(
            "Enhanced OCR unavailable (%s); using local fallback.", type(exc).__name__
        )
        result = await _recognize_native(image)
    result["image_size"] = {"width": image.width, "height": image.height}
    if max(image.size) < 500:
        result["warnings"].append(
            "截图尺寸较小，可框选账号列表或使用原始截图，以减少错字。"
        )
    result["cache_hit"] = cache_hit
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result
