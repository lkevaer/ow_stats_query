import asyncio
from contextlib import closing
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from api_server.batch_rating.service import BatchRatingService
from api_server.config_loader import load_config
from api_server.feature_flags import is_ocr_enabled, start_ocr_warmup
from api_server.frontend import rating_page
import api_server.main as main


class OCRFeatureTests(unittest.TestCase):
    def test_default_preserves_ocr_and_env_can_disable_only_one_installation(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("features: {}\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertTrue(is_ocr_enabled(load_config(path)))
            with patch.dict(os.environ, {"OW_OCR_ENABLED": "0"}):
                self.assertFalse(is_ocr_enabled(load_config(path)))
            path.write_text("features:\n  ocr_enabled: false\n", encoding="utf-8")
            with patch.dict(os.environ, {"OW_OCR_ENABLED": "1"}):
                self.assertTrue(is_ocr_enabled(load_config(path)))

    def test_server_page_removes_upload_and_ocr_scripts_but_keeps_manual_query(self):
        default = rating_page().body.decode()
        self.assertIn('id="imageInput"', default)
        self.assertIn('/static/batch_rating_ocr.js', default)
        manual = rating_page(ocr_enabled=False).body.decode()
        for removed in ['id="imageInput"', 'id="cropEditor"', '/static/batch_rating_ocr.js', '/static/ocr_crop.js', '上传或粘贴截图', '截图识别']:
            self.assertNotIn(removed, manual)
        for retained in ['id="tags"', 'id="leisure"', 'id="competitive"', 'id="selfBinding"', 'inputs-manual-only']:
            self.assertIn(retained, manual)

    def test_disabled_api_rejects_images_without_running_ocr_and_accepts_manual_ids(self):
        config = {"features": {"ocr_enabled": False}}
        worker = SimpleNamespace(
            prepare=AsyncMock(return_value={"battletags": ["测试#1234"], "source": "battletags"}),
            start=Mock(return_value={"task_id": "manual-query"}),
        )
        state = SimpleNamespace(config=config, api_client=object(), batch_rating=worker)
        with patch.object(main.app.state, "ow_app", state, create=True), patch.dict(main._config, config), \
                patch("api_server.routers.batch_rating.recognize_image", new_callable=AsyncMock) as recognize:
            with closing(TestClient(main.app)) as client:
                response = client.post('/api/batch-rating/recognize', json={"image_base64": "unused"})
                self.assertEqual(response.status_code, 503)
                self.assertIn('截图识别已关闭', response.text)
                for mode in ['leisure', 'competitive']:
                    for wait in [False, True]:
                        response = client.post('/api/batch-rating/' + mode, json={"image_base64": "unused", "wait": wait})
                        self.assertEqual(response.status_code, 503)
                recognize.assert_not_awaited()
                worker.prepare.assert_not_awaited()
                response = client.post('/api/batch-rating/competitive', json={"battletags": ["测试#1234"]})
                self.assertEqual(response.status_code, 202)
                self.assertEqual(response.json()['data']['task_id'], 'manual-query')
                self.assertNotIn('id="imageInput"', client.get('/').text)
        worker.prepare.assert_awaited_once()

    def test_enabled_recognition_endpoint_remains_available(self):
        state = SimpleNamespace(config={"features": {"ocr_enabled": True}})
        with patch.object(main.app.state, "ow_app", state, create=True), \
                patch("api_server.routers.batch_rating.recognize_image", new_callable=AsyncMock, return_value={"battletags": ["测试#1234"]}) as recognize:
            with closing(TestClient(main.app)) as client:
                response = client.post('/api/batch-rating/recognize', json={"image_base64": "unused"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['data']['battletags'], ['测试#1234'])
        recognize.assert_awaited_once_with('unused')


class OCRFeatureAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_startup_never_schedules_model_warmup(self):
        with patch('api_server.batch_rating.ocr.prepare_ocr', new_callable=AsyncMock) as warmup:
            self.assertIsNone(start_ocr_warmup({"features": {"ocr_enabled": False}}))
            await asyncio.sleep(0)
            warmup.assert_not_awaited()
            task = start_ocr_warmup({})
            await task
            warmup.assert_awaited_once()

    async def test_image_input_cannot_bypass_guard_by_calling_service_directly(self):
        state = SimpleNamespace(config={"features": {"ocr_enabled": False}})
        service = BatchRatingService(state)
        with patch('api_server.batch_rating.service.recognize_image', new_callable=AsyncMock) as recognize:
            with self.assertRaises(HTTPException) as error:
                await service.prepare([], 'unused')
            self.assertEqual(error.exception.status_code, 503)
            recognize.assert_not_awaited()
            prepared = await service.prepare(['测试#1234'], None)
            self.assertEqual(prepared['battletags'], ['测试#1234'])
