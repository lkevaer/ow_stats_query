from copy import deepcopy
import unittest

from api_server.batch_rating.rank import match_rank
from api_server.batch_rating.scoring import aggregate_player,score_match
from test_batch_rating import HEROES,match

RANKS=[{"key":"Gold","name":"黄金","level":[1500,1999],"icon":"https://example.com/gold.png"},
       {"key":"Emerald","name":"翡翠","level":[2500,2999],"icon":"https://example.com/emerald.png"}]


class RankDisplayTests(unittest.TestCase):
    def test_rank_uses_official_config_including_new_tiers(self):
        result=match_rank({}, {"gameMode":"SportPreset","rankInfo":{"rankScore":2600,"rankSubTier":4}},RANKS)
        self.assertEqual(result["label"],"翡翠4")
        self.assertEqual(result["icon"],"https://example.com/emerald.png")

    def test_explicit_rank_name_and_division_are_preserved(self):
        result=match_rank({"rankInfo":{"rankName":"Gold3"}},{"gameMode":"SportPreset"},RANKS)
        self.assertEqual(result["label"],"黄金3")

    def test_unknown_rank_is_not_guessed_as_bronze(self):
        result=match_rank({}, {"gameMode":"SportPreset","rankInfo":{"rankScore":0,"rankName":"None","rankSubTier":0}},RANKS)
        self.assertFalse(result["known"])
        self.assertIsNone(result["icon"])

    def test_player_snapshot_takes_priority_over_match_list(self):
        result=match_rank({"rankInfo":{"rankName":"Gold","rankSubTier":2}},{"gameMode":"SportPreset","rankInfo":{"rankName":"Emerald","rankSubTier":1}},RANKS)
        self.assertEqual(result["label"],"黄金2")

    def test_unknown_player_rank_falls_back_to_own_match_list_snapshot(self):
        result=match_rank({"rankInfo":{"rankName":"None","rankScore":0}},{"gameMode":"SportPreset","rankInfo":{"rankName":"Gold","rankSubTier":2}},RANKS)
        self.assertEqual(result["label"],"黄金2")

    def test_leisure_and_stadium_have_no_competitive_rank_badge(self):
        for mode in ["LeisurePreset","SportFight","Stadium"]:
            self.assertIsNone(match_rank({}, {"gameMode":mode,"rankInfo":{"rankName":"Gold"}},RANKS))

    def test_malformed_rank_metadata_does_not_break_ratings(self):
        for value in [None,"bad",{"rankScore":"NaN"},{"rankScore":True}]:
            self.assertFalse(match_rank({}, {"gameMode":"SportPreset","rankInfo":value},RANKS)["known"])

    def test_rank_does_not_change_score_or_absolute_grade(self):
        data=match()
        source={"gameMode":"SportPreset","matchId":"one"}
        kwargs={"customer_token":"","bnet_id":"me#1234","battletag":"me#1234","heroes":HEROES,"ranks":RANKS}
        one=score_match(data,source,**kwargs)
        source["rankInfo"]={"rankName":"Emerald","rankSubTier":1}
        two=score_match(data,source,**kwargs)
        self.assertEqual(one["score"],two["score"])
        self.assertEqual(one["grade"],two["grade"])
        self.assertEqual(two["rank"]["label"],"翡翠1")

    def test_aggregate_uses_latest_visible_record_not_highest_rank(self):
        source={"gameMode":"SportPreset","beginTs":100,"rankInfo":{"rankName":"Emerald","rankSubTier":1}}
        one=score_match(match(),source,customer_token="",bnet_id="me#1234",battletag="me#1234",heroes=HEROES,ranks=RANKS)
        two=deepcopy(one);two.update(begin_ts=200,rank={"known":True,"label":"黄金3","icon":"https://example.com/gold.png"})
        result=aggregate_player("me#1234",[one,two],fetched=2,errors=[])
        self.assertEqual(result["latest_rank"]["label"],"黄金3")
