import unittest
from contextlib import closing

from fastapi.testclient import TestClient

from api_server.batch_rating.ocr_candidates import normalize_tags
from api_server.batch_rating.scoring import grade, grouped_summary, rank_players
from api_server.main import app


class RatingHomeTests(unittest.TestCase):
    def test_home_is_rating_and_removed_frontend_is_unavailable(self):
        with closing(TestClient(app, follow_redirects=False)) as client:
            page = client.get('/')
            self.assertEqual(page.status_code, 200)
            self.assertIn('id="selfBinding"', page.text)
            self.assertNotIn('返回普通查询', page.text)
            self.assertNotIn('原70分水平', page.text)
            self.assertEqual(client.get('/static/index.html').status_code, 404)
            legacy = client.get('/batch-rating?task=example')
            self.assertEqual(legacy.status_code, 307)
            self.assertEqual(legacy.headers['location'], '/?task=example')

    def test_single_effective_player_is_not_labeled_as_a_middle_horse(self):
        ranked = rank_players([{'battletag': '本人#1234', 'score': 92, 'tier': '中等马'}])
        self.assertIsNone(ranked[0]['tier'])
        self.assertIsNone(ranked[0]['rank'])
        self.assertIn('请输入更多ID以比较', grouped_summary(ranked))
        self.assertNotIn('中等马', grouped_summary(ranked))

    def test_one_rated_player_among_unavailable_accounts_still_needs_a_comparison(self):
        ranked = rank_players([{'battletag': '本人#1234', 'score': 92}, {'battletag': '未公开#1234', 'score': None}])
        self.assertIsNone(ranked[0]['tier'])
        self.assertIn('请输入更多ID以比较', grouped_summary(ranked))

    def test_numeric_grade_boundaries_include_exactly_one_hundred(self):
        for value, expected in [(100,'SSS'),(99.99,'S'),(90,'S'),(89.99,'A'),(80,'A'),(70,'B'),(60,'C'),(59.99,'D')]:
            self.assertEqual(grade(value), expected)

    def test_normalization_deduplicates_before_ten_player_limit(self):
        ten = [f'队友{i}#1234' for i in range(9)] + ['Player#1234']
        self.assertEqual(len(normalize_tags(ten + ['player#1234'])), 10)
        with self.assertRaisesRegex(ValueError, '最多 10'):
            normalize_tags(ten + ['其他#1234'])
