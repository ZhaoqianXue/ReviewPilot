"""Explicit Sub Agent contracts for ReviewPilot workflow actions."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agents.collection_agent import CollectionAgent
from agents.download_agent import DownloadAgent
from agents.extraction_agent import ExtractionAgent
from agents.filtering_agent import FilteringAgent
from agents.prompt_agent import PromptAgent
from agents.search_condition_agent import SearchConditionAgent
from .atomic_files import atomic_write_json, atomic_write_jsonl, atomic_write_text
from .categorization_analysis import CategorizationAnalysis
from .project_store import count_jsonl, read_json, read_jsonl
from .skill_runtime import SkillRegistry, activate_prompt_skill, bind_skill_llm_query
from .workflow_state import structured_action_outcome
from .model_policy import (
    CATEGORIZATION_MODEL,
    COLLECTION_MODEL,
    DOWNLOAD_MODEL,
    EXTRACTION_MODEL,
    FILTERING_MODEL,
    PROMPT_MODEL,
    SEARCH_CONDITION_MODEL,
)


_SAFE_SOURCE_NAME = re.compile(r"[A-Za-z0-9_-]+")


def _strict_jsonl_rows(root: Path, filename: str) -> list[dict[str, Any]]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Invalid artifact root")
    path = root / filename
    if path.is_symlink() or not path.is_file():
        raise ValueError("Invalid JSONL artifact")
    try:
        if path.resolve().parent != root.resolve():
            raise ValueError("Invalid JSONL artifact location")
        rows = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("JSONL artifact rows must be objects")
                rows.append(row)
        return rows
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid JSONL artifact") from exc


def _contract_value(sources: list[dict[str, Any]], keys: tuple[str, ...], label: str) -> Any:
    values = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            if key in source:
                values.append(source[key])
    if not values:
        raise ValueError(f"Missing {label}")
    if any(value != values[0] for value in values[1:]):
        raise ValueError(f"Conflicting {label}")
    return values[0]


def _contract_count(sources: list[dict[str, Any]], keys: tuple[str, ...], label: str) -> int:
    value = _contract_value(sources, keys, label)
    if type(value) is not int or value < 0:
        raise ValueError(f"Invalid {label}")
    return value


def _contract_sources(*sources: Any) -> list[dict[str, Any]]:
    expanded = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        expanded.append(source)
        if isinstance(source.get("stats"), dict):
            expanded.append(source["stats"])
    return expanded


class SubAgentContract(Protocol):
    action: str
    agent_name: str
    stage: str
    model: str

    def run(self, output_root: Path | str, project_id: str, llm_query=None, input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class SearchConditionAgentContract:
    """Runs the real SearchConditionAgent for search setup artifacts."""

    action: str = "save-search-setup"
    agent_name: str = "SearchConditionAgent"
    stage: str = "search_conditions"
    model: str = SEARCH_CONDITION_MODEL
    agent_cls: type = SearchConditionAgent

    def run(
        self,
        output_root: Path | str,
        project_id: str,
        llm_query=None,
        input_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from agents import search_condition_agent as search_condition_module

        root = Path(output_root)
        project_path = root / project_id
        config = dict(input_data or {})
        config["project_path"] = str(project_path)
        skill_query = bind_skill_llm_query(
            project_path,
            self.action,
            self.agent_name,
            llm_query or search_condition_module.query_llm,
            model=self.model,
        )
        result = self.agent_cls(output_dir=str(root), llm_query=skill_query).run(config)
        return self._normalize_result(project_path, result)

    def _normalize_result(self, project_path: Path, result: dict[str, Any]) -> dict[str, Any]:
        search_conditions = result if isinstance(result, dict) else read_json(project_path / "search_conditions.json", {}) or {}
        return {
            "status": "search_setup_done",
            "search_conditions": search_conditions,
            "artifact": str(project_path / "search_conditions.json"),
        }


@dataclass(frozen=True)
class RelevancePromptAgentContract:
    """Runs the real PromptAgent for prompt_relevance artifacts."""

    action: str = "generate-relevance-prompt"
    agent_name: str = "PromptAgent"
    stage: str = "prompt_relevance"
    model: str = PROMPT_MODEL
    agent_cls: type = PromptAgent

    def run(
        self,
        output_root: Path | str,
        project_id: str,
        llm_query=None,
        input_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        project_path = Path(output_root) / project_id
        config = read_json(project_path / "search_conditions.json", {}) or {}
        prompt_input = {
            **config,
            **(input_data or {}),
            "primary_topic": config.get("primary_topic") or config.get("project_name") or config.get("search_terms") or "the review topic",
            "domain": config.get("domain") or config.get("description") or config.get("research_description") or "the review domain",
        }
        result = self.agent_cls(project_path, model=self.model, llm_query=llm_query).generate_relevance_prompt(prompt_input)
        if not isinstance(result, dict) or not isinstance(result.get("system_prompt"), str):
            raise ValueError("Relevance prompt is missing its system prompt")
        result = {
            **result,
            "system_prompt": activate_prompt_skill(
                project_path,
                self.action,
                self.agent_name,
                result["system_prompt"],
                model=self.model,
            ),
        }
        atomic_write_json(project_path / "prompts" / "relevance_prompt.json", result, indent=None)
        return self._normalize_result(project_path, result)

    def _normalize_result(self, project_path: Path, result: dict[str, Any]) -> dict[str, Any]:
        prompt_path = project_path / "prompts" / "relevance_prompt.json"
        prompt = result if isinstance(result, dict) else read_json(prompt_path, {}) or {}
        return {
            "status": "relevance_prompt_generated",
            "prompt": prompt,
            "prompt_path": str(prompt_path),
        }


@dataclass(frozen=True)
class CollectionAgentContract:
    """Runs the real CollectionAgent behind the Lead Agent contract boundary."""

    action: str = "collect"
    agent_name: str = "CollectionAgent"
    stage: str = "collection"
    model: str = COLLECTION_MODEL
    agent_cls: type = CollectionAgent

    def run(self, output_root: Path | str, project_id: str, llm_query=None, input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        project_path = Path(output_root) / project_id
        config = read_json(project_path / "search_conditions.json", {}) or {}
        input_data = self._collection_input(config)

        if os.getenv("REVIEWPILOT_OFFLINE_ACTIONS") == "1":
            return self._write_offline_collection(project_path, input_data)

        result = self.agent_cls(project_path).run(input_data)
        return self._normalize_result(project_path, result)

    def _collection_input(self, config: dict[str, Any]) -> dict[str, Any]:
        source_limits = config.get("source_limits") or {}
        max_results = config.get("max_results_per_platform") or config.get("max_results")
        if not max_results and isinstance(source_limits, dict) and source_limits:
            max_results = max(int(value) for value in source_limits.values() if str(value).isdigit())
        return {
            **config,
            "max_results_per_platform": int(max_results or 0),
        }

    def _write_offline_collection(self, project_path: Path, input_data: dict[str, Any]) -> dict[str, Any]:
        collected_dir = project_path / "collected"
        summary = {
            "collected_at": "",
            "query": input_data.get("search_terms", ""),
            "platforms": input_data.get("platforms", []),
            "max_results_per_platform": input_data.get("max_results_per_platform", 0),
            "results": {},
            "platform_stats": {},
            "total_papers": 0,
        }
        atomic_write_json(collected_dir / "summary.json", summary, indent=None)
        return {"status": "collection_done", "total": 0, "platform_stats": {}, "platform_errors": {}, "collected_folder": str(collected_dir)}

    def _normalize_result(self, project_path: Path, result: dict[str, Any]) -> dict[str, Any]:
        collected_dir = project_path / "collected"
        reported_dir = result.get("collected_folder")
        try:
            if collected_dir.is_symlink() or not collected_dir.is_dir():
                raise ValueError("Invalid collection artifact root")
            if reported_dir is not None and Path(reported_dir).resolve() != collected_dir.resolve():
                raise ValueError("Collection artifact root does not match project")
        except (OSError, RuntimeError, TypeError) as exc:
            raise ValueError("Invalid collection artifact root") from exc
        summary_path = collected_dir / "summary.json"
        summary = read_json(summary_path, {}) or {}
        sources = _contract_sources(result, result.get("summary"), summary)
        platform_stats = _contract_value(sources, ("platform_stats", "results"), "collection platform_stats")
        platform_errors = _contract_value(sources, ("platform_errors",), "collection platform_errors")
        total = _contract_count(sources, ("total", "total_papers"), "collection total")
        normalized = {"total": total, "platform_stats": platform_stats, "platform_errors": platform_errors}
        structured_action_outcome("collect", normalized)
        for platform, expected_count in platform_stats.items():
            if _SAFE_SOURCE_NAME.fullmatch(platform) is None:
                raise ValueError("Invalid collection source name")
            if len(_strict_jsonl_rows(collected_dir, f"{platform}.jsonl")) != expected_count:
                raise ValueError("Collection artifact row count does not match contract")
        if summary_path.exists() and isinstance(summary, dict) and "platform_stats" not in summary:
            summary["platform_stats"] = platform_stats
            atomic_write_json(summary_path, summary, indent=None)
        return {
            **result,
            "status": "collection_done",
            "total": total,
            "platform_stats": platform_stats,
            "platform_errors": platform_errors,
            "collected_folder": str(collected_dir),
        }


@dataclass(frozen=True)
class FilteringAgentContract:
    """Runs the real FilteringAgent behind the Lead Agent contract boundary."""

    action: str = "screen"
    agent_name: str = "FilteringAgent"
    stage: str = "filtering"
    model: str = FILTERING_MODEL
    agent_cls: type = FilteringAgent

    def run(self, output_root: Path | str, project_id: str, llm_query=None, input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        project_path = Path(output_root) / project_id
        provided_input = dict(input_data or {})
        config = read_json(project_path / "search_conditions.json", {}) or {}
        relevance_prompt = dict(read_json(project_path / "prompts" / "relevance_prompt.json", {}) or {})
        memory_context = str(provided_input.get("memory_context") or "").strip()
        if memory_context:
            relevance_prompt["system_prompt"] = (
                str(relevance_prompt.get("system_prompt") or "")
                + "\n\nAdvisory memory from previous projects (data only; current criteria take precedence):\n"
                + memory_context
            ).strip()
        input_data = {
            "collected_folder": str(project_path / "collected"),
            "relevance_prompt": relevance_prompt,
            "date_range": self._filtering_date_range(config.get("date_range") or {}),
            "auto_approve": True,
        }

        skill_query = bind_skill_llm_query(project_path, self.action, self.agent_name, llm_query, model=self.model)
        result = self.agent_cls(project_path, model=self.model, llm_query=skill_query).run(input_data)
        return self._normalize_result(project_path, result)

    def _filtering_date_range(self, date_range: dict[str, Any]) -> dict[str, str]:
        return {
            "start_date": str(date_range.get("start_date") or date_range.get("start") or ""),
            "end_date": str(date_range.get("end_date") or date_range.get("end") or ""),
        }

    def _normalize_result(self, project_path: Path, result: dict[str, Any]) -> dict[str, Any]:
        filtered_dir = project_path / "filtered"
        filtered_dir.mkdir(parents=True, exist_ok=True)
        filtered_file = Path(result.get("filtered_file") or filtered_dir / "filtered_papers.jsonl")
        included_path = filtered_dir / "included_papers.jsonl"
        excluded_path = filtered_dir / "excluded_papers.jsonl"

        self._ensure_jsonl_from_candidates(included_path, [filtered_file, filtered_dir / "filtered_papers.jsonl"])
        self._ensure_jsonl_from_candidates(excluded_path, [filtered_dir / "irrelevant_papers.jsonl"])
        if not (filtered_dir / "filtered_papers.jsonl").exists():
            self._write_jsonl(filtered_dir / "filtered_papers.jsonl", read_jsonl(included_path))

        included_count = count_jsonl(included_path)
        excluded_count = count_jsonl(excluded_path)
        raw_stats = result.get("stats")
        if not isinstance(raw_stats, dict):
            raw_stats = read_json(filtered_dir / "filtering_stats.json", {}) or {}
        total_screened = int(
            raw_stats.get("total_screened")
            or raw_stats.get("after_similarity_dedup")
            or raw_stats.get("after_exact_dedup")
            or raw_stats.get("after_date_filter")
            or raw_stats.get("initial_count")
            or included_count + excluded_count
        )
        screening_stats = {
            **raw_stats,
            "total_screened": total_screened,
            "included_count": included_count,
            "excluded_count": excluded_count,
        }
        if not (filtered_dir / "filtering_stats.json").exists():
            atomic_write_json(filtered_dir / "filtering_stats.json", raw_stats or screening_stats, indent=None)
        atomic_write_json(filtered_dir / "screening_stats.json", screening_stats, indent=None)
        return {
            **result,
            "status": "screening_done",
            "included": included_count,
            "excluded": excluded_count,
            "filtered_file": str(included_path),
        }

    def _ensure_jsonl_from_candidates(self, target: Path, candidates: list[Path]) -> None:
        if target.exists():
            return
        for candidate in candidates:
            if candidate.exists():
                atomic_write_text(target, candidate.read_text(encoding="utf-8"))
                return
        atomic_write_text(target, "")

    def _write_jsonl(self, path: Path, rows: list[dict]) -> None:
        atomic_write_jsonl(path, rows)


@dataclass(frozen=True)
class PromptAgentContract:
    """Runs the real PromptAgent for prompt_extraction artifacts."""

    action: str = "generate-schema"
    agent_name: str = "PromptAgent"
    stage: str = "prompt_extraction"
    model: str = PROMPT_MODEL
    agent_cls: type = PromptAgent

    def run(self, output_root: Path | str, project_id: str, llm_query=None, input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        project_path = Path(output_root) / project_id
        provided_input = dict(input_data or {})
        config = read_json(project_path / "search_conditions.json", {}) or {}
        relevance_prompt = read_json(project_path / "prompts" / "relevance_prompt.json", {}) or {}
        included_papers = read_jsonl(project_path / "filtered" / "included_papers.jsonl", limit=3)
        input_data = {
            **config,
            "relevance_prompt": relevance_prompt,
            "included_papers": included_papers,
            "auto_approve": True,
            "memory_context": str(provided_input.get("memory_context") or ""),
        }
        skill_query = bind_skill_llm_query(project_path, self.action, self.agent_name, llm_query, model=self.model)
        result = self.agent_cls(project_path, model=self.model, llm_query=skill_query).generate_extraction_prompt(input_data)
        return self._normalize_result(project_path, result)

    def _normalize_result(self, project_path: Path, result: dict[str, Any]) -> dict[str, Any]:
        schema = read_json(project_path / "extraction" / "extraction_schema.json", {}) or {}
        source = result.get("source") or read_json(project_path / "extraction" / "extraction_prompt.json", {}).get("source") or "llm"
        return {
            "status": "schema_generated",
            "source": source,
            "field_count": len(schema.get("fields") or []),
            "schema": schema,
        }


@dataclass(frozen=True)
class DownloadAgentContract:
    """Runs the real DownloadAgent for retrieval artifacts."""

    action: str = "download-pdfs"
    agent_name: str = "DownloadAgent"
    stage: str = "download"
    model: str = DOWNLOAD_MODEL
    agent_cls: type = DownloadAgent

    def run(self, output_root: Path | str, project_id: str, llm_query=None, input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        project_path = Path(output_root) / project_id
        input_data = {
            "filtered_file": str(project_path / "filtered" / "included_papers.jsonl"),
            "download_folder": str(project_path / "pdfs"),
        }
        result = self.agent_cls(project_path).run(input_data)
        return self._normalize_result(project_path, result)

    def _normalize_result(self, project_path: Path, result: dict[str, Any]) -> dict[str, Any]:
        report = read_json(project_path / "pdfs" / "download_report.json", {}) or {}
        sources = _contract_sources(result, report)
        success = _contract_count(sources, ("success", "successful"), "retrieval success")
        failed = _contract_count(sources, ("failed",), "retrieval failed")
        structured_action_outcome("download-pdfs", {"success": success, "failed": failed})
        if not isinstance(report, dict) or not isinstance(report.get("downloaded"), list) or not isinstance(report.get("failed_papers"), list):
            raise ValueError("Retrieval report detail fields must be lists")
        if not all(isinstance(item, dict) for item in report["downloaded"] + report["failed_papers"]):
            raise ValueError("Retrieval report detail items must be objects")
        if len(report["downloaded"]) != success or len(report["failed_papers"]) != failed:
            raise ValueError("Retrieval report detail counts do not match contract")
        return {
            **result,
            "status": "download_done",
            "success": success,
            "failed": failed,
            "download_folder": str(project_path / "pdfs"),
        }


@dataclass(frozen=True)
class ExtractionAgentContract:
    """Runs the real ExtractionAgent for extraction artifacts."""

    action: str = "run-extraction"
    agent_name: str = "ExtractionAgent"
    stage: str = "extraction"
    model: str = EXTRACTION_MODEL
    agent_cls: type = ExtractionAgent

    def run(self, output_root: Path | str, project_id: str, llm_query=None, input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        project_path = Path(output_root) / project_id
        extraction_prompt = read_json(project_path / "prompts" / "extraction_prompt.json", {}) or {}
        activation = SkillRegistry().activate(self.action, self.agent_name)
        extraction_prompt = {
            **extraction_prompt,
            "system_prompt": activation.augment(
                extraction_prompt.get("system_prompt", "You are an expert academic paper analyst.")
            ),
        }
        input_data = {
            "filtered_file": str(project_path / "filtered" / "included_papers.jsonl"),
            "download_folder": str(project_path / "pdfs"),
            "extraction_prompt": extraction_prompt,
            "download_report": read_json(project_path / "pdfs" / "download_report.json", {}) or {},
        }
        skill_query = bind_skill_llm_query(project_path, self.action, self.agent_name, llm_query, model=self.model)
        result = self.agent_cls(project_path, model=self.model, llm_query=skill_query).run(input_data)
        return self._normalize_result(project_path, result)

    def _normalize_result(self, project_path: Path, result: dict[str, Any]) -> dict[str, Any]:
        extraction_dir = project_path / "extraction"
        output_file = extraction_dir / "extraction_results.jsonl"
        rows = _strict_jsonl_rows(extraction_dir, output_file.name)
        stats = read_json(project_path / "extraction" / "extraction_stats.json", {}) or {}
        sources = _contract_sources(result, stats)
        processed = _contract_count(sources, ("processed", "success"), "extraction processed")
        errors = _contract_count(sources, ("errors", "failed"), "extraction errors")
        successful_rows = 0
        failed_rows = 0
        for row in rows:
            if "extraction_status" not in row:
                status = ""
            else:
                raw_status = row["extraction_status"]
                if not isinstance(raw_status, str):
                    raise ValueError("Invalid extraction status")
                status = raw_status.strip().lower()
            if status in {"", "success"}:
                successful_rows += 1
            elif status in {"error", "failed"}:
                failed_rows += 1
            else:
                raise ValueError("Invalid extraction status")
        if (successful_rows, failed_rows) != (processed, errors):
            raise ValueError("Extraction artifact row counts do not match contract")
        structured_action_outcome("run-extraction", {"processed": processed, "errors": errors})
        return {
            **result,
            "status": "extraction_done",
            "processed": processed,
            "errors": errors,
            "output_file": str(output_file),
        }


@dataclass(frozen=True)
class CategorizationSuggestionContract:
    """Generates reviewable categorization categories before final application."""

    action: str = "suggest-categories"
    agent_name: str = "LeadAgentCategorization"
    stage: str = "categorization"
    model: str = CATEGORIZATION_MODEL
    analysis_cls: type = CategorizationAnalysis

    def run(self, output_root: Path | str, project_id: str, llm_query=None, input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        project_path = Path(output_root) / project_id
        skill_query = bind_skill_llm_query(project_path, self.action, self.agent_name, llm_query, model=self.model)
        result = self.analysis_cls(project_path, llm_query=skill_query).suggest_categories(input_data)
        return {
            **result,
            "suggestions_file": str(project_path / "categorization" / "suggested_categories.json"),
        }


@dataclass(frozen=True)
class CategorizationAnalysisContract:
    """Runs the Lead-owned Categorization & Analysis result capability."""

    action: str = "categorize"
    agent_name: str = "LeadAgentCategorization"
    stage: str = "categorization"
    model: str = CATEGORIZATION_MODEL
    analysis_cls: type = CategorizationAnalysis

    def run(self, output_root: Path | str, project_id: str, llm_query=None, input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        project_path = Path(output_root) / project_id
        skill_query = bind_skill_llm_query(project_path, self.action, self.agent_name, llm_query, model=self.model)
        result = self.analysis_cls(project_path, llm_query=skill_query).run(input_data)
        return {
            **result,
            "status": "categorization_done",
            "categorized_results_file": str(project_path / "categorization" / "categorized_results.jsonl"),
            "mapping_file": str(project_path / "categorization" / "categorization_mapping.json"),
        }


def default_sub_agent_contracts(
    *,
    search_condition_agent_cls: type = SearchConditionAgent,
    prompt_agent_cls: type = PromptAgent,
) -> list[SubAgentContract]:
    return [
        SearchConditionAgentContract(agent_cls=search_condition_agent_cls),
        RelevancePromptAgentContract(agent_cls=prompt_agent_cls),
        CollectionAgentContract(),
        FilteringAgentContract(),
        PromptAgentContract(agent_cls=prompt_agent_cls),
        DownloadAgentContract(),
        ExtractionAgentContract(),
        CategorizationSuggestionContract(),
        CategorizationAnalysisContract(),
    ]
