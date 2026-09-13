import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from starlette.testclient import TestClient
import web_app
from reviewpilot_core.atomic_files import atomic_write_json, atomic_write_jsonl
from reviewpilot_core import record_review as review
from reviewpilot_core.demo_projects import copy_example
from reviewpilot_core.evidence_support import verify_field_evidence, evidence_for_field
from reviewpilot_core.screening_evidence import parse_screening_response, evidence_prompt
from reviewpilot_core.workflow_state import initialize_workflow_state, load_workflow_state, save_workflow_state
from reviewpilot_core.task_runner import TaskRunner


def make_project(root, name='study'):
    p = root / name
    p.mkdir(parents=True)
    atomic_write_json(p / 'search_conditions.json', {'project_name': name, 'description': 'Clinical AI', 'platforms': ['pubmed'], 'project_path': str(p)})
    atomic_write_json(p / 'prompts/relevance_prompt.json', {'criteria_finalized': True, 'eligibility': {'inclusion': ['Clinical study'], 'exclusion': ['Animal-only study']}})
    papers = [{'id': 'p1', 'title': 'Clinical study of AI', 'abstract': 'We enrolled 42 adults.', 'is_relevant': True},
              {'id': 'p2', 'title': 'Animal study', 'abstract': 'Only mice were studied.', 'is_relevant': False}]
    atomic_write_jsonl(p / 'filtered/included_papers.jsonl', papers[:1])
    atomic_write_jsonl(p / 'filtered/excluded_papers.jsonl', papers[1:])
    atomic_write_jsonl(p / 'filtered/filtered_papers.jsonl', papers[:1])
    atomic_write_json(p / 'collected/summary.json', {'total_papers': 2, 'platform_stats': {'pubmed': 2}})
    atomic_write_jsonl(p / 'collected/pubmed.jsonl', papers)
    atomic_write_json(p / 'extraction/extraction_schema.json', {'fields': [{'name': 'participants', 'type': 'integer', 'description': 'Participant count'}]})
    atomic_write_jsonl(p / 'extraction/extraction_results.jsonl', [{'paper_id': 'p1', 'title': papers[0]['title'], 'extraction_source': 'pdf', 'extraction_status': 'success', 'pdf_file': 'row1_paper.pdf', 'participants': 40, 'extracted_data': {'participants': 40}, 'field_evidence': {'participants': {'quote': 'We enrolled 42 adults.', 'page': 99}}}])
    (p / 'pdfs').mkdir()
    (p / 'pdfs/row1_paper.pdf').write_bytes(b'%PDF-1.4\nfixture')
    ledger = initialize_workflow_state(p)
    for stage in ledger['stages'].values():
        stage.update(status='completed', attempt=1, last_valid={'status':'completed','attempt':1,'updated_at':stage['updated_at'],'counts':{}}, counts={})
    save_workflow_state(p, ledger)
    return p


