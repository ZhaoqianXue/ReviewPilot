"""Explicit, revision-bound reuse of confirmed configurations from local projects."""
from __future__ import annotations

from pathlib import Path
import re

from .atomic_files import atomic_write_json
from .project_store import read_json
from .project_decisions import KINDS, confirmed_decisions, remember_confirmed, project_decision_revision
from .screening_criteria import criteria_state, save_criteria, validate_criteria
from .extraction_schema import normalize_schema, save_schema_draft, build_extraction_prompts
from .workflow_state import load_workflow_state, start_action, complete_action, fail_action

LABELS = {'search_setup': 'Search setup', 'screening_profile': 'Inclusion / exclusion criteria',
          'extraction_schema': 'Extraction fields', 'categorization_profile': 'Categories'}
STEPS = dict(zip(KINDS, ('search', 'screening', 'extraction', 'categorize')))


class ReuseConflict(ValueError):
    pass


def safe_project(root: Path, project_id: str) -> Path:
    if not isinstance(project_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}', project_id):
        raise ValueError('Invalid project')
    project = root / project_id
    if project.is_symlink() or not project.is_dir() or not (project / 'search_conditions.json').is_file():
        raise ValueError('Project is unavailable')
    # Reuse reads local configuration only; linked paths are not sources.
    for folder in ('memory', 'prompts', 'extraction', 'categorization'):
        path = project / folder
        if path.is_symlink() or (path.is_dir() and any(p.is_symlink() for p in path.iterdir())):
            raise ValueError('Project configuration contains linked files')
    if (project / 'search_conditions.json').is_symlink() or (project / 'workflow_state.json').is_symlink():
        raise ValueError('Project configuration contains linked files')
    if any((project / name).exists() for name in ('.setup_update_pending.json', '.retrieval_retry_pending.json')):
        raise ValueError('Project has an unfinished update')
    return project


def configuration_options(root: Path, target_id: str, *, busy=None) -> list[dict]:
    safe_project(root, target_id)
    result = []
    for path in sorted(root.iterdir(), key=lambda p: p.name):
        if path.name == target_id or (busy and busy(path.name)):
            continue
        try:
            project = safe_project(root, path.name)
            decisions = confirmed_decisions(project)
        except (ValueError, OSError, TypeError, AttributeError):
            continue
        title = str(read_json(project / 'search_conditions.json', {}).get('project_name') or path.name)
        for kind, decision in decisions.items():
            if not decision['stale']:
                result.append({'source_project_id': path.name, 'source_title': title,
                               'kind': kind, 'label': LABELS[kind]})
    return result


def preview_configuration(root: Path, target_id: str, selection: dict) -> dict:
    source_id, kind = selection.get('source_project_id'), selection.get('kind')
    if kind not in KINDS or source_id == target_id:
        raise ValueError('Select a configuration from another project')
    source, target = safe_project(root, source_id), safe_project(root, target_id)
    decision = confirmed_decisions(source).get(kind)
    if not decision or decision['stale']:
        raise ValueError('This source has no current confirmed configuration of that type')
    # Initialize a legacy target ledger before binding the preview revision.
    load_workflow_state(target)
    return {'source_project_id': source_id, 'source_title': str(read_json(source / 'search_conditions.json', {}).get('project_name') or source_id),
            'kind': kind, 'label': LABELS[kind], 'configuration': decision['configuration'],
            'source_revision': project_decision_revision(source), 'target_revision': project_decision_revision(target),
            'step': STEPS[kind]}


def apply_configuration(root: Path, target_id: str, request: dict) -> dict:
    preview = preview_configuration(root, target_id, request)
    if any(request.get(key) != preview[key] for key in ('source_revision', 'target_revision')):
        raise ReuseConflict('Source or current project changed. Preview the configuration again before importing.')
    target = safe_project(root, target_id)
    kind, config = preview['kind'], preview['configuration']
    stages = load_workflow_state(target)['stages']
    prerequisite = {'screening_profile': 'collection', 'extraction_schema': 'screening', 'categorization_profile': 'extraction'}.get(kind)
    if prerequisite and (stages[prerequisite]['status'] not in {'completed', 'partial'} or stages[prerequisite]['stale']):
        raise ValueError(f'Complete {prerequisite} before importing this configuration')
    if kind == 'search_setup':
        atomic_write_json(target / 'memory/search_setup_draft.json', config)
    elif kind == 'screening_profile':
        validate_criteria(config)
        save_criteria(target, {**config, 'revision': criteria_state(target)['revision']})
    elif kind == 'extraction_schema':
        schema = normalize_schema(config)
        if not schema['fields']:
            raise ValueError('Source extraction schema is empty')
        remember_confirmed(target, kind)
        start_action(target, 'edit-schema')
        try:
            save_schema_draft(target, schema)
            system, prompt, template = build_extraction_prompts(read_json(target / 'search_conditions.json', {}), schema)
            atomic_write_json(target / 'extraction/extraction_prompt.json', {'system_prompt': system, 'extraction_prompt': prompt, 'schema': schema, 'source': 'imported_draft'})
            atomic_write_json(target / 'prompts/extraction_prompt.json', {'system_prompt': system, 'user_prompt_template': template, 'schema': schema, 'source': 'imported_draft'})
            complete_action(target, 'edit-schema')
        except Exception as exc:
            fail_action(target, 'edit-schema', exc)
            raise
    else:
        fields = {field['name'] for field in normalize_schema(read_json(target / 'extraction/extraction_schema.json', {}))['fields']}
        if config.get('field') not in fields:
            raise ValueError('The source category field does not exist in this project. Add the matching extraction field before importing.')
        remember_confirmed(target, kind)
        start_action(target, 'suggest-categories')
        try:
            atomic_write_json(target / 'categorization/suggested_categories.json', config)
            complete_action(target, 'suggest-categories')
        except Exception as exc:
            fail_action(target, 'suggest-categories', exc)
            raise
    atomic_write_json(target / 'memory/last_configuration_import.json',
                      {key: value for key, value in preview.items() if key != 'configuration'})
    return {'status': 'draft_imported', 'kind': kind, 'step': preview['step']}
