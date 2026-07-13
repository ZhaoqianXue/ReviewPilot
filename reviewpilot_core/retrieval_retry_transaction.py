"""Durable recovery and target recording for failed-retrieval retry publication."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import stat
from threading import Lock, RLock
import tempfile
from types import MappingProxyType
from typing import Any, Iterator

from .atomic_files import atomic_write_json, atomic_write_jsonl
from .retrieval_retry import (
    RetryItem, RetryMergedFacts, RetryPlannedPdf, RetryPreparation, RetryPublicationPdf,
    RetryPublicationPlan, RetrySnapshot, StagedRetryOutcome, StagedRetryPdf, current_retry_snapshot,
    _authoritative_fingerprint, _publication_pdf_fingerprint, _validate_merged_retry_delta,
    retrieval_report_revision, run_retry_staging, stable_retry_id,
)
from .workflow_state import STAGE_NAMES, _validate as _validate_workflow_state, structured_action_outcome
from .workflow_state import load_workflow_state, save_workflow_state


PENDING_RETRY_FILE = ".retrieval_retry_pending.json"
_STAGING_PREFIX = ".retrieval_retry_staging_"
_GUARD = Lock()
_ACTIVE: dict[Path, str] = {}
_LOCKS: dict[Path, RLock] = {}
_FILE_LOCKS: dict[Path, tuple[int, int, int]] = {}
_RAW_MARKER_BYTES = "_raw_marker_bytes"
_MARKER_IDENTITY = "_marker_identity"
_FIXED_AUTHORITIES = (
    ("pdfs/download_report.json", "pdfs"),
    ("filtered/included_papers.jsonl", "filtered"),
    ("workflow_state.json", ""),
)
_IDENTITY_KEYS = {"device", "inode", "size", "mtime_ns", "ctime_ns"}


@dataclass(frozen=True)
class RetryTransactionHandle:
    marker_path: Path
    staging_name: str
    candidate_names: tuple[str, ...]
    transaction_id: str


@dataclass(frozen=True)
class RetryAuthorityClassification:
    kinds: tuple[str, str, str]
    identities: tuple[tuple[int, int, int, int, int, int], ...]


@dataclass(frozen=True)
class RetryPdfCommitSnapshot:
    baseline_identities: tuple[tuple[int, int, int, int, int, int], ...]
    receipt_identities: tuple[tuple[int, int, int, int, int, int], ...]
    staging_signature: tuple[tuple[str, tuple[int, int, int, int, int, int]], ...]


@dataclass(frozen=True)
class RetryStagingSubsetSnapshot:
    signature: tuple[tuple[str, tuple[int, int, int, int, int, int]], ...]


def _lock(project: Path) -> RLock:
    with _GUARD:
        return _LOCKS.setdefault(project, RLock())


@contextmanager
def _project_file_lock(project: Path) -> Iterator[None]:
    """Serialize retry marker operations across cooperating processes."""
    process_id = os.getpid()
    with _GUARD:
        held = _FILE_LOCKS.get(project)
        if held is not None and held[0] != process_id:
            _, inherited_descriptor, _ = _FILE_LOCKS.pop(project)
            os.close(inherited_descriptor)
            held = None
        if held is not None:
            _, descriptor, depth = held
            _FILE_LOCKS[project] = (process_id, descriptor, depth + 1)
        else:
            descriptor = -1
    if held is not None:
        try:
            yield
        finally:
            with _GUARD:
                current_process, current_descriptor, depth = _FILE_LOCKS[project]
                if current_process != process_id:
                    raise RuntimeError("Retry transaction file lock owner is invalid")
                if depth == 1:
                    _FILE_LOCKS.pop(project)
                else:
                    _FILE_LOCKS[project] = (process_id, current_descriptor, depth - 1)
        return

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(project, flags)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        opened = os.fstat(descriptor)
        current = project.lstat()
        if (not stat.S_ISDIR(opened.st_mode)
                or _file_identity(opened) != _file_identity(current)
                or project.resolve(strict=True) != project):
            raise ValueError
        with _GUARD:
            if project in _FILE_LOCKS:
                raise ValueError
            _FILE_LOCKS[project] = (process_id, descriptor, 1)
    except (OSError, RuntimeError, ValueError) as exc:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        raise ValueError("Retry transaction project lock is unsafe") from exc
    try:
        yield
    finally:
        with _GUARD:
            current_process, current_descriptor, depth = _FILE_LOCKS.get(project, (-1, -1, 0))
            if current_process != process_id or current_descriptor != descriptor or depth != 1:
                raise RuntimeError("Retry transaction file lock state is invalid")
            _FILE_LOCKS.pop(project)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


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


def _decode_pdf_baseline(value: Any) -> dict[str, Any]:
    baseline = _decode_before(value)
    if (type(baseline) is not dict or set(baseline) != {"fixed", "pdfs"}
            or type(baseline["fixed"]) is not list or type(baseline["pdfs"]) is not list):
        raise ValueError
    fixed_names = [name for name, _ in _FIXED_AUTHORITIES]
    if len(baseline["fixed"]) != len(fixed_names):
        raise ValueError
    for item, name in zip(baseline["fixed"], fixed_names):
        if type(item) is not dict or set(item) != {"name"} | _IDENTITY_KEYS or item["name"] != name:
            raise ValueError
        if any(type(item[key]) is not int or item[key] < 0 for key in _IDENTITY_KEYS):
            raise ValueError
    names: list[str] = []
    for pdf in baseline["pdfs"]:
        if (type(pdf) is not dict or set(pdf) != {"name", "sha256"} | _IDENTITY_KEYS
                or not _basename(pdf["name"]) or not pdf["name"].endswith(".pdf")
                or not _digest(pdf["sha256"])
                or any(type(pdf[key]) is not int or pdf[key] < 0 for key in _IDENTITY_KEYS)):
            raise ValueError
        names.append(pdf["name"])
    if names != sorted(names) or len(names) != len(set(names)):
        raise ValueError
    return baseline


def _direct_regular(path: Path, parent: Path) -> bool:
    try:
        info = path.lstat()
        return stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and path.resolve(strict=True).parent == parent
    except (OSError, RuntimeError, ValueError):
        return False


def _capture_authority_identities(project: Path, pdf_names: tuple[str, ...]) -> dict[str, Any]:
    """Capture path-safe identities for every fixed authority and existing PDF."""
    if (type(pdf_names) is not tuple or any(not _basename(name) or not name.endswith(".pdf") for name in pdf_names)
            or list(pdf_names) != sorted(pdf_names) or len(set(pdf_names)) != len(pdf_names)):
        raise ValueError

    def capture(name: str, path: Path, parent: Path) -> dict[str, Any]:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or path.resolve(strict=True).parent != parent):
            raise ValueError
        return {"name": name, "device": info.st_dev, "inode": info.st_ino, "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}

    fixed = [capture(name, project / name, project / parent if parent else project)
        for name, parent in _FIXED_AUTHORITIES]
    pdfs = [capture(name, project / "pdfs" / name, project / "pdfs") for name in pdf_names]
    return {"fixed": fixed, "pdfs": pdfs}


def _baseline_identities(baseline: dict[str, Any]) -> dict[str, Any]:
    return {"fixed": [{key: item[key] for key in ("name", "device", "inode", "size", "mtime_ns", "ctime_ns")}
            for item in baseline["fixed"]],
        "pdfs": [{key: item[key] for key in ("name", "device", "inode", "size", "mtime_ns", "ctime_ns")}
            for item in baseline["pdfs"]]}


def _validate_current_before(project: Path, marker: dict[str, Any]) -> RetrySnapshot:
    """Re-read every authoritative retry fact and bind it to the abort marker."""
    authorities = (
        (project / "pdfs" / "download_report.json", project / "pdfs"),
        (project / "filtered" / "included_papers.jsonl", project / "filtered"),
        (project / "workflow_state.json", project),
    )
    if any(not _direct_regular(path, parent) for path, parent in authorities):
        raise ValueError
    pdf_names = tuple(pdf["name"] for pdf in marker["pdf_baseline"]["pdfs"])
    expected_identities = _baseline_identities(marker["pdf_baseline"])
    identity_before = _capture_authority_identities(project, pdf_names)
    before_fingerprint = _authoritative_fingerprint(project)
    current = current_retry_snapshot(project)
    report, included, retrieval = current.mutable_fact_copies()
    ledger = load_workflow_state(project)
    after_fingerprint = _authoritative_fingerprint(project)
    identity_after = _capture_authority_identities(project, pdf_names)
    baseline = tuple((pdf["name"], pdf["sha256"]) for pdf in marker["pdf_baseline"]["pdfs"])
    selected_ids = set(marker["selected_ids"])
    canonical_selected = tuple(item.retry_id for item in current.items if item.retry_id in selected_ids)
    if (identity_before != expected_identities or identity_after != expected_identities
            or after_fingerprint[:3] != before_fingerprint[:3]
            or before_fingerprint[3] != baseline or after_fingerprint[3] != baseline
            or current.report_revision != marker["expected_revision"]
            or canonical_selected != tuple(marker["selected_ids"])
            or _encode_before({"report": report, "included": included, "ledger": ledger})
                != _encode_before(marker["before"])
            or _encode_before(retrieval)
                != _encode_before(marker["before"]["ledger"]["stages"]["retrieval"])):
        raise ValueError
    return current


def _fingerprint_matches_before(
    fingerprint: tuple[bytes, bytes, bytes, tuple[tuple[str, str], ...]],
    report: dict[str, Any],
    included: list[dict[str, Any]],
    ledger: dict[str, Any],
) -> bool:
    """Bind raw fixed-authority bytes to the facts selected for the marker."""
    try:
        raw_report = json.loads(fingerprint[0].decode("utf-8"))
        raw_included = [json.loads(line) for line in fingerprint[1].decode("utf-8").splitlines()]
        raw_ledger = json.loads(fingerprint[2].decode("utf-8"))
        return _same_loaded_fact(
            {"report": raw_report, "included": raw_included, "ledger": raw_ledger},
            {"report": report, "included": included, "ledger": ledger})
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def _same_loaded_fact(first: Any, second: Any) -> bool:
    if type(first) is not type(second):
        return False
    if type(first) is dict:
        return set(first) == set(second) and all(_same_loaded_fact(first[key], second[key]) for key in first)
    if type(first) is list:
        return len(first) == len(second) and all(_same_loaded_fact(a, b) for a, b in zip(first, second))
    if type(first) is float and math.isnan(first) and math.isnan(second):
        return True
    return first == second


def _serialized_marker_bytes(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def _create_marker_no_clobber(marker: Path, raw: bytes) -> None:
    """Publish a complete marker only when its name is still unoccupied."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{marker.name}.", suffix=".tmp", dir=marker.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, marker)
        temporary.unlink()
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory = os.open(marker.parent, directory_flags)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def _repair_interrupted_marker_publish(project: Path, marker: Path,
                                       marker_info: os.stat_result) -> os.stat_result:
    """Remove the one owned temp link left by a crash after no-clobber publish."""
    if not stat.S_ISREG(marker_info.st_mode) or marker_info.st_nlink != 2:
        return marker_info
    prefix = f".{PENDING_RETRY_FILE}."
    matches: list[Path] = []
    with os.scandir(project) as entries:
        for entry in entries:
            if not entry.name.startswith(prefix) or not entry.name.endswith(".tmp"):
                continue
            info = entry.stat(follow_symlinks=False)
            if (stat.S_ISREG(info.st_mode) and info.st_dev == marker_info.st_dev
                    and info.st_ino == marker_info.st_ino and info.st_nlink == 2):
                matches.append(project / entry.name)
    if len(matches) != 1 or marker.resolve(strict=True).parent != project:
        raise ValueError
    matches[0].unlink()
    directory = os.open(project, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    repaired = marker.lstat()
    if (not stat.S_ISREG(repaired.st_mode) or repaired.st_nlink != 1
            or repaired.st_dev != marker_info.st_dev or repaired.st_ino != marker_info.st_ino
            or repaired.st_size != marker_info.st_size or repaired.st_mtime_ns != marker_info.st_mtime_ns):
        raise ValueError
    return repaired


def _read_marker_generation(project: Path) -> tuple[bytes, tuple[int, int, int, int, int, int]]:
    marker = project / PENDING_RETRY_FILE
    before = marker.lstat()
    before = _repair_interrupted_marker_publish(project, marker, before)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError
    with marker.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if _file_identity(opened) != _file_identity(before):
            raise ValueError
        raw = stream.read()
        after_read = os.fstat(stream.fileno())
    after_path = marker.lstat()
    identity = _file_identity(before)
    if (identity != _file_identity(after_read) or identity != _file_identity(after_path)
            or len(raw) != before.st_size or marker.resolve(strict=True).parent != project
            or identity != _file_identity(marker.lstat())):
        raise ValueError
    return raw, identity


def _assert_marker_generation(project: Path, expected: dict[str, Any]) -> dict[str, Any]:
    if (_RAW_MARKER_BYTES not in expected or _MARKER_IDENTITY not in expected
            or not _digest(expected.get("transaction_id"))):
        raise ValueError
    current = _read_marker(project)
    if (current["transaction_id"] != expected["transaction_id"]
            or current[_RAW_MARKER_BYTES] != expected[_RAW_MARKER_BYTES]
            or current[_MARKER_IDENTITY] != expected[_MARKER_IDENTITY]):
        raise ValueError
    return current


def _remove_owned_begin_marker(marker: Path, project: Path, expected: dict[str, Any]) -> None:
    """Remove only the exact marker generation written by a failed begin."""
    try:
        with _project_file_lock(project):
            _assert_marker_generation(project, expected)
            marker.unlink()
    except (OSError, RuntimeError, TypeError, ValueError):
        return


def begin_retry_transaction(
    project_path: Path | str,
    preparation: RetryPreparation,
    staging_name: str,
    staging_path: Path | str,
) -> RetryTransactionHandle:
    """Reserve a project and durably record abort facts before later mutation."""
    project = _project_path(project_path)
    with _lock(project), _project_file_lock(project):
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
            authorities = ((project / "pdfs" / "download_report.json", project / "pdfs"),
                (project / "filtered" / "included_papers.jsonl", project / "filtered"),
                (project / "workflow_state.json", project))
            if any(not _direct_regular(path, parent) for path, parent in authorities):
                raise ValueError
            authority_before = _authoritative_fingerprint(project)
            pdf_names = tuple(name for name, _ in authority_before[3])
            identity_before = _capture_authority_identities(project, pdf_names)
            trusted = _trusted_preparation(preparation, project)
            current = current_retry_snapshot(project)
            before_report, before_included, before_stage = current.mutable_fact_copies()
            before_ledger = load_workflow_state(project)
            trusted_report, trusted_included, trusted_stage, trusted_selected_rows = _trusted_json((
                trusted.snapshot.report, trusted.snapshot.included, trusted.snapshot.ledger, trusted.included_rows,
            ))
            current_items = {item.retry_id: item for item in current.items}
            if (current.report_revision != trusted.snapshot.report_revision
                    or _encode_before({"report": before_report, "included": before_included, "stage": before_stage})
                    != _encode_before({"report": trusted_report, "included": trusted_included, "stage": trusted_stage})
                    or before_ledger.get("stages", {}).get("retrieval") != before_stage
                    or current.items != trusted.snapshot.items
                    or tuple(item.retry_id for item in trusted.items) != trusted.selected_ids
                    or trusted.items != tuple(current_items[retry_id] for retry_id in trusted.selected_ids)
                    or _encode_before({"rows": [before_included[item.included_index] for item in trusted.items]})
                    != _encode_before({"rows": trusted_selected_rows})):
                raise ValueError
            candidates = tuple(f"retry-{current.report_revision}-{retry_id}.pdf" for retry_id in trusted.selected_ids)
            if not candidates or len(set(candidates)) != len(candidates) or any(not _basename(name, "retry-") for name in candidates):
                raise ValueError
            if any(_lexists(project / "pdfs" / name) for name in candidates):
                raise ValueError
            authority_after = _authoritative_fingerprint(project)
            identity_after = _capture_authority_identities(project, pdf_names)
            if (authority_after != authority_before or identity_after != identity_before
                    or not _fingerprint_matches_before(
                        authority_after, before_report, before_included, before_ledger)):
                raise ValueError
            pdf_fingerprint = authority_after[3]
            digests = dict(pdf_fingerprint)
            pdf_baseline_json_b64 = _encode_before({"fixed": identity_after["fixed"], "pdfs": [
                {**item, "sha256": digests[item["name"]]} for item in identity_after["pdfs"]]})
            _decode_pdf_baseline(pdf_baseline_json_b64)
        except Exception as exc:
            raise ValueError("Retry transaction preparation is stale") from exc
        transaction_id = secrets.token_hex(32)
        with _GUARD:
            if project in _ACTIVE:
                raise ValueError("Retry transaction is already active")
            _ACTIVE[project] = transaction_id
        try:
            data = {
                "version": 2, "phase": "abort", "transaction_id": transaction_id,
                "expected_revision": current.report_revision,
                "selected_ids": list(trusted.selected_ids),
                "staging_name": staging_name,
                "candidate_names": list(candidates),
                "pdf_baseline_json_b64": pdf_baseline_json_b64,
                "before_json_b64": _encode_before({"report": deepcopy(before_report), "included": deepcopy(before_included), "ledger": deepcopy(before_ledger)}),
            }
            expected_marker_bytes = _serialized_marker_bytes(data)
            _create_marker_no_clobber(marker, expected_marker_bytes)
        except Exception as exc:
            with _GUARD:
                if _ACTIVE.get(project) == transaction_id:
                    _ACTIVE.pop(project)
            raise ValueError("Retry transaction marker could not be written") from exc
        written = None
        try:
            written = _read_marker(project)
            if (written["transaction_id"] != transaction_id
                    or written[_RAW_MARKER_BYTES] != expected_marker_bytes):
                raise ValueError
            _validate_current_before(project, written)
            _assert_marker_generation(project, written)
        except Exception as exc:
            if written is not None:
                _remove_owned_begin_marker(marker, project, written)
            with _GUARD:
                if _ACTIVE.get(project) == transaction_id:
                    _ACTIVE.pop(project)
            raise ValueError("Retry transaction marker could not be validated") from exc
        return RetryTransactionHandle(marker, staging_name, candidates, transaction_id)


def _read_marker(project: Path) -> dict[str, Any]:
    try:
        raw_marker_bytes, marker_identity = _read_marker_generation(project)
        data = json.loads(raw_marker_bytes.decode("utf-8"))
        base = {"version", "phase", "transaction_id", "expected_revision", "selected_ids", "staging_name", "candidate_names", "pdf_baseline_json_b64", "before_json_b64"}
        phase = data.get("phase") if type(data) is dict else None
        optional = {key for key in ("sources_json_b64", "target_json_b64", "published_json_b64") if key in data}
        expected = base | optional
        if (type(data) is not dict or set(data) != expected or type(data["version"]) is not int
                or data["version"] != 2 or type(phase) is not str or phase not in {"abort", "apply"}
                or not _digest(data["transaction_id"])):
            raise ValueError
        allowed_abort = (set(), {"sources_json_b64"}, {"sources_json_b64", "target_json_b64"},
            {"sources_json_b64", "target_json_b64", "published_json_b64"})
        if ((phase == "abort" and optional not in allowed_abort)
                or (phase == "apply" and optional != allowed_abort[-1])):
            raise ValueError
        revision, ids = data["expected_revision"], data["selected_ids"]
        if not _digest(revision) or not isinstance(ids, list) or not ids or any(not _digest(item) for item in ids) or len(set(ids)) != len(ids):
            raise ValueError
        expected_candidates = [f"retry-{revision}-{item}.pdf" for item in ids]
        if data["candidate_names"] != expected_candidates or not _basename(data["staging_name"], _STAGING_PREFIX):
            raise ValueError
        data["pdf_baseline"] = _decode_pdf_baseline(data["pdf_baseline_json_b64"])
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
        if "sources_json_b64" in data:
            data["sources"] = _decode_sources(data["sources_json_b64"], data)
        if "target_json_b64" in data:
            data["target"] = _decode_target(data["target_json_b64"], data, project)
        if "published_json_b64" in data:
            published = _decode_before(data["published_json_b64"])
            if ("target" not in data or set(published) != {"pdfs"} or type(published["pdfs"]) is not list
                    or len(published["pdfs"]) > len(data["target"]["pdfs"])):
                raise ValueError
            for receipt, target in zip(published["pdfs"], data["target"]["pdfs"]):
                if (type(receipt) is not dict or set(receipt) != {"destination_name", "temp_name", "device", "inode", "size", "sha256",
                        "quarantine_name", "quarantine_device", "quarantine_inode"}
                        or receipt["destination_name"] != target["destination_name"]
                        or receipt["temp_name"] != f".{target['destination_name']}.{data['transaction_id']}.tmp"
                        or any(type(receipt[key]) is not int or receipt[key] < 0 for key in ("device", "inode", "size"))
                        or receipt["quarantine_name"] != f".{target['destination_name']}.{data['transaction_id']}.quarantine"
                        or any(type(receipt[key]) is not int or receipt[key] < 0
                            for key in ("quarantine_device", "quarantine_inode"))
                        or receipt["size"] != target["size"] or receipt["sha256"] != target["sha256"]):
                    raise ValueError
            data["published"] = published
        if phase == "apply" and len(data["published"]["pdfs"]) != len(data["target"]["pdfs"]):
            raise ValueError
        data[_RAW_MARKER_BYTES] = raw_marker_bytes
        data[_MARKER_IDENTITY] = marker_identity
        return data
    except (OSError, RuntimeError, TypeError, KeyError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("Pending retry transaction cannot be recovered safely") from exc


def _replace_marker_cas(project: Path, marker: dict[str, Any], replacement: dict[str, Any]) -> None:
    """Replace only the marker generation read by the current transaction."""
    with _lock(project), _project_file_lock(project):
        transaction_id = marker.get("transaction_id")
        if (not _digest(transaction_id) or replacement.get("transaction_id") != transaction_id
                or _RAW_MARKER_BYTES not in marker or _MARKER_IDENTITY not in marker
                or _RAW_MARKER_BYTES in replacement or _MARKER_IDENTITY in replacement):
            raise ValueError
        with _GUARD:
            if _ACTIVE.get(project) != transaction_id:
                raise ValueError
        _assert_marker_generation(project, marker)
        marker_path = project / PENDING_RETRY_FILE
        raw = _serialized_marker_bytes(replacement)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{PENDING_RETRY_FILE}.", suffix=".tmp", dir=project)
        temporary = Path(temporary_name)
        replaced = False
        descriptor_owned = True
        stream = None
        try:
            try:
                stream = os.fdopen(descriptor, "wb"); descriptor_owned = False
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                before_replace_identity = _file_identity(os.fstat(stream.fileno()))
                _assert_marker_generation(project, marker)
                os.replace(temporary, marker_path); replaced = True
                _fsync_directory(project)
                temporary_identity = _file_identity(os.fstat(stream.fileno()))
                if (temporary_identity[:4] != before_replace_identity[:4]
                        or temporary_identity[5] != before_replace_identity[5]):
                    raise ValueError
                updated = _read_marker(project)
                if (updated["transaction_id"] != transaction_id
                        or updated[_RAW_MARKER_BYTES] != raw
                        or updated[_MARKER_IDENTITY] != temporary_identity):
                    raise ValueError
            finally:
                if stream is not None:
                    stream.close()
                elif descriptor_owned:
                    try: os.close(descriptor)
                    except OSError: pass
        finally:
            if not replaced:
                temporary.unlink(missing_ok=True)


def _decode_sources(encoded: Any, marker: dict[str, Any]) -> dict[str, Any]:
    sources = _decode_before(encoded)
    if set(sources) != {"pdfs"} or type(sources["pdfs"]) is not list:
        raise ValueError
    ids: list[str] = []
    names: set[str] = set()
    for pdf in sources["pdfs"]:
        if (type(pdf) is not dict or set(pdf) != {"retry_id", "source_name", "size", "sha256"}
                or not _digest(pdf["retry_id"]) or pdf["retry_id"] not in marker["selected_ids"]
                or not _basename(pdf["source_name"]) or not pdf["source_name"].endswith(".pdf")
                or type(pdf["size"]) is not int or pdf["size"] < 0 or not _digest(pdf["sha256"])):
            raise ValueError
        ids.append(pdf["retry_id"]); names.add(pdf["source_name"])
    if len(ids) != len(set(ids)) or len(names) != len(ids):
        raise ValueError
    if ids != [retry_id for retry_id in marker["selected_ids"] if retry_id in set(ids)]:
        raise ValueError
    return sources


def _validate_committed_source_set(project: Path, marker_or_sources: dict[str, Any]) -> None:
    """Bind the complete committed source set to two stable aggregate reads."""
    if type(marker_or_sources) is not dict or "sources" not in marker_or_sources:
        raise ValueError
    sources = marker_or_sources["sources"]
    if _decode_sources(_encode_before(sources), marker_or_sources) != sources:
        raise ValueError
    source_parent = project / marker_or_sources["staging_name"] / "retry" / "pdfs"
    first_round: tuple[tuple[str, int, str, tuple[int, int]], ...] | None = None
    for _ in range(2):
        identities: set[tuple[int, int]] = set()
        current_round: list[tuple[str, int, str, tuple[int, int]]] = []
        for committed in sources["pdfs"]:
            size, digest, identity = _publication_pdf_fingerprint(
                source_parent / committed["source_name"], source_parent)
            if (size != committed["size"] or digest != committed["sha256"] or identity in identities):
                raise ValueError
            identities.add(identity)
            current_round.append((committed["source_name"], size, digest, identity))
        aggregate = tuple(current_round)
        if first_round is None:
            first_round = aggregate
        elif aggregate != first_round:
            raise ValueError


def run_retry_transaction_staging(project_path: Path | str, preparation: RetryPreparation,
                                  run_download) -> StagedRetryOutcome:
    """Stage with a trusted internal downloader that may write only the supplied staging tree."""
    project = _project_path(project_path)
    with _lock(project), _project_file_lock(project):
        try:
            with _GUARD:
                active_id = _ACTIVE.get(project)
                if active_id is None:
                    raise ValueError
            marker = _read_marker(project)
            if marker["transaction_id"] != active_id:
                raise ValueError
            if "sources" in marker or "target" in marker:
                raise ValueError
            trusted = _trusted_preparation(preparation, project)
            if (trusted.snapshot.report_revision != marker["expected_revision"]
                    or trusted.selected_ids != tuple(marker["selected_ids"])):
                raise ValueError
            current = _validate_current_before(project, marker)
            current_report, current_included, _ = current.mutable_fact_copies()
            trusted_report, trusted_included, trusted_ledger, trusted_rows = _trusted_json((
                trusted.snapshot.report, trusted.snapshot.included, trusted.snapshot.ledger, trusted.included_rows))
            current_by_id = {item.retry_id: item for item in current.items}
            if (current.report_revision != marker["expected_revision"] or current.items != trusted.snapshot.items
                    or _encode_before({"report": current_report, "included": current_included})
                        != _encode_before({"report": marker["before"]["report"], "included": marker["before"]["included"]})
                    or _encode_before(load_workflow_state(project)) != _encode_before(marker["before"]["ledger"])
                    or _encode_before({"report": trusted_report, "included": trusted_included, "stage": trusted_ledger})
                        != _encode_before({"report": marker["before"]["report"], "included": marker["before"]["included"],
                            "stage": marker["before"]["ledger"]["stages"]["retrieval"]})
                    or trusted.items != tuple(current_by_id[retry_id] for retry_id in trusted.selected_ids)
                    or _encode_before({"rows": [current_included[item.included_index] for item in trusted.items]})
                        != _encode_before({"rows": trusted_rows})):
                raise ValueError
            outcome = run_retry_staging(project, trusted, project / marker["staging_name"], run_download)
            if (type(outcome) is not StagedRetryOutcome
                    or outcome.report_revision != marker["expected_revision"]
                    or outcome.selected_ids != tuple(marker["selected_ids"])
                    or outcome.staging_root != project / marker["staging_name"]
                    or outcome.staging_project_path != outcome.staging_root / "retry"
                    or type(outcome.successful_pdfs) is not tuple):
                raise ValueError
            source_parent = outcome.staging_project_path / "pdfs"
            ids: list[str] = []
            names: set[str] = set()
            identities: set[tuple[int, int]] = set()
            pdfs: list[dict[str, Any]] = []
            for item in outcome.successful_pdfs:
                if (type(item) is not StagedRetryPdf or not _digest(item.retry_id)
                        or type(item.source_path) is not type(Path()) or item.source_path.parent != source_parent
                        or not _basename(item.source_path.name) or item.source_path.suffix != ".pdf"
                        or type(item.source_size) is not int or item.source_size < 0
                        or not _digest(item.source_sha256) or not _direct_regular(item.source_path, source_parent)):
                    raise ValueError
                size, digest, identity = _publication_pdf_fingerprint(item.source_path, source_parent)
                if size != item.source_size or digest != item.source_sha256 or identity in identities:
                    raise ValueError
                ids.append(item.retry_id); names.add(item.source_path.name); identities.add(identity)
                pdfs.append({"retry_id": item.retry_id, "source_name": item.source_path.name,
                    "size": size, "sha256": digest})
            if (len(ids) != len(set(ids)) or len(names) != len(ids)
                    or ids != [retry_id for retry_id in marker["selected_ids"] if retry_id in set(ids)]):
                raise ValueError
            encoded = _encode_before({"pdfs": pdfs})
            sources = _decode_sources(encoded, marker)
            if _encode_before(sources) != encoded:
                raise ValueError
            raw_keys = ("version", "phase", "transaction_id", "expected_revision", "selected_ids", "staging_name",
                "candidate_names", "pdf_baseline_json_b64", "before_json_b64")
            raw = {key: marker[key] for key in raw_keys}; raw["sources_json_b64"] = encoded
            _validate_current_before(project, marker)
            _validate_committed_source_set(project, {**marker, "sources": sources})
            _replace_marker_cas(project, marker, raw)
            return outcome
        except Exception as exc:
            raise ValueError("Retry transaction staging failed") from exc


def _receipt_paths(project: Path, marker: dict[str, Any], receipt: dict[str, Any]) -> tuple[Path, ...]:
    source_parent = project / marker["staging_name"] / "retry" / "pdfs"
    quarantine = source_parent / receipt["quarantine_name"]
    candidates = (source_parent / receipt["temp_name"], project / "pdfs" / receipt["destination_name"],
        quarantine / "temp", quarantine / "destination")
    return tuple(path for path in candidates if _lexists(path))


def _validate_quarantine(project: Path, marker: dict[str, Any], receipt: dict[str, Any]) -> Path | None:
    parent = project / marker["staging_name"] / "retry" / "pdfs"
    path = parent / receipt["quarantine_name"]
    if not _lexists(path):
        originals = (parent / receipt["temp_name"], project / "pdfs" / receipt["destination_name"])
        if any(_lexists(original) for original in originals): raise ValueError
        return None
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or path.is_symlink()
            or (info.st_dev, info.st_ino) != (receipt["quarantine_device"], receipt["quarantine_inode"])
            or path.resolve(strict=True).parent != parent
            or {child.name for child in path.iterdir()} - {"temp", "destination"}):
        raise ValueError
    return path


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def _validate_receipt_path(path: Path, receipt: dict[str, Any], links: int) -> None:
    descriptor = None
    try:
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != links
                or (before.st_dev, before.st_ino) != (receipt["device"], receipt["inode"])
                or path.resolve(strict=True).parent != path.parent):
            raise ValueError
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        opened = os.fstat(descriptor)
        if _file_identity(opened) != _file_identity(before): raise ValueError
        digest = hashlib.sha256(); size = 0; header = bytearray()
        while chunk := os.read(descriptor, 64 * 1024):
            if len(header) < 1024: header.extend(chunk[:1024 - len(header)])
            digest.update(chunk); size += len(chunk)
        after_fd = os.fstat(descriptor); after_path = path.lstat()
        if (_file_identity(after_fd) != _file_identity(opened)
                or _file_identity(after_path) != _file_identity(opened)
                or (size, digest.hexdigest()) != (receipt["size"], receipt["sha256"])
                or not bytes(header).lstrip().startswith(b"%PDF-")):
            raise ValueError
    finally:
        if descriptor is not None: os.close(descriptor)


