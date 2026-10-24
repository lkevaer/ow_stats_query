import asyncio
from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from api_server.app_context import AppState
from api_server.hero_rates import (
    DEFAULT_FILTERS,
    HeroRatesCache,
    parse_public_statistics,
)
from api_server.main import app
from api_server.batch_rating.scoring import match_roster
from api_server.batch_rating.service import BatchRatingService


def public_page():
    heroes = [
        {
            "heroId": "winston",
            "role": "tank",
            "subrole": "initiator",
            "winRate": 55.4,
            "pickRate": 23.3,
            "banRate": 7.9,
        },
        {
            "heroId": "domina",
            "role": "tank",
            "subrole": "stalwart",
            "winRate": 53.9,
            "pickRate": 4,
            "banRate": 3.5,
        },
        {
            "heroId": "doctrine",
            "role": "support",
            "subrole": "survivor",
            "winRate": 57.9,
            "pickRate": 10,
            "banRate": 5,
        },
        {
            "heroId": "anran",
            "role": "damage",
            "subrole": "flanker",
            "winRate": None,
            "pickRate": 2,
            "banRate": None,
        },
    ]
    values = []

    def pack(value):
        index = len(values)
        values.append(None)
        if isinstance(value, dict):
            values[index] = {key: pack(item) for key, item in value.items()}
        elif isinstance(value, list):
            values[index] = [pack(item) for item in value]
        else:
            values[index] = value
        return index

    pack({"hero-rate-list": {"status": "success", "data": {"list": heroes}}})
    # Nuxt also records missing errors under the same key with a -1 reference.
    values.append({"hero-rate-list": -1})
    return (
        '<script type="application/json" id="__NUXT_DATA__">'
        + json.dumps(values)
        + "</script>"
    )


class HeroRatesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.now = 1000
        self.requests = []
        self.fail = False

        async def handler(request):
            self.requests.append(request)
            await asyncio.sleep(0)
            return httpx.Response(503 if self.fail else 200, text=public_page())

        self.transport = httpx.MockTransport(handler)
        self.cache = HeroRatesCache(
            Path(self.directory.name), transport=self.transport, clock=lambda: self.now
        )

    async def asyncTearDown(self):
        await self.cache.close()
        self.directory.cleanup()

    async def test_positions_share_one_persisted_snapshot_and_use_national_names(self):
        reports = await asyncio.gather(
            *(self.cache.get({"role": role}) for role in ["tank", "support", "damage"])
        )
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].url.params["role"], "all")
        self.assertEqual(
            [row["name"] for row in reports[0]["heroes"]], ["温斯顿", "金驭"]
        )
        self.assertEqual(reports[1]["heroes"][0]["name"], "血律")
        self.assertIsNone(reports[2]["heroes"][0]["winRate"])
        self.assertEqual(reports[0]["filters"], {**DEFAULT_FILTERS, "role": "tank"})
        await self.cache.close()
        self.cache = HeroRatesCache(
            Path(self.directory.name), transport=self.transport, clock=lambda: self.now
        )
        await self.cache.get({"role": "tank"})
        self.assertEqual(len(self.requests), 1)

    async def test_expiry_refreshes_once_and_failed_refresh_preserves_timestamp_and_data(
        self,
    ):
        before = await self.cache.get()
        self.now += 21601
        self.fail = True
        stale = await self.cache.get()
        await asyncio.gather(*self.cache.refreshing.values())
        again = await self.cache.get()
        self.assertTrue(stale["stale"])
        self.assertTrue(again["stale"])
        self.assertEqual(again["fetched_at"], before["fetched_at"])
        self.assertEqual(again["heroes"], before["heroes"])
        self.assertEqual(len(self.requests), 2)
        self.fail = False
        self.now += 301
        await self.cache.get()
        await asyncio.gather(*self.cache.refreshing.values())
        self.assertFalse((await self.cache.get())["stale"])
        self.assertEqual(len(self.requests), 3)

    async def test_filter_combinations_are_separate_and_subroles_filter_locally(self):
        await self.cache.get()
        report = await self.cache.get({"rank": "diamond", "role": "initiator"})
        self.assertEqual(report["filter_labels"]["rank"], "钻石")
        self.assertEqual([row["heroId"] for row in report["heroes"]], ["winston"])
        await self.cache.get({"rank": "diamond", "role": "support"})
        self.assertEqual(len(self.requests), 2)

    async def test_refresh_boundary_is_six_hours_and_roles_share_the_refresh(self):
        before = await self.cache.get()
        self.assertEqual(before["refresh_after"] - before["fetched_at"], 21600)
        self.now += 21599
        self.assertFalse((await self.cache.get({"role": "tank"}))["stale"])
        self.assertEqual(len(self.requests), 1)
        self.now += 1
        reports = await asyncio.gather(*(self.cache.get({"role": role}) for role in ["tank", "damage", "support"]))
        await asyncio.gather(*self.cache.refreshing.values())
        self.assertTrue(any(report["stale"] for report in reports))
        self.assertEqual(len(self.requests), 2)
        self.assertFalse((await self.cache.get())["stale"])

    async def test_invalid_filters_never_fetch_and_quick_mode_has_no_rank(self):
        for selected in [{"rank": "invented"}, {"input": "console"}, {"map": "escort"}]:
            with self.assertRaises(ValueError):
                await self.cache.get(selected)
        self.assertEqual(self.requests, [])
        quick = await self.cache.get({"rq": "0"})
        self.assertEqual(quick["filters"]["rank"], "all")
        self.assertEqual(self.requests[0].url.params["rank"], "all")

    async def test_upstream_failure_without_cache_returns_no_fabricated_table(self):
        self.fail = True
        with self.assertRaises(RuntimeError):
            await self.cache.get()
        with self.assertRaises(RuntimeError):
            await self.cache.get()
        self.assertEqual(len(self.requests), 1)


