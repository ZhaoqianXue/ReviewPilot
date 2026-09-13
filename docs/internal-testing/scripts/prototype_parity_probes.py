"""Run from any directory with ReviewPilot's Python environment.

Writes a new post-fix verification JSON, preserving the original audit evidence; exits 1 on regressions.
No model or network calls. All project writes use TemporaryDirectory.
"""
import sys, json, tempfile, contextlib, io, subprocess
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from agents.filtering_agent import FilteringAgent
from agents.extraction_agent import ExtractionAgent
from reviewpilot_core.state_projection import _full_results,_retrieval_summary
from reviewpilot_core.atomic_files import atomic_write_jsonl, atomic_write_json
from reviewpilot_core.project_store import read_json,read_jsonl
results=[]
def result(id,expected,observed,passed):results.append(dict(id=id,expected=expected,observed=observed,passed=passed))
with tempfile.TemporaryDirectory(prefix='parity-probes-') as tmp:
 p=Path(tmp)
 agent=FilteringAgent(p)
 papers=[{'id':'early','year':2025,'publication_date':'2025-01-15'},{'id':'within','year':2025,'publication_date':'2025-10-01'},{'id':'unknown','year':None}]
 observed=[x['id'] for x in agent._filter_by_date(papers,{'start_date':'2025-09-01','end_date':'2025-12-31'})]
 result('G02-date-precision','Exclude early; flag unknown for review',observed,'early' not in observed and 'unknown' in observed)
 future=[x['id'] for x in agent._filter_by_date([{'id':'future','year':2099}],{'start_date':'2020-01-01','end_date':''})]
 result('G02-open-end','Blank end must enforce current day',future,not future)
 rows=[{'title':f'paper-{i}','participants':i} for i in range(65)]
 observed=_full_results(rows,['participants'],'')
 result('G07-full-results','All 65 rows or explicit pagination metadata',{'rows':len(observed['rows']),'keys':list(observed)},len(observed['rows'])==65 or 'total' in observed)
 observed=_retrieval_summary(p,[{},{}],{'success':2,'failed':0})
 result('G08-access-provenance','Unknown provenance must not be asserted as open access',observed,observed['openAccess']==0)
 try:
  obs=ExtractionAgent(p)._validate_schema_data({'participants':'not a number'},{'schema':{'fields':[{'name':'participants','type':'integer','required':True}]}},set())
  rejected=False
 except ValueError as error:
  obs={'rejected':str(error)}
  rejected=True
 result('G09-schema-types','Reject nonnumeric value for integer field',obs,rejected)
 atomic_write_jsonl(p/'collected/pubmed.jsonl',[{'id':'a','title':'Clinical trial alpha','abstract':'adult trial','year':2025},{'id':'dup','title':'Clinical trial alpha','abstract':'same trial','year':2025},{'id':'old','title':'Historical report','year':2010},{'id':'animal','title':'Mice experiment beta','abstract':'Only mice were studied.','year':2025}])
 def llm(**kwargs):
  excluded='Mice experiment' in kwargs['text_prompt']
  return json.dumps({'include':not excluded,'reason':'Only mice' if excluded else 'Eligible trial','criterion':'Animal-only study' if excluded else 'Clinical study','quote':'Only mice were studied.' if excluded else 'adult trial','uncertain':False}),{}
 atomic_write_json(p/'collected/summary.json',{'platform_stats':{'pubmed':4}})
 with patch('agents.filtering_agent.SKLEARN_AVAILABLE',False),contextlib.redirect_stdout(io.StringIO()):
  FilteringAgent(p,llm_query=llm).run({'auto_approve':True,'date_range':{'start_date':'2020-01-01'},'relevance_prompt':{'criteria_finalized':True,'eligibility':{'inclusion':['Clinical study'],'exclusion':['Animal-only study']}}})
 stats=read_json(p/'filtered/filtering_stats.json',{})
 assert stats['initial_count']==4, 'Invalid audit fixture: no records loaded'
 result('G03-all-removals','Every removed record has a inspectable reason and identity',{'removed_counts':stats['removed'],'excluded_ids':[r['id'] for r in read_jsonl(p/'filtered/excluded_papers.jsonl')], 'removed_ledger':read_jsonl(p/'filtered/removed_records.jsonl')},len(read_jsonl(p/'filtered/excluded_papers.jsonl'))+len(read_jsonl(p/'filtered/removed_records.jsonl'))==sum(stats['removed'].values()))
js=r'''const fs=require('fs'),vm=require('vm');let source=fs.readFileSync('frontend/app.js','utf8');let fn=source.slice(source.indexOf('  function setupPayloadFromDraft('),source.indexOf('  function platformKey('));const ctx={state:{setupDraft:{project_name:'test',description:'test',search_terms:'clinical AND AI',keywords:['clinical','AI','auditmarker'],platforms:['pubmed'],max_results:'10',derive_search_terms:false}},D:{project:{title:'test'},researchQuestion:'test'},selectedSourceLimits:()=>({pubmed:10}),esc:v=>v,unescapePayloadValue:v=>v,maxResultsFromSourceLimits:()=>10};vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('  function queryClauses('),source.indexOf('  function setupDraftFromData(')),ctx);ctx.editQueryClauses([...ctx.queryClauses('clinical AND AI'),'auditmarker']);vm.runInContext(fn,ctx);process.stdout.write(JSON.stringify(ctx.setupPayloadFromDraft()));'''
payload=json.loads(subprocess.check_output(['node','-e',js],text=True,cwd=ROOT))
result('G01-keyword-save','Changed keyword must be transmitted or update effective query',{'query':payload['search_terms'],'keyword_field':payload.get('keywords'),'concept_field':payload.get('concept_blocks')},'auditmarker' in json.dumps(payload))
output=ROOT/'docs/internal-testing/prototype-parity-2026-09-12-fix-verification.json'
output.write_text(json.dumps({'scope':'deterministic local probes, no network/model calls; false means a confirmed gap, not an ordinary passing regression suite','results':results},ensure_ascii=False,indent=2)+'\n')
print(json.dumps(results,ensure_ascii=False,indent=2))

sys.exit(1 if any(not item["passed"] for item in results) else 0)