def _validate_receipt_group(project: Path, marker: dict[str, Any], receipt: dict[str, Any]) -> tuple[Path, ...]:
    paths = _receipt_paths(project, marker, receipt)
    if not paths:
        return paths
    if len(paths) not in (1, 2):
        raise ValueError
    for path in paths:
        _validate_receipt_path(path, receipt, len(paths))
    return paths


def _quarantine_owned_path(project: Path, marker: dict[str, Any], receipt: dict[str, Any], path: Path) -> None:
    source_parent = project / marker["staging_name"] / "retry" / "pdfs"
    quarantine_parent = _validate_quarantine(project, marker, receipt)
    if quarantine_parent is None: raise ValueError
    slot = "temp" if path.name in {receipt["temp_name"], "temp"} else "destination"
    original = (source_parent / receipt["temp_name"] if slot == "temp"
        else project / "pdfs" / receipt["destination_name"])
    quarantine = quarantine_parent / slot
    if path != quarantine:
        if _lexists(quarantine): raise ValueError
        _assert_marker_generation(project, marker)
        os.rename(path, quarantine)
        _fsync_directory(path.parent); _fsync_directory(quarantine_parent)
    remaining_links = len(_receipt_paths(project, marker, receipt))
    try:
        _validate_receipt_path(quarantine, receipt, remaining_links)
    except Exception:
        try:
            os.link(quarantine, original, follow_symlinks=False)
            _fsync_directory(original.parent)
            quarantine.unlink(); _fsync_directory(quarantine_parent)
        except OSError:
            pass
        raise
    _assert_marker_generation(project, marker)
    quarantine.unlink(); _fsync_directory(quarantine_parent)


