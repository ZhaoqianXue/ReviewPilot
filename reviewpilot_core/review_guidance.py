"""Review-specific guidance written by the Prompt Agent.

Screening guidance (review focus, definitions, include and exclude categories, tie-breakers) and
extraction coding rules are validated here and rendered in the shape the screening and extraction
instructions carry them. Rendering is deterministic, so the confirmed guidance is exactly the text
the Screening and Extraction Agents receive.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

SCREENING_KEYS = ("review_focus", "definitions", "include_when", "exclude_when", "tie_breakers")
SCREENING_LIMITS = {"definitions": (0, 4), "include_when": (4, 12), "exclude_when": (3, 10), "tie_breakers": (0, 4)}
CODING_KEYS = ("preamble", "fields")
MAX_ITEM_CHARS = 600
MAX_FOCUS_CHARS = 1500

INCLUDE_LEAD = "Include when the record's own work reports or will clearly produce at least one of:"
EXCLUDE_LEAD = "Exclude when the record's own work is:"


def _clean(text: Any, what: str, limit: int = MAX_ITEM_CHARS) -> str:
    value = " ".join(str(text).split()) if isinstance(text, str) else ""
    value = re.sub(r"^(?:[-*•]\s+|\d+[.)]\s+)+", "", value)
    if not value or len(value) > limit:
        raise ValueError(f"{what} must be non-empty text of at most {limit} characters")
    return value


def validate_screening_guidance(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != set(SCREENING_KEYS):
        raise ValueError(f"Screening guidance needs exactly these keys: {', '.join(SCREENING_KEYS)}")
    guidance: dict[str, Any] = {"review_focus": _clean(raw["review_focus"], "review_focus", MAX_FOCUS_CHARS)}
    for key, (low, high) in SCREENING_LIMITS.items():
        items = raw[key]
        if not isinstance(items, list) or not low <= len(items) <= high:
            raise ValueError(f"{key} must list {low} to {high} items")
        cleaned = [_clean(item, key) for item in items]
        if len({item.casefold() for item in cleaned}) != len(cleaned):
            raise ValueError(f"{key} items must be distinct")
        guidance[key] = cleaned
    overlap = {item.casefold() for item in guidance["include_when"]} & {item.casefold() for item in guidance["exclude_when"]}
    if overlap:
        raise ValueError("include_when and exclude_when must not repeat the same category")
    return guidance


def render_screening_guidance(guidance: dict[str, Any]) -> str:
    """The block inserted before the eligibility criteria (project-document Appendix B shape)."""
    lines = ["Review focus (from the review protocol):", guidance["review_focus"], "", "Domain rules for this review:"]
    lines += guidance["definitions"]
    lines += [INCLUDE_LEAD] + [f"- {item}" for item in guidance["include_when"]]
    lines += [EXCLUDE_LEAD] + [f"- {item}" for item in guidance["exclude_when"]]
    lines += guidance["tie_breakers"]
    return "\n".join(lines)


def validate_coding_rules(raw: Any, schema: dict[str, Any]) -> dict[str, Any]:
    field_names = [str(field.get("name")) for field in (schema.get("fields") or []) if isinstance(field, dict)]
    if not isinstance(raw, dict) or set(raw) != set(CODING_KEYS):
        raise ValueError(f"Coding rules need exactly these keys: {', '.join(CODING_KEYS)}")
    preamble = _clean(raw["preamble"], "preamble", MAX_FOCUS_CHARS)
    entries = raw["fields"]
    if not isinstance(entries, list) or len(entries) > len(field_names):
        raise ValueError("fields must list at most one entry per schema field")
    rules: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"field", "rules"}:
            raise ValueError("Each coding-rule entry needs exactly field and rules")
        name = str(entry["field"]).strip()
        if name not in field_names:
            raise ValueError(f"Coding rules name a field the schema does not declare: {name}")
        if any(item["field"] == name for item in rules):
            raise ValueError(f"Coding rules list field {name} more than once")
        if not isinstance(entry["rules"], list) or not 1 <= len(entry["rules"]) <= 8:
            raise ValueError(f"Field {name} needs 1 to 8 coding rules")
        rules.append({"field": name, "rules": [_clean(rule, f"rule for {name}") for rule in entry["rules"]]})
    rules.sort(key=lambda item: field_names.index(item["field"]))
    return {"preamble": preamble, "fields": rules}


def field_label(name: str) -> str:
    words = name.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def render_coding_rules(rules: dict[str, Any]) -> str:
    """The block inserted before the paper text (project-document Appendix D shape)."""
    lines = ["REVIEW CODING PROTOCOL", rules["preamble"]]
    for entry in rules["fields"]:
        lines += ["", field_label(entry["field"])] + [f"- {rule}" for rule in entry["rules"]]
    return "\n".join(lines)


def record_sample(records: list[dict[str, Any]], *, limit: int, abstract_chars: int) -> list[dict[str, str]]:
    """Distinct records (by normalized title) in a stable pseudo-random order, title plus abstract opening."""
    seen: set[str] = set()
    distinct: list[dict[str, str]] = []
    for record in records:
        title = " ".join(str(record.get("title") or "").split())
        key = re.sub(r"[^a-z0-9]+", "", title.casefold())
        if not key or key in seen:
            continue
        seen.add(key)
        abstract = " ".join(str(record.get("abstract") or "").split())
        distinct.append({"title": title, "abstract": abstract[:abstract_chars] + ("…" if len(abstract) > abstract_chars else "")})
    distinct.sort(key=lambda item: hashlib.sha256(item["title"].encode("utf-8")).hexdigest())
    return distinct[:limit]
