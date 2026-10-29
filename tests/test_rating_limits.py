"""Web submission policy regression tests; all query computation is mocked."""
import asyncio
import hashlib
import unittest
from unittest.mock import AsyncMock, patch

from api_server.ratelimit import SlidingWindowLimiter
import test_public_operations as fixtures


class RatingLimitTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixtures.PublicEndpointTests.asyncTearDown

    async def asyncSetUp(self):
        await fixtures.PublicEndpointTests.asyncSetUp(self)
        self.now = 1000.
        self.state.config['rate_limit'] = {}
        self.state.rating_request_limits = {
            'ordinary': SlidingWindowLimiter(2, 10, clock=lambda: self.now),
            'full_match': SlidingWindowLimiter(1, 60, clock=lambda: self.now),
        }
        self.state.batch_rating.query = AsyncMock(return_value={'players': []})
        self.client.cookies.set('ow_browser_id', 'a'*32)

    async def submit(self, *, full=False, mode='competitive', **kwargs):
        return await self.client.post('/api/batch-rating/'+mode,
                                      json={'battletags':['Example#1234'],'full_match':full,'wait':True}, **kwargs)

    async def test_two_ordinary_queries_share_the_window_across_modes_and_reset_at_ten_seconds(self):
        self.assertEqual((await self.submit()).status_code, 200)
        self.assertEqual((await self.submit(mode='leisure')).status_code, 200)
        denied = await self.submit()
        self.assertEqual(denied.status_code, 429)
        self.assertEqual(denied.headers['retry-after'], '10')
        self.assertIn('每10秒', denied.text)
        self.now += 9.1
        self.assertEqual((await self.submit()).headers['retry-after'], '1')
        self.now += .9
        self.assertEqual((await self.submit()).status_code, 200)
        self.assertEqual((await self.submit()).status_code, 200)

    async def test_full_match_has_one_minute_shared_across_rosters_and_modes(self):
        self.assertEqual((await self.submit(full=True)).status_code, 200)
        denied = await self.submit(full=True, mode='leisure')
        self.assertEqual(denied.status_code, 429)
        self.assertEqual(denied.headers['retry-after'], '60')
        self.now += 59.01
        self.assertEqual((await self.submit(full=True)).headers['retry-after'], '1')
        self.now += .99
        self.assertEqual((await self.submit(full=True)).status_code, 200)

    async def test_ordinary_and_full_match_allowances_are_independent(self):
        self.assertEqual((await self.submit()).status_code, 200)
        self.assertEqual((await self.submit()).status_code, 200)
        self.assertEqual((await self.submit(full=True)).status_code, 200)
        self.assertEqual((await self.submit()).status_code, 429)
        self.assertEqual((await self.submit(full=True)).status_code, 429)
        self.now += 10
        self.assertEqual((await self.submit()).status_code, 200)
        self.assertEqual((await self.submit(full=True)).status_code, 429)

    async def test_cached_queries_still_consume_the_web_allowance(self):
        with patch.object(self.state.batch_rating, 'fresh_snapshots', AsyncMock(return_value={'Example#1234': object()})):
            self.assertEqual((await self.submit()).status_code, 200)
            self.assertEqual((await self.submit()).status_code, 200)
            self.assertEqual((await self.submit()).status_code, 429)
            self.assertEqual((await self.submit(full=True)).status_code, 200)
            self.assertEqual((await self.submit(full=True)).status_code, 429)

    async def test_duplicate_pending_posts_are_counted_without_duplicate_computation(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return {'players': []}
        self.state.batch_rating.query = AsyncMock(side_effect=blocked)
        try:
            body={'battletags':['Example#1234']}
            first=await self.client.post('/api/batch-rating/competitive', json=body)
            await entered.wait()
            second=await self.client.post('/api/batch-rating/competitive', json=body)
            third=await self.client.post('/api/batch-rating/competitive', json=body)
            self.assertEqual([first.status_code,second.status_code,third.status_code], [202,202,429])
            self.assertEqual(first.json()['data']['task_id'],second.json()['data']['task_id'])
            self.assertEqual(self.state.batch_rating.query.await_count,1)
        finally:
            release.set()

    async def test_invalid_inputs_do_not_charge_and_browsers_are_independent(self):
        for _ in range(3):
            invalid=await self.client.post('/api/batch-rating/competitive',json={'battletags':['invalid']})
            self.assertEqual(invalid.status_code,422)
        self.assertEqual((await self.submit()).status_code,200)
        self.assertEqual((await self.submit()).status_code,200)
        self.assertEqual((await self.submit()).status_code,429)
        self.assertEqual((await self.submit(headers={'Cookie':'ow_browser_id='+'b'*32})).status_code,200)

    async def test_valid_bot_service_is_exempt_but_claimed_bot_headers_are_not(self):
        self.state.config['security']={'bot_api_key':'independent-bot-secret'}
        headers={'X-Bot-Key':'independent-bot-secret','X-Bot-User':hashlib.sha256(b'user').hexdigest(),
                 'X-Bot-Group':hashlib.sha256(b'group').hexdigest()}
        for _ in range(5):
            self.assertEqual((await self.submit(headers=headers)).status_code,200)
            self.assertEqual((await self.submit(full=True,headers=headers)).status_code,200)
        for _ in range(2):
            self.assertEqual((await self.submit(headers={'User-Agent':'robot','X-Bot-User':headers['X-Bot-User']})).status_code,200)
        self.assertEqual((await self.submit()).status_code,429)
        self.assertEqual((await self.submit(headers={**headers,'X-Bot-Key':'fake'})).status_code,403)
        self.assertEqual((await self.submit(headers={'X-Bot-Key':'independent-bot-secret'})).status_code,422)

    async def test_progress_polling_does_not_charge_ordinary_or_full_match(self):
        for _ in range(4):
            self.assertEqual((await self.client.get('/api/batch-rating/tasks/'+'f'*32)).status_code,404)
        self.assertEqual((await self.submit()).status_code,200)
        self.assertEqual((await self.submit()).status_code,200)
        self.assertEqual((await self.submit(full=True)).status_code,200)
