import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from threading import Event
from unittest.mock import patch
from starlette.testclient import TestClient

from reviewpilot_core.setup_revision import begin_setup_transaction, materially_changes_dependencies, normalize_setup, reconcile_setup_transaction, setup_revision, update_setup_transaction_target
from reviewpilot_core.state_projection import build_rp_data, export_artifact_path
from reviewpilot_core.extraction_schema import finalize_schema, save_schema_draft
from reviewpilot_core.workflow_state import complete_action, initialize_workflow_state, load_workflow_state, mark_stages_stale, start_action
import reviewpilot_core.setup_revision as setup_revision_module
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

    def test_each_material_setup_input_returns_impact_preview_without_writing(self):
        changes = {
            "description": {"description": "Other"}, "topic": {"primary_topic": "Robotics"},
            "domain": {"domain": "surgery"}, "query": {"search_terms": "robotics"},
            "platforms": {"platforms": ["arxiv"], "source_limits": {"arxiv": 10}},
            "limits": {"source_limits": {"pubmed": 20}, "max_results": 20},
            "dates": {"date_start": "2021", "date_end": "2025"},
            "model": {"model": "custom-model"}, "derive": {"derive_search_terms": True},
        }
        for label, change in changes.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); project, current = self._project(root)
                payload = {"project_name": "Demo", "description": "Question", "primary_topic": "Demo", "platforms": ["pubmed"], "source_limits": {"pubmed": 10}, "max_results": 10, **change}
                preview = web_app.update_project_setup(root, "demo", payload)
                self.assertTrue(preview["confirmationRequired"])
                self.assertEqual(json.loads((project / "search_conditions.json").read_text()), current)

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

    def test_legacy_implicit_defaults_are_a_visual_noop_when_saved(self):
        legacy = {"project_name": "Demo", "description": "Question", "search_terms": "Question", "platforms": ["pubmed"]}
        submitted = web_app._setup_config({"project_name": "Demo", "description": "Question", "platforms": ["pubmed"]})
        self.assertEqual(setup_revision(legacy), setup_revision(submitted))

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

    def test_projection_never_recounts_or_marks_stale_artifacts_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project, _config = self._project(root)
            (project / "collected").mkdir()
            (project / "collected" / "pubmed.jsonl").write_text('{"title":"old"}\n')
            (project / "pdfs").mkdir(); (project / "pdfs" / "old.pdf").write_bytes(b"old")
            (project / "extraction").mkdir(); (project / "extraction" / "extraction_schema.json").write_text('{"fields":[{"name":"old"}],"finalized":true}')
            (project / "extraction" / "extraction_results.jsonl").write_text('{"title":"old","old":"value"}\n')
            mark_stages_stale(project, ["collection", "retrieval", "extraction"])
            projected = build_rp_data(root, "demo")
        self.assertEqual(projected["platforms"], [["PubMed", 0]])
        self.assertEqual(projected["retrievalSummary"]["retrieved"], 0)
        self.assertEqual(projected["schemaWorkbench"]["status"], "missing")
        self.assertNotIn("categorize", projected["quietActions"])
        self.assertEqual(projected["steps"][0]["status"], "stale")
        self.assertEqual(projected["steps"][0]["sub"], "Needs rerun")

    def test_fresh_schema_after_stale_extraction_is_reviewable_without_old_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project, _ = self._project(root)
            for action in ("screen", "download-pdfs", "run-extraction"):
                start_action(project, action); complete_action(project, action, {"processed": 1})
            (project / "extraction").mkdir(exist_ok=True)
            (project / "extraction" / "extraction_results.jsonl").write_text('{"title":"old","legacy":"secret"}\n')
            mark_stages_stale(project, ["extraction"])
            start_action(project, "generate-schema")
            save_schema_draft(project, {"fields": [{"name": "fresh", "type": "Text"}]})
            complete_action(project, "generate-schema", {"field_count": 1})
            start_action(project, "finalize-schema"); finalize_schema(project); complete_action(project, "finalize-schema", {"field_count": 1})
            projected = build_rp_data(root, "demo")
        self.assertEqual(projected["fields"][0][0], "fresh")
        self.assertEqual(projected["schemaWorkbench"]["primary_action"], "Run Extraction")
        self.assertEqual(projected["previewFields"], [])
        self.assertTrue(projected["stageState"]["extraction"]["stale"])

    def test_fresh_suggestions_after_stale_categorization_are_reviewable_without_terminal_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project, _ = self._project(root)
            for action in ("screen", "download-pdfs", "run-extraction", "categorize"):
                start_action(project, action); complete_action(project, action, {"processed": 1})
            cat = project / "categorization"; cat.mkdir(exist_ok=True)
            (cat / "categorization_mapping.json").write_text(json.dumps({"categories": ["Old"], "mapping": {"P": "Old"}}))
            mark_stages_stale(project, ["categorization"])
            start_action(project, "suggest-categories")
            (cat / "suggested_categories.json").write_text(json.dumps({"field": "methods", "categories": ["Fresh"]}))
            complete_action(project, "suggest-categories", {"category_count": 1})
            projected = build_rp_data(root, "demo")
        self.assertEqual(projected["categorizationWorkflow"]["suggestedCategories"], ["Fresh"])
        self.assertFalse(projected["categorizationWorkflow"]["done"])
        self.assertEqual(projected["groups"], [])

    def test_fresh_ready_only_output_counts_as_setup_impact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project, _ = self._project(root)
            start_action(project, "screen"); complete_action(project, "screen", {})
            start_action(project, "generate-schema"); complete_action(project, "generate-schema", {"field_count": 1})
            preview = web_app.update_project_setup(root, "demo", {"project_name": "Demo", "description": "Changed", "platforms": ["pubmed"]})
        self.assertIn("extraction", preview["affectedStages"])

    def test_setup_projection_round_trips_model_and_derive_search_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project, config = self._project(root)
            config.update(model="custom-model", derive_search_terms=True)
            (project / "search_conditions.json").write_text(json.dumps(config))
            setup = build_rp_data(root, "demo")["setup"]
        self.assertEqual(setup["model"], "custom-model")
        self.assertTrue(setup["derive_search_terms"])

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

    def test_overwrite_validation_and_start_are_inside_project_reservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project, _config = self._project(root)
            mark_stages_stale(project, ["collection"])
            entered, release = Event(), Event()
            original = web_app.stale_replacement_stages
            def gated(*args):
                entered.set(); self.assertTrue(release.wait(2)); return original(*args)
            old_runner = web_app.task_runner; web_app.task_runner = web_app.TaskRunner(max_workers=1)
            try:
                with patch.object(web_app, "stale_replacement_stages", side_effect=gated), ThreadPoolExecutor(max_workers=2) as executor:
                    action = executor.submit(web_app.submit_project_action, root, "demo", "collect")
                    self.assertTrue(entered.wait(1))
                    update = executor.submit(web_app.update_project_setup, root, "demo", {"project_name": "Demo", "description": "Other", "platforms": ["pubmed"]})
                    self.assertFalse(update.done())
                    release.set()
                    with self.assertRaises(web_app.ConfirmationRequired): action.result(timeout=1)
                    self.assertTrue(update.result(timeout=1)["confirmationRequired"])
            finally:
                release.set(); web_app.task_runner.shutdown(); web_app.task_runner = old_runner

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

    def test_pending_transaction_rolls_forward_after_setup_write_before_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project, current = self._project(root)
            target = {**current, "description": "Changed"}
            marker = begin_setup_transaction(project, current, target, ["collection"])
            update_setup_transaction_target(marker, target)
            (project / "search_conditions.json").write_text(json.dumps(target))
            setup_revision_module._ACTIVE_TRANSACTIONS.clear()
            self.assertTrue(reconcile_setup_transaction(project))
            self.assertTrue(load_workflow_state(project)["stages"]["collection"]["stale"])
            self.assertFalse(marker.exists())

    def test_pending_transaction_rolls_back_when_setup_was_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project, current = self._project(root)
            marker = begin_setup_transaction(project, current, {**current, "description": "Changed"}, ["collection"])
            setup_revision_module._ACTIVE_TRANSACTIONS.clear()
            self.assertTrue(reconcile_setup_transaction(project))
            self.assertFalse(load_workflow_state(project)["stages"]["collection"]["stale"])
            self.assertFalse(marker.exists())

    def test_failed_immediate_rollback_leaves_marker_for_next_read_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project, _current = self._project(root)
            with patch.object(web_app, "_run_lead_agent_search_setup", side_effect=RuntimeError("agent failed")), patch.object(web_app, "atomic_write_json", side_effect=OSError("rollback failed")):
                with self.assertRaisesRegex(OSError, "rollback failed"):
                    web_app.update_project_setup(root, "demo", {"project_name": "Renamed", "description": "Question", "primary_topic": "Demo", "platforms": ["pubmed"], "max_results": 10})
            marker = project / ".setup_update_pending.json"
            self.assertTrue(marker.exists())
            build_rp_data(root, "demo")
            self.assertFalse(marker.exists())

    def test_failed_request_persists_abort_intent_and_never_rolls_forward_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project, current = self._project(root)
            real_write = web_app.atomic_write_json
            writes = 0
            def fail_rollback(path, data, **kwargs):
                nonlocal writes
                writes += 1
                if writes == 2: raise OSError("rollback failed")
                return real_write(path, data, **kwargs)
            with patch.object(web_app, "mark_stages_stale", side_effect=RuntimeError("post-write failure")), patch.object(web_app, "atomic_write_json", side_effect=fail_rollback):
                with self.assertRaisesRegex(OSError, "rollback failed"):
                    web_app.update_project_setup(root, "demo", {"project_name": "Demo", "description": "Changed", "primary_topic": "Demo", "platforms": ["pubmed"], "max_results": 10, "confirmation": {"expected_revision": setup_revision(current)}})
            marker = project / ".setup_update_pending.json"
            self.assertEqual(json.loads(marker.read_text())["phase"], "abort")
            build_rp_data(root, "demo")
            self.assertEqual(json.loads((project / "search_conditions.json").read_text()), current)

    def test_noop_update_recovers_pending_and_crash_rollforward_still_requires_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project, current = self._project(root)
            marker = begin_setup_transaction(project, current, {**current, "description": "Changed"}, ["collection"])
            setup_revision_module._ACTIVE_TRANSACTIONS.clear()
            result = web_app.update_project_setup(root, "demo", current)
            self.assertFalse(result["confirmationRequired"])
            self.assertFalse(marker.exists())

            target = {**current, "description": "Changed"}
            marker = begin_setup_transaction(project, current, target, ["collection"])
            update_setup_transaction_target(marker, target)
            (project / "search_conditions.json").write_text(json.dumps(target))
            setup_revision_module._ACTIVE_TRANSACTIONS.clear()
            with self.assertRaises(web_app.ConfirmationRequired):
                web_app.submit_project_action(root, "demo", "collect")
            self.assertTrue(load_workflow_state(project)["stages"]["collection"]["stale"])
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
