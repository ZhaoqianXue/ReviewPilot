"""Project output folder to frontend RP_DATA projection."""

from __future__ import annotations

from datetime import datetime
from html import unescape
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote

from ui_state import project_stage_label, schema_workbench_state

from .extraction_schema import is_schema_finalized, load_schema_draft
from .model_policy import DEFAULT_MAX_RESULTS_PER_PLATFORM, LEAD_AGENT_DEV_MODEL
from .project_store import count_jsonl, iter_project_dirs, project_dir, read_json, read_jsonl
from .safe_text import contains_absolute_path, safe_display_text
from .setup_revision import read_consistent_setup, reconcile_setup_transaction, setup_revision
from .workflow_state import STAGE_NAMES, load_workflow_state, new_workflow_state, reconcile_orphaned_running


NEW_REVIEW_WELCOME = """Welcome to **ReviewPilot**!

ReviewPilot helps you turn a research topic into a literature review:

| Step | Description |
|------|-------------|
| **1. Search Setup** | Define your research topic and search parameters |
| **2. Paper Screening** | Collect papers, filter, and check relevance |
| **3. Full-Text Retrieval** | Download full-text PDFs for included papers |
| **4. Information Extraction** | Extract structured information from papers |
| **5. Categorization & Analysis** | Generate the final Categorization & Analysis report |

**Choose a starter topic on the canvas, or describe your own topic in the chat.**"""


STEP_DEFS = [
    ("search", "Search Setup", "5 sources"),
    ("screening", "Paper Screening", "0 / 0"),
    ("retrieval", "Full-Text Retrieval", "0 / 0"),
    ("extraction", "Information Extraction", "0 fields"),
    ("categorize", "Categorization & Analysis", "0 groups"),
]

DEMO_HISTORY_DIRECTIONS = [
    {
        "label": "LLM for Biomedical",
        "id_prefixes": ("qa-live-llm-biomedical",),
        "keywords": ("biomedical", "biomedicine"),
        "starter_topic": "I want to review how LLMs are used in biomedical research and clinical care.",
    },
    {
        "label": "LLM for HCI",
        "id_prefixes": ("qa-live-llm-hci",),
        "keywords": ("human-computer", "hci"),
        "starter_topic": "I want to review how LLMs are changing human-computer interaction.",
    },
    {
        "label": "LLM for Urban",
        "id_prefixes": ("qa-live-llm-urban",),
        "keywords": ("urban",),
        "starter_topic": "I want to review how LLMs support urban planning and smart cities.",
    },
]

METADATA_FIELDS = {
    "paper_id",
    "title",
    "authors",
    "year",
    "doi",
    "source",
    "pdf_path",
    "title_match",
    "title_similarity",
    "extraction_source",
    "url",
    "abstract",
}

EXPORT_ARTIFACTS = {
    "search-setup": ("Search setup", Path("search_conditions.json"), "application/json"),
    "relevance-prompt": ("Relevance prompt", Path("prompts/relevance_prompt.json"), "application/json"),
    "included-papers": ("Included papers", Path("filtered/included_papers.jsonl"), "application/x-ndjson"),
    "download-report": ("Download report", Path("pdfs/download_report.json"), "application/json"),
    "extraction-results": ("Extraction results", Path("extraction/extraction_results.jsonl"), "application/x-ndjson"),
    "categorization-mapping": ("Categorization mapping", Path("categorization/categorization_mapping.json"), "application/json"),
    "categorized-results": ("Categorized results", Path("categorization/categorized_results.jsonl"), "application/x-ndjson"),
}


def export_artifact_path(project_path: Path, export_key: str) -> Path | None:
    reconcile_setup_transaction(project_path)
    artifact = EXPORT_ARTIFACTS.get(export_key)
    if not artifact or project_path.is_symlink() or (project_path / ".setup_update_pending.json").exists():
        return None
    export_stage = {
        "relevance-prompt": "collection", "included-papers": "screening", "download-report": "retrieval",
        "extraction-results": "extraction", "categorization-mapping": "categorization", "categorized-results": "categorization",
    }.get(export_key)
    if export_stage:
        try:
            if load_workflow_state(project_path)["stages"][export_stage]["stale"]:
                return None
        except ValueError:
            return None
    # Inner-beta is a single-user local app. Reject every link component immediately
    # before resolution; descriptor-level no-follow serving would require replacing
    # FileResponse and is disproportionate to this deployment's TOCTOU risk.
    candidate = project_path
    for part in artifact[1].parts:
        candidate /= part
        if candidate.is_symlink():
            return None
    try:
        project_root = project_path.resolve(strict=True)
        candidate = candidate.resolve(strict=True)
    except OSError:
        return None
    if not candidate.is_relative_to(project_root) or not candidate.is_file():
        return None
    return candidate


def list_projects(output_root: Path | str = Path("output")) -> list[dict]:
    projects = []
    for path in iter_project_dirs(Path(output_root)):
        config = _unescape_strings(read_consistent_setup(path))
        projects.append(
            {
                "id": path.name,
                "title": config.get("project_name") or path.name,
                "path": str(path),
                "modified": _format_mtime(path),
            }
        )
    return projects


