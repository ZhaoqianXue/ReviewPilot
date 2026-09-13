"""Session management preserves scientific artifacts and protects fixed examples."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch
from starlette.testclient import TestClient
import web_app
from reviewpilot_core.state_projection import _history, list_projects, build_rp_data
from reviewpilot_core.task_runner import TaskRunner


class SessionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runner = TaskRunner()
        self.addCleanup(self.runner.shutdown)
        for name in ('alpha', 'beta', 'quick-start-biomedical-showcase', 'quick-start-hci-showcase', 'quick-start-urban-showcase'):
            path = self.root / name
            path.mkdir()
            (path / 'search_conditions.json').write_text(json.dumps({'project_name': name, 'description': 'test scope', 'platforms': ['pubmed']}))
            (path / 'chat').mkdir()
            (path / 'chat/messages.jsonl').write_text(json.dumps({'role': 'u', 'text': name, 'step': 1}) + '\n')
        self.enterContext(patch.object(web_app, 'OUTPUT_ROOT', self.root))
        self.enterContext(patch.object(web_app, 'task_runner', self.runner))
        self.client = TestClient(web_app.create_app())

    def test_rename_changes_only_display_metadata_and_survives_reload(self):
        config = (self.root / 'alpha/search_conditions.json').read_bytes()
        chat = (self.root / 'alpha/chat/messages.jsonl').read_bytes()
        response = self.client.patch('/projects/alpha', json={'title': '新综述 <A> & B'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((self.root / 'alpha/search_conditions.json').read_bytes(), config)
        self.assertEqual((self.root / 'alpha/chat/messages.jsonl').read_bytes(), chat)
        state = self.client.get('/projects/alpha/state').json()
        self.assertEqual(state['project']['title'], '新综述 <A> & B')
        self.assertEqual(next(p for p in list_projects(self.root) if p['id'] == 'alpha')['title'], '新综述 <A> & B')

    def test_delete_removes_history_and_state_but_keeps_recovery_copy(self):
        self.assertEqual(self.client.delete('/projects/alpha').status_code, 200)
        self.assertEqual(self.client.get('/projects/alpha/state').status_code, 404)
        self.assertNotIn('alpha', [p['id'] for p in list_projects(self.root)])
        self.assertEqual(len(list((self.root / '.trash').glob('alpha-*/chat/messages.jsonl'))), 1)
        self.assertEqual(self.client.get('/projects/beta/state').status_code, 200)

    def test_three_examples_are_protected_at_api_even_if_renamed_in_config(self):
        for item in _history(self.root, '')[0]['items']:
            self.assertTrue(item['protected'])
            self.assertEqual(self.client.delete('/projects/' + item['id']).status_code, 403)
            self.assertEqual(self.client.patch('/projects/' + item['id'], json={'title': 'changed'}).status_code, 403)

    def test_ordinary_biomedical_topic_is_not_protected(self):
        self.client.patch('/projects/alpha', json={'title': 'LLM for Biomedical'})
        self.assertEqual(self.client.delete('/projects/alpha').status_code, 200)

    def test_invalid_names_missing_ids_and_symlinks_are_rejected(self):
        for title in ('', '   ', 'x' * 121, None, 23):
            self.assertEqual(self.client.patch('/projects/alpha', json={'title': title}).status_code, 400)
        self.assertEqual(self.client.delete('/projects/missing').status_code, 404)
        (self.root / 'linked').symlink_to(self.root / 'alpha', target_is_directory=True)
        self.assertEqual(self.client.delete('/projects/linked').status_code, 404)
        self.assertTrue((self.root / 'alpha').exists())

    def test_running_task_blocks_rename_and_delete(self):
        gate = Event()
        task = self.runner.submit('alpha', 'collect', lambda: gate.wait(5))
        try:
            self.assertEqual(self.client.delete('/projects/alpha').status_code, 409)
            self.assertEqual(self.client.patch('/projects/alpha', json={'title': 'changed'}).status_code, 409)
        finally:
            gate.set()
            self.runner.wait(task, 5)
        self.assertTrue((self.root / 'alpha').exists())

    def test_chat_activity_orders_history_and_examples_are_not_duplicated(self):
        os.utime(self.root / 'alpha/chat/messages.jsonl', (2000000000, 2000000000))
        history = self.client.get('/sessions').json()['history']
        self.assertEqual(history[1]['items'][0]['id'], 'alpha')
        self.assertEqual({item['id'] for item in history[1]['items']}, {'alpha', 'beta'})
        self.assertEqual(len(history[0]['items']), 3)

    def test_example_selection_stays_fixed_when_another_prefix_project_is_updated(self):
        extra = self.root / 'quick-start-hci-newer'
        extra.mkdir()
        (extra / 'search_conditions.json').write_text('{"project_name":"New HCI study"}')
        self.assertEqual(_history(self.root, '')[0]['items'][1]['id'], 'quick-start-hci-showcase')
        self.assertEqual(self.client.delete('/projects/quick-start-hci-newer').status_code, 200)
