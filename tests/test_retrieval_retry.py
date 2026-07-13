import json
import tempfile
import unittest
from pathlib import Path

from reviewpilot_core.retrieval_retry import (
    current_retry_snapshot,
    retrieval_report_revision,
    stable_retry_id,
)
from reviewpilot_core.workflow_state import complete_action, initialize_workflow_state, load_workflow_state, start_action


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def partial_project(project: Path, *, failed_row=None, included=None) -> None:
    failed_row = failed_row or {"id": "failed-1", "title": "Failed paper", "failure_class": "paywall"}
    included = included or [{"id": "ok-1", "title": "Downloaded"}, {"id": "failed-1", "title": "Failed paper"}]
    write_jsonl(project / "filtered" / "included_papers.jsonl", included)
    write_json(project / "pdfs" / "download_report.json", {
        "success": 1,
        "failed": 1,
        "downloaded": [{"id": "ok-1", "title": "Downloaded"}],
        "failed_papers": [failed_row],
    })
    initialize_workflow_state(project)
    start_action(project, "collect")
    complete_action(project, "collect", {"total": 0, "platform_stats": {"pubmed": 0}, "platform_errors": {}})
    start_action(project, "screen")
    complete_action(project, "screen", {})
    start_action(project, "download-pdfs")
    complete_action(project, "download-pdfs", {"success": 1, "failed": 1})


class RetrievalRetryTests(unittest.TestCase):
    def test_stable_identity_is_normalized_opaque_and_uses_frozen_precedence(self):
        identifier = stable_retry_id({"id": "  AbC  ", "doi": "10.1/OTHER", "title": "Visible"})
        self.assertEqual(identifier, stable_retry_id({"id": "abc"}))
        self.assertEqual(len(identifier), 64)
        self.assertNotIn("abc", identifier)
        self.assertNotEqual(identifier, stable_retry_id({"doi": "abc"}))
        with self.assertRaises(ValueError):
            stable_retry_id({"id": "", "doi": 5})
        with self.assertRaises(ValueError):
            stable_retry_id([])

    def test_report_revision_is_canonical_and_changes_with_report_or_stage_identity(self):
        report = {"failed": 1, "success": 0, "downloaded": [], "failed_papers": [{"id": "a"}]}
        stage = {"attempt": 2, "status": "failed", "counts": {"succeeded": 0, "failed": 1}}
        revision = retrieval_report_revision(report, stage)
        self.assertEqual(revision, retrieval_report_revision(dict(reversed(list(report.items()))), {"status": "failed", "attempt": 2}))
        self.assertNotEqual(revision, retrieval_report_revision({**report, "note": "changed"}, stage))
        self.assertNotEqual(revision, retrieval_report_revision(report, {"attempt": 3, "status": "failed"}))
        self.assertNotEqual(revision, retrieval_report_revision(report, {"attempt": 2, "status": "partial"}))

    def test_valid_partial_snapshot_maps_failed_item_and_is_detached_from_disk_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            partial_project(project)
            snapshot = current_retry_snapshot(project)
            snapshot.report["failed_papers"][0]["title"] = "mutated"
            snapshot.included[1]["title"] = "mutated"
            fresh = current_retry_snapshot(project)
        self.assertEqual(snapshot.items[0].report_index, 0)
        self.assertEqual(snapshot.items[0].included_index, 1)
        self.assertEqual(fresh.report["failed_papers"][0]["title"], "Failed paper")
        self.assertEqual(fresh.included[1]["title"], "Failed paper")

    def test_structured_failed_snapshot_is_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            partial_project(project)
            report = {"success": 0, "failed": 1, "downloaded": [], "failed_papers": [{"id": "failed-1", "title": "Failed"}]}
            write_json(project / "pdfs" / "download_report.json", report)
            state = load_workflow_state(project)
            stage = state["stages"]["retrieval"]
            stage.update(status="failed", counts={"succeeded": 0, "failed": 1}, error="Action produced no successful outputs (1 failed).", stale=False)
            write_json(project / "workflow_state.json", state)
            snapshot = current_retry_snapshot(project)
        self.assertEqual(snapshot.ledger["status"], "failed")

    def test_rejects_noncurrent_or_inconsistent_facts(self):
        mutations = {
            "completed": lambda p, r, s: s.update(status="completed"),
            "stale": lambda p, r, s: s.update(stale=True),
            "no failure": lambda p, r, s: (r.update(failed=0, failed_papers=[]), s["counts"].update(failed=0)),
            "ledger conflict": lambda p, r, s: s["counts"].update(failed=2),
            "detail conflict": lambda p, r, s: r.update(failed=2),
            "missing included mapping": lambda p, r, s: write_jsonl(p / "filtered" / "included_papers.jsonl", [{"id": "ok-1"}]),
            "duplicate included identity": lambda p, r, s: write_jsonl(p / "filtered" / "included_papers.jsonl", [{"id": "failed-1"}, {"id": "FAILED-1"}]),
            "malformed failed row": lambda p, r, s: r.update(failed_papers=["bad"]),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp)
                partial_project(project)
                report = json.loads((project / "pdfs" / "download_report.json").read_text())
                state = load_workflow_state(project)
                stage = state["stages"]["retrieval"]
                mutate(project, report, stage)
                write_json(project / "pdfs" / "download_report.json", report)
                write_json(project / "workflow_state.json", state)
                with self.assertRaises(ValueError):
                    current_retry_snapshot(project)

    def test_rejects_duplicate_failed_ids_and_malformed_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            partial_project(project)
            report = json.loads((project / "pdfs" / "download_report.json").read_text())
            report.update(failed=2, failed_papers=[{"id": "failed-1"}, {"id": "FAILED-1"}])
            write_json(project / "pdfs" / "download_report.json", report)
            state = load_workflow_state(project); state["stages"]["retrieval"]["counts"]["failed"] = 2
            write_json(project / "workflow_state.json", state)
            with self.assertRaises(ValueError):
                current_retry_snapshot(project)
            (project / "filtered" / "included_papers.jsonl").write_text('{"id":"ok"}\nnot-json\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                current_retry_snapshot(project)


if __name__ == "__main__":
    unittest.main()
