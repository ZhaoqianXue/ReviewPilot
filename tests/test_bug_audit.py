"""Regression coverage for the follow-up audit; all projects are temporary."""
import json
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient
import web_app
from reviewpilot_core import record_review as review, workflow_decisions as decisions
from reviewpilot_core.atomic_files import atomic_write_json, atomic_write_jsonl
from reviewpilot_core.publication_dates import assess
from reviewpilot_core.field_values import validate_value
from reviewpilot_core.task_runner import TaskRunner
from tests.test_record_review import make_project

ROOT = Path(__file__).resolve().parents[1]


class BugAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = make_project(self.root)
        self.runner = TaskRunner()
        self.addCleanup(self.runner.shutdown)
        self.enterContext(patch.object(web_app, 'OUTPUT_ROOT', self.root))
        self.enterContext(patch.object(web_app, 'task_runner', self.runner))
        self.enterContext(patch('agents.lead_agent.query_llm', return_value=('Fixture reply', {})))
        self.client = TestClient(web_app.create_app(), raise_server_exceptions=False)

    def correct(self, paper_id, decision):
        row = next(r for r in review.screening_rows(self.project) if r['raw']['id'] == paper_id)
        review.save_screening(self.project, {'key': row['key'], 'decision': decision,
            'criterion': 'Clinical study', 'reason': 'Checked eligibility', 'revision': review.revision(self.project)})

    def test_third_screening_edit_cannot_rebind_other_papers_pdf(self):
        self.correct('p1', 'exclude')
        self.correct('p2', 'include')
        self.correct('p1', 'include')
        rows = review.read_jsonl(self.project / 'filtered/included_papers.jsonl')
        p2 = next(r for r in rows if r['id'] == 'p2')
        self.assertIsNone(review.local_pdf(self.project, p2))
        p1 = next(r for r in rows if r['id'] == 'p1')
        self.assertEqual(review.local_pdf(self.project, p1).name, 'row1_paper.pdf')

    def test_reviewed_counts_conserve_the_collection(self):
        for name in ('filtering_stats', 'screening_stats'):
            atomic_write_json(self.project / f'filtered/{name}.json', {'initial_count': 4,
                'removed': {'by_date': 1, 'by_exact_dedup': 1, 'by_similarity': 0, 'by_relevance': 1}})
        for decision in ('include', 'exclude'):
            self.correct('p2', decision)
            for name in ('filtering_stats', 'screening_stats'):
                stats = review.read_json(self.project / f'filtered/{name}.json', {})
                self.assertEqual(stats['removed']['by_relevance'], stats['excluded_count'])
                self.assertEqual(stats['final_count'] + sum(stats['removed'].values()), stats['initial_count'])

    def test_malformed_request_bodies_are_400_without_writes(self):
        before = review.revision(self.project)
        for method, path in [('post','/projects'), ('post','/projects/study/chat'),
                             ('put','/projects/study/setup'), ('put','/memory/settings')]:
            for body in ('[]', '42', '"text"', '{broken', '{"message":"x","value":NaN}', '{"message":"x","max_results":1e999}'):
                with self.subTest(path=path, body=body):
                    result = getattr(self.client, method)(path, content=body, headers={'Content-Type':'application/json'})
                    self.assertEqual(result.status_code, 400, result.text)
        self.assertEqual(review.revision(self.project), before)

    def test_invalid_category_selection_is_400_without_decision(self):
        for selection in (['not an object'], {'field': [], 'mode': 'single', 'categories':['A']}):
            result = self.client.patch('/projects/study/review/decisions', json={
                'operation':'confirm', 'revision':decisions.revision(self.project), 'selection':selection})
            self.assertEqual(result.status_code, 400, result.text)
        self.assertFalse((self.project / decisions.FILE).exists())

    def test_zero_month_or_day_is_uncertain_not_an_exclusion(self):
        for raw in ('2010-00-00', '2010-01-00', '2010-00-25'):
            with self.subTest(raw=raw):
                result = assess({'publication_date':raw}, {'start':'2025-01-01','end':'2025-12-31'})
                self.assertFalse(result['excluded'])
                self.assertTrue(result['needs_review'])

    def test_nested_nonfinite_model_values_rejected(self):
        for value in ([float('nan')], {'nested':[float('inf')]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_value({'name':'outcomes','type':'array' if isinstance(value,list) else 'object'}, value)

    def test_source_ids_are_namespaced_and_stable_across_extraction(self):
        a = {'id':'123','source':'pubmed','title':'Trial A'}
        b = {'id':'123','source':'arxiv','title':'Trial B'}
        self.assertNotEqual(review.key(a), review.key(b))
        self.assertEqual(review.key(a), review.key({'paper_id':'123','source':'pubmed'}))

    def test_ambiguous_live_selection_never_silently_picks_last_record(self):
        rows = review.read_jsonl(self.project / 'filtered/included_papers.jsonl')
        atomic_write_jsonl(self.project / 'filtered/included_papers.jsonl', rows + [{**rows[0], 'title':'Different paper'}])
        with self.assertRaises(ValueError):
            review.run_sample(self.project, {'mode':'screening','keys':[review.key(rows[0])],
                'revision':review.revision(self.project)}, llm_query=lambda **kw: ('{}', {}))

    def test_invalid_manual_query_rejected_before_setup_mutation(self):
        before = review.revision(self.project)
        response = self.client.put('/projects/study/setup', json={
            'project_name':'study', 'description':'Clinical AI', 'search_terms':'AI AND (medicine',
            'derive_search_terms':False})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(review.revision(self.project), before)

    def test_chat_reserves_session_until_reply_is_saved(self):
        from agents.lead_agent import LeadAgentResult
        entered, release = Event(), Event()
        def respond(**kwargs):
            entered.set()
            release.wait(5)
            return LeadAgentResult(stage='collection', status='completed', reply='Saved reply', artifacts=[], next_actions=[])
        with patch.object(web_app.LeadAgent, 'handle_message', side_effect=respond), ThreadPoolExecutor() as pool:
            future = pool.submit(self.client.post, '/projects/study/chat', json={'step':'search','message':'Explain this query'})
            try:
                self.assertTrue(entered.wait(3))
                response = self.client.delete('/projects/study')
                self.assertEqual(response.status_code, 409, response.text)
                self.assertTrue(self.project.exists())
            finally:
                release.set()
                future.result(timeout=5)

    def test_invalid_schema_cannot_replace_confirmed_schema(self):
        from reviewpilot_core.extraction_schema import save_schema_draft
        before = review.revision(self.project)
        for fields in ([{'name':'paper_id'}], [{'name':'field_evidence'}],
                       [{'name':'sample_size'}, {'name':'SampleSize'}],
                       [{'name':'valid'}, 'bad'], [{'name':'valid'}, {}],
                       [{'name':'value','type':'unsupported'}]):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                save_schema_draft(self.project, {'fields':fields})
            self.assertEqual(review.revision(self.project), before)

    def test_model_cannot_replace_pdf_identity_with_extracted_metadata(self):
        from agents.extraction_agent import ExtractionAgent
        paper = review.read_jsonl(self.project / 'filtered/included_papers.jsonl')[0]
        agent = ExtractionAgent(self.project, llm_query=lambda **kw: (json.dumps({'paper_id':'another paper'}), {}), pdf_reader=lambda path:'Source text')
        result = agent.extract_one(paper=paper, row_number=1, pdf_folder=self.project/'pdfs',
            pdf_files=list((self.project/'pdfs').glob('*.pdf')),
            extraction_prompt={'system_prompt':'Extract', 'user_prompt_template':'{paper_text}',
                               'schema':{'fields':[{'name':'paper_id','type':'Text'}]}})
        self.assertEqual(result['paper_id'], 'p1')
        self.assertEqual(result['extraction_status'], 'error')
        legacy = agent.extract_one(paper=paper, row_number=1, pdf_folder=self.project/'pdfs',
            pdf_files=list((self.project/'pdfs').glob('*.pdf')),
            extraction_prompt={'system_prompt':'Extract', 'user_prompt_template':'{paper_text}'})
        self.assertEqual(legacy['paper_id'], 'p1')
        self.assertEqual(legacy['extraction_source'], 'pdf')
        fallback, _ = agent._extract_with_web_search_fallback(paper=paper, row_number=1,
            extraction_prompt={}, web_search_query=lambda **kw: (json.dumps({
                'paper_id':'wrong', 'extraction_source':'pdf', 'source_urls':['https://example.org/paper']}), {}))
        self.assertEqual(fallback['paper_id'], 'p1')
        self.assertEqual(fallback['extraction_source'], 'web_search_fallback')

    def test_preview_never_borrows_another_papers_result_by_position_or_id(self):
        from reviewpilot_core.extraction_preview import _formal_row
        self.assertIsNone(_formal_row(self.project, {'id':'different','title':'Different study'}, 0))
        rows = review.read_jsonl(self.project / 'extraction/extraction_results.jsonl')
        rows[0]['source'] = 'pubmed'
        atomic_write_jsonl(self.project / 'extraction/extraction_results.jsonl', rows)
        self.assertIsNone(_formal_row(self.project, {'id':'p1','source':'arxiv','title':'Different study'}, 0))

    def test_stale_formal_results_and_changed_source_preview_cache_are_hidden(self):
        from reviewpilot_core.extraction_preview import project_preview_projection, write_preview_cache
        from reviewpilot_core.extraction_schema import load_schema_draft
        from reviewpilot_core.workflow_state import load_workflow_state, save_workflow_state
        ledger = load_workflow_state(self.project)
        ledger['stages']['extraction']['stale'] = True
        save_workflow_state(self.project, ledger)
        self.assertEqual(project_preview_projection(self.project, 0)['status'], 'missing')
        write_preview_cache(self.project, load_schema_draft(self.project), 'p1', {'paper_id':'p1','participants':41})
        self.assertEqual(project_preview_projection(self.project, 0)['status'], 'ready')
        papers = review.read_jsonl(self.project / 'filtered/included_papers.jsonl')
        papers[0]['pdf_path'] = 'new-source.pdf'
        atomic_write_jsonl(self.project / 'filtered/included_papers.jsonl', papers)
        self.assertEqual(project_preview_projection(self.project, 0)['status'], 'missing')

    def test_query_draft_and_late_decisions_in_real_frontend_functions(self):
        script = r'''
const fs=require('fs'), assert=require('node:assert/strict');
const src=fs.readFileSync('frontend/app.js','utf8');
const esc=x=>String(x), unescapePayloadValue=x=>String(x);
eval(src.slice(src.indexOf('  function queryClauses('), src.indexOf('  function setupDraftFromData(')));
assert.deepEqual(queryClauses("Alzheimer's AND medicine"), ["Alzheimer's", 'medicine']);
let D={project:{id:'a'},categorizationWorkflow:{decisions:{revision:'r1'}}};
let state={setupDraft:{},actionPending:false,chatPending:false,decisionPending:false};
let activeTaskMonitor={generation:1}, paints=0, updates=0, pending=[];
const {createProjectNavigationOwnership}=require('./frontend/app.js');
const projectNavigation=createProjectNavigationOwnership('a');
const paintWorkspace=()=>paints++, setData=()=>{updates++;activeTaskMonitor.generation++;}, categorizationActionPayload=()=>({});
const fetch=()=>new Promise(resolve=>pending.push(resolve));
eval(src.slice(src.indexOf('  async function saveWorkflowDecision('),src.indexOf('  function categorizationActionPayload(')));
(async()=>{
 const a=saveWorkflowDecision('skip');
 D.project.id='b'; activeTaskMonitor.generation++; projectNavigation.begin('b'); state.decisionPending=false;
 const b=saveWorkflowDecision('skip'); const painted=paints;
 pending[0]({ok:true,json:async()=>({state:{}})}); await a;
 assert.equal(updates,0); assert.equal(state.decisionPending,true); assert.equal(paints,painted);
 pending[1]({ok:true,json:async()=>({state:{}})}); await b;
 assert.equal(updates,1); assert.equal(state.decisionPending,false);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node','-e',script], cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_quoted_keyword_removal_and_restored_draft_match_query(self):
        script = r'''
const fs=require('fs'), assert=require('node:assert/strict');
const src=fs.readFileSync('frontend/app.js','utf8');
const esc=x=>String(x).replaceAll('&','&amp;').replaceAll('"','&quot;');
const unescapePayloadValue=x=>String(x).replaceAll('&quot;','"').replaceAll('&amp;','&');
const state={setupDraft:{keywords:['clinical',esc('"machine learning"')]}};
eval(src.slice(src.indexOf('  function queryClauses('), src.indexOf('  function setupDraftFromData(')));
removeQueryClause('"machine learning"');
assert.deepEqual(state.setupDraft.keywords,['clinical']);
assert.equal(state.setupDraft.search_terms,'(clinical)');
const D={searchReuseDraft:null}, shouldRestoreSnapshotData=false, sameSetupRevision=true;
const ui={setupDraft:{search_terms:'(clinical)',keywords:['obsolete']}};
const setupDraftFromData=()=>({search_terms:'clinical AND AI',keywords:['clinical','AI']});
const normalizeSourceLimits=()=>({});
eval(src.slice(src.indexOf('    const baseDraft = setupDraftFromData(D);'), src.indexOf("    state.keywordDraft = '';",src.indexOf('    const baseDraft = setupDraftFromData(D);'))));
assert.equal(state.setupDraft.search_terms,'(clinical)');
assert.deepEqual(state.setupDraft.keywords,['(clinical)']);
'''
        result = subprocess.run(['node','-e',script], cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
