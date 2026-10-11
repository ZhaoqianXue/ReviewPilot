"""Per-project record of workflow time and LLM spend, summarized by stage for the run report.

Every workflow action and chat turn runs inside `activate(project, activity)`. The context
variable it sets travels with the call, so `record_llm_call` (called from utils.llm and the
Extraction Agent's own client) files each call under the project and stage that caused it.
Worker pools must submit through `submit_in_context` to keep that context.
"""

from __future__ import annotations

import contextvars
import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

from .project_store import read_jsonl

LEDGER = Path("logs") / "run_ledger.jsonl"
STAGES = ("search", "collection", "screening", "retrieval", "extraction", "categorization")
STAGE_LABELS = {"search": "Search Setup", "collection": "Collection", "screening": "Paper Screening", "retrieval": "Full-Text Retrieval",
                "extraction": "Information Extraction", "categorization": "Categorization & Analysis"}
ACTION_STAGES = {
    "create-project": "search", "update-setup": "search", "collect": "collection",
    "edit-criteria": "screening", "save-criteria": "screening", "finalize-criteria": "screening",
    "generate-screening-guidance": "screening", "confirm-screening-guidance": "screening", "screen": "screening",
    "download-pdfs": "retrieval", "retry-failed-downloads": "retrieval",
    "generate-schema": "extraction", "regenerate-schema": "extraction", "finalize-schema": "extraction", "edit-schema": "extraction",
    "generate-coding-rules": "extraction", "confirm-coding-rules": "extraction", "run-extraction": "extraction",
    "finalize-and-run-extraction": "extraction", "preview-extraction": "extraction", "review-sample": "screening",
    "suggest-categories": "categorization", "categorize": "categorization",
}
CHAT_STAGES = {"search": "search", "screening": "screening", "retrieval": "retrieval", "extraction": "extraction", "categorize": "categorization"}

_active: contextvars.ContextVar[tuple[Path, str, str] | None] = contextvars.ContextVar("run_ledger_activity", default=None)
_write_lock = Lock()
_progress: dict[tuple[str, str], dict[str, int]] = {}  # (project, activity) -> items done of total, while it runs


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _append(project: Path, row: dict[str, Any]) -> None:
    path = Path(project) / LEDGER
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def stage_of(activity: str) -> str:
    if activity.startswith("chat:"):
        return CHAT_STAGES.get(activity.split(":", 1)[1], "search")
    return ACTION_STAGES.get(activity, "other")


@contextmanager
def activate(project: Path | str, activity: str) -> Iterator[None]:
    """Time one action or chat turn and file the LLM calls made inside it under its stage."""
    project = Path(project)
    if _active.get() is not None:  # nested, e.g. a chat reply that saves the setup: the outer activity keeps the time and calls
        yield
        return
    stage = stage_of(activity)
    token = _active.set((project, activity, stage))
    started, clock, status = _now(), time.monotonic(), "completed"
    try:
        yield
    except BaseException:
        status = "failed"
        raise
    finally:
        _active.reset(token)
        _progress.pop((str(project), activity), None)
        try:
            if project.is_dir():
                _append(project, {"kind": "action", "activity": activity, "stage": stage, "status": status,
                                  "started_at": started, "finished_at": _now(), "seconds": round(time.monotonic() - clock, 3)})
        except OSError:
            pass  # the record is a report aid; a failed write must never fail the action


def record_llm_call(model: str, usage: dict[str, Any] | None, *, cost_usd: float | None = None, kind: str = "llm") -> None:
    active = _active.get()
    if active is None:
        return
    project, activity, stage = active
    usage = usage or {}
    cost = usage.get("cost_usd") if cost_usd is None else cost_usd
    try:
        _append(project, {"kind": kind, "activity": activity, "stage": stage, "at": _now(), "model": model or usage.get("model") or "",
                          "input_tokens": int(usage.get("input_tokens") or 0), "output_tokens": int(usage.get("output_tokens") or 0),
                          "cost_usd": float(cost or 0.0)})
    except (OSError, TypeError, ValueError):
        pass


