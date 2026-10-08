"""
Collection Agent.

Runs the saved Boolean query against each selected source, writes one JSONL file
per source, and records the exact query each source executed together with its
time and record count, so the search can be reported and reproduced.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from agents.base_agent import BaseAgent
from reviewpilot_core.model_policy import DEFAULT_MAX_RESULTS_PER_PLATFORM
from reviewpilot_core.publication_dates import resolve_range
from searchers.sources import SOURCE_ORDER, search_source
from utils.jsonl_handler import save_json, write_jsonl


class CollectionAgent(BaseAgent):
    """Collects bibliographic records from the configured sources."""

    def __init__(self, project_path: Path):
        super().__init__(project_path, "collection")

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        query = str(input_data.get("search_terms") or "").strip()
        if not query:
            raise ValueError("A saved search query is required before collection.")
        platforms = list(input_data.get("platforms") or SOURCE_ORDER)
        date_range = resolve_range(input_data.get("date_range") or {})
        source_limits = input_data.get("source_limits") if isinstance(input_data.get("source_limits"), dict) else {}
        fallback = self._positive(input_data.get("max_results_per_platform") or input_data.get("max_results"), DEFAULT_MAX_RESULTS_PER_PLATFORM)
        output_dir = self.ensure_directory("collected")
        self.log(f"Collecting from {', '.join(platforms)}; query: {query[:100]}")

        platform_stats: Dict[str, int] = {}
        platform_errors: Dict[str, str] = {}
        executed: Dict[str, Dict[str, Any]] = {}
        for source in platforms:
            limit = self._positive(source_limits.get(source), fallback)
            searched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            try:
                result = search_source(source, query, max_results=limit, date_range=date_range)
                records = result.records[:limit]
                executed_query = result.executed_query
            except Exception as exc:  # one failed source must not discard the others
                self.log(f"{source} search failed: {exc}", "error")
                platform_errors[source] = str(exc)
                records, executed_query = [], ""
            collected_at = datetime.now().isoformat()
            for record in records:
                record["collected_at"] = collected_at
            write_jsonl(str(output_dir / f"{source}.jsonl"), records)
            platform_stats[source] = len(records)
            executed[source] = {"query": executed_query, "searched_at": searched_at, "limit": limit,
                                "records": len(records), "error": platform_errors.get(source)}

        total = sum(platform_stats.values())
        summary = {
            "collected_at": datetime.now().isoformat(),
            "query": query,
            "platforms": platforms,
            "date_range": date_range,
            "source_limits": {source: executed[source]["limit"] for source in platforms},
            "executed_queries": executed,
            "coverage": {
                "bounded_by_source_limits": True,
                "fields": "title and abstract",
                "date_filter": {source: "source_native" for source in platforms},
                "note": "Source metadata determines native date coverage; incomplete dates in returned records are conservatively reviewed.",
            },
            "results": platform_stats,
            "platform_stats": platform_stats,
            "platform_errors": platform_errors,
            "total_papers": total,
        }
        save_json(str(output_dir / "summary.json"), summary)
        self.state = {"completed": True, "total_papers": total, "platform_stats": platform_stats,
                      "platform_errors": platform_errors, "output_dir": str(output_dir)}
        self.save_state()
        return {
            "collected_folder": str(output_dir),
            "total_papers": total,
            "platform_stats": platform_stats,
            "platform_errors": platform_errors,
            "executed_queries": executed,
            "summary": summary,
        }

    @staticmethod
    def _positive(value: Any, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default