class RosterAndPageTests(unittest.TestCase):
    def test_ten_and_twelve_player_rosters_are_complete_and_never_include_tokens(self):
        for count in [5, 6]:
            data = {
                key: [
                    {
                        "name": f"玩家{team}{index}#1234",
                        "customerToken": "PRIVATE_TOKEN",
                    }
                    for index in range(count)
                ]
                for team, key in enumerate(["teammateList", "enemyList"])
            }
            roster = match_roster(data)
            self.assertTrue(roster["roster_complete"])
            self.assertEqual(len(roster["participants"]), count * 2)
            self.assertNotIn("PRIVATE_TOKEN", json.dumps(roster))
            data["enemyList"][0]["name"] = "隐藏昵称"
            self.assertFalse(match_roster(data)["roster_complete"])

    def test_duplicate_identifiers_do_not_pretend_to_be_a_complete_roster(self):
        data = {
            key: [{"name": "重复#1234"} for _ in range(5)]
            for key in ["teammateList", "enemyList"]
        }
        self.assertFalse(match_roster(data)["roster_complete"])

    def test_ssr_parser_ignores_error_references_and_rejects_non_data_pages(self):
        self.assertEqual(len(parse_public_statistics(public_page())["list"]), 4)
        for html in ["<h1>Unavailable</h1>", '<script id="__NUXT_DATA__">{}</script>']:
            with self.assertRaises(ValueError):
                parse_public_statistics(html)

    def test_hero_page_exposes_packaged_assets_and_all_original_filters(self):
        with closing(TestClient(app)) as client:
            response = client.get("/hero-rates")
            self.assertEqual(response.status_code, 200)
            self.assertIn("must-revalidate", response.headers["cache-control"])
            for name in ["role", "rq", "rank", "map", "input", "region"]:
                self.assertIn(f'id="{name}"', response.text)
            self.assertEqual(client.get("/static/hero_rates.js").status_code, 200)
            self.assertEqual(
                client.get("/static/hero_rates_banner.png").status_code, 200
            )
            self.assertEqual(
                client.get("/api/hero-rates/filters").json()["data"]["defaults"][
                    "rank"
                ],
                "master",
            )

    def test_api_passes_each_filter_and_reports_unavailable_or_invalid_conditions(self):
        worker = type("Cache", (), {"get": AsyncMock(return_value={"heroes": []})})()
        with (
            patch.object(app.state, "ow_app", AppState(hero_rates=worker), create=True),
            closing(TestClient(app)) as client,
        ):
            response = client.get(
                "/api/hero-rates?role=support&rank=diamond&region=europe&input=console&map=nepal&rq=0"
            )
            self.assertEqual(response.status_code, 200)
            worker.get.assert_awaited_once_with(
                {
                    "role": "support",
                    "rq": "0",
                    "rank": "diamond",
                    "map": "nepal",
                    "input": "console",
                    "region": "europe",
                }
            )
            worker.get.side_effect = ValueError("invalid filter")
            self.assertEqual(client.get("/api/hero-rates").status_code, 422)
            worker.get.side_effect = RuntimeError("unavailable")
            self.assertEqual(client.get("/api/hero-rates").status_code, 503)

    def test_twelve_player_exception_applies_only_to_full_match_requests(self):
        async def check():
            service = BatchRatingService(AppState())
            tags = [f"玩家{index}#1234" for index in range(12)]
            with self.assertRaises(ValueError):
                await service.prepare(tags, None)
            prepared = await service.prepare(tags, None, full_match=True)
            self.assertEqual(prepared["battletags"], tags)

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