def build_new_project_data(output_root: Path | str) -> dict:
    return {
        "isNewProject": True,
        "project": {
            "id": "",
            "title": "Untitled review",
            "status": "Draft · Search Setup",
            "model": LEAD_AGENT_DEV_MODEL,
            "date": _format_mtime(Path(output_root)),
        },
        "researchQuestion": "",
        "setup": {
            "project_name": "",
            "description": "",
            "primary_topic": "",
            "domain": "",
            "search_terms": "",
            "platforms": ["pubmed", "arxiv", "openalex"],
            "max_results": DEFAULT_MAX_RESULTS_PER_PLATFORM,
            "source_limits": {
                "pubmed": DEFAULT_MAX_RESULTS_PER_PLATFORM,
                "arxiv": DEFAULT_MAX_RESULTS_PER_PLATFORM,
                "openalex": DEFAULT_MAX_RESULTS_PER_PLATFORM,
            },
            "date_start": "",
            "date_end": "",
            "model": LEAD_AGENT_DEV_MODEL,
            "derive_search_terms": False,
        },
        "setupRevision": "",
        "stageState": new_workflow_state()["stages"],
        "workflowNotices": {},
        "steps": [
            {"n": 1, "key": "search", "label": "Search Setup", "status": "active", "sub": "3 sources", "desc": ""},
            {"n": 2, "key": "screening", "label": "Paper Screening", "status": "todo", "sub": "0 / 0", "desc": ""},
            {"n": 3, "key": "retrieval", "label": "Full-Text Retrieval", "status": "todo", "sub": "0 / 0", "desc": ""},
            {"n": 4, "key": "extraction", "label": "Information Extraction", "status": "todo", "sub": "0 fields", "desc": ""},
            {"n": 5, "key": "categorize", "label": "Categorization & Analysis", "status": "todo", "sub": "0 groups", "desc": ""},
        ],
        "optionalCapabilities": [],
        "fields": [],
        "platforms": [["PubMed", 0], ["arXiv", 0], ["Openalex", 0]],
        "keywords": [],
        "groups": [],
        "retrieved": [],
        "platformIssues": [],
        "screeningMetrics": {"identified": 0, "afterDedup": 0, "included": 0},
        "retrievalSummary": {"retrieved": 0, "total": 0, "openAccess": 0, "viaInstitution": 0, "unavailable": 0},
        "categorizationSummary": {"papers": 0, "groups": 0},
        "categorizationWorkflow": _empty_categorization_workflow(),
        "resultOverview": [],
        "evidenceMatrix": [],
        "categorizationAnalysis": {"field": "", "categories": []},
        "exportPackage": [],
        "previewFields": [],
        "previewPaper": {"title": "No paper preview available", "ref": "", "countLabel": "0 / 0"},
        "messages": [
            {
                "step": 1,
                "role": "a",
                "text": NEW_REVIEW_WELCOME,
            }
        ],
        "activityByStep": {
            "search": [{"t": "--:--:--", "tag": "setup", "msg": "waiting for search configuration"}],
            "screening": [{"t": "--:--:--", "tag": "screening", "msg": "waiting for collection"}],
            "retrieval": [{"t": "--:--:--", "tag": "retrieval", "msg": "waiting for included papers"}],
            "extraction": [{"t": "--:--:--", "tag": "schema", "msg": "waiting for PDFs"}],
            "categorize": [{"t": "--:--:--", "tag": "categorize", "msg": "waiting for extraction"}],
        },
        "quietLabels": {},
        "quietActions": {},
        "ctxLabels": {
            "search": "Describe research topic",
            "screening": "Waiting for collection",
            "retrieval": "Waiting for screening",
            "extraction": "Waiting for retrieval",
            "categorize": "Waiting for extraction",
        },
        "history": _new_project_history(Path(output_root)),
    }


def build_rp_data(output_root: Path | str, project_id: str, active_action: str | None = None) -> dict:
    root = Path(output_root)
    path = project_dir(root, project_id)
    config = _unescape_strings(read_consistent_setup(path))
    collected_summary = _unescape_strings(read_json(path / "collected" / "summary.json", {}) or {})
    filtering_stats = _unescape_strings(read_json(path / "filtered" / "filtering_stats.json", {}) or {})
    screening_stats = _unescape_strings(read_json(path / "filtered" / "screening_stats.json", {}) or {})
    included = _unescape_strings(read_jsonl(path / "filtered" / "included_papers.jsonl"))
    download_report = _unescape_strings(read_json(path / "pdfs" / "download_report.json", {}) or {})
    schema = _unescape_strings(load_schema_draft(path))
    extraction_rows, extraction_failed_items = _extraction_snapshot(path / "extraction" / "extraction_results.jsonl")
    extraction_rows = _unescape_strings(extraction_rows)
    extraction_failed_items = _unescape_strings(extraction_failed_items)
    extraction_results = extraction_rows[:1]
    categorization = _unescape_strings(read_json(path / "categorization" / "categorization_mapping.json", {}) or {})
    categorization_suggestions = _unescape_strings(read_json(path / "categorization" / "suggested_categories.json", {}) or {})
    categorized_rows = _unescape_strings(read_jsonl(path / "categorization" / "categorized_results.jsonl", limit=200))

    workflow_state = reconcile_orphaned_running(path, active_action=active_action)
    workflow_notices = _workflow_notices(path, workflow_state, collected_summary, download_report, extraction_failed_items)
    setup_update_pending = (path / ".setup_update_pending.json").exists()
    if setup_update_pending:
        for stage in workflow_state["stages"].values():
            stage["stale"] = True
    if workflow_state["stages"]["collection"]["stale"]:
        collected_summary = {}
    if workflow_state["stages"]["screening"]["stale"]:
        filtering_stats, screening_stats, included = {}, {}, []
    if workflow_state["stages"]["retrieval"]["stale"]:
        download_report = {}
    if workflow_state["stages"]["extraction"]["stale"]:
        if not _fresh_ready_output(workflow_state["stages"]["extraction"]):
            schema = {}
        extraction_rows, extraction_results = [], []
    if workflow_state["stages"]["categorization"]["stale"]:
        categorization, categorized_rows = {}, []
        if not _fresh_ready_output(workflow_state["stages"]["categorization"]):
            categorization_suggestions = {}
    current_step = _current_step(workflow_state)
    stage = project_stage_label(current_step)
    fields = _schema_fields(schema)
    extraction_stage = workflow_state["stages"]["extraction"]
    schema_finalized = (not extraction_stage["stale"] or _fresh_ready_output(extraction_stage)) and is_schema_finalized(path)
    platform_stats = _platform_stats(path, config, collected_summary, allow_artifact_fallback=not workflow_state["stages"]["collection"]["stale"])

    return {
        "isNewProject": False,
        "project": {
            "id": project_id,
            "title": config.get("project_name") or path.name,
            "status": f"Active · {stage}",
            "model": config.get("model") or LEAD_AGENT_DEV_MODEL,
            "date": _format_mtime(path),
        },
        "researchQuestion": _research_question(config),
        "setup": _setup(config),
        "setupRevision": setup_revision(config),
        "stageState": workflow_state["stages"],
        "workflowNotices": workflow_notices,
        "steps": _steps(path, workflow_state, current_step, config, collected_summary, screening_stats, included, download_report, fields, categorization),
        "optionalCapabilities": [],
        "fields": fields,
        "schemaWorkbench": schema_workbench_state(has_schema=bool(fields), schema_finalized=schema_finalized),
        "platforms": platform_stats,
        "platformIssues": _platform_issues(collected_summary.get("platform_errors") or {}),
        "keywords": _keywords(config),
        "groups": _groups(categorization),
        "retrieved": _retrieved(included),
        "screeningMetrics": _screening_metrics(collected_summary, filtering_stats, screening_stats, included),
        "retrievalSummary": _retrieval_summary(path, included, download_report, allow_artifact_fallback=not workflow_state["stages"]["retrieval"]["stale"]),
        "categorizationSummary": _categorization_summary(categorization),
        "categorizationWorkflow": _categorization_workflow(schema, extraction_rows, categorization, categorization_suggestions, categorized_rows),
        "resultOverview": _result_overview(path, config, collected_summary, screening_stats, included, download_report, extraction_rows, categorization, allow_retrieval_fallback=not workflow_state["stages"]["retrieval"]["stale"]),
        "evidenceMatrix": _evidence_matrix(extraction_rows, included, categorized_rows),
        "categorizationAnalysis": _categorization_analysis(categorization, categorized_rows, extraction_rows),
        "exportPackage": _export_package(path),
        "previewFields": _preview_fields(extraction_results[0] if extraction_results else {}),
        "previewPaper": _preview_paper(extraction_results[0] if extraction_results else {}, included),
        "messages": _messages(path, config, collected_summary, screening_stats, included, download_report, fields, extraction_results, categorization, workflow_notices=workflow_notices, extraction_stale=workflow_state["stages"]["extraction"]["stale"], extraction_status=workflow_state["stages"]["extraction"]["status"]),
        "activityByStep": _activity_by_step(path, collected_summary, screening_stats, included, download_report, fields, categorization, workflow_state, workflow_notices),
        "quietLabels": _quiet_labels(path, workflow_state),
        "quietActions": _quiet_actions(path, workflow_state),
        "ctxLabels": _ctx_labels(current_step),
        "history": _history(root, project_id),
    }


