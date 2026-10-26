import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from starlette.requests import Request
from fastapi import HTTPException

from api_server.app_context import AppState
from api_server.batch_rating.service import BatchRatingService
from api_server.bindings import BindingsManager
from api_server.db import task_db
from api_server.main import app
from api_server.metrics import REGISTRY, render_metrics_text
from api_server.ratelimit import RateLimiters
from api_server.security import client_ip
from api_server.work_queue import WorkQueue, work_queue
from api_server.cache import TTLCache
from api_server.routers.stream import _ENDPOINT_DISPATCH


class InfrastructureTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_a_memory_cache_waiter_does_not_cancel_the_shared_result(self):
        cache = TTLCache("cancel-waiter")
        entered, release = asyncio.Event(), asyncio.Event()
        async def fetch():
            entered.set();await release.wait();return "ready"
        leader = asyncio.create_task(cache.get_or_compute("key", fetch))
        await entered.wait()
        waiter = asyncio.create_task(cache.get_or_compute("key", fetch))
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):await waiter
        release.set()
        self.assertEqual(await leader, "ready")
        self.assertEqual(await cache.get_or_compute("key", fetch), "ready")

    async def test_cancelled_memory_cache_leader_releases_waiters_and_allows_a_retry(self):
        cache = TTLCache("cancel-leader")
        entered = asyncio.Event()
        async def blocked():
            entered.set();await asyncio.Event().wait()
        leader = asyncio.create_task(cache.get_or_compute("key", blocked))
        await entered.wait()
        waiter = asyncio.create_task(cache.get_or_compute("key", blocked))
        await asyncio.sleep(0)
        leader.cancel()
        values = await asyncio.wait_for(asyncio.gather(leader, waiter, return_exceptions=True), 1)
        self.assertTrue(all(isinstance(value, asyncio.CancelledError) for value in values))
        async def recovered():return "recovered"
        self.assertEqual(await cache.get_or_compute("key", recovered), "recovered")

    async def test_reconfiguring_identical_limits_keeps_counts_and_disabling_clears_policy(self):
        limiter = RateLimiters()
        for index in range(3):
            limiter.configure(ip_max=2)
            self.assertEqual(limiter.check(ip="one")[0], index < 2)
        limiter.configure(ip_max=0)
        self.assertTrue(limiter.check(ip="one")[0])

    async def test_bounded_queue_rejects_fifth_and_cancelled_queued_tasks_release_capacity(self):
        queue = WorkQueue()
        release = asyncio.Event()
        tasks = [queue.submit(release.wait) for _ in range(4)]
        await asyncio.sleep(.01)
        self.assertEqual(queue.status(), {"active": 1, "queued": 3, "capacity": 4})
        with self.assertRaises(HTTPException) as error:
            queue.submit(release.wait)
        self.assertEqual(error.exception.status_code, 429)
        tasks[-1].cancel()
        await asyncio.gather(tasks[-1], return_exceptions=True)
        self.assertEqual(len(queue.tasks), 3)
        await queue.close()
        self.assertEqual(queue.status()["active"], 0)
        self.assertEqual(len(queue.tasks), 0)

    async def test_work_timeout_releases_the_running_slot(self):
        queue = WorkQueue(timeout=.02)
        with self.assertRaises(HTTPException) as error:
            await queue.call(asyncio.Event().wait)
        self.assertEqual(error.exception.status_code, 504)
        await asyncio.sleep(0)
        self.assertEqual(queue.status(), {"active": 0, "queued": 0, "capacity": 4})

    async def test_metrics_scrapes_do_not_count_cache_totals_twice(self):
        with patch("api_server.cache.list_cache_stats", return_value={"scrape-test": {"hits": 4, "misses": 3, "entries": 2}}):
            render_metrics_text();render_metrics_text()
        self.assertEqual(REGISTRY.counter("cache_hit_total", {"cache": "scrape-test"}).value(), 4)
        self.assertEqual(REGISTRY.counter("cache_lookup_total", {"cache": "scrape-test"}).value(), 7)

    async def test_only_configured_proxy_chain_can_supply_client_ip(self):
        def request(peer, forwarded, trusted=None):
            state = AppState(config={"security": {"trusted_proxy_cidrs": trusted}} if trusted is not None else {})
            return Request({"type": "http", "headers": [(b"x-forwarded-for", forwarded.encode())], "client": (peer, 123),
                            "app": SimpleNamespace(state=SimpleNamespace(ow_app=state))})
        self.assertEqual(client_ip(request("198.51.100.10", "1.1.1.1")), "198.51.100.10")
        self.assertEqual(client_ip(request("127.0.0.1", "1.1.1.1, 198.51.100.10")), "198.51.100.10")
        self.assertEqual(client_ip(request("127.0.0.1", "malformed")), "127.0.0.1")
        self.assertEqual(client_ip(request("127.0.0.1", "1.1.1.1", [])), "127.0.0.1")


class PublicEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict("os.environ", {"OW_RATING_CACHE_FILE": str(self.folder / "cache.sqlite3"),
            "OW_RATING_REPORT_DIR": str(self.folder / "reports"), "OW_QUERY_ID_LOG_FILE": str(self.folder / "audit.txt")}))
        self.stack.enter_context(patch.object(task_db, "_TASK_DB_PATH", str(self.folder / "tasks.sqlite3")))
        self.stack.enter_context(patch("api_server.main.LIMITERS", RateLimiters()))
        task_db.init_task_db()
        self.previous = getattr(app.state, "ow_app", None)
        self.state = AppState(config={"rate_limit": {"enable_heavy_limits": False}, "output": {"prefer_image_for_match": False}}, initialized=True)
        self.state.api_client = object()
        self.state.bnet_search = SimpleNamespace(search=AsyncMock())
        self.state.batch_rating = BatchRatingService(self.state)
        app.state.ow_app = self.state
        self.transport = httpx.ASGITransport(app=app, client=("198.51.100.8", 1234))
        self.client = httpx.AsyncClient(transport=self.transport, base_url="http://testserver")

    async def asyncTearDown(self):
        await work_queue(self.state).close()
        await self.state.batch_rating.close()
        await self.client.aclose()
        app.state.ow_app = self.previous
        self.stack.close()

    async def test_real_request_limiter_persists_and_forwarded_header_cannot_evade_it(self):
        self.state.config["rate_limit"] = {"requests_per_ip_per_minute": 2}
        results = [await self.client.get("/api/not-real", headers={"X-Forwarded-For": f"203.0.113.{i}"}) for i in range(3)]
        self.assertEqual([r.status_code for r in results], [404, 404, 429])
        self.assertIn("retry-after", results[-1].headers)

    async def test_admin_task_endpoints_require_a_separate_secret(self):
        self.state.config["security"] = {"admin_api_key": "admin-only"}
        self.assertEqual((await self.client.get("/api/task/list")).status_code, 403)
        self.assertEqual((await self.client.get("/api/task/list", headers={"X-API-Key": "admin-only"})).status_code, 403)
        self.assertEqual((await self.client.get("/api/task/list", headers={"X-Admin-Key": "admin-only"})).status_code, 200)
        self.assertEqual((await self.client.delete("/api/task/not-found")).status_code, 403)

    async def test_metrics_and_docs_are_not_exposed_to_public_clients(self):
        self.assertEqual((await self.client.get("/metrics")).status_code, 403)
        self.assertNotEqual((await self.client.get("/docs")).status_code, 200)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)), base_url="http://testserver") as local:
            self.assertEqual((await local.get("/metrics")).status_code, 200)
            self.assertEqual((await local.get("/metrics", headers={"X-Forwarded-For": "198.51.100.2"})).status_code, 403)

    async def test_errors_do_not_expose_exception_paths_or_tokens(self):
        self.state.match = SimpleNamespace(query_match_list=AsyncMock(side_effect=RuntimeError("SECRET_TOKEN /internal/private.db")))
        result = await self.client.get("/api/matches", params={"battletag": "示例#1234"})
        self.assertEqual(result.status_code, 500)
        self.assertNotIn("SECRET_TOKEN", result.text)
        self.assertNotIn("private.db", result.text)
        self.assertEqual(len(result.json()["request_id"]), 32)

    async def test_sync_wait_legacy_jobs_and_streams_share_the_four_job_limit(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            entered.set();await release.wait()
            return {"players": [], "recognized": {}}
        self.state.batch_rating.query = delayed
        pending = [asyncio.create_task(self.client.post("/api/batch-rating/competitive", json={"battletags": [f"玩家{i}#1234"], "wait": True})) for i in range(4)]
        await asyncio.wait_for(entered.wait(), 2)
        for _ in range(100):
            if len(work_queue(self.state).tasks) == 4:break
            await asyncio.sleep(.01)
        fifth = await self.client.post("/api/batch-rating/competitive", json={"battletags": ["额外#1234"], "wait": True})
        self.assertIn(fifth.status_code, {429, 503})
        self.assertEqual((await self.client.post("/api/task/create", json={"endpoint": "/api/shop"})).status_code, 429)
        self.assertEqual((await self.client.post("/api/stream", json={"endpoint": "/api/shop"})).status_code, 429)
        self.assertEqual((await self.client.get("/api/shop")).status_code, 429)
        self.assertEqual(len(task_db.list_tasks()), 0)
        release.set()
        responses = await asyncio.gather(*pending)
        self.assertTrue(all(r.status_code == 200 for r in responses))

    async def test_progress_polling_does_not_consume_heavy_query_allowance(self):
        self.state.config["rate_limit"] = {"requests_per_ip_per_minute": 2}
        for _ in range(20):
            self.assertEqual((await self.client.get("/api/batch-rating/tasks/" + "a" * 32)).status_code, 404)
        for i in range(2):
            response = await self.client.get("/api/no-such-query")
            self.assertEqual(response.status_code, 404)
        self.assertEqual((await self.client.get("/api/no-such-query")).status_code, 429)

    async def test_a_stale_report_expiring_just_before_wait_returns_is_not_an_internal_error(self):
        self.state.batch_rating.query = AsyncMock(return_value={"players": [], "recognized": {}})
        with patch.object(self.state.batch_rating, "get_job", return_value=None):
            result = await self.client.post("/api/batch-rating/competitive", json={"battletags": ["示例#1234"], "wait": True})
        self.assertEqual(result.status_code, 503)
        self.assertIn("过期", result.json()["error"])

    async def test_new_job_ids_are_full_length_and_private_params_are_not_persisted(self):
        async def handler(*args):return {"success": True, "data": {"ok": True}}
        with patch.dict(_ENDPOINT_DISPATCH, {"/api/public-test": handler}):
            response = await self.client.post("/api/task/create", json={"endpoint": "/api/public-test", "params": {"token": "PRIVATE", "image_base64": "LARGE", "limit": 3}})
            task_id = response.json()["data"]["task_id"]
            self.assertRegex(task_id, r"^[a-f0-9]{32}$")
            await asyncio.gather(*list(work_queue(self.state).tasks))
        task = task_db.get_task(task_id)
        self.assertEqual(task["params"], {"limit": 3})
        self.assertEqual(task["status"], "done")

    async def test_binding_headers_work_without_exposing_upstream_identity_fields(self):
        self.state.bindings_manager = BindingsManager(str(self.folder / "bindings.json"))
        self.state.bnet_search.search.return_value = SimpleNamespace(result=SimpleNamespace(
            customer_token="PRIVATE_CUSTOMER", bnet_id="PRIVATE_ID", full_id="玩家#1234", icon_url=""))
        bound = await self.client.post("/api/bind", params={"battletag": "玩家#1234"})
        self.assertEqual(bound.status_code, 200)
        self.assertNotIn("PRIVATE_", bound.text)
        self.assertNotIn('"customer_token"', bound.text)
        mine = await self.client.get("/api/my-bind", headers={"X-User-Token": bound.json()["token"]})
        self.assertEqual(mine.status_code, 200)
        self.assertNotIn("PRIVATE_", mine.text)
        self.assertEqual((await self.client.post("/api/unbind", headers={"X-User-Token": bound.json()["token"]})).status_code, 200)

    async def test_chunked_oversized_body_is_rejected_before_json_or_ocr(self):
        async def chunks():
            for _ in range(3):yield b"x" * (5 * 1024 * 1024)
        response = await self.client.post("/api/batch-rating/recognize", content=chunks(), headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)

    async def test_browser_cookie_can_be_cleared_and_api_responses_do_not_cache(self):
        first = await self.client.get("/api/query-status")
        self.assertIn("ow_browser_id", self.client.cookies)
        self.assertEqual(first.headers["cache-control"], "no-store")
        await self.client.post("/api/privacy/clear-session")
        self.assertNotIn("ow_browser_id", self.client.cookies)


if __name__ == "__main__":unittest.main()
