from copy import deepcopy
import unittest

from api_server.batch_rating.context import party_info
from api_server.batch_rating.scoring import aggregate_player, grade, score_match, scoring_rules
from test_batch_rating import HEROES, match, player, score


def full_match(role="s", deaths=(6, 5, 5, 5, 5), enemy_deaths=5):
    data = match(role)
    for row, death in zip(data["teammateList"], deaths):
        row["death"] = death
    data["enemyList"] = [player(f"enemy{i}#1234", hero, heal=6000 if hero == "s" else 0, deaths=enemy_deaths)
                         for i, hero in enumerate(("t", "d", "d", "s", "s"))]
    return data


def death_penalty(result):
    return sum(-a["points"] for a in result["adjustments"] if a["points"] < 0)


class DeathContextTests(unittest.TestCase):
    def test_two_three_four_deaths_do_not_trigger_large_support_penalty(self):
        result = score(full_match(deaths=(4, 2, 3, 2, 3)))
        self.assertTrue(result["death_context"]["low_team_deaths"])
        self.assertLess(death_penalty(result), 4)
        self.assertGreater(result["metrics"]["survival"]["points"], 54)

    def test_six_or_seven_when_teammates_have_five_is_substantial(self):
        six = score(full_match(deaths=(6, 5, 5, 5, 5)))
        seven = score(full_match(deaths=(7, 5, 5, 5, 5)))
        self.assertGreater(death_penalty(six), 8)
        self.assertGreater(death_penalty(seven), death_penalty(six))

    def test_all_roles_reduce_death_sensitivity_in_low_death_games(self):
        for role in ("t", "d", "s"):
            with self.subTest(role=role):
                result = score(full_match(role, deaths=(4, 2, 3, 2, 3)))
                self.assertLess(result["death_context"]["sensitivity"], .5)
                self.assertGreater(result["metrics"]["survival"]["points"], 54)

    def test_all_ten_deaths_do_not_excuse_support_even_when_tied(self):
        result = score(full_match(deaths=(10, 10, 10, 10, 10), enemy_deaths=15))
        self.assertFalse(result["death_context"]["low_team_deaths"])
        self.assertGreaterEqual(death_penalty(result), 10)
        self.assertLessEqual(result["metrics"]["survival"]["points"], 36)
        self.assertFalse(any(a["reason"] == "辅助低阵亡" for a in result["adjustments"]))

    def test_enemy_death_advantage_waives_only_single_death_highest_premium(self):
        even = score(full_match(enemy_deaths=5))
        favorable = score(full_match(enemy_deaths=9))
        self.assertFalse(favorable["is_stomp"])
        self.assertTrue(favorable["death_context"]["enemy_deaths_clearly_higher"])
        self.assertGreater(death_penalty(even) - death_penalty(favorable), 4)
        self.assertGreater(death_penalty(favorable), 0)
        self.assertTrue(any("免除" in w for w in favorable["warnings"]))
        two_extra = score(full_match(deaths=(7, 5, 5, 5, 5), enemy_deaths=10))
        self.assertFalse(any("免除" in w for w in two_extra["warnings"]))

    def test_missing_opponents_never_imply_a_favorable_death_advantage(self):
        data = full_match(enemy_deaths=10)
        del data["enemyList"][0]["death"]
        result = score(data)
        self.assertFalse(result["death_context"]["enemy_deaths_clearly_higher"])
        self.assertFalse(result["mvp_comparison_complete"])

    def test_stomp_does_not_use_single_death_advantage_exemption(self):
        data = full_match(enemy_deaths=10)
        for row in data["enemyList"]:
            row.update(kill=0, assist=0, finalHit=0)
        result = score(data)
        self.assertTrue(result["is_stomp"])
        self.assertFalse(result["death_context"]["enemy_deaths_clearly_higher"])

    def test_very_short_game_does_not_treat_four_deaths_as_low(self):
        data = full_match(deaths=(4, 2, 3, 2, 3))
        data["gameTimeSec"] = 120
        self.assertFalse(score(data)["death_context"]["low_team_deaths"])

    def test_long_game_with_high_counts_is_not_low_death_either(self):
        data = full_match(deaths=(10, 10, 10, 10, 10))
        data["gameTimeSec"] = 1800
        result = score(data)
        self.assertFalse(result["death_context"]["low_team_deaths"])
        self.assertGreaterEqual(death_penalty(result), 5)


