"""Full-text eligibility as the first extraction field, and PRISMA 2020 counts.

The field's options come from the reviewer-approved screening criteria. A paper is
excluded at full text only when its PDF was read and the (possibly corrected) value
names an exclusion; empty or unmatched values keep the paper, as screening does.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .project_store import read_json, read_jsonl

FIELD = "full_text_eligibility"
INCLUDE = "Include"
EXCLUDE_PREFIX = "Exclude — "
NOT_MET_PREFIX = EXCLUDE_PREFIX + "not met: "
RAW_KEY = FIELD + "_unmatched"
MISSING = "(not returned by the model)"
DESCRIPTION = (
    "Full-text eligibility against the reviewer-approved criteria. Choose \"Include\" unless the full text "
    "establishes that the study meets an exclusion criterion or does not meet an inclusion criterion; then "
    "choose that criterion. Quote the deciding passage."
)


def options(project: Path) -> list[str]:
    from .screening_criteria import criteria_state
    state = criteria_state(Path(project))
    return ([INCLUDE] + [EXCLUDE_PREFIX + c for c in state["exclusion"]]
            + [NOT_MET_PREFIX + c for c in state["inclusion"]])


def field(project: Path) -> dict[str, Any]:
    return {"enum": options(project), "name": FIELD, "type": "Select", "description": DESCRIPTION,
            "required": False, "example": INCLUDE}


def with_field(project: Path, schema: dict[str, Any]) -> dict[str, Any]:
    """Put the eligibility field first in a drafted schema once criteria are finalized."""
    from .screening_criteria import criteria_state
    fields = [f for f in schema.get("fields") or [] if f.get("name") != FIELD]
    if criteria_state(Path(project))["status"] != "finalized":
        return {**schema, "fields": fields}
    return {**schema, "fields": [field(project), *fields]}


def refresh(project: Path, schema: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the field's options from the current criteria, if the schema keeps the field."""
    fields = schema.get("fields") or []
    if not any(f.get("name") == FIELD for f in fields):
        return schema
    fresh = field(project)
    return {**schema, "fields": [{**f, "enum": fresh["enum"], "description": fresh["description"]}
                                 if f.get("name") == FIELD else f for f in fields]}


def _norm(text: str) -> str:
    text = str(text).replace("–", "-").replace("—", "-").replace("“", '"').replace("”", '"').replace("’", "'")
    return re.sub(r"\s+", " ", text).strip().lower()


def normalize_value(value: Any, allowed: list[str]) -> tuple[Any, Any]:
    """Map a model value onto the declared options; return (value, unmatched raw value or None)."""
    if value is None or value == "" or value in allowed:
        return value, None
    if isinstance(value, str):
        wanted = _norm(value)
        by_norm = {_norm(option): option for option in allowed}
        if wanted in by_norm:
            return by_norm[wanted], None
        if wanted == "include" or wanted.startswith("include ") or wanted.startswith("include:"):
            return INCLUDE, None
        criterion_matches = [option for option in allowed[1:]
                             if _norm(option.removeprefix(NOT_MET_PREFIX).removeprefix(EXCLUDE_PREFIX)) in wanted]
        if len(criterion_matches) == 1:
            return criterion_matches[0], None
    return "", value


def value(row: dict[str, Any]) -> str:
    raw = row.get(FIELD, (row.get("extracted_data") or {}).get(FIELD))
    return raw if isinstance(raw, str) else ""


def assessed(row: dict[str, Any]) -> bool:
    """A report counts as assessed at full text only when its PDF was extracted."""
    return (str(row.get("extraction_status") or "success").lower() == "success"
            and str(row.get("extraction_source") or "pdf") == "pdf")


def is_excluded(row: dict[str, Any]) -> bool:
    """Excluded from synthesis: an extracted paper whose eligibility value names an exclusion.

    Web-fallback rows count here too, but PRISMA reports them apart from full-text assessments.
    """
    return (str(row.get("extraction_status") or "success").lower() == "success"
            and value(row).startswith(EXCLUDE_PREFIX))


def prisma_counts(project: Path) -> dict[str, Any]:
    """PRISMA 2020 flow counts derived from the project's own artifacts."""
    from .workflow_state import load_workflow_state
    project = Path(project)
    collected = read_json(project / "collected" / "summary.json", {}) or {}
    stats = read_json(project / "filtered" / "screening_stats.json", {}) or read_json(project / "filtered" / "filtering_stats.json", {}) or {}
    removed = stats.get("removed") or {}
    included = read_jsonl(project / "filtered" / "included_papers.jsonl")
    excluded = read_jsonl(project / "filtered" / "excluded_papers.jsonl")
    extraction = read_jsonl(project / "extraction" / "extraction_results.jsonl")
    schema = read_json(project / "extraction" / "extraction_schema.json", {}) or {}
    assessed_rows = [row for row in extraction if assessed(row)]
    web_rows = [row for row in extraction if str(row.get("extraction_status") or "") == "success"
                and str(row.get("extraction_source") or "") == "web_search_fallback"]
    web_excluded = sum(1 for row in web_rows if is_excluded(row))
    reasons: dict[str, int] = {}
    for row in assessed_rows:
        if is_excluded(row):
            reason = value(row).removeprefix(EXCLUDE_PREFIX)
            reasons[reason] = reasons.get(reason, 0) + 1
    overrides = 0
    for row in included + excluded:
        review = row.get("human_screening") or {}
        overrides += bool(review) and review.get("original_decision") != review.get("decision")
    with_pdf = sum(1 for row in extraction if row.get("pdf_file"))
    categorized = read_jsonl(project / "categorization" / "categorized_results.jsonl")
    stages = load_workflow_state(project)["stages"]
    return {
        "stale_stages": [name for name, stage in stages.items() if stage.get("stale")],
        "identification": {
            "records_by_source": collected.get("platform_stats") or {},
            "records_identified": int(collected.get("total_papers") or stats.get("initial_count") or 0),
            "removed_outside_date_range": int(removed.get("by_date", 0)),
            "removed_duplicates": int(removed.get("by_exact_dedup", 0)) + int(removed.get("by_similarity", 0)),
        },
        "screening": {
            "records_screened": len(included) + len(excluded),
            "records_excluded": len(excluded),
            "human_overrides": overrides,
            "reports_sought": len(included),
            "reports_not_retrieved": len(included) - with_pdf,
            "not_retrieved_assessed_from_web_sources": len(web_rows),
            "not_retrieved_excluded_from_web_sources": web_excluded,
            "reports_not_extracted": with_pdf - len(assessed_rows),
            "reports_assessed": len(assessed_rows),
            "full_text_eligibility_field": any(f.get("name") == FIELD for f in schema.get("fields") or []),
            "reports_excluded": sum(reasons.values()),
            "reports_excluded_by_reason": dict(sorted(reasons.items(), key=lambda item: -item[1])),
            "reports_unmatched_value": sum(1 for row in assessed_rows if row.get(RAW_KEY)),
            "full_text_corrections": sum(1 for row in assessed_rows if FIELD in (row.get("human_fields") or {})),
        },
        "included": {
            "studies_included": len(assessed_rows) - sum(reasons.values()),
            "studies_included_from_web_sources": len(web_rows) - web_excluded,
            "studies_categorized": len({str(row.get("paper_id") or row.get("title")) for row in categorized}),
        },
    }
