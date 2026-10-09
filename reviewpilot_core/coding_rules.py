"""Extraction coding rules: drafted by the Prompt Agent, confirmed by the researcher.

After the schema is finalized the Prompt Agent drafts per-field coding rules from the schema and the
included papers' full text. The researcher confirms them, after editing if needed, and only confirmed
rules reach the extraction instruction, before the paper text. A draft records the schema and the
included papers it was written for; when either changes the rules are out of date.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomic_files import atomic_write_json
from .project_store import read_json, read_jsonl
from .review_guidance import render_coding_rules, validate_coding_rules
from .workflow_state import load_workflow_state, mark_stages_stale

RULES_FILE = "extraction/coding_rules.json"
PROMPT_FILE = "prompts/extraction_prompt.json"
RULES_KEY = "coding_rules"


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _schema(project: Path) -> dict[str, Any]:
    return (read_json(project / PROMPT_FILE, {}) or {}).get("schema") or {}


def basis_signature(project: Path) -> str:
    """The finalized schema and the included papers a draft is written for."""
    # Identities only: retrieval rewrites the file with PDF paths without changing which papers are included.
    papers = sorted(f"{row.get('source') or ''}|{row.get('id') or ''}|{' '.join(str(row.get('title') or '').split()).casefold()}"
                    for row in read_jsonl(project / "filtered" / "included_papers.jsonl"))
    return _sha({"schema": _schema(project), "included": papers})


def rules_state(project: Path) -> dict[str, Any]:
    stored = read_json(project / RULES_FILE, {}) or {}
    if not stored:
        return {"status": "missing", "revision": "", "rules": None, "text": "", "error": ""}
    status = stored.get("status") or "draft"
    if status in {"draft", "confirmed"} and stored.get("basis") != basis_signature(project):
        status = "stale"
    rules = {"preamble": stored["preamble"], "fields": stored["fields"]} if "fields" in stored else None
    return {"status": status, "revision": _sha(stored), "rules": rules, "text": str(stored.get("text") or ""),
            "error": str(stored.get("error") or ""), "generated_at": stored.get("generated_at") or "",
            "confirmed_at": stored.get("confirmed_at") or "", "paper_count": stored.get("paper_count")}


def save_draft(project: Path, rules: dict[str, Any]) -> dict[str, Any]:
    clean = validate_coding_rules({"preamble": rules["preamble"], "fields": rules["fields"]}, _schema(project))
    record = {**clean, "text": render_coding_rules(clean), "status": "draft", "basis": basis_signature(project),
              "generated_at": datetime.now(timezone.utc).isoformat(), "model": rules.get("model"),
              "paper_count": rules.get("paper_count"), "analysis": rules.get("analysis") or {},
              "llm_usage": rules.get("llm_usage") or {}}
    atomic_write_json(project / RULES_FILE, record)
    _withdraw_from_prompt(project)
    return rules_state(project)


def save_failure(project: Path, error: str) -> dict[str, Any]:
    atomic_write_json(project / RULES_FILE, {"status": "failed", "error": str(error)[:500],
                                             "generated_at": datetime.now(timezone.utc).isoformat()})
    _withdraw_from_prompt(project)
    return rules_state(project)


def confirm(project: Path, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Confirm the current draft, or an edited version of it, and hand it to the extraction instruction."""
    payload = payload or {}
    state = rules_state(project)
    if state["status"] not in {"draft", "confirmed"}:
        raise ValueError("Draft the coding rules for the current schema before confirming them.")
    if payload.get("revision") and payload["revision"] != state["revision"]:
        raise ValueError("The coding rules changed. Refresh before confirming.")
    stored = read_json(project / RULES_FILE, {}) or {}
    edited = {key: payload[key] for key in ("preamble", "fields") if key in payload}
    clean = validate_coding_rules({"preamble": stored["preamble"], "fields": stored["fields"], **edited}, _schema(project))
    text = render_coding_rules(clean)
    record = {**stored, **clean, "text": text, "status": "confirmed",
              "edited": bool(edited) or bool(stored.get("edited")), "confirmed_at": datetime.now(timezone.utc).isoformat()}
    prompt = read_json(project / PROMPT_FILE, {}) or {}
    if (prompt.get(RULES_KEY) or {}).get("text") != text:
        # Different rules make earlier extraction and categorization stale, as a schema change does.
        stages = load_workflow_state(project)["stages"]
        affected = [name for name in ("extraction", "categorization")
                    if stages[name]["last_valid"] is not None or stages[name]["attempt"] > 0]
        if affected:
            mark_stages_stale(project, affected)
    atomic_write_json(project / RULES_FILE, record)
    prompt[RULES_KEY] = {"text": text, "revision": _sha(clean)}
    _write_prompt(project, prompt)
    return rules_state(project)


def invalidate(project: Path) -> None:
    """The schema changed: the rules no longer match and leave the extraction instruction."""
    stored = read_json(project / RULES_FILE, {}) or {}
    if stored and stored.get("status") != "stale":
        atomic_write_json(project / RULES_FILE, {**stored, "status": "stale"})
    _withdraw_from_prompt(project)


def _withdraw_from_prompt(project: Path) -> None:
    prompt = read_json(project / PROMPT_FILE, {}) or {}
    if RULES_KEY in prompt:
        prompt.pop(RULES_KEY)
        _write_prompt(project, prompt)


def _write_prompt(project: Path, prompt: dict[str, Any]) -> None:
    """Store the prompt with the instruction it now produces, so the saved instruction is the one extraction uses."""
    from .extraction_schema import build_extraction_prompts

    if (prompt.get("schema") or {}).get("fields"):
        config = read_json(project / "search_conditions.json", {}) or {}
        system, _stage, template = build_extraction_prompts(config, prompt["schema"], (prompt.get(RULES_KEY) or {}).get("text", ""))
        prompt["system_prompt"], prompt["user_prompt_template"] = system, template
    atomic_write_json(project / PROMPT_FILE, prompt)


def require_confirmed_rules(project: Path) -> None:
    if rules_state(project)["status"] != "confirmed":
        raise ValueError("Review and confirm the coding rules before running extraction.")
