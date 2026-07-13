"""Read-only identity contracts for retrying failed PDF retrievals."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from .safe_text import safe_display_text
from .workflow_state import load_workflow_state


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
    report: dict[str, Any]
    included: list[dict[str, Any]]
    ledger: dict[str, Any]
    items: tuple[RetryItem, ...]

    def to_projection(self) -> dict[str, Any]:
        return {
            "canRetry": True,
            "reportRevision": self.report_revision,
            "items": [item.to_projection() for item in self.items],
        }


def current_retry_snapshot(project_path: Path | str) -> RetrySnapshot:
    project = Path(project_path)
    ledger = load_workflow_state(project)["stages"]["retrieval"]
    _validate_current_stage(ledger)
    report = _read_json_object(project / "pdfs" / "download_report.json")
    included = _read_jsonl_strict(project / "filtered" / "included_papers.jsonl")
    success = _count(report, "success")
    failed = _count(report, "failed")
    downloaded = _detail_rows(report, "downloaded")
    failed_papers = _detail_rows(report, "failed_papers")
    if len(downloaded) != success or len(failed_papers) != failed or failed == 0:
        raise ValueError("Retrieval report detail counts are inconsistent")
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
    return RetrySnapshot(
        report_revision=retrieval_report_revision(report_copy, ledger_copy),
        report=report_copy,
        included=included_copy,
        ledger=ledger_copy,
        items=tuple(items),
    )


def disabled_retry_projection() -> dict[str, Any]:
    return {"canRetry": False, "reportRevision": "", "items": []}


def _validate_current_stage(stage: dict[str, Any]) -> None:
    if stage.get("status") not in {"partial", "failed"} or stage.get("stale") is not False:
        raise ValueError("Retrieval report is not a current retryable outcome")
    if stage["status"] == "failed":
        error = stage.get("error")
        if not isinstance(error, str) or not error.startswith("Action produced no successful outputs"):
            raise ValueError("Retrieval failure is not a structured terminal report")


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
            return safe_display_text(value, fallback="Unavailable paper")
    return "Unavailable paper"


def _safe_failure_class(row: dict[str, Any]) -> str:
    for field in ("failure_class", "error", "reason"):
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            return safe_display_text(value, fallback="Retrieval failed")
    return "Retrieval failed"
