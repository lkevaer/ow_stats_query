import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import create_autospec, patch

from fastapi.testclient import TestClient

from api_server.main import app
from api_server.app_context import AppState
from api_server.bindings import BindingRecord
from api_server.routers.utils import resolve_target
from overstats.src.modules.dashen_match.service import DashenMatchModule
from overstats.src.modules.ow_hero_pick_rate.service import OWHeroPickRateModule
from overstats.src.modules.ow_hero_perk.service import OWHeroPerkModule
from overstats.src.modules.ow_hero_wiki.service import OWHeroWikiModule
from overstats.src.modules.patch_notes.service import PatchNotesModule
from overstats.src.modules.player_identity_search.service import PlayerIdentitySearchModule


class WebQueryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        setting = patch.dict(os.environ, {'OW_QUERY_ID_LOG_FILE': os.path.join(folder.name, 'audit.txt')})
        setting.start()
        self.addCleanup(setting.stop)
        self.previous_state = getattr(app.state, 'ow_app', None)
        self.state = AppState(config={'output': {'prefer_image_for_match': False}}, initialized=True)
        app.state.ow_app = self.state
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.state.ow_app = self.previous_state

    def test_inline_target_does_not_use_another_bound_player(self):
        binding = BindingRecord(battletag='Original#1234', bnet_id='111', customer_token='original-token')
        self.state.bindings_manager = SimpleNamespace(get=lambda token: binding)
        target = resolve_target(token='usr_local', battletag='Other#5678', state=self.state)
        self.assertEqual(target.effective_bnet_id, 'Other#5678')
        self.assertEqual(target.customer_token, '')

    def test_matching_binding_is_reused(self):
        binding = BindingRecord(battletag='Original#1234', bnet_id='111', customer_token='original-token')
        self.state.bindings_manager = SimpleNamespace(get=lambda token: binding)
        target = resolve_target(token='usr_local', battletag='Original#1234', state=self.state)
        self.assertEqual(target.customer_token, 'original-token')

    def test_inline_target_without_browser_token_reaches_account_check(self):
        response = self.client.get('/api/profile', params={'battletag': 'Example#1234'})
        self.assertEqual(response.status_code, 503)
        self.assertIn('OW_DASHEN_TOKEN', response.json()['error'])

    def test_match_count_is_used_in_normal_and_stream_queries(self):
        self.state.api_client = object()
        self.state.match = create_autospec(DashenMatchModule, instance=True)
        self.state.match.query_match_list.return_value = SimpleNamespace(matches=[], image=None, resolved_bnet=None)
        response = self.client.get('/api/matches', params={'battletag': 'Example#1234', 'limit': 3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.state.match.query_match_list.call_args.args[0].target_count, 3)
        response = self.client.post('/api/stream', json={'endpoint': '/api/matches', 'params': {'battletag': 'Example#1234', 'limit': 7}})
        self.assertIn('event: done', response.text)
        self.assertNotIn('event: error', response.text)
        self.assertEqual(self.state.match.query_match_list.call_args.args[0].target_count, 7)

    def test_pick_rate_adapter_and_stream_completion(self):
        self.state.pick_rate = create_autospec(OWHeroPickRateModule, instance=True)
        self.state.pick_rate.query_pick_rate.return_value = SimpleNamespace(image=None)
        response = self.client.get('/api/hero-pick-rate', params={'mode': 'quick', 'rank': 'gold'})
        self.assertTrue(response.json()['success'])
        query = self.state.pick_rate.query_pick_rate.call_args.args[0]
        self.assertEqual((query.game_mode, query.mmr), ('quick', 'gold'))
        response = self.client.post('/api/stream', json={'endpoint': '/api/hero-pick-rate', 'params': {}})
        self.assertIn('event: done', response.text)
        self.assertNotIn('event: error', response.text)

    def test_other_public_module_signatures(self):
        self.state.hero_perk = create_autospec(OWHeroPerkModule, instance=True)
        self.state.hero_perk.query_perk.return_value = SimpleNamespace(image=None)
        self.state.hero_wiki = create_autospec(OWHeroWikiModule, instance=True)
        self.state.hero_wiki.query_hero.return_value = SimpleNamespace(image=None, answer='', overview='Hero overview')
        self.state.patch_notes = create_autospec(PatchNotesModule, instance=True)
        self.state.patch_notes.query_patch_notes.return_value = SimpleNamespace(image=None)
        self.state.identity_search = create_autospec(PlayerIdentitySearchModule, instance=True)
        self.state.identity_search.search.return_value = SimpleNamespace(matches=())
        for path in ['/api/hero-perk?hero=Ana', '/api/hero-wiki?hero=Ana', '/api/patch-notes', '/api/search-player?keyword=example']:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['success'])
        self.assertEqual(self.state.hero_perk.query_perk.call_args.args[0].hero, 'Ana')
        self.assertEqual(self.state.patch_notes.query_patch_notes.call_args.kwargs['patch_kind'], 'latest')
        self.assertEqual(self.state.identity_search.search.call_args.args[0].bnet_id, 'example')

    def test_missing_modules_use_error_http_status(self):
        response = self.client.get('/api/hero-pick-rate')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()['success'])
        self.assertEqual(response.json()['status_code'], 503)

    def test_task_rejects_player_queries_before_credentials_are_configured(self):
        response = self.client.post('/api/task/create', json={'endpoint': '/api/matches', 'params': {'battletag': 'Example#1234'}})
        self.assertEqual(response.status_code, 503)
        self.assertIn('OW_DASHEN_TOKEN', response.json()['error'])


if __name__ == '__main__':
    unittest.main()
