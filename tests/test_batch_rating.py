import asyncio
import base64
from copy import deepcopy
import io
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from PIL import Image

from fastapi.testclient import TestClient

from api_server.app_context import AppState
from api_server.main import app
from api_server.batch_rating.ocr import extract_tags, normalize_tags, recognize_image
from api_server.batch_rating.scoring import WEIGHTS, aggregate_player, grouped_summary, rank_players, score_match
from api_server.batch_rating.service import BatchRatingService, QueryFailure

HEROES = {"t": {"name": "坦克", "roleType": "tank"}, "d": {"name": "输出", "roleType": "dps"}, "s": {"name": "血律", "roleType": "healer"}}


def player(name, hero, damage=6000, final=8, deaths=5, heal=0):
    return {"name": name, "bnetId": name, "heroGuid": hero, "heroDamage": damage, "finalHit": final,
            "death": deaths, "cure": heal, "kill": 20, "assist": 10, "resistDamage": 3000 if hero == "t" else 0}


def match(role="d"):
    return {"gameTimeSec": 600, "teammateList": [player("me#1234", role, heal=6000 if role == "s" else 0),
            player("a#1234", "t"), player("b#1234", "d"), player("c#1234", "s", damage=2500, final=3, deaths=3, heal=7000),
            player("e#1234", "s", damage=2000, final=2, deaths=4, heal=6000)], "enemyList": [player("enemy#1234", "d", damage=50000)]}


def score(data):
    return score_match(data, {"matchId": "m1", "gameMode": "LeisurePreset", "beginTs": 1792227400484},
                       customer_token="", bnet_id="me#1234", battletag="me#1234", heroes=HEROES)


