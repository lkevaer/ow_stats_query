from copy import deepcopy
import unittest

from api_server.batch_rating.context import duration_weight
from api_server.batch_rating.scoring import aggregate_player, calibrate_score, grade, score_match
from test_batch_rating import HEROES, match, score
from test_rating_context import full_match


def aggregate(items):
    return aggregate_player("me#1234", items, fetched=len(items), errors=[])


def summary_points(result, phrase):
    return sum(a["points"] for a in result["summary_adjustments"] if phrase in a["reason"])


class MatchWeightTests(unittest.TestCase):
    def test_short_games_discount_up_to_six_and_a_half_minutes(self):
        self.assertLess(duration_weight(180), duration_weight(300))
        self.assertLess(duration_weight(389), 1)
        self.assertEqual(duration_weight(390), 1)
        self.assertEqual(duration_weight(900), 1)
        self.assertGreater(duration_weight(901), 1)
        self.assertAlmostEqual(duration_weight(389.999), duration_weight(390), places=5)

    def test_long_game_bonus_is_modest_and_capped(self):
        self.assertGreater(duration_weight(1200), 1)
        self.assertLessEqual(duration_weight(1500), 1.15)
        self.assertEqual(duration_weight(36000), duration_weight(1500))

    def test_real_match_weight_combines_short_long_and_stomp_factors(self):
        data = match()
        data["gameTimeSec"] = 300
        short = score(data)
        self.assertTrue(short["short_match"])
        self.assertLess(short["sample_weight"], 1)
        data["gameTimeSec"] = 1200
        long = score(data)
        self.assertTrue(long["long_match"])
        self.assertGreater(long["sample_weight"], 1)
        self.assertEqual(short["fleta"], long["fleta"])
        for row in data["teammateList"]:
            row.update(kill=0, assist=0, finalHit=0, death=8)
        stomp = score(data)
        self.assertTrue(stomp["is_stomp"])
        self.assertLess(stomp["sample_weight"], .5)

    def test_nearly_equal_team_final_blows_receive_substantial_symmetric_weight(self):
        data = full_match("d")
        for row in data["teammateList"]:
            row["finalHit"] = 10
        for row in data["enemyList"]:
            row["finalHit"] = 9
        own = score(data)
        enemy = data["enemyList"][0]
        other = score_match(data, {}, customer_token="", bnet_id=enemy["bnetId"], battletag=enemy["name"], heroes=HEROES)
        self.assertTrue(own["balanced_final_blows"])
        self.assertGreaterEqual(own["balance_weight"], 1.25)
        self.assertEqual(own["sample_weight"], other["sample_weight"])
        self.assertEqual(own["opponent_final_blows"], 45)
        data["enemyList"][0]["finalHit"] = 8
        self.assertFalse(score(data)["balanced_final_blows"])

    def test_zero_missing_or_tiny_totals_do_not_fabricate_a_close_game(self):
        for value in (0, 1):
            data = full_match("d")
            for row in data["teammateList"] + data["enemyList"]:
                row["finalHit"] = value
            self.assertFalse(score(data)["balanced_final_blows"])
        data = full_match("d")
        del data["enemyList"][0]["finalHit"]
        self.assertFalse(score(data)["balanced_final_blows"])
        self.assertIsNone(score(data)["opponent_final_blows"])

    def test_weight_changes_aggregation_not_the_match_score(self):
        data = full_match("d")
        first = score(data)
        for row in data["enemyList"]:
            row["finalHit"] = data["teammateList"][0]["finalHit"]
        # Both full teams have 29 final blows, without changing the target's stats.
        for row, value in zip(data["enemyList"], (8, 8, 8, 3, 2)):
            row["finalHit"] = value
        balanced = score(data)
        self.assertEqual(first["score"], balanced["score"])
        self.assertGreater(balanced["sample_weight"], first["sample_weight"])
        first["score"], balanced["score"] = 50, 90
        result = aggregate([first, balanced])
        self.assertGreater(result["match_average"], 70)
        self.assertEqual(result["rated_matches"], 2)


