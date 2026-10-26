import asyncio
from contextlib import ExitStack, closing
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from api_server.batch_rating.player_cache import PlayerSnapshot, RETAIN_SECONDS
from api_server.batch_rating.scoring import score_match
from api_server.batch_rating.service import BatchRatingService
from api_server.batch_rating.circuit import UpstreamCircuit
from test_batch_rating import HEROES
from test_rating_context import full_match


TAG = "缓存玩家#12345"


class PlayerCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.now = 1_800_000_000.0
        self.stack.enter_context(patch("api_server.batch_rating.player_cache.time.time", side_effect=lambda: self.now))
        self.stack.enter_context(patch.dict(os.environ, {
            "OW_RATING_CACHE_FILE": str(self.folder / "nested/cache.sqlite3"),
            "OW_RATING_REPORT_DIR": str(self.folder / "reports"),
            "OW_QUERY_ID_LOG_FILE": str(self.folder / "audit.txt"),
        }))
        config = {"heroList": [{**hero, "heroGuid": guid} for guid, hero in HEROES.items()]}
        self.stack.enter_context(patch("overstats.src.modules.query_tool.load_query_tool", return_value=config))
        self.client = SimpleNamespace(query_match_list=AsyncMock(side_effect=self.match_list),
                                      query_match_info=AsyncMock(side_effect=self.match_detail))
        self.search = SimpleNamespace(search=AsyncMock(side_effect=self.identity))
        self.state = SimpleNamespace(api_client=self.client, bnet_search=self.search)
        self.worker = BatchRatingService(self.state)
        self.workers = [self.worker]

    async def asyncTearDown(self):
        for worker in self.workers:
            await worker.close()
        self.stack.close()

    async def identity(self, tag, **kwargs):
        return SimpleNamespace(result=SimpleNamespace(customer_token=tag, bnet_id=tag, full_id=tag))

    async def match_list(self, token, mode, **kwargs):
        return {"code": 0, "data": [{"matchId": f"{mode}-{i}", "gameMode": mode,
                                     "beginTs": 1_790_000_000_000 + i * 600_000} for i in range(12)]}

    async def match_detail(self, tag, match_id):
        data = full_match("d")
        data["teammateList"][0].update(name=tag, bnetId=tag)
        return {"code": 0, "data": data}

    async def query(self, mode="competitive", tags=None, worker=None):
        return await (worker or self.worker).query(mode, {"battletags": tags or [TAG]})

    def key(self, mode="competitive", tag=TAG):
        return self.worker.player_cache.key(tag, mode, self.worker.current_season, 12)

    def snapshot(self):
        return self.worker.player_cache.get(self.key())

    def rows(self):
        with closing(sqlite3.connect(self.worker.player_cache.path)) as db:
            return db.execute("SELECT cache_key, fetched_at, payload FROM player_snapshots").fetchall()

    async def test_five_minute_hit_skips_identity_list_and_all_twelve_details(self):
        first = (await self.query())["players"][0]
        self.now += 299
        second = (await self.query())["players"][0]
        self.assertEqual((first["data_cache"]["source"], second["data_cache"]["source"]), ("upstream", "cache"))
        self.assertEqual(first["matches"], second["matches"])
        self.assertEqual((self.search.search.await_count, self.client.query_match_list.await_count,
                          self.client.query_match_info.await_count), (1, 1, 12))
        self.assertEqual(second["data_cache"]["age_seconds"], 299)

    async def test_five_minute_boundary_and_hits_do_not_extend_freshness(self):
        await self.query()
        stamp = self.now
        for offset in (200, 299, 300):
            self.now = stamp + offset
            self.assertEqual((await self.query())["players"][0]["data_cache"]["source"], "cache")
        self.now = stamp + 301
        result = (await self.query())["players"][0]
        self.assertEqual(result["data_cache"]["source"], "upstream")
        self.assertEqual(self.client.query_match_info.await_count, 24)
        self.assertEqual(self.snapshot().fetched_at, stamp + 301)

    async def test_modes_ids_and_seasons_have_independent_snapshots(self):
        await self.query()
        await self.query("leisure")
        await self.query(tags=["另一玩家#54321"])
        self.worker.current_season += 1
        await self.query()
        self.assertEqual(len(self.rows()), 4)
        self.assertEqual(self.search.search.await_count, 4)
        self.assertEqual({call.args[1] for call in self.client.query_match_list.await_args_list}, {"sport", "leisure"})
        self.assertNotEqual(self.key(tag="Case#1234"), self.key(tag="case#1234"))
        self.assertEqual(self.key(tag=" 缓存玩家＃12345 "), self.key())

    async def test_restart_reuses_persisted_raw_data_and_recalculates_scores(self):
        await self.query()
        restarted = BatchRatingService(self.state)
        self.workers.append(restarted)
        calls = []
        def updated_rules(*args, **kwargs):
            value = score_match(*args, **kwargs)
            value["score"] = 42
            calls.append(value)
            return value
        with patch("api_server.batch_rating.service.score_match", side_effect=updated_rules):
            result = (await self.query(worker=restarted))["players"][0]
        self.assertEqual(len(calls), 12)
        self.assertTrue(all(row["score"] == 42 for row in result["matches"]))
        self.assertEqual(result["data_cache"]["source"], "cache")
        self.assertEqual(self.search.search.await_count, 1)
        payload = self.rows()[0][2]
        for forbidden in ('"scoring_version"', '"metrics"', '"task_id"', '"tier"'):
            self.assertNotIn(forbidden, payload)

    async def test_refresh_failure_serves_labelled_old_data_without_changing_timestamp(self):
        first = (await self.query())["players"][0]
        old = self.rows()
        self.now += 301
        self.search.search.side_effect = OSError("network offline")
        result = (await self.query())["players"][0]
        self.assertEqual(result["matches"], first["matches"])
        self.assertEqual(result["data_cache"]["source"], "stale_cache")
        self.assertIn("OSError", result["data_cache"]["refresh_error"])
        self.assertEqual(self.rows(), old)

    async def test_partial_refresh_does_not_replace_successful_snapshot_and_retry_can_update(self):
        await self.query()
        old = self.rows()
        self.now += 301
        async def partial(tag, match_id):
            if match_id.endswith("-3"):
                raise OSError("one detail unavailable")
            return await self.match_detail(tag, match_id)
        self.client.query_match_info.side_effect = partial
        result = (await self.query())["players"][0]
        self.assertEqual(result["rated_matches"], 12)
        self.assertEqual(result["data_cache"]["source"], "stale_cache")
        self.assertIn("1场", result["data_cache"]["refresh_error"])
        self.assertEqual(self.rows(), old)
        self.client.query_match_info.side_effect = self.match_detail
        self.now += 1
        self.assertEqual((await self.query())["players"][0]["data_cache"]["source"], "upstream")
        self.assertEqual(self.snapshot().fetched_at, self.now)

    async def test_partial_first_fetch_is_displayed_but_not_cached(self):
        async def partial(tag, match_id):
            return {"code": 503} if match_id.endswith("-3") else await self.match_detail(tag, match_id)
        self.client.query_match_info.side_effect = partial
        result = (await self.query())["players"][0]
        self.assertEqual(result["rated_matches"], 11)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(self.rows(), [])
        self.client.query_match_info.side_effect = self.match_detail
        self.assertEqual((await self.query())["players"][0]["rated_matches"], 12)
        self.assertEqual(self.search.search.await_count, 2)

    async def test_scoring_failure_cannot_poison_a_good_snapshot(self):
        await self.query()
        old = self.rows()
        self.now += 301
        self.client.query_match_info.side_effect = None
        self.client.query_match_info.return_value = {"code": 0, "data": {"gameTimeSec": 600}}
        result = (await self.query())["players"][0]
        self.assertEqual(result["data_cache"]["source"], "stale_cache")
        self.assertEqual(self.rows(), old)

    async def test_expired_data_is_deleted_and_cannot_be_used_when_upstream_fails(self):
        await self.query()
        self.now += RETAIN_SECONDS
        self.search.search.side_effect = TimeoutError()
        result = (await self.query())["players"][0]
        self.assertIsNone(result["score"])
        self.assertNotIn("data_cache", result)
        self.assertEqual(self.rows(), [])
        self.search.search.side_effect = self.identity
        result = (await self.query())["players"][0]
        self.assertEqual(result["rated_matches"], 12)
        self.assertEqual(result["data_cache"]["source"], "upstream")

    async def test_old_data_expiring_during_refresh_is_not_returned(self):
        await self.query()
        self.now += RETAIN_SECONDS - 1
        async def failed(*args, **kwargs):
            self.now += 2
            raise OSError()
        self.search.search.side_effect = failed
        result = (await self.query())["players"][0]
        self.assertIsNone(result["score"])
        self.assertNotIn("data_cache", result)

    async def test_concurrent_waiters_share_fetch_but_not_mutable_result_objects(self):
        results = await asyncio.gather(*(self.query() for _ in range(8)))
        self.assertEqual(self.search.search.await_count, 1)
        self.assertEqual(self.client.query_match_info.await_count, 12)
        results[0]["players"][0]["matches"][0]["score"] = -999
        self.assertNotEqual(results[1]["players"][0]["matches"][0]["score"], -999)
        self.assertEqual(self.worker._player_tasks, {})

    async def test_cancelling_one_waiter_does_not_cancel_the_shared_fetch(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(tag, **kwargs):
            entered.set()
            await release.wait()
            return await self.identity(tag)
        self.search.search.side_effect = delayed
        first = asyncio.create_task(self.query())
        await entered.wait()
        second = asyncio.create_task(self.query())
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()
        result = await asyncio.wait_for(second, 3)
        self.assertEqual(result["players"][0]["rated_matches"], 12)
        self.assertEqual(self.search.search.await_count, 1)

    async def test_database_read_or_write_errors_do_not_fail_a_live_query(self):
        with patch.object(self.worker.player_cache, "get", side_effect=sqlite3.OperationalError("locked")), \
             patch.object(self.worker.player_cache, "put", side_effect=sqlite3.OperationalError("disk full")), \
             self.assertLogs("ow_api_server.batch_rating", level="WARNING"):
            result = (await self.query())["players"][0]
        self.assertEqual(result["rated_matches"], 12)
        self.assertEqual(result["data_cache"]["source"], "upstream")

    async def test_failed_sql_transaction_and_serialization_leave_the_old_row_untouched(self):
        await self.query()
        old, snapshot = self.rows(), self.snapshot()
        with self.assertRaises(ValueError):
            self.worker.player_cache.put(self.key(), PlayerSnapshot({"bad": float("nan")}, self.now))
        with closing(sqlite3.connect(self.worker.player_cache.path)) as db, db:
            db.execute("CREATE TRIGGER simulate_full BEFORE UPDATE ON player_snapshots BEGIN SELECT RAISE(ABORT, 'disk full'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.worker.player_cache.put(self.key(), PlayerSnapshot(snapshot.data, self.now + 0))
        self.assertEqual(self.rows(), old)

    async def test_startup_and_idle_maintenance_remove_expired_data_and_reports(self):
        await self.query()
        self.worker.report_dir.mkdir(parents=True)
        old_report = self.worker.report_dir / ("a" * 32 + ".json")
        old_report.write_text("{}")
        os.utime(old_report, (self.now, self.now))
        old_temporary = self.worker.report_dir / ("b" * 32 + ".tmp")
        old_temporary.write_text("{}");os.utime(old_temporary, (self.now, self.now))
        unrelated = self.worker.report_dir / "keep.json"
        unrelated.write_text("{}")
        self.now += RETAIN_SECONDS
        await self.worker.start_maintenance()
        self.assertEqual(self.rows(), [])
        self.assertFalse(old_report.exists())
        self.assertFalse(old_temporary.exists())
        self.assertTrue(unrelated.exists())
        # The background loop also cleans while no request is being made.
        await self.query()
        self.now += RETAIN_SECONDS
        await self.worker.close()
        with patch("api_server.batch_rating.service.CLEANUP_SECONDS", .01):
            await self.worker.start_maintenance()
            await self.query()
            self.now += RETAIN_SECONDS
            for _ in range(50):
                if not self.rows():
                    break
                await asyncio.sleep(.01)
        self.assertEqual(self.rows(), [])

    async def test_stale_report_does_not_extend_retention_and_is_expired_in_memory_and_on_disk(self):
        await self.query()
        stamp = self.now
        self.now += RETAIN_SECONDS - 10
        self.search.search.side_effect = OSError()
        job = self.worker.start("competitive", {"battletags": [TAG]})
        await self.worker.running[job["task_id"]]
        saved = self.worker.get_job(job["task_id"])
        self.assertEqual(saved["status"], "done")
        self.assertEqual(saved["result"]["players"][0]["data_cache"]["fetched_at"], stamp)
        report = self.worker.report_dir / (job["task_id"] + ".json")
        self.assertEqual(report.stat().st_mtime, stamp)
        self.now += 10
        self.assertIsNone(self.worker.get_job(job["task_id"]))
        self.assertFalse(report.exists())

    async def test_bad_json_and_future_timestamps_are_discarded(self):
        await self.query()
        with closing(sqlite3.connect(self.worker.player_cache.path)) as db, db:
            db.execute("UPDATE player_snapshots SET payload='{' WHERE cache_key=?", (self.key(),))
        self.assertIsNone(self.snapshot())
        self.assertEqual(self.rows(), [])
        await self.query()
        self.now -= 1
        self.assertIsNone(self.snapshot())

    async def test_same_match_reuses_details_for_teammate_and_opponent_without_flipping_results(self):
        async def full(tag, match_id):
            payload = await self.match_detail(tag, match_id)
            payload["data"]["matchRet"] = 1
            return payload
        self.client.query_match_info.side_effect = full
        await self.query()
        teammate = (await self.query(tags=["a#1234"]))["players"][0]
        opponent = (await self.query(tags=["enemy0#1234"]))["players"][0]
        self.assertEqual(self.client.query_match_info.await_count, 12)
        self.assertTrue(all(row["outcome"] == 1 for row in teammate["matches"]))
        self.assertTrue(all(row["outcome"] == -1 for row in opponent["matches"]))
        for row in opponent["matches"]:
            original = (await full(TAG, row["match_id"]))["data"]
            expected = score_match(original, {"matchId": row["match_id"]}, customer_token="", bnet_id="enemy0#1234", battletag="enemy0#1234", heroes=HEROES)
            self.assertEqual(row["score"], expected["score"])

    async def test_source_only_result_is_anchored_before_cross_team_detail_reuse(self):
        async def listing(tag, mode, **kwargs):
            payload = await self.match_list(tag, mode, **kwargs)
            for row in payload["data"]:
                row["matchRet"] = 1 if tag == TAG else 2
            return payload
        self.client.query_match_list.side_effect = listing
        own = (await self.query())["players"][0]
        other = (await self.query(tags=["enemy0#1234"]))["players"][0]
        self.assertEqual(self.client.query_match_info.await_count, 12)
        self.assertTrue(all(row["outcome"] == 1 for row in own["matches"]))
        self.assertTrue(all(row["outcome"] == -1 for row in other["matches"]))

    async def test_refresh_fetches_new_list_but_uses_history_until_its_own_24_hour_deadline(self):
        async def full(tag, match_id):
            payload = await self.match_detail(tag, match_id)
            payload["data"]["matchRet"] = 1
            return payload
        self.client.query_match_info.side_effect = full
        start = self.now
        await self.query()
        self.now += 301
        await self.query()
        self.assertEqual(self.client.query_match_list.await_count, 2)
        self.assertEqual(self.client.query_match_info.await_count, 12)
        self.now = start + RETAIN_SECONDS
        await self.query()
        self.assertEqual(self.client.query_match_info.await_count, 24)

    async def test_circuit_rejects_further_network_calls_after_authorization_failure(self):
        self.client.query_match_list.side_effect = None
        self.client.query_match_list.return_value = {"code": 401}
        for _ in range(3):
            with self.assertRaises(Exception):
                await self.worker._request(self.client.query_match_list, "private", "sport")
        self.assertEqual(self.client.query_match_list.await_count, 1)
        self.assertTrue(self.worker.circuit.status())

    async def test_privacy_errors_do_not_open_a_circuit_for_other_players(self):
        self.client.query_match_list.side_effect = None
        self.client.query_match_list.return_value = {"code": 404}
        for _ in range(7):
            with self.assertRaises(Exception):
                await self.worker._request(self.client.query_match_list, "private", "sport")
        self.assertEqual(self.client.query_match_list.await_count, 7)
        self.assertEqual(self.worker.circuit.status(), {})

    async def test_circuit_cooldown_allows_recovery(self):
        circuit = UpstreamCircuit(threshold=2, cooldown=30)
        with patch("api_server.batch_rating.circuit.time.monotonic", return_value=100):
            circuit.failure("details");circuit.failure("details")
            self.assertEqual(circuit.remaining("details"), 30)
        with patch("api_server.batch_rating.circuit.time.monotonic", return_value=131):
            self.assertEqual(circuit.remaining("details"), 0)
            circuit.success("details")
            self.assertEqual(circuit.status(), {})

    async def test_http_cache_hits_bypass_new_heavy_query_cooldown_but_new_ids_do_not(self):
        from api_server.main import app
        import httpx
        self.state.config = {}
        self.state.batch_rating = self.worker
        previous = getattr(app.state, "ow_app", None)
        app.state.ow_app = self.state
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                first = await client.post("/api/batch-rating/competitive", json={"battletags": [TAG], "wait": True})
                second = await client.post("/api/batch-rating/competitive", json={"battletags": [TAG], "wait": True})
                self.assertEqual((first.status_code, second.status_code), (200, 200))
                self.assertEqual(second.json()["data"]["players"][0]["data_cache"]["source"], "cache")
                third = await client.post("/api/batch-rating/competitive", json={"battletags": ["其他#1234"], "wait": True})
                self.assertEqual(third.status_code, 429)
                self.assertEqual(self.search.search.await_count, 1)
        finally:
            app.state.ow_app = previous


if __name__ == "__main__":
    unittest.main()
