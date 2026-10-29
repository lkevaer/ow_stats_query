"""Single-game ceilings, participation checks, and finite zero-death rewards."""
import math
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from api_server.batch_rating.context import kd_stats
from api_server.batch_rating.match_policy import EMPTY_QUERY_NOTICE, compress_positive_adjustments, zero_death_bonus
from api_server.batch_rating.player_cache import PlayerSnapshot
from api_server.batch_rating.scoring import aggregate_player, stomp_weight
from api_server.batch_rating.service import BatchRatingService
from test_batch_rating import HEROES, player, score
from test_rating_context import full_match


def finished(role="s"):
    data = full_match(role, deaths=(5, 5, 5, 5, 5))
    data["matchRet"] = 1
    return data


class SingleGamePolicyTests(unittest.TestCase):
    def test_stacked_extreme_bonuses_never_produce_single_game_sss_on_either_side(self):
        for role in ("s", "d", "t"):
            for outcome in (-1, 0, 1):
                with self.subTest(role=role, outcome=outcome):
                    data = finished(role)
                    data["matchRet"] = outcome
                    data["teammateList"][0].update(heroDamage=1000000, cure=1000000, kill=1000, finalHit=1000, death=0)
                    result = score(data)
                    self.assertLessEqual(result["score"], 95 if outcome == -1 else 99)
                    self.assertNotEqual(result["grade"], "SSS")
                    self.assertLess(result["positive_bonus_scale"], 1)
                    self.assertAlmostEqual(result["score"], result["base_score"] + sum(a["points"] for a in result["adjustments"]), delta=.02)
                    self.assertTrue(all(math.isfinite(a["points"]) for a in result["adjustments"]))

    def test_bonuses_are_monotone_diminishing_and_preserve_negative_adjustments(self):
        previous = 0
        for total in (1, 6, 20, 50, 1000):
            items = [{"points": total / 2}, {"points": total / 2}, {"points": -7}]
            compress_positive_adjustments(items, -1)
            effective = sum(a["points"] for a in items if a["points"] > 0)
            self.assertGreater(effective, previous)
            self.assertLessEqual(effective, 12)
            self.assertEqual(items[-1]["points"], -7)
            previous = effective

    def test_zero_death_kd_is_finite_without_changing_recorded_deaths(self):
        for kills in (0, 1, 7, 12):
            data = finished("d")
            data["teammateList"][0].update(kill=kills, death=0)
            result = score(data)
            self.assertEqual(result["kd"], kills)
            self.assertEqual(result["stats"]["death"], 0)
            self.assertEqual(result["kd_deaths_used"], 1)
            self.assertFalse(result["kd_perfect"])
        self.assertIsNone(kd_stats(None, 0)["kd"])

    def test_zero_death_reward_applies_to_every_role_and_rises_with_teammate_deaths(self):
        for role in ("s", "d", "t"):
            rewards = []
            for deaths in (1, 5, 10):
                data = finished(role)
                data["teammateList"][0]["death"] = 0
                for row in data["teammateList"][1:]:
                    row["death"] = deaths
                result = score(data)
                rewards.append(sum(a["points"] for a in result["adjustments"] if "零阵亡" in a["reason"]))
            self.assertGreater(rewards[0], 2)
            self.assertLess(rewards[0], rewards[1])
            self.assertLess(rewards[1], rewards[2])
        active, others = player("active", "d", deaths=0), [player("other", "d", deaths=10)]
        self.assertLess(zero_death_bonus(active, others, 60), zero_death_bonus(active, others, 600))
        inactive = {key: 0 for key in ("death", "kill", "assist", "heroDamage", "cure", "resistDamage", "finalHit")}
        self.assertEqual(zero_death_bonus(inactive, others, 600), 0)

    def test_enemy_statistics_do_not_change_the_individual_score_or_bonus(self):
        data = finished()
        data["teammateList"][0]["death"] = 0
        before = score(data)
        for row in data["enemyList"]:
            row.update(heroDamage=999999, death=50, kill=60, finalHit=1)
        after = score(data)
        self.assertEqual(before["score"], after["score"])
        self.assertEqual(before["adjustments"], after["adjustments"])

    def test_support_fleta_thirty_five_adds_a_real_bonus_without_death_exemption(self):
        results = []
        for value in (34.99, 35, 40, 50):
            data = finished()
            for row in data["teammateList"]:
                row["finalHit"] = 0
            data["teammateList"][0].update(finalHit=value, death=12)
            data["teammateList"][1]["finalHit"] = 200-value
            results.append(score(data))
        self.assertFalse(any("35%" in a["reason"] for a in results[0]["adjustments"]))
        self.assertTrue(any("35%" in a["reason"] and a["points"] > 0 for a in results[1]["adjustments"]))
        self.assertFalse(results[1]["support_death_exempt"])
        self.assertTrue(results[-1]["support_death_exempt"])
        self.assertEqual(sorted(r["score"] for r in results), [r["score"] for r in results])
        self.assertGreater(results[1]["score"], results[0]["score"])


