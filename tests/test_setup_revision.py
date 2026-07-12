import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from starlette.testclient import TestClient

from reviewpilot_core.setup_revision import materially_changes_dependencies, normalize_setup, setup_revision
from reviewpilot_core.state_projection import build_rp_data, export_artifact_path
from reviewpilot_core.workflow_state import complete_action, initialize_workflow_state, load_workflow_state, mark_stages_stale
import web_app


class SetupRevisionTests(unittest.TestCase):
    def _project(self, root: Path):
        project = root / "demo"
        project.mkdir()
        config = web_app._setup_config({"project_name": "Demo", "description": "Question", "platforms": ["pubmed"], "max_results": 10})
        (project / "search_conditions.json").write_text(json.dumps(config), encoding="utf-8")
        initialize_workflow_state(project)
        complete_action(project, "collect", {"total_papers": 2})
        return project, config

    def test_revision_is_stable_for_equivalent_normalized_setup(self):
        left = web_app._setup_config({"project_name": " Demo ", "description": "Question", "platforms": "pubmed, arxiv", "source_limits": {"pubmed": "10", "arxiv": 20}})
        right = web_app._setup_config({"project_name": "Demo", "description": "Question", "platforms": ["pubmed", "arxiv"], "source_limits": {"pubmed": 10, "arxiv": 20}})
        self.assertEqual(normalize_setup(left), normalize_setup(right))
        self.assertEqual(setup_revision(left), setup_revision(right))

    def test_all_material_input_classes_invalidate_dependencies(self):
        base = web_app._setup_config({"project_name": "Demo", "description": "Question", "primary_topic": "AI", "domain": "medicine", "search_terms": "AI", "platforms": ["pubmed"], "source_limits": {"pubmed": 10}, "date_start": "2020", "date_end": "2024", "model": "m1"})
        variants = [
            {"description": "Other"}, {"primary_topic": "Robotics"}, {"domain": "surgery"},
            {"search_terms": "robotics", "search_queries": [{"name": "main", "query": "robotics"}]},
            {"platforms": ["arxiv"], "source_limits": {"arxiv": 10}},
            {"max_results": 20, "source_limits": {"pubmed": 20}},
            {"date_range": {"start": "2021", "end": "2024"}}, {"model": "m2"},
        ]
        for changes in variants:
            with self.subTest(changes=changes):
                self.assertTrue(materially_changes_dependencies(base, {**base, **changes}))
        self.assertFalse(materially_changes_dependencies(base, {**base, "project_name": "Renamed"}))

    def test_material_change_previews_then_confirmed_write_marks_outputs_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, old = self._project(root)
            artifact = project / "collected" / "summary.json"
            artifact.parent.mkdir()
            artifact.write_text('{"total_papers":2,"platform_stats":{"pubmed":2}}', encoding="utf-8")
            proposed = {"project_name": "Demo", "description": "Changed", "platforms": ["pubmed"], "max_results": 10}
            preview = web_app.update_project_setup(root, "demo", proposed)
            self.assertTrue(preview["confirmationRequired"])
            self.assertEqual(preview["expectedRevision"], setup_revision(old))
            self.assertEqual(preview["affectedStages"], ["collection"])
            self.assertEqual(json.loads((project / "search_conditions.json").read_text())["description"], "Question")

            confirmed = web_app.update_project_setup(root, "demo", {**proposed, "confirmation": {"expected_revision": preview["expectedRevision"]}})
            self.assertNotEqual(confirmed["setupRevision"], preview["expectedRevision"])
            self.assertTrue(load_workflow_state(project)["stages"]["collection"]["stale"])
            self.assertTrue(artifact.exists())

    def test_noop_does_not_require_confirmation_or_advance_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _project, config = self._project(root)
            result = web_app.update_project_setup(root, "demo", config)
            self.assertFalse(result["confirmationRequired"])
            self.assertEqual(result["setupRevision"], setup_revision(config))

    def test_stale_confirmation_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            with self.assertRaisesRegex(ValueError, "revision"):
                    web_app.update_project_setup(root, "demo", {"project_name": "Demo", "description": "Changed", "platforms": ["pubmed"], "confirmation": {"expected_revision": "stale"}})

    def test_stale_artifact_is_preserved_but_blocked_from_export_and_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project, old = self._project(root)
            artifact = project / "collected" / "summary.json"; artifact.parent.mkdir(); artifact.write_text('{"total_papers":2,"platform_stats":{"pubmed":2}}')
            prompt = project / "prompts" / "relevance_prompt.json"; prompt.parent.mkdir(); prompt.write_text('{}')
            preview = web_app.update_project_setup(root, "demo", {"project_name": "Demo", "description": "Changed", "platforms": ["pubmed"], "max_results": 10})
            web_app.update_project_setup(root, "demo", {"project_name": "Demo", "description": "Changed", "platforms": ["pubmed"], "max_results": 10, "confirmation": {"expected_revision": preview["expectedRevision"]}})
            self.assertTrue(artifact.exists())
            self.assertIsNone(export_artifact_path(project, "relevance-prompt"))
            projected = build_rp_data(root, "demo")
            self.assertEqual(projected["screeningMetrics"]["identified"], 0)

    def test_rerun_of_stale_stage_requires_exact_overwrite_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project, config = self._project(root)
            mark_stages_stale(project, ["collection"])
            with self.assertRaises(web_app.ConfirmationRequired) as raised:
                web_app.submit_project_action(root, "demo", "collect")
            self.assertEqual(raised.exception.stages, ["collection"])

            class Result:
                def to_dict(self): return {"data": {"total_papers": 3}}
            class Agent:
                def __init__(self, *args, **kwargs): pass
                def handle_message(self, **kwargs): return Result()
            with patch.object(web_app, "LeadAgent", Agent):
                task_id = web_app.submit_project_action(root, "demo", "collect", input_data={"overwrite_confirmation": {"expected_revision": setup_revision(config), "affected_stages": ["collection"]}})
                task = web_app.task_runner.wait(task_id, timeout=2)
            self.assertEqual(task["status"], "completed")
            self.assertFalse(load_workflow_state(project)["stages"]["collection"]["stale"])

    def test_active_task_rejects_setup_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            old = web_app.task_runner
            class Active:
                def active_for_project(self, project_id): return {"task_id": "1", "project_id": project_id, "action": "collect", "status": "running"}
            web_app.task_runner = Active()
            try:
                with self.assertRaises(web_app.TaskConflictError):
                    web_app.update_project_setup(root, "demo", {"project_name": "Demo", "description": "Changed", "platforms": ["pubmed"]})
            finally:
                web_app.task_runner = old

    def test_stale_revision_api_contract_is_conflict_and_failed_write_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project, config = self._project(root)
            old_root = web_app.OUTPUT_ROOT; web_app.OUTPUT_ROOT = root
            try:
                response = TestClient(web_app.create_app()).put("/projects/demo/setup", json={"project_name": "Demo", "description": "Changed", "platforms": ["pubmed"], "confirmation": {"expected_revision": "stale"}})
                self.assertEqual(response.status_code, 409)
            finally:
                web_app.OUTPUT_ROOT = old_root

            before = json.loads((project / "search_conditions.json").read_text())
            with patch.object(web_app, "_run_lead_agent_search_setup", side_effect=RuntimeError("write failed")):
                with self.assertRaisesRegex(RuntimeError, "write failed"):
                    web_app.update_project_setup(root, "demo", {"project_name": "Renamed", "description": "Question", "primary_topic": "Demo", "platforms": ["pubmed"], "max_results": 10})
            self.assertEqual(json.loads((project / "search_conditions.json").read_text()), before)
            self.assertFalse((project / ".setup_update_pending.json").exists())


if __name__ == "__main__":
    unittest.main()