class AwardsAndLossTests(unittest.TestCase):
    def test_mvp_compares_all_ten_with_each_players_own_role_and_team(self):
        data = full_match("d")
        data["matchRet"] = 1
        data["teammateList"][0].update(heroDamage=18000, finalHit=25, kill=30, death=3)
        data["enemyList"][1].update(heroDamage=25000, finalHit=40, kill=45, death=2)
        results = [score_match(data, {}, customer_token="", bnet_id=row["bnetId"], battletag=row["name"], heroes=HEROES)
                   for team in (data["teammateList"], data["enemyList"]) for row in team]
        highest = max(r["score"] for r in results)
        for result in results:
            self.assertTrue(result["mvp_comparison_complete"])
            self.assertEqual(result["mvp_compared_players"], 10)
            self.assertEqual(result["is_mvp"], result["score"] == highest)
        self.assertEqual(results[0]["outcome_label"], "胜")
        self.assertEqual(results[5]["outcome_label"], "负")

    def test_enemy_can_be_mvp_instead_of_the_best_requested_teammate(self):
        data = full_match("d")
        data["teammateList"][0].update(heroDamage=8000, finalHit=12, kill=24, death=3)
        data["enemyList"][1].update(heroDamage=50000, finalHit=80, kill=90, death=0)
        result = score(data)
        self.assertTrue(result["mvp_comparison_complete"])
        self.assertFalse(result["is_mvp"])

    def test_incomplete_ten_players_cannot_assert_mvp(self):
        data = full_match("d")
        data["teammateList"][0].update(heroDamage=50000, finalHit=80, kill=90, death=0)
        del data["enemyList"][4]["cure"]
        result = score(data)
        self.assertFalse(result["mvp_comparison_complete"])
        self.assertFalse(result["is_mvp"])
        self.assertLess(result["mvp_compared_players"], 10)

    def test_mvp_and_carry_can_coexist_and_tied_highest_is_not_arbitrary(self):
        data = full_match("d", deaths=(0, 5, 5, 5, 5))
        data["teammateList"][0].update(heroDamage=50000, finalHit=80, kill=90)
        data["enemyList"][1].update(heroDamage=50000, finalHit=80, kill=90, death=0)
        result = score(data)
        self.assertTrue(result["is_mvp"])
        self.assertTrue(result["mvp_tied"])
        self.assertTrue(result["is_carry"])

    def test_missing_optional_metric_also_prevents_unfair_mvp_comparison(self):
        data = full_match("d")
        del data["enemyList"][0]["resistDamage"]
        result = score(data)
        self.assertFalse(result["mvp_comparison_complete"])
        self.assertFalse(result["is_mvp"])

    def test_carry_at_eighty_svp_strictly_above_eighty_and_only_on_loss(self):
        data = full_match("d")
        for row in data["teammateList"]:
            row["finalHit"] = 0
        data["teammateList"][0]["finalHit"] = 20
        data["teammateList"][1]["finalHit"] = 30
        data["matchRet"] = -1
        exact = score(data)
        self.assertEqual(exact["fleta"], .8)
        self.assertTrue(exact["is_carry"])
        self.assertFalse(exact["is_svp"])
        data["teammateList"][0]["finalHit"] = 21
        self.assertTrue(score(data)["is_svp"])
        data["matchRet"] = 1
        self.assertFalse(score(data)["is_svp"])

    def test_solo_positive_loser_gets_more_relief_but_not_unlimited(self):
        data = full_match(deaths=(7, 8, 8, 8, 8))
        data["matchRet"] = -1
        data["teammateList"][0].update(kill=16, finalHit=12, heroDamage=500, cure=3000)
        for row in data["teammateList"][1:]:
            row.update(kill=4, finalHit=3)
        solo = score(data)
        self.assertTrue(solo["solo_positive"])
        self.assertTrue(solo["loss_protected"])
        solo_relief = sum(a["points"] for a in solo["adjustments"] if "败方" in a["reason"])
        for row in data["teammateList"][1:]:
            row["kill"] = 20
        usual = score(data)
        usual_relief = sum(a["points"] for a in usual["adjustments"] if "败方" in a["reason"])
        self.assertGreater(solo_relief, usual_relief)
        self.assertLessEqual(solo_relief, death_penalty(solo) + max(0, -solo["metrics"]["survival"]["contribution"]) + .001)

    def test_unknown_outcome_is_not_a_loss_or_svp(self):
        data = full_match("d")
        data["teammateList"][0]["finalHit"] = 80
        data["matchRet"] = "unknown"
        result = score(data)
        self.assertEqual(result["outcome_label"], "未知")
        self.assertFalse(result["is_svp"])
        self.assertFalse(result["loss_protected"])


