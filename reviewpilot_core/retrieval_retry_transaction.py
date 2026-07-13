"""Durable abort-only recovery for failed-retrieval retry publication."""

from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import stat
from threading import Lock, RLock
from typing import Any

from .atomic_files import atomic_write_json, atomic_write_jsonl
from .retrieval_retry import RetryPreparation, current_retry_snapshot, retrieval_report_revision, stable_retry_id
from .workflow_state import _validate as _validate_workflow_state
from .workflow_state import load_workflow_state, save_workflow_state


PENDING_RETRY_FILE = ".retrieval_retry_pending.json"
_STAGING_PREFIX = ".retrieval_retry_staging_"
_ABSOLUTE_TAG = "$reviewpilot_absolute_path"
_GUARD = Lock()
_ACTIVE: set[Path] = set()
_LOCKS: dict[Path, RLock] = {}


@dataclass(frozen=True)
class RetryTransactionHandle:
    marker_path: Path
    staging_name: str
    candidate_names: tuple[str, ...]


def _lock(project: Path) -> RLock:
    with _GUARD:
        return _LOCKS.setdefault(project, RLock())


def _project_path(value: Path | str) -> Path:
    try:
        project = Path(value)
        if project.is_symlink() or not project.is_dir():
            raise ValueError
        resolved = project.resolve(strict=True)
        if not resolved.is_absolute():
            raise ValueError
        for name in ("pdfs", "filtered"):
            child = resolved / name
            if child.is_symlink() or not child.is_dir() or child.resolve(strict=True).parent != resolved:
                raise ValueError
        return resolved
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ValueError("Retry transaction project is unsafe") from exc


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _basename(value: str, prefix: str = "") -> bool:
    return isinstance(value, str) and value.startswith(prefix) and value not in {"", ".", ".."} and Path(value).name == value