def _format_mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%b %d, %Y")
    except OSError:
        return datetime.now().strftime("%b %d, %Y")


def _extraction_snapshot(path: Path) -> tuple[list[dict], list[str]]:
    """Read extraction output once, bounding retained display and recovery data."""
    rows: list[dict] = []
    failed_items: list[str] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                if len(rows) < 200:
                    rows.append(row)
                if len(failed_items) < 10 and str(row.get("extraction_status") or "").lower() in {"error", "failed"}:
                    failed_items.append(_safe_failed_item(row))
                if len(rows) >= 200 and len(failed_items) >= 10:
                    break
    except OSError:
        pass
    return rows, failed_items


def _unescape_strings(value: Any) -> Any:
    if isinstance(value, str):
        return unescape(value)
    if isinstance(value, list):
        return [_unescape_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _unescape_strings(item) for key, item in value.items()}
    return value


def _current_step(workflow_state: dict) -> int:
    stages = workflow_state["stages"]
    exceptional = [index for index, name in enumerate(STAGE_NAMES, start=1) if stages[name]["status"] in {"running", "failed"}]
    if exceptional:
        return exceptional[-1]
    stale = [index for index, name in enumerate(STAGE_NAMES, start=1) if stages[name]["stale"]]
    if stale:
        return stale[0]
    completed = [index for index, name in enumerate(STAGE_NAMES, start=1) if stages[name]["status"] in {"completed", "partial"}]
    return min((max(completed) + 1) if completed else 1, len(STAGE_NAMES))


def _fresh_ready_output(stage: dict) -> bool:
    last_valid = stage.get("last_valid") or {}
    return stage.get("status") == "ready" and stage.get("attempt", 0) > last_valid.get("attempt", 0)


def _steps(
    path: Path,
    workflow_state: dict,
    current_step: int,
    config: dict,
    collected_summary: dict,
    screening_stats: dict,
    included: list[dict],
    download_report: dict,
    fields: list[list[Any]],
    categorization: dict,
) -> list[dict]:
    subs = {
        "search": f"{len(config.get('platforms') or []) or len(collected_summary.get('platform_stats') or {})} sources",
        "screening": f"{screening_stats.get('included_count', len(included))} / {collected_summary.get('total_papers', 0)}",
        "retrieval": f"{_download_success(path, download_report)} / {len(included)}",
        "extraction": f"{len(fields)} fields",
        "categorize": f"{_categorization_summary(categorization)['groups']} groups",
    }
    steps = []
    for index, (key, label, fallback_sub) in enumerate(STEP_DEFS, start=1):
        stage_name = STAGE_NAMES[index - 1]
        stage_state = workflow_state["stages"][stage_name]
        if stage_state["status"] == "failed":
            status = "failed"
        elif stage_state["stale"]:
            status = "stale"
        elif stage_state["status"] == "completed":
            status = "done"
        elif stage_state["status"] == "partial":
            status = "partial"
        elif index == current_step:
            status = "active"
        else:
            status = "todo"
        steps.append(
            {
                "n": index,
                "key": key,
                "label": label,
                "status": status,
                "sub": "Needs rerun" if stage_state["stale"] else (subs.get(key) or fallback_sub),
                "desc": "",
            }
        )
    return steps


def _download_success(path: Path, download_report: dict, *, allow_artifact_fallback: bool = True) -> int:
    for key in ("success", "successful", "downloaded", "success_count"):
        value = download_report.get(key)
        if isinstance(value, int):
            return value
    return len(list((path / "pdfs").glob("*.pdf"))) if allow_artifact_fallback and (path / "pdfs").exists() else 0


def _platform_stats(path: Path, config: dict, collected_summary: dict, *, allow_artifact_fallback: bool = True) -> list[list[Any]]:
    stats = collected_summary.get("platform_stats") or {}
    if not stats and allow_artifact_fallback:
        collected_dir = path / "collected"
        for platform in config.get("platforms") or []:
            stats[platform] = count_jsonl(collected_dir / f"{platform}.jsonl")
    if not stats:
        stats = {platform: 0 for platform in config.get("platforms") or []}
    return [[_platform_label(key), int(value or 0)] for key, value in stats.items()]


def _platform_label(key: str) -> str:
    if contains_absolute_path(str(key)):
        return "Unknown source"
    labels = {"pubmed": "PubMed", "arxiv": "arXiv", "openalex": "Openalex"}
    return labels.get(key, str(key).replace("_", " ").title())


def _platform_issues(platform_errors: dict) -> list[dict[str, str]]:
    if not isinstance(platform_errors, dict):
        return []
    issues = []
    for platform, message in platform_errors.items():
        text = safe_display_text(str(message), fallback="Source error details hidden.")
        if not text:
            continue
        issues.append(
            {
                "platform": safe_display_text(str(platform), fallback="unknown"),
                "label": _platform_label(str(platform)),
                "message": text,
                "severity": "warning",
            }
        )
    return issues


def _keywords(config: dict) -> list[str]:
    candidates: list[str] = []
    candidates.extend(_descriptive_keyword_terms(str(config.get("primary_topic") or "")))
    candidates.extend(_descriptive_keyword_terms(str(config.get("domain") or "")))

    queries = config.get("search_queries") or []
    for query in queries:
        candidates.extend(_keyword_terms(str(query.get("query") or query.get("name") or "")))
    candidates.extend(_keyword_terms(str(config.get("search_terms") or "")))

    seen: set[str] = set()
    keywords: list[str] = []
    for candidate in candidates:
        label = candidate.strip(" ()'\"")
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        keywords.append(label)
        if len(keywords) >= 8:
            break
    return keywords


