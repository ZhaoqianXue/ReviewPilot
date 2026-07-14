import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from starlette.testclient import TestClient

import web_app
from reviewpilot_core.atomic_files import atomic_write_json, atomic_write_jsonl
from reviewpilot_core.retrieval_retry import current_retry_snapshot
from reviewpilot_core.task_runner import TaskRunner
from reviewpilot_core.workflow_state import complete_action, initialize_workflow_state, load_workflow_state, start_action


def retryable_project(project: Path) -> None:
    (project / "pdfs").mkdir(parents=True)
    original = project / "pdfs/original.pdf"
    original.write_bytes(b"%PDF-original")
    atomic_write_json(project / "search_conditions.json", {"project_name": "demo", "platforms": ["pubmed"]})
    atomic_write_jsonl(project / "filtered/included_papers.jsonl", [
        {"id": "ok", "title": "Existing", "pdf_path": str(original), "pdf_downloaded": True},
        {"id": "failed", "title": "Retry me", "pdf_downloaded": False},
    ])
    atomic_write_json(project / "pdfs/download_report.json", {
        "success": 1,
        "failed": 1,
        "downloaded": [{"id": "ok", "title": "Existing", "path": str(original)}],
        "failed_papers": [{"id": "failed", "title": "Retry me", "failure_class": "network"}],
    })
    initialize_workflow_state(project)
    start_action(project, "collect")
    complete_action(project, "collect", {"total": 0, "platform_stats": {"pubmed": 0}, "platform_errors": {}})
    start_action(project, "screen")
    complete_action(project, "screen", {})
    start_action(project, "download-pdfs")
    complete_action(project, "download-pdfs", {"success": 1, "failed": 1})


class SuccessfulRetryAdapter:
    def run(self, action, output_root, project_id, llm_query=None, input_data=None):
        if action != "download-pdfs" or project_id != "retry":
            raise AssertionError("Unexpected retry adapter call")
        staged = Path(output_root) / project_id
        rows = [json.loads(line) for line in (staged / "filtered/included_papers.jsonl").read_text().splitlines()]
        pdf = staged / "pdfs/recovered.pdf"
        pdf.write_bytes(b"%PDF-recovered")
        rows[0].update(pdf_downloaded=True, pdf_path=str(pdf), retrieval_status="downloaded")
        atomic_write_jsonl(staged / "filtered/included_papers.jsonl", rows)
        atomic_write_json(staged / "pdfs/download_report.json", {
            "success": 1,
            "failed": 0,
            "downloaded": [{"id": rows[0]["id"], "title": rows[0]["title"], "path": str(pdf)}],
            "failed_papers": [],
            "pdf_count": 1,
            "attempted": 1,
        })
        return {"success": 1, "failed": 0, "stats": {"success": 1, "failed": 0}}


class UnsuccessfulRetryAdapter:
    def run(self, action, output_root, project_id, llm_query=None, input_data=None):
        staged = Path(output_root) / project_id
        rows = [json.loads(line) for line in (staged / "filtered/included_papers.jsonl").read_text().splitlines()]
        rows[0].update(
            pdf_downloaded=False,
            retrieval_status="unavailable",
            pdf_failure_class="network",
        )
        atomic_write_jsonl(staged / "filtered/included_papers.jsonl", rows)
        atomic_write_json(staged / "pdfs/download_report.json", {
            "success": 0,
            "failed": 1,
            "downloaded": [],
            "failed_papers": [{"id": rows[0]["id"], "title": rows[0]["title"], "failure_class": "network"}],
            "pdf_count": 0,
            "attempted": 1,
        })
        atomic_write_json(staged / "download_stats.json", {"success": 0, "failed": 1})
        atomic_write_json(staged / "logs/agent_states.json", {"download": {"state": {}}})
        return {"success": 0, "failed": 1, "stats": {"success": 0, "failed": 1}}


class WebRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "demo"
        self.project.mkdir()
        retryable_project(self.project)
        self.old_root = web_app.OUTPUT_ROOT
        self.old_runner = web_app.task_runner
        web_app.OUTPUT_ROOT = self.root
        web_app.task_runner = TaskRunner(max_workers=1)
        self.client = TestClient(web_app.create_app())

    def tearDown(self):
        web_app.task_runner.shutdown()
        web_app.task_runner = self.old_runner
        web_app.OUTPUT_ROOT = self.old_root
        self.temp.cleanup()

    def retry_payload(self, *, confirmed: bool) -> dict:
        snapshot = current_retry_snapshot(self.project)
        failed_ids = [item.retry_id for item in snapshot.items]
        payload = {"report_revision": snapshot.report_revision, "failed_ids": failed_ids}
        if confirmed:
            payload["retry_confirmation"] = {
                "expected_report_revision": snapshot.report_revision,
                "failed_ids": failed_ids,
            }
        return payload

    def test_retry_action_returns_order_bound_confirmation_challenge(self):
        payload = self.retry_payload(confirmed=False)

        response = self.client.post("/projects/demo/actions/retry-failed-downloads", json=payload)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {
            "code": "confirmation_required",
            "detail": "confirmation_required",
            "confirmationRequired": True,
            "expectedReportRevision": payload["report_revision"],
            "failedIds": payload["failed_ids"],
        })

    def test_confirmed_retry_commits_only_failed_item_and_updates_task_and_ledger(self):
        original = self.project / "pdfs/original.pdf"
        original_digest = hashlib.sha256(original.read_bytes()).hexdigest()

        with patch.object(web_app, "WorkflowActionAdapter", SuccessfulRetryAdapter):
            response = self.client.post(
                "/projects/demo/actions/retry-failed-downloads",
                json=self.retry_payload(confirmed=True),
            )
            task = web_app.task_runner.wait(response.json()["task_id"], timeout=2)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["result"]["data"], {"success": 2, "failed": 0, "retried": 1, "recovered": 1})
        self.assertEqual(load_workflow_state(self.project)["stages"]["retrieval"]["attempt"], 2)
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), original_digest)
        self.assertFalse((self.project / ".retrieval_retry_pending.json").exists())
        self.assertFalse(web_app.build_project_state(self.root, "demo")["retrievalRecovery"]["canRetry"])

    def test_revision_conflict_returns_current_revision_without_starting_task(self):
        payload = self.retry_payload(confirmed=False)
        payload["report_revision"] = "0" * 64

        response = self.client.post("/projects/demo/actions/retry-failed-downloads", json=payload)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "revision_conflict")
        self.assertEqual(response.json()["expectedReportRevision"], current_retry_snapshot(self.project).report_revision)
        self.assertIsNone(web_app.task_runner.active_for_project("demo"))

    def test_invalid_or_mismatched_retry_selection_is_a_safe_400(self):
        payload = self.retry_payload(confirmed=True)
        payload["retry_confirmation"]["failed_ids"] = []

        response = self.client.post("/projects/demo/actions/retry-failed-downloads", json=payload)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            "code": "invalid_retry_request",
            "detail": "confirmation_selection_mismatch",
        })
        self.assertNotIn(str(self.root), response.text)
        self.assertIsNone(web_app.task_runner.active_for_project("demo"))

    def test_zero_recovery_retry_commits_aggregate_partial_result_and_remains_retryable(self):
        with patch.object(web_app, "WorkflowActionAdapter", UnsuccessfulRetryAdapter):
            response = self.client.post(
                "/projects/demo/actions/retry-failed-downloads",
                json=self.retry_payload(confirmed=True),
            )
            task = web_app.task_runner.wait(response.json()["task_id"], timeout=2)

        retrieval = load_workflow_state(self.project)["stages"]["retrieval"]
        self.assertEqual(task["status"], "partial")
        self.assertEqual(task["result"]["data"], {"success": 1, "failed": 1, "retried": 1, "recovered": 0})
        self.assertEqual((retrieval["status"], retrieval["attempt"]), ("partial", 2))
        self.assertTrue(web_app.build_project_state(self.root, "demo")["retrievalRecovery"]["canRetry"])

    def test_apply_ack_failure_returns_committed_success_after_exact_verification(self):
        real_apply = web_app.apply_retry_transaction

        def commit_then_raise(project, transaction_id):
            real_apply(project, transaction_id)
            raise ValueError("acknowledgement lost")

        with (
            patch.object(web_app, "WorkflowActionAdapter", SuccessfulRetryAdapter),
            patch.object(web_app, "apply_retry_transaction", side_effect=commit_then_raise),
        ):
            response = self.client.post(
                "/projects/demo/actions/retry-failed-downloads",
                json=self.retry_payload(confirmed=True),
            )
            task = web_app.task_runner.wait(response.json()["task_id"], timeout=2)

        self.assertEqual(task["status"], "completed")
        self.assertEqual(load_workflow_state(self.project)["stages"]["retrieval"]["status"], "completed")
        self.assertFalse((self.project / ".retrieval_retry_pending.json").exists())

    def test_apply_failure_before_commit_rolls_back_and_reports_task_failure(self):
        before = json.loads((self.project / "pdfs/download_report.json").read_text())

        with (
            patch.object(web_app, "WorkflowActionAdapter", SuccessfulRetryAdapter),
            patch.object(web_app, "apply_retry_transaction", side_effect=ValueError("not committed")),
        ):
            response = self.client.post(
                "/projects/demo/actions/retry-failed-downloads",
                json=self.retry_payload(confirmed=True),
            )
            task = web_app.task_runner.wait(response.json()["task_id"], timeout=2)

        self.assertEqual(task["status"], "failed")
        self.assertEqual(json.loads((self.project / "pdfs/download_report.json").read_text()), before)
        self.assertFalse((self.project / ".retrieval_retry_pending.json").exists())

    def test_running_retry_uses_active_task_projection_without_mutating_authority_ledger(self):
        started = Event()
        release = Event()

        class BlockingAdapter:
            def run(self, *args, **kwargs):
                started.set()
                release.wait(timeout=2)
                return SuccessfulRetryAdapter().run(*args, **kwargs)

        with patch.object(web_app, "WorkflowActionAdapter", BlockingAdapter):
            response = self.client.post(
                "/projects/demo/actions/retry-failed-downloads",
                json=self.retry_payload(confirmed=True),
            )
            self.assertTrue(started.wait(timeout=1))
            state = web_app.build_project_state(self.root, "demo")
            ledger = load_workflow_state(self.project)["stages"]["retrieval"]
            release.set()
            task = web_app.task_runner.wait(response.json()["task_id"], timeout=2)

        self.assertEqual(state["activeTask"]["action"], "retry-failed-downloads")
        self.assertEqual(state["retrievalRecovery"], {"canRetry": False, "reportRevision": "", "items": []})
        self.assertEqual((ledger["status"], ledger["attempt"]), ("partial", 1))
        self.assertEqual(task["status"], "completed")

    def test_retry_worker_failure_rolls_back_before_authorities(self):
        def facts():
            return (
                json.loads((self.project / "pdfs/download_report.json").read_text()),
                [json.loads(line) for line in (self.project / "filtered/included_papers.jsonl").read_text().splitlines()],
                json.loads((self.project / "workflow_state.json").read_text()),
            )

        before = facts()

        class FailingAdapter:
            def run(self, *args, **kwargs):
                raise RuntimeError("private /Users/alice/retry.pdf")

        with patch.object(web_app, "WorkflowActionAdapter", FailingAdapter):
            response = self.client.post(
                "/projects/demo/actions/retry-failed-downloads",
                json=self.retry_payload(confirmed=True),
            )
            task = web_app.task_runner.wait(response.json()["task_id"], timeout=2)

        after = facts()
        self.assertEqual(task["status"], "failed")
        self.assertNotIn("Users", task["error"])
        self.assertEqual(after, before)
        self.assertFalse((self.project / ".retrieval_retry_pending.json").exists())


if __name__ == "__main__":
    unittest.main()
