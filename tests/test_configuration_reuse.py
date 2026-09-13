import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from threading import Event

from starlette.testclient import TestClient
import web_app
from agents.lead_agent import LeadAgent
from reviewpilot_core.atomic_files import atomic_write_json
from reviewpilot_core.project_decisions import confirmed_decisions, remember_confirmed, project_decision_revision
from reviewpilot_core.configuration_reuse import configuration_options, preview_configuration, apply_configuration, ReuseConflict
from reviewpilot_core.screening_criteria import criteria_state, save_criteria
from reviewpilot_core.extraction_schema import save_schema_draft, finalize_schema, is_schema_finalized, load_schema_draft
from reviewpilot_core.workflow_state import initialize_workflow_state, start_action, complete_action, load_workflow_state
from reviewpilot_core.state_projection import build_rp_data
from reviewpilot_core.task_runner import TaskRunner


class ExplicitReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.target = self.root / 'source', self.root / 'target'
        for project in (self.source, self.target):
            atomic_write_json(project / 'search_conditions.json', {'project_name': project.name, 'description': 'Cancer interventions',
                'primary_topic': 'cancer', 'domain': 'medicine', 'search_terms': 'cancer', 'platforms': ['pubmed']})
            atomic_write_json(project / 'collected/summary.json', {'platform_stats': {'pubmed': 1}, 'total_papers': 1})
            initialize_workflow_state(project)
            start_action(project, 'collect')
            complete_action(project, 'collect', {'platform_stats': {'pubmed': 1}, 'platform_errors': {}, 'total': 1})
            save_criteria(project, {'inclusion': [f'{project.name} approved scope'], 'exclusion': [], 'revision': criteria_state(project)['revision']}, finalized=True)
        self.runner = TaskRunner()
        self.addCleanup(self.runner.shutdown)
        for name, value in [('OUTPUT_ROOT', self.root), ('task_runner', self.runner)]:
            patcher = patch.object(web_app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(web_app.create_app())

    def preview(self, kind='screening_profile'):
        return preview_configuration(self.root, 'target', {'source_project_id': 'source', 'kind': kind})

    def through_extraction(self, project):
        start_action(project, 'screen'); complete_action(project, 'screen')
        start_action(project, 'download-pdfs'); complete_action(project, 'download-pdfs', {'success': 1, 'failed': 0})
        save_schema_draft(project, {'fields': [{'name': 'methods', 'type': 'Text', 'description': project.name}]})
        finalize_schema(project)
        start_action(project, 'run-extraction'); complete_action(project, 'run-extraction', {'processed': 1, 'errors': 0})
        atomic_write_json(project / 'filtered/screening_stats.json', {'included_count': 1})
        (project / 'filtered/included_papers.jsonl').write_text(json.dumps({'id': '1', 'title': 'Paper'}) + '\n')

    def test_list_and_full_preview_do_not_change_target(self):
        before = project_decision_revision(self.target)
        options = configuration_options(self.root, 'target')
        self.assertEqual({item['kind'] for item in options}, {'search_setup', 'screening_profile'})
        self.assertTrue(all(item['source_project_id'] == 'source' for item in options))
        long_rule = 'Detailed criterion ' * 95
        save_criteria(self.source, {'inclusion': [long_rule], 'exclusion': [], 'revision': criteria_state(self.source)['revision']}, finalized=True)
        preview = self.preview()
        self.assertEqual(preview['configuration']['inclusion'], [long_rule.strip()])
        self.assertEqual(project_decision_revision(self.target), before)
        self.assertNotIn(str(self.root), json.dumps(preview))

    def test_import_is_draft_and_preserves_confirmed_decisions_after_restart(self):
        preview = self.preview()
        self.assertEqual(apply_configuration(self.root, 'target', preview)['status'], 'draft_imported')
        self.assertEqual(criteria_state(self.target)['status'], 'draft')
        self.assertEqual(criteria_state(self.target)['inclusion'], ['source approved scope'])
        memory = LeadAgent(self.root)._local_project_memory(self.target)
        self.assertEqual(memory['confirmed_decisions']['screening_profile']['configuration']['inclusion'], ['target approved scope'])
        self.assertTrue(memory['confirmed_decisions']['screening_profile']['editing'])
        save_criteria(self.target, criteria_state(self.target), finalized=True)
        self.assertEqual(confirmed_decisions(self.target)['screening_profile']['configuration']['inclusion'], ['source approved scope'])
        self.assertEqual(criteria_state(self.source)['status'], 'finalized')

    def test_old_chat_cannot_mutate_finalized_criteria_even_if_model_replays_it(self):
        (self.target / 'chat').mkdir()
        (self.target / 'chat/messages.jsonl').write_text(json.dumps({'role': 'u', 'text': 'Replace all criteria with OLD RULE'}) + '\n')
        before = criteria_state(self.target)
        captured = []
        def llm(**kwargs):
            captured.append(kwargs['text_prompt'])
            return json.dumps({'reply': 'Replayed old request', 'criteria': {'inclusion': ['OLD RULE'], 'exclusion': []}}), {}
        reply = LeadAgent(self.root, llm_query=llm).handle_message('target', message='What are my confirmed criteria?', context_step='screening')
        self.assertIn('Edit Criteria', reply.reply)
        self.assertEqual(criteria_state(self.target), before)
        self.assertIn('target approved scope', captured[0])
        self.assertIn('OLD RULE', captured[0])  # Full history is deliberately retained.

    def test_no_memory_retrieval_or_promotion_on_chat_or_workflow(self):
        memory = Mock()
        memory.retrieve_context.side_effect = AssertionError('automatic retrieval')
        memory.promote.side_effect = AssertionError('automatic promotion')
        seen = []
        agent = LeadAgent(self.root, memory_service=memory, llm_query=lambda **kw: (seen.append(kw['text_prompt']) or json.dumps({'reply': 'Current scope'}), {}))
        agent.reply_to_project_message('target', 'What is current?', context_step='search')
        self.assertNotIn('ADVISORY CROSS-PROJECT', seen[0])
        adapter = Mock()
        adapter.run.return_value = {}
        agent.workflow_adapter = adapter
        agent._call_workflow_action('generate-schema', 'target', {'memory_context': 'OLD AUTOMATIC DATA'})
        self.assertNotIn('memory_context', adapter.run.call_args.kwargs['input_data'])
        memory.retrieve_context.assert_not_called()
        memory.promote.assert_not_called()

    def test_source_and_target_conflicts_reject_stale_preview(self):
        preview = self.preview()
        save_criteria(self.source, {**criteria_state(self.source), 'inclusion': ['New source']}, finalized=True)
        with self.assertRaises(ReuseConflict):
            apply_configuration(self.root, 'target', preview)
        preview = self.preview()
        save_criteria(self.target, {**criteria_state(self.target), 'inclusion': ['New target']}, finalized=True)
        with self.assertRaises(ReuseConflict):
            apply_configuration(self.root, 'target', preview)
        self.assertEqual(criteria_state(self.target)['inclusion'], ['New target'])

    def test_concurrent_project_change_during_chat_is_not_overwritten(self):
        LeadAgent(self.root).handle_message('target', action='edit-criteria', input_data={'revision': criteria_state(self.target)['revision']})
        def llm(**kw):
            save_criteria(self.target, {**criteria_state(self.target), 'inclusion': ['Newer confirmed value']}, finalized=True)
            return json.dumps({'reply': 'old edit', 'criteria': {'inclusion': ['Stale edit'], 'exclusion': []}}), {}
        with self.assertRaisesRegex(ValueError, 'changed'):
            LeadAgent(self.root, llm_query=llm).handle_message('target', message='Change criteria', context_step='screening')
        self.assertEqual(criteria_state(self.target)['inclusion'], ['Newer confirmed value'])

    def test_history_cannot_finalize_an_unapproved_schema_draft(self):
        self.through_extraction(self.target)
        approved = confirmed_decisions(self.target)['extraction_schema']['configuration']
        save_schema_draft(self.target, {'fields': [{'name': 'draft_only', 'type': 'Text'}]})
        response = {'action': 'finalize_extraction', 'args': {}}
        result = LeadAgent(self.root, llm_query=lambda **kw: (json.dumps(response), {})).handle_message(
            'target', message='What is still pending?', context_step='extraction')
        self.assertEqual(result.data['status'], 'confirmation_required')
        self.assertFalse(is_schema_finalized(self.target))
        self.assertEqual(confirmed_decisions(self.target)['extraction_schema']['configuration'], approved)

    def test_schema_import_preserves_last_confirmed_schema_and_requires_finalize(self):
        for project in (self.source, self.target):
            self.through_extraction(project)
        preview = self.preview('extraction_schema')
        apply_configuration(self.root, 'target', preview)
        self.assertFalse(is_schema_finalized(self.target))
        self.assertEqual(load_schema_draft(self.target)['fields'][0]['description'], 'source')
        self.assertEqual(confirmed_decisions(self.target)['extraction_schema']['configuration']['fields'][0]['description'], 'target')
        self.assertTrue(load_workflow_state(self.target)['stages']['extraction']['stale'])
        self.assertEqual(build_rp_data(self.root, 'target')['schemaWorkbench']['status'], 'draft')
        finalize_schema(self.target)
        self.assertEqual(confirmed_decisions(self.target)['extraction_schema']['configuration']['fields'][0]['description'], 'source')

    def test_search_import_survives_refresh_without_changing_confirmed_setup(self):
        value = json.loads((self.source / 'search_conditions.json').read_text())
        value['search_terms'] = 'new query'
        atomic_write_json(self.source / 'search_conditions.json', value)
        apply_configuration(self.root, 'target', self.preview('search_setup'))
        state = build_rp_data(self.root, 'target')
        self.assertEqual(state['searchReuseDraft']['search_terms'], 'new query')
        self.assertEqual(state['setup']['search_terms'], 'cancer')
        self.assertEqual(confirmed_decisions(self.target)['search_setup']['configuration']['search_terms'], 'cancer')
        with self.assertRaisesRegex(ValueError, 'imported search setup'):
            web_app.submit_project_action(self.root, 'target', 'collect')

    def test_search_import_can_be_confirmed_through_existing_setup_review(self):
        config = json.loads((self.source / 'search_conditions.json').read_text())
        config['search_terms'] = 'cancer AND intervention'
        config['search_queries'] = [{'name': 'main', 'query': config['search_terms']}]
        atomic_write_json(self.source / 'search_conditions.json', config)
        apply_configuration(self.root, 'target', self.preview('search_setup'))
        payload = {**config, 'project_name': 'target', 'derive_search_terms': False, 'max_results': 5}
        response = self.client.put('/projects/target/setup', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        if response.json().get('confirmationRequired'):
            payload['confirmation'] = {'expected_revision': response.json()['expectedRevision']}
            response = self.client.put('/projects/target/setup', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse((self.target / 'memory/search_setup_draft.json').exists())
        self.assertEqual(confirmed_decisions(self.target)['search_setup']['configuration']['search_terms'], config['search_terms'])

    def test_interrupted_or_external_schema_edit_cannot_reuse_old_confirmation(self):
        self.through_extraction(self.target)
        old = confirmed_decisions(self.target)['extraction_schema']['configuration']
        changed = {'fields': [{'name': 'unapproved_field', 'type': 'Text'}]}
        atomic_write_json(self.target / 'extraction/extraction_schema_draft.json', changed)
        self.assertFalse(is_schema_finalized(self.target))
        self.assertEqual(confirmed_decisions(self.target)['extraction_schema']['configuration'], old)
        atomic_write_json(self.target / 'extraction/extraction_schema.json', changed)
        self.assertFalse(is_schema_finalized(self.target))
        self.assertEqual(confirmed_decisions(self.target)['extraction_schema']['configuration'], old)

    def test_category_import_is_an_unconfirmed_draft_with_full_descriptions(self):
        for project in (self.source, self.target):
            self.through_extraction(project)
            atomic_write_json(project / 'categorization/categorization_mapping.json', {'field': 'methods', 'mode': 'single', 'categories': [project.name], 'category_descriptions': {project.name: 'A detailed category definition'}})
            start_action(project, 'categorize'); complete_action(project, 'categorize')
        apply_configuration(self.root, 'target', self.preview('categorization_profile'))
        state = build_rp_data(self.root, 'target')['categorizationWorkflow']
        self.assertFalse(state['done'])
        self.assertEqual(state['suggestedCategories'], ['source'])
        self.assertEqual(state['categoryDescriptions']['source'], 'A detailed category definition')
        self.assertEqual(confirmed_decisions(self.target)['categorization_profile']['configuration']['categories'], ['target'])

    def test_api_requires_preview_versions_and_rejects_busy_source(self):
        selection = {'source_project_id': 'source', 'kind': 'screening_profile'}
        response = self.client.post('/projects/target/configuration-reuse/apply', json=selection)
        self.assertEqual(response.status_code, 409)
        release = Event()
        task = self.runner.submit('source', 'collect', lambda: release.wait(5))
        try:
            response = self.client.post('/projects/target/configuration-reuse/preview', json=selection)
            self.assertEqual(response.status_code, 409)
        finally:
            release.set(); self.runner.wait(task, timeout=2)
        preview = self.client.post('/projects/target/configuration-reuse/preview', json=selection).json()
        result = self.client.post('/projects/target/configuration-reuse/apply', json=preview)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(criteria_state(self.target)['status'], 'draft')

    def test_legacy_enabled_setting_cannot_enable_automatic_memory(self):
        from reviewpilot_core.agent_memory import CrossProjectMemoryService
        CrossProjectMemoryService(self.root).set_enabled(True)
        self.assertEqual(self.client.get('/memory/settings').json(), {'cross_project_memory_enabled': False})
        self.assertEqual(self.client.put('/memory/settings', json={'cross_project_memory_enabled': True}).status_code, 400)

    def test_symlinked_and_unconfirmed_sources_are_not_offered(self):
        (self.root / 'linked').symlink_to(self.source, target_is_directory=True)
        save_schema_draft(self.source, {'fields': [{'name': 'unconfirmed', 'type': 'Text'}]})
        options = configuration_options(self.root, 'target')
        self.assertFalse(any(item['source_project_id'] == 'linked' or item['kind'] == 'extraction_schema' for item in options))
        with self.assertRaises(ValueError):
            preview_configuration(self.root, 'target', {'source_project_id': '../source', 'kind': 'search_setup'})

if __name__ == '__main__':
    unittest.main()