def _keyword_terms(text: str) -> list[str]:
    normalized = text.replace("\n", " ")
    parts = re.split(r"\s+(?:AND|OR|NOT)\s+|[,;]", normalized, flags=re.IGNORECASE)
    return [_clean_keyword(part) for part in parts if _clean_keyword(part)]


def _descriptive_keyword_terms(text: str) -> list[str]:
    cleaned = _clean_keyword(text)
    if not cleaned:
        return []

    lowered = cleaned.lower()
    terms: list[str] = []
    if "large language model" in lowered or "llm" in lowered:
        terms.append("large language model")
    if "biomedical informatics" in lowered:
        terms.append("biomedical informatics")
    if "health ai" in lowered:
        terms.append("health AI")
    if "biomedicine" in lowered:
        terms.append("biomedicine")
    if terms:
        return terms

    return [part for part in re.split(r"\s+(?:and|for|in)\s+", cleaned, flags=re.IGNORECASE) if 0 < len(part) <= 32]


def _clean_keyword(text: str) -> str:
    value = str(text or "").strip(" ()'\"")
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"^survey of\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*\([^)]*\)", "", value).strip()
    value = re.sub(r"^\"|\"$", "", value).strip()
    return value


def _setup(config: dict) -> dict:
    date_range = config.get("date_range") or {}
    platforms = list(config.get("platforms") or [])
    max_results = config.get("max_results") or DEFAULT_MAX_RESULTS_PER_PLATFORM
    return {
        "project_name": str(config.get("project_name") or ""),
        "description": str(config.get("description") or ""),
        "primary_topic": str(config.get("primary_topic") or ""),
        "domain": str(config.get("domain") or ""),
        "search_terms": str(config.get("search_terms") or _query_text(config) or ""),
        "platforms": platforms,
        "max_results": max_results,
        "source_limits": _source_limits(config, platforms, max_results),
        "date_start": str(date_range.get("start") or ""),
        "date_end": str(date_range.get("end") or ""),
        "model": str(config.get("model") or LEAD_AGENT_DEV_MODEL),
        "derive_search_terms": bool(config.get("derive_search_terms")),
    }


def _source_limits(config: dict, platforms: list[str], default: int) -> dict[str, int]:
    raw_limits = config.get("source_limits") or {}
    if not isinstance(raw_limits, dict):
        raw_limits = {}
    limits = {}
    for platform in platforms:
        try:
            parsed = int(raw_limits.get(platform, default))
        except (TypeError, ValueError):
            parsed = int(default)
        limits[platform] = parsed if parsed > 0 else int(default)
    return limits


def _query_text(config: dict) -> str:
    queries = config.get("search_queries") or []
    if not queries:
        return ""
    return str(queries[0].get("query") or "")


def _research_question(config: dict) -> str:
    return str(config.get("description") or config.get("search_terms") or config.get("primary_topic") or "Review project")


def _schema_fields(schema: dict) -> list[list[Any]]:
    fields = []
    for field in schema.get("fields") or []:
        name = str(field.get("name") or "")
        if not name:
            continue
        fields.append(
            [
                name,
                str(field.get("type") or "Text"),
                str(field.get("description") or field.get("example") or ""),
                bool(field.get("required", False)),
            ]
        )
    return fields


def _preview_fields(row: dict) -> list[dict]:
    preview = []
    for key, value in row.items():
        if key in METADATA_FIELDS or key.endswith("_category"):
            continue
        preview.append({"k": key, "v": str(value)})
    return preview[:12]


def _preview_paper(row: dict, included: list[dict]) -> dict:
    source = row if row else (included[0] if included else {})
    title = source.get("title") or "No paper preview available"
    ref_parts = []
    if source.get("source"):
        ref_parts.append(str(source["source"]))
    if source.get("year"):
        ref_parts.append(str(source["year"]))
    return {
        "title": str(title),
        "ref": " · ".join(ref_parts) if ref_parts else "extraction_results.jsonl",
        "countLabel": f"1 / {len(included)}" if included else "1 / 1",
    }


def _retrieved(included: list[dict]) -> list[dict]:
    rows = []
    for index, paper in enumerate(included[:8], start=1):
        title = paper.get("title") or f"Paper {index}"
        source = paper.get("source") or paper.get("platform") or "paper"
        year = paper.get("year") or paper.get("publication_year") or ""
        rows.append({"t": str(title), "v": f"{source}{f' · {year}' if year else ''}"})
    return rows


def _groups(categorization: dict) -> list[dict]:
    categories = _populated_category_names(categorization)
    descriptions = categorization.get("category_descriptions") or {}
    counts = _category_counts(categorization)
    return [{"name": cat, "n": counts.get(cat, 0), "desc": str(descriptions.get(cat, ""))} for cat in categories]


def _screening_metrics(collected_summary: dict, filtering_stats: dict, screening_stats: dict, included: list[dict]) -> dict:
    identified = int(collected_summary.get("total_papers") or screening_stats.get("initial") or filtering_stats.get("initial") or 0)
    after_dedup = int(screening_stats.get("after_dedup") or filtering_stats.get("after_dedup") or screening_stats.get("total_screened") or len(included))
    included_count = int(screening_stats.get("included_count") or len(included))
    return {"identified": identified, "afterDedup": after_dedup, "included": included_count}


def _retrieval_summary(path: Path, included: list[dict], download_report: dict, *, allow_artifact_fallback: bool = True) -> dict:
    retrieved = _download_success(path, download_report, allow_artifact_fallback=allow_artifact_fallback)
    total = len(included)
    failed = download_report.get("failed")
    unavailable = int(failed) if isinstance(failed, int) else max(total - retrieved, 0)
    return {"retrieved": retrieved, "total": total, "openAccess": retrieved, "viaInstitution": 0, "unavailable": unavailable}


def _categorization_summary(categorization: dict) -> dict:
    return {"papers": _categorized_paper_count(categorization), "groups": len(_populated_category_names(categorization))}


def _result_overview(
    path: Path,
    config: dict,
    collected_summary: dict,
    screening_stats: dict,
    included: list[dict],
    download_report: dict,
    extraction_rows: list[dict],
    categorization: dict,
    *,
    allow_retrieval_fallback: bool = True,
) -> list[dict[str, str]]:
    retrieval = _retrieval_summary(path, included, download_report, allow_artifact_fallback=allow_retrieval_fallback)
    categorization_summary = _categorization_summary(categorization)
    return [
        {"label": "Identified", "value": str(collected_summary.get("total_papers", 0))},
        {"label": "Screened", "value": str(screening_stats.get("total_screened") or screening_stats.get("included_count") or len(included))},
        {"label": "Included", "value": str(screening_stats.get("included_count") or len(included))},
        {"label": "PDFs retrieved", "value": f"{retrieval['retrieved']} / {retrieval['total']}"},
        {"label": "Subscribed/unavailable", "value": str(retrieval["unavailable"])},
        {"label": "Extracted", "value": str(len(extraction_rows))},
        {"label": "Categorized", "value": str(categorization_summary["papers"])},
        {"label": "Sources", "value": ", ".join(_platform_label(item) for item in config.get("platforms") or []) or "not set"},
        {"label": "Date range", "value": _date_range_label(config.get("date_range") or {})},
        {"label": "Search strategy", "value": str(config.get("search_terms") or _query_text(config) or "not set")},
    ]


