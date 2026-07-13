"""Read-only identity contracts for retrying failed PDF retrievals."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
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
    source_size: int | None = None
    source_sha256: str | None = None


@dataclass(frozen=True)
class StagedRetryOutcome:
    staging_root: Path
    staging_project_path: Path
    report_revision: str
    selected_ids: tuple[str, ...]
    updated_rows: tuple[Mapping[str, Any], ...]
    report: Mapping[str, Any]
    successful_pdfs: tuple[StagedRetryPdf, ...]

    def mutable_copies(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return _thaw_json(self.updated_rows), _thaw_json(self.report)


@dataclass(frozen=True)
class RetryPlannedPdf:
    retry_id: str
    source_path: Path
    destination_path: Path


@dataclass(frozen=True)
class RetryMergedFacts:
    report_revision: str
    report: Mapping[str, Any]
    included: tuple[Mapping[str, Any], ...]
    status: str
    counts: Mapping[str, int]
    planned_pdfs: tuple[RetryPlannedPdf, ...]

    def mutable_copies(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return _thaw_json(self.report), _thaw_json(self.included)


@dataclass(frozen=True)
class RetryPublicationPdf:
    retry_id: str
    source_path: Path
    destination_path: Path
    source_size: int
    source_sha256: str


@dataclass(frozen=True)
class RetryPublicationPlan:
    report_revision: str
    merged_facts: RetryMergedFacts
    pdfs: tuple[RetryPublicationPdf, ...]


def prepare_retry_publication(
    project_path: Path | str,
    preparation: RetryPreparation,
    staged_outcome: StagedRetryOutcome,
    merged_facts: RetryMergedFacts,
) -> RetryPublicationPlan:
    """Revalidate retry inputs and freeze a no-write publication plan."""
    try:
        concrete_path_type = type(Path())
        if type(project_path) not in (str, concrete_path_type):
            raise ValueError
        project = Path(project_path)
        resolved_project = project.resolve(strict=True)
        preparation, staged_outcome, merged_facts = _trusted_publication_inputs(
            preparation, staged_outcome, merged_facts, type(resolved_project))
        before = _authoritative_fingerprint(project)
        if preparation._project_identity != resolved_project:
            raise ValueError

        current = current_retry_snapshot(project)
        frozen = preparation.snapshot
        if (current.report_revision != frozen.report_revision
                or _thaw_json(current.report) != _thaw_json(frozen.report)
                or _thaw_json(current.included) != _thaw_json(frozen.included)
                or _thaw_json(current.ledger) != _thaw_json(frozen.ledger)
                or current.items != frozen.items):
            raise ValueError

        expected = merge_staged_retry_facts(preparation, staged_outcome)
        if merged_facts != expected:
            raise ValueError
        if (expected.report_revision != current.report_revision
                or staged_outcome.report_revision != current.report_revision):
            raise ValueError

        resolved_root = _directory_child(staged_outcome.staging_root, resolved_project)
        if (not staged_outcome.staging_root.name.startswith(".retrieval_retry_staging_")
                or staged_outcome.staging_root.parent != resolved_project
                or staged_outcome.staging_root.resolve(strict=True) != resolved_root):
            raise ValueError
        resolved_staged_project = _directory_child(staged_outcome.staging_project_path, resolved_root)
        if (staged_outcome.staging_project_path != staged_outcome.staging_root / _STAGING_PROJECT_ID
                or staged_outcome.staging_project_path.resolve(strict=True) != resolved_staged_project):
            raise ValueError
        staged_pdfs = staged_outcome.staging_project_path / "pdfs"
        resolved_staged_pdfs = _directory_child(staged_pdfs, resolved_staged_project)
        authoritative_pdfs = resolved_project / "pdfs"
        resolved_authoritative_pdfs = authoritative_pdfs.resolve(strict=True)

        outcome_by_id = {item.retry_id: item for item in staged_outcome.successful_pdfs}
        if len(outcome_by_id) != len(staged_outcome.successful_pdfs):
            raise ValueError
        publication_pdfs: list[RetryPublicationPdf] = []
        seen_sources: set[tuple[int, int]] = set()
        seen_destinations: set[Path] = set()
        for planned in expected.planned_pdfs:
            source_item = outcome_by_id.get(planned.retry_id)
            if source_item is None or source_item.source_path != planned.source_path:
                raise ValueError
            source = planned.source_path
            if source.parent != staged_pdfs or source.suffix != ".pdf":
                raise ValueError
            size, digest, identity = _publication_pdf_fingerprint(source, resolved_staged_pdfs)
            if source_item.source_size != size or source_item.source_sha256 != digest:
                raise ValueError
            if identity in seen_sources:
                raise ValueError
            seen_sources.add(identity)

            destination = planned.destination_path
            expected_name = f"retry-{current.report_revision}-{planned.retry_id}.pdf"
            if (destination != authoritative_pdfs / expected_name
                    or destination.parent != authoritative_pdfs
                    or destination.suffix != ".pdf"
                    or destination in seen_destinations
                    or destination.exists() or destination.is_symlink()):
                raise ValueError
            if destination.parent.resolve(strict=True) != resolved_authoritative_pdfs:
                raise ValueError
            seen_destinations.add(destination)
            publication_pdfs.append(RetryPublicationPdf(
                planned.retry_id, source, destination, size, digest))
        if set(outcome_by_id) != {item.retry_id for item in publication_pdfs}:
            raise ValueError
        if _authoritative_fingerprint(project) != before:
            raise ValueError
        return RetryPublicationPlan(current.report_revision, expected, tuple(publication_pdfs))
    except Exception:
        raise ValueError("Retry publication preparation failed") from None


def _trusted_publication_inputs(
    preparation: RetryPreparation,
    staged_outcome: StagedRetryOutcome,
    merged_facts: RetryMergedFacts,
    concrete_path_type: type[Path],
) -> tuple[RetryPreparation, StagedRetryOutcome, RetryMergedFacts]:
    """Reject polymorphic values and materialize facts before using them."""
    if (type(preparation) is not RetryPreparation
            or type(preparation.snapshot) is not RetrySnapshot
            or type(staged_outcome) is not StagedRetryOutcome
            or type(merged_facts) is not RetryMergedFacts):
        raise ValueError

    snapshot = preparation.snapshot
    _require_exact_sha256(snapshot.report_revision)
    _require_frozen_mapping(snapshot.report)
    _require_frozen_mapping_tuple(snapshot.included)
    _require_frozen_mapping(snapshot.ledger)
    _require_exact_tuple(snapshot.items)
    for item in snapshot.items:
        _validate_retry_item(item)

    _require_exact_tuple(preparation.selected_ids)
    for retry_id in preparation.selected_ids:
        _require_exact_sha256(retry_id)
    _require_exact_tuple(preparation.items)
    for item in preparation.items:
        _validate_retry_item(item)
    _require_frozen_mapping_tuple(preparation.included_rows)
    _require_concrete_path(preparation._project_identity, concrete_path_type)

    _require_concrete_path(staged_outcome.staging_root, concrete_path_type)
    _require_concrete_path(staged_outcome.staging_project_path, concrete_path_type)
    _require_exact_sha256(staged_outcome.report_revision)
    _require_exact_tuple(staged_outcome.selected_ids)
    for retry_id in staged_outcome.selected_ids:
        _require_exact_sha256(retry_id)
    _require_frozen_mapping_tuple(staged_outcome.updated_rows)
    _require_frozen_mapping(staged_outcome.report)
    _require_exact_tuple(staged_outcome.successful_pdfs)
    for item in staged_outcome.successful_pdfs:
        if type(item) is not StagedRetryPdf:
            raise ValueError
        _require_exact_sha256(item.retry_id)
        _require_concrete_path(item.source_path, concrete_path_type)
        if type(item.source_size) is not int or item.source_size < 0:
            raise ValueError
        _require_exact_sha256(item.source_sha256)

    _require_exact_sha256(merged_facts.report_revision)
    _require_frozen_mapping(merged_facts.report)
    _require_frozen_mapping_tuple(merged_facts.included)
    if type(merged_facts.status) is not str:
        raise ValueError
    _require_frozen_mapping(merged_facts.counts)
    _require_exact_tuple(merged_facts.planned_pdfs)
    for item in merged_facts.planned_pdfs:
        if type(item) is not RetryPlannedPdf:
            raise ValueError
        _require_exact_sha256(item.retry_id)
        _require_concrete_path(item.source_path, concrete_path_type)
        _require_concrete_path(item.destination_path, concrete_path_type)

    snapshot = RetrySnapshot(
        preparation.snapshot.report_revision,
        _freeze_json(_materialize_frozen_mapping(preparation.snapshot.report)),
        _freeze_json(_materialize_frozen_mapping_tuple(preparation.snapshot.included)),
        _freeze_json(_materialize_frozen_mapping(preparation.snapshot.ledger)),
        tuple(_clone_retry_item(item) for item in preparation.snapshot.items),
    )
    trusted_preparation = RetryPreparation(
        snapshot,
        tuple(value for value in preparation.selected_ids),
        tuple(_clone_retry_item(item) for item in preparation.items),
        _freeze_json(_materialize_frozen_mapping_tuple(preparation.included_rows)),
        preparation._project_identity,
    )
    trusted_outcome = StagedRetryOutcome(
        staged_outcome.staging_root,
        staged_outcome.staging_project_path,
        staged_outcome.report_revision,
        tuple(value for value in staged_outcome.selected_ids),
        _freeze_json(_materialize_frozen_mapping_tuple(staged_outcome.updated_rows)),
        _freeze_json(_materialize_frozen_mapping(staged_outcome.report)),
        tuple(StagedRetryPdf(item.retry_id, item.source_path, item.source_size, item.source_sha256)
            for item in staged_outcome.successful_pdfs),
    )
    counts = _materialize_frozen_mapping(merged_facts.counts)
    if any(type(value) is not int or value < 0 for value in counts.values()):
        raise ValueError
    trusted_merged = RetryMergedFacts(
        merged_facts.report_revision,
        _freeze_json(_materialize_frozen_mapping(merged_facts.report)),
        _freeze_json(_materialize_frozen_mapping_tuple(merged_facts.included)),
        merged_facts.status,
        _freeze_json(counts),
        tuple(RetryPlannedPdf(item.retry_id, item.source_path, item.destination_path)
            for item in merged_facts.planned_pdfs),
    )
    return trusted_preparation, trusted_outcome, trusted_merged


def _clone_retry_item(item: RetryItem) -> RetryItem:
    return RetryItem(item.retry_id, item.label, item.failure_class, item.report_index, item.included_index)


def _validate_retry_item(item: RetryItem) -> None:
    if type(item) is not RetryItem:
        raise ValueError
    _require_exact_sha256(item.retry_id)
    if (type(item.label) is not str or type(item.failure_class) is not str
            or type(item.report_index) is not int or item.report_index < 0
            or type(item.included_index) is not int or item.included_index < 0):
        raise ValueError


def _require_exact_sha256(value: Any) -> None:
    if (type(value) is not str or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)):
        raise ValueError


def _require_concrete_path(value: Any, concrete_path_type: type[Path]) -> None:
    if type(value) is not concrete_path_type or not value.is_absolute():
        raise ValueError


def _require_exact_tuple(value: Any) -> None:
    if type(value) is not tuple:
        raise ValueError


def _require_frozen_mapping_tuple(value: Any) -> None:
    _require_exact_tuple(value)


def _require_frozen_mapping(value: Any) -> None:
    if type(value) is not MappingProxyType:
        raise ValueError


def _materialize_frozen_mapping(value: Any) -> dict[str, Any]:
    if type(value) is not MappingProxyType:
        raise ValueError
    materialized = _materialize_frozen_json(value)
    if type(materialized) is not dict:
        raise ValueError
    return materialized


def _materialize_frozen_mapping_tuple(value: Any) -> list[dict[str, Any]]:
    if type(value) is not tuple:
        raise ValueError
    result = []
    for item in value:
        result.append(_materialize_frozen_mapping(item))
    return result


def _materialize_frozen_json(value: Any) -> Any:
    value_type = type(value)
    if value_type is MappingProxyType:
        result: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str or key in result:
                raise ValueError
            result[key] = _materialize_frozen_json(item)
        return result
    elif value_type is tuple:
        return [_materialize_frozen_json(item) for item in value]
    elif value is None or value_type in (str, int, bool):
        return value
    elif value_type is float and math.isfinite(value):
        return value
    else:
        raise ValueError


def _publication_pdf_fingerprint(path: Path, resolved_parent: Path) -> tuple[int, str, tuple[int, int]]:
    if path.is_symlink() or not path.is_file() or path.resolve(strict=True).parent != resolved_parent:
        raise ValueError
    stat = path.stat()
    if stat.st_nlink != 1:
        raise ValueError
    digest = hashlib.sha256()
    size = 0
    header = bytearray()
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            if len(header) < 1024:
                header.extend(chunk[:1024 - len(header)])
            size += len(chunk)
            digest.update(chunk)
    if size == 0 or not bytes(header).lstrip().startswith(b"%PDF-"):
        raise ValueError
    return size, digest.hexdigest(), (stat.st_dev, stat.st_ino)


def merge_staged_retry_facts(preparation: RetryPreparation, staged_outcome: StagedRetryOutcome) -> RetryMergedFacts:
    """Build publication-ready retry facts without consulting the filesystem."""
    if not isinstance(preparation, RetryPreparation) or not isinstance(staged_outcome, StagedRetryOutcome):
        raise ValueError("Invalid retry merge inputs")
    snapshot = preparation.snapshot
    if (not isinstance(snapshot, RetrySnapshot) or not isinstance(snapshot.report, Mapping)
            or not isinstance(snapshot.included, tuple) or not isinstance(snapshot.ledger, Mapping)
            or not isinstance(snapshot.items, tuple) or not isinstance(preparation.items, tuple)
            or not isinstance(preparation.included_rows, tuple) or not isinstance(preparation._project_identity, Path)
            or not preparation._project_identity.is_absolute()):
        raise ValueError("Invalid retry preparation")
    report, included, ledger = snapshot.mutable_fact_copies()
    if retrieval_report_revision(report, ledger) != snapshot.report_revision or not _is_sha256(snapshot.report_revision):
        raise ValueError("Retry snapshot revision is inconsistent")
    base_success = _count(report, "success")
    base_failed = _count(report, "failed")
    base_downloaded = _detail_rows(report, "downloaded")
    base_failures = _detail_rows(report, "failed_papers")
    if len(base_downloaded) != base_success or len(base_failures) != base_failed:
        raise ValueError("Retry snapshot report counts are inconsistent")
    expected_base_status, expected_base_counts = structured_action_outcome(
        "download-pdfs", {"success": base_success, "failed": base_failed})
    _validate_current_stage(ledger, expected_base_status, base_failed)
    if (_thaw_json(ledger.get("counts")) != expected_base_counts
            or type(ledger.get("attempt")) is not int or ledger["attempt"] < 0):
        raise ValueError("Retry snapshot ledger is inconsistent")

    included_by_id: dict[str, int] = {}
    for index, row in enumerate(included):
        retry_id = stable_retry_id(row)
        if retry_id in included_by_id:
            raise ValueError("Included paper identities must be globally unique")
        included_by_id[retry_id] = index
    failure_by_id: dict[str, dict[str, Any]] = {}
    for detail in base_failures:
        retry_id = stable_retry_id(detail)
        if retry_id in failure_by_id or retry_id not in included_by_id:
            raise ValueError("Retry snapshot failure identities are inconsistent")
        failure_by_id[retry_id] = detail
    expected_items = tuple(RetryItem(stable_retry_id(detail), _safe_label(detail), _safe_failure_class(detail),
        index, included_by_id[stable_retry_id(detail)]) for index, detail in enumerate(base_failures))
    if snapshot.items != expected_items:
        raise ValueError("Retry snapshot items are inconsistent")

    selected = preparation.selected_ids
    if (not isinstance(selected, tuple) or not selected or len(set(selected)) != len(selected)
            or any(not _is_sha256(value) for value in selected)):
        raise ValueError("Invalid prepared retry selection")
    if (not all(isinstance(item, RetryItem) for item in preparation.items)
            or tuple(item.retry_id for item in preparation.items) != selected
            or len(preparation.included_rows) != len(selected)):
        raise ValueError("Prepared retry selection is inconsistent")
    for retry_id, item, row in zip(selected, preparation.items, preparation.included_rows):
        if (not isinstance(item, RetryItem) or retry_id not in failure_by_id
                or item.included_index != included_by_id[retry_id]
                or item.report_index < 0 or item.report_index >= len(base_failures)
                or stable_retry_id(base_failures[item.report_index]) != retry_id
                or stable_retry_id(_thaw_json(row)) != retry_id
                or _thaw_json(row) != included[item.included_index]):
            raise ValueError("Prepared retry facts are inconsistent")
    for retry_id, detail in failure_by_id.items():
        if retry_id in selected:
            continue
        included_row = included[included_by_id[retry_id]]
        report_class, row_class = detail.get("failure_class"), included_row.get("pdf_failure_class")
        if (_has_meaningful_fact(report_class) and _has_meaningful_fact(row_class)
                and not _same_nonempty_text(report_class, row_class)):
            raise ValueError("Unselected retry failure classes conflict")
        for container, failure_class in ((detail, report_class), (included_row, row_class)):
            expected_status = "subscribed_unavailable" if _is_subscription_failure(
                str(failure_class or report_class or row_class or "")) else "unavailable"
            if "retrieval_status" in container and container["retrieval_status"] != expected_status:
                raise ValueError("Unselected retry failure status is inconsistent")
            for key in ("web_search_fallback_pending", "web_search_fallback_eligible"):
                if key in container and container[key] is not True:
                    raise ValueError("Unselected retry fallback fact is inconsistent")

    root = staged_outcome.staging_root
    staged_project = staged_outcome.staging_project_path
    project = preparation._project_identity
    if (not isinstance(root, Path) or not isinstance(staged_project, Path)
            or not isinstance(staged_outcome.selected_ids, tuple) or not isinstance(staged_outcome.updated_rows, tuple)
            or not isinstance(staged_outcome.report, Mapping) or not isinstance(staged_outcome.successful_pdfs, tuple)
            or not root.name.startswith(".retrieval_retry_staging_") or root.parent != project
            or staged_project != root / _STAGING_PROJECT_ID or staged_outcome.selected_ids != selected
            or staged_outcome.report_revision != snapshot.report_revision):
        raise ValueError("Staged retry belongs to a different preparation")
    rows, staged_report = staged_outcome.mutable_copies()
    row_ids = tuple(stable_retry_id(row) for row in rows)
    if row_ids != selected:
        raise ValueError("Staged retry rows are not in request order")
    for source, updated in zip(preparation.included_rows, rows):
        if _fresh_retry_row(_thaw_json(source)) != _fresh_retry_row(deepcopy(updated)):
            raise ValueError("Staged retry changed source paper fields")
    staged_success = _count(staged_report, "success")
    staged_failed = _count(staged_report, "failed")
    staged_downloaded = _detail_rows(staged_report, "downloaded")
    staged_failures = _detail_rows(staged_report, "failed_papers")
    if (staged_success + staged_failed != len(selected) or len(staged_downloaded) != staged_success
            or len(staged_failures) != staged_failed):
        raise ValueError("Staged retry report counts are inconsistent")

    successful = staged_outcome.successful_pdfs
    if not all(isinstance(item, StagedRetryPdf) for item in successful):
        raise ValueError("Staged retry successes are invalid")
    success_ids = tuple(item.retry_id for item in successful)
    if (len(success_ids) != staged_success or len(set(success_ids)) != len(success_ids)
            or any(retry_id not in selected for retry_id in success_ids)
            or tuple(retry_id for retry_id in selected if retry_id in set(success_ids)) != success_ids):
        raise ValueError("Staged retry successes are inconsistent")
    source_by_id: dict[str, Path] = {}
    for item in successful:
        if (not isinstance(item.source_path, Path)
                or item.source_path.parent != staged_project / "pdfs" or item.source_path.suffix != ".pdf"):
            raise ValueError("Staged retry PDF source is invalid")
        source_by_id[item.retry_id] = item.source_path
    detail_by_source: dict[Path, dict[str, Any]] = {}
    for detail in staged_downloaded:
        path = _logical_report_path(detail.get("path"), staged_project)
        if path in detail_by_source:
            raise ValueError("Staged retry downloaded paths are not unique")
        if "pdf_path" in detail and _logical_report_path(detail["pdf_path"], staged_project) != path:
            raise ValueError("Staged retry PDF aliases conflict")
        detail_by_source[path] = detail
    if set(detail_by_source) != set(source_by_id.values()):
        raise ValueError("Staged retry downloaded details do not match PDFs")
    failed_detail_by_id: dict[str, dict[str, Any]] = {}
    for detail in staged_failures:
        retry_id = stable_retry_id(detail)
        if retry_id in failed_detail_by_id:
            raise ValueError("Staged retry failure identities are not unique")
        failed_detail_by_id[retry_id] = detail
    expected_failed_ids = set(selected) - set(success_ids)
    if set(failed_detail_by_id) != expected_failed_ids:
        raise ValueError("Staged retry failures do not match selection")
    _validate_canonical_merge_stage(rows, staged_report, source_by_id, detail_by_source, failed_detail_by_id)
    for retry_id, row in zip(selected, rows):
        flag = row.get("pdf_downloaded")
        if type(flag) is not bool or flag is not (retry_id in source_by_id):
            raise ValueError("Staged retry row outcomes do not match PDFs")
        if retry_id in source_by_id:
            detail = detail_by_source[source_by_id[retry_id]]
            _validate_shared_identity_fields(row, detail)
            if any(isinstance(detail.get(field), str) and detail[field].strip() for field in ("id", "doi", "url")):
                if stable_retry_id(detail) != retry_id:
                    raise ValueError("Staged retry success identity is inconsistent")
        else:
            _validate_shared_identity_fields(row, failed_detail_by_id[retry_id])

    destinations: dict[str, Path] = {
        retry_id: project / "pdfs" / f"retry-{snapshot.report_revision}-{retry_id}.pdf"
        for retry_id in success_ids
    }
    if len(set(destinations.values())) != len(destinations):
        raise ValueError("Retry PDF destinations are not unique")
    planned = tuple(RetryPlannedPdf(retry_id, source_by_id[retry_id], destinations[retry_id]) for retry_id in success_ids)

    rows_by_id = dict(zip(selected, rows))
    for retry_id in selected:
        row = _clean_selected_row(rows_by_id[retry_id], destinations.get(retry_id))
        included[included_by_id[retry_id]] = row
    if len({stable_retry_id(row) for row in included}) != len(included):
        raise ValueError("Merged included identities are not unique")

    appended: list[dict[str, Any]] = []
    for retry_id in success_ids:
        detail = detail_by_source[source_by_id[retry_id]]
        appended.append(_clean_selected_success_detail(detail, destinations[retry_id]))
    merged_failures: list[dict[str, Any]] = []
    for detail in base_failures:
        retry_id = stable_retry_id(detail)
        if retry_id not in selected:
            merged_failures.append(deepcopy(detail))
        elif retry_id not in destinations:
            merged_failures.append(_clean_selected_failure_detail(failed_detail_by_id[retry_id]))
    downloaded = deepcopy(base_downloaded) + appended
    success_count, failed_count = len(downloaded), len(merged_failures)
    report.update(success=success_count, failed=failed_count, downloaded=downloaded,
        failed_papers=merged_failures, pdf_count=success_count, attempted=success_count + failed_count)
    report["subscribed_papers"] = deepcopy([row for row in merged_failures if _merged_failure_status(row) == "subscribed_unavailable"])
    report["unavailable_papers"] = deepcopy([row for row in merged_failures if _merged_failure_status(row) == "unavailable"])
    report["web_search_fallback_candidates"] = deepcopy(merged_failures)
    if len(report["downloaded"]) != report["success"] or len(report["failed_papers"]) != report["failed"]:
        raise ValueError("Merged retry report counts are inconsistent")
    status, counts = structured_action_outcome("retry-failed-downloads", {"success": success_count, "failed": failed_count})
    if _contains_text(report, str(root)) or _contains_text(included, str(root)):
        raise ValueError("Merged retry facts contain staging metadata")
    return RetryMergedFacts(snapshot.report_revision, _freeze_json(deepcopy(report)),
        tuple(_freeze_json(deepcopy(row)) for row in included), status, _freeze_json(counts), planned)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _logical_report_path(value: Any, staged_project: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("Staged retry PDF path is invalid")
    path = Path(value)
    return path if path.is_absolute() else staged_project / path


_ROW_SECONDARY_DIAGNOSTICS = {"pdf_failure_detail", "pdf_failure_classes", "pdf_error"}
_REPORT_SECONDARY_DIAGNOSTICS = {
    "failure_detail", "failure_classes", "error", "pdf_failure_class", "pdf_failure_detail",
    "pdf_failure_classes", "pdf_error",
}
_OUTCOME_DERIVED_DETAIL_FIELDS = {
    "method", "pdf_method",
    "path", "pdf_path", "pdf_downloaded", "retrieval_status", "failure_class", "pdf_failure_class",
    "failure_detail", "failure_classes", "error", "pdf_failure_detail", "pdf_failure_classes", "pdf_error",
    "web_search_fallback_pending", "web_search_fallback_eligible",
}


def _validate_canonical_merge_stage(
    rows: list[dict[str, Any]],
    report: dict[str, Any],
    source_by_id: dict[str, Path],
    detail_by_source: dict[Path, dict[str, Any]],
    failed_detail_by_id: dict[str, dict[str, Any]],
) -> None:
    for key, expected in (("pdf_count", len(source_by_id)), ("attempted", len(rows))):
        value = report.get(key)
        if type(value) is not int or value < 0 or value != expected:
            raise ValueError("Staged retry report aggregate counts are inconsistent")
    rows_by_id = {stable_retry_id(row): row for row in rows}
    for retry_id, source in source_by_id.items():
        _validate_detail_provenance(rows_by_id[retry_id], detail_by_source[source])
    for retry_id, detail in failed_detail_by_id.items():
        _validate_detail_provenance(rows_by_id[retry_id], detail)
    for row in rows:
        retry_id = stable_retry_id(row)
        if retry_id in source_by_id:
            detail = detail_by_source[source_by_id[retry_id]]
            if row.get("pdf_downloaded") is not True or row.get("retrieval_status") != "downloaded":
                raise ValueError("Staged retry success row is not canonical")
            row_method = row.get("pdf_method")
            if "pdf_method" in row and (not isinstance(row_method, str) or not row_method.strip()):
                raise ValueError("Staged retry success method is invalid")
            if _logical_report_path(row.get("pdf_path"), Path(".")) != source_by_id[retry_id]:
                raise ValueError("Staged retry success row path is inconsistent")
            forbidden = (*_ROW_SECONDARY_DIAGNOSTICS, "pdf_failure_class",
                "web_search_fallback_pending", "web_search_fallback_eligible")
            if any(key in row for key in forbidden):
                raise ValueError("Staged retry success row contains failure facts")
            if (detail.get("pdf_downloaded") is not True or detail.get("retrieval_status") != "downloaded"
                    or any(key in detail for key in (*_REPORT_SECONDARY_DIAGNOSTICS, "failure_class",
                        "pdf_failure_class", "web_search_fallback_pending", "web_search_fallback_eligible"))):
                raise ValueError("Staged retry success detail is not canonical")
            for alias in ("method", "pdf_method"):
                if alias in detail:
                    alias_value = detail[alias]
                    if (not isinstance(alias_value, str) or not alias_value.strip()
                            or not isinstance(row_method, str) or alias_value != row_method):
                        raise ValueError("Staged retry success method is inconsistent")
        else:
            detail = failed_detail_by_id[retry_id]
            row_class, detail_class = row.get("pdf_failure_class"), detail.get("failure_class")
            if not _same_nonempty_text(row_class, detail_class):
                raise ValueError("Staged retry failure classifications conflict")
            if "pdf_failure_class" in detail and not _same_nonempty_text(detail_class, detail["pdf_failure_class"]):
                raise ValueError("Staged retry failure class alias conflicts")
            expected_status = "subscribed_unavailable" if _is_subscription_failure(row_class.strip().casefold()) else "unavailable"
            for container in (row, detail):
                if (container.get("pdf_downloaded") is not False or container.get("retrieval_status") != expected_status
                        or container.get("web_search_fallback_pending") is not True
                        or container.get("web_search_fallback_eligible") is not True
                        or any(key in container for key in ("path", "pdf_path", "method", "pdf_method"))):
                    raise ValueError("Staged retry failure detail is not canonical")
    failures = list(failed_detail_by_id.values())
    expected = {
        "subscribed_papers": [row for row in failures if row["retrieval_status"] == "subscribed_unavailable"],
        "unavailable_papers": [row for row in failures if row["retrieval_status"] == "unavailable"],
        "web_search_fallback_candidates": failures,
    }
    for key, value in expected.items():
        actual = report.get(key)
        if not isinstance(actual, list) or actual != value:
            raise ValueError("Staged retry classification facts are inconsistent")


def _validate_detail_provenance(row: dict[str, Any], detail: dict[str, Any]) -> None:
    for key, value in detail.items():
        if key in _IDENTITY_FIELDS:
            if (key in row and row[key] != value) or (key not in row and value != ""):
                raise ValueError("Staged retry identity metadata has no paper provenance")
        elif key not in _OUTCOME_DERIVED_DETAIL_FIELDS and (key not in row or row[key] != value):
            raise ValueError("Staged retry report metadata has no paper provenance")


def _clean_selected_row(source: dict[str, Any], destination: Path | None) -> dict[str, Any]:
    row = deepcopy(source)
    for key in _ROW_SECONDARY_DIAGNOSTICS:
        row.pop(key, None)
    if destination is not None:
        for key in ("pdf_failure_class", "web_search_fallback_pending", "web_search_fallback_eligible"):
            row.pop(key, None)
        row.update(pdf_downloaded=True, pdf_path=str(destination), retrieval_status="downloaded")
    else:
        for key in ("path", "pdf_path", "pdf_method"):
            row.pop(key, None)
        row["pdf_downloaded"] = False
    return row


def _clean_selected_success_detail(source: dict[str, Any], destination: Path) -> dict[str, Any]:
    detail = deepcopy(source)
    for key in (*_REPORT_SECONDARY_DIAGNOSTICS, "failure_class", "pdf_failure_class",
            "web_search_fallback_pending", "web_search_fallback_eligible"):
        detail.pop(key, None)
    detail.update(pdf_downloaded=True, path=str(destination), pdf_path=str(destination), retrieval_status="downloaded")
    return detail


def _clean_selected_failure_detail(source: dict[str, Any]) -> dict[str, Any]:
    detail = deepcopy(source)
    for key in _REPORT_SECONDARY_DIAGNOSTICS:
        detail.pop(key, None)
    for key in ("path", "pdf_path", "pdf_method"):
        detail.pop(key, None)
    detail["pdf_downloaded"] = False
    return detail


def _merged_failure_status(row: dict[str, Any]) -> str:
    status = row.get("retrieval_status")
    if status in {"subscribed_unavailable", "unavailable"}:
        return status
    failure_class = row.get("failure_class")
    return "subscribed_unavailable" if isinstance(failure_class, str) and _is_subscription_failure(failure_class) else "unavailable"


def _contains_text(value: Any, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, Mapping):
        return any(_contains_text(key, needle) or _contains_text(item, needle) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_text(item, needle) for item in value)
    return False


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
            size, digest, _ = _publication_pdf_fingerprint(path, pdfs)
            successes.append(StagedRetryPdf(retry_id, path, size, digest))
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
        if "pdf_downloaded" in detail and (type(detail["pdf_downloaded"]) is not bool or detail["pdf_downloaded"] is not True):
            raise ValueError("Staged retry success flag is inconsistent")
        if "pdf_path" in detail and _staged_pdf_path(detail["pdf_path"], staging_project, pdfs) != path:
            raise ValueError("Staged retry success path alias is inconsistent")
        _canonicalize_success(row, detail, path)
    failed_by_id = {stable_retry_id(detail): detail for detail in failed_rows}
    for retry_id in actual_failed:
        row = rows_by_id[retry_id]
        detail = failed_by_id[retry_id]
        _validate_shared_identity_fields(row, detail)
        row_failure = row.get("pdf_failure_class")
        report_failure = detail.get("failure_class")
        if not _same_nonempty_text(row_failure, report_failure):
            raise ValueError("Staged retry failure classifications conflict")
        canonical_class = row_failure.strip().casefold()
        expected_status = "subscribed_unavailable" if _is_subscription_failure(canonical_class) else "unavailable"
        if row.get("retrieval_status") != expected_status:
            if "retrieval_status" in row:
                raise ValueError("Staged retry failure status is inconsistent")
        _validate_optional_fact(detail, "retrieval_status", expected_status)
        _validate_optional_fact(row, "web_search_fallback_pending", True)
        _validate_optional_fact(row, "web_search_fallback_eligible", True)
        _validate_optional_fact(detail, "web_search_fallback_pending", True)
        _validate_optional_fact(detail, "web_search_fallback_eligible", True)
        _canonicalize_failed_schema(row)
        _canonicalize_failed_schema(detail)
        row.update(pdf_failure_class=canonical_class, retrieval_status=expected_status,
            web_search_fallback_pending=True, web_search_fallback_eligible=True)
        detail.update(failure_class=canonical_class, retrieval_status=expected_status,
            web_search_fallback_pending=True, web_search_fallback_eligible=True)

    canonical_failed = list(failed_rows)
    subscribed = [detail for detail in canonical_failed if detail["retrieval_status"] == "subscribed_unavailable"]
    unavailable = [detail for detail in canonical_failed if detail["retrieval_status"] == "unavailable"]
    classifications = {
        "subscribed_papers": subscribed,
        "unavailable_papers": unavailable,
        "web_search_fallback_candidates": canonical_failed,
    }
    for key, expected in classifications.items():
        if key in report:
            actual = report[key]
            if not isinstance(actual, list) or not all(isinstance(item, dict) for item in actual):
                raise ValueError("Staged retry classification facts are inconsistent")
            normalized_actual = deepcopy(actual)
            for item in normalized_actual:
                _canonicalize_failed_schema(item)
            if normalized_actual != expected:
                raise ValueError("Staged retry classification facts are inconsistent")
        report[key] = deepcopy(expected)

    _validate_canonical_merge_stage(
        rows,
        report,
        {item.retry_id: item.source_path for item in successes},
        dict(zip(downloaded_paths, downloaded)),
        failed_by_id,
    )

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
        staging_root=staging_root,
        staging_project_path=staging_project,
        report_revision=preparation.snapshot.report_revision,
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
            pdfs.append((path.name, _sha256_file(path)))
        return fixed[0].read_bytes(), fixed[1].read_bytes(), fixed[2].read_bytes(), tuple(sorted(pdfs))
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("Authoritative retry facts are unavailable") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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


def _canonicalize_success(row: dict[str, Any], detail: dict[str, Any], path: Path) -> None:
    failure_fields = ("pdf_failure_class", "pdf_failure_detail", "pdf_failure_classes", "pdf_error")
    report_failure_fields = ("failure_class", "failure_detail", "failure_classes", "error", *failure_fields)
    for container, fields in ((row, failure_fields), (detail, report_failure_fields)):
        if "retrieval_status" in container and container["retrieval_status"] != "downloaded":
            raise ValueError("Successful staged retry status is inconsistent")
        if any(_has_meaningful_fact(container.get(key)) for key in fields if key in container):
            raise ValueError("Successful staged retry contains failure facts")
        if any(key in container for key in ("web_search_fallback_pending", "web_search_fallback_eligible")):
            raise ValueError("Successful staged retry contains fallback facts")
        for key in (*fields, "web_search_fallback_pending", "web_search_fallback_eligible"):
            container.pop(key, None)
        container["retrieval_status"] = "downloaded"
    canonical_path = str(path)
    row.update(pdf_downloaded=True, pdf_path=canonical_path)
    detail.update(pdf_downloaded=True, path=canonical_path, pdf_path=canonical_path)


def _has_meaningful_fact(value: Any) -> bool:
    return value not in (None, "", (), [], {})


def _validate_optional_fact(container: dict[str, Any], key: str, expected: Any) -> None:
    if key in container and container[key] != expected:
        raise ValueError("Staged retry optional fact is inconsistent")


def _canonicalize_failed_schema(container: dict[str, Any]) -> None:
    if "pdf_downloaded" in container and (type(container["pdf_downloaded"]) is not bool or container["pdf_downloaded"] is not False):
        raise ValueError("Staged retry failure flag is inconsistent")
    if any(_has_meaningful_fact(container.get(key)) for key in ("path", "pdf_path", "pdf_method") if key in container):
        raise ValueError("Staged retry failure contains success facts")
    for key in ("path", "pdf_path", "pdf_method"):
        container.pop(key, None)
    container["pdf_downloaded"] = False


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