class RecordReviewTests(unittest.TestCase):
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

    def test_screening_correction_updates_artifacts_and_invalidates_downstream(self):
        row = review.projection(self.project)['screening'][1]
        payload = {'key': row['key'], 'decision':'include', 'reason':'Retain for full-text assessment', 'criterion':'Clinical study', 'revision':review.revision(self.project)}
        response = self.client.patch('/projects/study/review/screening', json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(review.read_jsonl(self.project/'filtered/included_papers.jsonl')),2)
        self.assertEqual(review.read_jsonl(self.project/'filtered/excluded_papers.jsonl'),[])
        ledger=load_workflow_state(self.project)
        self.assertFalse(ledger['stages']['screening']['stale'])
        for counts in [ledger['stages']['screening']['counts'], ledger['stages']['screening']['last_valid']['counts']]:
            self.assertEqual(counts['included'], counts['included_count'])
            self.assertEqual(counts['excluded'], counts['excluded_count'])
        self.assertTrue(all(ledger['stages'][k]['stale'] for k in ['retrieval','extraction','categorization']))
        self.assertEqual(review.read_json(self.project/review.AUDIT,[])[0]['before']['is_relevant'],False)
        self.assertEqual(self.client.patch('/projects/study/review/screening',json=payload).status_code,409)
        self.assertEqual(self.client.get('/projects/study/exports/extraction-results').status_code,404)

    def test_same_decision_confirmation_keeps_downstream_current(self):
        row=review.projection(self.project)['screening'][0]
        review.save_screening(self.project,{'key':row['key'],'decision':'include','criterion':'Clinical study','reason':'Checked abstract','revision':review.revision(self.project)})
        self.assertFalse(load_workflow_state(self.project)['stages']['extraction']['stale'])
        self.assertTrue(review.projection(self.project)['screening'][0]['reviewed'])

    def test_pdf_identity_cannot_follow_a_changed_row_number(self):
        from agents.download_agent import DownloadAgent
        from agents.extraction_agent import ExtractionAgent
        row=review.projection(self.project)['screening'][0]
        review.save_screening(self.project,{'key':row['key'],'decision':'exclude','criterion':'Animal-only study','reason':'Reviewer correction','revision':review.revision(self.project)})
        row=review.projection(self.project)['screening'][1]  # other excluded paper
        row=next(r for r in review.projection(self.project)['screening'] if r['title']=='Animal study')
        review.save_screening(self.project,{'key':row['key'],'decision':'include','criterion':'Clinical study','reason':'Retain for review','revision':review.revision(self.project)})
        paper=review.read_jsonl(self.project/'filtered/included_papers.jsonl')[0]
        pdfs=list((self.project/'pdfs').glob('*.pdf'))
        self.assertIsNone(DownloadAgent(self.project)._matching_pdf_for_paper(1,paper,pdfs))
        self.assertIsNone(ExtractionAgent(self.project)._pdf_for_paper(1,paper,self.project/'pdfs',pdfs))
        from reviewpilot_core.extraction_preview import _formal_row
        self.assertIsNone(_formal_row(self.project, paper, 0))

    def test_paper_id_stays_consistent_when_collection_also_has_doi(self):
        paper = review.read_jsonl(self.project / 'filtered/included_papers.jsonl')[0]
        paper['doi'] = '10.1000/test'
        atomic_write_jsonl(self.project / 'filtered/included_papers.jsonl', [paper])
        self.assertEqual(review.key(paper), review.key({'paper_id': 'p1'}))
        before = (self.project / 'extraction/extraction_results.jsonl').read_bytes()
        calls = []
        def llm(**kwargs):
            calls.append(kwargs)
            return json.dumps({'participants': 42, '_field_evidence': {'participants': {'quote': '42 adults', 'page': 99}}}), {}
        review.run_sample(self.project, {'mode': 'extraction', 'keys': [review.key(paper)], 'revision': review.revision(self.project)}, llm_query=llm, pdf_reader=lambda path: '[PDF page 2]\nWe enrolled 42 adults.')
        sample = review.projection(self.project)['liveSample']
        self.assertEqual(len(calls), 1)
        self.assertEqual(sample['results'][0]['fields']['participants'], 42)
        self.assertEqual(sample['results'][0]['evidence']['participants']['page'], 2)
        self.assertEqual((self.project / 'extraction/extraction_results.jsonl').read_bytes(), before)

    def test_web_fallback_does_not_claim_pdf_grounding(self):
        row = {'extraction_source': 'web_search_fallback', 'source_urls': ['javascript:alert(1)', 'https://example.org/paper'], 'field_evidence': {'x': {'quote': 'unsupported', 'page': 1}}}
        evidence = evidence_for_field(self.project, row, 'x')
        self.assertEqual(evidence['status'], 'not_confirmable')
        self.assertEqual(evidence['quote'], '')
        self.assertEqual(evidence['sourceUrls'], ['https://example.org/paper'])

    def test_field_quote_uses_actual_page_and_correction_preserves_original(self):
        record_key=review.key({'id':'p1'})
        with patch('reviewpilot_core.evidence_support.read_pdf_pages',return_value=[{'page':2,'text':'Methods. We enrolled 42 adults.'}]):
            detail=self.client.get('/projects/study/review/field',params={'key':record_key,'field':'participants'}).json()
            self.assertEqual(detail['evidence']['page'],2)
            payload={'key':record_key,'field':'participants','value':42,'reason':'Corrected count against Methods','quote':'We enrolled 42 adults.','revision':detail['revision']}
            response=self.client.patch('/projects/study/review/field',json=payload)
            self.assertEqual(response.status_code,200,response.text)
        row=review.read_jsonl(self.project/'extraction/extraction_results.jsonl')[0]
        self.assertEqual(row['participants'],42)
        self.assertEqual(row['extracted_data']['participants'],42)
        self.assertEqual(row['human_fields']['participants']['original_value'],40)
        self.assertEqual(row['field_evidence']['participants']['page'],2)
        stages=load_workflow_state(self.project)['stages']
        self.assertTrue(stages['categorization']['stale'])
        self.assertFalse(stages['extraction']['stale'])

    def test_fabricated_quote_and_wrong_value_type_rejected(self):
        payload={'key':review.key({'id':'p1'}),'field':'participants','value':'forty','reason':'fix','revision':review.revision(self.project)}
        self.assertEqual(self.client.patch('/projects/study/review/field',json=payload).status_code,400)
        payload.update(value=42,quote='invented evidence')
        with patch('reviewpilot_core.evidence_support.read_pdf_pages',return_value=[{'page':1,'text':'different text'}]):
            self.assertEqual(self.client.patch('/projects/study/review/field',json=payload).status_code,400)
        self.assertFalse((self.project/review.AUDIT).exists())

    def test_interrupted_publication_recovers_artifacts_and_audit_together(self):
        row=review.projection(self.project)['screening'][1]
        payload={'key':row['key'],'decision':'include','criterion':'Clinical study','reason':'reviewed','revision':review.revision(self.project)}
        original=review.atomic_write_text
        calls=[]
        def fail_once(path,content):
            calls.append(path)
            if len(calls)==2:raise OSError('disk interrupted')
            return original(path,content)
        with patch.object(review,'atomic_write_text',side_effect=fail_once),self.assertRaises(OSError):review.save_screening(self.project,payload)
        self.assertTrue((self.project/review.PENDING).exists())
        review.recover(self.project)
        self.assertEqual(len(review.projection(self.project)['screening']),2)
        self.assertEqual(len(review.read_json(self.project/review.AUDIT,[])),1)
        self.assertFalse((self.project/review.PENDING).exists())

    def test_running_task_rejects_review(self):
        event=Event(); task=self.runner.submit('study','collect',lambda:event.wait(3))
        try:
            payload={'revision':review.revision(self.project),'key':'p1'}
            self.assertEqual(self.client.patch('/projects/study/review/field',json=payload).status_code,409)
        finally:event.set();self.runner.wait(task,3)

    def test_live_sample_runs_selected_paper_only_without_overwriting_saved_results(self):
        before=(self.project/'filtered/included_papers.jsonl').read_bytes()
        calls=[]
        def llm(**kwargs):
            calls.append(kwargs)
            return json.dumps({'include':False,'reason':'Animal-only research','criterion':'Animal-only study','quote':'Only mice were studied.','uncertain':False}),{}
        review.run_sample(self.project,{'mode':'screening','keys':[review.key({'id':'p2'})],'revision':review.revision(self.project)},llm_query=llm)
        sample=review.projection(self.project)['liveSample']
        self.assertEqual(len(calls),1)
        self.assertEqual(sample['results'][0]['decision'],'exclude')
        self.assertEqual(sample['execution'],'live')
        self.assertEqual((self.project/'filtered/included_papers.jsonl').read_bytes(),before)
        with self.assertRaises(ValueError):review.run_sample(self.project,{'mode':'screening','keys':['x']*6,'revision':review.revision(self.project)},llm_query=llm)

    def test_all_example_mutations_blocked_and_copy_is_independent(self):
        source=make_project(self.root,'quick-start-hci-showcase')
        papers = review.read_jsonl(source / 'filtered/included_papers.jsonl')
        papers[0]['pdf_path'] = str((source / 'pdfs/row1_paper.pdf').resolve())
        atomic_write_jsonl(source / 'filtered/included_papers.jsonl', papers)
        before={str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()}
        for method,endpoint,payload in [('post','chat',{'message':'change scope'}),('put','setup',{}),('post','actions/screen',{}),('patch','review/field',{}),('post','configuration-reuse/apply',{})]:
            response=getattr(self.client,method)('/projects/quick-start-hci-showcase/'+endpoint,json=payload)
            self.assertEqual(response.status_code,403,endpoint)
        response=self.client.post('/projects/quick-start-hci-showcase/copy-example')
        self.assertEqual(response.status_code,201,response.text)
        copied=self.root/response.json()['id']
        self.assertNotEqual(copied,source)
        self.assertEqual(review.read_json(copied/'search_conditions.json',{})['project_path'],str(copied.resolve()))
        self.assertEqual(review.read_json(copied/'example_origin.json',{})['mode'],'precomputed_snapshot')
        self.assertEqual(review.read_jsonl(copied/'filtered/included_papers.jsonl')[0]['pdf_path'], str((copied/'pdfs/row1_paper.pdf').resolve()))
        self.assertEqual(before,{str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()})
        self.assertEqual(self.client.delete('/projects/'+copied.name).status_code,200)
        self.assertTrue(source.exists())

    def test_quote_verification_and_conservative_exclusion(self):
        verified=verify_field_evidence({'participants':{'quote':'42 adults','page':99}},[{'name':'participants'}],'[PDF page 2]\nWe enrolled 42 adults.')
        self.assertEqual(verified['participants']['page'],2)
        prompt=evidence_prompt({'eligibility':{'exclusion':['Animal-only study']}})
        include,rationale=parse_screening_response(json.dumps({'include':False,'reason':'not human','criterion':'Animal-only study','quote':'invented'}),{'abstract':'42 adults'},prompt)
        self.assertTrue(include);self.assertTrue(rationale['uncertain'])
        self.assertEqual(rationale['quote'],'')
