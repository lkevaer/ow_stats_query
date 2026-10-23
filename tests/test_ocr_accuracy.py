import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from api_server.batch_rating.ocr_candidates import candidates_from_spans, extract_tags, parse_line, vote_candidates
from api_server.batch_rating.ocr_engine import image_tiles, recognize_neural


def span(text,box,pass_id="one",confidence=.99,**extra):
    return {"text":text,"box":box,"pass":pass_id,"confidence":confidence,**extra}


class ExtractionTests(unittest.TestCase):
    def test_spaces_fullwidth_digits_and_hash_are_normalized(self):
        tags,changes=extract_tags("队 友 甲 ＃ １２３４\nPlayer ＃ SlO8")
        self.assertEqual(tags,["队友甲#1234","Player#5108"])
        self.assertEqual(len(changes),1)

    def test_multiple_tags_on_a_row_and_status_are_not_glued(self):
        tags,_=extract_tags("Alice#1234 Bob#5678\n队友甲#2468 Online")
        self.assertEqual(tags,["Alice#1234","Bob#5678","队友甲#2468"])

    def test_split_hash_line_is_joined_to_name_not_header(self):
        tags,_=extract_tags("屏蔽列表\n#9999\n队 友 甲\n#1234")
        self.assertEqual(tags,["队友甲#1234"])

    def test_nickname_brackets_and_digits_are_preserved(self):
        tags,_=extract_tags("【玩家7】#1234")
        self.assertEqual(tags,["【玩家7】#1234"])

    def test_invalid_or_too_long_suffix_is_not_silently_truncated(self):
        for text in ["Name#1234567890123","Name#1234xyz","完成 1234"]:
            with self.subTest(text=text):
                self.assertEqual(parse_line(text),[])

    def test_spatial_fragments_join_without_absorbing_adjacent_ui(self):
        records=candidates_from_spans([
            span("屏蔽列表",[0,10,80,30]),span("队友甲",[200,10,260,30]),span("#",[263,10,271,30]),span("1234",[274,10,320,30]),
            span("在线",[500,10,550,30])])
        self.assertEqual([r["battletag"] for r in records],["队友甲#1234"])

    def test_columns_and_rows_do_not_merge(self):
        records=candidates_from_spans([
            span("甲#1234",[0,0,100,20]),span("乙#1234",[300,0,400,20]),span("丙#5678",[0,35,100,55])])
        chosen,_=vote_candidates(records)
        self.assertEqual([r["battletag"] for r in chosen],["甲#1234","乙#1234","丙#5678"])

    def test_missing_hash_needs_an_actual_tag_column_and_review(self):
        self.assertEqual(candidates_from_spans([span("队友甲 1234",[0,0,100,20])]),[])
        records=candidates_from_spans([span("队友甲 1234",[0,0,100,20]),span("乙#5678",[0,35,100,55])])
        chosen,changes=vote_candidates(records)
        self.assertEqual(chosen[0]["battletag"],"队友甲#1234")
        self.assertTrue(chosen[0]["needs_review"])
        self.assertTrue(changes)

    def test_consensus_beats_one_confident_typo_and_exposes_alternative(self):
        spans=[span("队友甲#1234",[0,0,100,20],"one",.96),span("队友甲#1234",[0,0,100,20],"two",.96),span("队友申#1234",[0,0,100,20],"three",1)]
        chosen,_=vote_candidates(candidates_from_spans(spans))
        self.assertEqual(chosen[0]["battletag"],"队友甲#1234")
        self.assertEqual(chosen[0]["alternatives"],["队友申#1234"])
        self.assertTrue(chosen[0]["needs_review"])

    def test_two_agreeing_clear_passes_are_not_flagged(self):
        chosen,_=vote_candidates(candidates_from_spans([span("甲#1234",[0,0,100,20],"one"),span("甲#1234",[0,0,100,20],"two")]))
        self.assertFalse(chosen[0]["needs_review"])

    def test_cut_off_nickname_cannot_outvote_a_complete_reading(self):
        spans=[span("完整名字#1234",[0,0,150,20],"full",.97)]
        spans += [span("名字#1234",[40,0,150,20],str(i),1,clipped=True) for i in range(3)]
        chosen,_=vote_candidates(candidates_from_spans(spans))
        self.assertEqual(chosen[0]["battletag"],"完整名字#1234")

    def test_overlapping_tiles_do_not_duplicate_players(self):
        spans=[span("甲#1234",[0,0,100,20],str(i)) for i in range(4)]
        chosen,_=vote_candidates(candidates_from_spans(spans))
        self.assertEqual(len(chosen),1)


class NeuralRuntimeTests(unittest.TestCase):
    def test_large_screenshots_are_tiled_without_downscaling_source(self):
        image=Image.new("RGB",(3840,2160))
        tiles=image_tiles(image)
        self.assertGreater(len(tiles),1)
        self.assertTrue(any(x+tile.width==3840 and y+tile.height==2160 for tile,(x,y) in tiles))
        self.assertTrue(all(tile.width<=2000 and tile.height<=2000 for tile,_ in tiles))

    def test_detection_is_reenabled_after_row_retry_and_between_requests(self):
        class Box:
            def __getitem__(self,key):
                return [10,170,170,10] if key[1]==0 else [10,10,40,40]
        class StatefulEngine:
            def __init__(self): self.flags=[]
            def __call__(self,image,**kwargs):
                self.flags.append(kwargs.get("use_det"))
                if kwargs.get("use_det"):
                    return SimpleNamespace(boxes=[Box()],txts=("甲#1234",),scores=(.8,))
                return SimpleNamespace(txts=("甲#1234",),scores=(.99,))
        engine=StatefulEngine()
        with patch("api_server.batch_rating.ocr_engine._get_engine",return_value=engine):
            for _ in range(2):
                image=Image.new("RGB",(200,80),"white")
                image.putpixel((20,20),(0,0,0))
                result=recognize_neural(image)
                self.assertEqual(result["battletags"],["甲#1234"])
        self.assertEqual(engine.flags,[True,True,False,True,True,False])
