"""Artifact-backed extraction schema draft and finalization helpers."""

from __future__ import annotations

from datetime import datetime
import json
import re
from pathlib import Path
from typing import Any

from .atomic_files import atomic_write_json


def schema_paths(project_path: Path | str) -> dict[str, Path]:
    project = Path(project_path)
    extraction_dir = project / "extraction"
    return {
        "extraction_dir": extraction_dir,
        "draft": extraction_dir / "extraction_schema_draft.json",
        "current": extraction_dir / "extraction_schema.json",
        "prompt": extraction_dir / "extraction_prompt.json",
        "results": extraction_dir / "extraction_results.jsonl",
        "finalized": extraction_dir / "schema_finalized.json",
        "architecture_prompt": project / "prompts" / "extraction_prompt.json",
        "search_conditions": project / "search_conditions.json",
    }


def load_schema_draft(project_path: Path | str) -> dict[str, Any]:
    paths = schema_paths(project_path)
    for path in (paths["draft"], paths["current"]):
        data = _read_json(path)
        if isinstance(data, dict) and data.get("fields"):
            return normalize_schema(data)
    return {"fields": []}


def save_schema_draft(project_path: Path | str, schema: dict[str, Any]) -> dict[str, Any]:
    paths = schema_paths(project_path)
    normalized = normalize_schema(schema)
    if not normalized["fields"]:
        raise ValueError("extraction schema must include at least one field")
    paths["extraction_dir"].mkdir(parents=True, exist_ok=True)
    _write_json(paths["draft"], normalized)
    _write_json(paths["current"], normalized)
    if paths["finalized"].exists():
        paths["finalized"].unlink()
    return normalized


def is_schema_finalized(project_path: Path | str) -> bool:
    paths = schema_paths(project_path)
    if paths["finalized"].exists():
        return True
    if paths["draft"].exists():
        return False
    schema = _read_json(paths["current"])
    return bool(
        isinstance(schema, dict)
        and schema.get("fields")
        and _has_jsonl_object(paths["results"])
    )


def add_schema_field(project_path: Path | str, field: dict[str, Any]) -> dict[str, Any]:
    schema = load_schema_draft(project_path)
    new_field = normalize_field(field)
    names = {item["name"] for item in schema["fields"]}
    if new_field["name"] in names:
        raise ValueError(f"extraction schema field already exists: {new_field['name']}")
    schema["fields"].append(new_field)
    return save_schema_draft(project_path, schema)


def remove_schema_field(project_path: Path | str, field_name: str) -> dict[str, Any]:
    schema = load_schema_draft(project_path)
    target = _field_name(field_name)
    if target not in {field["name"] for field in schema["fields"]}:
        raise ValueError(f"extraction schema field not found: {target}")
    schema["fields"] = [field for field in schema["fields"] if field["name"] != target]
    return save_schema_draft(project_path, schema)


def modify_schema_field(project_path: Path | str, field_name: str, updates: dict[str, Any]) -> dict[str, Any]:
    schema = load_schema_draft(project_path)
    target = _field_name(field_name)
    new_name = _field_name(updates["new_name"]) if updates.get("new_name") else ""
    if new_name and new_name != target and new_name in {field["name"] for field in schema["fields"]}:
        raise ValueError(f"extraction schema field already exists: {new_name}")
    updated = False
    for field in schema["fields"]:
        if field["name"] != target:
            continue
        if new_name:
            field["name"] = new_name
        if updates.get("new_type"):
            field["type"] = str(updates["new_type"]).strip() or field["type"]
        if updates.get("new_description"):
            field["description"] = str(updates["new_description"]).strip()
        if updates.get("new_example"):
            field["example"] = str(updates["new_example"]).strip()
        if "new_required" in updates:
            field["required"] = _bool_value(updates["new_required"])
        updated = True
        break
    if not updated:
        raise ValueError(f"extraction schema field not found: {target}")
    return save_schema_draft(project_path, schema)


def finalize_schema(project_path: Path | str) -> dict[str, Any]:
    project = Path(project_path)
    paths = schema_paths(project)
    schema = load_schema_draft(project)
    if not schema["fields"]:
        raise ValueError("cannot finalize extraction schema without fields")
    config = _read_json(paths["search_conditions"]) or {}
    system_prompt, extraction_prompt, user_prompt_template = build_extraction_prompts(config, schema)
    paths["extraction_dir"].mkdir(parents=True, exist_ok=True)
    paths["architecture_prompt"].parent.mkdir(parents=True, exist_ok=True)
    _write_json(paths["current"], schema)
    _write_json(
        paths["prompt"],
        {
            "system_prompt": system_prompt,
            "extraction_prompt": extraction_prompt,
            "schema": schema,
            "source": "user_finalized_schema",
        },
    )
    _write_json(
        paths["architecture_prompt"],
        {
            "prompt_type": "extraction",
            "system_prompt": system_prompt,
            "user_prompt_template": user_prompt_template,
            "extraction_fields": ", ".join(field["name"] for field in schema["fields"]),
            "output_structured": True,
            "schema": schema,
            "source": "user_finalized_schema",
            "generated_at": datetime.now().isoformat(),
        },
    )
    marker = {"finalized_at": datetime.now().isoformat(), "field_count": len(schema["fields"])}
    _write_json(paths["finalized"], marker)
    return {"status": "schema_finalized", "field_count": len(schema["fields"]), "schema": schema}


def build_extraction_prompts(config: dict[str, Any], schema: dict[str, Any]) -> tuple[str, str, str]:
    scope = {
        "research_description": config.get("description") or config.get("research_description") or "",
        "primary_topic": config.get("primary_topic") or "",
        "domain": config.get("domain") or "",
    }
    system_prompt = "You extract source-grounded structured evidence from scholarly papers into a supplied review schema."
    extraction_prompt = f"""RESEARCH SCOPE DATA:
{json.dumps(scope, ensure_ascii=False)}

FINALIZED EXTRACTION SCHEMA:
{json.dumps(schema, ensure_ascii=False)}

Populate every declared schema field from the supplied paper evidence. Preserve reported units, denominators, time points, comparison groups, and uncertainty. Use an empty string when the supplied evidence does not support a field. Return exactly one JSON object containing every declared field name and no additional fields."""
    user_prompt_template = f"{extraction_prompt}\n\nPAPER EVIDENCE DATA:\n{{paper_text}}\n\nJSON response:"
    return system_prompt, extraction_prompt, user_prompt_template


def normalize_schema(raw: dict[str, Any]) -> dict[str, Any]:
    fields = []
    for item in raw.get("fields") or []:
        try:
            field = normalize_field(item)
        except ValueError:
            continue
        fields.append(field)
    return {"fields": fields}


def normalize_field(raw: dict[str, Any]) -> dict[str, Any]:
    name = _field_name(raw.get("name") or raw.get("field_name") or "")
    if not name:
        raise ValueError("extraction schema field name is required")
    return {
        "name": name,
        "type": str(raw.get("type") or raw.get("new_type") or "Text").strip() or "Text",
        "description": str(raw.get("description") or raw.get("new_description") or raw.get("example") or "").strip(),
        "required": _bool_value(raw.get("required", False)),
        "example": str(raw.get("example") or raw.get("new_example") or "").strip(),
    }


def _field_name(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"(?<!^)(?=[A-Z])", "_", text)
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return re.sub(r"_+", "_", text)


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "required"}
    return bool(value)


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _has_jsonl_object(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return True
    except OSError:
        return False
    return False


def _write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write_json(path, data)
