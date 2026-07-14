"""Schema-scoped, user-safe Information Extraction preview projections."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .atomic_files import atomic_write_json
from .extraction_schema import load_schema_draft, normalize_schema
from .project_store import read_json, read_jsonl
from .safe_text import safe_display_text


PREVIEW_CACHE_VERSION = 1


def schema_revision(schema: dict[str, Any]) -> str:
    normalized = normalize_schema(schema if isinstance(schema, dict) else {})
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def paper_key(paper: dict[str, Any], index: int) -> str:
    for key in ("id", "paper_id", "doi"):
        value = str(paper.get(key) or "").strip()
        if value:
            return value
    title = str(paper.get("title") or "").strip()
    return title or f"paper-{index + 1}"


def load_preview_cache(project: Path, schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    payload = read_json(Path(project) / "extraction" / "schema_preview.json", {}) or {}
    if payload.get("version") != PREVIEW_CACHE_VERSION:
        return {}
    if payload.get("schema_revision") != schema_revision(schema):
        return {}
    items = payload.get("items")
    if not isinstance(items, dict):
        return {}
    return {str(key): value for key, value in items.items() if isinstance(value, dict)}


def write_preview_cache(project: Path, schema: dict[str, Any], key: str, row: dict[str, Any]) -> None:
    project = Path(project)
    items = load_preview_cache(project, schema)
    items[str(key)] = dict(row)
    atomic_write_json(
        project / "extraction" / "schema_preview.json",
        {
            "version": PREVIEW_CACHE_VERSION,
            "schema_revision": schema_revision(schema),
            "items": items,
        },
    )


def project_preview_projection(project: Path, index: int) -> dict[str, Any]:
    project = Path(project)
    if isinstance(index, bool) or not isinstance(index, int):
        raise ValueError("paper index must be an integer")
    papers = read_jsonl(project / "filtered" / "included_papers.jsonl")
    if index < 0 or index >= len(papers):
        raise ValueError("paper index is out of range")

    schema = load_schema_draft(project)
    paper = papers[index]
    key = paper_key(paper, index)
    row = _formal_row(project, paper, index)
    if row is None:
        row = load_preview_cache(project, schema).get(key)

    status = "missing"
    error = ""
    fields: list[dict[str, Any]] = []
    source = ""
    if row is not None:
        row_status = str(row.get("extraction_status") or "success").strip().lower()
        if row_status in {"error", "failed"}:
            status = "error"
            error = safe_display_text(
                str(row.get("error_message") or "This paper could not be extracted."),
                fallback="This paper could not be extracted.",
            )
        else:
            status = "ready"
            fields = _field_rows(schema, row)
            source = str(row.get("extraction_source") or "")

    return {
        "index": index,
        "total": len(papers),
        "canPrevious": index > 0,
        "canNext": index + 1 < len(papers),
        "status": status,
        "paper": _paper_projection(paper, key),
        "fields": fields,
        "source": source,
        "error": error,
    }


def empty_preview_projection() -> dict[str, Any]:
    return {
        "index": 0,
        "total": 0,
        "canPrevious": False,
        "canNext": False,
        "status": "missing",
        "paper": {"id": "", "title": "No paper preview available", "ref": ""},
        "fields": [],
        "source": "",
        "error": "",
    }


def _formal_row(project: Path, paper: dict[str, Any], index: int) -> dict[str, Any] | None:
    rows = read_jsonl(project / "extraction" / "extraction_results.jsonl")
    key = paper_key(paper, index)
    title = str(paper.get("title") or "").strip()
    for row in rows:
        identities = {str(row.get(name) or "").strip() for name in ("paper_id", "id", "doi")}
        if key in identities or (title and str(row.get("title") or "").strip() == title):
            return row
    if index < len(rows):
        row_number = rows[index].get("row_number")
        if row_number in (None, index + 1):
            return rows[index]
    return None


def _paper_projection(paper: dict[str, Any], key: str) -> dict[str, str]:
    parts = [str(paper.get("source") or "").strip(), str(paper.get("year") or "").strip()]
    return {
        "id": key,
        "title": str(paper.get("title") or "Untitled paper"),
        "ref": " · ".join(part for part in parts if part),
    }


def _field_rows(schema: dict[str, Any], row: dict[str, Any]) -> list[dict[str, Any]]:
    nested = row.get("extracted_data") if isinstance(row.get("extracted_data"), dict) else {}
    result = []
    for field in normalize_schema(schema).get("fields", []):
        name = field["name"]
        value = row.get(name, nested.get(name))
        result.append(
            {
                "name": name,
                "label": _label(name),
                "type": field["type"],
                "required": bool(field.get("required")),
                "value": _display_value(value) or "—",
            }
        )
    return result


def _display_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, list):
        return "; ".join(_display_value(item) for item in value if _display_value(item))
    if isinstance(value, dict):
        return "; ".join(f"{_label(str(key))}: {_display_value(item)}" for key, item in value.items())
    return str(value).strip()


def _label(name: str) -> str:
    return " ".join(part.capitalize() for part in str(name).replace("/", " ").replace("_", " ").split())