class SupportFletaTests(unittest.TestCase):
    def support_with_fleta(self, percentage, deaths=16):
        data = match("s")
        for row in data["teammateList"]:
            row["finalHit"] = 0
        data["teammateList"][0].update(finalHit=percentage, death=deaths)
        data["teammateList"][1]["finalHit"] = 200 - percentage
        return score(data)

    def test_forty_and_fifty_use_fleta_not_final_blow_share(self):
        below = self.support_with_fleta(39)
        forty = self.support_with_fleta(40)
        fifty = self.support_with_fleta(50)
        self.assertAlmostEqual(forty["final_blow_share"], .20)
        self.assertAlmostEqual(fifty["final_blow_share"], .25)
        self.assertFalse(any("40%" in a["reason"] for a in below["adjustments"]))
        self.assertTrue(any("40%" in a["reason"] and a["points"] > 0 for a in forty["adjustments"]))
        self.assertTrue(any("50%" in a["reason"] and a["points"] > 0 for a in fifty["adjustments"]))
        self.assertGreater(forty["score"], below["score"])
        self.assertGreater(fifty["score"], forty["score"])

    def test_exact_fifty_waives_both_high_death_adjustment_and_survival_deduction(self):
        below = self.support_with_fleta(49)
        exact = self.support_with_fleta(50)
        self.assertFalse(below["support_death_exempt"])
        self.assertTrue(any(a["points"] < 0 for a in below["adjustments"]))
        self.assertTrue(exact["support_death_exempt"])
        self.assertGreaterEqual(exact["metrics"]["survival"]["contribution"], 0)
        self.assertFalse(any(a["points"] < 0 for a in exact["adjustments"]))
        self.assertTrue(any("不触发" in w for w in exact["warnings"]))

    def test_low_fleta_support_still_has_meaningful_death_penalty(self):
        low = self.support_with_fleta(30, deaths=2)
        high = self.support_with_fleta(30, deaths=16)
        self.assertGreater(low["score"] - high["score"], 25)

    def test_exemption_does_not_apply_to_tank_or_dps(self):
        for role in ("t", "d"):
            data = match(role)
            data["teammateList"][0].update(finalHit=20, death=16)
            result = score(data)
            self.assertGreater(result["fleta"], .50)
            self.assertFalse(result["support_death_exempt"])
            self.assertLess(result["metrics"]["survival"]["contribution"], 0)


class RecentFormTests(unittest.TestCase):
    def items(self):
        item = score(match())
        item.update(score=60, outcome_label="负")
        item["stats"].update(kill=30, death=5)
        return [deepcopy(item) for _ in range(12)]

    def test_ten_wins_trigger_hot_badge_and_much_higher_win_bonus(self):
        previous = 0
        for wins in (9, 10, 11, 12):
            items = self.items()
            for row in items[:wins]:
                row["outcome_label"] = "胜"
            result = aggregate(items)
            self.assertEqual(result["hot_streak"], wins >= 10)
            bonus = summary_points(result, "胜率")
            self.assertGreater(bonus, previous)
            if wins == 10:
                self.assertGreaterEqual(bonus, 3)
            previous = bonus

    def test_ten_partial_wins_do_not_claim_a_complete_twelve_game_hot_streak(self):
        items = self.items()[:10]
        for row in items:
            row["outcome_label"] = "胜"
        result = aggregate(items)
        self.assertFalse(result["hot_streak"])
        self.assertEqual(result["rated_matches"], 10)
        self.assertLess(summary_points(result, "胜率"), 2)

    def test_nine_games_at_exact_kd_seven_receive_high_bonus_not_eight(self):
        previous = None
        for count in (8, 9):
            items = self.items()
            for row in items[:count]:
                row["stats"].update(kill=35, death=5)
            result = aggregate(items)
            self.assertEqual(result["high_kd_matches"], count)
            self.assertEqual(result["consistent_high_kd"], count >= 9)
            bonus = summary_points(result, "持续高KD")
            self.assertEqual(bonus > 0, count >= 9)
            if previous is not None:
                self.assertGreater(result["score"] - previous, 6)
            previous = result["score"]

    def test_zero_zero_excluded_positive_zero_is_infinite_and_qualifies(self):
        items = self.items()
        for row in items[:9]:
            row["stats"].update(kill=1, death=0)
        self.assertEqual(aggregate(items)["high_kd_matches"], 9)
        items[0]["stats"]["kill"] = 0
        result = aggregate(items)
        self.assertEqual(result["high_kd_matches"], 8)
        self.assertFalse(result["consistent_high_kd"])

    def test_only_latest_twelve_games_count_toward_kd_and_hot_streak(self):
        items = self.items() + [deepcopy(self.items()[0])]
        for index, row in enumerate(items):
            row["begin_ts"] = index
            if index < 9:
                row["stats"]["kill"] = 35
        result = aggregate(items)
        self.assertEqual(result["rated_matches"], 12)
        self.assertEqual(result["high_kd_matches"], 8)
        self.assertFalse(result["consistent_high_kd"])


class ScoreScaleTests(unittest.TestCase):
    def test_old_seventy_is_eighty_without_clipping_the_upper_range(self):
        self.assertAlmostEqual(calibrate_score(70), 80)
        self.assertEqual(calibrate_score(0), 0)
        self.assertEqual(calibrate_score(100), 100)
        values = [calibrate_score(value) for value in range(101)]
        self.assertTrue(all(a < b for a, b in zip(values, values[1:])))

    def test_grade_thresholds(self):
        for value, expected in ((90,"S"),(89.99,"A"),(80,"A"),(79.99,"B"),(70,"B"),(69.99,"C"),(60,"C"),(59.99,"D")):
            self.assertEqual(grade(value), expected)

    def test_displayed_adjustments_reconcile_with_calibrated_base(self):
        for role in ("t", "d", "s"):
            for final in (3, 8, 25, 80):
                with self.subTest(role=role, final=final):
                    data = match(role)
                    data["teammateList"][0].update(finalHit=final, death=12)
                    result = score(data)
                    total = result["base_score"] + sum(a["points"] for a in result["adjustments"])
                    self.assertAlmostEqual(total, result["score"], delta=.02)


if __name__ == "__main__":
    unittest.main()
