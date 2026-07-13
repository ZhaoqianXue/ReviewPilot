"""Read-only identity contracts for retrying failed PDF retrievals."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
from types import MappingProxyType
from typing import Any

from .atomic_files import atomic_write_jsonl
from .safe_text import safe_display_text
from .workflow_state import load_workflow_state, structured_action_outcome


_IDENTITY_FIELDS = ("id", "doi", "url", "title")


def stable_retry_id(row: dict[str, Any]) -> str:
    if not isinstance(row, dict):
        raise ValueError("Retry identity row must be an object")
    for field in _IDENTITY_FIELDS:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            canonical = f"{field}:{value.strip().casefold()}".encode("utf-8")
            return hashlib.sha256(canonical).hexdigest()
    raise ValueError("Retry identity row has no stable identity")


def retrieval_report_revision(report: dict[str, Any], retrieval_stage: dict[str, Any]) -> str:
    if not isinstance(report, dict) or not isinstance(retrieval_stage, dict):
        raise ValueError("Retry revision inputs must be objects")
    attempt = retrieval_stage.get("attempt")
    status = retrieval_stage.get("status")
    if type(attempt) is not int or attempt < 0 or not isinstance(status, str):
        raise ValueError("Retry revision stage identity is invalid")
    payload = {"report": report, "stage": {"attempt": attempt, "status": status}}
    try:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Retry revision facts are not canonical JSON") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RetryItem:
    retry_id: str
    label: str
    failure_class: str
    report_index: int
    included_index: int

    def to_projection(self) -> dict[str, str]:
        return {"retryId": self.retry_id, "label": self.label, "failureClass": self.failure_class}


@dataclass(frozen=True)
class RetrySnapshot:
    report_revision: str
    report: Mapping[str, Any]
    included: tuple[Mapping[str, Any], ...]
    ledger: Mapping[str, Any]
    items: tuple[RetryItem, ...]

    def to_projection(self) -> dict[str, Any]:
        return {
            "canRetry": True,
            "reportRevision": self.report_revision,
            "items": [item.to_projection() for item in self.items],
        }

    def mutable_fact_copies(self) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        """Return isolated ordinary containers for a single mutation transaction."""
        report = _thaw_json(self.report)
        included = _thaw_json(self.included)
        ledger = _thaw_json(self.ledger)
        return report, included, ledger


class RetryRequestError(ValueError):
    """Path-independent retry request failure suitable for transport mapping."""

    code = "invalid_retry_request"


class InvalidRetryRequest(RetryRequestError):
    pass


class RevisionConflict(RetryRequestError):
    code = "revision_conflict"

    def __init__(self, expected_report_revision: str):
        super().__init__(self.code)
        self.expected_report_revision = expected_report_revision


class ConfirmationRequired(RetryRequestError):
    code = "confirmation_required"

    def __init__(self, expected_report_revision: str, failed_ids: tuple[str, ...]):
        super().__init__(self.code)
        self.expected_report_revision = expected_report_revision
        self.failed_ids = failed_ids


@dataclass(frozen=True)
class RetryPreparation:
    snapshot: RetrySnapshot
    selected_ids: tuple[str, ...]
    items: tuple[RetryItem, ...]
    included_rows: tuple[Mapping[str, Any], ...]
    _project_identity: Path


def prepare_retry_request(project_path: Path | str, payload: Any) -> RetryPreparation:
    """Validate and freeze one confirmed retry selection without writing files."""
    try:
        snapshot = current_retry_snapshot(project_path)
    except ValueError as exc:
        raise InvalidRetryRequest("retry_unavailable") from exc
    if not isinstance(payload, dict):
        raise InvalidRetryRequest("payload_must_be_object")

    failed_ids = payload.get("failed_ids")
    if (
        not isinstance(failed_ids, list)
        or not failed_ids
        or any(not isinstance(retry_id, str) or not retry_id.strip() for retry_id in failed_ids)
        or len(set(failed_ids)) != len(failed_ids)
    ):
        raise InvalidRetryRequest("invalid_failed_ids")
    report_revision = payload.get("report_revision")
    if not isinstance(report_revision, str) or not report_revision:
        raise InvalidRetryRequest("invalid_report_revision")
    if report_revision != snapshot.report_revision:
        raise RevisionConflict(snapshot.report_revision)

    items_by_id = {item.retry_id: item for item in snapshot.items}
    if any(retry_id not in items_by_id for retry_id in failed_ids):
        raise InvalidRetryRequest("unknown_failed_id")
    selected_ids = tuple(failed_ids)
    if "retry_confirmation" not in payload:
        raise ConfirmationRequired(snapshot.report_revision, selected_ids)
    confirmation = payload["retry_confirmation"]
    if not isinstance(confirmation, dict):
        raise InvalidRetryRequest("invalid_retry_confirmation")
    expected_revision = confirmation.get("expected_report_revision")
    if not isinstance(expected_revision, str) or not expected_revision:
        raise InvalidRetryRequest("invalid_confirmation_revision")
    if expected_revision != snapshot.report_revision:
        raise RevisionConflict(snapshot.report_revision)
    if confirmation.get("failed_ids") != failed_ids:
        raise InvalidRetryRequest("confirmation_selection_mismatch")

    items = tuple(items_by_id[retry_id] for retry_id in selected_ids)
    included_rows = tuple(snapshot.included[item.included_index] for item in items)
    return RetryPreparation(
        snapshot=snapshot,
        selected_ids=selected_ids,
        items=items,
        included_rows=included_rows,
        _project_identity=Path(project_path).resolve(strict=True),
    )


@dataclass(frozen=True)
class StagedRetryPdf:
    retry_id: str
    source_path: Path


@dataclass(frozen=True)
class StagedRetryOutcome:
    staging_path: Path
    selected_ids: tuple[str, ...]
    updated_rows: tuple[Mapping[str, Any], ...]
    report: Mapping[str, Any]
    successful_pdfs: tuple[StagedRetryPdf, ...]

    def mutable_copies(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return _thaw_json(self.updated_rows), _thaw_json(self.report)


_STAGING_PROJECT_ID = "retry"
_DERIVED_RETRY_FIELDS = {
    "pdf_downloaded",
    "pdf_path",
    "pdf_method",
    "pdf_failure_class",
    "pdf_failure_detail",
    "pdf_failure_classes",
    "pdf_error",
    "retrieval_status",
    "web_search_fallback_pending",
    "web_search_fallback_eligible",
}


def run_retry_staging(
    project_path: Path | str,
    preparation: RetryPreparation,
    staging_path: Path | str,
    run_download,
) -> StagedRetryOutcome:
    """Run a confirmed retry subset in an isolated, caller-planned staging root."""
    project = Path(project_path)
    staging = Path(staging_path)
    created = False
    before = _authoritative_fingerprint(project)
    try:
        resolved_project = project.resolve(strict=True)
        if not isinstance(preparation, RetryPreparation) or preparation._project_identity != resolved_project:
            raise ValueError("Retry preparation does not belong to project")
        if (
            staging.name.startswith(".retrieval_retry_staging_") is False
            or staging.parent != project
            or staging.exists()
            or staging.is_symlink()
            or staging.parent.resolve(strict=True) != resolved_project
        ):
            raise ValueError("Invalid retry staging location")
        staging.mkdir()
        created = True
        staging_project = staging / _STAGING_PROJECT_ID
        filtered = staging_project / "filtered"
        pdfs = staging_project / "pdfs"
        filtered.mkdir(parents=True)
        pdfs.mkdir()
        selected_rows = [_fresh_retry_row(_thaw_json(row)) for row in preparation.included_rows]
        atomic_write_jsonl(filtered / "included_papers.jsonl", selected_rows)

        try:
            result = run_download(staging, _STAGING_PROJECT_ID)
        except Exception as exc:
            raise ValueError("Retry downloader failed") from exc
        outcome = _normalize_staged_retry(staging, staging_project, preparation, result)
        if _authoritative_fingerprint(project) != before:
            raise ValueError("Authoritative retry facts changed during staging")
        return outcome
    except OSError as exc:
        if created:
            _remove_staging(staging)
        raise ValueError("Retry staging setup failed") from exc
    except Exception:
        if created:
            _remove_staging(staging)
        raise


def _fresh_retry_row(row: dict[str, Any]) -> dict[str, Any]:
    for key in tuple(row):
        if key in _DERIVED_RETRY_FIELDS:
            row.pop(key)
    return row


def _normalize_staged_retry(
    staging_root: Path,
    staging_project: Path,
    preparation: RetryPreparation,
    result: Any,
) -> StagedRetryOutcome:
    try:
        if staging_root.is_symlink() or not staging_root.is_dir():
            raise ValueError
        resolved_root = staging_root.resolve(strict=True)
        if resolved_root.parent != preparation._project_identity:
            raise ValueError
        resolved_project = _directory_child(staging_project, resolved_root)
        filtered = _directory_child(staging_project / "filtered", resolved_project)
        pdfs = _directory_child(staging_project / "pdfs", resolved_project)
        included_path = staging_project / "filtered" / "included_papers.jsonl"
        report_path = staging_project / "pdfs" / "download_report.json"
        _regular_child(included_path, filtered)
        _regular_child(report_path, pdfs)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("Invalid staged retry artifacts") from exc

    rows = _read_jsonl_strict(included_path)
    report = _read_json_object(report_path)
    success = _strict_result_count(result, report, "success")
    failed = _strict_result_count(result, report, "failed")
    downloaded = _detail_rows(report, "downloaded")
    failed_rows = _detail_rows(report, "failed_papers")
    if len(rows) != len(preparation.selected_ids) or success + failed != len(rows):
        raise ValueError("Staged retry counts are inconsistent")
    if len(downloaded) != success or len(failed_rows) != failed:
        raise ValueError("Staged retry report details are inconsistent")

    row_ids = tuple(stable_retry_id(row) for row in rows)
    if row_ids != preparation.selected_ids or len(set(row_ids)) != len(row_ids):
        raise ValueError("Staged retry paper identities are inconsistent")
    for source, updated in zip(preparation.included_rows, rows):
        if _fresh_retry_row(_thaw_json(source)) != _fresh_retry_row(deepcopy(updated)):
            raise ValueError("Staged retry changed source paper fields")
    failed_ids = tuple(stable_retry_id(row) for row in failed_rows)
    if len(set(failed_ids)) != len(failed_ids):
        raise ValueError("Staged retry failure identities are inconsistent")

    downloaded_paths: list[Path] = []
    for detail in downloaded:
        downloaded_paths.append(_staged_pdf_path(detail.get("path"), staging_project, pdfs))
    if len(set(downloaded_paths)) != len(downloaded_paths):
        raise ValueError("Staged retry PDF report paths are not unique")

    successes: list[StagedRetryPdf] = []
    actual_failed: list[str] = []
    rows_by_id = dict(zip(row_ids, rows))
    for retry_id, row in zip(row_ids, rows):
        downloaded_flag = row.get("pdf_downloaded")
        if type(downloaded_flag) is not bool:
            raise ValueError("Staged retry outcome flag is invalid")
        if downloaded_flag:
            path = _staged_pdf_path(row.get("pdf_path"), staging_project, pdfs)
            if path not in downloaded_paths:
                raise ValueError("Staged retry PDF is absent from report")
            successes.append(StagedRetryPdf(retry_id, path))
        else:
            if row.get("pdf_path") not in (None, ""):
                raise ValueError("Failed staged retry has a PDF path")
            actual_failed.append(retry_id)
    if set(actual_failed) != set(failed_ids) or len(actual_failed) != len(failed_ids):
        raise ValueError("Staged retry failures do not match report")
    if {item.source_path for item in successes} != set(downloaded_paths):
        raise ValueError("Staged retry successes do not match report")
    success_by_path = {item.source_path: item.retry_id for item in successes}
    for detail, path in zip(downloaded, downloaded_paths):
        row = rows_by_id[success_by_path[path]]
        _validate_shared_identity_fields(row, detail)
        if any(isinstance(detail.get(field), str) and detail[field].strip() for field in ("id", "doi", "url")):
            if stable_retry_id(detail) != success_by_path[path]:
                raise ValueError("Staged retry success identity does not match report")
    failed_by_id = {stable_retry_id(detail): detail for detail in failed_rows}
    for retry_id in actual_failed:
        row = rows_by_id[retry_id]
        detail = failed_by_id[retry_id]
        _validate_shared_identity_fields(row, detail)
        row_failure = row.get("pdf_failure_class")
        report_failure = detail.get("failure_class")
        if not _same_nonempty_text(row_failure, report_failure):
            raise ValueError("Staged retry failure classifications conflict")
        expected_status = "subscribed_unavailable" if _is_subscription_failure(row_failure) else "unavailable"
        if row.get("retrieval_status") != expected_status:
            raise ValueError("Staged retry failure status is inconsistent")

    disk_pdfs: set[Path] = set()
    for path in (staging_project / "pdfs").iterdir():
        if path.name == "download_report.json":
            continue
        if path.is_symlink() or not path.is_file() or path.suffix != ".pdf" or path.resolve(strict=True).parent != pdfs:
            raise ValueError("Invalid staged retry PDF artifact")
        disk_pdfs.add(path.resolve(strict=True))
    if disk_pdfs != {item.source_path for item in successes}:
        raise ValueError("Staged retry PDF artifacts do not match outcomes")

    report_copy = deepcopy(report)
    rows_copy = deepcopy(rows)
    return StagedRetryOutcome(
        staging_path=staging_project,
        selected_ids=preparation.selected_ids,
        updated_rows=tuple(_freeze_json(row) for row in rows_copy),
        report=_freeze_json(report_copy),
        successful_pdfs=tuple(successes),
    )


def _strict_result_count(result: Any, report: dict[str, Any], key: str) -> int:
    if not isinstance(result, dict) or not isinstance(result.get("stats"), dict):
        raise ValueError("Staged retry result contract is invalid")
    values = (result.get(key), result["stats"].get(key), report.get(key))
    if any(type(value) is not int or value < 0 for value in values) or len(set(values)) != 1:
        raise ValueError("Staged retry result counts conflict")
    return values[0]


def _staged_pdf_path(value: Any, staging_project: Path, resolved_pdfs: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("Staged retry PDF path is invalid")
    path = Path(value)
    if not path.is_absolute():
        path = staging_project / path
    try:
        if path.parent != staging_project / "pdfs" or path.is_symlink() or not path.is_file() or path.suffix != ".pdf":
            raise ValueError
        resolved = path.resolve(strict=True)
        with path.open("rb") as handle:
            header = handle.read(1024)
        if resolved.parent != resolved_pdfs or not header.lstrip().startswith(b"%PDF-"):
            raise ValueError
        return resolved
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("Staged retry PDF is invalid") from exc


def _authoritative_fingerprint(project: Path) -> tuple[bytes, bytes, bytes, tuple[tuple[str, str], ...]]:
    try:
        _validate_authoritative_paths(project)
        fixed = (project / "pdfs" / "download_report.json", project / "filtered" / "included_papers.jsonl", project / "workflow_state.json")
        pdfs = []
        for path in (project / "pdfs").iterdir():
            if path.name == "download_report.json":
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError
            pdfs.append((path.name, hashlib.sha256(path.read_bytes()).hexdigest()))
        return fixed[0].read_bytes(), fixed[1].read_bytes(), fixed[2].read_bytes(), tuple(sorted(pdfs))
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("Authoritative retry facts are unavailable") from exc


def _remove_staging(staging: Path) -> None:
    try:
        if staging.is_symlink():
            staging.unlink()
        elif staging.is_dir():
            shutil.rmtree(staging)
        elif staging.exists():
            staging.unlink()
    except OSError as exc:
        raise ValueError("Retry staging cleanup failed") from exc


def _same_nonempty_text(first: Any, second: Any) -> bool:
    return (
        isinstance(first, str)
        and isinstance(second, str)
        and bool(first.strip())
        and first.strip().casefold() == second.strip().casefold()
    )


def _validate_shared_identity_fields(row: dict[str, Any], detail: dict[str, Any]) -> None:
    for field in _IDENTITY_FIELDS:
        left = row.get(field)
        right = detail.get(field)
        if _has_identity_value(left) and _has_identity_value(right):
            if not isinstance(left, str) or not isinstance(right, str) or left.strip().casefold() != right.strip().casefold():
                raise ValueError("Staged retry report identity conflicts with paper")


def _has_identity_value(value: Any) -> bool:
    return bool(value.strip()) if isinstance(value, str) else value is not None


def _is_subscription_failure(value: str) -> bool:
    normalized = value.casefold()
    return any(marker in normalized for marker in ("paywall", "subscrib", "subscription", "closed"))


def current_retry_snapshot(project_path: Path | str) -> RetrySnapshot:
    project = Path(project_path)
    _validate_authoritative_paths(project)
    ledger = load_workflow_state(project)["stages"]["retrieval"]
    report = _read_json_object(project / "pdfs" / "download_report.json")
    included = _read_jsonl_strict(project / "filtered" / "included_papers.jsonl")
    success = _count(report, "success")
    failed = _count(report, "failed")
    downloaded = _detail_rows(report, "downloaded")
    failed_papers = _detail_rows(report, "failed_papers")
    if len(downloaded) != success or len(failed_papers) != failed or failed == 0:
        raise ValueError("Retrieval report detail counts are inconsistent")
    expected_status, _ = structured_action_outcome("download-pdfs", {"success": success, "failed": failed})
    _validate_current_stage(ledger, expected_status, failed)
    counts = ledger.get("counts")
    if not isinstance(counts, dict) or _count(counts, "succeeded") != success or _count(counts, "failed") != failed:
        raise ValueError("Retrieval ledger counts do not match report")

    included_by_id: dict[str, int] = {}
    for index, row in enumerate(included):
        retry_id = stable_retry_id(row)
        if retry_id in included_by_id:
            raise ValueError("Included paper identities must be globally unique")
        included_by_id[retry_id] = index

    failed_ids: set[str] = set()
    items: list[RetryItem] = []
    for report_index, row in enumerate(failed_papers):
        retry_id = stable_retry_id(row)
        if retry_id in failed_ids:
            raise ValueError("Failed report identities must be unique")
        failed_ids.add(retry_id)
        if retry_id not in included_by_id:
            raise ValueError("Failed report entry has no unique included-paper match")
        items.append(RetryItem(
            retry_id=retry_id,
            label=_safe_label(row),
            failure_class=_safe_failure_class(row),
            report_index=report_index,
            included_index=included_by_id[retry_id],
        ))

    report_copy = deepcopy(report)
    included_copy = deepcopy(included)
    ledger_copy = deepcopy(ledger)
    report_revision = retrieval_report_revision(report_copy, ledger_copy)
    return RetrySnapshot(
        report_revision=report_revision,
        report=_freeze_json(report_copy),
        included=_freeze_json(included_copy),
        ledger=_freeze_json(ledger_copy),
        items=tuple(items),
    )


def disabled_retry_projection() -> dict[str, Any]:
    return {"canRetry": False, "reportRevision": "", "items": []}


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _validate_current_stage(stage: dict[str, Any], expected_status: str, failed: int) -> None:
    if stage.get("status") not in {"partial", "failed"} or stage.get("stale") is not False:
        raise ValueError("Retrieval report is not a current retryable outcome")
    if stage["status"] != expected_status:
        raise ValueError("Retrieval ledger classification does not match report")
    if stage["status"] == "partial" and stage.get("error") is not None:
        raise ValueError("Partial retrieval must not contain a terminal error")
    if stage["status"] == "failed":
        if stage.get("error") != f"Action produced no successful outputs ({failed} failed).":
            raise ValueError("Retrieval failure is not a structured terminal report")


def _validate_authoritative_paths(project: Path) -> None:
    try:
        if project.is_symlink() or not project.is_dir():
            raise ValueError
        resolved_project = project.resolve(strict=True)
        _regular_child(project / "workflow_state.json", resolved_project)
        pdfs = _directory_child(project / "pdfs", resolved_project)
        filtered = _directory_child(project / "filtered", resolved_project)
        _regular_child(project / "pdfs" / "download_report.json", pdfs)
        _regular_child(project / "filtered" / "included_papers.jsonl", filtered)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("Authoritative retry facts are unavailable") from exc


def _directory_child(path: Path, resolved_parent: Path) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise ValueError
    resolved = path.resolve(strict=True)
    if resolved.parent != resolved_parent:
        raise ValueError
    return resolved


def _regular_child(path: Path, resolved_parent: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError
    if path.resolve(strict=True).parent != resolved_parent:
        raise ValueError


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Retrieval report is unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError("Retrieval report must be an object")
    return value


def _read_jsonl_strict(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError("Included papers are unreadable") from exc
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("Included papers contain malformed JSON") from exc
        if not isinstance(row, dict):
            raise ValueError("Included paper rows must be objects")
        rows.append(row)
    return rows


def _count(source: dict[str, Any], key: str) -> int:
    value = source.get(key)
    if type(value) is not int or value < 0:
        raise ValueError(f"Invalid retrieval {key} count")
    return value


def _detail_rows(report: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = report.get(key)
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError(f"Retrieval {key} must be a list of objects")
    return value


def _safe_label(row: dict[str, Any]) -> str:
    for field in ("title", "id", "doi"):
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            safe = safe_display_text(value, fallback="")
            if safe:
                return safe
    return "Unavailable paper"


def _safe_failure_class(row: dict[str, Any]) -> str:
    for field in ("failure_class", "error", "reason"):
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            safe = safe_display_text(value, fallback="")
            if safe:
                return safe
    return "Retrieval failed"
