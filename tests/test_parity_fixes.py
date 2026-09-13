"""Behavior regressions for G01–G09; all files and model responses are isolated."""
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch
from starlette.testclient import TestClient
import web_app
from agents.filtering_agent import FilteringAgent
from agents.extraction_agent import ExtractionAgent
from reviewpilot_core import workflow_decisions as decisions, record_review
from reviewpilot_core.publication_dates import assess, resolve_range
from reviewpilot_core.field_values import validate_value
from reviewpilot_core.state_projection import _full_results, _retrieval_summary, export_artifact_path
from reviewpilot_core.atomic_files import atomic_write_json, atomic_write_jsonl
from reviewpilot_core.project_store import read_json, read_jsonl
from reviewpilot_core.workflow_state import load_workflow_state, save_workflow_state
from reviewpilot_core.task_runner import TaskRunner
from tests.test_record_review import make_project

ROOT = Path(__file__).resolve().parents[1]


class ParityFixTests(unittest.TestCase):
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

    def decision(self, operation, **extra):
        return self.client.patch('/projects/study/review/decisions', json={'operation':operation,'revision':decisions.revision(self.project),**extra})

    def test_exact_inclusive_dates_and_partial_metadata(self):
        bounds={'start':'2025-09-01','end':'2025-09-30'}
        for raw, excluded, review in [('2025-01-15',True,False),('2025-09-01',False,False),('2025-09-30',False,False),('2025-10-01',True,False),('2025',False,True),('2025-09',False,False),('',False,True),('not a date',False,True),('2024',True,False)]:
            with self.subTest(raw=raw):
                result=assess({'publication_date':raw},bounds)
                self.assertEqual((result['excluded'],result['needs_review']),(excluded,review))
        self.assertTrue(assess({'year':2099},{})['excluded'])
        self.assertEqual(resolve_range({})['end'],date.today().isoformat())
        self.assertEqual(resolve_range({'start':'2020','end':'2021'}),{'start':'2020-01-01','end':'2021-12-31'})
        for bounds in [{'start':'2025-02-30'},{'start':'2025-12-01','end':'2025-01-01'}]:
            with self.assertRaises(ValueError): resolve_range(bounds)

    def test_setup_resolves_blank_end_before_revision_and_rejects_bad_dates(self):
        config=web_app._setup_config({'project_name':'P','description':'Question','date_start':'2025-01-01'})
        self.assertEqual(config['date_range']['end'],date.today().isoformat())
        response=self.client.put('/projects/study/setup',json={'project_name':'P','description':'Question','date_start':'2025-02-31'})
        self.assertEqual(response.status_code,400)

    def test_every_removed_record_has_identity_and_duplicate_representative(self):
        papers=[{'id':'old','title':'Historical study','year':2010},
                {'id':'keep','title':'Clinical alpha treatment evaluation adults','year':2025},
                {'id':'exact','title':'Clinical alpha treatment evaluation adults','year':2025},
                {'id':'similar','title':'Clinical alpha treatment evaluation adults trial','year':2025},
                {'id':'animal','title':'Mice experiment','year':2025},
                {'id':'unknown','title':'Unreported publication date'}]
        atomic_write_jsonl(self.project/'collected/pubmed.jsonl',papers)
        atomic_write_json(self.project/'collected/summary.json',{'platform_stats':{'pubmed':len(papers)}})
        agent=FilteringAgent(self.project)
        def relevance(rows,*_):
            return [r for r in rows if r['id']!='animal'],[r for r in rows if r['id']=='animal']
        with patch.object(agent,'_check_relevance',side_effect=relevance),contextlib.redirect_stdout(io.StringIO()):
            agent.run({'auto_approve':True,'date_range':{'start_date':'2020-01-01'},'relevance_prompt':{'system_prompt':'fixture'}})
        included=read_jsonl(self.project/'filtered/included_papers.jsonl')
        excluded=read_jsonl(self.project/'filtered/excluded_papers.jsonl')
        removed=read_jsonl(self.project/'filtered/removed_records.jsonl')
        self.assertEqual(len(included)+len(excluded)+len(removed),len(papers))
        self.assertEqual({r['removal']['kind'] for r in removed},{'date','exact_duplicate','similar_duplicate'})
        self.assertEqual(len({r['collection_record_id'] for r in included+excluded+removed}),len(papers))
        for row in removed:
            self.assertTrue(row['removal']['reason'])
            if row['removal']['kind']!='date':self.assertEqual(row['removal']['representative']['id'],'keep')
        self.assertTrue(next(r for r in record_review.screening_rows(self.project) if r['raw']['id']=='unknown')['uncertain'])
        self.assertIsNotNone(export_artifact_path(self.project,'removed-records'))

    def test_confirm_categories_survives_refresh_and_old_window_cannot_overwrite(self):
        old=decisions.revision(self.project)
        choice={'field':'participants','mode':'single','categories':['Small','Large']}
        response=self.decision('confirm',selection=choice)
        self.assertEqual(response.status_code,200,response.text)
        for client in [self.client, TestClient(web_app.create_app())]:
            state=client.get('/projects/study/state').json()
            self.assertTrue(state['categorizationWorkflow']['decisions']['confirmed'])
            self.assertEqual(state['categorizationWorkflow']['suggestedCategories'],choice['categories'])
        response=self.client.patch('/projects/study/review/decisions',json={'operation':'skip','revision':old})
        self.assertEqual(response.status_code,409)

    def test_skip_finalize_restore_invalidate_and_export(self):
        self.assertEqual(self.decision('skip').status_code,200)
        response=self.decision('finalize')
        self.assertEqual(response.status_code,200,response.text)
        state=TestClient(web_app.create_app()).get('/projects/study/state').json()
        self.assertEqual(state['project']['status'],'Complete')
        self.assertTrue(state['categorizationWorkflow']['decisions']['skipped'])
        self.assertTrue(state['categorizationWorkflow']['decisions']['finalized'])
        self.assertEqual(self.client.get('/projects/study/exports/workflow-decisions').status_code,200)
        row=record_review.projection(self.project)['extraction'][0]
        record_review.save_field(self.project,{'key':row['key'],'field':'participants','value':42,'reason':'Checked source','revision':record_review.revision(self.project)})
        state=self.client.get('/projects/study/state').json()
        self.assertNotEqual(state['project']['status'],'Complete')
        self.assertFalse(state['categorizationWorkflow']['decisions']['finalized'])
        self.assertEqual(self.client.get('/projects/study/exports/workflow-decisions').status_code,404)

    def test_reopen_invalidates_completion_without_changing_extraction(self):
        self.decision('skip');self.decision('finalize')
        before=(self.project/'extraction/extraction_results.jsonl').read_bytes()
        self.assertEqual(self.decision('reopen').status_code,200)
        state=self.client.get('/projects/study/state').json()
        self.assertTrue(state['categorizationWorkflow']['decisions']['editing'])
        self.assertFalse(state['categorizationWorkflow']['decisions']['skipped'])
        self.assertFalse(state['categorizationWorkflow']['decisions']['finalized'])
        self.assertEqual(before,(self.project/'extraction/extraction_results.jsonl').read_bytes())

    def test_cannot_finalize_unapplied_categories_or_failed_extraction(self):
        self.assertEqual(self.decision('finalize').status_code,400)
        ledger=load_workflow_state(self.project);ledger['stages']['extraction']['status']='failed';save_workflow_state(self.project,ledger)
        self.assertEqual(self.decision('skip').status_code,409)
        self.assertFalse(self.client.get('/projects/study/state').json()['reviewWorkbench']['canReviewExtraction'])

    def test_all_three_examples_refuse_decision_writes(self):
        for name in ['quick-start-biomedical-showcase','quick-start-hci-showcase','quick-start-urban-showcase']:
            p=make_project(self.root,name)
            response=self.client.patch(f'/projects/{name}/review/decisions',json={'operation':'skip','revision':decisions.revision(p)})
            self.assertEqual(response.status_code,403)
            self.assertFalse((p/decisions.FILE).exists())

    def test_complete_results_at_every_requested_boundary(self):
        for count in [0,1,2,50,51,70]:
            rows=[{'title':f'Paper {i}','participants':i} for i in range(count)]
            table=_full_results(rows,['participants'],'')
            self.assertEqual(table['total'],count)
            self.assertEqual([r['title'] for r in table['rows']],[r['title'] for r in rows])

    def test_access_provenance_is_explicit_or_unknown(self):
        for report, expected in [({'success':3},(0,0,3)),({'success':3,'downloaded':3},(0,0,3)),({'success':3,'downloaded':[{'access_method':'open_access'},{'access_method':'institution'},{'path':'local.pdf'}]},(1,1,1))]:
            result=_retrieval_summary(self.project,[{}, {}, {}],report)
            self.assertEqual((result['openAccess'],result['viaInstitution'],result['unknownAccess']),expected)

    def test_model_and_manual_values_follow_identical_contracts(self):
        cases=[('integer',42,'42'),('Number',1.5,True),('Text','result',3),('Long text','result',{}),('Text (list)',['a'],[1]),('list',['a'],{}),('bool',False,'false'),('Select','A','B')]
        agent=ExtractionAgent(self.project)
        for kind,valid,invalid in cases:
            field={'name':'participants','type':kind, **({'enum':['A','C']} if kind=='Select' else {})}
            atomic_write_json(self.project/'extraction/extraction_schema.json',{'fields':[field]})
            for value,accepted in [(valid,True),(invalid,False),(None,True),('',True)]:
                with self.subTest(kind=kind,value=value):
                    prompt={'schema':{'fields':[field]}}
                    payload={'key':record_review.key({'id':'p1'}),'field':'participants','value':value,'reason':'Reviewed','revision':record_review.revision(self.project)}
                    if accepted:
                        agent._validate_schema_data({'participants':value},prompt,set())
                        record_review.save_field(self.project,payload)
                    else:
                        with self.assertRaises(ValueError):agent._validate_schema_data({'participants':value},prompt,set())
                        with self.assertRaises(ValueError):record_review.save_field(self.project,payload)
        with self.assertRaises(ValueError):validate_value({'type':'list','items':{'type':'integer'}},['bad'])
        with self.assertRaises(ValueError):validate_value({'type':'Number'},float('nan'))

    def test_native_date_bounds_reach_source_requests(self):
        from searchers.pubmed import PubMedSearcher
        from searchers.openalex import OpenAlexSearcher
        from searchers.arxiv_search import ArxivSearcher
        bounds={'start':'2025-09-01','end':'2025-09-30'}
        response=Mock();response.json.return_value={'esearchresult':{'idlist':[]},'results':[]};response.text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        with patch('requests.get',return_value=response) as get,contextlib.redirect_stdout(io.StringIO()):
            PubMedSearcher(email="fixture@example.org").search('clinical',max_results=5,date_range=bounds)
            self.assertEqual(get.call_args.kwargs['params']['mindate'],'2025/09/01')
            OpenAlexSearcher().search('clinical',max_results=5,date_range=bounds)
            self.assertIn('from_publication_date:2025-09-01',get.call_args.kwargs['params']['filter'])
            ArxivSearcher().search('clinical',max_results=5,date_range=bounds)
            self.assertIn('submittedDate:[202509010000 TO 202509302359]',get.call_args.kwargs['params']['search_query'])

    def test_keyword_edit_changes_payload_and_preserves_boolean_groups(self):
        script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const src=fs.readFileSync('frontend/app.js','utf8');
