"""Lead-owned Categorization & Analysis result capability."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .atomic_files import atomic_write_json, atomic_write_jsonl
from .project_store import read_jsonl


METADATA_FIELDS = {
    "paper_id",
    "title",
    "authors",
    "year",
    "doi",
    "source",
    "url",
    "pdf_path",
    "pdf_file",
    "row_number",
    "extraction_source",
    "extraction_status",
    "extraction_model",
    "extraction_cost_usd",
    "extracted_at",
    "error_message",
    "source_urls",
    "confidence",
}


class CategorizationAnalysis:
    """Groups extracted evidence into semantic categories for final review presentation."""

    def __init__(self, project_path: Path | str, llm_query: Callable | None = None):
        self.project_path = Path(project_path)
        self.llm_query = llm_query

    def run(self, input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(input_data or {})
        rows = _successful_rows(read_jsonl(self.project_path / "extraction" / "extraction_results.jsonl"))
        categorizer_func = payload.get("categorizer_func")
        if categorizer_func is not None:
            return self._apply_external_categorizer(rows, categorizer_func)
        if _input_categories(payload):
            return self._apply_user_categories(rows, payload)
        return self._apply_llm_categorization(rows)

    def suggest_categories(self, input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(input_data or {})
        rows = _successful_rows(read_jsonl(self.project_path / "extraction" / "extraction_results.jsonl"))
        field = str(payload.get("field") or _recommended_category_field(rows)).strip() or "title"
        mode = _category_mode(payload)
        categories, descriptions = _generate_suggestions(rows, field, mode, self.llm_query or _default_llm_query)
        sample_values = _field_sample_values(rows, field, limit=10)
        self._write_suggestions(field, mode, categories, descriptions, sample_values)
        return {
            "status": "categories_suggested",
            "field": field,
            "mode": mode,
            "categories": len(categories),
            "suggestions_file": str(self.project_path / "categorization" / "suggested_categories.json"),
        }

    def _apply_external_categorizer(self, rows: list[dict], categorizer_func: Callable) -> dict[str, Any]:
        categories: list[str] = []
        categorized: list[dict] = []
        for row in rows:
            category = str(categorizer_func(row) or "Uncategorized")
            if category not in categories:
                categories.append(category)
            categorized.append({**row, "category": category})

        self._write_outputs("auto", categories, {}, categorized, mode="single")
        return {"status": "categorization_done", "categories": len(categories), "rows": len(categorized), "field": "auto", "mode": "single"}

    def _apply_llm_categorization(self, rows: list[dict]) -> dict[str, Any]:
        if not rows:
            self._write_outputs("auto", [], {}, [], mode="single")
            return {"status": "categorization_done", "categories": 0, "rows": 0, "field": "auto", "mode": "single"}

        llm_query = self.llm_query or _default_llm_query
        plan = _generate_category_plan(rows, llm_query)
        field = plan["field"]
        categories = plan["categories"]
        descriptions = plan.get("category_descriptions", {})
        categorized: list[dict] = []

        for row in rows:
            value = str(row.get(field) or row.get("title") or "")
            category = _assign_category(row, field, value, categories, llm_query)
            if category not in categories:
                raise ValueError(f"Categorization LLM returned category outside allowed list: {category}")
            categorized.append({**row, "category": category, f"{field}_category": category})

        populated_categories = _populated_categories(categories, categorized)
        populated_descriptions = {category: descriptions[category] for category in populated_categories if category in descriptions}
        self._write_outputs(field, populated_categories, populated_descriptions, categorized, mode="single")
        return {"status": "categorization_done", "categories": len(populated_categories), "rows": len(categorized), "field": field, "mode": "single"}

    def _apply_user_categories(self, rows: list[dict], payload: dict[str, Any]) -> dict[str, Any]:
        field = str(payload.get("field") or _recommended_category_field(rows)).strip() or "title"
        mode = _category_mode(payload)
        categories = _input_categories(payload)
        descriptions = _input_descriptions(payload)
        llm_query = self.llm_query or _default_llm_query
        categorized: list[dict] = []

        for row in rows:
            value = str(row.get(field) or "")
            if not value:
                assigned: str | list[str] = "N/A" if mode == "single" else []
            elif mode == "multiple":
                assigned = _assign_multiple_categories(row, field, value, categories, llm_query)
            else:
                assigned = _assign_category(row, field, value, categories, llm_query)
            categorized.append({**row, "category": assigned, f"{field}_category": assigned})

        populated_categories = _populated_categories(categories, categorized)
        populated_descriptions = {category: descriptions[category] for category in populated_categories if category in descriptions}
        self._write_outputs(field, populated_categories, populated_descriptions, categorized, mode=mode)
        return {"status": "categorization_done", "categories": len(populated_categories), "rows": len(categorized), "field": field, "mode": mode}

    def _write_outputs(self, field: str, categories: list[str], descriptions: dict[str, str], rows: list[dict], *, mode: str) -> None:
        categorization_dir = self.project_path / "categorization"
        atomic_write_jsonl(categorization_dir / "categorized_results.jsonl", rows)
        mapping = {
            "field": field,
            "mode": mode,
            "categories": categories,
            "category_descriptions": descriptions,
            "descriptions": descriptions,
            "mapping": {row.get("title") or row.get("paper_id") or f"row_{index}": row.get("category", "") for index, row in enumerate(rows)},
            "categorized_at": datetime.now().isoformat(),
        }
        atomic_write_json(categorization_dir / "categorization_mapping.json", mapping, indent=None)

    def _write_suggestions(self, field: str, mode: str, categories: list[str], descriptions: dict[str, str], sample_values: list[str]) -> None:
        categorization_dir = self.project_path / "categorization"
        payload = {
            "field": field,
            "mode": mode,
            "categories": categories,
            "category_descriptions": descriptions,
            "descriptions": descriptions,
            "sample_values": sample_values,
            "suggested_at": datetime.now().isoformat(),
        }
        atomic_write_json(categorization_dir / "suggested_categories.json", payload, indent=None)


def _generate_suggestions(rows: list[dict], field: str, mode: str, llm_query: Callable) -> tuple[list[str], dict[str, str]]:
    sample_values = _field_sample_values(rows, field, limit=30)
    mode_instruction = (
        "Create 5-10 meaningful categories that could classify ALL papers. Each paper should fit into exactly ONE category."
        if mode == "single"
        else "Create 5-10 meaningful categories/tags. Each paper may belong to MULTIPLE categories."
    )
    response, _usage = llm_query(
        text_prompt=f"""Analyze these values from the "{field}" field in research papers.