def _restore(project: Path, data: dict[str, Any]) -> bool:
    before = data["before"]
    try:
        if data.get("phase") != "abort": raise ValueError
        with _project_file_lock(project):
            _assert_marker_generation(project, data)
            for path, parent in ((project / "pdfs" / "download_report.json", project / "pdfs"),
                    (project / "filtered" / "included_papers.jsonl", project / "filtered"),
                    (project / "workflow_state.json", project)):
                if not _direct_regular(path, parent):
                    raise ValueError
            staging = project / data["staging_name"]
            if _lexists(staging) and (staging.is_symlink() or not staging.is_dir() or staging.resolve(strict=True).parent != project):
                raise ValueError
            for receipt in data.get("published", {}).get("pdfs", []):
                _validate_quarantine(project, data, receipt)
                _validate_receipt_group(project, data, receipt)
            _assert_marker_generation(project, data)
            atomic_write_json(project / "pdfs" / "download_report.json", before["report"])
            _assert_marker_generation(project, data)
            atomic_write_jsonl(project / "filtered" / "included_papers.jsonl", before["included"])
            _assert_marker_generation(project, data)
            save_workflow_state(project, before["ledger"])
            for receipt in data.get("published", {}).get("pdfs", []):
                for path in _receipt_paths(project, data, receipt):
                    _assert_marker_generation(project, data)
                    _validate_receipt_group(project, data, receipt)
                    _quarantine_owned_path(project, data, receipt, path)
                quarantine = _validate_quarantine(project, data, receipt)
                if quarantine is not None:
                    if any(quarantine.iterdir()): raise ValueError
                    quarantine.rmdir(); _fsync_directory(quarantine.parent)
            _fsync_directory(project / "pdfs")
            _assert_marker_generation(project, data)
            if _lexists(staging):
                _assert_marker_generation(project, data)
                shutil.rmtree(staging)
                _fsync_directory(project)
            _assert_marker_generation(project, data)
            (project / PENDING_RETRY_FILE).unlink()
            _fsync_directory(project)
            return True
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ValueError("Pending retry transaction cannot be recovered safely") from exc