const ctx={state:{setupDraft:{project_name:'Test',description:'Clinical',platforms:['pubmed'],max_results:5}},D:{project:{title:'Test'}},esc:v=>v,unescapePayloadValue:v=>v||'',selectedSourceLimits:()=>({pubmed:5}),maxResultsFromSourceLimits:()=>5};vm.createContext(ctx);
vm.runInContext(src.slice(src.indexOf('  function queryClauses('),src.indexOf('  function setupDraftFromData(')),ctx);
vm.runInContext(src.slice(src.indexOf('  function setupPayloadFromDraft('),src.indexOf('  function platformKey(')),ctx);
const base='(clinical OR trial) AND (AI OR "machine learning")';
const clauses=ctx.queryClauses(base);assert.equal(clauses.length,2);
ctx.editQueryClauses([...clauses,'"auditmarker"']);const payload=ctx.setupPayloadFromDraft();
assert.match(payload.search_terms,/auditmarker/);assert.match(payload.search_terms,/clinical OR trial/);assert.match(payload.search_terms,/AI OR "machine learning"/);assert.equal(payload.derive_search_terms,false);
const restored=ctx.queryClauses(payload.search_terms);assert.equal(restored.length,3);
ctx.editQueryClauses(restored.filter(c=>!c.includes('auditmarker')));assert.doesNotMatch(ctx.setupPayloadFromDraft().search_terms,/auditmarker/);
assert.equal(ctx.queryClauses('A OR B AND C').length,1);assert.equal(ctx.queryClauses('"A AND B" AND C').length,2);
const previous=ctx.setupPayloadFromDraft().search_terms;ctx.editQueryClauses([]);assert.equal(ctx.setupPayloadFromDraft().search_terms,previous);
process.stdout.write(JSON.stringify(payload));
'''
        result=subprocess.run(['node','-e',script],cwd=ROOT,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        payload=json.loads(result.stdout)
        payload['date_end']='2026-09-12'
        # The backend stores the exact reviewed Boolean expression.
        self.assertEqual(web_app._setup_config(payload)['search_terms'],payload['search_terms'])

    def test_finalized_extraction_action_state_matrix(self):
        script=r'''const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const src=fs.readFileSync('frontend/app.js','utf8');
