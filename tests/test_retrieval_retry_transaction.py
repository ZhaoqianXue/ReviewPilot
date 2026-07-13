import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reviewpilot_core.atomic_files import atomic_write_json, atomic_write_jsonl
from reviewpilot_core.retrieval_retry import current_retry_snapshot, prepare_retry_request
from reviewpilot_core.retrieval_retry_transaction import (
    PENDING_RETRY_FILE,
    abandon_retry_transaction,
    abort_retry_transaction,
    begin_retry_transaction,
    reconcile_retry_transaction,
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
        self.assertEqual((marker["version"], marker["phase"]), (1, "abort"))
        self.assertNotIn(str(self.project), json.dumps(marker))
        self.assertEqual(marker["candidate_names"], [f"retry-{self.preparation.snapshot.report_revision}-{self.preparation.selected_ids[0]}.pdf"])
        self.preparation.snapshot.mutable_fact_copies()[0]["success"] = 999
        self.assertEqual(marker, json.loads(handle.marker_path.read_text()))
        self.assertEqual(original[2], self.preparation.snapshot.mutable_fact_copies()[2])

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

    def test_live_reconcile_skips_but_abandon_allows_restart_recovery(self):
        begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        self.assertFalse(reconcile_retry_transaction(self.project))
        abandon_retry_transaction(self.project)
        self.assertTrue(reconcile_retry_transaction(self.project)); self.assertFalse(reconcile_retry_transaction(self.project))

    def test_partial_restore_failure_leaves_marker_and_second_reconcile_finishes(self):
        begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        abandon_retry_transaction(self.project)
        atomic_write_json(self.project / "pdfs" / "download_report.json", {"mutated": True})
        with patch("reviewpilot_core.retrieval_retry_transaction.atomic_write_jsonl", side_effect=OSError(str(self.project))):
            with self.assertRaises(ValueError) as caught: reconcile_retry_transaction(self.project)
        self.assertNotIn(str(self.project), str(caught.exception)); self.assertTrue((self.project / PENDING_RETRY_FILE).exists())
        self.assertTrue(reconcile_retry_transaction(self.project))

    def test_malformed_or_symlink_marker_and_unsafe_staging_fail_closed(self):
        marker = self.project / PENDING_RETRY_FILE
        marker.write_text("{}")
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)
        marker.unlink(); marker.symlink_to(self.project / "workflow_state.json")
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)
        marker.unlink(); begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        abandon_retry_transaction(self.project); (self.project / self.staging_name).symlink_to(self.project / "filtered")
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)


if __name__ == "__main__": unittest.main()
