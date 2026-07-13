"""Canonical search setup revisions and downstream invalidation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import Lock, RLock
from typing import Any

from .atomic_files import atomic_write_json
from .model_policy import DEFAULT_MAX_RESULTS_PER_PLATFORM, LEAD_AGENT_DEV_MODEL
from .workflow_state import STAGE_NAMES, load_workflow_state, mark_stages_stale, save_workflow_state

PENDING_SETUP_FILE = ".setup_update_pending.json"
_ACTIVE_LOCK = Lock()
_ACTIVE_TRANSACTIONS: set[Path] = set()
_RECOVERY_LOCKS: dict[Path, RLock] = {}


def _recovery_lock(project: Path) -> RLock:
    key = project.resolve()
    with _ACTIVE_LOCK:
        return _RECOVERY_LOCKS.setdefault(key, RLock())

_FIELDS = ("project_name", "description", "primary_topic", "domain", "search_terms", "search_queries", "platforms", "max_results", "source_limits", "date_range", "model", "derive_search_terms")
_DEPENDENCY_FIELDS = set(_FIELDS) - {"project_name"}


def normalize_setup(config: dict[str, Any]) -> dict[str, Any]:
    normalized = {key: config.get(key) for key in _FIELDS}
    normalized["derive_search_terms"] = bool(normalized.get("derive_search_terms"))
    normalized["project_name"] = str(normalized.get("project_name") or "").strip()
    normalized["description"] = str(normalized.get("description") or "").strip()
    normalized["primary_topic"] = str(normalized.get("primary_topic") or ("" if normalized["derive_search_terms"] else normalized["project_name"])).strip()
    normalized["domain"] = str(normalized.get("domain") or "").strip()
    normalized["search_terms"] = str(normalized.get("search_terms") or normalized["description"]).strip()
    normalized["platforms"] = [str(item).strip().lower() for item in normalized.get("platforms") or ["pubmed", "arxiv", "openalex"]]
    try:
        normalized["max_results"] = int(normalized.get("max_results") or DEFAULT_MAX_RESULTS_PER_PLATFORM)
    except (TypeError, ValueError):
        normalized["max_results"] = DEFAULT_MAX_RESULTS_PER_PLATFORM
    limits = normalized.get("source_limits") or {}
    normalized_limits = {}
    for key in normalized["platforms"]:
        try:
            normalized_limits[key] = int(limits.get(key, normalized["max_results"]))
        except (TypeError, ValueError):
            normalized_limits[key] = normalized["max_results"]
    normalized["source_limits"] = normalized_limits
    if normalized_limits:
        normalized["max_results"] = max(normalized_limits.values())
    normalized["search_queries"] = normalized.get("search_queries") or [{"name": "main", "query": normalized.get("search_terms") or ""}]
    normalized["date_range"] = normalized.get("date_range") or {"start": "", "end": ""}
    normalized["model"] = str(normalized.get("model") or LEAD_AGENT_DEV_MODEL)
    return normalized


def setup_revision(config: dict[str, Any]) -> str:
    payload = json.dumps(normalize_setup(config), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def materially_changes_dependencies(old: dict[str, Any], new: dict[str, Any]) -> bool:
    left, right = normalize_setup(old), normalize_setup(new)
    return any(left[key] != right[key] for key in _DEPENDENCY_FIELDS)


def affected_stages(project_path: Path | str) -> list[str]:
    state = load_workflow_state(project_path)
    return [name for name in STAGE_NAMES if _stage_has_material_output(state["stages"][name])]


def _stage_has_material_output(stage: dict[str, Any]) -> bool:
    last_valid = stage["last_valid"]
    return last_valid is not None or (stage["status"] == "ready" and stage["attempt"] > (last_valid or {}).get("attempt", 0))


def stale_replacement_stages(project_path: Path | str, action_stage: str) -> list[str]:
    state = load_workflow_state(project_path)
    start = STAGE_NAMES.index(action_stage)
    return [name for name in STAGE_NAMES[start:] if state["stages"][name]["stale"] and state["stages"][name]["last_valid"] is not None]


def begin_setup_transaction(project_path: Path | str, current: dict[str, Any], target: dict[str, Any], affected: list[str]) -> Path:
    project = Path(project_path)
    marker = project / PENDING_SETUP_FILE
    with _ACTIVE_LOCK:
        _ACTIVE_TRANSACTIONS.add(project.resolve())
    try:
        atomic_write_json(marker, {
            "version": 1,
            "phase": "apply",
            "current_revision": setup_revision(current),
            "target_revision": setup_revision(target),
            "current_setup": current,
            "target_setup": target,
            "affected_stages": affected,
            "ledger_before": load_workflow_state(project),
        })
    except Exception:
        with _ACTIVE_LOCK:
            _ACTIVE_TRANSACTIONS.discard(project.resolve())
        raise
    return marker


def update_setup_transaction_target(marker: Path, target: dict[str, Any]) -> None:
    data = json.loads(marker.read_text(encoding="utf-8"))
    data.update(target_setup=target, target_revision=setup_revision(target))
    atomic_write_json(marker, data)


def mark_setup_transaction_aborting(marker: Path) -> None:
    data = json.loads(marker.read_text(encoding="utf-8"))
    data["phase"] = "abort"
    atomic_write_json(marker, data)


def finish_setup_transaction(project_path: Path | str) -> None:
    project = Path(project_path)
    try:
        (project / PENDING_SETUP_FILE).unlink(missing_ok=True)
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE_TRANSACTIONS.discard(project.resolve())


def abandon_setup_transaction(project_path: Path | str) -> None:
    """Leave durable recovery data while releasing the live-process reservation."""
    with _ACTIVE_LOCK:
        _ACTIVE_TRANSACTIONS.discard(Path(project_path).resolve())


def reconcile_setup_transaction(project_path: Path | str) -> bool:
    project = Path(project_path)
    with _recovery_lock(project):
        marker = project / PENDING_SETUP_FILE
        if not marker.exists():
            return False
        with _ACTIVE_LOCK:
            if project.resolve() in _ACTIVE_TRANSACTIONS:
                return False
        try:
            pending = json.loads(marker.read_text(encoding="utf-8"))
            current_file = json.loads((project / "search_conditions.json").read_text(encoding="utf-8"))
            phase = pending.get("phase", "abort")
            if pending.get("version") != 1 or phase not in {"apply", "abort"} or not isinstance(pending.get("affected_stages"), list):
                raise ValueError("Invalid pending setup transaction")
            revision = setup_revision(current_file)
            if phase == "abort":
                atomic_write_json(project / "search_conditions.json", pending["current_setup"])
                save_workflow_state(project, pending["ledger_before"])
            elif revision == pending["target_revision"]:
                mark_stages_stale(project, pending["affected_stages"])
            elif revision == pending["current_revision"]:
                save_workflow_state(project, pending["ledger_before"])
            else:
                atomic_write_json(project / "search_conditions.json", pending["current_setup"])
                save_workflow_state(project, pending["ledger_before"])
        except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("Pending setup update cannot be recovered safely") from exc
        finish_setup_transaction(project)
        return True


def read_consistent_setup(project_path: Path | str) -> dict[str, Any]:
    project = Path(project_path)
    reconcile_setup_transaction(project)
    marker = project / PENDING_SETUP_FILE
    if marker.exists():
        try:
            pending = json.loads(marker.read_text(encoding="utf-8"))
            current = pending["current_setup"]
            if not isinstance(current, dict):
                raise TypeError
            return current
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Pending setup update cannot be read safely") from exc
    try:
        config = json.loads((project / "search_conditions.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Search setup is unreadable") from exc
    if not isinstance(config, dict):
        raise ValueError("Search setup is invalid")
    return config
