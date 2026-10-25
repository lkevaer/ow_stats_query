"""Local OCR with one fast detector pass and independent, accurate row checks."""

from __future__ import annotations

import os
from pathlib import Path
import threading

from PIL import Image, ImageFilter, ImageOps, ImageStat

from .ocr_candidates import candidates_from_spans, parse_line, vote_candidates

_ENGINE = None
_VERIFY_ENGINE = None
_LOCK = threading.Lock()
ENGINE_NAME = "RapidOCR PP-OCRv6"


class OCRInputError(ValueError):
    """A valid image is unsuitable or contains no complete public BattleTag."""


def _get_engine(*, verify=False):
    global _ENGINE, _VERIFY_ENGINE
    selected = _VERIFY_ENGINE if verify else _ENGINE
    if selected is None:
        from rapidocr import ModelType, OCRVersion, RapidOCR

        model_dir = Path(
            os.getenv("OW_OCR_MODEL_DIR")
            or Path(os.getenv("OW_API_ROOT", str(Path.cwd()))) / "data" / "ocr_models"
        )
        threads = max(
            1, min(4, int(os.getenv("OW_OCR_THREADS") or 1))
        )
        selected = RapidOCR(
            params={
                "Det.ocr_version": OCRVersion.PPOCRV6,
                "Det.model_type": ModelType.SMALL,
                "Det.limit_type": "max",
                "Det.limit_side_len": 1280,
                "Rec.ocr_version": OCRVersion.PPOCRV6,
                "Rec.model_type": ModelType.MEDIUM if verify else ModelType.SMALL,
                "Global.model_root_dir": str(model_dir.resolve()),
                "Global.log_level": "warning",
                "Global.text_score": 0.35,
                "Global.max_side_len": 2200,
                "EngineConfig.onnxruntime.intra_op_num_threads": threads,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            }
        )
        if verify:
            _VERIFY_ENGINE = selected
        else:
            _ENGINE = selected
    return selected


def image_variants(image: Image.Image):
    # Tiling preserves small lettering in a full-resolution 4K screenshot.
    scale = min(2.5, 2600 / max(image.size))
    scaled = (
        image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
        if scale != 1
        else image.copy()
    )
    gray = ImageOps.grayscale(scaled)
    if ImageStat.Stat(gray).mean[0] < 128:
        gray = ImageOps.invert(gray)
    enhanced = (
        ImageOps.autocontrast(gray, cutoff=0.5)
        .filter(ImageFilter.UnsharpMask(radius=1, percent=115, threshold=3))
        .convert("RGB")
    )
    return [(scaled, scale), (enhanced, scale)]


def image_tiles(image: Image.Image):
    if max(image.size) <= 2200:
        return [(image, (0, 0))]
    step, size = 1488, 2000
    positions = [
        (x, y)
        for y in range(0, image.height, step)
        for x in range(0, image.width, step)
    ]
    if len(positions) > 24:
        raise OCRInputError("截图过长，请先框选账号列表再识别")
    return [
        (
            image.crop((x, y, min(x + size, image.width), min(y + size, image.height))),
            (x, y),
        )
        for x, y in positions
    ]


def _scan(engine, image, tile, offset, tile_index, pass_index, *, enhanced=False):
    variant, scale = image_variants(tile)[1] if enhanced else (tile, 1.0)
    result = engine(variant, use_det=True, use_cls=False, use_rec=True)
    if result.boxes is None or not result.txts:
        return []
    ox, oy = offset
    spans = []
    for box, text, confidence in zip(result.boxes, result.txts, result.scores):
        xs, ys = box[:, 0], box[:, 1]
        clipped = (min(xs) <= 2 and ox > 0) or (
            max(xs) >= variant.width - 2 and ox + tile.width < image.width
        )
        spans.append(
            {
                "text": text,
                "confidence": float(confidence),
                "pass": f"{tile_index}:{pass_index}",
                "clipped": bool(clipped),
                "box": [
                    float(min(xs)) / scale + ox,
                    float(min(ys)) / scale + oy,
                    float(max(xs)) / scale + ox,
                    float(max(ys)) / scale + oy,
                ],
            }
        )
    return spans


def _candidates(records):
    try:
        return vote_candidates(records) if records else ([], [])
    except ValueError as exc:
        raise OCRInputError(str(exc)) from exc


def _unread_tag_span(spans, candidates):
    for span in spans:
        if "#" not in span["text"] and "＃" not in span["text"]:
            continue
        x1, y1, x2, y2 = span["box"]
        if not any(
            min(x2, c["box"][2]) > max(x1, c["box"][0])
            and min(y2, c["box"][3]) > max(y1, c["box"][1])
            for c in candidates
        ):
            return True
    return False


def _same_row_region(left, right):
    x1, y1, x2, y2 = left["box"]
    a1, b1, a2, b2 = right["box"]
    return (
        abs((y1 + y2 - b1 - b2) / 2) < min(y2 - y1, b2 - b1) * 0.65
        and max(0, min(x2, a2) - max(x1, a1)) > min(x2 - x1, a2 - a1) * 0.35
    )


