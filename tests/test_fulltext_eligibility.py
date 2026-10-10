import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

import web_app
from agents.extraction_agent import ExtractionAgent
from reviewpilot_core import fulltext_eligibility as fte
from reviewpilot_core import record_review as review
from reviewpilot_core.atomic_files import atomic_write_json, atomic_write_jsonl
from reviewpilot_core.categorization_analysis import CategorizationAnalysis, _successful_rows
from reviewpilot_core.extraction_schema import build_extraction_prompts, finalize_schema, save_schema_draft
from reviewpilot_core.state_projection import _result_overview, build_rp_data
from reviewpilot_core.task_runner import TaskRunner
from reviewpilot_core.workflow_state import initialize_workflow_state, save_workflow_state

CRITERIA = {'inclusion': ['Clinical study'], 'exclusion': ['Animal-only study']}
EXCLUDE_ANIMAL = 'Exclude — Animal-only study'


def make_project(root, name='study', finalized=True):
    p = root / name
    p.mkdir(parents=True)
    atomic_write_json(p / 'search_conditions.json', {'project_name': name, 'description': 'Clinical AI', 'platforms': ['pubmed'], 'project_path': str(p)})
    atomic_write_json(p / 'prompts/relevance_prompt.json', {'criteria_finalized': finalized, 'eligibility': CRITERIA})
    papers = [{'id': f'p{i}', 'title': f'Paper {i}', 'abstract': 'We enrolled adults.', 'is_relevant': True} for i in range(1, 5)]
    rejected = {'id': 'p9', 'title': 'Mouse paper', 'abstract': 'Mice only.', 'is_relevant': False,
                'human_screening': {'decision': 'exclude', 'original_decision': 'include', 'reason': 'mice', 'criterion': 'Animal-only study'}}
    atomic_write_jsonl(p / 'filtered/included_papers.jsonl', papers)
    atomic_write_jsonl(p / 'filtered/excluded_papers.jsonl', [rejected])
    atomic_write_json(p / 'filtered/screening_stats.json', {'initial_count': 7, 'removed': {'by_date': 1, 'by_exact_dedup': 1, 'by_similarity': 0, 'by_relevance': 1}})
    atomic_write_json(p / 'collected/summary.json', {'total_papers': 7, 'platform_stats': {'pubmed': 7}})
    schema = {'fields': [fte.field(p), {'name': 'participants', 'type': 'integer', 'description': 'Participant count', 'required': False, 'example': ''}]}
    atomic_write_json(p / 'extraction/extraction_schema.json', schema)
    def row(i, eligibility, **extra):
        return {'paper_id': f'p{i}', 'title': f'Paper {i}', 'extraction_source': 'pdf', 'extraction_status': 'success', 'pdf_file': f'row{i}_paper.pdf',
                fte.FIELD: eligibility, 'participants': 10, 'extracted_data': {fte.FIELD: eligibility, 'participants': 10},
                'field_evidence': {}, **extra}
    atomic_write_jsonl(p / 'extraction/extraction_results.jsonl', [
        row(1, 'Include'),
        row(2, EXCLUDE_ANIMAL),
        row(3, ''),
        {'paper_id': 'p4', 'title': 'Paper 4', 'extraction_source': 'web_search_fallback', 'extraction_status': 'success',
         fte.FIELD: EXCLUDE_ANIMAL, 'extracted_data': {fte.FIELD: EXCLUDE_ANIMAL, 'participants': None}, 'participants': None,
         'evidence_tiers': {fte.FIELD: 'web', 'participants': 'not_available'}},
    ])
    (p / 'pdfs').mkdir()
    for i in (1, 2, 3):
        (p / f'pdfs/row{i}_paper.pdf').write_bytes(b'%PDF-1.4\nfixture')
    ledger = initialize_workflow_state(p)
    for stage in ledger['stages'].values():
        stage.update(status='completed', attempt=1, last_valid={'status': 'completed', 'attempt': 1, 'updated_at': stage['updated_at'], 'counts': {}}, counts={})
    save_workflow_state(p, ledger)
    return p


class FieldDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_options_follow_approved_criteria(self):
        p = make_project(self.root)
        self.assertEqual(fte.options(p), ['Include', EXCLUDE_ANIMAL, 'Exclude — not met: Clinical study'])

    def test_field_is_put_first_only_after_criteria_are_finalized(self):
        other = {'fields': [{'name': 'participants', 'type': 'integer'}]}
        finalized = fte.with_field(make_project(self.root), other)
        self.assertEqual([f['name'] for f in finalized['fields']], [fte.FIELD, 'participants'])
        self.assertEqual(fte.with_field(make_project(self.root, 'draft', finalized=False), other)['fields'], other['fields'])
        self.assertEqual(len(fte.with_field(make_project(self.root, 'again'), finalized)['fields']), 2)

    def test_finalize_refreshes_options_from_current_criteria(self):
        p = make_project(self.root)
        save_schema_draft(p, json.loads((p / 'extraction/extraction_schema.json').read_text()))
        atomic_write_json(p / 'prompts/relevance_prompt.json', {'criteria_finalized': True, 'eligibility': {'inclusion': ['Clinical study'], 'exclusion': ['Review article']}})
        finalize_schema(p)
        prompt = json.loads((p / 'extraction/extraction_prompt.json').read_text())
        self.assertIn('Exclude — Review article', prompt['schema']['fields'][0]['enum'])
        self.assertNotIn(EXCLUDE_ANIMAL, prompt['schema']['fields'][0]['enum'])

    def test_schema_without_the_field_finalizes_exactly_as_before(self):
        p = make_project(self.root)
        schema = {'fields': [{'name': 'model_type', 'type': 'Select', 'enum': ['A', 'B'], 'description': 'Type', 'required': False, 'example': 'A'}]}
        saved = save_schema_draft(p, schema)
        draft_before = (p / 'extraction/extraction_schema_draft.json').read_bytes()
        finalize_schema(p)
        self.assertEqual((p / 'extraction/extraction_schema_draft.json').read_bytes(), draft_before)
        system, stage, _ = build_extraction_prompts(json.loads((p / 'search_conditions.json').read_text()), saved)
        prompt = json.loads((p / 'extraction/extraction_prompt.json').read_text())
        self.assertEqual((prompt['system_prompt'], prompt['extraction_prompt'], prompt['schema']), (system, stage, saved))


class ValueTests(unittest.TestCase):
    OPTIONS = ['Include', EXCLUDE_ANIMAL, 'Exclude — not met: Clinical study']

    def test_normalize_value(self):
        cases = {'Include': 'Include', 'include': 'Include', 'Include: meets all criteria': 'Include',
                 'Exclude - Animal-only study': EXCLUDE_ANIMAL, 'EXCLUDE — animal-only  study': EXCLUDE_ANIMAL,
                 'Exclude because it is an Animal-only study': EXCLUDE_ANIMAL, '': '', None: None}
        for raw, expected in cases.items():
            self.assertEqual(fte.normalize_value(raw, self.OPTIONS), (expected, None), raw)
        self.assertEqual(fte.normalize_value('Exclude — off topic', self.OPTIONS), ('', 'Exclude — off topic'))

    def test_extraction_keeps_paper_when_value_cannot_be_mapped(self):
        agent = ExtractionAgent.__new__(ExtractionAgent)
        prompt = {'schema': {'fields': [{'name': fte.FIELD, 'type': 'Select', 'enum': self.OPTIONS}, {'name': 'n', 'type': 'integer'}]}}
        data = agent._validate_schema_data({fte.FIELD: 'Exclude - Animal-only study', 'n': 3}, prompt, set())
        self.assertEqual(data[fte.FIELD], EXCLUDE_ANIMAL)
        data = agent._validate_schema_data({fte.FIELD: 'Not sure', 'n': 3}, prompt, set())
        self.assertEqual((data[fte.FIELD], data[fte.RAW_KEY]), ('', 'Not sure'))
        data = agent._validate_schema_data({'n': 3}, prompt, set())
        self.assertEqual((data[fte.FIELD], data[fte.RAW_KEY]), ('', fte.MISSING))
        with self.assertRaisesRegex(ValueError, 'missing fields: n'):
            agent._validate_schema_data({fte.FIELD: 'Include'}, prompt, set())

    def test_exclusions_skip_synthesis_but_only_pdfs_count_as_full_text(self):
        pdf = {'extraction_source': 'pdf', 'extraction_status': 'success', fte.FIELD: EXCLUDE_ANIMAL}
        self.assertTrue(fte.is_excluded(pdf))
        web = {**pdf, 'extraction_source': 'web_search_fallback'}
        self.assertTrue(fte.is_excluded(web))
        self.assertFalse(fte.assessed(web))
        self.assertFalse(fte.is_excluded({**pdf, 'extraction_status': 'error'}))
        self.assertFalse(fte.is_excluded({**pdf, fte.FIELD: 'Include'}))
        self.assertEqual([r['paper_id'] for r in _successful_rows([{**pdf, 'paper_id': 'a'}, {**pdf, 'paper_id': 'b', fte.FIELD: ''}])], ['b'])


class ProjectCountsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = make_project(self.root)
        self.runner = TaskRunner()
        self.addCleanup(self.runner.shutdown)
        self.enterContext(patch.object(web_app, 'OUTPUT_ROOT', self.root))
        self.enterContext(patch.object(web_app, 'task_runner', self.runner))
        self.client = TestClient(web_app.create_app())

    def test_prisma_counts(self):
        counts = fte.prisma_counts(self.project)
        self.assertEqual(counts['identification'], {'records_by_source': {'pubmed': 7}, 'records_identified': 7,
                                                    'removed_outside_date_range': 1, 'removed_duplicates': 1})
        s = counts['screening']
        self.assertEqual((s['records_screened'], s['records_excluded'], s['human_overrides']), (5, 1, 1))
        self.assertEqual((s['reports_sought'], s['reports_not_retrieved'], s['reports_not_extracted'], s['reports_assessed']), (4, 1, 0, 3))
        self.assertEqual((s['reports_excluded'], s['reports_excluded_by_reason']), (1, {'Animal-only study': 1}))
        self.assertEqual(counts['included']['studies_included'], 2)
        self.assertEqual((s['not_retrieved_assessed_from_web_sources'], s['not_retrieved_excluded_from_web_sources'],
                          counts['included']['studies_included_from_web_sources']), (1, 1, 0))
        self.assertTrue(s['full_text_eligibility_field'])

    def test_export_and_overview(self):
        response = self.client.get('/projects/study/exports/prisma')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['included']['studies_included'], 2)
        rows = [json.loads(line) for line in (self.project / 'extraction/extraction_results.jsonl').read_text().splitlines()]
        overview = {item['label']: item['value'] for item in _result_overview(self.project, {}, {}, {}, [], {}, rows, {})}
        self.assertEqual((overview['Full-text excluded'], overview['Included in review']), ('1', '2'))
        rows = [{k: v for k, v in r.items() if k != fte.FIELD} for r in rows]
        self.assertNotIn('Full-text excluded', {item['label'] for item in _result_overview(self.project, {}, {}, {}, [], {}, rows, {})})

    def test_human_correction_uses_options_and_updates_counts(self):
        key = review.key(json.loads((self.project / 'extraction/extraction_results.jsonl').read_text().splitlines()[0]))
        detail = self.client.get('/projects/study/review/field', params={'key': key, 'field': fte.FIELD}).json()
        self.assertEqual(detail['options'], fte.options(self.project))
        bad = {'key': key, 'field': fte.FIELD, 'value': 'Exclude', 'reason': 'x', 'revision': detail['revision']}
        self.assertEqual(self.client.patch('/projects/study/review/field', json=bad).status_code, 400)
        good = {**bad, 'value': EXCLUDE_ANIMAL, 'reason': 'Methods describe mice only'}
        response = self.client.patch('/projects/study/review/field', json=good)
        self.assertEqual(response.status_code, 200, response.text)
        counts = fte.prisma_counts(self.project)['screening']
        self.assertEqual((counts['reports_excluded'], counts['full_text_corrections']), (2, 1))



