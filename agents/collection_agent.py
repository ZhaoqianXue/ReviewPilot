"""
Collection Agent (the Literature Search Tool).

Runs the saved Boolean query against each selected source, writes one JSONL file
per source, and records the exact query each source executed together with its
time and record count, so the search can be reported and reproduced. It makes no
LLM calls, so the same saved setup always sends the same queries.

Failure handling, in two layers so total collection time stays bounded:

1. Each adapter retries a transient HTTP failure (429, 5xx, timeout, dropped
   connection) a fixed number of times with backoff (searchers/http_retry.py).
2. A source that still fails transiently gets exactly one more attempt after the
   other sources have finished, and no sooner than ``DEFERRED_RETRY_COOLDOWN``
   seconds after it failed. Rate limits and overloads usually clear within that
   window; a failure that survives it is reported, not retried again. Errors a
   retry cannot fix (a rejected query, a bad limit) are never retried.

A source that fails is recorded with zero rows, its error, and the query the
adapter attempted. The other sources still run. ``retry_sources`` re-runs only
failed sources of the latest collection while keeping the others' records; it
is refused when the saved setup no longer matches that collection.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent
from reviewpilot_core.model_policy import DEFAULT_MAX_RESULTS_PER_PLATFORM
from reviewpilot_core.project_store import read_json
from reviewpilot_core.publication_dates import resolve_range
from searchers.sources import SOURCE_ORDER, SourceSearchError, search_source
from utils.jsonl_handler import save_json, write_jsonl

DEFERRED_RETRY_COOLDOWN = 15  # seconds between a source's failure and its single deferred retry


def collection_plan(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """The query, sources, dates and per-source limits a collection run will use."""
    query = str(input_data.get("search_terms") or "").strip()
    platforms = list(input_data.get("platforms") or SOURCE_ORDER)
    source_limits = input_data.get("source_limits") if isinstance(input_data.get("source_limits"), dict) else {}
    fallback = _positive(input_data.get("max_results_per_platform") or input_data.get("max_results"), DEFAULT_MAX_RESULTS_PER_PLATFORM)
    return {
        "query": query,
        "platforms": platforms,
        "date_range": resolve_range(input_data.get("date_range") or {}),
        "source_limits": {source: _positive(source_limits.get(source), fallback) for source in platforms},
    }


def retryable_sources(summary: Any, input_data: Dict[str, Any]) -> List[str]:
    """Failed sources of ``summary`` that can be re-run alone under the current setup.

    Re-running one source is only sound when every other source's records came
    from the same query, dates and limits; otherwise the whole collection must
    be run again.
    """
    if not isinstance(summary, dict) or not isinstance(summary.get("platform_errors"), dict):
        return []
    plan = collection_plan(input_data)
    if not plan["query"] or (summary.get("query") or "").strip() != plan["query"]:
        return []
    if list(summary.get("platforms") or []) != plan["platforms"] or summary.get("date_range") != plan["date_range"]:
        return []
    if summary.get("source_limits") != plan["source_limits"]:
        return []
    return [source for source in plan["platforms"] if source in summary["platform_errors"]]


def collection_recovery(status: str) -> str:
    """How to recover failed collection sources (shared by the banner, chat notice and Lead Agent reply)."""
    if status == "partial":
        return "Retry the failed sources from Records by source; the other sources keep their records."
    return "Retry the failed sources from Records by source."


def _positive(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


class CollectionAgent(BaseAgent):
    """Collects bibliographic records from the configured sources."""

    def __init__(self, project_path: Path):
        super().__init__(project_path, "collection")

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        plan = collection_plan(input_data)
        query, platforms, date_range = plan["query"], plan["platforms"], plan["date_range"]
        if not query:
            raise ValueError("A saved search query is required before collection.")
        output_dir = self.ensure_directory("collected")

        previous: Dict[str, Any] = {}
        to_run = platforms
        requested_retry = input_data.get("retry_sources")
        if requested_retry is not None:
            previous = read_json(output_dir / "summary.json", {}) or {}
            allowed = retryable_sources(previous, input_data)
            to_run = [source for source in platforms if source in set(requested_retry)]
            if not to_run or any(source not in allowed for source in to_run):
                raise ValueError("Only failed sources of the current collection can be retried. Run the full collection again.")
            self.log(f"Retrying failed sources {', '.join(to_run)}; query: {query[:100]}")
        else:
            self.log(f"Collecting from {', '.join(platforms)}; query: {query[:100]}")

        platform_stats: Dict[str, int] = {}
        platform_errors: Dict[str, str] = {}
        executed: Dict[str, Dict[str, Any]] = {}
        deferred: Dict[str, float] = {}
        for source in platforms:
            if source not in to_run:
                platform_stats[source] = int((previous.get("platform_stats") or {}).get(source, 0))
                executed[source] = (previous.get("executed_queries") or {}).get(source) or {}
                continue
            failure = self._collect_source(source, query, plan["source_limits"][source], date_range, output_dir,
                                           platform_stats, platform_errors, executed, attempts=1)
            if failure is not None and failure.transient:
                deferred[source] = time.monotonic()

        # One deferred retry per transiently failed source, after the others finished.
        for source, failed_at in deferred.items():
            wait = DEFERRED_RETRY_COOLDOWN - (time.monotonic() - failed_at)
            if wait > 0:
                time.sleep(wait)
            self.log(f"Retrying {source} once after earlier failure: {platform_errors[source]}", "warning")
            platform_errors.pop(source, None)
            self._collect_source(source, query, plan["source_limits"][source], date_range, output_dir,
                                 platform_stats, platform_errors, executed, attempts=2)

        total = sum(platform_stats.values())
        summary = {
            "collected_at": datetime.now().isoformat(),
            "query": query,
            "platforms": platforms,
            "date_range": date_range,
            "source_limits": plan["source_limits"],
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

    def _collect_source(self, source: str, query: str, limit: int, date_range: Dict[str, str], output_dir: Path,
                        platform_stats: Dict[str, int], platform_errors: Dict[str, str],
                        executed: Dict[str, Dict[str, Any]], *, attempts: int) -> Optional[SourceSearchError]:
        """Search one source and record its rows, query and error; returns the failure, if any."""
        searched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        failure: Optional[SourceSearchError] = None
        try:
            result = search_source(source, query, max_results=limit, date_range=date_range)
            records = result.records[:limit]
            executed_query = result.executed_query
        except Exception as exc:  # one failed source must not discard the others
            failure = exc if isinstance(exc, SourceSearchError) else SourceSearchError(str(exc) or exc.__class__.__name__)
            self.log(f"{source} search failed: {failure}", "error")
            platform_errors[source] = str(failure)
            records, executed_query = [], failure.executed_query
        collected_at = datetime.now().isoformat()
        for record in records:
            record["collected_at"] = collected_at
        write_jsonl(str(output_dir / f"{source}.jsonl"), records)
        platform_stats[source] = len(records)
        executed[source] = {"query": executed_query, "searched_at": searched_at, "limit": limit,
                            "records": len(records), "error": platform_errors.get(source), "attempts": attempts}
        return failure
