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

    def test_stable_identity_precedence_is_id_then_doi_then_url_then_title(self):
        row = {"id": "I", "doi": "D", "url": "U", "title": "T"}
        for discarded in ((), ("id",), ("id", "doi"), ("id", "doi", "url")):
            candidate = {key: value for key, value in row.items() if key not in discarded}
            expected_field = next(key for key in ("id", "doi", "url", "title") if key in candidate)
            self.assertEqual(stable_retry_id(candidate), stable_retry_id({expected_field: candidate[expected_field]}))

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
            fresh = current_retry_snapshot(project)
        self.assertEqual(snapshot.items[0].report_index, 0)
        self.assertEqual(snapshot.items[0].included_index, 1)
        self.assertEqual(fresh.report["failed_papers"][0]["title"], "Failed paper")
        self.assertEqual(fresh.included[1]["title"], "Failed paper")

    def test_snapshot_authoritative_facts_are_recursively_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp); partial_project(project)
            snapshot = current_retry_snapshot(project)
            operations = (
                lambda: snapshot.report.__setitem__("failed", 2),
                lambda: snapshot.report["failed_papers"][0].__setitem__("id", "changed"),
                lambda: snapshot.report["failed_papers"].append({"id": "extra"}),
                lambda: snapshot.report.__delitem__("success"),
                lambda: snapshot.included[0].__setitem__("id", "changed"),
                lambda: snapshot.included.append({"id": "extra"}),
                lambda: snapshot.included.__delitem__(0),
                lambda: snapshot.ledger["counts"].__setitem__("failed", 9),
                lambda: snapshot.ledger.__delitem__("status"),
            )
            for operation in operations:
                with self.subTest(operation=operation), self.assertRaises((AttributeError, TypeError)):
                    operation()

    def test_mutable_fact_copies_are_plain_isolated_deep_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp); partial_project(project)
            snapshot = current_retry_snapshot(project)
            first_report, first_included, first_ledger = snapshot.mutable_fact_copies()
            second_report, second_included, second_ledger = snapshot.mutable_fact_copies()
            first_report["failed_papers"][0]["id"] = "changed"
            first_report["failed_papers"].append({"id": "extra"})
            del first_report["downloaded"]
            first_included[0]["id"] = "changed"
            first_included.append({"id": "extra"})
            del first_included[1]
            first_ledger["counts"]["failed"] = 9
            del first_ledger["error"]
        self.assertEqual(second_report["failed_papers"], [{"id": "failed-1", "title": "Failed paper", "failure_class": "paywall"}])
        self.assertEqual(len(second_included), 2); self.assertEqual(second_ledger["counts"]["failed"], 1)
        self.assertEqual(snapshot.report["failed_papers"][0]["id"], "failed-1")
        self.assertEqual(snapshot.included[0]["id"], "ok-1"); self.assertEqual(snapshot.ledger["counts"]["failed"], 1)

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

    def test_rejects_invalid_report_counts(self):
        for value in (True, "1", 1.0, -1):
            for key in ("success", "failed"):
                with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as tmp:
                    project = Path(tmp); partial_project(project)
                    report = json.loads((project / "pdfs" / "download_report.json").read_text()); report[key] = value
                    write_json(project / "pdfs" / "download_report.json", report)
                    with self.assertRaises(ValueError): current_retry_snapshot(project)

    def test_ledger_classification_must_exactly_match_report(self):
        cases = [
            (0, 1, "partial", None, False),
            (1, 1, "failed", "Action produced no successful outputs (1 failed).", False),
            (0, 1, "failed", "Action produced no successful outputs (1 failed). forged", False),
            (1, 1, "partial", None, True),
            (0, 1, "failed", "Action produced no successful outputs (1 failed).", True),
        ]
        for success, failed, status, error, accepted in cases:
            with self.subTest(success=success, status=status, error=error), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp); partial_project(project)
                write_json(project / "pdfs" / "download_report.json", {"success": success, "failed": failed, "downloaded": [{"id": "ok-1"}][:success], "failed_papers": [{"id": "failed-1"}]})
                state = load_workflow_state(project); stage = state["stages"]["retrieval"]
                stage.update(status=status, error=error, counts={"succeeded": success, "failed": failed}, stale=False)
                write_json(project / "workflow_state.json", state)
                if accepted: self.assertEqual(current_retry_snapshot(project).ledger["status"], status)
                else:
                    with self.assertRaises(ValueError): current_retry_snapshot(project)

    def test_rejects_symlinks_at_every_authoritative_boundary(self):
        boundaries = ("project", "ledger", "pdfs", "report", "filtered", "included")
        for boundary in boundaries:
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp); project = base / "project"; project.mkdir(); partial_project(project)
                external = base / "external"; external.mkdir()
                if boundary == "project":
                    link = base / "linked-project"; link.symlink_to(project, target_is_directory=True); target = link
                else:
                    target = project
                    paths = {"ledger": project / "workflow_state.json", "pdfs": project / "pdfs", "report": project / "pdfs" / "download_report.json", "filtered": project / "filtered", "included": project / "filtered" / "included_papers.jsonl"}
                    path = paths[boundary]
                    if path.is_dir():
                        replacement = external / boundary; replacement.mkdir();
                        for child in path.iterdir(): (replacement / child.name).write_bytes(child.read_bytes())
                        for child in path.iterdir(): child.unlink()
                        path.rmdir(); path.symlink_to(replacement, target_is_directory=True)
                    else:
                        replacement = external / path.name; replacement.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(replacement)
                with self.assertRaisesRegex(ValueError, "^Authoritative retry facts are unavailable$"):
                    current_retry_snapshot(target)

    def test_safe_display_candidates_fall_through_before_generic(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            partial_project(project, failed_row={"id": "failed-1", "title": "/Users/private/paper.pdf", "failure_class": "C:\\private\\error", "error": "paywall"})
            item = current_retry_snapshot(project).items[0]
        self.assertEqual(item.label, "failed-1"); self.assertEqual(item.failure_class, "paywall")

    def test_snapshot_ledger_is_detached_from_disk_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp); partial_project(project)
            snapshot = current_retry_snapshot(project)
            with self.assertRaises(TypeError): snapshot.ledger["status"] = "completed"
            self.assertEqual(current_retry_snapshot(project).ledger["status"], "partial")


if __name__ == "__main__":
    unittest.main()