const ctx={state:{actionPending:''},D:{stageState:{retrieval:{status:'completed'}}},buttonStyle:'',extractionSchemaAction:()=> 'regenerate-schema',fieldsTable:()=>'',previewTable:()=>'',reviewUI:{extraction:()=>''}};vm.createContext(ctx);
vm.runInContext(src.slice(src.indexOf('  function extractionCanvas('),src.indexOf('  function fieldsTable(')),ctx);
for(const status of ['completed','failed','partial','ready']) {
 ctx.D.stageState.extraction={status};
 let html=ctx.extractionCanvas({schemaWorkbench:{status:'finalized'},isFieldsTab:true,allFields:[]});assert.match(html,/data-action="run-extraction"/);
 if(['failed','partial'].includes(status))assert.match(html,/Retry extraction with this schema/);
 ctx.D.stageState.extraction.stale=true;html=ctx.extractionCanvas({schemaWorkbench:{status:'finalized'},allFields:[]});assert.match(html,/Rerun extraction with this schema/);
}
ctx.state.actionPending='run-extraction';assert.match(ctx.extractionCanvas({schemaWorkbench:{status:'finalized'},allFields:[]}),/data-action="run-extraction" disabled/);
for (const status of ['draft','missing'])assert.doesNotMatch(ctx.extractionCanvas({schemaWorkbench:{status},allFields:[]}),/data-action="run-extraction"/);
'''
        result=subprocess.run(['node','-e',script],cwd=ROOT,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_nested_query_logic_survives_every_source_adapter(self):
        from searchers.pubmed import PubMedSearcher
        from searchers.arxiv_search import ArxivSearcher
        from searchers.openalex import OpenAlexSearcher
        query='(clinical OR trial) AND ((AI AND "data AND evidence") OR learning)'
        self.assertEqual(PubMedSearcher(email='test@example.org')._add_field_restrictions(query),
                         '((clinical[Title/Abstract] OR trial[Title/Abstract]) AND ((AI[Title/Abstract] AND "data AND evidence"[Title/Abstract]) OR learning[Title/Abstract]))')
        arxiv=ArxivSearcher()
        self.assertEqual(arxiv._format_query(query), '((all:clinical OR all:trial) AND ((all:AI AND all:"data AND evidence") OR all:learning))')
        self.assertIsNone(arxiv._parse_query(query))  # cannot flatten into AND-of-OR fallback
        self.assertEqual(OpenAlexSearcher()._build_query_param_sets(query),[{'search':'((clinical OR trial) AND ((AI AND "data AND evidence") OR learning))'}])
        self.assertEqual(arxiv._format_query('AI AND NOT animal'),'(all:AI ANDNOT all:animal)')
        with self.assertRaises(ValueError):PubMedSearcher(email='test@example.org')._add_field_restrictions('(AI OR learning')

    def test_saved_query_and_dates_are_consumed_by_collection_worker(self):
        from reviewpilot_core.sub_agent_contracts import CollectionAgentContract
        query='(clinical OR trial) AND (AI OR "machine learning") AND auditmarker'
        payload={'project_name':'study','description':'Clinical AI','search_terms':query,'platforms':['pubmed','arxiv','openalex'],'date_start':'2025-09-01','date_end':'','derive_search_terms':False}
        preview=self.client.put('/projects/study/setup',json=payload).json()
        payload['confirmation']={'expected_revision':preview['expectedRevision'],'affected_stages':preview['affectedStages']}
        with contextlib.redirect_stdout(io.StringIO()):
            response=self.client.put('/projects/study/setup',json=payload)
        self.assertEqual(response.status_code,200,response.text)
        saved=read_json(self.project/'search_conditions.json',{})
        self.assertEqual(saved['search_terms'],query)
        worker=Mock();worker.last_errors={}
        worker.search.side_effect=lambda **kw:{source:[] for source in kw['platforms']}
        with patch('main.AcademicSearcher',return_value=worker),patch.dict('os.environ',{'REVIEWPILOT_OFFLINE_ACTIONS':'0'}),contextlib.redirect_stdout(io.StringIO()):
            CollectionAgentContract().run(self.root,'study')
        self.assertEqual(worker.search.call_count,3)
        for call in worker.search.call_args_list:
            self.assertEqual(call.kwargs['query'],query)
            self.assertEqual(call.kwargs['date_range'],{'start':'2025-09-01','end':date.today().isoformat()})
            if call.kwargs['platforms']==['arxiv']:self.assertEqual(call.kwargs['arxiv_query'],query)
