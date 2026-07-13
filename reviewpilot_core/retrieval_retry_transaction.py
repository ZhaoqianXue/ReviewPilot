"""Durable abort-only recovery for failed-retrieval retry publication."""

from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import stat
from threading import Lock, RLock
from types import MappingProxyType
from typing import Any

from .atomic_files import atomic_write_json, atomic_write_jsonl
from .retrieval_retry import (
    RetryItem, RetryMergedFacts, RetryPlannedPdf, RetryPreparation, RetryPublicationPdf,
    RetryPublicationPlan, RetrySnapshot, current_retry_snapshot,
    _publication_pdf_fingerprint, retrieval_report_revision, stable_retry_id,
)
from .workflow_state import STAGE_NAMES, _validate as _validate_workflow_state, structured_action_outcome
from .workflow_state import load_workflow_state, save_workflow_state


PENDING_RETRY_FILE = ".retrieval_retry_pending.json"
_STAGING_PREFIX = ".retrieval_retry_staging_"
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
    return type(value) is str and value.startswith(prefix) and value not in {"", ".", ".."} and Path(value).name == value


def _digest(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _trusted_json(value: Any) -> Any:
    value_type = type(value)
    if value_type is MappingProxyType:
        result: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str or key in result:
                raise ValueError
            result[key] = _trusted_json(item)
        return result
    if value_type is tuple:
        return [_trusted_json(item) for item in value]
    if value is None or value_type in (str, int, bool):
        return value
    if value_type is float and math.isfinite(value):
        return value
    raise ValueError


def _plain_json(value: Any) -> Any:
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError
        return {key: _plain_json(item) for key, item in value.items()}
    if type(value) is list:
        return [_plain_json(item) for item in value]
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError


def _freeze_json(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze_json(item) for item in value)
    return value


def _trusted_item(value: Any) -> RetryItem:
    if type(value) is not RetryItem:
        raise ValueError
    retry_id, label, failure_class = value.retry_id, value.label, value.failure_class
    report_index, included_index = value.report_index, value.included_index
    if (not _digest(retry_id) or type(label) is not str or type(failure_class) is not str
            or type(report_index) is not int or report_index < 0
            or type(included_index) is not int or included_index < 0):
        raise ValueError
    return RetryItem(retry_id, label, failure_class, report_index, included_index)


def _trusted_preparation(value: Any, project: Path) -> RetryPreparation:
    if type(value) is not RetryPreparation:
        raise ValueError
    snapshot, selected_ids, items = value.snapshot, value.selected_ids, value.items
    included_rows, identity = value.included_rows, value._project_identity
    if (type(snapshot) is not RetrySnapshot or type(selected_ids) is not tuple
            or type(items) is not tuple or type(included_rows) is not tuple
            or type(identity) is not type(project) or not identity.is_absolute() or identity != project):
        raise ValueError
    revision, snapshot_items = snapshot.report_revision, snapshot.items
    if not _digest(revision) or type(snapshot_items) is not tuple:
        raise ValueError
    trusted_ids = tuple(selected_ids)
    if not trusted_ids or any(not _digest(retry_id) for retry_id in trusted_ids) or len(set(trusted_ids)) != len(trusted_ids):
        raise ValueError
    report = _trusted_json(snapshot.report)
    included = _trusted_json(snapshot.included)
    ledger = _trusted_json(snapshot.ledger)
    selected_rows = _trusted_json(included_rows)
    if (type(report) is not dict or type(included) is not list or type(ledger) is not dict
            or type(selected_rows) is not list or any(type(row) is not dict for row in included + selected_rows)):
        raise ValueError
    trusted_snapshot = RetrySnapshot(revision, _freeze_json(report), _freeze_json(included), _freeze_json(ledger),
        tuple(_trusted_item(item) for item in snapshot_items))
    return RetryPreparation(trusted_snapshot, trusted_ids, tuple(_trusted_item(item) for item in items),
        _freeze_json(selected_rows), identity)


def _encode_before(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _decode_before(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        raise ValueError
    try:
        raw = base64.b64decode(value, validate=True)
        if base64.b64encode(raw).decode("ascii") != value:
            raise ValueError
        decoded = json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError from exc
    if not isinstance(decoded, dict):
        raise ValueError
    canonical = json.dumps(decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if raw != canonical:
        raise ValueError
    return decoded


def _direct_regular(path: Path, parent: Path) -> bool:
    try:
        info = path.lstat()
        return stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and path.resolve(strict=True).parent == parent
    except (OSError, RuntimeError, ValueError):
        return False


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
        try:
            trusted = _trusted_preparation(preparation, project)
            authorities = ((project / "pdfs" / "download_report.json", project / "pdfs"),
                (project / "filtered" / "included_papers.jsonl", project / "filtered"),
                (project / "workflow_state.json", project))
            if any(not _direct_regular(path, parent) for path, parent in authorities):
                raise ValueError
            current = current_retry_snapshot(project)
            before_report, before_included, before_stage = current.mutable_fact_copies()
            before_ledger = load_workflow_state(project)
            trusted_report, trusted_included, trusted_stage = trusted.snapshot.mutable_fact_copies()
            current_items = {item.retry_id: item for item in current.items}
            if (current.report_revision != trusted.snapshot.report_revision
                    or (before_report, before_included, before_stage) != (trusted_report, trusted_included, trusted_stage)
                    or before_ledger.get("stages", {}).get("retrieval") != before_stage
                    or current.items != trusted.snapshot.items
                    or tuple(item.retry_id for item in trusted.items) != trusted.selected_ids
                    or trusted.items != tuple(current_items[retry_id] for retry_id in trusted.selected_ids)
                    or [before_included[item.included_index] for item in trusted.items] != list(trusted.included_rows)):
                raise ValueError
            candidates = tuple(f"retry-{current.report_revision}-{retry_id}.pdf" for retry_id in trusted.selected_ids)
            if not candidates or len(set(candidates)) != len(candidates) or any(not _basename(name, "retry-") for name in candidates):
                raise ValueError
            if any(_lexists(project / "pdfs" / name) for name in candidates):
                raise ValueError
        except Exception as exc:
            raise ValueError("Retry transaction preparation is stale") from exc
        with _GUARD:
            if project in _ACTIVE:
                raise ValueError("Retry transaction is already active")
            _ACTIVE.add(project)
        try:
            data = {
                "version": 1, "phase": "abort",
                "expected_revision": current.report_revision,
                "selected_ids": list(trusted.selected_ids),
                "staging_name": staging_name,
                "candidate_names": list(candidates),
                "before_json_b64": _encode_before({"report": deepcopy(before_report), "included": deepcopy(before_included), "ledger": deepcopy(before_ledger)}),
            }
            atomic_write_json(marker, data)
        except Exception as exc:
            with _GUARD:
                _ACTIVE.discard(project)
            raise ValueError("Retry transaction marker could not be written") from exc
        return RetryTransactionHandle(marker, staging_name, candidates)


def _read_marker(project: Path) -> dict[str, Any]:
    marker = project / PENDING_RETRY_FILE
    try:
        if not _direct_regular(marker, project):
            raise ValueError
        data = json.loads(marker.read_text(encoding="utf-8"))
        base = {"version", "phase", "expected_revision", "selected_ids", "staging_name", "candidate_names", "before_json_b64"}
        phase = data.get("phase") if type(data) is dict else None
        expected = base | ({"target_json_b64"} if "target_json_b64" in data else set())
        if (type(data) is not dict or set(data) != expected or type(data["version"]) is not int
                or data["version"] != 1 or type(phase) is not str or phase != "abort"):
            raise ValueError
        revision, ids = data["expected_revision"], data["selected_ids"]
        if not _digest(revision) or not isinstance(ids, list) or not ids or any(not _digest(item) for item in ids) or len(set(ids)) != len(ids):
            raise ValueError
        expected_candidates = [f"retry-{revision}-{item}.pdf" for item in ids]
        if data["candidate_names"] != expected_candidates or not _basename(data["staging_name"], _STAGING_PREFIX):
            raise ValueError
        before = _decode_before(data["before_json_b64"])
        if not isinstance(before, dict) or set(before) != {"report", "included", "ledger"} or not isinstance(before["report"], dict) or not isinstance(before["included"], list) or not isinstance(before["ledger"], dict):
            raise ValueError
        if any(not isinstance(row, dict) for row in before["included"]):
            raise ValueError
        stages = before["ledger"].get("stages")
        if not isinstance(stages, dict) or set(stages) != set(STAGE_NAMES):
            raise ValueError
        before["ledger"]["stages"] = {name: stages[name] for name in STAGE_NAMES}
        _validate_workflow_state(before["ledger"])
        stage = before["ledger"]["stages"]["retrieval"]
        if retrieval_report_revision(before["report"], stage) != revision:
            raise ValueError
        failed = before["report"].get("failed_papers")
        if not isinstance(failed, list) or not set(ids).issubset({stable_retry_id(row) for row in failed}):
            raise ValueError
        data["before"] = before
        if "target_json_b64" in data:
            data["target"] = _decode_target(data["target_json_b64"], data, project)
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
            if not _direct_regular(path, parent):
                raise ValueError
        for name in data["candidate_names"]:
            path = project / "pdfs" / name
            if _lexists(path) and not _direct_regular(path, project / "pdfs"):
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
            if not _direct_regular(path, project / "pdfs"):
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


def _contains_text(value: Any, needle: str) -> bool:
    if type(value) is str:
        return needle in value
    if type(value) is dict:
        return any(_contains_text(key, needle) or _contains_text(item, needle) for key, item in value.items())
    if type(value) is list:
        return any(_contains_text(item, needle) for item in value)
    return False


def _validate_target_provenance(project: Path, marker: dict[str, Any], report: dict[str, Any],
                                included: list[dict[str, Any]], pdfs: list[dict[str, Any]]) -> None:
    """Bind each selected success to its canonical row, detail, and candidate."""
    downloaded = report.get("downloaded")
    if type(downloaded) is not list or any(type(detail) is not dict for detail in downloaded):
        raise ValueError
    candidates = dict(zip(marker["selected_ids"], marker["candidate_names"]))
    pdf_ids = [pdf["retry_id"] for pdf in pdfs]
    if (len(pdf_ids) != len(set(pdf_ids))
            or pdf_ids != [retry_id for retry_id in marker["selected_ids"] if retry_id in set(pdf_ids)]):
        raise ValueError

    rows_by_id: dict[str, list[dict[str, Any]]] = {retry_id: [] for retry_id in marker["selected_ids"]}
    details_by_id: dict[str, list[dict[str, Any]]] = {retry_id: [] for retry_id in marker["selected_ids"]}
    for row in included:
        try:
            retry_id = stable_retry_id(row)
        except ValueError:
            continue
        if retry_id in rows_by_id:
            rows_by_id[retry_id].append(row)
    for detail in downloaded:
        try:
            retry_id = stable_retry_id(detail)
        except ValueError:
            continue
        if retry_id in details_by_id:
            details_by_id[retry_id].append(detail)

    success_ids = set(pdf_ids)
    for retry_id in marker["selected_ids"]:
        rows = rows_by_id[retry_id]
        details = details_by_id[retry_id]
        if len(rows) != 1:
            raise ValueError
        row = rows[0]
        destination = str(project / "pdfs" / candidates[retry_id])
        if retry_id in success_ids:
            if (row.get("pdf_downloaded") is not True or row.get("retrieval_status") != "downloaded"
                    or row.get("pdf_path") != destination or len(details) != 1):
                raise ValueError
            detail = details[0]
            if (detail.get("pdf_downloaded") is not True or detail.get("retrieval_status") != "downloaded"
                    or detail.get("path") != destination or detail.get("pdf_path") != destination):
                raise ValueError
        elif (details or row.get("pdf_downloaded") is True or row.get("retrieval_status") == "downloaded"
                or row.get("pdf_path") not in (None, "")):
            raise ValueError


def _material_output(stage: dict[str, Any]) -> bool:
    return (stage["last_valid"] is not None
        or (stage["status"] == "ready" and stage["attempt"] > 0)
        or (stage["status"] == "failed"
            and str(stage.get("error") or "").startswith("Action produced no successful outputs")))


def _validate_target_ledger(marker: dict[str, Any], ledger: dict[str, Any],
                            status: str, counts: dict[str, Any]) -> None:
    """Require the exact non-time result of one retry start and completion."""
    expected = deepcopy(marker["before"]["ledger"])
    stages = expected["stages"]
    retrieval_index = STAGE_NAMES.index("retrieval")
    retrieval = stages["retrieval"]

    if _material_output(retrieval):
        retrieval["stale"] = True
        for name in STAGE_NAMES[retrieval_index + 1:]:
            if _material_output(stages[name]):
                stages[name]["stale"] = True
    retrieval.update(status="running", attempt=retrieval["attempt"] + 1, error=None, counts={})

    is_rerun = retrieval["last_valid"] is not None
    target_retrieval = ledger["stages"]["retrieval"]
    error = None if status != "failed" else f"Action produced no successful outputs ({counts.get('failed', 0)} failed)."
    retrieval.update(status=status, updated_at=target_retrieval["updated_at"], error=error,
        counts=deepcopy(counts), stale=False)
    if status in {"completed", "partial"}:
        retrieval["last_valid"] = {
            "status": status,
            "attempt": retrieval["attempt"],
            "updated_at": retrieval["updated_at"],
            "counts": deepcopy(counts),
        }

    if is_rerun:
        for name in STAGE_NAMES[retrieval_index + 1:]:
            downstream = stages[name]
            if downstream["last_valid"] is not None or downstream["attempt"] > 0:
                downstream["stale"] = True

    extraction = stages["extraction"]
    if status in {"completed", "partial"} and extraction["status"] == "not_started":
        extraction.update(status="ready", updated_at=ledger["stages"]["extraction"]["updated_at"])

    if ledger != expected:
        raise ValueError


def _trusted_target(project: Path, marker: dict[str, Any], plan: RetryPublicationPlan,
                    target_ledger: dict[str, Any]) -> dict[str, Any]:
    if type(plan) is not RetryPublicationPlan or type(plan.merged_facts) is not RetryMergedFacts:
        raise ValueError
    merged = plan.merged_facts
    if (not _digest(plan.report_revision) or not _digest(merged.report_revision)
            or plan.report_revision != marker["expected_revision"] or merged.report_revision != plan.report_revision
            or type(plan.pdfs) is not tuple or type(merged.planned_pdfs) is not tuple
            or type(merged.status) is not str or type(merged.counts) is not MappingProxyType):
        raise ValueError
    report, included = _trusted_json(merged.report), _trusted_json(merged.included)
    counts = _trusted_json(merged.counts)
    ledger = _plain_json(target_ledger)
    if (type(report) is not dict or type(included) is not list or type(counts) is not dict
            or type(ledger) is not dict or any(type(row) is not dict for row in included)):
        raise ValueError
    _validate_workflow_state(ledger)
    status, outcome_counts = structured_action_outcome("retry-failed-downloads", {
        "success": report.get("success"), "failed": report.get("failed")})
    if merged.status != status or counts != outcome_counts:
        raise ValueError
    _validate_target_ledger(marker, ledger, status, outcome_counts)
    pdfs: list[dict[str, Any]] = []
    successful_names: list[str] = []
    concrete_path_type = type(Path())
    if len(plan.pdfs) != len(merged.planned_pdfs):
        raise ValueError
    for pdf, planned in zip(plan.pdfs, merged.planned_pdfs):
        if (type(planned) is not RetryPlannedPdf or type(pdf) is not RetryPublicationPdf
                or type(pdf.retry_id) is not str or not _digest(pdf.retry_id)
                or type(pdf.source_path) is not concrete_path_type or type(pdf.destination_path) is not concrete_path_type
                or type(pdf.source_size) is not int or pdf.source_size < 0 or not _digest(pdf.source_sha256)
                or planned.retry_id != pdf.retry_id or planned.source_path != pdf.source_path
                or planned.destination_path != pdf.destination_path):
            raise ValueError
        name = pdf.destination_path.name
        source_parent = project / marker["staging_name"] / "retry" / "pdfs"
        if (pdf.destination_path != project / "pdfs" / name or name not in marker["candidate_names"]
                or pdf.source_path.parent != source_parent or not _direct_regular(pdf.source_path, source_parent)):
            raise ValueError
        size, digest, _ = _publication_pdf_fingerprint(pdf.source_path, source_parent)
        if size != pdf.source_size or digest != pdf.source_sha256:
            raise ValueError
        successful_names.append(name)
        pdfs.append({"destination_name": name, "retry_id": pdf.retry_id,
            "size": pdf.source_size, "sha256": pdf.source_sha256})
    if successful_names != [name for name in marker["candidate_names"] if name in set(successful_names)]:
        raise ValueError
    _validate_target_provenance(project, marker, report, included, pdfs)
    if (_contains_text(report, str(project / marker["staging_name"]))
            or _contains_text(included, str(project / marker["staging_name"]))):
        raise ValueError
    return {"report": report, "included": included, "ledger": ledger, "pdfs": pdfs}


def _decode_target(encoded: Any, marker: dict[str, Any], project: Path) -> dict[str, Any]:
    target = _decode_before(encoded)
    if set(target) != {"report", "included", "ledger", "pdfs"} or type(target["report"]) is not dict \
            or type(target["included"]) is not list or type(target["ledger"]) is not dict or type(target["pdfs"]) is not list:
        raise ValueError
    if any(type(row) is not dict for row in target["included"]):
        raise ValueError
    report = target["report"]
    success, failed = report.get("success"), report.get("failed")
    downloaded, failures = report.get("downloaded"), report.get("failed_papers")
    if (type(success) is not int or success < 0 or type(failed) is not int or failed < 0
            or type(downloaded) is not list or type(failures) is not list
            or len(downloaded) != success or len(failures) != failed
            or any(type(row) is not dict for row in downloaded + failures)):
        raise ValueError
    status, counts = structured_action_outcome("retry-failed-downloads", {"success": success, "failed": failed})
    stages = target["ledger"].get("stages")
    if type(stages) is not dict or set(stages) != set(STAGE_NAMES):
        raise ValueError
    target["ledger"]["stages"] = {name: stages[name] for name in STAGE_NAMES}
    _validate_workflow_state(target["ledger"])
    _validate_target_ledger(marker, target["ledger"], status, counts)
    names: list[str] = []
    for pdf in target["pdfs"]:
        if (type(pdf) is not dict or set(pdf) != {"destination_name", "retry_id", "size", "sha256"}
                or not _basename(pdf["destination_name"], "retry-") or pdf["destination_name"] not in marker["candidate_names"]
                or pdf["destination_name"] != f"retry-{marker['expected_revision']}-{pdf['retry_id']}.pdf"
                or not _digest(pdf["retry_id"]) or type(pdf["size"]) is not int or pdf["size"] < 0 or not _digest(pdf["sha256"])):
            raise ValueError
        names.append(pdf["destination_name"])
    if len(names) != len(set(names)) or names != [name for name in marker["candidate_names"] if name in set(names)]:
        raise ValueError
    _validate_target_provenance(project, marker, report, target["included"], target["pdfs"])
    staging = str(project / marker["staging_name"])
    if _contains_text(report, staging) or _contains_text(target["included"], staging):
        raise ValueError
    return target


def record_retry_transaction_target(project_path: Path | str, publication_plan: RetryPublicationPlan,
                                    target_ledger: dict[str, Any]) -> None:
    project = _project_path(project_path)
    with _lock(project):
        with _GUARD:
            if project not in _ACTIVE:
                raise ValueError("Retry transaction is not active")
        marker = _read_marker(project)
        if marker["phase"] != "abort" or "target" in marker:
            raise ValueError("Retry transaction target cannot be recorded")
        try:
            target = _trusted_target(project, marker, publication_plan, target_ledger)
            encoded = _encode_before(target)
            if _encode_before(_decode_target(encoded, marker, project)) != encoded:
                raise ValueError
            raw = {key: marker[key] for key in ("version", "phase", "expected_revision", "selected_ids", "staging_name", "candidate_names", "before_json_b64")}
            raw["target_json_b64"] = encoded
            atomic_write_json(project / PENDING_RETRY_FILE, raw)
        except Exception as exc:
            raise ValueError("Retry transaction target is invalid") from exc
