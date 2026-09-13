"""Editable screening criteria stored with the prompt used by FilteringAgent."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .atomic_files import atomic_write_json
from .project_store import read_json
from .workflow_state import load_workflow_state, mark_stages_stale
from .project_decisions import remember_confirmed


def criteria_state(project: Path) -> dict:
    prompt = read_json(project / "prompts/relevance_prompt.json", {})
    config = read_json(project / "search_conditions.json", {})
    saved = prompt.get("eligibility")
    if isinstance(saved, dict):
        inclusion = saved.get("inclusion", [])
        exclusion = saved.get("exclusion", [])
    else:
        scope = prompt.get("criteria") or {}
        inclusion = []
        for key, fallback in (("topic", "primary_topic"), ("context", "domain")):
            label = (scope.get(key) or {}).get("label") or config.get(fallback)
            if label:
                inclusion.append(f"Study addresses the review {key}: {label}.")
        if not inclusion:
            inclusion = [str(config.get("description") or config.get("search_terms") or "Fits the stated review scope.")]
        exclusion = ["Explicit evidence shows incompatibility with the stated review scope."]
    revision = hashlib.sha256(json.dumps(prompt, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {
        "inclusion": inclusion,
        "exclusion": exclusion,
        "status": "finalized" if prompt.get("criteria_finalized") is True else "draft",
        "revision": revision,
        "prompt": str(prompt.get("user_prompt_template") or ""),
    }


def validate_criteria(payload: dict) -> dict:
    result = {}
    for key in ("inclusion", "exclusion"):
        values = payload.get(key)
        if not isinstance(values, list) or len(values) > 40 or any(not isinstance(v, str) or not v.strip() or len(v) > 2000 for v in values):
            raise ValueError(f"{key} must be a list of up to 40 non-empty criteria")
        result[key] = list(dict.fromkeys(v.strip() for v in values))
    if not result["inclusion"]:
        raise ValueError("At least one inclusion criterion is required")
    return result


def save_criteria(project: Path, payload: dict, *, finalized: bool = False) -> dict:
    state = criteria_state(project)
    if payload.get("revision") != state["revision"]:
        raise ValueError("Screening criteria changed. Refresh before saving.")
    eligibility = validate_criteria(payload)
    remember_confirmed(project, 'screening_profile')
    prompt_path = project / "prompts/relevance_prompt.json"
    prompt = read_json(prompt_path, {})
    changed = eligibility != prompt.get("eligibility")
    if changed:
        # Invalidate first: an interrupted write must never leave old results current.
        stages = load_workflow_state(project)["stages"]
        affected = [name for name in ("screening", "retrieval", "extraction", "categorization")
                    if stages[name]["last_valid"] is not None or stages[name]["attempt"] > 0]
        if affected:
            mark_stages_stale(project, affected)
        prompt["eligibility"] = eligibility
        prompt["system_prompt"] = "You screen scholarly records against reviewer-approved eligibility criteria using only supplied record evidence."
        prompt["instruction"] = (
            "Apply the inclusion and exclusion criteria. Return False when explicit evidence establishes an exclusion condition "
            "or material incompatibility with an inclusion criterion. Retain plausibly eligible records when evidence is incomplete. "
            "Return exactly one token: True or False."
        )
        prompt["user_prompt_template"] = (
            "REVIEWER ELIGIBILITY CRITERIA DATA:\n" + json.dumps(eligibility, ensure_ascii=False)
            + "\n\nPaper Title: {title}\nPaper Abstract: {abstract}\n\n" + prompt["instruction"]
        )
    prompt["criteria_finalized"] = finalized
    atomic_write_json(prompt_path, prompt)
    if finalized:
        remember_confirmed(project, 'screening_profile')
    return criteria_state(project)


def require_finalized_criteria(project: Path) -> None:
    if criteria_state(project)["status"] != "finalized":
        raise ValueError("Review and finalize inclusion/exclusion criteria before running screening.")