class AggregateReferenceTests(unittest.TestCase):
    def test_s_starts_at_eighty_two(self):
        self.assertEqual(grade(82), "S")
        self.assertEqual(grade(81.99), "A")

    def test_smurf_is_the_largest_aggregate_bonus(self):
        item = score(match())
        item.update(score=55, fleta=.8, final_blow_share=.4, outcome_label="胜")
        item["stats"].update(kill=50, death=5)
        result = aggregate_player("me#1234", [deepcopy(item) for _ in range(12)], fetched=12, errors=[])
        self.assertTrue(result["is_smurf"])
        self.assertEqual(result["smurf_threshold"], 7)
        bonuses = result["summary_adjustments"]
        major = next(a["points"] for a in bonuses if "SMURF" in a["reason"])
        self.assertGreater(major, sum(a["points"] for a in bonuses if "SMURF" not in a["reason"]))
        self.assertGreater(result["score"], result["match_average"] + 15)

    def test_below_eighty_never_counts_and_only_latest_twelve_count(self):
        items = []
        for i in range(13):
            item = score(match())
            item.update(begin_ts=i, fleta=.8 if i < 7 else .799999)
            items.append(item)
        result = aggregate_player("me#1234", items, fetched=13, errors=[])
        self.assertEqual(result["rated_matches"], 12)
        self.assertEqual(result["smurf_matches"], 6)
        self.assertFalse(result["is_smurf"])

    def test_seven_observed_qualifying_games_can_confirm_smurf_without_inventing_five(self):
        item = score(match())
        item.update(fleta=.8, score=60)
        result = aggregate_player("me#1234", [item] * 7, fetched=7, errors=[])
        self.assertEqual(result["rated_matches"], 7)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["is_smurf"])

    def test_kd_ten_bonus_is_small_and_uses_total_kills_over_total_deaths(self):
        item = score(match())
        item["stats"].update(kill=50, death=5)
        result = aggregate_player("me#1234", [item] * 12, fetched=12, errors=[])
        self.assertEqual(result["kd"], 10)
        self.assertGreater(result["score"], result["match_average"])
        self.assertLessEqual(result["score"] - result["match_average"], 1.5)
        item["stats"].update(kill=49)
        below = aggregate_player("me#1234", [item] * 12, fetched=12, errors=[])
        self.assertEqual(below["score"], below["match_average"])

    def test_zero_deaths_display_perfect_without_json_infinity_and_zero_zero_is_unknown(self):
        item = score(match())
        item["stats"].update(kill=12, death=0)
        result = aggregate_player("me#1234", [item], fetched=1, errors=[])
        self.assertTrue(result["kd_perfect"])
        self.assertIsNone(result["kd"])
        item["stats"]["kill"] = 0
        result = aggregate_player("me#1234", [item], fetched=1, errors=[])
        self.assertFalse(result["kd_perfect"])
        self.assertIsNone(result["kd"])
        self.assertEqual(result["summary_adjustments"], [])

    def test_high_fleta_losses_reduce_the_small_win_rate_penalty(self):
        item = score(match())
        item.update(score=60, outcome_label="负", loss_protected=False)
        ordinary = aggregate_player("me#1234", [item]*12, fetched=12, errors=[])
        item["loss_protected"] = True
        protected = aggregate_player("me#1234", [item]*12, fetched=12, errors=[])
        self.assertGreater(protected["score"], ordinary["score"])
        self.assertLessEqual(60 - ordinary["score"], 1.5)

    def test_rules_are_text_reference_without_numeric_weights(self):
        rules = scoring_rules()
        self.assertNotIn("weights", rules)
        self.assertNotIn("final_blow_share_thresholds", rules)
        self.assertTrue(all(isinstance(line, str) for line in rules["reference"]))
        self.assertIn("段位差距过大", rules["relative_notice"])


class PartyTests(unittest.TestCase):
    def test_doubles_triples_and_five_stacks_from_recorded_links(self):
        for size, label in ((2, "双排"), (3, "三排"), (5, "五排")):
            with self.subTest(size=size):
                team = [player(str(i), "d") for i in range(1, 6)]
                for i, row in enumerate(team):
                    row["friendBnetIds"] = [str(j) for j in range(1, size+1) if j != i+1] if i < size else []
                self.assertEqual(party_info(team[0], team), {"known": True, "size": size, "label": label})

    def test_inbound_and_transitive_links_are_combined_without_duplicate_members(self):
        team = [player(str(i), "d") for i in range(1, 6)]
        team[1]["friendBnetIds"] = ["1", "1", "2", "3"]
        self.assertEqual(party_info(team[0], team)["size"], 3)

    def test_missing_metadata_is_unknown_explicit_empty_is_solo(self):
        team = [player(str(i), "d") for i in range(1, 6)]
        self.assertFalse(party_info(team[0], team)["known"])
        team[0]["friendBnetIds"] = []
        self.assertEqual(party_info(team[0], team)["label"], "单排")

    def test_unrelated_or_invalid_ids_do_not_fabricate_party_sizes(self):
        team = [player(str(i), "d") for i in range(1, 6)]
        team[0]["friendBnetIds"] = ["999999"]
        self.assertFalse(party_info(team[0], team)["known"])
        team[0]["friendBnetIds"] = [True]
        self.assertFalse(party_info(team[0], team)["known"])


if __name__ == "__main__":
    unittest.main()
