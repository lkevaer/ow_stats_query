import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from api_server import query_audit
from api_server.app_context import AppState
from api_server.batch_rating.service import BatchRatingService
from api_server.bindings import BindingRecord
from api_server.db import task_db
from api_server.main import app


class QueryAuditFileTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.path = self.folder / 'nested' / 'successful_battletags.txt'
        self.stack.enter_context(patch.dict(os.environ, {'OW_QUERY_ID_LOG_FILE': str(self.path)}))

    def tearDown(self):
        self.stack.close()

    def test_utf8_normalization_and_repeated_queries_are_kept(self):
        query_audit._append_successful_ids(['中文玩家＃12345', '中文玩家#12345'])
        query_audit._append_successful_ids(['中文玩家#12345'])
        lines = self.path.read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(line.endswith('\t中文玩家#12345') for line in lines))
        self.assertTrue(all('+08:00\t' in line for line in lines))

    def test_invalid_ids_and_injected_lines_are_not_written(self):
        query_audit._append_successful_ids(['token-only', None, 'Bad#1234\nInjected#5678'])
        self.assertFalse(self.path.exists())

    def test_threshold_trims_oldest_records_at_whole_utf8_line_boundaries(self):
        self.path.parent.mkdir()
        lines = [f'2026-10-23T23:00:00+08:00\t中文玩家{i}#12345\n' for i in range(20)]
        old = ''.join(lines).encode('utf-8')
        self.path.write_bytes(old)
        with patch.object(query_audit, 'MAX_LOG_BYTES', len(old)), patch.object(query_audit, 'RETAIN_LOG_BYTES', 310):
            query_audit._append_successful_ids(['最新玩家#98765'])
        result = self.path.read_text(encoding='utf-8')
        remaining = result.splitlines(keepends=True)
        self.assertLessEqual(self.path.stat().st_size, 310)
        self.assertNotIn('中文玩家0#', result)
        self.assertTrue(remaining[-1].endswith('\t最新玩家#98765\n'))
        old_remaining = remaining[:-1]
        self.assertEqual(old_remaining, lines[-len(old_remaining):])
        self.assertFalse(self.path.with_name(self.path.name + '.trim.tmp').exists())

    def test_exact_threshold_triggers_cleanup_and_keeps_complete_boundary_line(self):
        stamp = '2026-10-23T23:00:00+08:00'
        lines = [stamp + '\t玩家' + str(i) + '#12345\n' for i in range(8)]
        final = stamp + '\t最新#12345\n'
        self.path.parent.mkdir()
        self.path.write_bytes(''.join(lines).encode('utf-8'))
        keep = ''.join(lines[-2:]) + final
        size = self.path.stat().st_size + len(final.encode('utf-8'))
        with patch.object(query_audit, 'MAX_LOG_BYTES', size), patch.object(query_audit, 'RETAIN_LOG_BYTES', len(keep.encode('utf-8'))), patch.object(query_audit, 'datetime') as clock:
            clock.now.return_value.isoformat.return_value = stamp
            query_audit._append_successful_ids(['最新#12345'])
        self.assertEqual(self.path.read_text(encoding='utf-8'), keep)

    def test_concurrent_queries_do_not_interleave_or_lose_records(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: query_audit._append_successful_ids([f'并发玩家{i}#12345']), range(60)))
        lines = self.path.read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 60)
        self.assertEqual({line.split('\t')[1] for line in lines}, {f'并发玩家{i}#12345' for i in range(60)})


class SuccessfulQueryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.path = self.folder / 'successful_battletags.txt'
        self.stack.enter_context(patch.dict(os.environ, {'OW_QUERY_ID_LOG_FILE': str(self.path)}))
        self.stack.enter_context(patch.object(task_db, '_TASK_DB_PATH', str(self.folder / 'tasks.sqlite3')))
        task_db.init_task_db()
        self.previous = getattr(app.state, 'ow_app', None)
        self.state = AppState(config={'output': {'prefer_image_for_match': False}, 'rate_limit': {'enable_heavy_limits': False}}, initialized=True)
        self.state.api_client = object()
        self.state.match = SimpleNamespace(query_match_list=AsyncMock(return_value=SimpleNamespace(resolved_bnet=None, image=None, matches=[{'matchId': 'demo'}])))
        app.state.ow_app = self.state
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver')

    async def asyncTearDown(self):
        await self.client.aclose()
        app.state.ow_app = self.previous
        self.stack.close()

    async def test_direct_success_logs_id_but_not_tokens_and_failure_does_not_log(self):
        result = await self.client.get('/api/matches', params={'battletag': '成功玩家#12345', 'token': 'private-client-token'})
        self.assertEqual(result.status_code, 200)
        self.assertIn('成功玩家#12345', self.path.read_text(encoding='utf-8'))
        self.assertNotIn('private-client-token', self.path.read_text(encoding='utf-8'))
        self.state.match.query_match_list.side_effect = RuntimeError('demo failure')
        result = await self.client.get('/api/matches', params={'battletag': '失败玩家#12345'})
        self.assertEqual(result.status_code, 500)
        self.assertNotIn('失败玩家', self.path.read_text(encoding='utf-8'))

    async def test_bound_account_logs_the_battletag_without_either_token(self):
        self.state.bindings_manager = SimpleNamespace(get=lambda _: BindingRecord(battletag='绑定玩家#12345', bnet_id='321', customer_token='private-upstream-token'))
        result = await self.client.get('/api/matches', params={'token': 'private-browser-token'})
        self.assertEqual(result.status_code, 200)
        content = self.path.read_text(encoding='utf-8')
        self.assertIn('绑定玩家#12345', content)
        self.assertNotIn('private-', content)

    async def test_sse_and_async_jobs_log_once_per_success_not_per_poll(self):
        result = await self.client.post('/api/stream', json={'endpoint': '/api/matches', 'params': {'battletag': '流式玩家#12345'}})
        self.assertIn('event: done', result.text)
        result = await self.client.post('/api/task/create', json={'endpoint': '/api/matches', 'params': {'battletag': '异步玩家#12345'}})
        task_id = result.json()['data']['task_id']
        for _ in range(30):
            current = (await self.client.get('/api/task/' + task_id)).json()['data']
            if current['status'] == 'done':
                break
            await asyncio.sleep(.01)
        self.assertEqual(current['status'], 'done')
        for _ in range(3):
            await self.client.get('/api/task/' + task_id)
        lines = self.path.read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(sum('流式玩家#' in line for line in lines), 1)
        self.assertEqual(sum('异步玩家#' in line for line in lines), 1)

    async def test_batch_logs_only_players_with_a_real_score(self):
        worker = BatchRatingService(SimpleNamespace(api_client=object(), bnet_search=object()))
        async def one(tag, *args):
            return {'battletag': tag, 'score': None if tag.startswith('失败') else 80, 'rated_matches': 0 if tag.startswith('失败') else 1, 'fleta': .5}
        worker._one_player = one
        with patch('overstats.src.modules.query_tool.load_query_tool', return_value={'heroList': [{'heroGuid': 't', 'name': '测试', 'roleType': 'tank'}]}):
            await worker.query('competitive', {'battletags': ['批量玩家#12345', '失败玩家#12345']})
        content = self.path.read_text(encoding='utf-8')
        self.assertIn('批量玩家#12345', content)
        self.assertNotIn('失败玩家', content)

    async def test_write_failure_is_visible_in_service_logs_and_does_not_fail_query(self):
        with patch.object(query_audit, '_append_successful_ids', side_effect=OSError('test disk full')), self.assertLogs(query_audit.logger, level='ERROR'):
            response = await self.client.get('/api/matches', params={'battletag': '成功玩家#12345'})
        self.assertEqual(response.status_code, 200)