def reconcile_retry_transaction(project_path: Path | str) -> bool:
    project = _project_path(project_path)
    with _lock(project), _project_file_lock(project):
        marker = project / PENDING_RETRY_FILE
        if not _lexists(marker):
            return False
        with _GUARD:
            if project in _ACTIVE:
                return False
        marker_data = _read_marker(project)
        if marker_data["phase"] == "abort": return _restore(project, marker_data)
        if marker_data["phase"] == "apply": return _roll_forward_retry_transaction(project, marker_data)
        raise ValueError("Pending retry transaction cannot be recovered safely")


def abort_retry_transaction(project_path: Path | str, expected_transaction_id: str | None = None) -> bool:
    project = _project_path(project_path)
    released_id = expected_transaction_id
    with _lock(project), _project_file_lock(project):
        if not _lexists(project / PENDING_RETRY_FILE):
            return False
        marker = _read_marker(project)
        transaction_id = marker["transaction_id"]
        if expected_transaction_id is not None and transaction_id != expected_transaction_id:
            return False
        if marker["phase"] != "abort":
            raise ValueError("Retry transaction cannot be aborted")
        released_id = transaction_id
        try:
            return _restore(project, marker)
        finally:
            with _GUARD:
                if _ACTIVE.get(project) == released_id:
                    _ACTIVE.pop(project)


