"""Atomic, project-scoped workflow stage ledger."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .atomic_files import atomic_write_json


WORKFLOW_STATE_VERSION = 1
STAGE_NAMES = ("collection", "screening", "retrieval", "extraction", "categorization")
STAGE_STATUSES = ("not_started", "ready", "running", "partial", "failed", "completed")
ACTION_STAGES = {
    "collect": "collection",
    "screen": "screening",
    "download-pdfs": "retrieval",
    "generate-schema": "extraction",
    "finalize-schema": "extraction",
    "edit-schema": "extraction",
    "run-extraction": "extraction",
    "suggest-categories": "categorization",
    "categorize": "categorization",
}
_READY_ACTIONS = {"generate-schema", "finalize-schema", "edit-schema", "suggest-categories"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _stage(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "attempt": 0,
        "updated_at": _now(),
        "error": None,
        "counts": {},
        "stale": False,
        "last_valid": None,
    }


def initialize_workflow_state(project_path: Path | str) -> dict[str, Any]:
    project = Path(project_path)
    state = new_workflow_state()
    atomic_write_json(project / "workflow_state.json", state)
    return state


def new_workflow_state() -> dict[str, Any]:
    return {
        "version": WORKFLOW_STATE_VERSION,
        "created_at": _now(),
        "stages": {name: _stage("ready" if name == "collection" else "not_started") for name in STAGE_NAMES},
    }


def load_workflow_state(project_path: Path | str) -> dict[str, Any]:
    project = Path(project_path)
    path = project / "workflow_state.json"
    if not path.exists():
        return migrate_legacy_workflow_state(project)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Workflow state ledger is unreadable") from exc
    _validate(state)
    return state


def start_action(project_path: Path | str, action: str) -> dict[str, Any]:
    state = load_workflow_state(project_path)
    stage = state["stages"][_action_stage(action)]
    stage.update(status="running", attempt=stage["attempt"] + 1, updated_at=_now(), error=None)
    _write(project_path, state)
    return state


def complete_action(project_path: Path | str, action: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    state = load_workflow_state(project_path)
    stage_name = _action_stage(action)
    stage = state["stages"][stage_name]
    counts = _counts(result or {})
    status = "ready" if action in _READY_ACTIONS else "completed"
    stage.update(status=status, updated_at=_now(), error=None, counts=counts, stale=False)
    stage["last_valid"] = {
        "status": status,
        "attempt": stage["attempt"],
        "updated_at": stage["updated_at"],
        "counts": counts,
    }
    next_index = STAGE_NAMES.index(stage_name) + 1
    if status == "completed" and next_index < len(STAGE_NAMES):
        next_stage = state["stages"][STAGE_NAMES[next_index]]
        if next_stage["status"] == "not_started":
            next_stage.update(status="ready", updated_at=_now())
    _write(project_path, state)
    return state


def fail_action(project_path: Path | str, action: str, error: BaseException | str) -> dict[str, Any]:
    state = load_workflow_state(project_path)
    stage = state["stages"][_action_stage(action)]
    name = error.__class__.__name__ if isinstance(error, BaseException) else "Error"
    stage.update(status="failed", updated_at=_now(), error=f"Action failed ({name}).")
    _write(project_path, state)
    return state


def reconcile_orphaned_running(project_path: Path | str, active_action: str | None = None) -> dict[str, Any]:
    state = load_workflow_state(project_path)
    active_stage = ACTION_STAGES.get(active_action or "")
    changed = False
    for name, stage in state["stages"].items():
        if stage["status"] == "running" and name != active_stage:
            stage.update(status="failed", updated_at=_now(), error="Action stopped before completion after application restart.")
            changed = True
    if changed:
        _write(project_path, state)
    return state


def save_workflow_state(project_path: Path | str, state: dict[str, Any]) -> None:
    """Atomically restore a previously validated ledger snapshot."""
    _write(project_path, state)


def migrate_legacy_workflow_state(project_path: Path | str) -> dict[str, Any]:
    project = Path(project_path)
    state = {
        "version": WORKFLOW_STATE_VERSION,
        "created_at": _now(),
        "migration": {"source": "legacy_artifacts", "migrated_at": _now()},
        "stages": {name: _stage("ready" if name == "collection" else "not_started") for name in STAGE_NAMES},
    }
    evidence = {
        "collection": _valid_json_with_keys(project / "collected" / "summary.json", {"total_papers", "platform_stats"}),
        "screening": _valid_jsonl(
            project / "filtered" / "included_papers.jsonl",
            empty_companion=project / "filtered" / "screening_stats.json",
            empty_companion_keys={"total_screened", "included_count", "excluded_count"},
        ),
        "retrieval": _valid_json_with_any_key(
            project / "pdfs" / "download_report.json",
            {"success", "successful", "downloaded", "success_count", "failed", "failed_count"},
        ),
        "extraction": _valid_jsonl(
            project / "extraction" / "extraction_results.jsonl",
            empty_companion=project / "extraction" / "extraction_stats.json",
            empty_companion_keys={"processed", "errors"},
        ),
        "categorization": _valid_json_with_any_key(
            project / "categorization" / "categorization_mapping.json", {"mapping", "categories"}
        ),
    }
    previous_completed = True
    for name in STAGE_NAMES:
        valid = evidence[name] and previous_completed
        if valid:
            stage = state["stages"][name]
            stage.update(status="completed", updated_at=_now())
            stage["last_valid"] = {"status": "completed", "attempt": 0, "updated_at": stage["updated_at"], "counts": {}}
        previous_completed = valid
    completed = [index for index, name in enumerate(STAGE_NAMES) if state["stages"][name]["status"] == "completed"]
    if completed and len(completed) < len(STAGE_NAMES):
        next_stage = state["stages"][STAGE_NAMES[len(completed)]]
        if next_stage["status"] == "not_started":
            next_stage["status"] = "ready"
    _write(project, state)
    return state


def _counts(result: dict[str, Any]) -> dict[str, int | float]:
    return {
        str(key): value
        for key, value in result.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _action_stage(action: str) -> str:
    try:
        return ACTION_STAGES[action]
    except KeyError as exc:
        raise ValueError(f"Unsupported workflow action: {action}") from exc


def _valid_json(path: Path) -> bool:
    try:
        return path.is_file() and isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
    except (OSError, json.JSONDecodeError):
        return False


def _valid_json_with_keys(path: Path, keys: set[str]) -> bool:
    data = _json_object(path)
    return data is not None and keys.issubset(data)


def _valid_json_with_any_key(path: Path, keys: set[str]) -> bool:
    data = _json_object(path)
    return data is not None and bool(keys.intersection(data))


def _json_object(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _valid_jsonl(
    path: Path,
    *,
    empty_companion: Path | None = None,
    empty_companion_keys: set[str] | None = None,
) -> bool:
    if not path.is_file():
        return False
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            if not empty_companion:
                return False
            return _valid_json_with_keys(empty_companion, empty_companion_keys or set())
        for line in lines:
            if line.strip() and not isinstance(json.loads(line), dict):
                return False
        return True
    except (OSError, json.JSONDecodeError):
        return False


def _validate(state: dict[str, Any]) -> None:
    if state.get("version") != WORKFLOW_STATE_VERSION or tuple((state.get("stages") or {}).keys()) != STAGE_NAMES:
        raise ValueError("Unsupported workflow state ledger")
    for stage in state["stages"].values():
        if set(stage) != {"status", "attempt", "updated_at", "error", "counts", "stale", "last_valid"}:
            raise ValueError("Invalid workflow stage fields")
        if stage.get("status") not in STAGE_STATUSES:
            raise ValueError("Invalid workflow stage status")
        if not isinstance(stage.get("attempt"), int) or stage["attempt"] < 0:
            raise ValueError("Invalid workflow stage attempt")
        if not isinstance(stage.get("counts"), dict) or not isinstance(stage.get("stale"), bool):
            raise ValueError("Invalid workflow stage metadata")


def _write(project_path: Path | str, state: dict[str, Any]) -> None:
    _validate(state)
    atomic_write_json(Path(project_path) / "workflow_state.json", state)
