"""Validate OCR failure classification, private in-memory caching and coalescing."""

import asyncio
import base64
from copy import deepcopy
import io
import time
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image
from api_server.batch_rating import ocr
from api_server.batch_rating.ocr_engine import OCRInputError


def encoded_image():
    stream = io.BytesIO()
    Image.new("RGB", (600, 200), "white").save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode()


RESULT = {
    "battletags": ["测试#1234"],
    "candidates": [],
    "corrections": [],
    "text": "测试#1234",
    "engine": "mock",
    "language": "zh-Hans+en",
    "review_count": 0,
    "warnings": [],
    "passes": 2,
}


class OCRRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with ocr._CACHE_LOCK:
            ocr._CACHE.clear()

    async def test_no_tags_is_not_misreported_as_an_unavailable_fallback(self):
        with (
            patch.object(
                ocr, "recognize_neural", side_effect=OCRInputError("没有完整ID")
            ),
            patch.object(ocr, "_recognize_native", new_callable=AsyncMock) as native,
        ):
            with self.assertRaisesRegex(ValueError, "没有完整ID"):
                await ocr.recognize_image(encoded_image())
            native.assert_not_awaited()

    async def test_same_image_is_cached_without_sharing_mutable_result_objects(self):
        with patch.object(
            ocr, "recognize_neural", return_value=deepcopy(RESULT)
        ) as engine:
            first = await ocr.recognize_image(encoded_image())
            first["battletags"].append("额外#5678")
            first["warnings"].append("external change")
            second = await ocr.recognize_image(encoded_image())
        self.assertEqual(engine.call_count, 1)
        self.assertEqual(second["battletags"], ["测试#1234"])
        self.assertNotIn("external change", second["warnings"])
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])

    async def test_simultaneous_identical_uploads_share_one_inference(self):
        def infer(_image):
            time.sleep(0.02)
            return deepcopy(RESULT)

        with patch.object(ocr, "recognize_neural", side_effect=infer) as engine:
            results = await asyncio.gather(
                *(ocr.recognize_image(encoded_image()) for _ in range(4))
            )
        self.assertEqual(engine.call_count, 1)
        self.assertEqual(sum(result["cache_hit"] for result in results), 3)

    async def test_expired_entry_is_recognized_again(self):
        with patch.object(
            ocr, "recognize_neural", side_effect=lambda _image: deepcopy(RESULT)
        ) as engine:
            await ocr.recognize_image(encoded_image())
            with patch.object(ocr, "CACHE_SECONDS", -1):
                result = await ocr.recognize_image(encoded_image())
        self.assertEqual(engine.call_count, 2)
        self.assertFalse(result["cache_hit"])
