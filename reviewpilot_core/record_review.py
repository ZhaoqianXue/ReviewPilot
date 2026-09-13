"""Local, revision-bound human review and recoverable artifact publication."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import RLock

from .atomic_files import atomic_write_json, atomic_write_text
from .project_store import read_json, read_jsonl
from .workflow_state import load_workflow_state
from .extraction_schema import load_schema_draft
from .evidence_support import evidence_for_field, normalized_text, local_pdf

_REVIEW_LOCK = RLock()
PENDING = 'review/pending.json'
AUDIT = 'review/changes.json'
ARTIFACTS = ('search_conditions.json', 'prompts/relevance_prompt.json', 'filtered/included_papers.jsonl',
             'filtered/excluded_papers.jsonl', 'filtered/removed_records.jsonl', 'filtered/filtering_stats.json', 'filtered/screening_stats.json',
             'extraction/extraction_results.jsonl', 'extraction/extraction_schema.json',
             'extraction/extraction_schema_draft.json', 'workflow_state.json', AUDIT)
WRITABLE = {*ARTIFACTS, 'filtered/filtered_papers.jsonl', 'review/live_sample.json', 'review/workflow_decisions.json'}


class ReviewConflict(ValueError):
    pass


def revision(project: Path) -> str:
    digest = hashlib.sha256()
    for name in ARTIFACTS:
        digest.update(name.encode())
        path = project / name
        digest.update(path.read_bytes() if path.is_file() else b'')
    return digest.hexdigest()


def key(row: dict) -> str:
    identity = str(row.get('paper_id') or row.get('id') or row.get('doi') or '').strip().casefold()
    if identity in {'', 'unknown'}:
        identity = str(row.get('title') or '').strip().casefold()
    source = str(row.get('source') or '').strip().casefold()
    if source and source != 'unknown':
        identity = json.dumps([source, identity], ensure_ascii=False)
    return hashlib.sha256(identity.encode()).hexdigest()[:24]


def unique_record(rows, record_key):
    matches = [row for row in rows if key(row) == record_key]
    if len(matches) != 1:
        raise ValueError('Select one identifiable paper; the record is missing or ambiguous.')
    return matches[0]


def recover(project: Path) -> None:
    with _REVIEW_LOCK:
        pending = project / PENDING
        if not pending.exists():
            return
        document = read_json(pending, {})
        writes = document.get('writes')
        if not isinstance(writes, dict) or not set(writes).issubset(WRITABLE) or not all(isinstance(v, str) for v in writes.values()):
            raise ReviewConflict('Review update is unreadable; restore the local project backup.')
        for name, content in writes.items():
            target = project / name
            if not target.resolve().is_relative_to(project.resolve()):
                raise ReviewConflict('Review update contains an invalid artifact path.')
            atomic_write_text(target, content)
        pending.unlink()


def publish(project: Path, writes: dict) -> None:
    with _REVIEW_LOCK:
        atomic_write_json(project / PENDING, {'writes': writes})
        recover(project)


def serialized(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def jsonlines(rows) -> str:
    return ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)


def screening_rows(project: Path) -> list[dict]:
    rows = []
    for decision, name in [('include', 'included_papers'), ('exclude', 'excluded_papers')]:
        for row in read_jsonl(project / f'filtered/{name}.jsonl'):
            evidence = row.get('screening_evidence') or {}
            manual = row.get('human_screening') or {}
            rows.append({'key': key(row), 'title': str(row.get('title') or 'Untitled paper'),
                         'abstract': str(row.get('abstract') or ''), 'year': row.get('year'),
                         'decision': decision, 'reason': (manual.get('reason') or evidence.get('reason') or '') + (' ' + row['date_assessment']['reason'] if (row.get('date_assessment') or {}).get('needs_review') else ''),
                         'criterion': manual.get('criterion') or evidence.get('criterion') or '',
                         'quote': evidence.get('quote') or '', 'reviewed': bool(manual),
                         'uncertain': bool((row.get('date_assessment') or {}).get('needs_review')) or not manual and (evidence.get('uncertain') is True or row.get('is_relevant') is None),
                         'origin': 'human' if manual else 'model', 'raw': row})
    return rows


def extraction_rows(project: Path) -> list[dict]:
    schema = load_schema_draft(project)
    result = []
    for row in read_jsonl(project / 'extraction/extraction_results.jsonl'):
        fields = []
        for field in schema.get('fields', []):
            name = field['name']
            value = row.get(name, (row.get('extracted_data') or {}).get(name))
            fields.append({'name': name, 'type': field['type'], 'value': value,
                           'reviewed': name in (row.get('human_fields') or {})})
        result.append({'key': key(row), 'title': row.get('title') or 'Untitled paper',
                       'source': row.get('extraction_source') or 'unknown', 'fields': fields,
                       'status': row.get('extraction_status') or 'success'})
    return result


def projection(project: Path) -> dict:
    recover(project)
    stages = load_workflow_state(project)['stages']
    return {'revision': revision(project), 'screening': [{k: v for k, v in row.items() if k != 'raw'} for row in screening_rows(project)],
            'extraction': extraction_rows(project),
            'removed': read_jsonl(project / 'filtered/removed_records.jsonl'),
            'canReviewScreening': not stages['screening']['stale'] and stages['screening']['status'] in {'completed', 'partial'},
            'canReviewExtraction': not stages['extraction']['stale'] and stages['extraction']['status'] in {'completed', 'partial'},
            'screeningStale': stages['screening']['stale'], 'extractionStale': stages['extraction']['stale'],
            'changes': [{k: entry.get(k) for k in ('kind', 'key', 'field', 'reason', 'at')} for entry in read_json(project / AUDIT, [])[-50:]], 'liveSample': read_json(project / 'review/live_sample.json', None)}


def _check(project: Path, payload: dict, stage: str):
    recover(project)
    if payload.get('revision') != revision(project):
        raise ReviewConflict('Project results changed. Refresh and review the latest version before saving.')
    if any((project / name).exists() for name in ('.setup_update_pending.json', '.retrieval_retry_pending.json')):
        raise ReviewConflict('A project update needs recovery before review can continue.')
    ledger = load_workflow_state(project)
    if ledger['stages'][stage]['stale'] or ledger['stages'][stage]['status'] not in {'completed', 'partial'}:
        raise ReviewConflict('Run the current stage before reviewing its results.')
    return ledger


def _audit(project, entry):
    return [*read_json(project / AUDIT, []), {**entry, 'at': datetime.now(timezone.utc).isoformat()}]


def save_screening(project: Path, payload: dict) -> None:
    ledger = _check(project, payload, 'screening')
    reason = str(payload.get('reason') or '').strip()
    decision = payload.get('decision')
    if decision not in {'include', 'exclude'} or not reason:
        raise ValueError('Choose Include or Exclude and provide a review reason.')
    rows = screening_rows(project)
    matches = [row for row in rows if row['key'] == payload.get('key')]
    if len(matches) != 1:
        raise ValueError('Select one identifiable screened record.')
    target = matches[0]
    old_decision = target['decision']
    criterion = str(payload.get('criterion') or '').strip()
    rules = read_json(project / 'prompts/relevance_prompt.json', {}).get('eligibility') or {}
    available = [str(rule) for values in rules.values() for rule in (values if isinstance(values, list) else [values])]
    if not criterion or criterion not in available:
        raise ValueError('Select the approved criterion that supports this decision.')
    old = deepcopy(target['raw'])
    target['raw']['human_screening'] = {'reason': reason, 'criterion': criterion, 'decision': decision,
                                       'original_decision': (old.get('human_screening') or {}).get('original_decision', target['decision'])}
    target['raw']['is_relevant'] = decision == 'include'
    target['decision'] = decision
    changed = old_decision != decision
    if changed:
        original_included = read_jsonl(project / 'filtered/included_papers.jsonl')
        bindings = {}
        for index, paper in enumerate(original_included, 1):
            path = local_pdf(project, paper)
            if path is None and not paper.get('pdf_identity_required'):
                candidates = sorted((project / 'pdfs').glob(f'row{index}_*.pdf'))
                path = candidates[0] if len(candidates) == 1 else None
            if path and path.resolve().is_relative_to((project / 'pdfs').resolve()):
                if sum(key(p) == key(paper) for p in original_included) == 1:
                    bindings[key(paper)] = str(path.resolve())
        for item in rows:
            if item['key'] in bindings:
                item['raw'].update(pdf_path=bindings[item['key']], pdf_downloaded=True)
    included = [row['raw'] for row in rows if row['decision'] == 'include']
    excluded = [row['raw'] for row in rows if row['decision'] == 'exclude']
    if changed:
        # Positional PDF names cannot identify a paper after the included list changes.
        for row in included:
            row['pdf_identity_required'] = True
        for name in ('retrieval', 'extraction', 'categorization'):
            ledger['stages'][name]['stale'] = True
    counts = {'included_count': len(included), 'excluded_count': len(excluded), 'included': len(included), 'excluded': len(excluded), 'final_count': len(included), 'after_relevance_check': len(included), 'total_screened': len(rows)}
    ledger['stages']['screening']['counts'].update(counts)
    if ledger['stages']['screening']['last_valid']:
        ledger['stages']['screening']['last_valid']['counts'].update(counts)
    writes = {'filtered/included_papers.jsonl': jsonlines(included), 'filtered/filtered_papers.jsonl': jsonlines(included),
              'filtered/excluded_papers.jsonl': jsonlines(excluded), 'workflow_state.json': serialized(ledger),
              AUDIT: serialized(_audit(project, {'kind': 'screening', 'key': target['key'], 'before': old, 'after': target['raw'], 'reason': reason}))}
    for name in ('filtering_stats', 'screening_stats'):
        stats = read_json(project / f'filtered/{name}.json', {})
        writes[f'filtered/{name}.json'] = serialized({**stats, **counts,
            'removed': {**stats.get('removed', {}), 'by_relevance': len(excluded)}, 'human_reviewed': True})
    publish(project, writes)


def field_detail(project: Path, record_key: str, field_name: str) -> dict:
    rows = read_jsonl(project / 'extraction/extraction_results.jsonl')
    matches = [row for row in rows if key(row) == record_key]
    field = next((field for field in load_schema_draft(project).get('fields', []) if field['name'] == field_name), None)
    if len(matches) != 1 or not field:
        raise ValueError('Field or paper was not found.')
    row = matches[0]
    papers = [p for p in read_jsonl(project / 'filtered/included_papers.jsonl') if key(p) == record_key]
    paper = unique_record(papers, record_key) if papers else {}
    return {'key': record_key, 'field': field_name, 'type': field['type'], 'description': field.get('description', ''),
            'title': row.get('title'), 'value': row.get(field_name, (row.get('extracted_data') or {}).get(field_name)),
            'correction': (row.get('human_fields') or {}).get(field_name),
            'evidence': evidence_for_field(project, row, field_name, paper), 'revision': revision(project)}


def save_field(project: Path, payload: dict) -> None:
    ledger = _check(project, payload, 'extraction')
    detail = field_detail(project, payload.get('key'), payload.get('field'))
    reason = str(payload.get('reason') or '').strip()
    if not reason or 'value' not in payload:
        raise ValueError('A corrected value and a review reason are required.')
    value = payload['value']
    from .field_values import validate_value
    field = next(f for f in load_schema_draft(project)['fields'] if f['name'] == payload['field'])
    validate_value(field, value)
    quote = str(payload.get('quote') or '').strip()
    evidence = detail['evidence']
    page = next((p for p in evidence['pages'] if quote and normalized_text(quote) in normalized_text(p['text'])), None)
    if quote and not page:
        raise ValueError('The supporting quote must occur in this paper’s current PDF text. Leave it empty if unavailable.')
    rows = read_jsonl(project / 'extraction/extraction_results.jsonl')
    row = next(row for row in rows if key(row) == payload['key'])
    name = payload['field']
    old = deepcopy(row)
    row[name] = value
    row.setdefault('extracted_data', {})[name] = value
    prior = (row.get('human_fields') or {}).get(name) or {}
    row.setdefault('human_fields', {})[name] = {'original_value': prior.get('original_value', detail['value']), 'reason': reason,
                                             'value': value, 'at': datetime.now(timezone.utc).isoformat()}
    row.setdefault('field_evidence', {})[name] = {'quote': quote if page else '', 'page': page['page'] if page else None,
                                                 'status': 'located' if page else 'not_confirmable', 'origin': 'human'}
    ledger['stages']['categorization']['stale'] = True
    publish(project, {'extraction/extraction_results.jsonl': jsonlines(rows), 'workflow_state.json': serialized(ledger),
                      AUDIT: serialized(_audit(project, {'kind': 'field', 'key': payload['key'], 'field': name,
                                                         'before': old, 'after': row, 'reason': reason}))})


def run_sample(project: Path, payload: dict, *, llm_query=None, pdf_reader=None, web_search_query=None) -> dict:
    """Run only explicitly selected records; keep the saved full result unchanged."""
    mode = payload.get('mode')
    if mode not in {'screening', 'extraction'}:
        raise ValueError('Choose screening or extraction for the live sample.')
    _check(project, payload, mode)
    keys = payload.get('keys')
    if not isinstance(keys, list) or not 1 <= len(keys) <= 5 or any(not isinstance(k, str) for k in keys) or len(set(keys)) != len(keys):
        raise ValueError('Select between one and five distinct papers for a live run.')
    original_revision = revision(project)
    started = datetime.now(timezone.utc).isoformat()
    results = []
    if mode == 'screening':
        from agents.filtering_agent import FilteringAgent
        from .screening_evidence import evidence_prompt
        from .screening_criteria import require_finalized_criteria
        require_finalized_criteria(project)
        rows = [row['raw'] for row in screening_rows(project)]
        available = {k: unique_record(rows, k) for k in keys}
        prompt = evidence_prompt(read_json(project / 'prompts/relevance_prompt.json', {}))
        folder = project / 'review/sample_logs'
        folder.mkdir(parents=True, exist_ok=True)
        kept, excluded = FilteringAgent(project, llm_query=llm_query)._check_relevance([deepcopy(available[k]) for k in keys], prompt, folder)
        for row in kept + excluded:
            results.append({'key': key(row), 'title': row.get('title'), 'decision': 'exclude' if row.get('is_relevant') is False else 'include',
                            'evidence': row.get('screening_evidence') or {}, 'error': row.get('relevance_error') or ''})
    else:
        from agents.extraction_agent import ExtractionAgent
        from .extraction_preview import draft_extraction_prompt
        from .extraction_schema import is_schema_finalized
        if not is_schema_finalized(project):
            raise ReviewConflict('Finalize the current schema before running an extraction sample.')
        papers = read_jsonl(project / 'filtered/included_papers.jsonl')
        selected = {k: unique_record(papers, k) for k in keys}
        available = {k: (papers.index(row) + 1, row) for k, row in selected.items()}
        prompt = draft_extraction_prompt(project, load_schema_draft(project))
        for record_key in keys:
            index, paper = available[record_key]
            result = ExtractionAgent(project, llm_query=llm_query, pdf_reader=pdf_reader, web_search_query=web_search_query).extract_one(
                paper=paper, row_number=index, extraction_prompt=prompt, pdf_folder=project / 'pdfs', pdf_files=sorted((project / 'pdfs').glob('*.pdf')))
            results.append({'key': record_key, 'title': result.get('title'), 'source': result.get('extraction_source'),
                            'fields': result.get('extracted_data') or {}, 'evidence': result.get('field_evidence') or {},
                            'error': result.get('error_message') or ''})
    if revision(project) != original_revision:
        raise ReviewConflict('Project changed while the sample was running; the sample was not published.')
    atomic_write_json(project / 'review/live_sample.json', {'mode': mode, 'started_at': started,
                      'completed_at': datetime.now(timezone.utc).isoformat(), 'base_revision': original_revision,
                      'execution': 'live', 'results': results})
    return {'status': 'sample_done', 'sample_count': len(results), 'errors': sum(bool(r['error']) for r in results)}
