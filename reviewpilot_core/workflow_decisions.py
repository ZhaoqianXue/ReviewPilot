"""Explicit category approval, skipping and completion, bound to local artifacts."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from .atomic_files import atomic_write_json
from .project_store import read_json, read_jsonl
from .workflow_state import load_workflow_state
from .extraction_schema import load_schema_draft
from .record_review import ReviewConflict

FILE = 'review/workflow_decisions.json'
SOURCE_FILES = ('search_conditions.json', 'prompts/relevance_prompt.json', 'filtered/included_papers.jsonl',
                'filtered/excluded_papers.jsonl', 'pdfs/download_report.json', 'extraction/extraction_schema.json',
                'extraction/extraction_schema_draft.json', 'extraction/extraction_results.jsonl')
CATEGORY_FILES = ('categorization/categorization_mapping.json', 'categorization/categorized_results.jsonl')


def fingerprint(project, files, stages):
    h = hashlib.sha256()
    for name in files:
        p = project / name
        h.update(name.encode()); h.update(p.read_bytes() if p.is_file() else b'')
    ledger = load_workflow_state(project)['stages']
    h.update(json.dumps({k: ledger[k] for k in stages}, sort_keys=True).encode())
    return h.hexdigest()


def source_revision(project):
    return fingerprint(project, SOURCE_FILES, ('collection', 'screening', 'retrieval', 'extraction'))


def result_revision(project):
    return fingerprint(project, (*SOURCE_FILES, *CATEGORY_FILES), tuple(load_workflow_state(project)['stages']))


def revision(project):
    return hashlib.sha256((result_revision(project) + json.dumps(read_json(project / FILE, {}), sort_keys=True)).encode()).hexdigest()


def projection(project):
    saved = read_json(project / FILE, {})
    valid = saved.get('source_revision') == source_revision(project)
    applied = saved.get('kind') == 'confirmed' and not load_workflow_state(project)['stages']['categorization']['stale'] and (project / CATEGORY_FILES[0]).exists()
    return {'revision': revision(project), 'confirmed': valid and saved.get('kind') == 'confirmed',
            'skipped': valid and saved.get('kind') == 'skipped',
            'finalized': valid and saved.get('final_revision') == result_revision(project),
            'selection': saved.get('selection', {}) if valid else {},
            'editing': valid and (saved.get('kind') == 'draft' or saved.get('kind') == 'confirmed' and not applied), 'outdated': bool(saved) and not valid}


def save(project: Path, payload):
    if payload.get('revision') != revision(project):
        raise ReviewConflict('Project decisions or results changed. Refresh before confirming.')
    stages = load_workflow_state(project)['stages']
    if any(stages[k]['stale'] or stages[k]['status'] not in {'completed', 'partial'} for k in ('collection','screening','retrieval','extraction')):
        raise ReviewConflict('Complete the current extraction workflow before confirming project decisions.')
    saved = read_json(project / FILE, {})
    current = projection(project)
    op = payload.get('operation')
    if op == 'finalize':
        cat = stages['categorization']
        if not current['skipped'] and (cat['stale'] or cat['status'] not in {'completed','partial'} or not (project / CATEGORY_FILES[0]).exists()):
            raise ValueError('Apply categorization or explicitly skip it before finalizing.')
        saved.update(source_revision=source_revision(project), final_revision=result_revision(project))
    elif op in {'confirm', 'skip', 'reopen'}:
        selection = payload.get('selection') or {}
        if not isinstance(selection, dict):
            raise ValueError('Category selection must be a JSON object.')
        if op == 'confirm':
            fields = {f['name'] for f in load_schema_draft(project).get('fields', [])}
            categories = selection.get('categories')
            if selection.get('field') not in fields or selection.get('mode') not in {'single', 'multiple'}:
                raise ValueError('Choose a valid schema field and categorization mode.')
            if not isinstance(categories, list) or not categories or any(not isinstance(v, str) or not v.strip() for v in categories) or len(set(categories)) != len(categories):
                raise ValueError('Provide distinct, nonempty category labels.')
        saved = {'kind': {'confirm':'confirmed','skip':'skipped','reopen':'draft'}[op],
                 'selection': selection if op == 'confirm' else {}, 'source_revision': source_revision(project)}
    else:
        raise ValueError('Unknown project decision.')
    saved['updated_at'] = datetime.now(timezone.utc).isoformat()
    from .record_review import publish, serialized
    writes = {FILE: serialized(saved)}
    if op in {'confirm', 'skip', 'reopen'} and (project / CATEGORY_FILES[0]).exists():
        ledger = load_workflow_state(project)
        ledger['stages']['categorization']['stale'] = True
        writes['workflow_state.json'] = serialized(ledger)
    publish(project, writes)
