import base64
from copy import deepcopy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

from reviewpilot_core.atomic_files import atomic_write_json, atomic_write_jsonl
from reviewpilot_core.retrieval_retry import (
    RetryItem, RetryMergedFacts, RetryPlannedPdf, RetryPreparation, RetryPublicationPdf,
    RetryPublicationPlan, RetrySnapshot, current_retry_snapshot,
    merge_staged_retry_facts, prepare_retry_publication, prepare_retry_request,
    stable_retry_id, _authoritative_fingerprint, _validate_detail_provenance,
)
from reviewpilot_core.retrieval_retry_transaction import (
    PENDING_RETRY_FILE,
    abandon_retry_transaction,
    abort_retry_transaction,
    begin_retry_transaction,
    reconcile_retry_transaction,
    publish_retry_transaction_pdfs,
    record_retry_transaction_target,
    run_retry_transaction_staging,
    _project_file_lock,
    _publication_pdf_fingerprint,
)
from reviewpilot_core.workflow_state import complete_action, load_workflow_state, save_workflow_state, start_action, initialize_workflow_state


def retryable_project(project: Path) -> None:
    atomic_write_jsonl(project / "filtered" / "included_papers.jsonl", [
        {"id": "ok", "pdf_path": str(project / "pdfs" / "original.pdf")},
        {"id": "failed", "title": "Failed"},
    ])
    (project / "pdfs").mkdir(parents=True, exist_ok=True)
    (project / "pdfs" / "original.pdf").write_bytes(b"%PDF-old")
    atomic_write_json(project / "pdfs" / "download_report.json", {
        "success": 1, "failed": 1,
        "downloaded": [{"id": "ok", "path": str(project / "pdfs" / "original.pdf")}],
        "failed_papers": [{"id": "failed", "title": "Failed", "failure_class": "network"}],
    })
    initialize_workflow_state(project)
    start_action(project, "collect"); complete_action(project, "collect", {"total": 0, "platform_stats": {"x": 0}, "platform_errors": {}})
    start_action(project, "screen"); complete_action(project, "screen", {})
    start_action(project, "download-pdfs"); complete_action(project, "download-pdfs", {"success": 1, "failed": 1})


def preparation(project: Path):
    snapshot = current_retry_snapshot(project)
    ids = [item.retry_id for item in snapshot.items]
    return prepare_retry_request(project, {"failed_ids": ids, "report_revision": snapshot.report_revision,
        "retry_confirmation": {"expected_report_revision": snapshot.report_revision, "failed_ids": ids}})


def publication(project: Path, prepared: RetryPreparation, staging_name: str, *, succeeds: bool | int = True,
                pdf_payload: bytes = b"%PDF-1.7\nretry"):
    def download(root: Path, project_id: str):
        staged = root / project_id
        rows = [json.loads(line) for line in (staged / "filtered" / "included_papers.jsonl").read_text().splitlines()]
        downloaded = []; failed = []
        success_count = len(rows) if succeeds is True else 0 if succeeds is False else succeeds
        for index, row in enumerate(rows):
            if index < success_count:
                pdf = staged / "pdfs" / f"retry-{index}.pdf"
                pdf.write_bytes(pdf_payload + f"\nsource-{index}".encode())
                row.update(pdf_downloaded=True, pdf_path=str(pdf), retrieval_status="downloaded")
                downloaded.append({"id": row.get("id", ""), "title": row.get("title", ""), "path": str(pdf)})
            else:
                row.update(pdf_downloaded=False, retrieval_status="unavailable", pdf_failure_class="download_failed")
                failed.append({"id": row["id"], "title": row.get("title", ""),
                    "doi": "", "url": "", "failure_class": "download_failed"})
        atomic_write_jsonl(staged / "filtered" / "included_papers.jsonl", rows)
        atomic_write_json(staged / "pdfs" / "download_report.json", {
            "success": len(downloaded), "failed": len(failed), "downloaded": downloaded,
            "failed_papers": failed, "pdf_count": len(downloaded), "attempted": len(rows),
        })
        return {"success": len(downloaded), "failed": len(failed),
            "stats": {"success": len(downloaded), "failed": len(failed)}}
    outcome = run_retry_transaction_staging(project, prepared, download)
    merged = merge_staged_retry_facts(prepared, outcome)
    return prepare_retry_publication(project, prepared, outcome, merged)


def target_ledger(project: Path, plan) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        oracle = Path(directory)
        save_workflow_state(oracle, load_workflow_state(project))
        start_action(oracle, "retry-failed-downloads")
        counts = plan.merged_facts.counts
        return complete_action(oracle, "retry-failed-downloads", {
            "success": counts["succeeded"], "failed": counts["failed"],
        })


def marker_lock_contender(project: Path, marker_bytes: bytes):
    marker = project / PENDING_RETRY_FILE
    replacement = project / ".retry-contender-marker"
    started = project / ".retry-contender-started"
    acquired = project / ".retry-contender-acquired"
    replacement.write_bytes(marker_bytes)
    code = (
        "import fcntl, os, pathlib, sys; "
        "lock, marker, replacement, started, acquired = map(pathlib.Path, sys.argv[1:]); "
        "started.write_text('1'); "
        "fd = os.open(lock, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)); "
        "fcntl.flock(fd, fcntl.LOCK_EX); "
        "acquired.write_text('1'); "
        "os.replace(replacement, marker); "
        "fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)"
    )
    process = subprocess.Popen([sys.executable, "-c", code,
        str(project), str(marker), str(replacement),
        str(started), str(acquired)])
    deadline = time.monotonic() + 2
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not started.exists():
        process.kill(); process.wait()
        raise AssertionError("marker lock contender did not start")
    return process, acquired


class RetryAbortTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.project = Path(self.temp.name) / "project"
        self.project.mkdir(); retryable_project(self.project); self.preparation = preparation(self.project)
        self.staging_name = ".retrieval_retry_staging_case"

    def tearDown(self):
        abandon_retry_transaction(self.project); self.temp.cleanup()

    def test_begin_writes_path_safe_immutable_abort_marker_before_mutation(self):
        original = self.preparation.snapshot.mutable_fact_copies()
        handle = begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        marker = json.loads((self.project / PENDING_RETRY_FILE).read_text())
        self.assertEqual((marker["version"], marker["phase"]), (2, "abort"))
        self.assertRegex(marker["transaction_id"], r"^[0-9a-f]{64}$")
        self.assertEqual(handle.transaction_id, marker["transaction_id"])
        self.assertNotIn(str(self.project), json.dumps(marker))
        self.assertEqual(marker["candidate_names"], [f"retry-{self.preparation.snapshot.report_revision}-{self.preparation.selected_ids[0]}.pdf"])
        baseline = json.loads(base64.b64decode(marker["pdf_baseline_json_b64"]))
        self.assertEqual([item["name"] for item in baseline["fixed"]], [
            "pdfs/download_report.json", "filtered/included_papers.jsonl", "workflow_state.json"])
        self.assertEqual(baseline["pdfs"][0]["name"], "original.pdf")
        self.assertEqual(baseline["pdfs"][0]["sha256"], hashlib.sha256(b"%PDF-old").hexdigest())
        for item in baseline["fixed"] + baseline["pdfs"]:
            for key in ("device", "inode", "size", "mtime_ns", "ctime_ns"):
                self.assertIs(type(item[key]), int)
        self.preparation.snapshot.mutable_fact_copies()[0]["success"] = 999
        self.assertEqual(marker, json.loads(handle.marker_path.read_text()))
        self.assertEqual(original[2], self.preparation.snapshot.mutable_fact_copies()[2])

    def test_begin_never_overwrites_marker_created_after_preflight(self):
        resolved_project = self.project.resolve()
        marker = resolved_project / PENDING_RETRY_FILE
        foreign = b'{"foreign":"transaction"}'

        def race_after_preflight(_):
            marker.write_bytes(foreign)
            return "a" * 64

        with patch("reviewpilot_core.retrieval_retry_transaction.secrets.token_hex",
                   side_effect=race_after_preflight):
            with self.assertRaisesRegex(ValueError, r"^Retry transaction marker could not be written$"):
                begin_retry_transaction(self.project, self.preparation,
                    self.staging_name, self.project / self.staging_name)

        self.assertEqual(marker.read_bytes(), foreign)
        marker.unlink()

    @unittest.skipUnless(hasattr(os, "fork"), "requires fork")
    def test_forked_child_cannot_treat_parent_file_lock_as_reentrant(self):
        project = self.project.resolve()
        read_descriptor, write_descriptor = os.pipe()
        child = None
        try:
            with _project_file_lock(project):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    child = os.fork()
                if child == 0:
                    os.close(read_descriptor)
                    try:
                        with _project_file_lock(project):
                            os.write(write_descriptor, b"entered")
                    finally:
                        os.close(write_descriptor)
                    os._exit(0)
                os.close(write_descriptor)
                write_descriptor = -1
                time.sleep(0.2)
                os.set_blocking(read_descriptor, False)
                with self.assertRaises(BlockingIOError):
                    os.read(read_descriptor, 7)
                os.set_blocking(read_descriptor, True)
            self.assertEqual(os.read(read_descriptor, 7), b"entered")
            _, status = os.waitpid(child, 0)
            child = None
            self.assertEqual(status, 0)
        finally:
            if write_descriptor >= 0:
                os.close(write_descriptor)
            os.close(read_descriptor)
            if child is not None:
                os.kill(child, 9)
                os.waitpid(child, 0)

    def test_recovery_repairs_crash_between_marker_link_and_temp_unlink(self):
        marker = self.project.resolve() / PENDING_RETRY_FILE
        real_unlink = Path.unlink

        def fail_publish_temp(path, *args, **kwargs):
            target = Path(path)
            if (target.parent == marker.parent
                    and target.name.startswith(f".{PENDING_RETRY_FILE}.")
                    and target.name.endswith(".tmp")):
                raise OSError("publish temp unlink crash seam")
            return real_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", autospec=True, side_effect=fail_publish_temp):
            with self.assertRaisesRegex(ValueError, r"^Retry transaction marker could not be written$"):
                begin_retry_transaction(self.project, self.preparation,
                    self.staging_name, self.project / self.staging_name)

        temporary_links = list(marker.parent.glob(f".{PENDING_RETRY_FILE}.*.tmp"))
        self.assertTrue(marker.exists())
        self.assertEqual(marker.stat().st_nlink, 2)
        self.assertEqual(len(temporary_links), 1)
        self.assertTrue(reconcile_retry_transaction(self.project))
        self.assertFalse(marker.exists())
        self.assertFalse(temporary_links[0].exists())

    def test_marker_requires_canonical_path_safe_sorted_pdf_baseline(self):
        (self.project / "pdfs" / "z.pdf").write_bytes(b"%PDF-z")
        self.preparation = preparation(self.project)
        handle = begin_retry_transaction(
            self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        valid = handle.marker_path.read_bytes()
        marker = json.loads(valid)
        baseline = json.loads(base64.b64decode(marker["pdf_baseline_json_b64"]))
        self.assertEqual([pdf["name"] for pdf in baseline["pdfs"]], ["original.pdf", "z.pdf"])
        invalid = []
        invalid.append({**baseline, "fixed": list(reversed(baseline["fixed"]))})
        invalid.append({**baseline, "fixed": [{**baseline["fixed"][0], "name": "/tmp/report"}, *baseline["fixed"][1:]]})
        invalid.append({**baseline, "fixed": [{**baseline["fixed"][0], "inode": True}, *baseline["fixed"][1:]]})
        invalid.append({**baseline, "fixed": [{**baseline["fixed"][0], "size": 1.0}, *baseline["fixed"][1:]]})
        invalid.append({**baseline, "pdfs": list(reversed(baseline["pdfs"]))})
        invalid.append({**baseline, "pdfs": [{**baseline["pdfs"][0], "name": "../original.pdf"}, baseline["pdfs"][1]]})
        invalid.append({**baseline, "pdfs": [{**baseline["pdfs"][0], "extra": True}, baseline["pdfs"][1]]})
        invalid.append({**baseline, "pdfs": [baseline["pdfs"][0], baseline["pdfs"][0]]})
        for forged in invalid:
            with self.subTest(forged=forged):
                raw = dict(marker)
                raw["pdf_baseline_json_b64"] = base64.b64encode(
                    json.dumps(forged, sort_keys=True, separators=(",", ":")).encode()).decode()
                atomic_write_json(handle.marker_path, raw)
                with self.assertRaisesRegex(ValueError, r"^Pending retry transaction cannot be recovered safely$"):
                    abort_retry_transaction(self.project)
                handle.marker_path.write_bytes(valid)
        self.assertTrue(abort_retry_transaction(self.project))

    def test_begin_rejects_polymorphic_or_noncanonical_preparation_without_reserving_project(self):
        class SnapshotSubclass(RetrySnapshot): pass
        class ItemSubclass(RetryItem): pass
        class EvilStr(str): pass
        class EvilInt(int): pass
        absolute_project = str(self.project)
        class AlwaysEqualDict(dict):
            def __eq__(self, other): return True
            def __ne__(self, other): return False
        class StatefulItems(dict):
            calls = 0
            def items(self):
                self.calls += 1
                if self.calls == 1:
                    return {**self, "success": 99}.items()
                return super().items()
        class ExplodingItems(dict):
            def items(self): raise ValueError(absolute_project)

        base = self.preparation
        snapshot = base.snapshot
        item = base.items[0]
        bad_item = ItemSubclass(item.retry_id, item.label, item.failure_class, item.report_index, item.included_index)
        stateful_backing = StatefulItems(snapshot.report)
        cases = [
            RetryPreparation(SnapshotSubclass(snapshot.report_revision, snapshot.report, snapshot.included, snapshot.ledger, snapshot.items), base.selected_ids, base.items, base.included_rows, base._project_identity),
            RetryPreparation(snapshot, base.selected_ids, (bad_item,), base.included_rows, base._project_identity),
            RetryPreparation(snapshot, (EvilStr(base.selected_ids[0]),), base.items, base.included_rows, base._project_identity),
            RetryPreparation(RetrySnapshot(snapshot.report_revision, snapshot.report, snapshot.included, snapshot.ledger,
                (RetryItem(item.retry_id, item.label, item.failure_class, EvilInt(item.report_index), item.included_index),)), base.selected_ids, base.items, base.included_rows, base._project_identity),
            RetryPreparation(RetrySnapshot(snapshot.report_revision, MappingProxyType(AlwaysEqualDict({**snapshot.report, "success": 99})), snapshot.included, snapshot.ledger, snapshot.items), base.selected_ids, base.items, base.included_rows, base._project_identity),
            RetryPreparation(RetrySnapshot(snapshot.report_revision, MappingProxyType({**snapshot.report,
                "downloaded": (MappingProxyType(AlwaysEqualDict({**snapshot.report["downloaded"][0], "id": "forged"})),)}), snapshot.included, snapshot.ledger, snapshot.items), base.selected_ids, base.items, base.included_rows, base._project_identity),
            RetryPreparation(RetrySnapshot(snapshot.report_revision, snapshot.report,
                (MappingProxyType(AlwaysEqualDict({**snapshot.included[1], "title": "forged"})), snapshot.included[0]), snapshot.ledger, snapshot.items), base.selected_ids, base.items, base.included_rows, base._project_identity),
            RetryPreparation(RetrySnapshot(snapshot.report_revision, MappingProxyType(stateful_backing), snapshot.included, snapshot.ledger, snapshot.items), base.selected_ids, base.items, base.included_rows, base._project_identity),
            RetryPreparation(RetrySnapshot(snapshot.report_revision, MappingProxyType(ExplodingItems(snapshot.report)), snapshot.included, snapshot.ledger, snapshot.items), base.selected_ids, base.items, base.included_rows, base._project_identity),
            RetryPreparation(snapshot, base.selected_ids,
                (RetryItem(item.retry_id, "forged", item.failure_class, item.report_index, item.included_index),), base.included_rows, base._project_identity),
        ]
        for forged in cases:
            with self.subTest(kind=type(forged.snapshot).__name__):
                with self.assertRaises(ValueError) as caught:
                    begin_retry_transaction(self.project, forged, self.staging_name, self.project / self.staging_name)
                self.assertNotIn(str(self.project), str(caught.exception))
                self.assertFalse((self.project / PENDING_RETRY_FILE).exists())
                begin_retry_transaction(self.project, base, self.staging_name, self.project / self.staging_name)
                self.assertTrue(abort_retry_transaction(self.project))
        self.assertEqual(stateful_backing.calls, 1)

    def test_begin_uses_fresh_authoritative_facts_for_abort_marker(self):
        snapshot = self.preparation.snapshot
        forged_report = MappingProxyType({**snapshot.report, "opaque": "forged"})
        forged = RetryPreparation(RetrySnapshot(snapshot.report_revision, forged_report, snapshot.included, snapshot.ledger, snapshot.items),
            self.preparation.selected_ids, self.preparation.items, self.preparation.included_rows, self.preparation._project_identity)
        with self.assertRaises(ValueError):
            begin_retry_transaction(self.project, forged, self.staging_name, self.project / self.staging_name)
        self.assertFalse((self.project / PENDING_RETRY_FILE).exists())

    def test_begin_compares_nested_selected_facts_without_frozen_container_false_staleness(self):
        included_path = self.project / "filtered" / "included_papers.jsonl"
        rows = [json.loads(line) for line in included_path.read_text().splitlines()]
        rows[1]["authors"] = [{"name": "Ada", "aliases": ["A. Lovelace"]}]
        rows[1]["keywords"] = ["retrieval", {"topic": ["pdf", "retry"]}]
        atomic_write_jsonl(included_path, rows)
        prepared = preparation(self.project)

        begin_retry_transaction(self.project, prepared, self.staging_name, self.project / self.staging_name)

        self.assertTrue(abort_retry_transaction(self.project))

    def test_begin_rejects_python_equal_but_json_distinct_selected_facts(self):
        included_path = self.project / "filtered" / "included_papers.jsonl"
        rows = [json.loads(line) for line in included_path.read_text().splitlines()]
        rows[1]["opaque_numeric"] = 1
        atomic_write_jsonl(included_path, rows)
        prepared = preparation(self.project)
        item = prepared.items[0]

        for forged_value in (True, 1.0):
            with self.subTest(forged_value=forged_value):
                forged_row = MappingProxyType({**prepared.snapshot.included[item.included_index],
                    "opaque_numeric": forged_value})
                forged_included = tuple(forged_row if index == item.included_index else row
                    for index, row in enumerate(prepared.snapshot.included))
                forged_snapshot = RetrySnapshot(prepared.snapshot.report_revision, prepared.snapshot.report,
                    forged_included, prepared.snapshot.ledger, prepared.snapshot.items)
                forged = RetryPreparation(forged_snapshot, prepared.selected_ids, prepared.items,
                    (forged_row,), prepared._project_identity)

                try:
                    with self.assertRaisesRegex(ValueError, r"^Retry transaction preparation is stale$"):
                        begin_retry_transaction(self.project, forged, self.staging_name, self.project / self.staging_name)
                finally:
                    if (self.project / PENDING_RETRY_FILE).exists():
                        abort_retry_transaction(self.project)
                self.assertFalse((self.project / PENDING_RETRY_FILE).exists())

                begin_retry_transaction(self.project, prepared, self.staging_name, self.project / self.staging_name)
                self.assertTrue(abort_retry_transaction(self.project))

    def test_begin_releases_reservation_when_marker_encoding_fails(self):
        ledger = load_workflow_state(self.project)
        ledger["opaque"] = float("nan")
        save_workflow_state(self.project, ledger)

        with self.assertRaisesRegex(ValueError, r"^Retry transaction marker could not be written$") as caught:
            begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        self.assertNotIn(str(self.project), str(caught.exception))
        self.assertFalse((self.project / PENDING_RETRY_FILE).exists())

        ledger = load_workflow_state(self.project)
        ledger.pop("opaque")
        save_workflow_state(self.project, ledger)
        begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        self.assertTrue(abort_retry_transaction(self.project))

    def test_begin_rejects_fixed_authority_races_after_final_fingerprint_without_rollback(self):
        report_path = self.project / "pdfs" / "download_report.json"
        included_path = self.project / "filtered" / "included_papers.jsonl"
        ledger_path = self.project / "workflow_state.json"

        def mutate_report():
            report = json.loads(report_path.read_text())
            report["concurrent"] = "report"
            atomic_write_json(report_path, report)

        def mutate_included():
            rows = [json.loads(line) for line in included_path.read_text().splitlines()]
            rows[0]["concurrent"] = "included"
            atomic_write_jsonl(included_path, rows)

        def mutate_ledger():
            ledger = load_workflow_state(self.project)
            ledger["concurrent"] = "ledger"
            save_workflow_state(self.project, ledger)

        for label, mutate, path in (
                ("report", mutate_report, report_path),
                ("included", mutate_included, included_path),
                ("ledger", mutate_ledger, ledger_path)):
            with self.subTest(authority=label):
                calls = 0
                concurrent_bytes = None

                def raced_fingerprint(project):
                    nonlocal calls, concurrent_bytes
                    fingerprint = _authoritative_fingerprint(project)
                    calls += 1
                    if calls == 2:
                        mutate()
                        concurrent_bytes = path.read_bytes()
                    return fingerprint

                with patch("reviewpilot_core.retrieval_retry_transaction._authoritative_fingerprint",
                           side_effect=raced_fingerprint):
                    with self.assertRaisesRegex(ValueError, r"^Retry transaction preparation is stale$"):
                        begin_retry_transaction(self.project, self.preparation,
                            self.staging_name, self.project / self.staging_name)

                self.assertGreaterEqual(calls, 2)
                self.assertFalse((self.project / PENDING_RETRY_FILE).exists())
                self.assertEqual(path.read_bytes(), concurrent_bytes)
                fresh = preparation(self.project)
                begin_retry_transaction(self.project, fresh, self.staging_name, self.project / self.staging_name)
                self.assertTrue(abort_retry_transaction(self.project))
                self.preparation = preparation(self.project)

    def test_begin_postwrite_validation_failure_removes_only_its_exact_marker(self):
        before = _authoritative_fingerprint(self.project)
        with patch("reviewpilot_core.retrieval_retry_transaction._validate_current_before",
                   side_effect=ValueError("postwrite seam")):
            with self.assertRaisesRegex(ValueError, r"^Retry transaction marker could not be validated$"):
                begin_retry_transaction(self.project, self.preparation,
                    self.staging_name, self.project / self.staging_name)

        self.assertFalse((self.project / PENDING_RETRY_FILE).exists())
        self.assertEqual(_authoritative_fingerprint(self.project), before)
        begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        self.assertTrue(abort_retry_transaction(self.project))

    def test_begin_postwrite_failure_leaves_a_marker_it_cannot_prove_it_owns(self):
        marker_path = self.project / PENDING_RETRY_FILE

        def replace_own_marker(project, marker):
            replacement = json.loads(marker_path.read_text())
            replacement["transaction_id"] = "f" * 64
            atomic_write_json(marker_path, replacement)
            raise ValueError("postwrite replacement")

        with patch("reviewpilot_core.retrieval_retry_transaction._validate_current_before",
                   side_effect=replace_own_marker):
            with self.assertRaisesRegex(ValueError, r"^Retry transaction marker could not be validated$"):
                begin_retry_transaction(self.project, self.preparation,
                    self.staging_name, self.project / self.staging_name)

        self.assertTrue(marker_path.exists())
        self.assertEqual(json.loads(marker_path.read_text())["transaction_id"], "f" * 64)
        marker_path.unlink()
        begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        self.assertTrue(abort_retry_transaction(self.project))

    def test_begin_postwrite_failure_preserves_same_bytes_new_inode_marker(self):
        marker_path = self.project / PENDING_RETRY_FILE
        replacement = {}

        def replace_with_same_bytes(project, marker):
            old_inode = marker_path.stat().st_ino
            temporary = marker_path.with_name("same-marker.tmp")
            temporary.write_bytes(marker_path.read_bytes())
            os.replace(temporary, marker_path)
            replacement["inode"] = marker_path.stat().st_ino
            self.assertNotEqual(replacement["inode"], old_inode)
            raise ValueError("postwrite replacement")

        with patch("reviewpilot_core.retrieval_retry_transaction._validate_current_before",
                   side_effect=replace_with_same_bytes):
            with self.assertRaisesRegex(ValueError, r"^Retry transaction marker could not be validated$"):
                begin_retry_transaction(self.project, self.preparation,
                    self.staging_name, self.project / self.staging_name)

        self.assertTrue(marker_path.exists())
        self.assertEqual(marker_path.stat().st_ino, replacement["inode"])
        self.assertTrue(reconcile_retry_transaction(self.project))

    def test_marker_round_trips_sentinel_shaped_values_and_absolute_keys_without_raw_paths(self):
        report_path = self.project / "pdfs" / "download_report.json"
        report = json.loads(report_path.read_text())
        report["opaque"] = [
            {"$reviewpilot_absolute_path": "L3RtcA=="},
            {"$reviewpilot_absolute_path": "not-base64!"},
            {str(self.project / "absolute-key"): str(self.project / "absolute-value")},
        ]
        atomic_write_json(report_path, report)
        prepared = preparation(self.project)
        before = prepared.snapshot.mutable_fact_copies()
        begin_retry_transaction(self.project, prepared, self.staging_name, self.project / self.staging_name)
        raw = (self.project / PENDING_RETRY_FILE).read_text()
        self.assertNotIn(str(self.project), raw)
        atomic_write_json(report_path, {"mutated": True})
        self.assertTrue(abort_retry_transaction(self.project))
        self.assertEqual(json.loads(report_path.read_text()), before[0])

    def test_marker_version_requires_exact_integer_two_and_transaction_id_schema(self):
        for invalid in (True, 2.0, "2", 1):
            with self.subTest(invalid=invalid):
                begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
                abandon_retry_transaction(self.project)
                marker = self.project / PENDING_RETRY_FILE
                data = json.loads(marker.read_text()); data["version"] = invalid
                marker.write_text(json.dumps(data))
                with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)
                marker.unlink()

        for invalid in (None, "", "0" * 63, "g" * 64, 1):
            with self.subTest(transaction_id=invalid):
                begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
                abandon_retry_transaction(self.project)
                marker = self.project / PENDING_RETRY_FILE
                data = json.loads(marker.read_text()); data["transaction_id"] = invalid
                marker.write_text(json.dumps(data))
                with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)
                marker.unlink()

    def test_marker_rejects_noncanonical_duplicate_before_keys(self):
        begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        abandon_retry_transaction(self.project)
        marker = self.project / PENDING_RETRY_FILE
        data = json.loads(marker.read_text())
        raw = base64.b64decode(data["before_json_b64"])
        before = json.loads(raw)
        raw = raw[:-1] + b',"report":' + json.dumps(before["report"], sort_keys=True, separators=(",", ":")).encode() + b"}"
        data["before_json_b64"] = base64.b64encode(raw).decode()
        marker.write_text(json.dumps(data))
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)

    def test_begin_rejects_hardlinked_authoritative_files(self):
        paths = [self.project / "pdfs" / "download_report.json", self.project / "filtered" / "included_papers.jsonl", self.project / "workflow_state.json"]
        for index, path in enumerate(paths):
            with self.subTest(path=path.name):
                link = self.project / f"authority-link-{index}"
                os.link(path, link)
                with self.assertRaises(ValueError):
                    begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
                link.unlink()

    def test_reconcile_rejects_hardlinked_marker_authority_and_candidate_without_deleting_links(self):
        handle = begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        abandon_retry_transaction(self.project)
        marker_link = self.project / "marker-link"; os.link(handle.marker_path, marker_link)
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)
        self.assertTrue(handle.marker_path.exists()); self.assertTrue(marker_link.exists()); marker_link.unlink()

        authority = self.project / "workflow_state.json"; authority_link = self.project / "authority-link"; os.link(authority, authority_link)
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)
        self.assertTrue(authority.exists()); self.assertTrue(authority_link.exists()); authority_link.unlink()

        candidate = self.project / "pdfs" / handle.candidate_names[0]; candidate.write_bytes(b"%PDF-new")
        candidate_link = self.project / "candidate-link"; os.link(candidate, candidate_link)
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)
        self.assertTrue(candidate.exists()); self.assertTrue(candidate_link.exists())

    def test_begin_revalidates_and_rejects_every_collision_kind(self):
        name = f"retry-{self.preparation.snapshot.report_revision}-{self.preparation.selected_ids[0]}.pdf"
        for kind in ("file", "dir", "symlink", "broken"):
            with self.subTest(kind=kind):
                path = self.project / "pdfs" / name
                if kind == "file": path.write_bytes(b"x")
                elif kind == "dir": path.mkdir()
                else: path.symlink_to(self.project / ("missing" if kind == "broken" else "workflow_state.json"))
                with self.assertRaises(ValueError) as caught:
                    begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
                self.assertNotIn(str(self.project), str(caught.exception)); path.unlink() if not path.is_dir() else path.rmdir()

    def test_abort_restores_all_facts_and_removes_only_owned_names(self):
        before = self.preparation.snapshot.mutable_fact_copies()
        handle = begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        candidate = self.project / "pdfs" / handle.candidate_names[0]
        candidate.write_bytes(b"%PDF-new")
        keep = self.project / "pdfs" / "keep.pdf"; keep.write_bytes(b"keep")
        staging = self.project / self.staging_name; staging.mkdir(); (staging / "junk").write_text("x")
        atomic_write_json(self.project / "pdfs" / "download_report.json", {"mutated": True})
        atomic_write_jsonl(self.project / "filtered" / "included_papers.jsonl", [{"mutated": True}])
        ledger = load_workflow_state(self.project); ledger["stages"]["retrieval"]["status"] = "running"; save_workflow_state(self.project, ledger)
        self.assertTrue(abort_retry_transaction(self.project))
        self.assertEqual(json.loads((self.project / "pdfs" / "download_report.json").read_text()), before[0])
        self.assertEqual([json.loads(line) for line in (self.project / "filtered" / "included_papers.jsonl").read_text().splitlines()], before[1])
        self.assertEqual(load_workflow_state(self.project)["stages"]["retrieval"], before[2])
        self.assertFalse(candidate.exists()); self.assertFalse(staging.exists()); self.assertEqual(keep.read_bytes(), b"keep")
        self.assertFalse((self.project / PENDING_RETRY_FILE).exists())

    def test_abort_stops_when_authority_write_is_followed_by_a_new_transaction(self):
        first = begin_retry_transaction(
            self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        marker_path = self.project / PENDING_RETRY_FILE
        report_path = self.project.resolve() / "pdfs" / "download_report.json"
        second = {}
        injected = False

        def replace_transaction(path, data, *args, **kwargs):
            nonlocal injected
            result = atomic_write_json(path, data, *args, **kwargs)
            if Path(path) == report_path and not injected:
                injected = True
                abandon_retry_transaction(self.project, first.transaction_id)
                marker_path.unlink()
                prepared = preparation(self.project)
                handle = begin_retry_transaction(
                    self.project, prepared, self.staging_name, self.project / self.staging_name)
                (self.project / self.staging_name).mkdir()
                (self.project / "pdfs" / handle.candidate_names[0]).write_bytes(b"%PDF-second")
                second.update(handle=handle, marker=marker_path.read_bytes())
            return result

        with patch("reviewpilot_core.retrieval_retry_transaction.atomic_write_json",
                   side_effect=replace_transaction):
            with self.assertRaisesRegex(
                    ValueError, r"^Pending retry transaction cannot be recovered safely$"):
                abort_retry_transaction(self.project, first.transaction_id)

        handle = second["handle"]
        self.assertEqual(marker_path.read_bytes(), second["marker"])
        self.assertTrue((self.project / self.staging_name).exists())
        self.assertTrue((self.project / "pdfs" / handle.candidate_names[0]).exists())
        self.assertFalse(reconcile_retry_transaction(self.project))
        self.assertTrue(abort_retry_transaction(self.project, handle.transaction_id))

    def test_abort_holds_cross_process_lock_through_final_marker_unlink(self):
        handle = begin_retry_transaction(
            self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        resolved_project = self.project.resolve()
        marker = resolved_project / PENDING_RETRY_FILE
        marker_bytes = marker.read_bytes()
        real_unlink = Path.unlink
        contender = None
        acquired_during_unlink = False

        def raced_unlink(path, *args, **kwargs):
            nonlocal contender, acquired_during_unlink
            if Path(path) == marker:
                contender, acquired = marker_lock_contender(resolved_project, marker_bytes)
                try:
                    contender.wait(timeout=0.2)
                    acquired_during_unlink = acquired.exists()
                except subprocess.TimeoutExpired:
                    acquired_during_unlink = False
            return real_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", autospec=True, side_effect=raced_unlink):
            self.assertTrue(abort_retry_transaction(self.project, handle.transaction_id))
        self.assertIsNotNone(contender)
        contender.wait(timeout=2)
        self.assertFalse(acquired_during_unlink)
        self.assertTrue(marker.exists())
        self.assertTrue(reconcile_retry_transaction(self.project))

    def test_live_reconcile_skips_but_abandon_allows_restart_recovery(self):
        begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        self.assertFalse(reconcile_retry_transaction(self.project))
        abandon_retry_transaction(self.project)
        self.assertTrue(reconcile_retry_transaction(self.project)); self.assertFalse(reconcile_retry_transaction(self.project))

    def test_every_abort_failure_keeps_the_complete_rollback_retryable(self):
        ledger = load_workflow_state(self.project)
        ledger["unknown_valid"] = {"preserve": [1, {"nested": True}]}
        save_workflow_state(self.project, ledger)
        self.preparation = preparation(self.project)
        before_report, before_included, _ = self.preparation.snapshot.mutable_fact_copies()
        before_ledger = load_workflow_state(self.project)
        resolved_project = self.project.resolve()
        keep = self.project / "pdfs" / "unowned-keep.pdf"
        keep.write_bytes(b"keep")
        cases = (
            ("report-write", "reconcile"),
            ("included-write", "reconcile"),
            ("ledger-write", "reconcile"),
            ("candidate-unlink", "abort"),
            ("staging-rmtree", "abort"),
            ("marker-unlink", "abort"),
        )

        for seam, recovery_mode in cases:
            with self.subTest(seam=seam, recovery_mode=recovery_mode):
                handle = begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
                marker = self.project / PENDING_RETRY_FILE
                candidate = self.project / "pdfs" / handle.candidate_names[0]
                candidate.write_bytes(b"%PDF-new")
                staging = self.project / self.staging_name
                staging.mkdir()
                (staging / "owned-junk").write_text("x")
                atomic_write_json(self.project / "pdfs" / "download_report.json", {"mutated": seam})
                atomic_write_jsonl(self.project / "filtered" / "included_papers.jsonl", [{"mutated": seam}])
                mutated_ledger = load_workflow_state(self.project)
                mutated_ledger["unknown_valid"] = {"mutated": seam}
                for index, stage in enumerate(mutated_ledger["stages"].values()):
                    stage["attempt"] += 100 + index
                    stage["counts"] = {"mutated": index}
                save_workflow_state(self.project, mutated_ledger)

                failed = {"once": False}
                real_unlink = Path.unlink
                real_rmtree = shutil.rmtree

                def fail_once(target):
                    if not failed["once"]:
                        failed["once"] = True
                        raise OSError(str(self.project / target))

                def injected_json(path, data, *args, **kwargs):
                    if Path(path) == resolved_project / "pdfs" / "download_report.json":
                        fail_once("report-write")
                    return atomic_write_json(path, data, *args, **kwargs)

                def injected_jsonl(path, records, *args, **kwargs):
                    if Path(path) == resolved_project / "filtered" / "included_papers.jsonl":
                        fail_once("included-write")
                    return atomic_write_jsonl(path, records, *args, **kwargs)

                def injected_ledger(project, state):
                    if Path(project).resolve() == self.project.resolve():
                        fail_once("ledger-write")
                    return save_workflow_state(project, state)

                def injected_unlink(path, *args, **kwargs):
                    target = resolved_project / PENDING_RETRY_FILE if seam == "marker-unlink" else resolved_project / "pdfs" / handle.candidate_names[0]
                    if Path(path) == target:
                        fail_once(seam)
                    return real_unlink(path, *args, **kwargs)

                def injected_rmtree(path, *args, **kwargs):
                    if Path(path) == resolved_project / self.staging_name:
                        fail_once("staging-rmtree")
                    return real_rmtree(path, *args, **kwargs)

                if seam == "report-write":
                    failure_patch = patch("reviewpilot_core.retrieval_retry_transaction.atomic_write_json", side_effect=injected_json)
                elif seam == "included-write":
                    failure_patch = patch("reviewpilot_core.retrieval_retry_transaction.atomic_write_jsonl", side_effect=injected_jsonl)
                elif seam == "ledger-write":
                    failure_patch = patch("reviewpilot_core.retrieval_retry_transaction.save_workflow_state", side_effect=injected_ledger)
                elif seam in {"candidate-unlink", "marker-unlink"}:
                    failure_patch = patch.object(Path, "unlink", autospec=True, side_effect=injected_unlink)
                else:
                    failure_patch = patch("reviewpilot_core.retrieval_retry_transaction.shutil.rmtree", side_effect=injected_rmtree)

                if recovery_mode == "reconcile":
                    abandon_retry_transaction(self.project)
                    recover = reconcile_retry_transaction
                else:
                    recover = abort_retry_transaction
                with failure_patch:
                    with self.assertRaises(ValueError) as caught:
                        recover(self.project)
                self.assertTrue(failed["once"])
                self.assertNotIn(str(self.project), str(caught.exception))
                self.assertTrue(marker.exists())

                # The marker is the durable promise that a partially completed
                # rollback can be repeated until every authoritative fact wins.
                self.assertTrue(reconcile_retry_transaction(self.project))
                self.assertEqual(json.loads((self.project / "pdfs" / "download_report.json").read_text()), before_report)
                self.assertEqual([json.loads(line) for line in (self.project / "filtered" / "included_papers.jsonl").read_text().splitlines()], before_included)
                self.assertEqual(load_workflow_state(self.project), before_ledger)
                self.assertFalse(candidate.exists())
                self.assertFalse(staging.exists())
                self.assertFalse(marker.exists())
                self.assertEqual(keep.read_bytes(), b"keep")

    def test_active_reservations_are_isolated_by_project(self):
        with tempfile.TemporaryDirectory() as second_temp:
            second_project = Path(second_temp) / "project"
            second_project.mkdir()
            retryable_project(second_project)
            second_preparation = preparation(second_project)
            second_staging = ".retrieval_retry_staging_second"
            try:
                first = begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
                second = begin_retry_transaction(second_project, second_preparation, second_staging, second_project / second_staging)
                first_marker = first.marker_path.read_bytes()
                with self.assertRaisesRegex(ValueError, "cannot begin safely|already active"):
                    begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
                self.assertEqual(first.marker_path.read_bytes(), first_marker)
                self.assertTrue(second.marker_path.exists())
                self.assertFalse(reconcile_retry_transaction(self.project))
                self.assertFalse(reconcile_retry_transaction(second_project))

                abandon_retry_transaction(self.project)
                abandon_retry_transaction(second_project)
                self.assertTrue(reconcile_retry_transaction(self.project))
                self.assertTrue(reconcile_retry_transaction(second_project))
            finally:
                abandon_retry_transaction(self.project)
                abandon_retry_transaction(second_project)

    def test_malformed_or_symlink_marker_and_unsafe_staging_fail_closed(self):
        marker = self.project / PENDING_RETRY_FILE
        marker.write_text("{}")
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)
        marker.unlink(); marker.symlink_to(self.project / "workflow_state.json")
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)
        marker.unlink(); begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        abandon_retry_transaction(self.project); (self.project / self.staging_name).symlink_to(self.project / "filtered")
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)


class RetrySourceCommitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.project = Path(self.temp.name).resolve() / "project"
        self.project.mkdir(); retryable_project(self.project)
        included = [json.loads(line) for line in (self.project / "filtered" / "included_papers.jsonl").read_text().splitlines()]
        included.append({"id": "failed-two", "title": "Failed Two"})
        atomic_write_jsonl(self.project / "filtered" / "included_papers.jsonl", included)
        report_path = self.project / "pdfs" / "download_report.json"; report = json.loads(report_path.read_text())
        report["failed"] = 2; report["failed_papers"].append({"id": "failed-two", "title": "Failed Two", "failure_class": "network"})
        atomic_write_json(report_path, report)
        ledger = load_workflow_state(self.project); ledger["stages"]["retrieval"]["counts"]["failed"] = 2
        ledger["stages"]["retrieval"]["last_valid"]["counts"]["failed"] = 2; save_workflow_state(self.project, ledger)
        self.prepared = preparation(self.project)
        self.staging_name = ".retrieval_retry_staging_sources"
        begin_retry_transaction(self.project, self.prepared, self.staging_name, self.project / self.staging_name)

    def tearDown(self):
        abandon_retry_transaction(self.project); self.temp.cleanup()

    def download(self, success_count):
        def callback(root, project_id):
            staged = root / project_id
            rows = [json.loads(line) for line in (staged / "filtered" / "included_papers.jsonl").read_text().splitlines()]
            downloaded, failed = [], []
            for index, row in enumerate(rows):
                if index < success_count:
                    pdf = staged / "pdfs" / f"source-{index}.pdf"; pdf.write_bytes(b"\n%PDF-1.7\nsource")
                    row.update(pdf_downloaded=True, pdf_path=str(pdf), retrieval_status="downloaded")
                    downloaded.append({"id": row["id"], "title": row.get("title", ""), "path": str(pdf)})
                else:
                    row.update(pdf_downloaded=False, retrieval_status="unavailable", pdf_failure_class="download_failed")
                    failed.append({"id": row["id"], "title": row.get("title", ""), "doi": "", "url": "", "failure_class": "download_failed"})
            atomic_write_jsonl(staged / "filtered" / "included_papers.jsonl", rows)
            atomic_write_json(staged / "pdfs" / "download_report.json", {"success": len(downloaded), "failed": len(failed),
                "downloaded": downloaded, "failed_papers": failed, "pdf_count": len(downloaded), "attempted": len(rows)})
            return {"success": len(downloaded), "failed": len(failed),
                "stats": {"success": len(downloaded), "failed": len(failed)}}
        return callback

    def decoded_sources(self):
        marker = json.loads((self.project / PENDING_RETRY_FILE).read_text())
        return marker, json.loads(base64.b64decode(marker["sources_json_b64"]))

    def test_wrapper_commits_exact_path_safe_success_source_before_return(self):
        outcome = run_retry_transaction_staging(self.project, self.prepared, self.download(2))
        marker, sources = self.decoded_sources()
        self.assertEqual(sources, {"pdfs": [{"retry_id": item.retry_id, "source_name": item.source_path.name,
            "size": item.source_size, "sha256": item.source_sha256} for item in outcome.successful_pdfs]})
        self.assertNotIn(str(self.project), json.dumps(marker))

    def test_wrapper_commits_selected_order_for_partial_outcome(self):
        outcome = run_retry_transaction_staging(self.project, self.prepared, self.download(1))
        self.assertEqual([row["retry_id"] for row in self.decoded_sources()[1]["pdfs"]],
            [outcome.successful_pdfs[0].retry_id])

    def test_wrapper_commits_explicit_empty_sources_for_all_failure(self):
        outcome = run_retry_transaction_staging(self.project, self.prepared, self.download(0))
        self.assertEqual(outcome.successful_pdfs, ())
        self.assertEqual(self.decoded_sources()[1], {"pdfs": []})

    def test_outer_generation_cannot_overwrite_or_release_reentrant_aba_transaction(self):
        first_marker = self.project / PENDING_RETRY_FILE
        first = json.loads(first_marker.read_text())
        first_id = first["transaction_id"]
        download = self.download(1)
        replacement = {}

        def replace_with_same_business_transaction(root, project_id):
            result = download(root, project_id)
            saved = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertTrue(abort_retry_transaction(self.project, first_id))
            handle = begin_retry_transaction(
                self.project, self.prepared, self.staging_name, self.project / self.staging_name)
            for relative, payload in saved.items():
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
            replacement["handle"] = handle
            replacement["marker"] = handle.marker_path.read_bytes()
            replacement["staging"] = saved
            return result

        with self.assertRaisesRegex(ValueError, r"^Retry transaction staging failed$"):
            run_retry_transaction_staging(self.project, self.prepared, replace_with_same_business_transaction)

        second = replacement["handle"]
        self.assertNotEqual(second.transaction_id, first_id)
        self.assertEqual(second.marker_path.read_bytes(), replacement["marker"])
        self.assertEqual(
            {path.relative_to(self.project / self.staging_name): path.read_bytes()
                for path in (self.project / self.staging_name).rglob("*") if path.is_file()},
            replacement["staging"],
        )
        self.assertFalse(abort_retry_transaction(self.project, first_id))
        abandon_retry_transaction(self.project, first_id)
        self.assertFalse(reconcile_retry_transaction(self.project))
        self.assertTrue(abort_retry_transaction(self.project, second.transaction_id))

    def test_malformed_source_commit_fails_recovery_closed(self):
        run_retry_transaction_staging(self.project, self.prepared, self.download(1))
        abandon_retry_transaction(self.project)
        marker_path = self.project / PENDING_RETRY_FILE; marker = json.loads(marker_path.read_text())
        marker["sources_json_b64"] = base64.b64encode(b'{"pdfs":[{"retry_id":"x","source_name":"../x.pdf","size":1,"sha256":"x"}]}').decode()
        marker_path.write_text(json.dumps(marker))
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)

    def test_marker_write_failure_keeps_abort_marker_and_staging_retryable(self):
        marker_path = self.project / PENDING_RETRY_FILE; before = marker_path.read_bytes()
        with patch("reviewpilot_core.retrieval_retry_transaction.atomic_write_json", side_effect=OSError(str(self.project))):
            with self.assertRaises(ValueError) as caught:
                run_retry_transaction_staging(self.project, self.prepared, self.download(1))
        self.assertNotIn(str(self.project), str(caught.exception)); self.assertEqual(marker_path.read_bytes(), before)
        self.assertTrue((self.project / self.staging_name).exists())
        self.assertTrue(abort_retry_transaction(self.project)); self.assertFalse((self.project / self.staging_name).exists())

    def test_wrapper_rejects_authority_changed_when_callback_result_is_released(self):
        marker_path = self.project / PENDING_RETRY_FILE
        before = marker_path.read_bytes()
        report_path = self.project / "pdfs" / "download_report.json"

        class MutatingResult(dict):
            def __del__(self):
                report = json.loads(report_path.read_text())
                report["failed_papers"][0]["title"] = "drift-after-staging"
                atomic_write_json(report_path, report)

        download = self.download(1)

        def mutating_download(root, project_id):
            return MutatingResult(download(root, project_id))

        with self.assertRaisesRegex(ValueError, r"^Retry transaction staging failed$") as caught:
            run_retry_transaction_staging(self.project, self.prepared, mutating_download)

        self.assertNotIn(str(self.project), str(caught.exception))
        self.assertEqual(marker_path.read_bytes(), before)
        self.assertNotIn("sources_json_b64", json.loads(marker_path.read_text()))
        self.assertFalse(reconcile_retry_transaction(self.project))
        self.assertTrue(abort_retry_transaction(self.project))

    def test_wrapper_rejects_same_bytes_new_inode_for_existing_pdf(self):
        marker_path = self.project / PENDING_RETRY_FILE
        before = marker_path.read_bytes()
        original = self.project / "pdfs" / "original.pdf"
        old_inode = original.stat().st_ino
        download = self.download(1)

        class ReplacingResult(dict):
            def __del__(self):
                replacement = original.with_name("same-bytes.tmp")
                replacement.write_bytes(original.read_bytes())
                os.replace(replacement, original)

        with self.assertRaisesRegex(ValueError, r"^Retry transaction staging failed$"):
            run_retry_transaction_staging(
                self.project, self.prepared, lambda root, project_id: ReplacingResult(download(root, project_id)))

        self.assertNotEqual(original.stat().st_ino, old_inode)
        self.assertEqual(marker_path.read_bytes(), before)
        self.assertNotIn("sources_json_b64", json.loads(marker_path.read_text()))
        self.assertFalse(reconcile_retry_transaction(self.project))
        self.assertTrue(abort_retry_transaction(self.project))

    def test_source_commit_rejects_same_bytes_new_inode_for_each_fixed_authority(self):
        for relative in ("pdfs/download_report.json", "filtered/included_papers.jsonl", "workflow_state.json"):
            with self.subTest(relative=relative):
                marker_path = self.project / PENDING_RETRY_FILE
                before = marker_path.read_bytes()
                authority = self.project / relative
                download = self.download(1)

                class ReplacingResult(dict):
                    def __del__(self):
                        replacement = authority.with_name(authority.name + ".tmp")
                        replacement.write_bytes(authority.read_bytes())
                        os.replace(replacement, authority)

                with self.assertRaisesRegex(ValueError, r"^Retry transaction staging failed$"):
                    run_retry_transaction_staging(
                        self.project, self.prepared, lambda root, project_id: ReplacingResult(download(root, project_id)))
                self.assertEqual(marker_path.read_bytes(), before)
                self.assertNotIn("sources_json_b64", json.loads(marker_path.read_text()))
                self.assertTrue(abort_retry_transaction(self.project))
                self.prepared = preparation(self.project)
                begin_retry_transaction(self.project, self.prepared, self.staging_name, self.project / self.staging_name)

    def test_wrapper_rejects_existing_pdf_changed_after_inner_validation(self):
        original = self.project / "pdfs" / "original.pdf"
        original_bytes = original.read_bytes()
        download = self.download(1)

        for operation in ("mutate", "delete", "replace"):
            with self.subTest(operation=operation):
                marker = self.project / PENDING_RETRY_FILE
                before = marker.read_bytes()

                class MutatingResult(dict):
                    def __del__(self):
                        if operation == "mutate":
                            original.write_bytes(b"%PDF-mutated")
                        elif operation == "delete":
                            original.unlink()
                        else:
                            replacement = original.with_name("replacement.tmp")
                            replacement.write_bytes(b"%PDF-replaced")
                            os.replace(replacement, original)

                def mutating_download(root, project_id):
                    return MutatingResult(download(root, project_id))

                with self.assertRaisesRegex(ValueError, r"^Retry transaction staging failed$"):
                    run_retry_transaction_staging(self.project, self.prepared, mutating_download)
                self.assertEqual(marker.read_bytes(), before)
                self.assertNotIn("sources_json_b64", json.loads(marker.read_text()))
                original.write_bytes(original_bytes)
                self.assertTrue(abort_retry_transaction(self.project))
                if operation != "replace":
                    begin_retry_transaction(
                        self.project, self.prepared, self.staging_name, self.project / self.staging_name)

    def test_source_commit_rejects_earlier_source_changed_while_later_source_is_fingerprinted(self):
        marker_path = self.project / PENDING_RETRY_FILE
        before = marker_path.read_bytes()
        real_fingerprint = _publication_pdf_fingerprint
        seen = []

        def mutate_after_second(path, parent):
            result = real_fingerprint(path, parent)
            seen.append(path)
            if len(seen) == 4:
                seen[0].write_bytes(b"%PDF-1.7\nchanged-after-first-fingerprint")
            return result

        with patch(
                "reviewpilot_core.retrieval_retry_transaction._publication_pdf_fingerprint",
                side_effect=mutate_after_second):
            with self.assertRaisesRegex(ValueError, r"^Retry transaction staging failed$") as caught:
                run_retry_transaction_staging(self.project, self.prepared, self.download(2))

        self.assertNotIn(str(self.project), str(caught.exception))
        self.assertGreaterEqual(len(seen), 5)
        self.assertEqual(marker_path.read_bytes(), before)
        self.assertNotIn("sources_json_b64", json.loads(marker_path.read_text()))
        self.assertFalse(reconcile_retry_transaction(self.project))
        self.assertTrue(abort_retry_transaction(self.project))


class RetryTargetTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.project = Path(self.temp.name).resolve() / "project"
        self.project.mkdir(); retryable_project(self.project); self.prepared = preparation(self.project)
        self.staging_name = ".retrieval_retry_staging_apply"
        self.handle = begin_retry_transaction(self.project, self.prepared, self.staging_name, self.project / self.staging_name)
        self.plan = publication(self.project, self.prepared, self.staging_name)
        self.ledger = target_ledger(self.project, self.plan)

    def tearDown(self):
        abandon_retry_transaction(self.project); self.temp.cleanup()

    def publish_target(self):
        report, included = self.plan.merged_facts.mutable_copies()
        pdf = self.plan.pdfs[0]
        pdf.destination_path.write_bytes(pdf.source_path.read_bytes())
        atomic_write_json(self.project / "pdfs" / "download_report.json", report)
        atomic_write_jsonl(self.project / "filtered" / "included_papers.jsonl", included)
        save_workflow_state(self.project, self.ledger)

    def test_record_rejects_pdf_baseline_drift_without_marker_mutation(self):
        marker = self.project / PENDING_RETRY_FILE
        before = marker.read_bytes()
        original = self.project / "pdfs" / "original.pdf"
        original.write_bytes(b"%PDF-drift")

        with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$"):
            record_retry_transaction_target(self.project, self.plan, self.ledger)

        self.assertEqual(marker.read_bytes(), before)
        self.assertTrue(abort_retry_transaction(self.project))

    def test_record_rejects_same_bytes_new_inode_for_each_fixed_authority(self):
        for relative in ("pdfs/download_report.json", "filtered/included_papers.jsonl", "workflow_state.json"):
            with self.subTest(relative=relative):
                marker = self.project / PENDING_RETRY_FILE
                before = marker.read_bytes()
                authority = self.project / relative
                replacement = authority.with_name(authority.name + ".tmp")
                replacement.write_bytes(authority.read_bytes())
                os.replace(replacement, authority)

                with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$"):
                    record_retry_transaction_target(self.project, self.plan, self.ledger)

                self.assertEqual(marker.read_bytes(), before)
                self.assertNotIn("target_json_b64", json.loads(marker.read_text()))
                self.assertTrue(abort_retry_transaction(self.project))
                self.prepared = preparation(self.project)
                self.handle = begin_retry_transaction(
                    self.project, self.prepared, self.staging_name, self.project / self.staging_name)
                self.plan = publication(self.project, self.prepared, self.staging_name)
                self.ledger = target_ledger(self.project, self.plan)

    def forged_target(self, mutate_report):
        def freeze(value):
            if type(value) is dict:
                return MappingProxyType({key: freeze(item) for key, item in value.items()})
            if type(value) is list:
                return tuple(freeze(item) for item in value)
            return value

        report, _ = self.plan.merged_facts.mutable_copies()
        mutate_report(report)
        success, failed = report["success"], report["failed"]
        status = "partial" if success and failed else "completed" if success else "failed"
        counts = {"succeeded": success, "failed": failed}
        merged = RetryMergedFacts(
            self.plan.merged_facts.report_revision, freeze(report), self.plan.merged_facts.included,
            status, MappingProxyType(counts), self.plan.merged_facts.planned_pdfs,
        )
        plan = RetryPublicationPlan(self.plan.report_revision, merged, self.plan.pdfs)
        ledger = deepcopy(self.ledger)
        ledger["stages"]["retrieval"].update(status=status, counts=counts, stale=False, error=None)
        return plan, ledger

    def refreeze_plan(self, report, included, pdfs=None, planned_pdfs=None, base_plan=None):
        def freeze(value):
            if type(value) is dict:
                return MappingProxyType({key: freeze(item) for key, item in value.items()})
            if type(value) is list:
                return tuple(freeze(item) for item in value)
            return value

        base = self.plan if base_plan is None else base_plan
        merged = base.merged_facts
        forged = RetryMergedFacts(
            merged.report_revision, freeze(report), tuple(freeze(row) for row in included),
            merged.status, merged.counts, merged.planned_pdfs if planned_pdfs is None else planned_pdfs,
        )
        return RetryPublicationPlan(base.report_revision, forged, base.pdfs if pdfs is None else pdfs)

    def assert_ledger_rejected_without_marker_mutation(self, ledger):
        before = (self.project / PENDING_RETRY_FILE).read_bytes()
        with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$"):
            record_retry_transaction_target(self.project, self.plan, ledger)
        self.assertEqual((self.project / PENDING_RETRY_FILE).read_bytes(), before)
        self.assertTrue(abort_retry_transaction(self.project))

    def assert_screening_prerequisite_rejected(self, **screening_changes):
        valid_target = deepcopy(self.ledger)
        self.assertTrue(abort_retry_transaction(self.project))
        before_ledger = load_workflow_state(self.project)
        before_ledger["stages"]["screening"].update(screening_changes)
        save_workflow_state(self.project, before_ledger)
        self.prepared = preparation(self.project)
        begin_retry_transaction(
            self.project, self.prepared, self.staging_name, self.project / self.staging_name)
        self.plan = publication(self.project, self.prepared, self.staging_name)
        target = deepcopy(valid_target)
        target["stages"]["screening"] = deepcopy(before_ledger["stages"]["screening"])
        marker = self.project / PENDING_RETRY_FILE
        marker_before = marker.read_bytes()

        with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$") as caught:
            record_retry_transaction_target(self.project, self.plan, target)

        self.assertNotIn(str(self.project), str(caught.exception))
        self.assertEqual(marker.read_bytes(), marker_before)
        self.assertFalse(reconcile_retry_transaction(self.project))
        self.assertTrue(abort_retry_transaction(self.project))

    def test_recorded_target_is_path_safe_immutable_and_abort_still_restores(self):
        before = self.prepared.snapshot.mutable_fact_copies()
        record_retry_transaction_target(self.project, self.plan, self.ledger)
        marker_path = self.project / PENDING_RETRY_FILE
        raw = marker_path.read_text(); marker = json.loads(raw)
        self.assertEqual(marker["phase"], "abort"); self.assertNotIn(str(self.project), raw)
        self.assertEqual(set(marker), {"version", "phase", "transaction_id", "expected_revision", "selected_ids", "staging_name",
            "candidate_names", "pdf_baseline_json_b64", "before_json_b64", "sources_json_b64", "target_json_b64"})
        target = json.loads(base64.b64decode(marker["target_json_b64"]))
        self.assertEqual(target["pdfs"][0]["source_name"], self.plan.pdfs[0].source_path.name)
        self.ledger["stages"]["retrieval"]["counts"]["succeeded"] = 99
        self.assertEqual(raw, marker_path.read_text())
        self.publish_target()
        self.assertTrue(abort_retry_transaction(self.project))
        self.assertEqual(json.loads((self.project / "pdfs" / "download_report.json").read_text()), before[0])
        self.assertFalse(self.plan.pdfs[0].destination_path.exists())

    def test_target_cas_rejects_same_bytes_new_inode_marker(self):
        marker_path = self.project / PENDING_RETRY_FILE
        before = marker_path.read_bytes()
        old_inode = marker_path.stat().st_ino

        def replace_with_same_bytes(project, marker):
            temporary = marker_path.with_name("same-marker.tmp")
            temporary.write_bytes(before)
            os.replace(temporary, marker_path)

        with patch("reviewpilot_core.retrieval_retry_transaction._validate_committed_source_set",
                   side_effect=replace_with_same_bytes):
            with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$"):
                record_retry_transaction_target(self.project, self.plan, self.ledger)

        self.assertNotEqual(marker_path.stat().st_ino, old_inode)
        self.assertEqual(marker_path.read_bytes(), before)
        self.assertNotIn("target_json_b64", json.loads(marker_path.read_text()))
        self.assertTrue(abort_retry_transaction(self.project))

    def test_target_replace_holds_cross_process_lock_after_generation_check(self):
        resolved_project = self.project.resolve()
        marker = resolved_project / PENDING_RETRY_FILE
        marker_bytes = marker.read_bytes()
        contender = None
        acquired_during_replace = False

        def raced_write(path, data, *args, **kwargs):
            nonlocal contender, acquired_during_replace
            if Path(path) == marker:
                contender, acquired = marker_lock_contender(resolved_project, marker_bytes)
                try:
                    contender.wait(timeout=0.2)
                    acquired_during_replace = acquired.exists()
                except subprocess.TimeoutExpired:
                    acquired_during_replace = False
            return atomic_write_json(path, data, *args, **kwargs)

        with patch("reviewpilot_core.retrieval_retry_transaction.atomic_write_json",
                   side_effect=raced_write):
            record_retry_transaction_target(self.project, self.plan, self.ledger)
        self.assertIsNotNone(contender)
        contender.wait(timeout=2)
        self.assertFalse(acquired_during_replace)
        self.assertEqual(marker.read_bytes(), marker_bytes)
        self.assertTrue(abort_retry_transaction(self.project))

    def test_record_rejects_each_live_authority_drift_without_marker_mutation(self):
        def mutate_report():
            path = self.project / "pdfs" / "download_report.json"
            report = json.loads(path.read_text())
            report["failed_papers"][0]["title"] = "drift-before-record"
            atomic_write_json(path, report)

        def mutate_included():
            path = self.project / "filtered" / "included_papers.jsonl"
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            rows[-1]["drift"] = True
            atomic_write_jsonl(path, rows)

        def mutate_ledger():
            ledger = load_workflow_state(self.project)
            ledger["stages"]["collection"]["counts"]["drift"] = 1
            save_workflow_state(self.project, ledger)

        for index, mutate in enumerate((mutate_report, mutate_included, mutate_ledger)):
            with self.subTest(authority=index):
                marker = self.project / PENDING_RETRY_FILE
                before = marker.read_bytes()
                mutate()

                with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$") as caught:
                    record_retry_transaction_target(self.project, self.plan, self.ledger)

                self.assertNotIn(str(self.project), str(caught.exception))
                self.assertEqual(marker.read_bytes(), before)
                self.assertFalse(reconcile_retry_transaction(self.project))
                self.assertTrue(abort_retry_transaction(self.project))
                if index < 2:
                    begin_retry_transaction(
                        self.project, self.prepared, self.staging_name, self.project / self.staging_name)
                    self.plan = publication(self.project, self.prepared, self.staging_name)
                    self.ledger = target_ledger(self.project, self.plan)

    def test_record_rejects_authority_changed_during_final_ledger_load(self):
        marker = self.project / PENDING_RETRY_FILE
        before = marker.read_bytes()
        report_path = self.project / "pdfs" / "download_report.json"
        real_load = load_workflow_state
        calls = 0

        def mutate_during_final_load(project):
            nonlocal calls
            ledger = real_load(project)
            calls += 1
            if calls == 2:
                report = json.loads(report_path.read_text())
                report["failed_papers"][0]["title"] = "changed-during-final-ledger-load"
                atomic_write_json(report_path, report)
            return ledger

        with patch(
                "reviewpilot_core.retrieval_retry_transaction.load_workflow_state",
                side_effect=mutate_during_final_load):
            with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$") as caught:
                record_retry_transaction_target(self.project, self.plan, self.ledger)

        self.assertEqual(calls, 2)
        self.assertNotIn(str(self.project), str(caught.exception))
        self.assertEqual(marker.read_bytes(), before)
        self.assertFalse(reconcile_retry_transaction(self.project))
        self.assertTrue(abort_retry_transaction(self.project))

    def test_target_record_rejects_earlier_source_changed_while_later_source_is_fingerprinted(self):
        self.assertTrue(abort_retry_transaction(self.project))
        project = Path(self.temp.name).resolve() / "two-source-target"
        project.mkdir(); retryable_project(project)
        included_path = project / "filtered" / "included_papers.jsonl"
        rows = [json.loads(line) for line in included_path.read_text().splitlines()]
        rows.append({"id": "failed-two", "title": "Failed Two"})
        atomic_write_jsonl(included_path, rows)
        report_path = project / "pdfs" / "download_report.json"
        report = json.loads(report_path.read_text()); report["failed"] = 2
        report["failed_papers"].append(
            {"id": "failed-two", "title": "Failed Two", "failure_class": "network"})
        atomic_write_json(report_path, report)
        ledger = load_workflow_state(project); ledger["stages"]["retrieval"]["counts"]["failed"] = 2
        ledger["stages"]["retrieval"]["last_valid"]["counts"]["failed"] = 2
        save_workflow_state(project, ledger)
        prepared = preparation(project); staging = ".retrieval_retry_staging_two_source_target"
        begin_retry_transaction(project, prepared, staging, project / staging)
        plan = publication(project, prepared, staging); target = target_ledger(project, plan)
        marker = project / PENDING_RETRY_FILE; before = marker.read_bytes()
        real_fingerprint = _publication_pdf_fingerprint
        seen = []

        def mutate_after_second(path, parent):
            result = real_fingerprint(path, parent)
            seen.append(path)
            if len(seen) == 4:
                seen[0].write_bytes(b"%PDF-1.7\nchanged-after-first-fingerprint")
            return result

        with patch(
                "reviewpilot_core.retrieval_retry_transaction._publication_pdf_fingerprint",
                side_effect=mutate_after_second):
            with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$") as caught:
                record_retry_transaction_target(project, plan, target)

        self.assertNotIn(str(project), str(caught.exception))
        self.assertGreaterEqual(len(seen), 5)
        self.assertEqual(marker.read_bytes(), before)
        self.assertFalse(reconcile_retry_transaction(project))
        self.assertTrue(abort_retry_transaction(project))

    def test_record_rejects_retry_when_screening_is_not_terminal(self):
        self.assert_screening_prerequisite_rejected(status="ready")

    def test_record_rejects_retry_when_screening_is_stale(self):
        self.assert_screening_prerequisite_rejected(stale=True)

    def test_record_streams_staged_pdf_and_accepts_upstream_whitespace_header(self):
        self.assertTrue(abort_retry_transaction(self.project))
        self.handle = begin_retry_transaction(
            self.project, self.prepared, self.staging_name, self.project / self.staging_name)
        payload = b" \n\t%PDF-1.7\n" + b"x" * (130 * 1024)
        self.plan = publication(
            self.project, self.prepared, self.staging_name, pdf_payload=payload)
        self.ledger = target_ledger(self.project, self.plan)
        source = self.plan.pdfs[0].source_path
        original_read_bytes = Path.read_bytes
        original_os_read = os.read
        source_identity = (source.stat().st_dev, source.stat().st_ino)
        read_sizes = []

        def tracking_read(fd, size):
            stat = os.fstat(fd)
            if (stat.st_dev, stat.st_ino) == source_identity:
                read_sizes.append(size)
            return original_os_read(fd, size)

        def guarded_read_bytes(path):
            if path == source:
                raise AssertionError("record must not call read_bytes for a staged PDF")
            return original_read_bytes(path)

        with patch("reviewpilot_core.retrieval_retry.os.read", tracking_read), \
                patch.object(Path, "read_bytes", guarded_read_bytes):
            record_retry_transaction_target(self.project, self.plan, self.ledger)

        self.assertGreater(len(read_sizes), 2)
        self.assertEqual(set(read_sizes), {64 * 1024})
        self.assertTrue(abort_retry_transaction(self.project))

    def test_record_stream_rejection_preserves_marker_and_abort_recovery(self):
        source = self.plan.pdfs[0].source_path
        source.write_bytes(b"not-a-pdf")
        before = (self.project / PENDING_RETRY_FILE).read_bytes()
        original_read_bytes = Path.read_bytes

        def guarded_read_bytes(path):
            if path == source:
                raise AssertionError("record must not call read_bytes for a staged PDF")
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", guarded_read_bytes):
            with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$"):
                record_retry_transaction_target(self.project, self.plan, self.ledger)

        self.assertEqual((self.project / PENDING_RETRY_FILE).read_bytes(), before)
        self.assertTrue(abort_retry_transaction(self.project))

    def test_record_rejects_ledger_mismatch_and_forged_plan_without_changing_marker(self):
        before = (self.project / PENDING_RETRY_FILE).read_bytes()
        bad = deepcopy(self.ledger); bad["stages"]["retrieval"]["counts"] = {"succeeded": 99, "failed": 0}
        with self.assertRaisesRegex(ValueError, "target is invalid") as caught:
            record_retry_transaction_target(self.project, self.plan, bad)
        self.assertNotIn(str(self.project), str(caught.exception)); self.assertEqual((self.project / PENDING_RETRY_FILE).read_bytes(), before)

    def test_record_rejects_equal_float_merged_counts_without_changing_marker(self):
        merged = self.plan.merged_facts
        float_counts = MappingProxyType({key: float(value) for key, value in merged.counts.items()})
        forged_merged = RetryMergedFacts(
            merged.report_revision, merged.report, merged.included,
            merged.status, float_counts, merged.planned_pdfs,
        )
        forged = RetryPublicationPlan(self.plan.report_revision, forged_merged, self.plan.pdfs)
        before = (self.project / PENDING_RETRY_FILE).read_bytes()

        with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$"):
            record_retry_transaction_target(self.project, forged, self.ledger)

        self.assertEqual((self.project / PENDING_RETRY_FILE).read_bytes(), before)
        self.assertTrue(abort_retry_transaction(self.project))

    def test_record_rejects_equal_float_terminal_counts_without_changing_marker(self):
        locations = (
            ("counts",),
            ("last_valid", "counts"),
        )
        for index, location in enumerate(locations):
            with self.subTest(location=location):
                bad = deepcopy(self.ledger)
                counts = bad["stages"]["retrieval"]
                for key in location:
                    counts = counts[key]
                counts["succeeded"] = float(counts["succeeded"])
                self.assert_ledger_rejected_without_marker_mutation(bad)
                if index + 1 < len(locations):
                    self.prepared = preparation(self.project)
                    begin_retry_transaction(
                        self.project, self.prepared, self.staging_name, self.project / self.staging_name)
                    self.plan = publication(self.project, self.prepared, self.staging_name)
                    self.ledger = target_ledger(self.project, self.plan)

    def test_record_rejects_equal_float_in_unknown_before_ledger_fact(self):
        self.assertTrue(abort_retry_transaction(self.project))
        before_ledger = load_workflow_state(self.project)
        before_ledger["opaque"] = {"nested": {"value": 2}}
        save_workflow_state(self.project, before_ledger)
        self.prepared = preparation(self.project)
        begin_retry_transaction(
            self.project, self.prepared, self.staging_name, self.project / self.staging_name)
        self.plan = publication(self.project, self.prepared, self.staging_name)
        self.ledger = target_ledger(self.project, self.plan)
        bad = deepcopy(self.ledger)
        bad["opaque"]["nested"]["value"] = 2.0

        self.assert_ledger_rejected_without_marker_mutation(bad)

    def test_record_rejects_forged_terminal_ledger_semantics(self):
        cases = {
            "attempt": lambda ledger: ledger["stages"]["retrieval"].update(attempt=999),
            "error": lambda ledger: ledger["stages"]["retrieval"].update(error="forged"),
            "last-valid": lambda ledger: ledger["stages"]["retrieval"]["last_valid"].update(attempt=999),
            "downstream-stale": lambda ledger: ledger["stages"]["extraction"].update(stale=True),
        }
        for index, (label, mutate) in enumerate(cases.items()):
            with self.subTest(label=label):
                bad = deepcopy(self.ledger); mutate(bad)
                self.assert_ledger_rejected_without_marker_mutation(bad)
                if index + 1 < len(cases):
                    self.prepared = preparation(self.project)
                    self.handle = begin_retry_transaction(
                        self.project, self.prepared, self.staging_name, self.project / self.staging_name)
                    self.plan = publication(self.project, self.prepared, self.staging_name)
                    self.ledger = target_ledger(self.project, self.plan)

    def test_record_requires_exact_extraction_promotion_from_not_started(self):
        self.assertTrue(abort_retry_transaction(self.project))
        before = load_workflow_state(self.project)
        before["stages"]["extraction"].update(status="not_started", stale=False)
        save_workflow_state(self.project, before)
        self.prepared = preparation(self.project)
        begin_retry_transaction(self.project, self.prepared, self.staging_name, self.project / self.staging_name)
        self.plan = publication(self.project, self.prepared, self.staging_name)
        self.ledger = target_ledger(self.project, self.plan)
        self.assertEqual(self.ledger["stages"]["extraction"]["status"], "ready")
        bad = deepcopy(self.ledger)
        bad["stages"]["extraction"] = deepcopy(before["stages"]["extraction"])
        self.assert_ledger_rejected_without_marker_mutation(bad)

    def test_record_matches_start_only_downstream_stale_rule_without_last_valid(self):
        self.assertTrue(abort_retry_transaction(self.project))
        report_path = self.project / "pdfs" / "download_report.json"
        report = json.loads(report_path.read_text()); report.update(success=0, downloaded=[])
        atomic_write_json(report_path, report)
        rows_path = self.project / "filtered" / "included_papers.jsonl"
        rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
        rows[0].pop("pdf_path"); atomic_write_jsonl(rows_path, rows)
        before = load_workflow_state(self.project)
        before["stages"]["retrieval"].update(status="failed", error="Action produced no successful outputs (1 failed).",
            counts={"succeeded": 0, "failed": 1}, stale=False, last_valid=None)
        before["stages"]["extraction"].update(status="ready", attempt=1, stale=False, last_valid=None)
        save_workflow_state(self.project, before)
        self.prepared = preparation(self.project)
        begin_retry_transaction(self.project, self.prepared, self.staging_name, self.project / self.staging_name)
        self.plan = publication(self.project, self.prepared, self.staging_name)
        self.ledger = target_ledger(self.project, self.plan)
        self.assertTrue(self.ledger["stages"]["extraction"]["stale"])
        bad = deepcopy(self.ledger); bad["stages"]["extraction"]["stale"] = False
        self.assert_ledger_rejected_without_marker_mutation(bad)

    def test_record_preserves_unknown_top_level_ledger_facts(self):
        self.assertTrue(abort_retry_transaction(self.project))
        before = load_workflow_state(self.project); before["opaque"] = {"kept": [1, "two"]}
        save_workflow_state(self.project, before)
        self.prepared = preparation(self.project)
        begin_retry_transaction(self.project, self.prepared, self.staging_name, self.project / self.staging_name)
        self.plan = publication(self.project, self.prepared, self.staging_name)
        self.ledger = target_ledger(self.project, self.plan)
        record_retry_transaction_target(self.project, self.plan, self.ledger)
        self.assertTrue(abort_retry_transaction(self.project))

        self.prepared = preparation(self.project)
        begin_retry_transaction(self.project, self.prepared, self.staging_name, self.project / self.staging_name)
        self.plan = publication(self.project, self.prepared, self.staging_name)
        self.ledger = target_ledger(self.project, self.plan)
        bad = deepcopy(self.ledger); bad["opaque"] = {"kept": [999]}
        self.assert_ledger_rejected_without_marker_mutation(bad)

    def test_record_rejects_foreign_business_paths_hidden_by_opaque_destination(self):
        report, included = self.plan.merged_facts.mutable_copies()
        destination = str(self.plan.pdfs[0].destination_path)
        foreign = "/tmp/foreign.pdf"
        selected = self.prepared.selected_ids[0]
        row = next(row for row in included if row.get("id") == "failed")
        detail = next(detail for detail in report["downloaded"] if detail.get("id") == "failed")
        row.update(pdf_path=foreign, opaque={"claimed_destination": destination})
        detail.update(path=foreign, pdf_path=foreign, opaque={"claimed_destination": destination})
        self.assertEqual(stable_retry_id(row), selected)
        self.assertEqual(stable_retry_id(detail), selected)
        forged = self.refreeze_plan(report, included)
        before = (self.project / PENDING_RETRY_FILE).read_bytes()

        with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$") as caught:
            record_retry_transaction_target(self.project, forged, self.ledger)

        self.assertNotIn(str(self.project), str(caught.exception))
        self.assertEqual((self.project / PENDING_RETRY_FILE).read_bytes(), before)
        self.assertTrue(abort_retry_transaction(self.project))

    def test_record_rejects_foreign_success_detail_paths_when_included_path_is_canonical(self):
        report, included = self.plan.merged_facts.mutable_copies()
        selected = self.prepared.selected_ids[0]
        destination = str(self.plan.pdfs[0].destination_path)
        row = next(row for row in included if stable_retry_id(row) == selected)
        detail = report["downloaded"][-1]
        self.assertEqual(row["pdf_path"], destination)
        detail.update(path="/tmp/foreign.pdf", pdf_path="/tmp/foreign.pdf")
        forged = self.refreeze_plan(report, included)
        marker = self.project / PENDING_RETRY_FILE
        before = marker.read_bytes()

        with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$") as caught:
            record_retry_transaction_target(self.project, forged, self.ledger)

        self.assertNotIn(str(self.project), str(caught.exception))
        self.assertEqual(marker.read_bytes(), before)
        self.assertFalse(reconcile_retry_transaction(self.project))
        self.assertTrue(abort_retry_transaction(self.project))

    def test_record_rejects_every_noncanonical_merged_fact_delta_without_marker_mutation(self):
        def mutate_unselected(report, included):
            included[0]["opaque"] = {"forged": True}

        def mutate_selected_business(report, included):
            included[1]["title"] = "forged"

        def mutate_prior_success(report, included):
            report["downloaded"][0]["opaque"] = "forged"

        def mutate_unrelated_report(report, included):
            report["opaque"] = {"nested": True}

        def mutate_aggregate_type(report, included):
            report["attempted"] = float(report["attempted"])

        def mutate_classification(report, included):
            report["web_search_fallback_candidates"] = [{"id": "forged"}]

        def mutate_success_identity(report, included):
            report["downloaded"][-1]["id"] = None

        cases = (mutate_unselected, mutate_selected_business, mutate_prior_success,
            mutate_unrelated_report, mutate_aggregate_type, mutate_classification, mutate_success_identity)
        for index, mutate in enumerate(cases):
            with self.subTest(case=mutate.__name__):
                report, included = self.plan.merged_facts.mutable_copies()
                mutate(report, included)
                forged = self.refreeze_plan(report, included)
                before = (self.project / PENDING_RETRY_FILE).read_bytes()
                with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$"):
                    record_retry_transaction_target(self.project, forged, self.ledger)
                self.assertEqual((self.project / PENDING_RETRY_FILE).read_bytes(), before)
                self.assertTrue(abort_retry_transaction(self.project))
                if index + 1 < len(cases):
                    self.prepared = preparation(self.project)
                    begin_retry_transaction(self.project, self.prepared, self.staging_name, self.project / self.staging_name)
                    self.plan = publication(self.project, self.prepared, self.staging_name)
                    self.ledger = target_ledger(self.project, self.plan)

    def test_record_rejects_failure_diagnostics_removed_by_canonical_merge(self):
        row_diagnostics = {
            "pdf_failure_detail": "secondary detail",
            "pdf_failure_classes": ["download_failed"],
            "pdf_error": "secondary error",
        }
        report_diagnostics = {
            "failure_detail": "secondary detail",
            "failure_classes": ["download_failed"],
            "error": "secondary error",
            "pdf_failure_class": "download_failed",
            "pdf_failure_detail": "secondary detail",
            "pdf_failure_classes": ["download_failed"],
            "pdf_error": "secondary error",
        }

        self.assertTrue(abort_retry_transaction(self.project))
        case_index = 0
        for label, failure_count, succeeds in (("all-failure", 1, False), ("partial", 2, 1)):
            for container, diagnostics in (("row", row_diagnostics), ("report", report_diagnostics)):
                for key, value in diagnostics.items():
                    with self.subTest(outcome=label, container=container, key=key):
                        case_index += 1
                        project = Path(self.temp.name).resolve() / f"{label}-{case_index}"
                        project.mkdir(); retryable_project(project)
                        if failure_count == 2:
                            included_path = project / "filtered" / "included_papers.jsonl"
                            included = [json.loads(line) for line in included_path.read_text().splitlines()]
                            included.append({"id": "failed-2", "title": "Failed 2"})
                            atomic_write_jsonl(included_path, included)
                            report_path = project / "pdfs" / "download_report.json"
                            report = json.loads(report_path.read_text()); report["failed"] = 2
                            report["failed_papers"].append(
                                {"id": "failed-2", "title": "Failed 2", "failure_class": "network"})
                            atomic_write_json(report_path, report)
                            current_ledger = load_workflow_state(project)
                            current_ledger["stages"]["retrieval"]["counts"]["failed"] = 2
                            current_ledger["stages"]["retrieval"]["last_valid"]["counts"]["failed"] = 2
                            save_workflow_state(project, current_ledger)
                        prepared = preparation(project)
                        staging = f".retrieval_retry_staging_{label}"
                        begin_retry_transaction(project, prepared, staging, project / staging)
                        plan = publication(project, prepared, staging, succeeds=succeeds)
                        ledger = target_ledger(project, plan)
                        marker = project / PENDING_RETRY_FILE
                        marker_before = marker.read_bytes()
                        report, included = plan.merged_facts.mutable_copies()
                        failed_row = next(row for row in included if row.get("pdf_downloaded") is False)
                        if container == "row":
                            failed_row[key] = deepcopy(value)
                        else:
                            report["failed_papers"][0][key] = deepcopy(value)
                            failures = report["failed_papers"]
                            report["subscribed_papers"] = [
                                row for row in failures if row["retrieval_status"] == "subscribed_unavailable"]
                            report["unavailable_papers"] = [
                                row for row in failures if row["retrieval_status"] == "unavailable"]
                            report["web_search_fallback_candidates"] = deepcopy(failures)
                        forged = self.refreeze_plan(report, included, base_plan=plan)

                        with self.assertRaisesRegex(
                                ValueError, r"^Retry transaction target is invalid$") as caught:
                            record_retry_transaction_target(project, forged, ledger)

                        self.assertNotIn(str(project), str(caught.exception))
                        self.assertEqual(marker.read_bytes(), marker_before)
                        self.assertFalse(reconcile_retry_transaction(project))
                        record_retry_transaction_target(project, plan, ledger)
                        self.assertTrue(abort_retry_transaction(project))

    def test_record_accepts_success_detail_with_all_identity_aliases_omitted(self):
        report, included = self.plan.merged_facts.mutable_copies()
        detail = report["downloaded"][-1]
        for key in ("id", "doi", "url", "title"):
            detail.pop(key, None)
        plan = self.refreeze_plan(report, included)

        record_retry_transaction_target(self.project, plan, self.ledger)

        self.assertTrue(abort_retry_transaction(self.project))

    def test_record_rejects_equal_but_differently_typed_detail_provenance(self):
        cases = (
            ("integer-bool", "rank", 1, True),
            ("integer-float", "score", 1, 1.0),
            ("nested-dict", "metadata", {"rank": 1}, {"rank": True}),
            ("identity", "url", 1, True),
            ("present-identity", "doi", None, ""),
        )
        self.assertTrue(abort_retry_transaction(self.project))
        for index, (label, key, row_value, detail_value) in enumerate(cases):
            with self.subTest(label=label):
                project = Path(self.temp.name).resolve() / f"exact-provenance-{index}"
                project.mkdir(); retryable_project(project)
                rows_path = project / "filtered" / "included_papers.jsonl"
                rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
                next(row for row in rows if row.get("id") == "failed")[key] = row_value
                atomic_write_jsonl(rows_path, rows)
                prepared = preparation(project)
                begin_retry_transaction(
                    project, prepared, self.staging_name, project / self.staging_name)
                plan = publication(project, prepared, self.staging_name)
                ledger = target_ledger(project, plan)
                report, included = plan.merged_facts.mutable_copies()
                report["downloaded"][-1][key] = detail_value
                forged = self.refreeze_plan(report, included, base_plan=plan)
                marker = project / PENDING_RETRY_FILE
                before = marker.read_bytes()

                with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$") as caught:
                    record_retry_transaction_target(project, forged, ledger)

                self.assertNotIn(str(project), str(caught.exception))
                self.assertEqual(marker.read_bytes(), before)
                self.assertFalse(reconcile_retry_transaction(project))
                self.assertTrue(abort_retry_transaction(project))

    def test_detail_provenance_compares_nested_lists_and_dicts_exactly(self):
        row = {"metadata": [{"rank": 1}, {"score": 1}]}
        detail = {"metadata": [{"rank": True}, {"score": 1.0}]}

        with self.assertRaisesRegex(ValueError, "no paper provenance"):
            _validate_detail_provenance(row, detail)

    def test_partial_delta_preserves_unselected_failure_order_and_nested_facts(self):
        self.assertTrue(abort_retry_transaction(self.project))
        rows_path = self.project / "filtered" / "included_papers.jsonl"
        rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
        rows[0]["opaque"] = {"nested": [1, {"typed": True}]}
        rows.extend(({"id": "failed-2", "title": "Failed 2"}, {"id": "failed-3", "title": "Failed 3"}))
        atomic_write_jsonl(rows_path, rows)
        report_path = self.project / "pdfs" / "download_report.json"
        report = json.loads(report_path.read_text())
        report["opaque"] = {"nested": [1, {"typed": True}]}
        report["failed_papers"].extend((
            {"id": "failed-2", "title": "Failed 2", "failure_class": "network"},
            {"id": "failed-3", "title": "Failed 3", "failure_class": "network"},
        ))
        report["failed"] = 3
        atomic_write_json(report_path, report)
        ledger = load_workflow_state(self.project)
        ledger["stages"]["retrieval"]["counts"]["failed"] = 3
        save_workflow_state(self.project, ledger)
        snapshot = current_retry_snapshot(self.project)
        selected = [item.retry_id for item in snapshot.items[:2]]
        prepared = prepare_retry_request(self.project, {"failed_ids": selected,
            "report_revision": snapshot.report_revision,
            "retry_confirmation": {"expected_report_revision": snapshot.report_revision, "failed_ids": selected}})
        begin_retry_transaction(self.project, prepared, self.staging_name, self.project / self.staging_name)
        plan = publication(self.project, prepared, self.staging_name, succeeds=1)
        ledger = target_ledger(self.project, plan)
        before = (self.project / PENDING_RETRY_FILE).read_bytes()

        target_report, target_included = plan.merged_facts.mutable_copies()
        reversed_report = deepcopy(target_report)
        reversed_report["failed_papers"].reverse()
        forged_unselected = deepcopy(target_report)
        forged_unselected["failed_papers"][-1]["title"] = "forged"
        for forged_report in (reversed_report, forged_unselected):
            with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$"):
                record_retry_transaction_target(self.project,
                    self.refreeze_plan(forged_report, target_included, base_plan=plan), ledger)
            self.assertEqual((self.project / PENDING_RETRY_FILE).read_bytes(), before)
        record_retry_transaction_target(self.project, plan, ledger)
        self.assertTrue(abort_retry_transaction(self.project))

    def test_record_rejects_success_facts_when_pdf_subset_is_empty(self):
        report, included = self.plan.merged_facts.mutable_copies()
        forged = self.refreeze_plan(report, included, pdfs=(), planned_pdfs=())
        before = (self.project / PENDING_RETRY_FILE).read_bytes()

        with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$"):
            record_retry_transaction_target(self.project, forged, self.ledger)

        self.assertEqual((self.project / PENDING_RETRY_FILE).read_bytes(), before)
        self.assertTrue(abort_retry_transaction(self.project))

    def test_record_rejects_duplicate_and_out_of_order_success_pdf_ids(self):
        project = Path(self.temp.name).resolve() / "two-failures"
        project.mkdir(); retryable_project(project)
        included_path = project / "filtered" / "included_papers.jsonl"
        rows = [json.loads(line) for line in included_path.read_text().splitlines()]
        rows.append({"id": "failed-2", "title": "Failed 2"}); atomic_write_jsonl(included_path, rows)
        report_path = project / "pdfs" / "download_report.json"
        report = json.loads(report_path.read_text()); report["failed"] = 2
        report["failed_papers"].append({"id": "failed-2", "title": "Failed 2", "failure_class": "network"})
        atomic_write_json(report_path, report)
        ledger = load_workflow_state(project); ledger["stages"]["retrieval"]["counts"]["failed"] = 2
        save_workflow_state(project, ledger)
        prepared = preparation(project); staging_name = ".retrieval_retry_staging_two"
        begin_retry_transaction(project, prepared, staging_name, project / staging_name)
        plan = publication(project, prepared, staging_name); ledger = target_ledger(project, plan)
        before = (project / PENDING_RETRY_FILE).read_bytes()

        duplicate = self.refreeze_plan(*plan.merged_facts.mutable_copies(), base_plan=plan,
            pdfs=(plan.pdfs[0], plan.pdfs[0]), planned_pdfs=(plan.merged_facts.planned_pdfs[0],) * 2)
        reversed_plan = self.refreeze_plan(*plan.merged_facts.mutable_copies(), base_plan=plan,
            pdfs=tuple(reversed(plan.pdfs)), planned_pdfs=tuple(reversed(plan.merged_facts.planned_pdfs)))
        for label, forged in (("duplicate", duplicate), ("out-of-order", reversed_plan)):
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$"):
                    record_retry_transaction_target(project, forged, ledger)
                self.assertEqual((project / PENDING_RETRY_FILE).read_bytes(), before)
        self.assertTrue(abort_retry_transaction(project))

    def test_record_rejects_cross_swapped_committed_sources_without_mutating_marker(self):
        project = Path(self.temp.name).resolve() / "cross-swapped-sources"
        project.mkdir(); retryable_project(project)
        included_path = project / "filtered" / "included_papers.jsonl"
        rows = [json.loads(line) for line in included_path.read_text().splitlines()]
        rows.append({"id": "failed-2", "title": "Failed 2"}); atomic_write_jsonl(included_path, rows)
        report_path = project / "pdfs" / "download_report.json"
        report = json.loads(report_path.read_text()); report["failed"] = 2
        report["failed_papers"].append({"id": "failed-2", "title": "Failed 2", "failure_class": "network"})
        atomic_write_json(report_path, report)
        ledger = load_workflow_state(project); ledger["stages"]["retrieval"]["counts"]["failed"] = 2
        save_workflow_state(project, ledger)
        prepared = preparation(project); staging = ".retrieval_retry_staging_cross_swap"
        begin_retry_transaction(project, prepared, staging, project / staging)
        plan = publication(project, prepared, staging); ledger = target_ledger(project, plan)
        first, second = plan.pdfs; first_planned, second_planned = plan.merged_facts.planned_pdfs
        forged_pdfs = (
            RetryPublicationPdf(first.retry_id, second.source_path, first.destination_path,
                second.source_size, second.source_sha256),
            RetryPublicationPdf(second.retry_id, first.source_path, second.destination_path,
                first.source_size, first.source_sha256),
        )
        forged_planned = (
            RetryPlannedPdf(first_planned.retry_id, second_planned.source_path, first_planned.destination_path),
            RetryPlannedPdf(second_planned.retry_id, first_planned.source_path, second_planned.destination_path),
        )
        forged = self.refreeze_plan(*plan.merged_facts.mutable_copies(), base_plan=plan,
            pdfs=forged_pdfs, planned_pdfs=forged_planned)
        marker = project / PENDING_RETRY_FILE; before = marker.read_bytes()

        with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$"):
            record_retry_transaction_target(project, forged, ledger)

        self.assertEqual(marker.read_bytes(), before)
        temporary = first.source_path.with_suffix(".swap")
        first.source_path.replace(temporary); second.source_path.replace(first.source_path); temporary.replace(second.source_path)
        synchronously_swapped = self.refreeze_plan(*plan.merged_facts.mutable_copies(), base_plan=plan,
            pdfs=(
                RetryPublicationPdf(first.retry_id, second.source_path, first.destination_path,
                    first.source_size, first.source_sha256),
                RetryPublicationPdf(second.retry_id, first.source_path, second.destination_path,
                    second.source_size, second.source_sha256),
            ), planned_pdfs=forged_planned)
        with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$"):
            record_retry_transaction_target(project, synchronously_swapped, ledger)
        self.assertEqual(marker.read_bytes(), before)

        first.source_path.replace(temporary); second.source_path.replace(first.source_path); temporary.replace(second.source_path)
        record_retry_transaction_target(project, plan, ledger)
        raw = json.loads(marker.read_text()); target = json.loads(base64.b64decode(raw["target_json_b64"]))
        target["pdfs"].reverse(); raw["target_json_b64"] = base64.b64encode(
            json.dumps(target, sort_keys=True, separators=(",", ":")).encode()).decode()
        atomic_write_json(marker, raw); abandon_retry_transaction(project)
        with self.assertRaisesRegex(ValueError, r"^Pending retry transaction cannot be recovered safely$"):
            reconcile_retry_transaction(project)

    def test_record_requires_committed_sources_but_legacy_marker_remains_abortable(self):
        marker_path = self.project / PENDING_RETRY_FILE
        marker = json.loads(marker_path.read_text()); marker.pop("sources_json_b64")
        atomic_write_json(marker_path, marker); before = marker_path.read_bytes()

        with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$"):
            record_retry_transaction_target(self.project, self.plan, self.ledger)

        self.assertEqual(marker_path.read_bytes(), before)
        self.assertTrue(abort_retry_transaction(self.project))

    def test_target_source_schema_is_strict_and_recovery_does_not_read_staging_files(self):
        record_retry_transaction_target(self.project, self.plan, self.ledger)
        marker_path = self.project / PENDING_RETRY_FILE; valid = marker_path.read_bytes()
        for mutate in (
                lambda pdf: pdf.update(source_name="../foreign.pdf"),
                lambda pdf: pdf.pop("source_name")):
            raw = json.loads(valid); target = json.loads(base64.b64decode(raw["target_json_b64"]))
            mutate(target["pdfs"][0]); raw["target_json_b64"] = base64.b64encode(
                json.dumps(target, sort_keys=True, separators=(",", ":")).encode()).decode()
            atomic_write_json(marker_path, raw); abandon_retry_transaction(self.project)
            with self.assertRaisesRegex(ValueError, r"^Pending retry transaction cannot be recovered safely$"):
                reconcile_retry_transaction(self.project)
        marker_path.write_bytes(valid)
        shutil.rmtree(self.project / self.staging_name)
        self.assertTrue(abort_retry_transaction(self.project))

    def test_recovery_rejects_non_integer_committed_source_size(self):
        record_retry_transaction_target(self.project, self.plan, self.ledger)
        marker_path = self.project / PENDING_RETRY_FILE
        valid = json.loads(marker_path.read_text())
        committed_size = json.loads(base64.b64decode(valid["target_json_b64"]))["pdfs"][0]["size"]

        for forged_size in (float(committed_size), True):
            with self.subTest(forged_size=forged_size):
                raw = deepcopy(valid)
                target = json.loads(base64.b64decode(raw["target_json_b64"]))
                target["pdfs"][0]["size"] = forged_size
                raw["target_json_b64"] = base64.b64encode(
                    json.dumps(target, sort_keys=True, separators=(",", ":")).encode()).decode()
                atomic_write_json(marker_path, raw)
                abandon_retry_transaction(self.project)

                with self.assertRaisesRegex(
                        ValueError, r"^Pending retry transaction cannot be recovered safely$"):
                    reconcile_retry_transaction(self.project)

    def test_record_rejects_distinct_retry_ids_that_share_one_staged_pdf_source(self):
        project = Path(self.temp.name).resolve() / "shared-source"
        project.mkdir(); retryable_project(project)
        included_path = project / "filtered" / "included_papers.jsonl"
        rows = [json.loads(line) for line in included_path.read_text().splitlines()]
        rows.append({"id": "failed-2", "title": "Failed 2"}); atomic_write_jsonl(included_path, rows)
        report_path = project / "pdfs" / "download_report.json"
        report = json.loads(report_path.read_text()); report["failed"] = 2
        report["failed_papers"].append({"id": "failed-2", "title": "Failed 2", "failure_class": "network"})
        atomic_write_json(report_path, report)
        ledger = load_workflow_state(project); ledger["stages"]["retrieval"]["counts"]["failed"] = 2
        save_workflow_state(project, ledger)
        prepared = preparation(project); staging_name = ".retrieval_retry_staging_shared"
        begin_retry_transaction(project, prepared, staging_name, project / staging_name)
        plan = publication(project, prepared, staging_name); ledger = target_ledger(project, plan)
        first, second = plan.pdfs
        shared_second = RetryPublicationPdf(
            second.retry_id, first.source_path, second.destination_path,
            first.source_size, first.source_sha256)
        first_planned, second_planned = plan.merged_facts.planned_pdfs
        shared_second_planned = RetryPlannedPdf(
            second_planned.retry_id, first_planned.source_path, second_planned.destination_path)
        forged = self.refreeze_plan(
            *plan.merged_facts.mutable_copies(), base_plan=plan,
            pdfs=(first, shared_second), planned_pdfs=(first_planned, shared_second_planned))
        marker = project / PENDING_RETRY_FILE
        before = marker.read_bytes()

        with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$") as caught:
            record_retry_transaction_target(project, forged, ledger)

        self.assertNotIn(str(project), str(caught.exception))
        self.assertEqual(marker.read_bytes(), before)
        record_retry_transaction_target(project, plan, ledger)
        self.assertTrue(abort_retry_transaction(project))

    def test_record_rejects_every_target_that_recovery_cannot_decode_before_writing_marker(self):
        cases = (
            ("success-count", lambda report: report.update(success=999)),
            ("failed-count", lambda report: report.update(failed=1)),
            ("downloaded-type", lambda report: report.update(downloaded=report["downloaded"][-1])),
            ("failed-papers-type", lambda report: report.update(failed_papers={})),
        )
        for index, (label, mutate) in enumerate(cases):
            with self.subTest(label=label):
                plan, ledger = self.forged_target(mutate)
                self.assertIs(type(plan), RetryPublicationPlan)
                self.assertIs(type(plan.merged_facts), RetryMergedFacts)
                before = (self.project / PENDING_RETRY_FILE).read_bytes()
                with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$") as caught:
                    record_retry_transaction_target(self.project, plan, ledger)
                self.assertNotIn(str(self.project), str(caught.exception))
                self.assertEqual((self.project / PENDING_RETRY_FILE).read_bytes(), before)
                self.assertTrue(abort_retry_transaction(self.project))
                if index + 1 < len(cases):
                    self.handle = begin_retry_transaction(
                        self.project, self.prepared, self.staging_name, self.project / self.staging_name)
                    self.plan = publication(self.project, self.prepared, self.staging_name)
                    self.ledger = target_ledger(self.project, self.plan)

    def test_all_failure_target_records_an_empty_pdf_set_and_remains_abortable(self):
        self.assertTrue(abort_retry_transaction(self.project))
        self.handle = begin_retry_transaction(self.project, self.prepared, self.staging_name, self.project / self.staging_name)
        plan = publication(self.project, self.prepared, self.staging_name, succeeds=False)
        ledger = target_ledger(self.project, plan)
        before = (self.project / PENDING_RETRY_FILE).read_bytes()
        bad = deepcopy(ledger); bad["stages"]["retrieval"]["last_valid"] = None
        with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$"):
            record_retry_transaction_target(self.project, plan, bad)
        self.assertEqual((self.project / PENDING_RETRY_FILE).read_bytes(), before)
        record_retry_transaction_target(self.project, plan, ledger)
        marker = json.loads((self.project / PENDING_RETRY_FILE).read_text())
        target = json.loads(base64.b64decode(marker["target_json_b64"]))
        self.assertEqual(target["pdfs"], [])
        self.assertTrue(abort_retry_transaction(self.project))

    def test_partial_target_uses_real_workflow_terminal_ledger(self):
        self.assertTrue(abort_retry_transaction(self.project))
        project = Path(self.temp.name).resolve() / "partial"; project.mkdir(); retryable_project(project)
        rows_path = project / "filtered" / "included_papers.jsonl"
        rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
        rows.append({"id": "failed-2", "title": "Failed 2"}); atomic_write_jsonl(rows_path, rows)
        report_path = project / "pdfs" / "download_report.json"
        report = json.loads(report_path.read_text()); report["failed"] = 2
        report["failed_papers"].append({"id": "failed-2", "title": "Failed 2", "failure_class": "network"})
        atomic_write_json(report_path, report)
        ledger = load_workflow_state(project); ledger["stages"]["retrieval"]["counts"]["failed"] = 2
        save_workflow_state(project, ledger)
        prepared = preparation(project); staging = ".retrieval_retry_staging_partial"
        begin_retry_transaction(project, prepared, staging, project / staging)
        plan = publication(project, prepared, staging, succeeds=1)
        ledger = target_ledger(project, plan)
        self.assertEqual((plan.merged_facts.status, ledger["stages"]["retrieval"]["status"]), ("partial", "partial"))
        before = (project / PENDING_RETRY_FILE).read_bytes()
        bad = deepcopy(ledger); bad["stages"]["retrieval"]["attempt"] += 1
        with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$"):
            record_retry_transaction_target(project, plan, bad)
        self.assertEqual((project / PENDING_RETRY_FILE).read_bytes(), before)
        record_retry_transaction_target(project, plan, ledger)
        self.assertTrue(abort_retry_transaction(project))


class RetryPdfPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.project = Path(self.temp.name) / "project"
        self.project.mkdir(); retryable_project(self.project); self.prepared = preparation(self.project)
        self.staging = ".retrieval_retry_staging_publish"; begin_retry_transaction(
            self.project, self.prepared, self.staging, self.project / self.staging)
        self.plan = publication(self.project, self.prepared, self.staging)
        record_retry_transaction_target(self.project, self.plan, target_ledger(self.project, self.plan))

    def tearDown(self):
        abandon_retry_transaction(self.project); self.temp.cleanup()

    def authority_bytes(self):
        return tuple((self.project / name).read_bytes() for name in (
            "pdfs/download_report.json", "filtered/included_papers.jsonl", "workflow_state.json"))

    def test_publish_refuses_destination_collision_and_leaves_abort_marker(self):
        destination = self.plan.pdfs[0].destination_path
        destination.write_bytes(b"%PDF-foreign")

        with self.assertRaisesRegex(ValueError, r"^Retry transaction PDFs could not be published$"):
            publish_retry_transaction_pdfs(self.project)

        self.assertEqual(destination.read_bytes(), b"%PDF-foreign")
        self.assertEqual(json.loads((self.project / PENDING_RETRY_FILE).read_text())["phase"], "abort")

    def test_interrupted_partial_destination_is_abort_cleanable(self):
        original_write = os.write
        wrote = False

        def interrupt(descriptor, payload):
            nonlocal wrote
            if not wrote:
                wrote = True; original_write(descriptor, payload[:5]); raise OSError("crash")
            return original_write(descriptor, payload)

        with patch("reviewpilot_core.retrieval_retry_transaction.os.write", side_effect=interrupt):
            with self.assertRaises(ValueError): publish_retry_transaction_pdfs(self.project)
        destination = self.plan.pdfs[0].destination_path
        self.assertTrue(destination.exists()); self.assertNotEqual(destination.read_bytes(), self.plan.pdfs[0].source_path.read_bytes())
        self.assertTrue(abort_retry_transaction(self.project)); self.assertFalse(destination.exists())

    def test_publish_rejects_source_mutation(self):
        source = self.plan.pdfs[0].source_path
        source.write_bytes(source.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, r"^Retry transaction PDFs could not be published$"):
            publish_retry_transaction_pdfs(self.project)
        self.assertFalse(self.plan.pdfs[0].destination_path.exists())

    def test_publish_is_idempotent_after_complete_file_and_does_not_mutate_authorities(self):
        before = self.authority_bytes()
        publish_retry_transaction_pdfs(self.project)
        destination = self.plan.pdfs[0].destination_path; first = destination.read_bytes()
        publish_retry_transaction_pdfs(self.project)
        self.assertEqual(destination.read_bytes(), first)
        self.assertEqual(self.authority_bytes(), before)
        marker = json.loads((self.project / PENDING_RETRY_FILE).read_text())
        self.assertEqual(marker["phase"], "abort"); self.assertIn("target_json_b64", marker)

    def test_multi_pdf_retry_accepts_first_complete_target_and_publishes_second(self):
        self.assertTrue(abort_retry_transaction(self.project))
        included = json.loads((self.project / "filtered/included_papers.jsonl").read_text().splitlines()[1])
        included_rows = [json.loads(line) for line in (self.project / "filtered/included_papers.jsonl").read_text().splitlines()]
        included_rows.append({**included, "id": "failed-2", "title": "Failed 2"})
        atomic_write_jsonl(self.project / "filtered/included_papers.jsonl", included_rows)
        report = json.loads((self.project / "pdfs/download_report.json").read_text()); report["failed"] = 2
        report["failed_papers"].append({"id": "failed-2", "title": "Failed 2", "failure_class": "network"})
        atomic_write_json(self.project / "pdfs/download_report.json", report)
        ledger = load_workflow_state(self.project); ledger["stages"]["retrieval"]["counts"]["failed"] = 2
        save_workflow_state(self.project, ledger)
        self.prepared = preparation(self.project); begin_retry_transaction(
            self.project, self.prepared, self.staging, self.project / self.staging)
        self.plan = publication(self.project, self.prepared, self.staging)
        record_retry_transaction_target(self.project, self.plan, target_ledger(self.project, self.plan))
        original = __import__("reviewpilot_core.retrieval_retry_transaction", fromlist=["_publish_one_pdf"])._publish_one_pdf
        calls = 0

        def stop_after_first(*args):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError("crash")
            return original(*args)

        with patch("reviewpilot_core.retrieval_retry_transaction._publish_one_pdf", side_effect=stop_after_first):
            with self.assertRaises(ValueError): publish_retry_transaction_pdfs(self.project)
        self.assertTrue(self.plan.pdfs[0].destination_path.exists()); self.assertFalse(self.plan.pdfs[1].destination_path.exists())
        publish_retry_transaction_pdfs(self.project)
        self.assertTrue(all(pdf.destination_path.exists() for pdf in self.plan.pdfs))
        self.plan.pdfs[0].destination_path.unlink()
        with self.assertRaisesRegex(ValueError, r"^Retry transaction PDFs could not be published$"):
            publish_retry_transaction_pdfs(self.project)


if __name__ == "__main__": unittest.main()