class BotParticipationTests(unittest.TestCase):
    def test_zero_damage_support_gets_gray_bot_data_and_strongest_adjustment(self):
        data = finished()
        data["teammateList"][0].update(heroDamage=0, death=0, cure=20000)
        result = score(data)
        self.assertTrue(result["is_bot"])
        self.assertTrue(result["completed_match"])
        self.assertLessEqual(result["score"], 25)
        deduction = next(a["points"] for a in result["adjustments"] if "BOT" in a["reason"])
        self.assertLess(deduction, -30)
        self.assertAlmostEqual(result["score"], result["base_score"] + sum(a["points"] for a in result["adjustments"]), delta=.02)
        summary = aggregate_player("me#1234", [result]*12, fetched=12, errors=[])
        self.assertTrue(summary["is_bot"])
        self.assertEqual(summary["bot_matches"], 12)
        self.assertLessEqual(summary["score"], 25)

    def test_short_incomplete_aborted_or_left_games_are_not_labeled_bot(self):
        cases = [
            lambda d: d.update(gameTimeSec=389),
            lambda d: d.pop("matchRet"),
            lambda d: d.update(matchRet=True),
            lambda d: d["enemyList"].pop(),
            lambda d: d["enemyList"][0].pop("heroDamage"),
            lambda d: d["teammateList"][0].update(isLeaver=True),
            lambda d: d["teammateList"][0].update(playedTimeSec=200),
            lambda d: d.update(isComplete=False),
            lambda d: d.update(matchStatus="aborted"),
        ]
        for index, change in enumerate(cases):
            with self.subTest(case=index):
                data = finished()
                data["teammateList"][0]["heroDamage"] = 0
                change(data)
                self.assertFalse(score(data)["is_bot"])

    def test_missing_damage_is_not_zero_damage_and_other_roles_are_not_bot(self):
        data = finished()
        data["teammateList"][0].pop("heroDamage")
        with self.assertRaisesRegex(ValueError, "关键统计缺失"):
            score(data)
        for role in ("d", "t"):
            data = finished(role)
            data["teammateList"][0]["heroDamage"] = 0
            self.assertFalse(score(data)["is_bot"])

    def test_boundary_duration_full_roster_and_objective_time_do_not_imply_early_leave(self):
        data = finished()
        data["gameTimeSec"] = 390
        data["teammateList"][0].update(heroDamage=0, targetCompetingTime=0)
        self.assertTrue(score(data)["is_bot"])
        data["teammateList"].append(player("sixth-ally", "d"))
        data["enemyList"].append(player("sixth-enemy", "d"))
        self.assertTrue(score(data)["is_bot"])
        data["enemyList"][-1]["bnetId"] = data["enemyList"][0]["bnetId"]
        self.assertFalse(score(data)["is_bot"])

    def test_one_bot_game_does_not_label_the_entire_player_bot(self):
        data = finished()
        regular = score(data)
        data["teammateList"][0]["heroDamage"] = 0
        bot = score(data)
        result = aggregate_player("me#1234", [bot, regular], fetched=2, errors=[])
        self.assertEqual(result["bot_matches"], 1)
        self.assertFalse(result["is_bot"])


class QuarterStompTests(unittest.TestCase):
    def test_quarter_boundary_applies_the_same_discount_to_both_teams(self):
        data = finished("d")
        for row in data["teammateList"]:
            row.update(finalHit=20, death=5, kill=20)
        for row in data["enemyList"]:
            row.update(finalHit=5, death=5, kill=20)
        result = score(data)
        self.assertEqual(result["stomp_weight"], .4)
        self.assertIn("四分之一", result["stomp_reason"])
        self.assertTrue(all(row["stomp_weight"] == .4 for row in result["match_details"]["other_players"]))
        data["enemyList"][0]["finalHit"] = 5.1
        self.assertEqual(score(data)["stomp_weight"], 1)
        data["matchRet"] = -1
        self.assertEqual(score(data)["stomp_weight"], 1)

    def test_missing_result_or_zero_or_missing_totals_do_not_invent_a_ratio(self):
        data = finished("d")
        for team in (data["teammateList"], data["enemyList"]):
            for row in team:
                row["finalHit"] = 0
        self.assertEqual(score(data)["stomp_weight"], 1)
        for row in data["teammateList"]:
            row["finalHit"] = 20
        data.pop("matchRet")
        self.assertEqual(score(data)["stomp_weight"], 1)
        data["matchRet"] = 1
        data["enemyList"][0].pop("finalHit")
        self.assertEqual(stomp_weight([data["teammateList"], data["enemyList"]], winner=0), (1., ""))


class EmptyDataTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_search_and_empty_matches_report_advice_without_a_fake_score(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "OW_RATING_CACHE_FILE": str(Path(folder)/"cache.sqlite3"), "OW_RATING_REPORT_DIR": folder,
        }):
            state = SimpleNamespace(bnet_search=SimpleNamespace(search=AsyncMock(return_value=SimpleNamespace(result=None))))
            worker = BatchRatingService(state)
            try:
                result = await worker._load_player("unused", "me#1234", "competitive", HEROES, {}, [], lambda _: None)
                self.assertIsNone(result["score"])
                self.assertIn(EMPTY_QUERY_NOTICE, result["sample_note"])
                snapshot = PlayerSnapshot({"identity": {"customer_token": "", "bnet_id": "me#1234", "full_id": "me#1234"},
                                           "matches": [], "fetched": 0, "seasons_scanned": 1, "errors": []}, 1_800_000_000)
                result = worker._rate_snapshot("me#1234", snapshot, HEROES, {}, [])
                self.assertEqual(result["empty_notice"], EMPTY_QUERY_NOTICE)
                self.assertIn("未混入其他模式", result["sample_note"])
            finally:
                await worker.close()