class ScoringTests(unittest.TestCase):
    def test_ocr_number_corrections_and_chinese_spacing(self):
        tags, changes = extract_tags("屏 蔽 列 表\n队 友 甲 # S1807\n队 友 乙 ＃ S722\n队 友 丙 # 5371\n队 友 丁 # 5155")
        self.assertEqual(tags, ["队友甲#51807", "队友乙#5722", "队友丙#5371", "队友丁#5155"])
        self.assertEqual(len(changes), 2)

    def test_tags_deduplicate_and_reject_incomplete_id(self):
        self.assertEqual(normalize_tags(["name#1234", "name#1234"]), ["name#1234"])
        with self.assertRaises(ValueError): normalize_tags(["name"])

    def test_exact_fleta_definition_uses_half_the_team_final_blows(self):
        data = match()
        data["teammateList"][0]["kill"] = 500
        result = score(data)
        self.assertAlmostEqual(result["fleta"], 8 / (29 * .5), places=5)

    def test_enemy_statistics_and_victory_do_not_change_score(self):
        data = match()
        before = score(data)["score"]
        data["enemyList"][0].update(heroDamage=999999, death=0, finalHit=999)
        data["matchRet"] = -1
        self.assertEqual(score(data)["score"], before)

    def test_target_on_other_side_uses_its_actual_team(self):
        data = match()
        expected = score(data)["score"]
        data["enemyList"], data["teammateList"] = data["teammateList"], data["enemyList"]
        self.assertEqual(score(data)["score"], expected)

    def test_unknown_player_never_defaults_to_first_row(self):
        data = match()
        data["teammateList"][0]["name"] = "different#1234"
        data["teammateList"][0]["bnetId"] = "different#1234"
        with self.assertRaisesRegex(ValueError, "没有找到"):
            score(data)

    def test_damage_and_final_blows_are_normalized_to_ten_minutes(self):
        data = match()
        data["gameTimeSec"] = 300
        result = score(data)
        self.assertEqual(result["metrics"]["damage"]["per10"], 12000)
        self.assertEqual(result["metrics"]["final_blows"]["per10"], 16)

    def test_dps_final_blows_have_more_weight_than_tank_and_deaths_less(self):
        dps, tank = score(match("d")), score(match("t"))
        self.assertGreater(dps["metrics"]["final_blows"]["weight"], tank["metrics"]["final_blows"]["weight"])
        self.assertLess(dps["metrics"]["survival"]["weight"], tank["metrics"]["survival"]["weight"])

    def test_dps_thresholds_use_real_share_and_major_bonus_at_half(self):
        data = match()
        for p in data["teammateList"][1:]: p["finalHit"] = 0
        for own, expected_regular, expected_major in [(25,False,False),(39,False,False),(40,True,False),(49,True,False),(50,True,True),(60,True,True)]:
            with self.subTest(share=own):
                data["teammateList"][0]["finalHit"] = own
                data["teammateList"][1]["finalHit"] = 100-own
                result = score(data)
                self.assertEqual(any("40%" in a["reason"] for a in result["adjustments"]), expected_regular)
                self.assertEqual(any(a["points"] >= 18 for a in result["adjustments"]), expected_major)
                self.assertAlmostEqual(result["fleta"], own/50)
                self.assertAlmostEqual(result["final_blow_share"], own/100)

    def test_fleta_over_one_hundred_percent_is_not_clamped(self):
        data = match()
        data["teammateList"][0]["finalHit"] = 80
        self.assertGreater(score(data)["fleta"], 1)

    def test_support_highest_deaths_carry_large_penalty(self):
        low = match("s")
        low["teammateList"][0]["death"] = 2
        high = deepcopy(low)
        high["teammateList"][0]["death"] = 15
        self.assertGreater(score(low)["score"] - score(high)["score"], 25)
        self.assertTrue(any(a["points"] < -20 for a in score(high)["adjustments"]))

    def test_support_fleta_bonus_cannot_erase_highest_death_penalty(self):
        data = match("s")
        data["teammateList"][0].update(death=16, finalHit=15)
        result = score(data)
        self.assertGreaterEqual(result["fleta"], .30)
        self.assertLess(sum(a["points"] for a in result["adjustments"]), -10)

    def test_tank_thirty_percent_bonus(self):
        data = match("t")
        for p in data["teammateList"][1:]: p["finalHit"] = 0
        for own in (15,29,30):
            data["teammateList"][0]["finalHit"] = own
            data["teammateList"][1]["finalHit"] = 100-own
            self.assertEqual(any("30%" in a["reason"] for a in score(data)["adjustments"]), own>=30)

    def test_missing_final_hits_are_not_treated_as_zero(self):
        data = match()
        del data["teammateList"][0]["finalHit"]
        with self.assertRaisesRegex(ValueError, "关键统计缺失"): score(data)

    def test_zero_team_final_hits_do_not_divide_by_zero(self):
        data = match()
        for p in data["teammateList"]: p["finalHit"] = 0
        self.assertIsNone(score(data)["fleta"])

    def test_four_players_have_one_two_one_tiers_and_unrated_is_not_lower(self):
        players = [{"battletag": str(i), "score": n} for i, n in enumerate([90, 80, 65, 40])]
        missing = aggregate_player("empty#1234", [], fetched=0, errors=[])
        ranked = rank_players(players + [missing])
        self.assertEqual([p["tier"] for p in ranked[:4]], ["上等马", "中等马", "中等马", "下等马"])
        self.assertIsNone(ranked[4]["rank"])
        self.assertEqual(ranked[4]["tier"], "未评级")

    def test_all_ties_are_not_artificially_split(self):
        ranked = rank_players([{"battletag": str(i), "score": 70} for i in range(4)])
        self.assertEqual({p["tier"] for p in ranked}, {"中等马"})
        self.assertEqual({p["rank"] for p in ranked}, {1})

    def test_partial_sample_reports_actual_count(self):
        item = score(match())
        result = aggregate_player("me#1234", [item] * 3, fetched=4, errors=[{"error": "missing"}])
        self.assertEqual(result["rated_matches"], 3)
        self.assertEqual(result["status"], "partial")
        self.assertLessEqual(result["confidence"], .25)

    def test_summary_includes_all_tiers_scores_and_corrected_fleta(self):
        players = [{"battletag": f"player{i}#1234", "score": value, "rated_matches": 12, "fleta": .60}
                   for i, value in enumerate([90, 80, 65, 40])]
        text = grouped_summary(rank_players(players))
        self.assertIn("我方上等马：player0(90.0/F60%)", text)
        self.assertIn("我方中等马：player1", text)
        self.assertIn("、player2", text)
        self.assertIn("我方下等马：player3", text)
        self.assertNotIn("#", text)
        self.assertLess(len(text), 130)

    def test_damage_matters_for_all_roles_but_less_for_support(self):
        for weights in WEIGHTS.values(): self.assertAlmostEqual(sum(weights.values()), 1)
        self.assertGreater(WEIGHTS["support"]["damage"], .10)
        self.assertLess(WEIGHTS["support"]["damage"], WEIGHTS["dps"]["damage"])
        data = match("s")
        data["teammateList"][0]["death"] = 2
        data["teammateList"][0]["heroDamage"] = 500
        low = score(data)
        data["teammateList"][0]["heroDamage"] = 10000
        self.assertGreater(score(data)["score"] - low["score"], 8)

    def test_support_attack_bonus_accelerates_and_recovers_deaths_only_with_high_share(self):
        data = match("s")
        for p in data["teammateList"][1:]: p["finalHit"] = 0
        bonuses, scores = [], []
        for own in (10,20,30,40,50,60):
            data["teammateList"][0].update(death=16,finalHit=own)
            data["teammateList"][1]["finalHit"] = 100-own
            result = score(data)
            bonuses.append(sum(a["points"] for a in result["adjustments"] if a["points"]>0))
            scores.append(result["score"])
        self.assertEqual(bonuses[:2], [0,0])
        self.assertGreater(bonuses[4]-bonuses[3], bonuses[3]-bonuses[2])
        self.assertGreater(scores[-1]-scores[0], 30)

    def test_smurf_requires_five_matches_and_counts_exactly_one_hundred(self):
        normal = score(match())
        data = match()
        for p in data["teammateList"]: p["finalHit"] = 0
        data["teammateList"][0]["finalHit"] = 20
        data["teammateList"][1]["finalHit"] = 20
        dominant = score(data)
        self.assertEqual(dominant["fleta"], 1)
        for count in (4,5):
            report = aggregate_player("me#1234", [dominant]*count+[normal]*(12-count), fetched=12,errors=[])
            self.assertEqual(report["smurf_matches"], count)
            self.assertEqual(report["is_smurf"], count>=5)
            self.assertEqual(report["grade"]=="SMURF", count>=5)

    def test_stomp_zero_zero_x_discounts_either_side_without_altering_single_game_score(self):
        for own_weak in (False,True):
            data = match()
            data["enemyList"] = [player(f"enemy{i}#1234","d") for i in range(5)]
            team = data["teammateList"] if own_weak else data["enemyList"]
            for p in team: p.update(kill=0,assist=0,death=8,finalHit=0)
            result = score(data)
            self.assertTrue(result["is_stomp"])
            self.assertEqual(result["sample_weight"], .25)
            data["teammateList"],data["enemyList"] = data["enemyList"],data["teammateList"]
            self.assertEqual(score(data)["sample_weight"], .25)

    def test_all_negative_stomp_and_balanced_game_weights(self):
        data = match()
        self.assertEqual(score(data)["sample_weight"], 1)
        for p in data["teammateList"]: p.update(kill=3,death=6)
        self.assertEqual(score(data)["sample_weight"], .45)

    def test_weighted_aggregation_and_win_rate_are_separate(self):
        one, two = score(match()), score(match())
        one.update(score=90,sample_weight=.25,is_stomp=True,outcome_label="胜")
        two.update(score=50,sample_weight=1,outcome_label="负")
        report = aggregate_player("me#1234",[one,two],fetched=2,errors=[])
        self.assertEqual(report["score"], 58)
        self.assertEqual(report["win_rate"], .5)
        self.assertEqual(report["effective_matches"], 1.25)
        two["outcome_label"]="胜"
        self.assertEqual(aggregate_player("me#1234",[one,two],fetched=2,errors=[])["score"],58)

    def test_hero_and_map_assets_are_resolved_safely(self):
        heroes = deepcopy(HEROES)
        heroes["d"]["icon"]="https://example.com/hero.png"
        data = match()
        data["mapGuid"]="map1"
        result = score_match(data,{},customer_token="",bnet_id="me#1234",battletag="me#1234",heroes=heroes,
                             maps={"map1":{"name":"测试地图","icon":"https://example.com/map.png"}})
        self.assertEqual(result["map_name"],"测试地图")
        self.assertEqual(result["hero_icon"],"https://example.com/hero.png")
        self.assertEqual(result["map_icon"],"https://example.com/map.png")
        self.assertIn("contribution",result["metrics"]["damage"])

    def test_hero_calibration_retains_role_without_exposing_internal_adjustment(self):
        data = match("s")
        before = score(data)
        heroes = deepcopy(HEROES)
        heroes["s"]["name"]="天使"
        after = score_match(data,{},customer_token="",bnet_id="me#1234",battletag="me#1234",heroes=heroes)
        self.assertEqual(after["score"],round(before["score"]*.72,2))
        self.assertFalse(any("天使" in a["reason"] for a in after["adjustments"]))


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_queues_are_isolated_sorted_and_deduplicated(self):
        calls = []
        async def fetch(token, mode, page, season):
            calls.append(mode)
            if page != 1 or season is not None: return {"code": 0, "data": []}
            rows = [{"matchId": f"m{i}", "gameMode": "LeisurePreset" if mode == "leisure" else "SportPreset", "beginTs": i} for i in range(15)]
            rows += [{"matchId": "foreign", "gameMode": "SportFight", "beginTs": 100}, rows[0]]
            return {"code": 0, "data": rows}
        worker = BatchRatingService(SimpleNamespace(api_client=SimpleNamespace(query_match_list=fetch)))
        for mode, upstream in [("leisure", "leisure"), ("competitive", "sport")]:
            calls.clear()
            rows, _ = await worker._recent("private", mode)
            self.assertEqual(len(rows), 12)
            self.assertEqual([x["beginTs"] for x in rows], list(range(14, 2, -1)))
            self.assertEqual(set(calls), {upstream})

    async def test_upstream_error_is_not_reported_as_no_matches(self):
        async def fetch(*args, **kwargs): return {"code": 401, "data": []}
        worker = BatchRatingService(SimpleNamespace(api_client=SimpleNamespace(query_match_list=fetch)))
        with self.assertRaises(QueryFailure): await worker._recent("private", "leisure")

    async def test_short_pages_do_not_skip_newer_matches(self):
        async def fetch(token, mode, page, season):
            values = range(20, 25) if page == 1 else range(10, 20) if page == 2 else []
            return {"code": 0, "data": [{"matchId": str(i), "gameMode": "LeisurePreset", "beginTs": i} for i in values]}
        worker = BatchRatingService(SimpleNamespace(api_client=SimpleNamespace(query_match_list=fetch)))
        rows, seasons = await worker._recent("private", "leisure")
        self.assertEqual([r["beginTs"] for r in rows], list(range(24, 12, -1)))
        self.assertEqual(seasons, ["current"])

    async def test_missing_new_matches_backfills_from_same_queue_older_season(self):
        async def fetch(token, mode, page, season):
            rows = [{"matchId": str(i), "gameMode": "SportPreset", "beginTs": i} for i in range(12)] if season == 24 else []
            return {"code": 0, "data": rows}
        worker = BatchRatingService(SimpleNamespace(api_client=SimpleNamespace(query_match_list=fetch)))
        rows, seasons = await worker._recent("private", "competitive")
        self.assertEqual(len(rows), 12)
        self.assertEqual(seasons, ["current", 24])


class OCRRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_linux_chinese_ocr_uses_local_engine_and_extracts_tags(self):
        image = io.BytesIO()
        Image.new("RGB", (50, 50), "white").save(image, format="PNG")
        process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=("队 友 甲 # 1234".encode(), b"")))
        with patch("api_server.batch_rating.ocr.sys.platform", "linux"), \
             patch("api_server.batch_rating.ocr.recognize_neural", side_effect=ImportError("fallback test")), \
             patch("api_server.batch_rating.ocr.shutil.which", return_value="/usr/bin/tesseract"), \
             patch("api_server.batch_rating.ocr.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=process) as execute:
            result = await recognize_image(base64.b64encode(image.getvalue()).decode())
        self.assertEqual(result["battletags"], ["队友甲#1234"])
        self.assertEqual(result["engine"], "Tesseract")
        self.assertIn("chi_sim+eng", execute.call_args.args)

    async def test_malformed_image_is_rejected_before_ocr(self):
        with self.assertRaisesRegex(ValueError, "无法读取图片"):
            await recognize_image(base64.b64encode(b"not an image").decode())


class EndpointTests(unittest.TestCase):
    def test_new_query_endpoints_require_configured_account(self):
        previous = getattr(app.state, "ow_app", None)
        app.state.ow_app = AppState()
        client = TestClient(app)
        try:
            for mode in ["leisure", "competitive"]:
                response = client.post('/api/batch-rating/' + mode, json={"battletags": ["me#1234"]})
                self.assertEqual(response.status_code, 503)
        finally:
            client.close()
            app.state.ow_app = previous


if __name__ == '__main__': unittest.main()