class AnalysisHeaderTests(unittest.TestCase):
    """The Step 5 header, the category counts and the PRISMA export agree once papers are excluded at full text."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = make_project(self.root)
        self.results = self.project / 'extraction/extraction_results.jsonl'

    def rows(self):
        return [json.loads(line) for line in self.results.read_text().splitlines()]

    def test_summary_matches_prisma_counts_and_the_analysis_rows(self):
        summary = fte.analysis_summary(self.rows())
        self.assertEqual(summary, {'extracted': 4, 'included': 2, 'excluded': 2, 'excluded_at_full_text': 1,
                                   'excluded_from_web_sources': 1, 'no_evidence': 0, 'eligibility_undetermined': 0,
                                   'reasons': {'Animal-only study': 2}})
        counts = fte.prisma_counts(self.project)
        self.assertEqual(summary['excluded_at_full_text'], counts['screening']['reports_excluded'])
        self.assertEqual(summary['excluded_from_web_sources'], counts['screening']['not_retrieved_excluded_from_web_sources'])
        self.assertEqual(summary['included'], counts['included']['studies_included'] + counts['included']['studies_included_from_web_sources'])
        self.assertEqual(summary['included'], len(_successful_rows(self.rows())))

    def test_header_categories_and_prisma_export_agree(self):
        CategorizationAnalysis(self.project).run({'categorizer_func': lambda row: 'Clinical cohort'})
        data = build_rp_data(self.root, 'study')
        workflow = data['categorizationWorkflow']
        self.assertEqual(workflow['metrics'], {'papersExtracted': 4, 'papersInAnalysis': 2, 'fields': 1, 'papersWithoutCategory': 0})
        self.assertEqual(workflow['fullTextExclusions'], {'total': 2, 'atFullText': 1, 'fromWebSources': 1,
                                                          'reasons': [{'label': 'Animal-only study', 'count': 2}]})
        self.assertEqual(data['categorizationSummary'], {'papers': 2, 'groups': 1})
        self.assertEqual([(c['name'], c['count']) for c in data['categorizationAnalysis']['categories']], [('Clinical cohort', 2)])
        self.assertEqual((workflow['fullResults']['total'], len(workflow['fullResults']['rows'])), (2, 2))
        self.assertEqual(fte.prisma_counts(self.project)['included']['studies_categorized'], 2)

    def test_not_met_reason_reads_as_a_sentence(self):
        rows = self.rows()
        rows[0][fte.FIELD] = 'Exclude — not met: Clinical study'
        atomic_write_jsonl(self.results, rows)
        reasons = build_rp_data(self.root, 'study')['categorizationWorkflow']['fullTextExclusions']['reasons']
        self.assertEqual(reasons, [{'label': 'Animal-only study', 'count': 2}, {'label': 'Not met: Clinical study', 'count': 1}])

    def test_single_mode_paper_without_a_value_is_not_counted_as_a_category(self):
        rows = self.rows()
        rows[2]['participants'] = None
        atomic_write_jsonl(self.results, rows)
        llm = lambda **kwargs: ('{"category": "Small"}', {})
        CategorizationAnalysis(self.project, llm_query=llm).run({'field': 'participants', 'mode': 'single', 'categories': ['Small', 'Large']})
        data = build_rp_data(self.root, 'study')
        workflow = data['categorizationWorkflow']
        self.assertEqual(data['categorizationSummary'], {'papers': 1, 'groups': 1})
        self.assertEqual((workflow['metrics']['papersInAnalysis'], workflow['metrics']['papersWithoutCategory']), (2, 1))
        self.assertEqual([c['name'] for c in data['categorizationAnalysis']['categories']], ['Small'])
        self.assertEqual(workflow['categorizedDistribution']['values'], [{'label': 'Small', 'count': 1}])
        self.assertEqual([g['name'] for g in data['groups']], ['Small'])


if __name__ == '__main__':
    unittest.main()
