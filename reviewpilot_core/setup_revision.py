"""Canonical search setup revisions and downstream invalidation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .workflow_state import STAGE_NAMES, load_workflow_state

_FIELDS = ("project_name", "description", "primary_topic", "domain", "search_terms", "search_queries", "platforms", "max_results", "source_limits", "date_range", "model", "derive_search_terms")
_DEPENDENCY_FIELDS = set(_FIELDS) - {"project_name"}


def normalize_setup(config: dict[str, Any]) -> dict[str, Any]:
    normalized = {key: config.get(key) for key in _FIELDS}
    normalized["platforms"] = [str(item).strip().lower() for item in normalized.get("platforms") or []]
    limits = normalized.get("source_limits") or {}
    normalized["source_limits"] = {key: int(limits[key]) for key in normalized["platforms"] if key in limits}
    normalized["search_queries"] = normalized.get("search_queries") or [{"name": "main", "query": normalized.get("search_terms") or ""}]
    normalized["date_range"] = normalized.get("date_range") or {"start": "", "end": ""}
    normalized["derive_search_terms"] = bool(normalized.get("derive_search_terms"))
    return normalized


def setup_revision(config: dict[str, Any]) -> str:
    payload = json.dumps(normalize_setup(config), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def materially_changes_dependencies(old: dict[str, Any], new: dict[str, Any]) -> bool:
    left, right = normalize_setup(old), normalize_setup(new)
    return any(left[key] != right[key] for key in _DEPENDENCY_FIELDS)


def affected_stages(project_path: Path | str) -> list[str]:
    state = load_workflow_state(project_path)
    return [name for name in STAGE_NAMES if state["stages"][name]["last_valid"] is not None]


def stale_replacement_stages(project_path: Path | str, action_stage: str) -> list[str]:
    state = load_workflow_state(project_path)
    start = STAGE_NAMES.index(action_stage)
    return [name for name in STAGE_NAMES[start:] if state["stages"][name]["stale"] and state["stages"][name]["last_valid"] is not None]