def report_progress(done: int, total: int) -> None:
    """Items finished so far in the current activity (records screened, papers retrieved or extracted)."""
    active = _active.get()
    if active is not None and total:
        _progress[(str(active[0]), active[1])] = {"done": int(done), "total": int(total)}


def progress(project: Path | str, activity: str) -> dict[str, int] | None:
    return _progress.get((str(Path(project)), activity))


def submit_in_context(pool, fn, *args, **kwargs):
    """Submit to a worker pool so the worker's LLM calls stay filed under the current activity."""
    return pool.submit(contextvars.copy_context().run, fn, *args, **kwargs)


def human_corrections(project: Path | str) -> dict[str, int]:
    """Decisions and values a person changed, and PDFs a person supplied, read from the records themselves."""
    project = Path(project)
    records = read_jsonl(project / "filtered" / "included_papers.jsonl") + read_jsonl(project / "filtered" / "excluded_papers.jsonl")
    reviews = [row.get("human_screening") or {} for row in records]
    fields = [entry for row in read_jsonl(project / "extraction" / "extraction_results.jsonl")
              for entry in (row.get("human_fields") or {}).values() if isinstance(entry, dict)]
    changed = [entry for entry in fields if "value" not in entry or entry.get("value") != entry.get("original_value")]
    from .retrieval_retry import USER_DOWNLOAD_METHOD
    included = read_jsonl(project / "filtered" / "included_papers.jsonl")
    return {"screening_reviewed": sum(1 for review in reviews if review),
            "screening_overridden": sum(1 for review in reviews if review and review.get("original_decision") != review.get("decision")),
            "extraction_fields_reviewed": len(fields), "extraction_fields_corrected": len(changed),
            "pdfs_provided": sum(1 for row in included if row.get("pdf_method") == USER_DOWNLOAD_METHOD)}


def summary(project: Path | str) -> dict[str, Any]:
    """Time, LLM calls and cost per stage, plus human corrections, for the run report."""
    project = Path(project)
    rows = read_jsonl(project / LEDGER)
    stages: dict[str, dict[str, Any]] = {}
    for name in (*STAGES, "other"):
        actions = [row for row in rows if row.get("kind") == "action" and row.get("stage") == name]
        calls = [row for row in rows if row.get("kind") in {"llm", "web_search"} and row.get("stage") == name]
        if name == "other" and not actions and not calls:
            continue
        starts = sorted(row["started_at"] for row in actions if row.get("started_at"))
        ends = sorted(row["finished_at"] for row in actions if row.get("finished_at"))
        stages[name] = {
            "label": STAGE_LABELS.get(name, "Other"),
            "actions": len(actions),
            "run_seconds": round(sum(float(row.get("seconds") or 0) for row in actions), 1),
            "first_started_at": starts[0] if starts else "",
            "last_finished_at": ends[-1] if ends else "",
            "llm_calls": sum(1 for row in calls if row.get("kind") == "llm"),
            "web_searches": sum(1 for row in calls if row.get("kind") == "web_search"),
            "input_tokens": sum(int(row.get("input_tokens") or 0) for row in calls),
            "output_tokens": sum(int(row.get("output_tokens") or 0) for row in calls),
            "cost_usd": round(sum(float(row.get("cost_usd") or 0) for row in calls), 4),
        }
    models = sorted({str(row.get("model")) for row in rows if row.get("kind") == "llm" and row.get("model")})
    return {
        "recorded": bool(rows),
        "models": models,
        "stages": stages,
        "total": {"run_seconds": round(sum(stage["run_seconds"] for stage in stages.values()), 1),
                  "llm_calls": sum(stage["llm_calls"] for stage in stages.values()),
                  "cost_usd": round(sum(stage["cost_usd"] for stage in stages.values()), 4)},
        "human_corrections": human_corrections(project),
    }