Sample values (each is from one paper):
{chr(10).join([f'- "{value[:200]}"' for value in sample_values])}

{mode_instruction}

Return JSON:
{{
    "categories": ["Category1", "Category2"],
    "category_descriptions": {{
        "Category1": "Brief description of what belongs here",
        "Category2": "Brief description"
    }}
}}""",
        system_prompt="You are a research analyst creating meaningful categories for academic paper data. Focus on semantic understanding, not keyword matching.",
    )
    raw = _extract_json(response)
    categories = [str(item).strip() for item in raw.get("categories") or [] if str(item).strip()]
    if not categories:
        raise ValueError("No categories generated")
    descriptions = {str(key): str(value) for key, value in (raw.get("category_descriptions") or raw.get("descriptions") or {}).items()}
    return categories, descriptions


def _generate_category_plan(rows: list[dict], llm_query: Callable) -> dict[str, Any]:
    field = _recommended_category_field(rows)
    sample_values = [str(row.get(field) or row.get("title") or "")[:500] for row in rows[:25]]
    response, _usage = llm_query(
        text_prompt=f"""Create 3-8 meaningful categories for systematic-review extraction results.

Preferred field: {field}
Sample values:
{json.dumps(sample_values, ensure_ascii=False, indent=2)}

Return ONLY valid JSON:
{{
  "field": "{field}",
  "categories": ["Category A", "Category B"],
  "category_descriptions": {{"Category A": "Description"}}
}}""",
        system_prompt="You are a research analyst creating concise semantic categories.",
    )
    raw = _extract_json(response)
    categories = [str(item).strip() for item in raw.get("categories") or [] if str(item).strip()]
    if not categories:
        raise ValueError("No categories generated")
    return {
        "field": str(raw.get("field") or field),
        "categories": categories,
        "category_descriptions": raw.get("category_descriptions") or {},
    }


def _assign_category(row: dict, field: str, value: str, categories: list[str], llm_query: Callable) -> str:
    response, _usage = llm_query(
        text_prompt=f"""Categorize this paper into exactly one of these categories:
{", ".join(categories)}

Title: {row.get("title", "")}
Field: {field}
Value:
{value}

