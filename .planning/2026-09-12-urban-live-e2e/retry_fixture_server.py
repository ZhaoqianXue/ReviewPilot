"""Isolated browser recovery probe using real records/PDFs and injected failures."""
from pathlib import Path
import sys,json,shutil,time,hashlib
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import web_app
from reviewpilot_core.atomic_files import atomic_write_json,atomic_write_jsonl
from reviewpilot_core.workflow_state import initialize_workflow_state,start_action,complete_action
from reviewpilot_core.task_runner import TaskRunner
from tests.test_web_retry import UnsuccessfulRetryAdapter
base=Path(__file__).parent/'retry-fixture'
project=base/'urban-recovery'
project.mkdir(exist_ok=True)
source=ROOT/'output/review-project'
rows=[json.loads(line) for line in (source/'filtered/included_papers.jsonl').read_text().splitlines()]
original_sources=[Path(row['pdf_path']) for row in rows]
(project/'pdfs').mkdir(exist_ok=True)
kept=project/'pdfs/original.pdf'
shutil.copyfile(original_sources[0],kept)
rows[0]['pdf_path']=str(kept);rows[0]['pdf_downloaded']=True
for key in list(rows[1]):
    if key.startswith(('pdf_','retrieval_')):rows[1].pop(key)
rows[1].update(pdf_downloaded=False,retrieval_status='unavailable',pdf_failure_class='network')
config=json.loads((source/'search_conditions.json').read_text());config.update(project_name='Urban Failed-Download Recovery Test',project_path=str(project))
atomic_write_json(project/'search_conditions.json',config)
atomic_write_jsonl(project/'filtered/included_papers.jsonl',rows)
atomic_write_json(project/'pdfs/download_report.json',{'success':1,'failed':1,'downloaded':[{'id':rows[0]['id'],'title':rows[0]['title'],'path':str(kept)}],'failed_papers':[{'id':rows[1]['id'],'title':rows[1]['title'],'failure_class':'network'}]})
initialize_workflow_state(project)
for action,result in [('collect',{'total':2,'platform_stats':{'openalex':2},'platform_errors':{}}),('screen',{}),('download-pdfs',{'success':1,'failed':1})]:
 start_action(project,action);complete_action(project,action,result)
base_digest=hashlib.sha256(kept.read_bytes()).hexdigest()
class Adapter:
 attempts=0
 def run(self,action,output_root,project_id,**kwargs):
  Adapter.attempts+=1
  time.sleep(4)
  if Adapter.attempts==1:return UnsuccessfulRetryAdapter().run(action,output_root,project_id,**kwargs)
  staged=Path(output_root)/project_id
  batch=[json.loads(line) for line in (staged/'filtered/included_papers.jsonl').read_text().splitlines()]
  assert len(batch)==1 and batch[0]['id']==rows[1]['id']
  pdf=staged/'pdfs/recovered.pdf';shutil.copyfile(original_sources[1],pdf)
  batch[0].update(pdf_downloaded=True,pdf_path=str(pdf),retrieval_status='downloaded')
  atomic_write_jsonl(staged/'filtered/included_papers.jsonl',batch)
  atomic_write_json(staged/'pdfs/download_report.json',{'success':1,'failed':0,'downloaded':[{'id':batch[0]['id'],'title':batch[0]['title'],'path':str(pdf)}],'failed_papers':[],'pdf_count':1,'attempted':1})
  assert hashlib.sha256(kept.read_bytes()).hexdigest()==base_digest
  return {'success':1,'failed':0,'stats':{'success':1,'failed':0}}
web_app.OUTPUT_ROOT=base
web_app.task_runner=TaskRunner()
web_app.WorkflowActionAdapter=Adapter
if __name__=='__main__':
 import uvicorn
 uvicorn.run(web_app.create_app(),host='127.0.0.1',port=5604)