def _date_range_label(date_range: dict) -> str:
    start = str(date_range.get("start") or date_range.get("start_date") or "").strip()
    end = str(date_range.get("end") or date_range.get("end_date") or "").strip()
    if start and end:
        return f"{start} to {end}"
    if start:
        return f"{start} to present"
    if end:
        return f"until {end}"
    return "all dates"


def _evidence_matrix(extraction_rows: list[dict], included: list[dict], categorized_rows: list[dict]) -> list[dict]:
    category_by_key = {}
    for row in categorized_rows:
        key = _paper_identity(row)
        if key and row.get("category"):
            category_by_key[key] = str(row["category"])

    rows = extraction_rows if extraction_rows else included
    matrix = []
    for index, row in enumerate(rows[:30], start=1):
        key = _paper_identity(row)
        extracted_fields = _evidence_fields(row)
        matrix.append(
            {
                "idx": index,
                "title": str(row.get("title") or f"Paper {index}"),
                "source": str(row.get("source") or row.get("platform") or ""),
                "year": str(row.get("year") or row.get("publication_year") or ""),
                "retrievalStatus": str(row.get("retrieval_status") or ("downloaded" if row.get("pdf_downloaded") else "")),
                "extractionSource": str(row.get("extraction_source") or ""),
                "category": str(row.get("category") or category_by_key.get(key, "")),
                "sourceUrls": _source_urls(row.get("source_urls") or row.get("sources") or []),
                "confidence": str(row.get("confidence") or ""),
                "fields": extracted_fields,
            }
        )
    return matrix


def _paper_identity(row: dict) -> str:
    return str(row.get("paper_id") or row.get("id") or row.get("doi") or row.get("title") or "").strip().casefold()


def _evidence_fields(row: dict) -> list[dict[str, str]]:
    fields = []
    hidden = {
        *METADATA_FIELDS,
        "id",
        "paper_id",
        "row_number",
        "pdf_file",
        "extracted_at",
        "extraction_model",
        "extraction_cost_usd",
        "extracted_data",
        "extraction_status",
        "error_message",
        "source_urls",
        "sources",
        "confidence",
        "category",
        "retrieval_status",
        "pdf_downloaded",
        "pdf_failure_class",
        "web_search_fallback_pending",
    }
    for key, value in row.items():
        if key in hidden or key.endswith("_category"):
            continue
        text = _compact_cell(value)
        if not text:
            continue
        fields.append({"name": str(key), "value": text})
        if len(fields) >= 4:
            break
    return fields


def _compact_cell(value: Any, *, limit: int = 900) -> str:
    if value in (None, ""):
        return ""
    text = _display_cell_value(value)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    clipped = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{clipped} ..."


def _display_cell_value(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, list):
        parts = [_display_cell_value(item) for item in value]
        return "; ".join(part for part in parts if part)
    if isinstance(value, dict):
        parts = []
        for key, nested_value in value.items():
            if _is_display_metadata_key(key):
                continue
            nested_text = _display_cell_value(nested_value)
            if nested_text:
                parts.append(f"{_display_key(key)}: {nested_text}")
        return "; ".join(parts)
    return str(value)


def _display_key(value: Any) -> str:
    return str(value).strip().replace("_", " ")


def _is_display_metadata_key(value: Any) -> bool:
    return str(value).strip().lower() in {
        "source_url",
        "source_urls",
        "sources",
        "confidence",
        "extraction_source",
        "extraction_status",
        "pdf_failure_class",
        "retrieval_status",
        "web_search_fallback_pending",
    }


