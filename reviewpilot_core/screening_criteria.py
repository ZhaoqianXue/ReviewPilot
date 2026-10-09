"""Editable screening criteria stored with the prompt used by FilteringAgent."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .atomic_files import atomic_write_json
from .project_store import read_json
from .workflow_state import load_workflow_state, mark_stages_stale
from .project_decisions import remember_confirmed
from .screening_evidence import GUIDANCE_KEY, OUTPUT_INSTRUCTION, evidence_prompt
from .screening_guidance import invalidate as invalidate_guidance


def criteria_state(project: Path) -> dict:
    prompt = read_json(project / "prompts/relevance_prompt.json", {})
    config = read_json(project / "search_conditions.json", {})
    saved = prompt.get("eligibility")
    if isinstance(saved, dict):
        inclusion = saved.get("inclusion", [])
        exclusion = saved.get("exclusion", [])
    else:
        # The unsaved draft follows the current search setup, so it never lags behind a setup change.
        inclusion = concept_rules(config.get("concept_blocks"))
        if not inclusion:
            scope = prompt.get("criteria") or {}
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
        # Until the criteria are saved, the stored template predates them and is not what screening will use.
        "prompt": str(prompt.get("user_prompt_template") or "") if isinstance(saved, dict) else "",
    }


def concept_rules(blocks) -> list[str]:
    """One inclusion rule per concept row: alternatives in a row are joined with "or".

    Required rows and the concepts kept for screening both describe the review scope;
    analytical dimensions only organise the included evidence, so they are left out.
    """
    rows: dict[str, list[str]] = {}
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict) or block.get("role") == "analytical_dimension" or not str(block.get("label") or "").strip():
            continue
        group = str(block.get("eligibility_group") or block["label"])
        key = f"{'required' if block.get('required_for_eligibility', True) else 'screening'}:{group}"
        rows.setdefault(key, []).append(str(block["label"]).strip())
    ordered = sorted(rows.items(), key=lambda item: not item[0].startswith("required:"))
    return [f"Study addresses {' or '.join(labels)}." for _key, labels in ordered]


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
        # Review guidance was written for the previous criteria and leaves the instruction until redrafted.
        prompt.pop(GUIDANCE_KEY, None)
        built = evidence_prompt(prompt)
        prompt["system_prompt"], prompt["user_prompt_template"] = built["system_prompt"], built["user_prompt_template"]
        prompt["instruction"] = OUTPUT_INSTRUCTION
    prompt["criteria_finalized"] = finalized
    atomic_write_json(prompt_path, prompt)
    if changed:
        invalidate_guidance(project)
    if finalized:
        remember_confirmed(project, 'screening_profile')
    return criteria_state(project)


def require_finalized_criteria(project: Path) -> None:
    if criteria_state(project)["status"] != "finalized":
        raise ValueError("Review and finalize inclusion/exclusion criteria before running screening.")
