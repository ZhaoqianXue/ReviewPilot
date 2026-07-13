"""Atomic, project-scoped workflow stage ledger."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from threading import Lock, RLock
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
_ACTION_PREREQUISITES = {
    "screen": "collection",
    "download-pdfs": "screening",
    "generate-schema": "screening",
    "finalize-schema": "screening",
    "edit-schema": "screening",
    "run-extraction": "retrieval",
    "suggest-categories": "extraction",
    "categorize": "extraction",
}
_LOCKS_GUARD = Lock()
_PROJECT_LOCKS: dict[Path, RLock] = {}


def _project_lock(project_path: Path | str) -> RLock:
    key = Path(project_path).resolve()
    with _LOCKS_GUARD:
        return _PROJECT_LOCKS.setdefault(key, RLock())


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
    with _project_lock(project):
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
    with _project_lock(project):
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
    with _project_lock(project_path):
        state = load_workflow_state(project_path)
        prerequisite = _ACTION_PREREQUISITES.get(action)
        if prerequisite and (state["stages"][prerequisite]["status"] not in {"completed", "partial"} or state["stages"][prerequisite]["stale"]):
            raise ValueError(f"Action '{action}' requires completed stage '{prerequisite}'")
        stage_name = _action_stage(action)
        stage = state["stages"][stage_name]
        if _has_material_output(stage):
            _stale_material_outputs(state, stage_name, include_current=True)
        stage.update(status="running", attempt=stage["attempt"] + 1, updated_at=_now(), error=None, counts={})
        _write(project_path, state)
        return state


def complete_action(project_path: Path | str, action: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    with _project_lock(project_path):
        state = load_workflow_state(project_path)
        stage_name = _action_stage(action)
        stage = state["stages"][stage_name]
        is_rerun = stage["last_valid"] is not None
        result = result or {}
        status, outcome_counts = structured_action_outcome(action, result)
        counts = outcome_counts if action in {"collect", "download-pdfs", "run-extraction"} else _counts(result)
        if action in _READY_ACTIONS:
            status = "ready"
        error = None if status != "failed" else f"Action produced no successful outputs ({counts.get('failed', 0)} failed)."
        stage.update(status=status, updated_at=_now(), error=error, counts=counts)
        if status in {"completed", "partial"}:
            stage["stale"] = False
            stage["last_valid"] = {"status": status, "attempt": stage["attempt"], "updated_at": stage["updated_at"], "counts": counts}
        elif status == "failed":
            # A structured terminal report is authoritative for this attempt even
            # when every item failed. Only outputs from later stages are obsolete.
            stage["stale"] = False
        next_index = STAGE_NAMES.index(stage_name) + 1
        if is_rerun:
            for downstream_name in STAGE_NAMES[next_index:]:
                downstream = state["stages"][downstream_name]
                if downstream["last_valid"] is not None or downstream["attempt"] > 0:
                    downstream["stale"] = True
        if status in {"completed", "partial"} and next_index < len(STAGE_NAMES):
            next_stage = state["stages"][STAGE_NAMES[next_index]]
            if next_stage["status"] == "not_started":
                next_stage.update(status="ready", updated_at=_now())
        _write(project_path, state)
        return state


def structured_action_outcome(action: str, result: dict[str, Any]) -> tuple[str, dict[str, int]]:
    """Classify terminal outcomes from structured agent contracts only."""
    if not isinstance(result, dict):
        raise ValueError("Structured action result must be an object")
    if "stats" in result and not isinstance(result["stats"], dict):
        raise ValueError("Structured action stats must be an object")
    nested = result.get("stats") or {}
    if action == "collect":
        stats = _consistent_value(result, nested, ("platform_stats",), "collection platform_stats")
        errors = _consistent_value(result, nested, ("platform_errors",), "collection platform_errors")
        total = _consistent_count(result, nested, ("total", "total_papers"), "collection total")
        if not isinstance(stats, dict) or not all(isinstance(key, str) and key and _nonnegative_int(value) for key, value in stats.items()):
            raise ValueError("Invalid collection platform_stats")
        if not isinstance(errors, dict) or not all(isinstance(key, str) and key and isinstance(value, str) for key, value in errors.items()):
            raise ValueError("Invalid collection platform_errors")
        if sum(stats.values()) != total:
            raise ValueError("Collection total does not match platform_stats")
        if not set(errors).issubset(stats):
            raise ValueError("Collection errors must reference reported sources")
        if any(stats[source] != 0 for source in errors):
            raise ValueError("Failed collection sources must report zero rows")
        succeeded = len(set(stats) - set(errors))
        failed = len(errors)
        counts = {"succeeded": succeeded, "failed": failed, "collected": total}
    elif action == "download-pdfs":
        failed = _consistent_count(result, nested, ("failed",), "retrieval failed")
        success = _consistent_count(result, nested, ("success", "successful"), "retrieval success")
        succeeded = success
        counts = {"succeeded": succeeded, "failed": failed}
    elif action == "run-extraction":
        failed = _consistent_count(result, nested, ("errors", "failed"), "extraction errors")
        processed = _consistent_count(result, nested, ("processed", "success"), "extraction processed")
        succeeded = processed
        counts = {"succeeded": succeeded, "failed": failed}
    else:
        return "completed", {}
    if not failed:
        return "completed", counts
    return ("partial" if counts["succeeded"] > 0 else "failed"), counts


def _consistent_value(primary: dict[str, Any], secondary: dict[str, Any], keys: tuple[str, ...], label: str) -> Any:
    values = []
    for source in (primary, secondary):
        for key in keys:
            if key in source:
                values.append(source[key])
    if not values:
        raise ValueError(f"Missing {label}")
    if any(value != values[0] for value in values[1:]):
        raise ValueError(f"Conflicting {label}")
    return values[0]


def _consistent_count(primary: dict[str, Any], secondary: dict[str, Any], keys: tuple[str, ...], label: str) -> int:
    value = _consistent_value(primary, secondary, keys, label)
    if not _nonnegative_int(value):
        raise ValueError(f"Invalid {label}")
    return value


def fail_action(project_path: Path | str, action: str, error: BaseException | str) -> dict[str, Any]:
    with _project_lock(project_path):
        state = load_workflow_state(project_path)
        stage_name = _action_stage(action)
        stage = state["stages"][stage_name]
        had_material_output = stage["last_valid"] is not None
        name = error.__class__.__name__ if isinstance(error, BaseException) else "Error"
        stage.update(status="failed", updated_at=_now(), error=f"Action failed ({name}).")
        if had_material_output:
            _stale_material_outputs(state, stage_name, include_current=True)
        _write(project_path, state)
        return state


def reconcile_orphaned_running(project_path: Path | str, active_action: str | None = None) -> dict[str, Any]:
    with _project_lock(project_path):
        state = load_workflow_state(project_path)
        active_stage = ACTION_STAGES.get(active_action or "")
        changed = False
        for name, stage in state["stages"].items():
            if stage["status"] == "running" and name != active_stage:
                stage.update(status="failed", updated_at=_now(), error="Action stopped before completion after application restart.")
                if stage["last_valid"] is not None:
                    stage["counts"] = {}
                    _stale_material_outputs(state, name, include_current=True)
                changed = True
        if changed:
            _write(project_path, state)
        return state


def save_workflow_state(project_path: Path | str, state: dict[str, Any]) -> None:
    """Atomically restore a previously validated ledger snapshot."""
    with _project_lock(project_path):
        _write(project_path, state)


def mark_stages_stale(project_path: Path | str, names: list[str]) -> dict[str, Any]:
    """Atomically mark authoritative outputs stale without deleting artifacts."""
    with _project_lock(project_path):
        state = load_workflow_state(project_path)
        unknown = set(names) - set(STAGE_NAMES)
        if unknown:
            raise ValueError(f"Unknown workflow stages: {', '.join(sorted(unknown))}")
        for name in names:
            state["stages"][name]["stale"] = True
        _write(project_path, state)
        return state


def migrate_legacy_workflow_state(project_path: Path | str) -> dict[str, Any]:
    project = Path(project_path)
    with _project_lock(project):
        path = project / "workflow_state.json"
        if path.exists():
            return load_workflow_state(project)
        state = {
            "version": WORKFLOW_STATE_VERSION,
            "created_at": _now(),
            "migration": {"source": "legacy_artifacts", "migrated_at": _now()},
            "stages": {name: _stage("ready" if name == "collection" else "not_started") for name in STAGE_NAMES},
        }
        evidence = {
            "collection": _valid_collection(project / "collected" / "summary.json"),
            "screening": _valid_identity_jsonl(
                project / "filtered" / "included_papers.jsonl",
                empty_companion=project / "filtered" / "screening_stats.json",
                empty_companion_keys={"total_screened", "included_count", "excluded_count"},
            ),
            "retrieval": _valid_retrieval(project / "pdfs" / "download_report.json"),
            "extraction": _valid_identity_jsonl(
                project / "extraction" / "extraction_results.jsonl",
                empty_companion=project / "extraction" / "extraction_stats.json",
                empty_companion_keys={"processed", "errors"},
            ),
            "categorization": _valid_categorization(project / "categorization" / "categorization_mapping.json"),
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


def _stale_material_outputs(state: dict[str, Any], stage_name: str, *, include_current: bool) -> None:
    start = STAGE_NAMES.index(stage_name)
    for index, name in enumerate(STAGE_NAMES[start:], start=start):
        stage = state["stages"][name]
        if (index == start and include_current) or _has_material_output(stage):
            stage["stale"] = True


def _has_material_output(stage: dict[str, Any]) -> bool:
    return (
        stage["last_valid"] is not None
        or (stage["status"] == "ready" and stage["attempt"] > 0)
        or (stage["status"] == "failed" and str(stage.get("error") or "").startswith("Action produced no successful outputs"))
    )


def _action_stage(action: str) -> str:
    try:
        return ACTION_STAGES[action]
    except KeyError as exc:
        raise ValueError(f"Unsupported workflow action: {action}") from exc


def _nonnegative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _valid_collection(path: Path) -> bool:
    data = _json_object(path)
    if data is None or not _nonnegative_int(data.get("total_papers")):
        return False
    stats = data.get("platform_stats")
    return (
        isinstance(stats, dict)
        and all(isinstance(key, str) and _nonnegative_int(value) for key, value in stats.items())
        and sum(stats.values()) == data["total_papers"]
    )


def _valid_retrieval(path: Path) -> bool:
    data = _json_object(path)
    return data is not None and _nonnegative_int(data.get("success")) and _nonnegative_int(data.get("failed"))


def _valid_categorization(path: Path) -> bool:
    data = _json_object(path)
    if data is None or not isinstance(data.get("mapping"), dict) or not isinstance(data.get("categories"), list):
        return False
    categories = data["categories"]
    if not all(isinstance(category, str) and bool(category.strip()) for category in categories):
        return False
    allowed = set(categories)
    for paper, assigned in data["mapping"].items():
        if not isinstance(paper, str) or not paper.strip():
            return False
        values = [assigned] if isinstance(assigned, str) else assigned
        if not isinstance(values, list) or not values:
            return False
        if not all(isinstance(value, str) and bool(value.strip()) and value in allowed for value in values):
            return False
    return True


def _json_object(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _valid_identity_jsonl(
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
            companion = _json_object(empty_companion)
            required = empty_companion_keys or set()
            if companion is None or not required.issubset(companion):
                return False
            if required == {"total_screened", "included_count", "excluded_count"}:
                values = [companion[key] for key in required]
                return all(_nonnegative_int(value) for value in values) and companion["included_count"] + companion["excluded_count"] == companion["total_screened"]
            return all(companion.get(key) == 0 for key in required)
        for line in lines:
            row = json.loads(line)
            if not isinstance(row, dict) or not any(isinstance(row.get(key), str) and row[key].strip() for key in ("paper_id", "id", "doi", "title")):
                return False
        return True
    except (OSError, json.JSONDecodeError):
        return False


def _validate(state: dict[str, Any]) -> None:
    if state.get("version") != WORKFLOW_STATE_VERSION or tuple((state.get("stages") or {}).keys()) != STAGE_NAMES:
        raise ValueError("Unsupported workflow state ledger")
    if not _valid_timestamp(state.get("created_at")):
        raise ValueError("Invalid workflow state created_at")
    if "migration" in state:
        migration = state["migration"]
        if not isinstance(migration, dict) or set(migration) != {"source", "migrated_at"}:
            raise ValueError("Invalid workflow migration metadata")
        if migration["source"] != "legacy_artifacts" or not _valid_timestamp(migration["migrated_at"]):
            raise ValueError("Invalid workflow migration metadata")
    for stage in state["stages"].values():
        if set(stage) != {"status", "attempt", "updated_at", "error", "counts", "stale", "last_valid"}:
            raise ValueError("Invalid workflow stage fields")
        if stage.get("status") not in STAGE_STATUSES:
            raise ValueError("Invalid workflow stage status")
        if type(stage.get("attempt")) is not int or stage["attempt"] < 0:
            raise ValueError("Invalid workflow stage attempt")
        if not _valid_timestamp(stage.get("updated_at")):
            raise ValueError("Invalid workflow stage updated_at")
        if stage.get("error") is not None and not isinstance(stage["error"], str):
            raise ValueError("Invalid workflow stage error")
        if not _valid_counts(stage.get("counts")) or type(stage.get("stale")) is not bool:
            raise ValueError("Invalid workflow stage metadata")
        last_valid = stage.get("last_valid")
        if last_valid is not None:
            if set(last_valid) != {"status", "attempt", "updated_at", "counts"}:
                raise ValueError("Invalid workflow last-valid fields")
            if last_valid["status"] not in STAGE_STATUSES or type(last_valid["attempt"]) is not int or last_valid["attempt"] < 0:
                raise ValueError("Invalid workflow last-valid state")
            if not _valid_timestamp(last_valid["updated_at"]) or not _valid_counts(last_valid["counts"]):
                raise ValueError("Invalid workflow last-valid metadata")


def _valid_counts(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(key, str)
        and isinstance(item, (int, float))
        and not isinstance(item, bool)
        and math.isfinite(item)
        and item >= 0
        for key, item in value.items()
    )


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _write(project_path: Path | str, state: dict[str, Any]) -> None:
    with _project_lock(project_path):
        _validate(state)
        atomic_write_json(Path(project_path) / "workflow_state.json", state)
