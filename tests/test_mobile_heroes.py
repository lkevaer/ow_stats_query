from copy import deepcopy
from pathlib import Path
import unittest

from api_server.batch_rating.hero_profiles import mobile_dps_profile
from api_server.batch_rating.scoring import VERSION, WEIGHTS, score_match
from test_batch_rating import HEROES, match


MOBILE = {
    "207165582859042819": "猎空",
    "207165582859042857": "源氏",
    "207165582859043334": "回声",
}


def rated(hero_guid="d", *, damage=1500, deaths=14, finals=8, hero_name=None, role="dps", mutate=None):
    data = match()
    data["teammateList"][0].update(heroGuid=hero_guid, heroDamage=damage, death=deaths, finalHit=finals)
    if mutate:
        mutate(data)
    heroes = {**HEROES, **{guid: {"name": name, "roleType": "dps"} for guid, name in MOBILE.items()}}
    if hero_name is not None:
        heroes[hero_guid] = {"name": hero_name, "roleType": role}
    return score_match(data, {"matchId": "mobile-test", "gameMode": "sport"}, customer_token="",
                       bnet_id="me#1234", battletag="me#1234", heroes=heroes)


class MobileHeroTests(unittest.TestCase):
    def test_three_mobile_dps_receive_bounded_damage_and_death_relief(self):
        baseline = rated()
        for guid in MOBILE:
            with self.subTest(hero=MOBILE[guid]):
                result = rated(guid)
                for metric in ["damage", "survival"]:
                    original = baseline["metrics"][metric]["points"]
                    actual = result["metrics"][metric]["points"]
                    self.assertLess(original, actual)
                    self.assertLess(actual, 60)
                    self.assertAlmostEqual(actual, 60 + (original - 60) * .75, delta=.001)

    def test_only_tracer_gets_relative_ten_percent_final_blow_weight(self):
        for guid, name in MOBILE.items():
            result = rated(guid)
            expected = WEIGHTS["dps"]["final_blows"] * (1.10 if name == "猎空" else 1)
            self.assertAlmostEqual(result["metrics"]["final_blows"]["weight"], expected)
            self.assertEqual(result["metrics"]["final_blow_share"]["weight"], WEIGHTS["dps"]["final_blow_share"])

    def test_strong_damage_and_survival_do_not_get_a_free_bonus(self):
        baseline = rated(damage=18000, deaths=1)
        for guid in MOBILE:
            result = rated(guid, damage=18000, deaths=1)
            for metric in ["damage", "survival"]:
                self.assertEqual(result["metrics"][metric]["points"], baseline["metrics"][metric]["points"])
            if MOBILE[guid] != "猎空":
                self.assertEqual(result["score"], baseline["score"])

    def test_higher_deaths_and_lower_damage_still_reduce_score(self):
        for guid in MOBILE:
            self.assertGreater(rated(guid, deaths=6)["score"], rated(guid, deaths=18)["score"])
            self.assertGreater(rated(guid, damage=8000)["score"], rated(guid, damage=1000)["score"])

    def test_unknown_heroes_and_other_roles_do_not_get_the_profile(self):
        for role in ["tank", "support", None]:
            self.assertIsNone(mobile_dps_profile("207165582859042819", {"name": "猎空"}, role))
        self.assertIsNone(mobile_dps_profile("other", {"name": "黑影"}, "dps"))
        self.assertEqual(rated()["score"], rated("other", hero_name="其他输出")["score"])

    def test_guid_and_localized_name_resolve_to_the_same_profile(self):
        self.assertEqual(rated("207165582859042819")["score"], rated("new-id", hero_name="猎空")["score"])
        self.assertEqual(rated("207165582859042857")["score"], rated("new-id", hero_name="GENJI")["score"])

    def test_raw_stats_fleta_and_badges_are_not_rewritten(self):
        baseline, tracer = rated(), rated("207165582859042819")
        for key in ["stats", "fleta", "final_blow_share", "team_final_blows", "is_carry", "is_svp", "kd", "sample_weight"]:
            self.assertEqual(tracer[key], baseline[key])

    def test_tracer_weight_change_does_not_mutate_global_or_other_hero_weights(self):
        original = deepcopy(WEIGHTS)
        for _ in range(3):
            rated("207165582859042819")
        self.assertEqual(WEIGHTS, original)
        self.assertEqual(rated()["metrics"]["final_blows"]["weight"], .40)

    def test_complete_data_coverage_stays_one_with_larger_weight_sum(self):
        self.assertEqual(rated("207165582859042819")["data_coverage"], 1)
        partial = rated("207165582859042819", mutate=lambda d: d["teammateList"][0].pop("kill"))
        self.assertAlmostEqual(partial["data_coverage"], .94 / 1.04, places=3)

    def test_missing_required_metrics_are_not_fabricated(self):
        for key in ["heroDamage", "death", "finalHit"]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                rated("207165582859042819", mutate=lambda d: d["teammateList"][0].pop(key))

    def test_extreme_inputs_remain_finite_and_within_score_bounds(self):
        for guid in MOBILE:
            for damage, death, finals in [(0, 1000, 0), (1000000, 0, 1000)]:
                result = rated(guid, damage=damage, deaths=death, finals=finals)
                self.assertGreaterEqual(result["score"], 0)
                self.assertLessEqual(result["score"], 100)

    def test_new_rules_are_versioned_and_not_announced_in_frontend(self):
        self.assertEqual(VERSION, "team-relative-2026s5-v9")
        static = Path(__file__).resolve().parents[1] / "api_server/static"
        for name in ["batch_rating.html", "batch_rating.js", "batch_rating.css"]:
            text = (static / name).read_text(encoding="utf-8")
            self.assertNotIn("最后一击权重提高10%", text)
            self.assertNotIn("高机动英雄专项修正", text)
