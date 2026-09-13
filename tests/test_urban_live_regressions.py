"""Regressions reproduced in an ordinary, real-literature review session."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import web_app
from agents.search_condition_agent import SearchConditionAgent
from reviewpilot_core.screening_evidence import parse_screening_response
from reviewpilot_core.state_projection import build_rp_data
from reviewpilot_core.extraction_schema import save_schema_draft, finalize_schema
from reviewpilot_core.workflow_state import initialize_workflow_state, mark_stages_stale


class OrdinaryChatPersistenceTests(unittest.TestCase):
    def test_rerun_keeps_confirmed_schema_but_hides_outdated_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'review'
            project.mkdir()
            config = {'project_name': 'Review', 'description': 'Urban applications', 'search_terms': 'LLM AND urban', 'platforms': ['arxiv']}
            (project / 'search_conditions.json').write_text(json.dumps(config))
            initialize_workflow_state(project)
            save_schema_draft(project, {'fields': [{'name': 'purpose', 'type': 'Text', 'description': 'Reported purpose', 'required': True}]})
            finalize_schema(project)
            mark_stages_stale(project, ['extraction'])
            state = build_rp_data(root, project.name)
            self.assertEqual(state['schemaWorkbench']['status'], 'finalized')
            self.assertEqual(len(state['fields']), 1)
            (project / 'search_conditions.json').write_text(json.dumps({**config, 'search_terms': 'LLM AND medicine'}))
            self.assertEqual(build_rp_data(root, project.name)['schemaWorkbench']['status'], 'missing')

    def test_wrapped_verbatim_quote_is_located_but_rewritten_quote_is_not(self):
        paper = {'title': 'A survey', 'abstract': 'This paper surveys the landscape of urban models.'}
        prompt = {'eligibility': {'exclusion': ['No original evaluation.']}}
        base = {'include': False, 'reason': 'A survey only.', 'criterion': 'No original evaluation.', 'uncertain': False}
        included, evidence = parse_screening_response(json.dumps({**base, 'quote': '"This paper surveys the landscape"'}), paper, prompt)
        self.assertFalse(included)
        self.assertEqual(evidence['quote'], 'This paper surveys the landscape')
        self.assertTrue(evidence['quote_verified'])
        included, evidence = parse_screening_response(json.dumps({**base, 'quote': '"The paper reviews urban planning"'}), paper, prompt)
        self.assertTrue(included)
        self.assertFalse(evidence['quote_verified'])

    def test_chat_settings_override_defaults_only_on_initial_chat_path(self):
        defaults = {'project_name': 'Default', 'platforms': ['pubmed', 'arxiv', 'openalex'], 'max_results': 10, 'source_limits': {'pubmed': 10, 'arxiv': 10, 'openalex': 10}, 'date_range': {'start': '', 'end': '2026-09-12'}}
        settings = {'project_name': 'Urban live review', 'platforms': ['arxiv'], 'max_results': 5, 'date_start': '2023-01-01'}
        result = SearchConditionAgent._apply_chat_settings(defaults, settings)
        self.assertEqual(result['platforms'], ['arxiv'])
        self.assertEqual(result['source_limits'], {'arxiv': 5})
        self.assertEqual(result['date_range'], {'start': '2023-01-01', 'end': '2026-09-12'})
        self.assertEqual(defaults['source_limits']['arxiv'], 10)
        for invalid in [{'max_results': True}, {'max_results': 0}, {'platforms': ['invented']}, {'date_start': '2026-02-30'}, {'extra': 1}]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                SearchConditionAgent._apply_chat_settings(defaults, invalid)

    def test_initial_exchange_survives_setup_change_and_new_state_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def save(output_root, project_id, config):
                return {**config, "project_path": str(root / project_id), "lead_agent_reply": "Review these search settings."}
            payload = {"project_name": "Urban planning test", "description": "My original urban governance question", "search_terms": "LLM AND urban", "platforms": ["arxiv"]}
            with patch.object(web_app, "_run_lead_agent_search_setup", side_effect=save):
                project = web_app.create_project(root, payload)
                before = (root / project['id'] / 'chat/messages.jsonl').read_bytes()
                web_app.update_project_setup(root, project['id'], {**payload, "description": "Revised scope", "project_name": "Renamed urban review"})
            state = build_rp_data(root, project['id'])
            text = [row['text'] for row in state['messages']]
            self.assertEqual((root / project['id'] / 'chat/messages.jsonl').read_bytes(), before)
            self.assertEqual(text.count(payload['description']), 1)
            self.assertEqual(text.count('Review these search settings.'), 1)
            self.assertNotIn('I want to review how LLMs support urban planning and smart cities.', text)

    def test_legacy_ordinary_urban_project_uses_its_own_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'urban-review'
            project.mkdir()
            (project / 'search_conditions.json').write_text(json.dumps({'project_name': 'Urban review', 'description': 'Public participation using LLMs', 'platforms': ['arxiv']}))
            text = [row['text'] for row in build_rp_data(root, project.name)['messages']]
            self.assertIn('Public participation using LLMs', text)
            self.assertNotIn('I want to review how LLMs support urban planning and smart cities.', text)