def abandon_retry_transaction(project_path: Path | str, expected_transaction_id: str | None = None) -> None:
    try:
        project = Path(project_path).resolve()
    except (OSError, RuntimeError, TypeError):
        return
    with _GUARD:
        if expected_transaction_id is None or _ACTIVE.get(project) == expected_transaction_id:
            _ACTIVE.pop(project, None)


def _contains_text(value: Any, needle: str) -> bool:
    if type(value) is str:
        return needle in value
    if type(value) is dict:
        return any(_contains_text(key, needle) or _contains_text(item, needle) for key, item in value.items())
    if type(value) is list:
        return any(_contains_text(item, needle) for item in value)
    return False


def _material_output(stage: dict[str, Any]) -> bool:
    return (stage["last_valid"] is not None
        or (stage["status"] == "ready" and stage["attempt"] > 0)
        or (stage["status"] == "failed"
            and str(stage.get("error") or "").startswith("Action produced no successful outputs")))


def _validate_target_ledger(marker: dict[str, Any], ledger: dict[str, Any],
                            status: str, counts: dict[str, Any]) -> None:
    """Require the exact non-time result of one retry start and completion."""
    screening = marker["before"]["ledger"]["stages"]["screening"]
    if screening["status"] not in {"completed", "partial"} or screening["stale"]:
        raise ValueError
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

    if _encode_before(ledger) != _encode_before(expected):
        raise ValueError


def _trusted_target(project: Path, marker: dict[str, Any], plan: RetryPublicationPlan,
                    target_ledger: dict[str, Any]) -> dict[str, Any]:
    if ("sources" not in marker or type(plan) is not RetryPublicationPlan
            or type(plan.merged_facts) is not RetryMergedFacts):
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
    if merged.status != status or _encode_before(counts) != _encode_before(outcome_counts):
        raise ValueError
    _validate_target_ledger(marker, ledger, status, outcome_counts)
    pdfs: list[dict[str, Any]] = []
    successful_names: list[str] = []
    source_identities: set[tuple[int, int]] = set()
    concrete_path_type = type(Path())
    committed_pdfs = marker["sources"]["pdfs"]
    if len(plan.pdfs) != len(merged.planned_pdfs) or len(plan.pdfs) != len(committed_pdfs):
        raise ValueError
    for pdf, planned, committed in zip(plan.pdfs, merged.planned_pdfs, committed_pdfs):
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
                or pdf.retry_id != committed["retry_id"]
                or pdf.source_path != source_parent / committed["source_name"]
                or pdf.source_size != committed["size"] or pdf.source_sha256 != committed["sha256"]
                or not _direct_regular(pdf.source_path, source_parent)):
            raise ValueError
        size, digest, identity = _publication_pdf_fingerprint(pdf.source_path, source_parent)
        if (size != pdf.source_size or digest != pdf.source_sha256
                or identity in source_identities):
            raise ValueError
        source_identities.add(identity)
        successful_names.append(name)
        pdfs.append({"destination_name": name, "retry_id": pdf.retry_id,
            "source_name": committed["source_name"],
            "size": pdf.source_size, "sha256": pdf.source_sha256})
    if successful_names != [name for name in marker["candidate_names"] if name in set(successful_names)]:
        raise ValueError
    destinations = {pdf["retry_id"]: project / "pdfs" / pdf["destination_name"] for pdf in pdfs}
    _validate_merged_retry_delta(marker["before"]["report"], marker["before"]["included"],
        tuple(marker["selected_ids"]), report, included, destinations)
    if (_contains_text(report, str(project / marker["staging_name"]))
            or _contains_text(included, str(project / marker["staging_name"]))):
        raise ValueError
    return {"report": report, "included": included, "ledger": ledger, "pdfs": pdfs}


def _decode_target(encoded: Any, marker: dict[str, Any], project: Path) -> dict[str, Any]:
    target = _decode_before(encoded)
    if "sources" not in marker or set(target) != {"report", "included", "ledger", "pdfs"} or type(target["report"]) is not dict \
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
    committed_pdfs = marker["sources"]["pdfs"]
    if len(target["pdfs"]) != len(committed_pdfs):
        raise ValueError
    for pdf, committed in zip(target["pdfs"], committed_pdfs):
        if (type(pdf) is not dict or set(pdf) != {"destination_name", "retry_id", "source_name", "size", "sha256"}
                or not _basename(pdf["destination_name"], "retry-") or pdf["destination_name"] not in marker["candidate_names"]
                or pdf["destination_name"] != f"retry-{marker['expected_revision']}-{pdf['retry_id']}.pdf"
                or not _digest(pdf["retry_id"]) or not _basename(pdf["source_name"])
                or type(pdf["size"]) is not int or pdf["size"] < 0 or not _digest(pdf["sha256"])):
            raise ValueError
        committed_projection = {
            key: pdf[key] for key in ("retry_id", "source_name", "size", "sha256")
        }
        if _encode_before(committed_projection) != _encode_before(committed):
            raise ValueError
        names.append(pdf["destination_name"])
    if len(names) != len(set(names)) or names != [name for name in marker["candidate_names"] if name in set(names)]:
        raise ValueError
    destinations = {pdf["retry_id"]: project / "pdfs" / pdf["destination_name"] for pdf in target["pdfs"]}
    _validate_merged_retry_delta(marker["before"]["report"], marker["before"]["included"],
        tuple(marker["selected_ids"]), report, target["included"], destinations)
    staging = str(project / marker["staging_name"])
    if _contains_text(report, staging) or _contains_text(target["included"], staging):
        raise ValueError
    return target


def record_retry_transaction_target(project_path: Path | str, publication_plan: RetryPublicationPlan,
                                    target_ledger: dict[str, Any]) -> None:
    project = _project_path(project_path)
    with _lock(project), _project_file_lock(project):
        with _GUARD:
            active_id = _ACTIVE.get(project)
            if active_id is None:
                raise ValueError("Retry transaction is not active")
        marker = _read_marker(project)
        if marker["transaction_id"] != active_id:
            raise ValueError("Retry transaction is not active")
        if marker["phase"] != "abort" or "target" in marker:
            raise ValueError("Retry transaction target cannot be recorded")
        try:
            _validate_current_before(project, marker)
            target = _trusted_target(project, marker, publication_plan, target_ledger)
            encoded = _encode_before(target)
            if _encode_before(_decode_target(encoded, marker, project)) != encoded:
                raise ValueError
            raw = {key: marker[key] for key in ("version", "phase", "transaction_id", "expected_revision", "selected_ids", "staging_name", "candidate_names", "pdf_baseline_json_b64", "before_json_b64")}
            if "sources_json_b64" in marker:
                raw["sources_json_b64"] = marker["sources_json_b64"]
            raw["target_json_b64"] = encoded
            _validate_current_before(project, marker)
            _validate_committed_source_set(project, marker)
            _replace_marker_cas(project, marker, raw)
        except Exception as exc:
            raise ValueError("Retry transaction target is invalid") from exc


