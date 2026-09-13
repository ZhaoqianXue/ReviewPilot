"""Local confirmed decisions, kept separately from editable drafts and chat history."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .atomic_files import atomic_write_json
from .project_store import read_json

KINDS = ('search_setup', 'screening_profile', 'extraction_schema', 'categorization_profile')
STAGES = dict(zip(KINDS, ('collection', 'screening', 'extraction', 'categorization')))
SEARCH_KEYS = ('description', 'primary_topic', 'domain', 'primary_synonyms', 'domain_synonyms',
               'search_terms', 'search_queries', 'concept_blocks', 'keywords', 'platforms', 'date_range',
               'source_limits', 'max_results')


def revision(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def live_configuration(project: Path, kind: str) -> dict | None:
    if kind == 'search_setup':
        value = read_json(project / 'search_conditions.json', {})
        return {key: value[key] for key in SEARCH_KEYS if key in value} if value else None
    if kind == 'screening_profile':
        value = read_json(project / 'prompts/relevance_prompt.json', {})
        if value.get('criteria_finalized') is True and isinstance(value.get('eligibility'), dict):
            return value['eligibility']
    if kind == 'extraction_schema':
        from .extraction_schema import is_schema_finalized
        if is_schema_finalized(project):
            return read_json(project / 'extraction/extraction_schema.json', {}) or None
    if kind == 'categorization_profile':
        value = read_json(project / 'categorization/categorization_mapping.json', {})
        if value.get('categories'):
            return {key: value[key] for key in ('field', 'mode', 'categories', 'category_descriptions') if key in value}
    return None


def remember_confirmed(project: Path, kind: str) -> None:
    if kind not in KINDS:
        raise ValueError('Unsupported configuration kind')
    value = live_configuration(project, kind)
    if value is None:
        return
    path = project / 'memory/confirmed_decisions.json'
    decisions = read_json(path, {})
    decisions[kind] = {'configuration': value, 'revision': revision(value)}
    atomic_write_json(path, decisions)


def confirmed_decisions(project: Path) -> dict:
    """Current confirmed artifacts win; snapshots preserve decisions while editing."""
    saved = read_json(project / 'memory/confirmed_decisions.json', {})
    stages = read_json(project / 'workflow_state.json', {}).get('stages', {})
    result = {}
    for kind in KINDS:
        live = live_configuration(project, kind)
        record = saved.get(kind, {})
        value = live if live is not None else record.get('configuration')
        if isinstance(value, dict):
            result[kind] = {'configuration': value, 'revision': revision(value),
                            'stale': bool(stages.get(STAGES[kind], {}).get('stale')),
                            'editing': live is None}
    return result


def project_decision_revision(project: Path) -> str:
    """Bind a pending edit to the project state it was reviewed against."""
    files = ('search_conditions.json', 'prompts/relevance_prompt.json',
             'extraction/extraction_schema.json', 'extraction/extraction_schema_draft.json',
             'extraction/schema_finalized.json', 'categorization/categorization_mapping.json',
             'categorization/suggested_categories.json', 'workflow_state.json',
             'memory/search_setup_draft.json', 'memory/confirmed_decisions.json')
    return revision({name: read_json(project / name) for name in files})
