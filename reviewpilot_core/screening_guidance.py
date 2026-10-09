"""Review guidance for screening: drafted by the Prompt Agent, confirmed by the researcher.

The Prompt Agent drafts the guidance after the criteria are finalized, from the criteria and the
collected records. The researcher confirms it, after editing if needed, and only confirmed guidance
reaches the screening instruction. A draft records the criteria and the collection it was written
for; when either changes the guidance is out of date and has to be drafted again.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomic_files import atomic_write_json
from .project_store import read_json
from .review_guidance import SCREENING_KEYS, render_screening_guidance, validate_screening_guidance
from .screening_evidence import GUIDANCE_KEY, evidence_prompt
from .workflow_state import load_workflow_state, mark_stages_stale

GUIDANCE_FILE = "prompts/screening_guidance.json"
RELEVANCE_FILE = "prompts/relevance_prompt.json"


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def basis_signature(project: Path) -> str:
    """The saved criteria and the collection a draft is written for."""
    eligibility = (read_json(project / RELEVANCE_FILE, {}) or {}).get("eligibility") or {}
    summary = read_json(project / "collected" / "summary.json", {}) or {}
    collection = {path.name: (path.stat().st_size, int(path.stat().st_mtime))
                  for path in sorted((project / "collected").glob("*.jsonl"))} if (project / "collected").exists() else {}
    return _sha({"eligibility": eligibility, "summary": summary, "collection": collection})


def guidance_state(project: Path) -> dict[str, Any]:
    stored = read_json(project / GUIDANCE_FILE, {}) or {}
    if not stored:
        return {"status": "missing", "revision": "", "guidance": None, "text": "", "error": ""}
    status = stored.get("status") or "draft"
    if status in {"draft", "confirmed"} and stored.get("basis") != basis_signature(project):
        status = "stale"
    guidance = {key: stored[key] for key in SCREENING_KEYS if key in stored} or None
    return {"status": status, "revision": _sha(stored), "guidance": guidance, "text": str(stored.get("text") or ""),
            "error": str(stored.get("error") or ""), "generated_at": stored.get("generated_at") or "",
            "confirmed_at": stored.get("confirmed_at") or "", "candidate_count": stored.get("candidate_count")}


def save_draft(project: Path, guidance: dict[str, Any]) -> dict[str, Any]:
    clean = validate_screening_guidance({key: guidance[key] for key in SCREENING_KEYS})
    record = {**clean, "text": render_screening_guidance(clean), "status": "draft", "basis": basis_signature(project),
              "generated_at": datetime.now(timezone.utc).isoformat(), "model": guidance.get("model"),
              "candidate_count": guidance.get("candidate_count"), "llm_usage": guidance.get("llm_usage") or {}}
    atomic_write_json(project / GUIDANCE_FILE, record)
    _withdraw_from_prompt(project)
    return guidance_state(project)


def save_failure(project: Path, error: str) -> dict[str, Any]:
    atomic_write_json(project / GUIDANCE_FILE, {"status": "failed", "error": str(error)[:500],
                                               "generated_at": datetime.now(timezone.utc).isoformat()})
    _withdraw_from_prompt(project)
    return guidance_state(project)


def confirm(project: Path, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Confirm the current draft, or an edited version of it, and hand it to the screening instruction."""
    payload = payload or {}
    state = guidance_state(project)
    if state["status"] not in {"draft", "confirmed"}:
        raise ValueError("Draft the review guidance for the current criteria before confirming it.")
    if payload.get("revision") and payload["revision"] != state["revision"]:
        raise ValueError("The review guidance changed. Refresh before confirming.")
    stored = read_json(project / GUIDANCE_FILE, {}) or {}
    edited = {key: payload[key] for key in SCREENING_KEYS if key in payload}
    clean = validate_screening_guidance({**{key: stored[key] for key in SCREENING_KEYS}, **edited})
    record = {**stored, **clean, "text": render_screening_guidance(clean), "status": "confirmed",
              "edited": bool(edited) or bool(stored.get("edited")), "confirmed_at": datetime.now(timezone.utc).isoformat()}
    prompt = read_json(project / RELEVANCE_FILE, {}) or {}
    revision = _sha(clean)
    if (prompt.get(GUIDANCE_KEY) or {}).get("revision") != revision:
        # Like a criteria change, different guidance makes earlier screening and its downstream results stale.
        stages = load_workflow_state(project)["stages"]
        affected = [name for name in ("screening", "retrieval", "extraction", "categorization")
                    if stages[name]["last_valid"] is not None or stages[name]["attempt"] > 0]
        if affected:
            mark_stages_stale(project, affected)
    atomic_write_json(project / GUIDANCE_FILE, record)
    prompt[GUIDANCE_KEY] = {"text": record["text"], "include_when": clean["include_when"], "exclude_when": clean["exclude_when"],
                            "revision": revision}
    _write_prompt(project, prompt)
    return guidance_state(project)


def invalidate(project: Path) -> None:
    """Criteria changed: the guidance no longer matches and leaves the screening instruction."""
    stored = read_json(project / GUIDANCE_FILE, {}) or {}
    if stored and stored.get("status") != "stale":
        atomic_write_json(project / GUIDANCE_FILE, {**stored, "status": "stale"})
    _withdraw_from_prompt(project)


def _withdraw_from_prompt(project: Path) -> None:
    prompt = read_json(project / RELEVANCE_FILE, {}) or {}
    if GUIDANCE_KEY in prompt:
        prompt.pop(GUIDANCE_KEY)
        _write_prompt(project, prompt)


def _write_prompt(project: Path, prompt: dict[str, Any]) -> None:
    """Store the prompt with the instruction it now produces, so the saved instruction is the one screening uses."""
    if isinstance(prompt.get("eligibility"), dict):
        built = evidence_prompt(prompt)
        prompt["system_prompt"], prompt["user_prompt_template"] = built["system_prompt"], built["user_prompt_template"]
    atomic_write_json(project / RELEVANCE_FILE, prompt)


def require_confirmed_guidance(project: Path) -> None:
    state = guidance_state(project)
    if state["status"] != "confirmed":
        raise ValueError("Review and confirm the screening guidance before running screening.")
