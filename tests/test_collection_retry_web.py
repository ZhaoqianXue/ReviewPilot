import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import web_app
from agents.lead_agent import LeadAgent
from reviewpilot_core.state_projection import build_rp_data
from reviewpilot_core.task_runner import TaskRunner
from reviewpilot_core.workflow_state import load_workflow_state, save_workflow_state
from searchers.sources import SourceResult, SourceSearchError


class SourceLimitValidationTests(unittest.TestCase):
    def config(self, limits):
        return web_app._setup_config({"project_name": "P", "description": "D", "search_terms": "AI",
                                      "platforms": ["pubmed", "arxiv"], "source_limits": limits})

    def test_positive_whole_numbers_are_accepted(self):
        self.assertEqual(self.config({"pubmed": "25", "arxiv": 7})["source_limits"], {"pubmed": 25, "arxiv": 7})
        self.assertEqual(self.config({"pubmed": 3.0})["source_limits"], {"pubmed": 3, "arxiv": 10})

    def test_invalid_limits_are_rejected_not_silently_replaced(self):
        for bad in (0, -5, "0", "2.5", 2.5, "abc", "", "1e3", True, [], {}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "Max results for arXiv must be a whole number of at least 1"):
                self.config({"pubmed": 10, "arxiv": bad})


class CollectionRetryWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_runner, web_app.task_runner = web_app.task_runner, TaskRunner(max_workers=1)
        self.failing = {"arxiv"}
        self.calls = []
        patches = [
            patch("agents.collection_agent.search_source", side_effect=self.search),
            patch("agents.collection_agent.time.sleep"),
            patch.object(LeadAgent, "_ensure_relevance_prompt", lambda self, project_path, config: project_path / "prompts/relevance_prompt.json"),
            patch.object(LeadAgent, "_verify_stage_artifacts", lambda self, project_path, stage: []),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        project = web_app.create_project(self.root, {"project_name": "Retry", "description": "Review AI", "search_terms": "AI",
                                                     "platforms": ["pubmed", "arxiv"], "source_limits": {"pubmed": 3, "arxiv": 3}})
        self.project_id = project["id"]
        self.project = self.root / self.project_id

    def tearDown(self):
        web_app.task_runner = self.old_runner
        self.tmp.cleanup()

    def search(self, source, query, *, max_results, date_range=None, output_file=None):
        self.calls.append(source)
        if source in self.failing:
            raise SourceSearchError(f"{source} HTTP 503", f"<{source}> {query}", transient=True)
        return SourceResult([{"id": f"{source}-{n}", "title": "t"} for n in range(2)], f"<{source}> {query}")

    def run_action(self, input_data=None):
        task_id = web_app.submit_project_action(self.root, self.project_id, "collect", input_data=input_data)
        return web_app.task_runner.wait(task_id, timeout=10)

    def test_retry_only_reruns_failed_sources_and_marks_downstream_stale(self):
        task = self.run_action()
        self.assertEqual(task["status"], "partial", task.get("error"))
        state = build_rp_data(self.root, self.project_id)
        self.assertEqual(state["collectionRetry"], {"sources": ["arxiv"]})
        self.assertEqual(state["executedQueries"]["arxiv"]["query"], "<arxiv> (AI)")

        # Pretend screening ran on the partial collection.
        ledger = load_workflow_state(self.project)
        ledger["stages"]["screening"].update(status="completed", attempt=1, last_valid={"status": "completed", "attempt": 1, "updated_at": ledger["stages"]["collection"]["updated_at"], "counts": {}})
        save_workflow_state(self.project, ledger)

        with self.assertRaisesRegex(ValueError, "Only failed sources"):
            web_app.submit_project_action(self.root, self.project_id, "collect", input_data={"retry_sources": ["pubmed"]})
        with self.assertRaisesRegex(ValueError, "non-empty list"):
            web_app.submit_project_action(self.root, self.project_id, "collect", input_data={"retry_sources": "arxiv"})

        # A retry that still fails changes no records, so screening stays valid.
        task = self.run_action({"retry_sources": ["arxiv"]})
        self.assertEqual(task["status"], "partial", task.get("error"))
        self.assertFalse(load_workflow_state(self.project)["stages"]["screening"]["stale"])
        self.assertEqual(build_rp_data(self.root, self.project_id)["collectionRetry"], {"sources": ["arxiv"]})

        self.failing.clear()
        self.calls.clear()
        task = self.run_action({"retry_sources": ["arxiv"]})
        self.assertEqual(task["status"], "completed", task.get("error"))
        self.assertEqual(self.calls, ["arxiv"])
        state = build_rp_data(self.root, self.project_id)
        self.assertEqual(state["collectionRetry"], {"sources": []})
        self.assertEqual(state["platformIssues"], [])
        ledger = load_workflow_state(self.project)
        self.assertEqual(ledger["stages"]["collection"]["counts"], {"succeeded": 2, "failed": 0, "collected": 4})
        self.assertTrue(ledger["stages"]["screening"]["stale"])

    def test_retry_is_refused_after_the_search_setup_changed(self):
        self.assertEqual(self.run_action()["status"], "partial")
        config = json.loads((self.project / "search_conditions.json").read_text(encoding="utf-8"))
        payload = {"project_name": config["project_name"], "description": config["description"], "search_terms": "AI",
                   "platforms": ["pubmed", "arxiv"], "source_limits": {"pubmed": 3, "arxiv": 9}, "date_range": config["date_range"]}
        preview = web_app.update_project_setup(self.root, self.project_id, payload)
        self.assertTrue(preview["confirmationRequired"])
        web_app.update_project_setup(self.root, self.project_id, {**payload, "confirmation": {"expected_revision": preview["expectedRevision"]}})
        self.assertTrue(load_workflow_state(self.project)["stages"]["collection"]["stale"])
        self.assertEqual(build_rp_data(self.root, self.project_id)["collectionRetry"], {"sources": []})
        with self.assertRaisesRegex(ValueError, "Only failed sources"):
            web_app.submit_project_action(self.root, self.project_id, "collect", input_data={"retry_sources": ["arxiv"]})


if __name__ == "__main__":
    unittest.main()
