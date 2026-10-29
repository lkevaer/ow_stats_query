import asyncio
import os
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx

from api_server.batch_rating.service import BatchRatingService
from api_server.feature_flags import rating_no_queue
from api_server.security import RequestBodyLimit
from api_server.work_queue import WorkQueue


class ParallelRatingTests(unittest.IsolatedAsyncioTestCase):
    async def test_zero_capacity_and_concurrency_start_twenty_operations_without_queueing(self):
        queue = WorkQueue(capacity=0, concurrency=0)
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0

        async def work():
            nonlocal count
            count += 1
            if count == 20:
                entered.set()
            await release.wait()
            return 'done'

        tasks = [queue.submit(work) for _ in range(20)]
        try:
            await asyncio.wait_for(entered.wait(), 1)
            self.assertEqual(queue.status(), {'active': 20, 'queued': 0, 'capacity': 0})
            release.set()
            self.assertEqual(await asyncio.gather(*tasks), ['done']*20)
        finally:
            await queue.close()
        self.assertEqual(queue.active, 0)

    async def test_no_queue_still_times_out_and_cancels_owned_work(self):
        queue = WorkQueue(capacity=0, concurrency=0, timeout=.02)
        values = await asyncio.gather(*(queue.submit(asyncio.Event().wait) for _ in range(20)), return_exceptions=True)
        self.assertTrue(all(getattr(error, 'status_code', None) == 504 for error in values))
        await queue.close()
        self.assertEqual(queue.status(), {'active': 0, 'queued': 0, 'capacity': 0})

    async def test_rating_wrapper_and_computation_allow_twenty_distinct_queries(self):
        with TemporaryDirectory() as folder, patch.dict(os.environ, {'OW_RATING_NO_QUEUE': '1', 'OW_RATING_REPORT_DIR': folder}):
            service = BatchRatingService(SimpleNamespace())
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0

        async def query(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 20:
                entered.set()
            await release.wait()
            return {'players': [], 'summary': {}}

        service.query = query
        service._save_report = Mock()
        try:
            jobs = [service.start('competitive', {'battletags': [f'Test{i}#1234']}, owner=str(i)) for i in range(20)]
            await asyncio.wait_for(entered.wait(), 1)
            self.assertEqual(len(service.running), 20)
            internal_jobs = list(service.jobs.values())
            self.assertEqual(service.rating_queue.status()['active'], 20)
            self.assertEqual(service.rating_queue.status()['queued'], 0)
            release.set()
            await asyncio.gather(*list(service.running.values()))
            self.assertTrue(all(job['status'] == 'done' for job in internal_jobs))
            self.assertEqual(service._save_report.call_count, 20)
        finally:
            await service.close()

    async def test_no_queue_does_not_serialize_upstream_calls(self):
        with patch.dict(os.environ, {'OW_RATING_NO_QUEUE': '1'}):
            service = BatchRatingService(SimpleNamespace())
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0

        async def search():
            nonlocal count
            count += 1
            if count == 20:
                entered.set()
            await release.wait()
            return {'code': 0}

        tasks = [asyncio.create_task(service._request(search)) for _ in range(20)]
        try:
            await asyncio.wait_for(entered.wait(), 1)
            self.assertEqual(count, 20)
            release.set()
            await asyncio.gather(*tasks)
        finally:
            await service.close()

    async def test_twenty_rating_bodies_are_admitted_but_size_and_other_routes_stay_bounded(self):
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0

        async def application(scope, receive, send):
            nonlocal count
            count += 1
            if count == 20:
                entered.set()
            await release.wait()
            await send({'type': 'http.response.start', 'status': 204, 'headers': []})
            await send({'type': 'http.response.body', 'body': b''})

        with patch.dict(os.environ, {'OW_RATING_NO_QUEUE': '1'}):
            app = RequestBodyLimit(application, maximum=128, capacity=4)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            tasks = [asyncio.create_task(client.post('/api/batch-rating/competitive', json={'battletags': ['Test#1234']})) for _ in range(20)]
            try:
                await asyncio.wait_for(entered.wait(), 1)
                oversize = await client.post('/api/batch-rating/competitive', content='x'*129)
                self.assertEqual(oversize.status_code, 413)
                app.active = 4
                self.assertEqual((await client.post('/api/bind', json={})).status_code, 503)
            finally:
                release.set()
                responses = await asyncio.gather(*tasks)
            self.assertTrue(all(response.status_code == 204 for response in responses))

    async def test_default_installation_keeps_existing_limits_and_ocr_queue(self):
        with patch.dict(os.environ, {'OW_RATING_NO_QUEUE': '0'}):
            service = BatchRatingService(SimpleNamespace())
            self.assertFalse(rating_no_queue())
        self.assertIs(service.rating_queue, service.queue)
        self.assertEqual(service.rating_queue.status()['capacity'], 4)
        self.assertEqual(service.cached_queue.status()['capacity'], 4)
        await service.close()