Return ONLY the category name.""",
        system_prompt="Return exactly one category name from the allowed list.",
    )
    category = _parse_category_response(response)
    return _normalize_allowed_category(category, categories)


def _assign_multiple_categories(row: dict, field: str, value: str, categories: list[str], llm_query: Callable) -> list[str]:
    response, _usage = llm_query(
        text_prompt=f"""Categorize this value into one or more of these categories:
{", ".join(categories)}

Title: {row.get("title", "")}
Field: {field}
Value:
{value}

Return category names separated by ", " (comma space). Only include categories that clearly apply.""",
        system_prompt="Return only category names from the allowed list, no explanations.",
    )
    return _parse_multiple_category_response(response, categories)


def _parse_multiple_category_response(response: Any, categories: list[str]) -> list[str]:
    text = str(response or "").strip()
    if not text:
        return []
    parsed_items: list[Any] = []
    try:
        payload = _extract_json(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        parsed_items = payload.get("categories") or payload.get("category") or payload.get("labels") or []
    elif isinstance(payload, list):
        parsed_items = payload
    if isinstance(parsed_items, str):
        parsed_items = re.split(r"[,;|]\s*", parsed_items)
    if not parsed_items:
        parsed_items = re.split(r"[,;|]\s*", text)

    normalized: list[str] = []
    for item in parsed_items:
        category = _normalize_allowed_category(str(item), categories)
        if category in categories and category not in normalized:
            normalized.append(category)
    if normalized:
        return normalized

    lowered = text.casefold()
    return [category for category in categories if category.casefold() in lowered]


def _parse_category_response(response: Any) -> str:
    text = str(response or "").strip()
    if not text:
        return ""
    try:
        payload = _extract_json(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return text.strip().strip('"')
    if isinstance(payload, dict):
        return str(payload.get("category") or payload.get("label") or payload.get("name") or "").strip()
    return text.strip().strip('"')


def _normalize_allowed_category(category: str, categories: list[str]) -> str:
    text = str(category or "").strip().strip('"')
    normalized = re.sub(r"\s+", " ", text).casefold()
    for allowed in categories:
        if re.sub(r"\s+", " ", str(allowed).strip()).casefold() == normalized:
            return str(allowed)
    return text


def _populated_categories(categories: list[str], rows: list[dict]) -> list[str]:
    assigned: set[str] = set()
    for row in rows:
        value = row.get("category")
        if isinstance(value, list):
            assigned.update(str(item) for item in value)
        else:
            assigned.add(str(value or ""))
    return [category for category in categories if category in assigned]


def _recommended_category_field(rows: list[dict]) -> str:
    candidates: dict[str, set[str]] = {}
    for row in rows:
        for key, value in row.items():
            if key in METADATA_FIELDS or key.endswith("_category"):
                continue
            text = str(value or "").strip()
            if text:
                candidates.setdefault(key, set()).add(text.lower()[:120])
    if not candidates:
        return "title"
    return max(candidates.items(), key=lambda item: (len(item[1]), item[0] in {"key_findings", "methods"}))[0]


def _successful_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if str(row.get("extraction_status") or "success").lower() == "success"]


def _category_mode(payload: dict[str, Any]) -> str:
    mode = str(payload.get("mode") or payload.get("cat_mode") or "multiple").strip().lower()
    return "single" if mode == "single" else "multiple"


def _input_categories(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("categories") or payload.get("confirmed_categories") or []
    if isinstance(raw, str):
        items = raw.splitlines() if "\n" in raw else raw.split(",")
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    categories = []
    for item in items:
        text = str(item).strip()
        if text and text not in categories:
            categories.append(text)
    return categories


def _input_descriptions(payload: dict[str, Any]) -> dict[str, str]:
    raw = payload.get("category_descriptions") or payload.get("descriptions") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _field_sample_values(rows: list[dict], field: str, *, limit: int) -> list[str]:
    values = []
    seen = set()
    for row in rows:
        value = row.get(field)
        if value in (None, ""):
            continue
        text = re.sub(r"\s+", " ", str(value)).strip()
        if not text or text == "None":
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(text)
        if len(values) >= limit:
            break
    return values


def _extract_json(text: str) -> dict:
    stripped = str(text or "").strip()
    if "```json" in stripped:
        stripped = stripped.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in stripped:
        stripped = stripped.split("```", 1)[1].split("```", 1)[0]
    return json.loads(stripped)


def _default_llm_query(*args, **kwargs):
    from utils.llm import query_llm

    return query_llm(*args, **kwargs)
