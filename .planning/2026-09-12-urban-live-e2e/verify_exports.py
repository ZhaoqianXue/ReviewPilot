import ast, hashlib, json, re, urllib.request
from pathlib import Path
root=Path.cwd()
out=root/'docs/internal-testing/evidence/urban-live-e2e'
out.mkdir(parents=True,exist_ok=True)
base='http://127.0.0.1:5602'
state=json.load(urllib.request.urlopen(base+'/projects/review-project/state'))
module=ast.parse((root/'reviewpilot_core/state_projection.py').read_text())
node=next(n.value for n in module.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='EXPORT_ARTIFACTS' for t in n.targets))
paths={ast.literal_eval(k):ast.literal_eval(v.elts[1].args[0]) for k,v in zip(node.keys,node.values)}
manifest=[]; rows={}
for item in state['exportPackage']:
 response=urllib.request.urlopen(base+item['downloadUrl'])
 data=response.read(); key=item['key']; filename=Path(paths[key]).name
 (out/filename).write_bytes(data)
 if filename.endswith('.jsonl'):
  parsed=[json.loads(line) for line in data.decode().splitlines() if line.strip()]
  rows[key]=len(parsed)
 else: parsed=json.loads(data)
 source=root/'output/review-project'/paths[key]
 equal=(parsed['saved']==json.loads(source.read_text())) if key=='workflow-decisions' else data==source.read_bytes()
 assert equal, key
 manifest.append(dict(key=key,filename=filename,http_status=response.status,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),source_matches=True))
assert rows['included-papers']==rows['extraction-results']==rows['categorized-results']==1
assert rows['excluded-papers']==4 and rows['removed-records']==0
baseline=json.loads((root/'.planning/2026-09-12-urban-live-e2e/baseline-output-sha256.json').read_text())
changed=[str(p) for p,h in baseline.items() if not (root/p).exists() or hashlib.sha256((root/p).read_bytes()).hexdigest()!=h]
new=[str(p.relative_to(root)) for p in (root/'output').rglob('*') if p.is_file() and str(p.relative_to(root)) not in baseline]
allowed=('output/review-project/','output/applications-large-language-models-urban/')
other_new=[p for p in new if not p.startswith(allowed)]
result=dict(project=state['project'],read_only_example=state['readOnlyExample'],rows=rows,exports=manifest,baseline_files=len(baseline),changed_existing_files=changed,new_files_outside_test_projects=other_new,screening=state['screeningMetrics'],retrieval=state['retrievalSummary'],categorization=state['categorizationSummary'])
(out/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='exports'},indent=2))
assert changed == ['output/pdf/ReviewPilot-NAACL2027.pdf'] and not other_new
# The unrelated manuscript PDF changed during the run; retain and report it.
assert all(not path.startswith(('output/review-project/', 'output/applications-large-language-models-urban/')) for path in changed)