def _verify_rows(image, candidates, pass_id, *, enhanced=True, accurate=True):
    if not candidates:
        return []
    crops = []
    for candidate in candidates:
        x1, y1, x2, y2 = candidate["box"]
        pad = max(3, (y2 - y1) * 0.18)
        crop = image.crop(
            (
                max(0, int(x1 - pad)),
                max(0, int(y1 - pad)),
                min(image.width, round(x2 + pad)),
                min(image.height, round(y2 + pad)),
            )
        )
        crop = crop.resize(
            (max(1, round(crop.width * 48 / max(crop.height, 1))), 48),
            Image.Resampling.LANCZOS,
        )
        if enhanced:
            crop = ImageOps.autocontrast(ImageOps.grayscale(crop), cutoff=0.5)
            if ImageStat.Stat(crop).mean[0] < 128:
                crop = ImageOps.invert(crop)
        crops.append(crop.convert("RGB"))
    engine = _get_engine(verify=accurate)
    # Batch only the detected ID lines through the accurate model, rather than
    # recognizing every label, score and hero name twice in a full screenshot.
    if callable(getattr(engine, "recognize_txt", None)):
        import numpy as np

        result = engine.recognize_txt([np.asarray(crop) for crop in crops])
        results = list(zip(result.txts, result.scores))
    else:
        results = []
        for crop in crops:
            result = engine(crop, use_det=False, use_cls=False, use_rec=True)
            values = list(zip(result.txts or [], result.scores or []))
            results.append(values[0] if values else ("", 0.0))
    records = []
    for index, ((text, confidence), candidate) in enumerate(zip(results, candidates)):
        for parsed in parse_line(text):
            records.append(
                {
                    **parsed,
                    "confidence": float(confidence),
                    "pass": f"{pass_id}:{index}",
                    "box": candidate["box"],
                    "clipped": candidate.get("clipped", False),
                }
            )
    return records


def recognize_neural(image: Image.Image) -> dict:
    tiles = [
        (tile, offset)
        for tile, offset in image_tiles(image)
        if ImageStat.Stat(ImageOps.grayscale(tile)).stddev[0] >= 0.4
    ]
    if not tiles:
        raise OCRInputError(
            "没有识别到完整 BattleTag，请框选账号列表后重试，或手动输入"
        )
    with _LOCK:
        engine = _get_engine()
        spans = []
        for index, (tile, offset) in enumerate(tiles):
            spans.extend(_scan(engine, image, tile, offset, index, 0))
        records = candidates_from_spans(spans)
        candidates, _ = _candidates(records)
        full_passes = 1
        # Keep an enhanced detector pass for missed/fragmented ID text. Clear
        # images instead obtain their second independent reading from the
        # accurate row recognizer, so successful reads are still cross-checked.
        if tiles and (not candidates or _unread_tag_span(spans, candidates)):
            full_passes += 1
            for index, (tile, offset) in enumerate(tiles):
                spans.extend(
                    _scan(engine, image, tile, offset, index, 1, enhanced=True)
                )
            records = candidates_from_spans(spans)
            candidates, _ = _candidates(records)
        verification_passes = 0
        recovered = False
        if not candidates:
            # A fast recognizer can miss '#'. Re-read plausible numeric-name
            # rows with the accurate model before declaring that no ID exists.
            possible = []
            for item in spans:
                if parse_line(item["text"], infer_separator=True):
                    possible.append(
                        {"box": item["box"], "clipped": item.get("clipped", False)}
                    )
            if possible:
                records.extend(_verify_rows(image, possible[:12], "recover"))
                verification_passes += 1
                candidates, _ = _candidates(records)
                recovered = bool(candidates)
        if not candidates:
            raise OCRInputError(
                "没有识别到完整 BattleTag，请框选账号列表后重试，或手动输入"
            )
        if recovered:
            records.extend(
                _verify_rows(image, candidates, "confirm-recovery", enhanced=False)
            )
            verification_passes += 1
            disputed = []
        else:
            quick = _verify_rows(image, candidates, "verify-fast", accurate=False)
            verification_passes += 1
            checked, _ = _candidates(records + quick)
            clear = {
                (c["battletag"], tuple(c["box"]))
                for c in checked
                if not c["needs_review"]
            }
            records.extend(
                r for r in quick if (r["battletag"], tuple(r["box"])) in clear
            )
            disputed = [
                c for c in checked if (c["battletag"], tuple(c["box"])) not in clear
            ][:12]
            if disputed:
                # Do not let repeated fast readings outvote an accurate retry.
                kept, seen = [], set()
                for record in records:
                    region = next(
                        (
                            i
                            for i, candidate in enumerate(disputed)
                            if _same_row_region(record, candidate)
                        ),
                        None,
                    )
                    if region is None or region not in seen:
                        kept.append(record)
                    if region is not None:
                        seen.add(region)
                records = kept
                records.extend(_verify_rows(image, disputed, "verify-accurate"))
                records.extend(
                    _verify_rows(image, disputed, "retry-accurate", enhanced=False)
                )
                verification_passes += 2
        candidates, corrections = _candidates(records)
        return {
            "candidates": candidates,
            "battletags": [c["battletag"] for c in candidates],
            "corrections": corrections,
            "text": "\n".join(s["text"] for s in spans if s["pass"].endswith(":0")),
            "engine": ENGINE_NAME,
            "language": "zh-Hans+en",
            "review_count": sum(c["needs_review"] for c in candidates),
            "warnings": [],
            "passes": full_passes + verification_passes,
            "full_image_passes": full_passes,
            "verification": "adaptive-row-consensus",
        }


def warmup():
    from PIL import ImageDraw, ImageFont

    image = Image.new("RGB", (480, 80), "white")
    font = ImageFont.truetype(
        str(Path(__file__).resolve().parents[2] / "overstats" / "res" / "simhei.ttf"),
        32,
    )
    ImageDraw.Draw(image).text((14, 18), "测试玩家#1234", font=font, fill="black")
    result = recognize_neural(image)
    if "测试玩家#1234" not in result["battletags"]:
        raise RuntimeError("中文OCR模型自检未识别测试账号")
    with _LOCK:
        _verify_rows(image, result["candidates"], "warmup-accurate")
    print("Local Chinese OCR model verified.", flush=True)


if __name__ == "__main__":
    warmup()