def _source_urls(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = []
    urls = []
    for item in candidates:
        text = str(item.get("url") if isinstance(item, dict) else item).strip()
        if text and text not in urls:
            urls.append(text)
    return urls[:4]


def _categorization_analysis(categorization: dict, categorized_rows: list[dict], extraction_rows: list[dict]) -> dict:
    field = str(categorization.get("field") or "category")
    descriptions = categorization.get("category_descriptions") or {}
    categories = _populated_category_names(categorization)
    rows = categorized_rows if categorized_rows else extraction_rows
    category_items = []
    for category in categories:
        name = str(category)
        papers = [row for row in rows if name in _row_category_values(categorization, row)]
        category_items.append(
            {
                "name": name,
                "description": str(descriptions.get(name, "")),
                "count": len(papers),
                "papers": [
                    {
                        "title": str(paper.get("title") or "Untitled paper"),
                        "evidence": _category_evidence(paper, field),
                    }
                    for paper in papers[:5]
                ],
            }
        )
    return {"field": field, "categories": category_items}


def _row_category_values(categorization: dict, row: dict) -> list[str]:
    known_categories = _category_names(categorization)
    direct_values = _category_values(row.get("category"), known_categories)
    mapped_values = _category_values(_paper_mapped_category(categorization, row), known_categories)
    values = []
    for value in [*direct_values, *mapped_values]:
        if value and value not in values:
            values.append(value)
    return values


def _category_evidence(paper: dict, field: str) -> str:
    for value in (
        paper.get(field),
        paper.get("key_findings"),
        paper.get("finding"),
        paper.get("findings"),
        paper.get("abstract"),
    ):
        text = _compact_cell(value)
        if text:
            return text
    return ""


def _empty_categorization_workflow() -> dict[str, Any]:
    return {
        "done": False,
        "metrics": {"papersExtracted": 0, "fields": 0},
        "fieldNames": [],
        "recommendedField": "",
        "selectedField": "",
        "mode": "multiple",
        "suggestedCategories": [],
        "categoryDescriptions": {},
        "fieldProfiles": {},
        "analysisDistributions": [],
        "categorizedDistribution": {"field": "", "values": []},
        "fullResults": {"columns": [], "rows": []},
    }


def _categorization_workflow(
    schema: dict,
    extraction_rows: list[dict],
    categorization: dict,
    suggestions: dict,
    categorized_rows: list[dict],
) -> dict[str, Any]:
    field_names = _categorization_field_names(schema, extraction_rows)
    recommended = _recommended_categorization_field(field_names)
    selected = str(categorization.get("field") or suggestions.get("field") or recommended or (field_names[0] if field_names else ""))
    mode = str(categorization.get("mode") or suggestions.get("mode") or "multiple")
    rows_for_analysis = categorized_rows if categorized_rows else extraction_rows
    field_profiles = {field: _field_profile(extraction_rows, field) for field in field_names}
    categorized_field = f"{selected}_category" if selected else ""
    return {
        "done": bool(categorization),
        "metrics": {"papersExtracted": len(extraction_rows), "fields": len(field_names)},
        "fieldNames": field_names,
        "recommendedField": recommended,
        "selectedField": selected,
        "mode": "single" if mode == "single" else "multiple",
        "suggestedCategories": _category_names(categorization) or _category_names(suggestions),
        "categoryDescriptions": categorization.get("category_descriptions") or categorization.get("descriptions") or suggestions.get("category_descriptions") or suggestions.get("descriptions") or {},
        "fieldProfiles": field_profiles,
        "analysisDistributions": [{"field": field, "values": _distribution(rows_for_analysis, field)} for field in field_names[:5]],
        "categorizedDistribution": {
            "field": categorized_field or "category",
            "values": _distribution(rows_for_analysis, categorized_field if categorized_field and any(categorized_field in row for row in rows_for_analysis) else "category"),
        },
        "fullResults": _full_results(rows_for_analysis, field_names, categorized_field),
    }


def _categorization_field_names(schema: dict, rows: list[dict]) -> list[str]:
    names = [field[0] for field in _schema_fields(schema)]
    for row in rows:
        for key, value in row.items():
            if key in METADATA_FIELDS or key.endswith("_category") or key in {"id", "row_number", "extracted_data", "source_urls", "confidence", "extraction_status", "extraction_model", "extraction_cost_usd", "extracted_at", "error_message"}:
                continue
            if value in (None, "") or key in names:
                continue
            names.append(str(key))
    return names


def _recommended_categorization_field(field_names: list[str]) -> str:
    priority = [
        "model_used",
        "llm_judge_model",
        "techniques",
        "llm_techniques",
        "clinical_task",
        "dataset",
        "data_modality",
        "methodology",
        "methods",
        "datasets_used",
        "task_or_application",
    ]
    for field in priority:
        if field in field_names:
            return field
    return field_names[0] if field_names else ""


def _field_profile(rows: list[dict], field: str) -> dict[str, Any]:
    values = [_profile_value(row.get(field)) for row in rows]
    values = [value for value in values if value]
    seen = set()
    sample_values = []
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        sample_values.append(value)
        if len(sample_values) >= 10:
            break
    return {
        "papersWithValue": len(values),
        "uniqueValuesCount": len(set(value.casefold() for value in values)),
        "sampleValues": sample_values,
        "distribution": _distribution_from_values(values),
    }


def _profile_value(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = _compact_cell(value, limit=240)
    return "" if text == "None" else text


def _distribution(rows: list[dict], field: str) -> list[dict[str, Any]]:
    values: list[str] = []
    for row in rows:
        value = row.get(field)
        if isinstance(value, list):
            values.extend(_profile_value(item) for item in value)
        else:
            values.append(_profile_value(value))
    return _distribution_from_values([value for value in values if value])


def _distribution_from_values(values: list[str]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for value in values:
        key = value.casefold()
        counts[key] = counts.get(key, 0) + 1
        labels.setdefault(key, value)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], labels[item[0]].casefold()))
    return [{"label": labels[key], "count": count} for key, count in ordered[:10]]


def _full_results(rows: list[dict], field_names: list[str], categorized_field: str) -> dict[str, Any]:
    columns = ["title", *field_names]
    if categorized_field and any(categorized_field in row for row in rows):
        columns.append(categorized_field)
    elif any("category" in row for row in rows):
        columns.append("category")
    normalized_rows = []
    for row in rows[:50]:
        normalized_rows.append({column: _profile_value(row.get(column)) for column in columns})
    return {"columns": columns, "rows": normalized_rows}


def _paper_mapped_category(categorization: dict, row: dict) -> str:
    mapping = categorization.get("mapping") or {}
    for key in (row.get("title"), row.get("paper_id"), row.get("id")):
        if key and key in mapping:
            return str(mapping[key])
    return ""


def _export_package(path: Path) -> list[dict[str, Any]]:
    project_id = quote(path.name, safe="")
    return [
        {
            "key": key,
            "label": label,
            "downloadUrl": f"/projects/{project_id}/exports/{key}",
            "exists": export_artifact_path(path, key) is not None,
        }
        for key, (label, _relative_path, _media_type) in EXPORT_ARTIFACTS.items()
    ]


def _quiet_labels(path: Path, workflow_state: dict | None = None) -> dict[str, str]:
    extraction_done = (path / "extraction" / "extraction_results.jsonl").exists() and not (workflow_state and workflow_state["stages"]["extraction"]["stale"])
    labels = {
        "search": "Run collection",
        "screening": "Run screening",
        "retrieval": "Download PDFs",
        "extraction": "Run extraction",
    }
    if extraction_done:
        labels["categorize"] = "Apply categorization"
    return labels


def _quiet_actions(path: Path, workflow_state: dict | None = None) -> dict[str, str]:
    extraction_done = (path / "extraction" / "extraction_results.jsonl").exists() and not (workflow_state and workflow_state["stages"]["extraction"]["stale"])
    actions = {
        "search": "collect",
        "screening": "screen",
        "retrieval": "download-pdfs",
        "extraction": "run-extraction",
    }
    if extraction_done:
        actions["categorize"] = "categorize"
    return actions


def _category_counts(categorization: dict) -> dict:
    counts: dict[str, int] = {}
    known_categories = _category_names(categorization)
    mapping = categorization.get("mapping") or {}
    if isinstance(mapping, dict):
        for category in mapping.values():
            for item in _category_values(category, known_categories):
                counts[item] = counts.get(item, 0) + 1
    return counts


def _category_names(categorization: dict) -> list[str]:
    return [str(item).strip() for item in (categorization.get("categories") or categorization.get("confirmed_categories") or []) if str(item).strip()]


def _populated_category_names(categorization: dict) -> list[str]:
    counts = _category_counts(categorization)
    names = [category for category in _category_names(categorization) if counts.get(category, 0) > 0]
    for category, count in counts.items():
        if count > 0 and category not in names:
            names.append(category)
    return names


def _categorized_paper_count(categorization: dict) -> int:
    known_categories = _category_names(categorization)
    mapping = categorization.get("mapping") or {}
    if isinstance(mapping, dict):
        return sum(1 for category in mapping.values() if _category_values(category, known_categories))
    return 0


def _category_values(value, known_categories: list[str]) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    if text in known_categories:
        return [text]
    matched = [category for category in known_categories if category and category in text]
    if matched:
        return matched
    if ";" in text or "|" in text:
        return [part.strip() for part in re.split(r"[;|]", text) if part.strip()]
    return [text]


