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


def _analysis(raw: Any, lists: dict[str, int], texts: tuple[str, ...] = ()) -> dict[str, Any]:
    """The decisions a draft states before its rules; kept with the draft, never rendered into prompts."""
    if not isinstance(raw, dict) or set(raw) != set(lists) | set(texts):
        raise ValueError(f"analysis needs exactly these keys: {', '.join([*texts, *lists])}")
    out: dict[str, Any] = {key: _clean(raw[key], f"analysis.{key}", MAX_FOCUS_CHARS) for key in texts}
    for key, limit in lists.items():
        if not isinstance(raw[key], list) or len(raw[key]) > limit:
            raise ValueError(f"analysis.{key} must list at most {limit} items")
        out[key] = raw[key]
    return out


def validate_screening_guidance(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - {"analysis"} != set(SCREENING_KEYS):
        raise ValueError(f"Screening guidance needs exactly these keys: {', '.join(SCREENING_KEYS)}")
    guidance: dict[str, Any] = {"review_focus": _clean(raw["review_focus"], "review_focus", MAX_FOCUS_CHARS)}
    if "analysis" in raw:
        analysis = _analysis(raw["analysis"], {"outside_setting_work": 10}, ("setting",))
        analysis["outside_setting_work"] = [_clean(item, "analysis.outside_setting_work") for item in analysis["outside_setting_work"]]
        guidance["analysis"] = analysis
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
    if not isinstance(raw, dict) or set(raw) - {"analysis"} != set(CODING_KEYS):
        raise ValueError(f"Coding rules need exactly these keys: {', '.join(CODING_KEYS)}")
    preamble = _clean(raw["preamble"], "preamble", MAX_FOCUS_CHARS)
    analysis = None
    if "analysis" in raw:
        analysis = _analysis(raw["analysis"], {"central_fields": len(field_names)})
        unknown = [str(name) for name in analysis["central_fields"] if name not in field_names]
        if unknown:
            raise ValueError(f"analysis names fields the schema does not declare: {', '.join(unknown)}")
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
    return {"preamble": preamble, "fields": rules, **({"analysis": analysis} if analysis is not None else {})}


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


_STOPWORDS = set("""about above across after again against also among another because been before being below between both
could does doing during each either every from further have having here into itself just more most much must neither
other others otherwise over same should since some such than that their them then there these they this those through
under until upon very were what when where whether which while with within without would your yours paper papers study
studies record records report reports reported reporting unreported unspecified specified allowed categories category
include includes including value values field fields used uses using only union describe described description given
none empty null true false""".split())


def _field_terms(schema: dict[str, Any]) -> dict[str, set[str]]:
    """Distinctive words per field: from its name and description, minus words most fields share."""
    raw = {}
    for field in schema.get("fields") or []:
        if not isinstance(field, dict) or not field.get("name"):
            continue
        text = f"{field['name'].replace('_', ' ')} {field.get('description') or ''} {' '.join(map(str, field.get('options') or []))}"
        raw[field["name"]] = {word for word in re.findall(r"[a-z][a-z-]{3,}", text.casefold()) if word not in _STOPWORDS}
    shared = {word for word in set().union(*raw.values()) if sum(word in terms for terms in raw.values()) > max(1, len(raw) // 2)} if raw else set()
    return {name: terms - shared for name, terms in raw.items()}


def field_coverage(samples: list[dict[str, Any]], schema: dict[str, Any]) -> dict[str, float]:
    """Share of sampled papers whose full-text excerpts speak to each schema field."""
    names = [field["name"] for field in schema.get("fields") or [] if isinstance(field, dict) and field.get("name")]
    total = len(samples) or 1
    return {name: round(sum(name in (item.get("field_excerpts") or {}) for item in samples) / total, 2) for name in names}


def field_excerpts(text: str, schema: dict[str, Any], *, per_field: int = 2, max_sentence_chars: int = 320) -> dict[str, list[str]]:
    """Sentences of a paper's full text that speak to each schema field, best matches first.

    The Prompt Agent reads these to see how papers phrase each field before writing coding rules;
    the reference list is left out.
    """
    body = str(text or "")
    cut = max((m.start() for m in re.finditer(r"\n\s*(references|bibliography)\s*\n", body, re.I)), default=-1)
    if cut > len(body) * 0.5:
        body = body[:cut]
    sentences = [" ".join(s.split()) for s in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", body)]
    sentences = [s for s in sentences if 40 <= len(s) <= max_sentence_chars]
    excerpts: dict[str, list[str]] = {}
    for name, terms in _field_terms(schema).items():
        scored = []
        for index, sentence in enumerate(sentences):
            words = set(re.findall(r"[a-z][a-z-]{3,}", sentence.casefold()))
            hits = len(words & terms)
            if hits >= 2:
                scored.append((-hits, index, sentence))
        chosen = [sentence for _hits, _index, sentence in sorted(scored)[:per_field]]
        if chosen:
            excerpts[name] = chosen
    return excerpts
