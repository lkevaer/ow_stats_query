"""Regression scenarios from the public-readiness audit. No upstream requests."""
import asyncio
from contextlib import closing
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from api_server.batch_rating.player_cache import PlayerDataCache, PlayerSnapshot
from api_server.db import task_db
from api_server.hero_rates import HeroRatesCache, DEFAULT_FILTERS
from api_server.main import app
from api_server.native_work import bounded_thread
from api_server.security import RequestBodyLimit
from api_server.work_queue import WorkQueue
from api_server.routers.stream import _ENDPOINT_DISPATCH
import test_public_operations as fixtures
from fastapi import FastAPI, Request
from api_server.security import is_local


class StorageAndRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_supported_screenshot_formats_reach_decoders(self):
        import base64
        import io
        from PIL import Image
        from api_server.batch_rating.ocr import decode_image
        for kind in ["PNG", "JPEG", "WEBP", "GIF", "TIFF", "BMP"]:
            buffer = io.BytesIO()
            with Image.new("RGB", (40, 40), "white") as source:
                source.save(buffer, format=kind)
            encoded = base64.b64encode(buffer.getvalue()).decode()
            if kind in {"PNG", "JPEG", "WEBP"}:
                with decode_image(encoded) as result:
                    self.assertEqual(result.size, (40, 40))
            else:
                with self.assertRaises(ValueError):
                    decode_image(encoded)

    async def test_real_uvicorn_proxy_processing_cannot_grant_malformed_chain_local_access(self):
        import socket
        import uvicorn
        from types import SimpleNamespace
        for enabled in [False, True]:
            local = FastAPI()
            local.state.ow_app = SimpleNamespace(config={})
            @local.get("/metrics")
            async def metrics(request: Request):
                if not is_local(request):
                    raise HTTPException(403)
                return {"ok": True}
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(local, proxy_headers=enabled, forwarded_allow_ips="127.0.0.1", lifespan="off", log_level="critical"))
            task = asyncio.create_task(server.serve(sockets=[sock]))
            try:
                for _ in range(200):
                    if server.started:
                        break
                    await asyncio.sleep(.005)
                self.assertTrue(server.started)
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
                    self.assertEqual((await client.get("/metrics")).status_code, 200)
                    for value in ["invalid", "invalid, 198.51.100.8", "127.0.0.1", "198.51.100.8"]:
                        self.assertEqual((await client.get("/metrics", headers={"X-Forwarded-For": value})).status_code, 403)
            finally:
                server.should_exit = True
                await asyncio.wait_for(task, 2)
                sock.close()

    async def test_database_page_budget_survives_every_connection_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cache.db"
            cache = PlayerDataCache(path)
            for instance in [cache, cache, PlayerDataCache(path)]:
                with closing(instance._connect()) as db:
                    self.assertEqual(db.execute("PRAGMA max_page_count").fetchone()[0] *
                                     db.execute("PRAGMA page_size").fetchone()[0], 128 * 1024 * 1024)

    async def test_cache_full_keeps_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as folder, patch("api_server.batch_rating.player_cache.MAX_CACHE_BYTES", 32 * 4096):
            cache = PlayerDataCache(Path(folder) / "cache.db")
            stamp = time.time()
            cache.put("old", PlayerSnapshot({"value": "kept"}, stamp))
            import sqlite3
            with self.assertRaises(sqlite3.Error):
                cache.put("large", PlayerSnapshot({"value": "x" * (256 * 1024)}, stamp))
            self.assertEqual(cache.get("old").data, {"value": "kept"})

    async def test_queued_and_running_work_share_one_deadline(self):
        queue = WorkQueue(timeout=.04)
        started = time.monotonic()
        try:
            results = await asyncio.gather(*(queue.submit(asyncio.Event().wait) for _ in range(4)), return_exceptions=True)
            self.assertTrue(all(isinstance(result, HTTPException) and result.status_code == 504 for result in results))
            self.assertLess(time.monotonic() - started, .13)
        finally:
            await queue.close()

    async def test_native_timeout_retains_slot_until_real_worker_ends(self):
        queue = WorkQueue(timeout=.03)
        started, release = threading.Event(), threading.Event()
        def worker():
            started.set(); release.wait(3)
        task = queue.submit(lambda: bounded_thread(worker))
        try:
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(.002)
            self.assertTrue(started.is_set())
            await asyncio.sleep(.05)
            self.assertEqual(queue.status()["active"], 1)
            self.assertFalse(task.done())
        finally:
            release.set()
            values = await asyncio.gather(task, return_exceptions=True)
            await queue.close()
        self.assertEqual(values[0].status_code, 504)
        self.assertEqual(queue.active, 0)

    async def test_upload_admission_rejects_before_reading_another_body(self):
        release = asyncio.Event()
        reads, statuses = [], []
        async def application(scope, receive, send):
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})
        limited = RequestBodyLimit(application, capacity=2)
        async def request(index, content_length=None):
            scope = {"type": "http", "method": "POST", "path": "/", "headers":
                     [] if content_length is None else [(b"content-length", content_length.encode())]}
            async def receive():
                reads.append(index)
                await release.wait()
                return {"type": "http.request", "body": b"{}", "more_body": False}
            async def send(message):
                if message["type"] == "http.response.start":
                    statuses.append((index, message["status"]))
            await limited(scope, receive, send)
        pending = [asyncio.create_task(request(i)) for i in range(2)]
        try:
            while len(reads) < 2:
                await asyncio.sleep(0)
            await request(2)
            await request(3, str(13 * 1024 * 1024))
            self.assertNotIn(2, reads)
            self.assertNotIn(3, reads)
            self.assertIn((2, 503), statuses)
            self.assertIn((3, 413), statuses)
        finally:
            release.set()
            await asyncio.gather(*pending)
        self.assertEqual(limited.active, 0)

    async def test_cold_hero_variants_are_not_recurring_refresh_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = HeroRatesCache(Path(folder), clock=lambda: 1_000_000)
            cold = {**DEFAULT_FILTERS, "rank": "gold"}
            key = cache.cache_key(cold)
            path = Path(folder) / (key + ".json")
            path.write_text(json.dumps({"fetched_at": 900_000, "filters": cold, "heroes": []}))
            scheduled = []
            def schedule(filters):
                scheduled.append(filters)
            try:
                with patch.object(cache, "schedule", side_effect=schedule), patch("api_server.hero_rates.asyncio.sleep", side_effect=asyncio.CancelledError):
                    with self.assertRaises(asyncio.CancelledError):
                        await cache.run()
                self.assertEqual(scheduled, [DEFAULT_FILTERS])
                self.assertTrue(path.exists())
            finally:
                await cache.close()

    async def test_hero_variant_count_and_cold_retention_are_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = HeroRatesCache(Path(folder), clock=lambda: 1_000_000)
            try:
                for index in range(8):
                    path = Path(folder) / (f"{index:064x}.json")
                    path.write_text("{}")
                    import os
                    os.utime(path, (index, index))
                cache.cleanup()
                self.assertEqual(list(Path(folder).glob("*.json")), [])
                for index in range(8):
                    path = Path(folder) / (f"{index:064x}.json")
                    path.write_text("{}")
                with patch("api_server.hero_rates.MAX_VARIANTS", 3):
                    cache.cleanup()
                self.assertEqual(len(list(Path(folder).glob("*.json"))), 3)
            finally:
                await cache.close()


class EndpointBoundaryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.PublicEndpointTests.asyncSetUp
    asyncTearDown = fixtures.PublicEndpointTests.asyncTearDown

    async def test_malformed_proxy_chain_cannot_get_metrics_privileges(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 10)), base_url="http://testserver") as local:
            for chain in ["invalid", "invalid, 198.51.100.8", "127.0.0.1", "198.51.100.8, invalid", ""]:
                self.assertEqual((await local.get("/metrics", headers={"X-Forwarded-For": chain})).status_code, 403)
            self.state.config["security"] = {"trusted_proxy_cidrs": []}
            self.assertEqual((await local.get("/metrics", headers={"X-Forwarded-For": "198.51.100.8"})).status_code, 403)
            self.assertEqual((await local.get("/metrics", headers={"X-Real-IP": "198.51.100.8"})).status_code, 403)

    async def test_same_client_duplicates_reuse_one_job_and_leave_capacity(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def compute(*args, **kwargs):
            entered.set(); await release.wait()
            return {"players": []}
        self.state.batch_rating.query = compute
        try:
            responses = [await self.client.post("/api/batch-rating/competitive", json={"battletags": ["示例#1234"]}) for _ in range(4)]
            self.assertEqual({r.status_code for r in responses}, {202})
            self.assertEqual(len({r.json()["data"]["task_id"] for r in responses}), 1)
            self.assertEqual(len(self.state.work_queue.tasks), 1)
            other = await self.client.post("/api/batch-rating/competitive", json={"battletags": ["其他#1234"]},
                                           headers={"Cookie": "ow_browser_id=" + "b" * 32})
            self.assertEqual(other.status_code, 202)
            own = await self.client.post("/api/batch-rating/competitive", json={"battletags": ["另一个#1234"]})
            self.assertEqual(own.status_code, 429)
        finally:
            release.set()

    async def test_shared_core_never_shares_callers_raw_ocr_text(self):
        worker = self.state.batch_rating
        release = asyncio.Event()
        async def compute(mode, prepared, **kwargs):
            self.assertNotIn("text", prepared)
            await release.wait()
            return {"players": []}
        worker.query = AsyncMock(side_effect=compute)
        first = worker.start("competitive", {"battletags": ["示例#1234"], "source": "image", "text": "PRIVATE_A"}, owner="a")
        second = worker.start("competitive", {"battletags": ["示例#1234"], "source": "image", "text": "PRIVATE_B"}, owner="b")
        self.assertEqual(len(self.state.work_queue.tasks), 1)
        release.set()
        await asyncio.gather(*list(worker.running.values()))
        reports = [worker.get_job(job["task_id"])["result"] for job in [first, second]]
        self.assertEqual([r["recognized"]["text"] for r in reports], ["PRIVATE_A", "PRIVATE_B"])
        self.assertEqual(worker.query.await_count, 1)
        saved = "".join(path.read_text() for path in worker.report_dir.glob("*.json"))
        self.assertNotIn("PRIVATE_", saved)

    async def test_cached_request_finishes_while_an_unrelated_network_job_is_blocked(self):
        worker = self.state.batch_rating
        entered, release = asyncio.Event(), asyncio.Event()
        async def compute(mode, prepared, **kwargs):
            if "_snapshots" not in prepared:
                entered.set(); await release.wait()
            return {"players": []}
        worker.query = compute
        first = await self.client.post("/api/batch-rating/competitive", json={"battletags": ["慢查询#1234"]})
        await entered.wait()
        try:
            with patch.object(worker, "fresh_snapshots", AsyncMock(return_value={"缓存#1234": object()})):
                response = await asyncio.wait_for(self.client.post("/api/batch-rating/competitive",
                    json={"battletags": ["缓存#1234"], "wait": True}, headers={"Cookie": "ow_browser_id=" + "c" * 32}), 1)
            self.assertEqual(response.status_code, 200)
            self.assertNotEqual(worker.get_job(first.json()["data"]["task_id"])["status"], "done")
        finally:
            release.set()

    async def test_deleting_running_legacy_job_cancels_real_handler(self):
        self.state.config["security"] = {"admin_api_key": "test-admin"}
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def handler(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        with patch.dict(_ENDPOINT_DISPATCH, {"/api/controlled-test": handler}):
            response = await self.client.post("/api/task/create", json={"endpoint": "/api/controlled-test"})
            task_id = response.json()["data"]["task_id"]
            await entered.wait()
            deleted = await self.client.delete("/api/task/" + task_id, headers={"X-Admin-Key": "test-admin"})
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(cancelled.is_set())
        self.assertIsNone(task_db.get_task(task_id))
        self.assertEqual(self.state.work_queue.active, 0)

    async def test_restart_marks_only_orphaned_work_as_interrupted(self):
        for key in ["pending", "running", "done"]:
            task_db.create_task(key, "/api/shop", {})
        task_db.update_task_progress("running", "查询", 50)
        task_db.update_task_done("done", {"success": True})
        self.assertEqual(task_db.recover_interrupted_tasks(), 2)
        self.assertEqual(task_db.get_task("done")["status"], "done")
        self.assertEqual(task_db.get_task("running")["error_code"], 503)

    async def test_queued_legacy_timeout_is_recorded_even_before_handler_starts(self):
        queue = self.state.work_queue
        queue.timeout = 1
        blocker = queue.submit(asyncio.Event().wait)
        queue.timeout = .04
        response = await self.client.post("/api/task/create", json={"endpoint": "/api/shop"})
        task_id = response.json()["data"]["task_id"]
        pending = self.state.legacy_tasks.get(task_id)
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        blocker.cancel()
        await asyncio.gather(blocker, return_exceptions=True)
        await asyncio.sleep(0)
        self.assertEqual(task_db.get_task(task_id)["status"], "error")
        self.assertEqual(task_db.get_task(task_id)["error_code"], 504)

    async def test_each_browser_has_only_one_ocr_work_item(self):
        self.client.cookies.set("ow_browser_id", "1" * 32)
        entered, release = asyncio.Event(), asyncio.Event()
        async def inference(encoded):
            entered.set(); await release.wait()
            return {"battletags": ["示例#1234"]}
        with patch("api_server.routers.batch_rating.recognize_image", side_effect=inference):
            pending = asyncio.create_task(self.client.post("/api/batch-rating/recognize", json={"image_base64": "test"}))
            await entered.wait()
            try:
                other = await self.client.post("/api/batch-rating/recognize", json={"image_base64": "test"})
                self.assertEqual(other.status_code, 429)
                self.assertEqual(len(self.state.work_queue.tasks), 1)
            finally:
                release.set()
                await pending

    async def test_authenticated_bot_queries_are_exempt_from_web_cooldowns_and_ignore_cookie(self):
        self.state.config.update(security={"bot_api_key": "independent-bot-secret"}, server={"api_key": "web-only"}, rate_limit={})
        self.state.batch_rating.query = AsyncMock(return_value={"players": []})
        def headers(user):
            return {"X-Bot-Key": "independent-bot-secret", "X-Bot-User": hashlib.sha256(user.encode()).hexdigest(),
                    "X-Bot-Group": "e" * 64, "Cookie": "ow_browser_id=" + "f" * 32}
        for user in ["alice", "bob"]:
            response = await self.client.post("/api/batch-rating/competitive", headers=headers(user),
                json={"battletags": [user + "#1234"], "wait": True})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("set-cookie", response.headers)
        for _ in range(32):
            allowed = await self.client.post("/api/batch-rating/competitive", headers=headers("alice"), json={"battletags": ["第三个#1234"], "wait": True})
            self.assertEqual(allowed.status_code, 200)
        allowed = await self.client.post("/api/batch-rating/competitive", headers=headers("charlie"),
                                         json={"battletags": ["第四个#1234"], "wait": True})
        self.assertEqual(allowed.status_code, 200)
        invalid = {**headers("alice"), "X-Bot-Key": "web-only"}
        self.assertEqual((await self.client.get("/api/batch-rating/rules", headers=invalid)).status_code, 403)
        self.assertEqual((await self.client.get("/api/task/list", headers=headers("alice"))).status_code, 403)

    async def test_cancel_one_report_keeps_shared_core_for_other_user(self):
        worker = self.state.batch_rating
        entered, release = asyncio.Event(), asyncio.Event()
        async def compute(*args, **kwargs):
            entered.set(); await release.wait()
            return {"players": []}
        worker.query = compute
        first = worker.start("competitive", {"battletags": ["共享#1234"]}, owner="a")
        second = worker.start("competitive", {"battletags": ["共享#1234"]}, owner="b")
        await entered.wait()
        first_task, second_task = worker.running[first["task_id"]], worker.running[second["task_id"]]
        first_task.cancel()
        await asyncio.gather(first_task, return_exceptions=True)
        release.set()
        await second_task
        self.assertEqual(worker.get_job(second["task_id"])["status"], "done")


if __name__ == "__main__":
    unittest.main()