def _messages(
    path: Path,
    config: dict,
    collected_summary: dict,
    screening_stats: dict,
    included: list[dict],
    download_report: dict,
    fields: list[list[Any]],
    extraction_results: list[dict],
    categorization: dict,
    *,
    workflow_notices: dict[str, dict[str, Any]] | None = None,
    extraction_stale: bool = False,
    extraction_status: str = "not_started",
) -> list[dict]:
    description = _initial_user_topic(path, config)
    messages = [
        {"step": 1, "role": "a", "text": NEW_REVIEW_WELCOME},
        {"step": 1, "role": "u", "text": str(description)},
    ]
    if config.get("lead_agent_reply"):
        messages.append(
            {
                "step": 1,
                "role": "a",
                "text": str(config.get("lead_agent_reply")),
            }
        )
    messages.extend(_workflow_outcome_messages(workflow_notices or {}))
    has_extraction_outcome = "extraction" in (workflow_notices or {})
    if categorization:
        messages.append({"step": 5, "role": "a", "text": "The final Categorization & Analysis report is ready."})
    elif extraction_status == "failed" and not has_extraction_outcome:
        messages.append({"step": 4, "role": "a", "text": "Information Extraction failed with no successful outputs. Recover the failed items before continuing."})
    elif extraction_status == "partial" and not has_extraction_outcome:
        messages.append({"step": 4, "role": "a", "text": "Information Extraction partially completed. Successful outputs are available for Categorization & Analysis; failed items remain recoverable."})
    elif has_extraction_outcome:
        pass
    elif extraction_results or (not extraction_stale and (path / "extraction" / "extraction_results.jsonl").exists()):
        messages.append({"step": 5, "role": "a", "text": "Extraction is complete. Choose a field to categorize for final analysis."})
    messages.extend(_stored_chat_messages(path))
    return _dedupe_messages(messages)


def _workflow_outcome_messages(notices: dict[str, dict[str, Any]]) -> list[dict]:
    labels = {"collection": "Collection", "retrieval": "Full-Text Retrieval", "extraction": "Information Extraction"}
    steps = {"collection": 1, "retrieval": 3, "extraction": 4}
    messages = []
    for stage_name in ("collection", "retrieval", "extraction"):
        notice = notices.get(stage_name)
        if not notice:
            continue
        outcome = "partially completed" if notice["status"] == "partial" else "failed"
        text = f"{labels[stage_name]} {outcome}: {notice['succeeded']} completed, {notice['failed']} failed."
        if notice.get("nextAction"):
            text += f" Next action: {notice['nextAction']}. Failed items remain retryable in the recovery step."
        else:
            text += " This stage is blocked until its failed items are recovered."
        messages.append({"step": steps[stage_name], "role": "a", "text": text})
    return messages


def _initial_user_topic(path: Path, config: dict) -> str:
    demo_direction = _demo_direction_for_project(path, config)
    if demo_direction and demo_direction.get("starter_topic"):
        return str(demo_direction["starter_topic"])
    return str(config.get("user_prompt") or config.get("description") or config.get("search_terms") or "Review project")


def _demo_direction_for_project(path: Path, config: dict) -> dict | None:
    project_id = path.name.lower()
    title = str(config.get("project_name") or "").lower()
    haystack = f"{project_id} {title}"
    for direction in DEMO_HISTORY_DIRECTIONS:
        if any(project_id.startswith(prefix) for prefix in direction["id_prefixes"]):
            return direction
        if any(keyword in haystack for keyword in direction["keywords"]):
            return direction
    return None


