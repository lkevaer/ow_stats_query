"""The web-only child table reuses each participant's actual single-match score."""

from copy import deepcopy
import json
import unittest

from api_server.batch_rating.scoring import score_match
from test_batch_rating import HEROES, player, score
from test_rating_context import full_match


class MatchDetailsTests(unittest.TestCase):
    def test_nine_other_players_match_individual_scores_on_both_teams(self):
        data = full_match("d")
        data["matchRet"] = 1
        result = score(data)
        details = result["match_details"]
        self.assertEqual((details["total_players"], details["rated_players"]), (10, 10))
        self.assertEqual(len(details["other_players"]), 9)
        self.assertEqual(
            sum(row["team"] == "ally" for row in details["other_players"]), 4
        )
        for row in details["other_players"]:
            actual = score_match(
                data,
                {"matchId": "m1", "gameMode": "LeisurePreset"},
                customer_token="",
                bnet_id=row["battletag"],
                battletag=row["battletag"],
                heroes=HEROES,
            )
            for key in (
                "score",
                "fleta",
                "metrics",
                "adjustments",
                "outcome",
                "is_mvp",
            ):
                self.assertEqual(row[key], actual[key], (row["battletag"], key))
            self.assertEqual(row["outcome"], 1 if row["team"] == "ally" else -1)

    def test_internal_identifiers_never_enter_public_child_rows(self):
        data = full_match("d")
        for team in (data["teammateList"], data["enemyList"]):
            for index, row in enumerate(team):
                row["customerToken"] = f"PRIVATE_TOKEN_{index}"
                row["bnetId"] = f"PRIVATE_ID_{index}_{id(team)}"
        data["enemyList"][0]["name"] = "隐藏昵称"
        result = score(data)
        encoded = json.dumps(result["match_details"], ensure_ascii=False)
        self.assertNotIn("PRIVATE_", encoded)
        self.assertNotIn("customerToken", encoded)
        self.assertNotIn("bnetId", encoded)
        self.assertIn("隐藏昵称", encoded)

    def test_missing_participant_data_is_unrated_without_changing_own_score(self):
        data = full_match("d")
        before = score(data)
        damaged = deepcopy(data)
        damaged["enemyList"][0].pop("death")
        result = score(damaged)
        row = next(
            row
            for row in result["match_details"]["other_players"]
            if row["battletag"] == "enemy0#1234"
        )
        self.assertIsNone(row["score"])
        self.assertTrue(row["warnings"])
        self.assertEqual(result["match_details"]["rated_players"], 9)
        self.assertFalse(result["is_mvp"])
        self.assertEqual(result["score"], before["score"])

    def test_six_player_teams_keep_all_eleven_other_players(self):
        data = full_match("d")
        for index, key in enumerate(("teammateList", "enemyList")):
            data[key].append(player(f"extra{index}#1234", "d"))
        details = score(data)["match_details"]
        self.assertEqual(len(details["other_players"]), 11)
        self.assertEqual(details["total_players"], 12)