def _publish_one_pdf(project: Path, marker: dict[str, Any], pdf: dict[str, Any]) -> dict[str, Any]:
    source_parent = project / marker["staging_name"] / "retry" / "pdfs"
    destination_parent = project / "pdfs"
    source = source_parent / pdf["source_name"]
    destination = destination_parent / pdf["destination_name"]
    receipts = marker.get("published", {}).get("pdfs", [])
    index = marker["target"]["pdfs"].index(pdf)
    if index < len(receipts):
        receipt = receipts[index]; _validate_quarantine(project, marker, receipt)
        temporary = source_parent / receipt["temp_name"]
        paths = [path for path in (temporary, destination) if _lexists(path)]
        if not paths:
            raise ValueError
        for path in paths:
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != len(paths)
                    or (info.st_dev, info.st_ino) != (receipt["device"], receipt["inode"])):
                raise ValueError
        probe = destination if destination in paths else temporary
        descriptor = os.open(probe, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            digest = hashlib.sha256(); size = 0
            while chunk := os.read(descriptor, 64 * 1024): digest.update(chunk); size += len(chunk)
        finally: os.close(descriptor)
        if (size, digest.hexdigest()) != (receipt["size"], receipt["sha256"]): raise ValueError
        _assert_marker_generation(project, marker)
        if destination not in paths:
            os.link(temporary, destination, follow_symlinks=False)
            directory = os.open(destination_parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try: os.fsync(directory)
            finally: os.close(directory)
            _assert_marker_generation(project, marker)
        if _lexists(temporary):
            _assert_marker_generation(project, marker)
            temporary.unlink()
            directory = os.open(source_parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try: os.fsync(directory)
            finally: os.close(directory)
        directory = os.open(destination_parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try: os.fsync(directory)
        finally: os.close(directory)
        _assert_marker_generation(project, marker)
        return marker
    if index != len(receipts) or _lexists(destination):
        raise ValueError

    temp_name = f".{pdf['destination_name']}.{marker['transaction_id']}.tmp"
    temporary = source_parent / temp_name
    before_info = source.lstat()
    size, digest, identity = _publication_pdf_fingerprint(source, source_parent)
    if (size, digest) != (pdf["size"], pdf["sha256"]):
        raise ValueError
    source_descriptor = destination_descriptor = None
    created_identity = None
    try:
        source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        source_descriptor = os.open(source, source_flags)
        opened = os.fstat(source_descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != identity
                or _file_identity(opened) != _file_identity(before_info)):
            raise ValueError
        destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        destination_descriptor = os.open(temporary, destination_flags, 0o600)
        created = os.fstat(destination_descriptor); created_identity = (created.st_dev, created.st_ino)
        copied_size = 0
        copied_digest = hashlib.sha256()
        while chunk := os.read(source_descriptor, 64 * 1024):
            copied_digest.update(chunk); copied_size += len(chunk)
            offset = 0
            while offset < len(chunk):
                written = os.write(destination_descriptor, chunk[offset:])
                if written <= 0:
                    raise OSError
                offset += written
        os.fsync(destination_descriptor)
        after_open = os.fstat(source_descriptor)
        after_path = source.lstat()
        if (_file_identity(after_open) != _file_identity(opened)
                or _file_identity(after_path) != _file_identity(opened)
                or (copied_size, copied_digest.hexdigest()) != (pdf["size"], pdf["sha256"])):
            raise ValueError
    except Exception:
        if destination_descriptor is not None:
            os.close(destination_descriptor); destination_descriptor = None
        if source_descriptor is not None:
            os.close(source_descriptor); source_descriptor = None
        try:
            _assert_marker_generation(project, marker)
            info = temporary.lstat()
            if created_identity is not None and (info.st_dev, info.st_ino) == created_identity:
                temporary.unlink()
                directory = os.open(source_parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try: os.fsync(directory)
                finally: os.close(directory)
        except (OSError, ValueError):
            pass
        raise
    finally:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        if source_descriptor is not None:
            os.close(source_descriptor)
    after_size, after_digest, after_identity = _publication_pdf_fingerprint(source, source_parent)
    destination_size, destination_digest, destination_identity = _publication_pdf_fingerprint(temporary, source_parent)
    if ((after_size, after_digest, after_identity) != (pdf["size"], pdf["sha256"], identity)
            or (destination_size, destination_digest) != (pdf["size"], pdf["sha256"])):
        raise ValueError
    directory = os.open(source_parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    receipt = {"destination_name": pdf["destination_name"], "temp_name": temp_name,
        "device": destination_identity[0], "inode": destination_identity[1],
        "size": pdf["size"], "sha256": pdf["sha256"]}
    quarantine_name = f".{pdf['destination_name']}.{marker['transaction_id']}.quarantine"
    quarantine = source_parent / quarantine_name
    os.mkdir(quarantine, 0o700)
    quarantine_info = quarantine.lstat()
    if (not stat.S_ISDIR(quarantine_info.st_mode) or stat.S_IMODE(quarantine_info.st_mode) != 0o700 or quarantine.is_symlink()
            or quarantine.resolve(strict=True).parent != source_parent):
        raise ValueError
    _fsync_directory(source_parent)
    receipt.update(quarantine_name=quarantine_name, quarantine_device=quarantine_info.st_dev,
        quarantine_inode=quarantine_info.st_ino)
    raw = json.loads(marker[_RAW_MARKER_BYTES].decode("utf-8"))
    raw["published_json_b64"] = _encode_before({"pdfs": receipts + [receipt]})
    _replace_marker_cas(project, marker, raw)
    marker = _read_marker(project)
    return _publish_one_pdf(project, marker, pdf)


def _validate_before_with_published_pdfs(project: Path, marker: dict[str, Any]) -> None:
    expected_pdfs = [(pdf["name"], pdf["sha256"]) for pdf in marker["pdf_baseline"]["pdfs"]]
    receipts = marker.get("published", {}).get("pdfs", [])
    receipt_count = len(receipts)
    for index, pdf in enumerate(marker["target"]["pdfs"]):
        destination = project / "pdfs" / pdf["destination_name"]
        if not _lexists(destination):
            continue
        if index >= receipt_count:
            raise ValueError
        receipt = receipts[index]
        info = destination.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink not in (1, 2)
                or (info.st_dev, info.st_ino) != (receipt["device"], receipt["inode"])):
            raise ValueError
        expected_pdfs.append((pdf["destination_name"], receipt["sha256"]))
    pdf_names = tuple(pdf["name"] for pdf in marker["pdf_baseline"]["pdfs"])
    expected_identities = _baseline_identities(marker["pdf_baseline"])
    identity_before = _capture_authority_identities(project, pdf_names)
    before_fingerprint = _authoritative_fingerprint(project)
    current = current_retry_snapshot(project)
    report, included, retrieval = current.mutable_fact_copies()
    ledger = load_workflow_state(project)
    after_fingerprint = _authoritative_fingerprint(project)
    identity_after = _capture_authority_identities(project, pdf_names)
    selected_ids = set(marker["selected_ids"])
    canonical_selected = tuple(item.retry_id for item in current.items if item.retry_id in selected_ids)
    expected = tuple(sorted(expected_pdfs))
    if (identity_before != expected_identities or identity_after != expected_identities
            or after_fingerprint[:3] != before_fingerprint[:3]
            or before_fingerprint[3] != expected or after_fingerprint[3] != expected
            or current.report_revision != marker["expected_revision"]
            or canonical_selected != tuple(marker["selected_ids"])
            or _encode_before({"report": report, "included": included, "ledger": ledger})
                != _encode_before(marker["before"])
            or _encode_before(retrieval)
                != _encode_before(marker["before"]["ledger"]["stages"]["retrieval"])):
        raise ValueError


def publish_retry_transaction_pdfs(project_path: Path | str) -> None:
    """Publish only target-declared PDFs while retaining abort-phase recovery authority."""
    project = _project_path(project_path)
    with _lock(project), _project_file_lock(project):
        try:
            with _GUARD:
                active_id = _ACTIVE.get(project)
                if active_id is None:
                    raise ValueError
            marker = _read_marker(project)
            if (marker["transaction_id"] != active_id or marker["phase"] != "abort"
                    or "sources" not in marker or "target" not in marker):
                raise ValueError
            _validate_before_with_published_pdfs(project, marker)
            _assert_marker_generation(project, marker)
            _validate_committed_source_set(project, marker)
            for pdf in marker["target"]["pdfs"]:
                _validate_before_with_published_pdfs(project, marker)
                _assert_marker_generation(project, marker)
                _validate_committed_source_set(project, marker)
                marker = _publish_one_pdf(project, marker, pdf)
                _assert_marker_generation(project, marker)
                _validate_committed_source_set(project, marker)
                _validate_before_with_published_pdfs(project, marker)
            _validate_before_with_published_pdfs(project, marker)
            _validate_committed_source_set(project, marker)
            _assert_marker_generation(project, marker)
        except Exception as exc:
            raise ValueError("Retry transaction PDFs could not be published") from exc


def _validate_retry_staging_hierarchy(project: Path, marker: dict[str, Any]) -> None:
    staging = project / marker["staging_name"]
    retry = staging / "retry"; pdfs = retry / "pdfs"; filtered = retry / "filtered"

    def direct_directory(path: Path, parent: Path) -> None:
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or path.is_symlink()
                or path.resolve(strict=True).parent != parent):
            raise ValueError

    direct_directory(staging, project); direct_directory(retry, staging)
    direct_directory(pdfs, retry); direct_directory(filtered, retry)
    if ({path.name for path in staging.iterdir()} != {"retry"}
            or {path.name for path in retry.iterdir()} != {"pdfs", "filtered"}
            or {path.name for path in filtered.iterdir()} != {"included_papers.jsonl"}):
        raise ValueError
    if not _direct_regular(filtered / "included_papers.jsonl", filtered): raise ValueError
    source_names = {source["source_name"] for source in marker["sources"]["pdfs"]}
    quarantine_names = {receipt["quarantine_name"] for receipt in marker["published"]["pdfs"]}
    if {path.name for path in pdfs.iterdir()} != source_names | quarantine_names | {"download_report.json"}:
        raise ValueError
    if not _direct_regular(pdfs / "download_report.json", pdfs): raise ValueError
    for name in source_names:
        if not _direct_regular(pdfs / name, pdfs): raise ValueError
    for receipt in marker["published"]["pdfs"]:
        quarantine = _validate_quarantine(project, marker, receipt)
        if quarantine is None or any(quarantine.iterdir()): raise ValueError


def _validate_retry_apply_readiness(project_path: Path | str) -> dict[str, Any]:
    """Return the stable active abort marker only when authority apply may begin."""
    project = _project_path(project_path)
    with _lock(project), _project_file_lock(project):
        try:
            marker_path = project / PENDING_RETRY_FILE
            marker_info = marker_path.lstat()
            if not stat.S_ISREG(marker_info.st_mode) or marker_info.st_nlink != 1:
                raise ValueError
            with _GUARD:
                active_id = _ACTIVE.get(project)
                if active_id is None: raise ValueError
            marker = _read_marker(project)
            if (marker["transaction_id"] != active_id or marker["phase"] != "abort"
                    or "sources" not in marker or "target" not in marker or "published" not in marker
                    or len(marker["published"]["pdfs"]) != len(marker["target"]["pdfs"])):
                raise ValueError

            def validate_complete_publication() -> None:
                source_parent = project / marker["staging_name"] / "retry" / "pdfs"
                _validate_retry_staging_hierarchy(project, marker)
                for receipt in marker["published"]["pdfs"]:
                    quarantine = _validate_quarantine(project, marker, receipt)
                    if quarantine is None or any(quarantine.iterdir()): raise ValueError
                    if _lexists(source_parent / receipt["temp_name"]): raise ValueError
                    destination = project / "pdfs" / receipt["destination_name"]
                    if not _lexists(destination): raise ValueError
                    _validate_receipt_path(destination, receipt, 1)

            for _ in range(2):
                _assert_marker_generation(project, marker)
                _validate_before_with_published_pdfs(project, marker)
                _validate_committed_source_set(project, marker)
                validate_complete_publication()
                _assert_marker_generation(project, marker)
            return marker
        except Exception as exc:
            raise ValueError("Retry transaction is not ready to apply") from exc


def _classify_retry_authorities(project_path: Path | str,
                                marker: dict[str, Any]) -> RetryAuthorityClassification:
    """Stably classify apply-phase authorities as exact target or exact before facts."""
    project = _project_path(project_path)
    with _lock(project), _project_file_lock(project):
        try:
            if (type(marker) is not dict
                    or _RAW_MARKER_BYTES not in marker or _MARKER_IDENTITY not in marker):
                raise ValueError
            current = _assert_marker_generation(project, marker)
            if (current.get("phase") != "apply" or "before" not in current or "target" not in current):
                raise ValueError
            authorities = (
                (project / "pdfs/download_report.json", project / "pdfs", "report", False),
                (project / "filtered/included_papers.jsonl", project / "filtered", "included", True),
                (project / "workflow_state.json", project, "ledger", False),
            )
            def strict_json(raw: str) -> Any:
                def object_pairs(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result: raise ValueError
                        result[key] = value
                    return result
                return json.loads(raw, object_pairs_hook=object_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))

            kinds: list[str] = []; identities: list[tuple[int, int, int, int, int, int]] = []
            for path, parent, key, jsonl in authorities:
                before = path.lstat()
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                        or path.resolve(strict=True).parent != parent):
                    raise ValueError
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
                try:
                    opened = os.fstat(descriptor)
                    if _file_identity(opened) != _file_identity(before): raise ValueError
                    chunks = bytearray()
                    while chunk := os.read(descriptor, 64 * 1024): chunks.extend(chunk)
                    after_fd = os.fstat(descriptor); after_path = path.lstat()
                    if (_file_identity(after_fd) != _file_identity(opened)
                            or _file_identity(after_path) != _file_identity(opened)
                            or path.resolve(strict=True).parent != parent):
                        raise ValueError
                finally:
                    os.close(descriptor)
                raw = bytes(chunks)
                if jsonl:
                    value = [strict_json(line) for line in raw.decode("utf-8").splitlines()]
                else:
                    value = strict_json(raw.decode("utf-8"))
                if _same_loaded_fact(value, current["target"][key]): kind = "target"
                elif _same_loaded_fact(value, current["before"][key]): kind = "before"
                else: raise ValueError
                kinds.append(kind); identities.append(_file_identity(opened))
            _assert_marker_generation(project, current)
            return RetryAuthorityClassification(tuple(kinds), tuple(identities))
        except Exception as exc:
            raise ValueError("Retry transaction authorities cannot be classified") from exc


def _write_retry_authority_target(project_path: Path | str, marker: dict[str, Any],
                                  expected: RetryAuthorityClassification,
                                  index: int) -> RetryAuthorityClassification:
    """Durably replace one exact before authority with its apply target fact."""
    project = _project_path(project_path)
    with _lock(project), _project_file_lock(project):
        temporary = None; replaced = False; descriptor_owned = False; stream = None
        try:
            if type(expected) is not RetryAuthorityClassification or type(index) is not int or index not in range(3):
                raise ValueError
            current = _assert_marker_generation(project, marker)
            if current.get("phase") != "apply" or expected.kinds[index] != "before": raise ValueError
            specs = ((project / "pdfs/download_report.json", current["target"]["report"], False),
                (project / "filtered/included_papers.jsonl", current["target"]["included"], True),
                (project / "workflow_state.json", current["target"]["ledger"], False))
            path, value, jsonl = specs[index]
            raw = ("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in value).encode("utf-8")
                if jsonl else json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            descriptor_owned = True; temporary = Path(temporary_name)
            stream = os.fdopen(descriptor, "wb"); descriptor_owned = False
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            before_replace_temp = _file_identity(os.fstat(stream.fileno()))
            _assert_marker_generation(project, current)
            observed = _classify_retry_authorities(project, current)
            destination_identity = _file_identity(path.lstat())
            if observed != expected or destination_identity != expected.identities[index]:
                raise ValueError
            os.replace(temporary, path); replaced = True
            _fsync_directory(path.parent)
            post_identity = _file_identity(os.fstat(stream.fileno()))
            if (post_identity[:4] != before_replace_temp[:4] or post_identity[5] != before_replace_temp[5]):
                raise ValueError
            after = _classify_retry_authorities(project, current)
            kinds = list(expected.kinds); kinds[index] = "target"
            identities = list(expected.identities); identities[index] = post_identity
            if after != RetryAuthorityClassification(tuple(kinds), tuple(identities)): raise ValueError
            return after
        except Exception as exc:
            raise ValueError("Retry transaction authority could not be written") from exc
        finally:
            if stream is not None:
                try: stream.close()
                except OSError: pass
            elif descriptor_owned:
                try: os.close(descriptor)
                except OSError: pass
            if temporary is not None and not replaced:
                try: temporary.unlink(missing_ok=True)
                except OSError: pass


def _roll_forward_retry_authorities(project_path: Path | str,
                                    marker: dict[str, Any]) -> RetryAuthorityClassification:
    """Idempotently roll an apply marker's authorities forward in fixed order."""
    project = _project_path(project_path)
    with _lock(project), _project_file_lock(project):
        try:
            if type(marker) is not dict or _RAW_MARKER_BYTES not in marker or _MARKER_IDENTITY not in marker:
                raise ValueError
            current = _assert_marker_generation(project, marker)
            if current.get("phase") != "apply": raise ValueError
            snapshot = _classify_retry_authorities(project, current)
            for index in range(3):
                _assert_marker_generation(project, current)
                if snapshot.kinds[index] == "before":
                    snapshot = _write_retry_authority_target(project, current, snapshot, index)
                elif snapshot.kinds[index] != "target":
                    raise ValueError
                _assert_marker_generation(project, current)
            first = _classify_retry_authorities(project, current)
            _assert_marker_generation(project, current)
            second = _classify_retry_authorities(project, current)
            _assert_marker_generation(project, current)
            if first != second or second.kinds != ("target", "target", "target"): raise ValueError
            return second
        except Exception as exc:
            raise ValueError("Retry transaction authorities could not be rolled forward") from exc


def _validate_apply_staging_subset(project_path: Path | str,
                                   marker: dict[str, Any]) -> RetryStagingSubsetSnapshot:
    """Read-only proof that remaining apply staging is a safe cleanup subset."""
    project = _project_path(project_path)
    with _lock(project), _project_file_lock(project):
        try:
            current = _assert_marker_generation(project, marker)
            if current.get("phase") != "apply": raise ValueError
            entries = []
            def directory(path: Path, parent: Path, relative: str) -> None:
                before = path.lstat()
                if (not stat.S_ISDIR(before.st_mode) or path.is_symlink()
                        or path.resolve(strict=True).parent != parent
                        or _file_identity(path.lstat()) != _file_identity(before)): raise ValueError
                entries.append((relative, _file_identity(before)))
            def regular(path: Path, parent: Path, relative: str) -> None:
                before = path.lstat()
                if not _direct_regular(path, parent) or _file_identity(path.lstat()) != _file_identity(before):
                    raise ValueError
                entries.append((relative, _file_identity(before)))

            staging = project / current["staging_name"]
            if not _lexists(staging):
                _assert_marker_generation(project, current)
                return RetryStagingSubsetSnapshot(())
            directory(staging, project, "."); retry = staging / "retry"
            if {path.name for path in staging.iterdir()} - {"retry"}: raise ValueError
            if _lexists(retry):
                directory(retry, staging, "retry")
                if {path.name for path in retry.iterdir()} - {"pdfs", "filtered"}: raise ValueError
                filtered = retry / "filtered"
                if _lexists(filtered):
                    directory(filtered, retry, "retry/filtered")
                    if {path.name for path in filtered.iterdir()} - {"included_papers.jsonl"}: raise ValueError
                    included = filtered / "included_papers.jsonl"
                    if _lexists(included): regular(included, filtered, "retry/filtered/included_papers.jsonl")
                pdfs = retry / "pdfs"
                if _lexists(pdfs):
                    directory(pdfs, retry, "retry/pdfs")
                    sources = {item["source_name"]: item for item in current["sources"]["pdfs"]}
                    receipts = {item["quarantine_name"]: item for item in current["published"]["pdfs"]}
                    allowed = set(sources) | set(receipts) | {"download_report.json"}
                    if {path.name for path in pdfs.iterdir()} - allowed: raise ValueError
                    report = pdfs / "download_report.json"
                    if _lexists(report): regular(report, pdfs, "retry/pdfs/download_report.json")
                    for name, source in sources.items():
                        path = pdfs / name
                        if not _lexists(path): continue
                        regular(path, pdfs, f"retry/pdfs/{name}")
                        size, digest, _ = _publication_pdf_fingerprint(path, pdfs)
                        if (size, digest) != (source["size"], source["sha256"]): raise ValueError
                    for name, receipt in receipts.items():
                        path = pdfs / name
                        if not _lexists(path): continue
                        quarantine = _validate_quarantine(project, current, receipt)
                        if quarantine is None or any(quarantine.iterdir()): raise ValueError
                        entries.append((f"retry/pdfs/{name}", _file_identity(quarantine.lstat())))
            _assert_marker_generation(project, current)
            return RetryStagingSubsetSnapshot(tuple(sorted(entries)))
        except Exception as exc:
            raise ValueError("Retry transaction staging subset is invalid") from exc


def _validate_apply_pdf_commit(project_path: Path | str,
                               marker: dict[str, Any]) -> RetryPdfCommitSnapshot:
    """Read-only proof that an apply marker's exact committed PDF set is stable."""
    project = _project_path(project_path)
    with _lock(project), _project_file_lock(project):
        try:
            if type(marker) is not dict or _RAW_MARKER_BYTES not in marker or _MARKER_IDENTITY not in marker:
                raise ValueError
            current = _assert_marker_generation(project, marker)
            if current.get("phase") != "apply": raise ValueError

            def direct_directory(path: Path, parent: Path) -> None:
                info = path.lstat()
                if (not stat.S_ISDIR(info.st_mode) or path.is_symlink()
                        or path.resolve(strict=True).parent != parent): raise ValueError

            def validate_round() -> RetryPdfCommitSnapshot:
                pdfs = project / "pdfs"; direct_directory(pdfs, project)
                baseline = current["pdf_baseline"]["pdfs"]; receipts = current["published"]["pdfs"]
                expected_names = {"download_report.json"} | {item["name"] for item in baseline} \
                    | {item["destination_name"] for item in receipts}
                if {path.name for path in pdfs.iterdir()} != expected_names \
                        or not _direct_regular(pdfs / "download_report.json", pdfs): raise ValueError
                baseline_identities = []
                for item in baseline:
                    path = pdfs / item["name"]; before = path.lstat()
                    expected = tuple(item[key] for key in ("device", "inode", "size", "mtime_ns", "ctime_ns")) + (1,)
                    if _file_identity(before) != expected: raise ValueError
                    size, digest, identity = _publication_pdf_fingerprint(path, pdfs)
                    after = path.lstat()
                    if (_file_identity(after) != expected or size != item["size"] or digest != item["sha256"]
                            or identity != (item["device"], item["inode"])): raise ValueError
                    baseline_identities.append(expected)
                receipt_identities = []
                for receipt in receipts:
                    destination = pdfs / receipt["destination_name"]
                    _validate_receipt_path(destination, receipt, 1)
                    receipt_identities.append(_file_identity(destination.lstat()))

                staging_snapshot = _validate_apply_staging_subset(project, current)
                return RetryPdfCommitSnapshot(tuple(baseline_identities), tuple(receipt_identities),
                    staging_snapshot.signature)

            snapshots = []
            for _ in range(2):
                _assert_marker_generation(project, current); snapshots.append(validate_round())
                _assert_marker_generation(project, current)
            if snapshots[0] != snapshots[1]: raise ValueError
            return snapshots[1]
        except Exception as exc:
            raise ValueError("Retry transaction PDF commit is invalid") from exc


def _cleanup_apply_staging(project_path: Path | str, marker: dict[str, Any]) -> bool:
    """Idempotently remove only validated staging for a fully committed apply marker."""
    project = _project_path(project_path)
    with _lock(project), _project_file_lock(project):
        try:
            current = _assert_marker_generation(project, marker)
            if current.get("phase") != "apply": raise ValueError

            def validate_committed() -> None:
                _assert_marker_generation(project, current)
                if _classify_retry_authorities(project, current).kinds != ("target", "target", "target"):
                    raise ValueError
                _validate_apply_pdf_commit(project, current)
                _validate_apply_staging_subset(project, current)
                _assert_marker_generation(project, current)

            validate_committed(); staging = project / current["staging_name"]
            if not _lexists(staging):
                validate_committed(); _fsync_directory(project)
                _assert_marker_generation(project, current); return True
            source_parent = staging / "retry" / "pdfs"
            for receipt in current["published"]["pdfs"]:
                quarantine = source_parent / receipt["quarantine_name"]
                if not _lexists(quarantine): continue
                validated = _validate_quarantine(project, current, receipt)
                if validated is None or any(validated.iterdir()): raise ValueError
                _assert_marker_generation(project, current)
                validated.rmdir(); _fsync_directory(source_parent)
                _assert_marker_generation(project, current)
            validate_committed()
            _assert_marker_generation(project, current)
            shutil.rmtree(staging); _fsync_directory(project)
            if _lexists(staging): raise ValueError
            validate_committed()
            return True
        except Exception as exc:
            raise ValueError("Retry transaction staging could not be cleaned") from exc


def _roll_forward_retry_transaction(project_path: Path | str, marker: dict[str, Any]) -> bool:
    """Finish one fresh apply generation, deleting its marker only after stable commit."""
    project = _project_path(project_path)
    with _lock(project), _project_file_lock(project):
        try:
            current = _assert_marker_generation(project, marker)
            if current.get("phase") != "apply": raise ValueError
            _validate_apply_pdf_commit(project, current)
            _roll_forward_retry_authorities(project, current)
            _validate_apply_pdf_commit(project, current)
            _cleanup_apply_staging(project, current)
            _assert_marker_generation(project, current)
            first = _classify_retry_authorities(project, current)
            _assert_marker_generation(project, current)
            second = _classify_retry_authorities(project, current)
            if (first != second or second.kinds != ("target", "target", "target")): raise ValueError
            _validate_apply_pdf_commit(project, current)
            if _lexists(project / current["staging_name"]): raise ValueError
            _fsync_directory(project)
            _assert_marker_generation(project, current)
            (project / PENDING_RETRY_FILE).unlink()
            _fsync_directory(project)
            return True
        except Exception as exc:
            raise ValueError("Retry transaction could not be rolled forward") from exc