def _dedupe_messages(messages: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[int, str, str]] = set()
    for message in messages:
        key = (int(message.get("step") or 1), str(message.get("role") or ""), str(message.get("text") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(message)
    return deduped


def _stored_chat_messages(path: Path) -> list[dict]:
    rows = read_jsonl(path / "chat" / "messages.jsonl")
    messages: list[tuple[int, dict]] = []
    for index, row in enumerate(rows):
        role = row.get("role")
        text = row.get("text")
        if role not in {"u", "a"} or text in (None, ""):
            continue
        if role == "a" and row.get("source") == "canvas_action":
            continue
        visible_text = safe_display_text(str(text), fallback="Message details hidden because they contained a local path.") if role == "a" else str(text)
        message = {"step": int(row.get("step") or 1), "role": role, "text": visible_text}
        messages.append((index, message))
    messages.sort(key=lambda item: item[0])
    return [message for _, message in messages]


def _canvas_action_message_key(row: dict[str, Any]) -> str:
    stage = str(row.get("stage") or "").strip()
    if stage:
        return stage
    text = str(row.get("text") or "").lower()
    if text.startswith("collection completed"):
        return "collection"
    if text.startswith("filtering completed"):
        return "filtering"
    if text.startswith("download completed"):
        return "download"
    if text.startswith("prompt extraction completed"):
        return "prompt_extraction"
    if text.startswith("extraction completed"):
        return "extraction"
    if text.startswith("categorization completed for") and "suggested" in text:
        return "categorization_suggestions"
    if text.startswith("categorization completed"):
        return "categorization"
    return f"step:{row.get('step') or 1}:{text[:80]}"


def _activity_by_step(
    path: Path,
    collected_summary: dict,
    screening_stats: dict,
    included: list[dict],
    download_report: dict,
    fields: list[list[Any]],
    categorization: dict,
    workflow_state: dict,
    notices: dict[str, dict[str, Any]],
) -> dict:
    search_activity = [{"t": "--:--:--", "tag": "collection", "msg": f"{collected_summary.get('total_papers', 0)} records"}]
    search_activity.extend(_platform_error_activity(collected_summary.get("platform_errors") or {}))
    activity = {
        "search": search_activity,
        "screening": [{"t": "--:--:--", "tag": "screening", "msg": f"{screening_stats.get('included_count', len(included))} included"}],
        "retrieval": [{"t": "--:--:--", "tag": "retrieval", "msg": f"{_download_success(path, download_report, allow_artifact_fallback=not workflow_state['stages']['retrieval']['stale'])} PDFs fetched"}],
        "extraction": [{"t": "--:--:--", "tag": "schema", "msg": f"{len(fields)} fields"}],
        "categorize": [{"t": "--:--:--", "tag": "categorize", "msg": f"{_categorization_summary(categorization)['groups']} groups"}],
    }
    for stage_name, notice in notices.items():
        step_key = {"collection": "search", "screening": "screening", "retrieval": "retrieval", "extraction": "extraction", "categorization": "categorize"}[stage_name]
        activity[step_key].append({"t": "--:--:--", "tag": notice["status"], "msg": f"{notice['succeeded']} completed · {notice['failed']} failed · {notice['nextAction'] or 'recovery required'}"})
    for step_key, line in _canvas_action_activity(path):
        stage_name = {"search": "collection", "screening": "screening", "retrieval": "retrieval", "extraction": "extraction", "categorize": "categorization"}.get(step_key)
        if stage_name and (workflow_state["stages"][stage_name]["stale"] or stage_name in notices):
            continue
        activity.setdefault(step_key, []).append(line)
    return activity


def _workflow_notices(path: Path, workflow_state: dict, collected_summary: dict, download_report: dict, extraction_failed_items: list[str]) -> dict[str, dict[str, Any]]:
    notices: dict[str, dict[str, Any]] = {}
    next_actions = {"collection": "Paper Screening", "retrieval": "Information Extraction", "extraction": "Categorization & Analysis"}
    for stage_name in ("collection", "retrieval", "extraction"):
        stage = workflow_state["stages"][stage_name]
        if stage["stale"] or stage["status"] not in {"partial", "failed"}:
            continue
        if stage_name == "collection":
            errors = collected_summary.get("platform_errors") if isinstance(collected_summary.get("platform_errors"), dict) else {}
            failed_items = [_platform_label(str(item)) for item in errors]
        elif stage_name == "retrieval":
            failed_items = [_safe_failed_item(row) for row in (download_report.get("failed_papers") or []) if isinstance(row, dict)]
        else:
            failed_items = extraction_failed_items
        notices[stage_name] = {
            "status": stage["status"],
            "succeeded": int(stage["counts"].get("succeeded", 0)),
            "failed": int(stage["counts"].get("failed", 0)),
            "failedItems": [item for item in failed_items if item][:10],
            "retryable": int(stage["counts"].get("failed", 0)) > 0,
            "nextAction": next_actions.get(stage_name, "") if stage["status"] == "partial" else "",
        }
    return notices


def _safe_failed_item(row: dict[str, Any]) -> str:
    for key in ("title", "id", "doi", "paper_id"):
        value = str(row.get(key) or "").strip()
        if value and not contains_absolute_path(value):
            return value[:160]
    return "Unidentified item"


def _canvas_action_activity(path: Path) -> list[tuple[str, dict[str, str]]]:
    latest: dict[str, tuple[int, str, dict[str, str]]] = {}
    for index, row in enumerate(read_jsonl(path / "chat" / "messages.jsonl")):
        if row.get("role") != "a" or row.get("source") != "canvas_action" or not row.get("text"):
            continue
        key = _canvas_action_message_key(row)
        step_key = _canvas_action_step(row)
        latest[key] = (
            index,
            step_key,
            {
                "t": _activity_time(row),
                "tag": _canvas_action_tag(row),
                "msg": _activity_text(str(row.get("text") or "")),
            },
        )
    return [(step_key, line) for _index, step_key, line in sorted(latest.values(), key=lambda item: item[0])]


def _canvas_action_step(row: dict[str, Any]) -> str:
    stage = str(row.get("stage") or "").strip().lower()
    text = str(row.get("text") or "").lower()
    if stage in {"collection", "collect"} or text.startswith("collection completed"):
        return "search"
    if stage in {"filtering", "screening"} or text.startswith("filtering completed"):
        return "screening"
    if stage in {"download", "retrieval"} or text.startswith("download completed"):
        return "retrieval"
    if stage in {"prompt_extraction", "extraction", "schema"} or text.startswith(("prompt extraction completed", "extraction completed")):
        return "extraction"
    if stage in {"categorization", "categorization_suggestions", "categorize"} or text.startswith("categorization completed"):
        return "categorize"
    step = int(row.get("step") or 1)
    return {1: "search", 2: "screening", 3: "retrieval", 4: "extraction", 5: "categorize"}.get(step, "search")


def _canvas_action_tag(row: dict[str, Any]) -> str:
    stage = str(row.get("stage") or "").strip().lower()
    key = _canvas_action_message_key(row)
    if stage == "prompt_extraction":
        return "schema"
    if key == "prompt_extraction":
        return "schema"
    if stage in {"categorization_suggestions", "categorization"} or key in {"categorization_suggestions", "categorization"}:
        return "categorize"
    if stage == "filtering" or key == "filtering":
        return "screening"
    if stage == "download" or key == "download":
        return "retrieval"
    if key == "collection":
        return "collection"
    return stage or _canvas_action_step(row)


def _activity_time(row: dict[str, Any]) -> str:
    value = str(row.get("created_at") or "").strip()
    match = re.search(r"T(\d{2}:\d{2}:\d{2})", value)
    return match.group(1) if match else "--:--:--"


def _activity_text(text: str) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    return safe_display_text(value, fallback="Workflow action details hidden because they contained a local path.")


def _platform_error_activity(platform_errors: dict) -> list[dict[str, str]]:
    if not isinstance(platform_errors, dict):
        return []
    return [
        {"t": "--:--:--", "tag": safe_display_text(str(platform), fallback="unknown"), "msg": safe_display_text(str(message), fallback="Source error details hidden.")}
        for platform, message in platform_errors.items()
        if str(message).strip()
    ]


def _ctx_labels(current_step: int) -> dict:
    labels = {
        "search": "Ready",
        "screening": "Ready",
        "retrieval": "Ready",
        "extraction": "Schema ready for review" if current_step >= 4 else "Waiting",
        "categorize": "Ready for analysis" if current_step >= 5 else "Waiting",
    }
    return labels


def _history(output_root: Path, active_project_id: str) -> list[dict]:
    items = [{"id": "", "title": "Untitled review", "active": not active_project_id, "isNewProject": True}]
    items.extend(_demo_history_project_items(output_root, active_project_id))
    return [{"label": "Historys", "items": items}]


def _new_project_history(output_root: Path) -> list[dict]:
    return _history(output_root, "")


def _demo_history_project_items(output_root: Path, active_project_id: str) -> list[dict]:
    projects = list_projects(output_root)
    selected: list[dict] = []
    used_ids: set[str] = set()
    for direction in DEMO_HISTORY_DIRECTIONS:
        match = _best_demo_project(projects, direction, used_ids)
        if not match:
            continue
        used_ids.add(match["id"])
        selected.append(
            {
                "id": match["id"],
                "title": str(direction["label"]),
                "active": match["id"] == active_project_id,
            }
        )
    return selected


def _best_demo_project(projects: list[dict], direction: dict, used_ids: set[str]) -> dict | None:
    for project in projects:
        project_id = str(project.get("id") or "")
        if project_id in used_ids:
            continue
        if any(project_id.lower().startswith(prefix) for prefix in direction["id_prefixes"]):
            return project
    for project in projects:
        project_id = str(project.get("id") or "")
        if project_id in used_ids:
            continue
        haystack = f"{project_id} {project.get('title') or ''}".lower()
        if any(keyword in haystack for keyword in direction["keywords"]):
            return project
    return None