def _digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _encode(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, str) and Path(value).is_absolute():
        return {_ABSOLUTE_TAG: base64.b64encode(value.encode("utf-8")).decode("ascii")}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {_ABSOLUTE_TAG} and isinstance(value[_ABSOLUTE_TAG], str):
            try:
                decoded = base64.b64decode(value[_ABSOLUTE_TAG], validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                raise ValueError from exc
            if not Path(decoded).is_absolute():
                raise ValueError
            return decoded
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def begin_retry_transaction(
    project_path: Path | str,
    preparation: RetryPreparation,
    staging_name: str,
    staging_path: Path | str,
) -> RetryTransactionHandle:
    """Reserve a project and durably record abort facts before later mutation."""
    project = _project_path(project_path)
    with _lock(project):
        marker = project / PENDING_RETRY_FILE
        try:
            staging_matches = Path(staging_path).resolve(strict=False) == project / staging_name
        except (OSError, RuntimeError, TypeError):
            staging_matches = False
        if _lexists(marker) or not _basename(staging_name, _STAGING_PREFIX) or not staging_matches:
            raise ValueError("Retry transaction cannot begin safely")
        if _lexists(project / staging_name):
            raise ValueError("Retry transaction cannot begin safely")
        if type(preparation) is not RetryPreparation or preparation._project_identity != project:
            raise ValueError("Retry transaction preparation is stale")
        try:
            current = current_retry_snapshot(project)
            before_report, before_included, before_stage = preparation.snapshot.mutable_fact_copies()
            before_ledger = load_workflow_state(project)
            if (current.report_revision != preparation.snapshot.report_revision
                    or current.mutable_fact_copies() != (before_report, before_included, before_stage)
                    or before_ledger.get("stages", {}).get("retrieval") != before_stage
                    or current.items != preparation.snapshot.items
                    or tuple(item.retry_id for item in preparation.items) != preparation.selected_ids
                    or [before_included[item.included_index] for item in preparation.items] != list(preparation.included_rows)
                    or any(not _digest(item) for item in preparation.selected_ids)):
                raise ValueError
            candidates = tuple(f"retry-{current.report_revision}-{retry_id}.pdf" for retry_id in preparation.selected_ids)
            if not candidates or len(set(candidates)) != len(candidates) or any(not _basename(name, "retry-") for name in candidates):
                raise ValueError
            if any(_lexists(project / "pdfs" / name) for name in candidates):
                raise ValueError
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ValueError("Retry transaction preparation is stale") from exc
        with _GUARD:
            if project in _ACTIVE:
                raise ValueError("Retry transaction is already active")
            _ACTIVE.add(project)
        data = {
            "version": 1, "phase": "abort",
            "expected_revision": current.report_revision,
            "selected_ids": list(preparation.selected_ids),
            "staging_name": staging_name,
            "candidate_names": list(candidates),
            "before": _encode({"report": deepcopy(before_report), "included": deepcopy(before_included), "ledger": deepcopy(before_ledger)}),
        }
        try:
            atomic_write_json(marker, data)
        except Exception as exc:
            with _GUARD:
                _ACTIVE.discard(project)
            raise ValueError("Retry transaction marker could not be written") from exc
        return RetryTransactionHandle(marker, staging_name, candidates)


def _read_marker(project: Path) -> dict[str, Any]:
    marker = project / PENDING_RETRY_FILE
    try:
        if marker.is_symlink() or not marker.is_file() or marker.resolve(strict=True).parent != project:
            raise ValueError
        data = json.loads(marker.read_text(encoding="utf-8"))
        expected = {"version", "phase", "expected_revision", "selected_ids", "staging_name", "candidate_names", "before"}
        if not isinstance(data, dict) or set(data) != expected or data["version"] != 1 or data["phase"] != "abort":
            raise ValueError
        revision, ids = data["expected_revision"], data["selected_ids"]
        if not _digest(revision) or not isinstance(ids, list) or not ids or any(not _digest(item) for item in ids) or len(set(ids)) != len(ids):
            raise ValueError
        expected_candidates = [f"retry-{revision}-{item}.pdf" for item in ids]
        if data["candidate_names"] != expected_candidates or not _basename(data["staging_name"], _STAGING_PREFIX):
            raise ValueError
        before = _decode(data["before"])
        if not isinstance(before, dict) or set(before) != {"report", "included", "ledger"} or not isinstance(before["report"], dict) or not isinstance(before["included"], list) or not isinstance(before["ledger"], dict):
            raise ValueError
        if any(not isinstance(row, dict) for row in before["included"]):
            raise ValueError
        _validate_workflow_state(before["ledger"])
        stage = before["ledger"]["stages"]["retrieval"]
        if retrieval_report_revision(before["report"], stage) != revision:
            raise ValueError
        failed = before["report"].get("failed_papers")
        if not isinstance(failed, list) or not set(ids).issubset({stable_retry_id(row) for row in failed}):
            raise ValueError
        data["before"] = before
        return data
    except (OSError, RuntimeError, TypeError, KeyError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("Pending retry transaction cannot be recovered safely") from exc


def _restore(project: Path) -> bool:
    data = _read_marker(project)
    before = data["before"]
    try:
        for path, parent in ((project / "pdfs" / "download_report.json", project / "pdfs"),
                (project / "filtered" / "included_papers.jsonl", project / "filtered"),
                (project / "workflow_state.json", project)):
            if path.is_symlink() or not path.is_file() or path.resolve(strict=True).parent != parent:
                raise ValueError
        for name in data["candidate_names"]:
            path = project / "pdfs" / name
            if _lexists(path) and not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError
        staging = project / data["staging_name"]
        if _lexists(staging) and (staging.is_symlink() or not staging.is_dir() or staging.resolve(strict=True).parent != project):
            raise ValueError
        atomic_write_json(project / "pdfs" / "download_report.json", before["report"])
        atomic_write_jsonl(project / "filtered" / "included_papers.jsonl", before["included"])
        save_workflow_state(project, before["ledger"])
        for name in data["candidate_names"]:
            path = project / "pdfs" / name
            if not _lexists(path):
                continue
            mode = path.lstat().st_mode
            if path.parent != project / "pdfs" or not stat.S_ISREG(mode):
                raise ValueError
            path.unlink()
        if _lexists(staging):
            shutil.rmtree(staging)
        (project / PENDING_RETRY_FILE).unlink()
        return True
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ValueError("Pending retry transaction cannot be recovered safely") from exc


def reconcile_retry_transaction(project_path: Path | str) -> bool:
    project = _project_path(project_path)
    with _lock(project):
        marker = project / PENDING_RETRY_FILE
        if not _lexists(marker):
            return False
        with _GUARD:
            if project in _ACTIVE:
                return False
        return _restore(project)


def abort_retry_transaction(project_path: Path | str) -> bool:
    project = _project_path(project_path)
    try:
        with _lock(project):
            if not _lexists(project / PENDING_RETRY_FILE):
                return False
            return _restore(project)
    finally:
        with _GUARD:
            _ACTIVE.discard(project)


def abandon_retry_transaction(project_path: Path | str) -> None:
    try:
        project = Path(project_path).resolve()
    except (OSError, RuntimeError, TypeError):
        return
    with _GUARD:
        _ACTIVE.discard(project)
