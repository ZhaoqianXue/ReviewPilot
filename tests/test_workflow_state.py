import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reviewpilot_core.workflow_state import (
    ACTION_STAGES,
    STAGE_NAMES,
    STAGE_STATUSES,
    complete_action,
    initialize_workflow_state,
    load_workflow_state,
    migrate_legacy_workflow_state,
    start_action,
)


class WorkflowStateTests(unittest.TestCase):
    def test_new_ledger_has_exact_versioned_state_contract_and_uses_atomic_writer(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "reviewpilot_core.workflow_state.atomic_write_json"
        ) as writer:
            project = Path(tmp)
            state = initialize_workflow_state(project)

        self.assertEqual(state["version"], 1)
        self.assertEqual(tuple(state["stages"]), STAGE_NAMES)
        self.assertEqual(set(STAGE_STATUSES), {"not_started", "ready", "running", "partial", "failed", "completed"})
        self.assertEqual([stage["status"] for stage in state["stages"].values()], ["ready", "not_started", "not_started", "not_started", "not_started"])
        self.assertTrue(all(set(stage) == {"status", "attempt", "updated_at", "error", "counts", "stale", "last_valid"} for stage in state["stages"].values()))
        writer.assert_called_once_with(project / "workflow_state.json", state)

    def test_actions_map_to_stages_and_intermediate_actions_finish_ready(self):
        self.assertEqual(
            ACTION_STAGES,
            {
                "collect": "collection", "screen": "screening", "download-pdfs": "retrieval",
                "generate-schema": "extraction", "finalize-schema": "extraction", "edit-schema": "extraction",
                "run-extraction": "extraction", "suggest-categories": "categorization", "categorize": "categorization",
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            initialize_workflow_state(project)
            start_action(project, "generate-schema")
            state = complete_action(project, "generate-schema", {"field_count": 7})
            self.assertEqual(state["stages"]["extraction"]["status"], "ready")
            self.assertEqual(state["stages"]["extraction"]["counts"], {"field_count": 7})

    def test_failed_attempt_preserves_last_valid_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            initialize_workflow_state(project)
            start_action(project, "collect")
            complete_action(project, "collect", {"total_papers": 12})
            start_action(project, "collect")
            from reviewpilot_core.workflow_state import fail_action
            state = fail_action(project, "collect", RuntimeError("/Users/private/secret"))

        stage = state["stages"]["collection"]
        self.assertEqual(stage["status"], "failed")
        self.assertEqual(stage["attempt"], 2)
        self.assertEqual(stage["last_valid"]["counts"], {"total_papers": 12})
        self.assertNotIn("private", stage["error"])

    def test_legacy_migration_uses_valid_json_evidence_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "collected").mkdir(parents=True)
            (project / "collected" / "summary.json").write_text(json.dumps({"total_papers": 3, "platform_stats": {"pubmed": 3}}), encoding="utf-8")
            (project / "filtered").mkdir()
            (project / "filtered" / "included_papers.jsonl").write_text('{"title":"A"}\n', encoding="utf-8")
            state = migrate_legacy_workflow_state(project)
            (project / "categorization").mkdir()
            (project / "categorization" / "categorization_mapping.json").write_text('{"mapping":{}}', encoding="utf-8")
            loaded = load_workflow_state(project)

        self.assertEqual(state["migration"]["source"], "legacy_artifacts")
        self.assertEqual(state["stages"]["collection"]["status"], "completed")
        self.assertEqual(state["stages"]["screening"]["status"], "completed")
        self.assertEqual(loaded["stages"]["categorization"]["status"], "not_started")

    def test_loader_rejects_status_outside_exact_enum(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            state = initialize_workflow_state(project)
            state["stages"]["collection"]["status"] = "cancelled"
            (project / "workflow_state.json").write_text(json.dumps(state), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Invalid workflow stage status"):
                load_workflow_state(project)

    def test_migration_does_not_complete_stages_from_arbitrary_empty_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "collected").mkdir(parents=True)
            (project / "collected" / "summary.json").write_text("{}", encoding="utf-8")
            (project / "filtered").mkdir()
            (project / "filtered" / "included_papers.jsonl").write_text("", encoding="utf-8")
            (project / "filtered" / "screening_stats.json").write_text("{}", encoding="utf-8")
            (project / "pdfs").mkdir()
            (project / "pdfs" / "download_report.json").write_text("{}", encoding="utf-8")
            (project / "extraction").mkdir()
            (project / "extraction" / "extraction_results.jsonl").write_text("", encoding="utf-8")
            (project / "extraction" / "extraction_schema.json").write_text('{"fields":[]}', encoding="utf-8")

            state = migrate_legacy_workflow_state(project)

        self.assertEqual(state["stages"]["collection"]["status"], "ready")
        self.assertEqual(state["stages"]["screening"]["status"], "not_started")
        self.assertEqual(state["stages"]["retrieval"]["status"], "not_started")
        self.assertEqual(state["stages"]["extraction"]["status"], "not_started")

    def test_migration_requires_contiguous_completed_prerequisites(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "categorization").mkdir(parents=True)
            (project / "categorization" / "categorization_mapping.json").write_text('{"mapping":{"A":"x"}}', encoding="utf-8")

            state = migrate_legacy_workflow_state(project)

        self.assertEqual(state["stages"]["collection"]["status"], "ready")
        self.assertTrue(all(state["stages"][name]["status"] == "not_started" for name in STAGE_NAMES[1:]))


if __name__ == "__main__":
    unittest.main()
