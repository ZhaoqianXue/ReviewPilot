import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient
import web_app
from agents.lead_agent import LeadAgent
from agents.filtering_agent import FilteringAgent
from reviewpilot_core.atomic_files import atomic_write_json
from reviewpilot_core.screening_criteria import criteria_state, save_criteria, require_finalized_criteria
from reviewpilot_core.workflow_state import initialize_workflow_state, start_action, complete_action, load_workflow_state
from reviewpilot_core.state_projection import build_rp_data


class ScreeningCriteriaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'review'
        atomic_write_json(self.project / 'search_conditions.json', {
            'project_name': 'Review', 'description': 'Cancer interventions', 'primary_topic': 'cancer',
            'domain': 'medicine', 'search_terms': 'cancer', 'platforms': ['pubmed']})
        atomic_write_json(self.project / 'collected/summary.json', {'platform_stats': {'pubmed': 1}, 'total_papers': 1})
        initialize_workflow_state(self.project)
        start_action(self.project, 'collect')
        complete_action(self.project, 'collect', {'platform_stats': {'pubmed': 1}, 'platform_errors': {}, 'total': 1})

    def payload(self):
        return {'inclusion': ['Clinical studies involving adults {18+}'], 'exclusion': ['Exclude animal studies'],
                'revision': criteria_state(self.project)['revision']}

    def test_finalized_rules_survive_restart_and_reach_filtering_prompt(self):
        with self.assertRaisesRegex(ValueError, 'finalize'):
            require_finalized_criteria(self.project)
        LeadAgent(self.root).handle_message('review', action='finalize-criteria', input_data=self.payload())
        require_finalized_criteria(self.project)
        self.assertEqual(criteria_state(self.project)['status'], 'finalized')
        captured = []
        def llm(**kwargs):
            captured.append(kwargs['text_prompt'])
            return 'False', {}
        prompt = json.loads((self.project / 'prompts/relevance_prompt.json').read_text())
        out = self.project / 'filtered'
        out.mkdir()
        included, excluded = FilteringAgent(self.project, llm_query=llm)._check_relevance(
            [{'id': '1', 'title': 'Animal experiment', 'abstract': 'Mice were treated.'}], prompt, out)
        self.assertFalse(included)
        self.assertEqual(len(excluded), 1)
        self.assertIn('Exclude animal studies', captured[0])
        self.assertIn('{18+}', captured[0])
        self.assertIn('Mice were treated.', captured[0])
        state = build_rp_data(self.root, 'review')
        self.assertEqual(state['quietActions']['screening'], 'screen')
        self.assertEqual(state['screeningCriteria']['inclusion'], self.payload()['inclusion'])

    def test_revision_conflict_preserves_saved_rules(self):
        old = self.payload()
        save_criteria(self.project, old)
        with self.assertRaisesRegex(ValueError, 'changed'):
            save_criteria(self.project, old, finalized=True)
        self.assertEqual(criteria_state(self.project)['status'], 'draft')

    def test_edits_invalidate_existing_results_and_require_finalization(self):
        save_criteria(self.project, self.payload(), finalized=True)
        start_action(self.project, 'screen')
        complete_action(self.project, 'screen')
        start_action(self.project, 'download-pdfs')
        complete_action(self.project, 'download-pdfs', {'success': 1, 'failed': 0})
        payload = self.payload()
        payload['exclusion'] = ['Exclude reviews']
        save_criteria(self.project, payload)
        stages = load_workflow_state(self.project)['stages']
        self.assertTrue(stages['screening']['stale'])
        self.assertTrue(stages['retrieval']['stale'])
        self.assertFalse(stages['collection']['stale'])
        with self.assertRaisesRegex(ValueError, 'finalize'):
            require_finalized_criteria(self.project)

    def test_chat_refines_and_new_agent_recalls_local_state_without_cross_project_memory(self):
        save_criteria(self.project, self.payload(), finalized=True)
        update = {'reply': 'Added the exclusion.', 'criteria': {'inclusion': ['Adult clinical studies'], 'exclusion': ['Exclude reviews']}}
        LeadAgent(self.root, llm_query=lambda **kw: (json.dumps(update), {})).handle_message(
            'review', message='Exclude reviews', context_step='screening')
        self.assertEqual(criteria_state(self.project)['status'], 'draft')
        captured = {}
        def llm(**kwargs):
            captured.update(kwargs)
            return json.dumps({'reply': 'You excluded reviews.'}), {}
        fresh = LeadAgent(self.root, llm_query=llm)
        fresh.memory_service.set_enabled(False)
        fresh.reply_to_project_message('review', 'What did we decide?', context_step='retrieval')
        self.assertIn('Exclude reviews', captured['text_prompt'])
        self.assertIn('SAVED LOCAL PROJECT STATE', captured['text_prompt'])
        self.assertEqual(captured['text_prompt'].count('What did we decide?'), 1)
        self.assertIn('Criteria saved locally', captured['text_prompt'])

    def test_screen_api_rejects_draft_before_starting_task(self):
        with patch.object(web_app, 'OUTPUT_ROOT', self.root):
            client = TestClient(web_app.create_app())
            response = client.post('/projects/review/actions/screen')
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn('finalize', response.text)
            self.assertEqual(load_workflow_state(self.project)['stages']['screening']['status'], 'ready')

    def test_empty_inclusion_does_not_modify_artifact(self):
        with self.assertRaisesRegex(ValueError, 'inclusion'):
            save_criteria(self.project, {**self.payload(), 'inclusion': []}, finalized=True)
        self.assertFalse((self.project / 'prompts/relevance_prompt.json').exists())

    def test_question_does_not_change_criteria(self):
        save_criteria(self.project, self.payload(), finalized=True)
        before = criteria_state(self.project)
        fresh = LeadAgent(self.root, llm_query=lambda **kw: (json.dumps({'reply': 'Animal studies are excluded.', 'criteria': None}), {}))
        fresh.handle_message('review', message='What are my exclusions?', context_step='screening')
        self.assertEqual(criteria_state(self.project), before)


if __name__ == '__main__':
    unittest.main()
