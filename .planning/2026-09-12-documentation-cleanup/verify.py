from pathlib import Path
import hashlib, json, os, re, subprocess
from urllib.parse import unquote
root=Path.cwd()
plan=root/'.planning/2026-09-12-documentation-cleanup'
baseline=json.loads((plan/'baseline.json').read_text())
translated=['writing/NAACL2027/notes/2026-09-12-implementation-sync/revision-notes.md','docs/internal-testing/prototype-parity-2026-09-12.md','docs/internal-testing/prototype-parity-2026-09-12-fixes.md','writing/NAACL2027/notes/2026-09-12-content-migration/migration-map.md','writing/NAACL2027/notes/2026-09-12-section-structure-reference.md']
removed=['task_plan.md','findings.md','progress.md']
skip={'.git','.venv','venv','.venv-native','node_modules','.worktrees','.cache','__pycache__','tmp','output','.pytest_cache','toolenv','site-packages'}
scanned=[]; hits=[]
for directory,dirs,files in os.walk(root):
 dirs[:]=[d for d in dirs if d not in skip]
 for name in files:
  p=Path(directory)/name
  if p.suffix.lower() not in {'.md','.markdown','.rst','.adoc','.tex','.txt','.html'} or name in {'secrets.txt','requirements.txt','requirements-dev.txt'}: continue
  if p.is_symlink(): continue
  relative=str(p.relative_to(root)); scanned.append(relative)
  if re.search(r'[\u3400-\u9fff\U00020000-\U000323af]',p.read_text(errors='replace')): hits.append(relative)
changes=[p for p,h in baseline['protected_files'].items() if not (root/p).is_file() or hashlib.sha256((root/p).read_bytes()).hexdigest()!=h]
expected=[p for p in translated if p.startswith('writing/') and p in baseline['protected_files']]
assert all(path in changes for path in expected)
concurrent_changes=[path for path in changes if path not in expected]
assert all(path.startswith("writing/NAACL2027/") for path in concurrent_changes),concurrent_changes
broken=[]
for name in translated+['docs/HISTORY.md']:
 p=root/name
 for href in re.findall(r'\]\(([^)]+)\)',p.read_text()):
  href=href.strip('<>').split('#')[0]
  if not href or re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:',href): continue
  if not (p.parent/unquote(href)).exists(): broken.append({'file':name,'target':href})
audit=(root/'docs/internal-testing/prototype-parity-2026-09-12.md').read_text()
assert len(re.findall(r'^\| F\d\d \|',audit,re.M))==52
assert len(re.findall(r'^\| G\d\d \|',audit,re.M))==13
assert len(re.findall(r'^- \*\*A\d\d ',audit,re.M))==8
assert len(re.findall(r'^\| G\d\d \|',(root/'docs/internal-testing/prototype-parity-2026-09-12-fixes.md').read_text(),re.M))==9
pdfs=[]
for p in (root/'writing').rglob('*.pdf'):
 result=subprocess.run(['pdftotext',str(p),'-'],capture_output=True,text=True)
 pdfs.append({'file':str(p.relative_to(root)),'text_extraction_ok':result.returncode==0,'han_characters':len(re.findall(r'[\u3400-\u9fff]',result.stdout))})
verification={'translated':translated,'deleted_obsolete_logs':removed,'scanned_text_document_count':len(scanned),'remaining_han_documents':hits,'protected_file_count':len(baseline['protected_files']),'protected_files_changed':changes,'other_changes_observed_during_cleanup':concurrent_changes,'broken_links_in_updated_documents':broken,'writing_pdf_text_checks':pdfs,'scope_exclusions':sorted(skip),'note':'Dependencies, caches, worktree mirrors, runtime project data and downloaded source papers are not project-authored documentation. PDF checks inspect extractable text, not image-only content.'}
(plan/'verification.json').write_text(json.dumps(verification,indent=2)+'\n')
print(json.dumps(verification,indent=2))
assert not hits and not broken
assert all(not (root/p).exists() for p in removed)
assert all(p['text_extraction_ok'] and not p['han_characters'] for p in pdfs)
