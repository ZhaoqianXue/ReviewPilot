import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, local
from unittest.mock import patch

import reviewpilot_core.workflow_state as workflow_state
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


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _write_valid_legacy(project: Path, through: str) -> None:
    order = ["collection", "screening", "retrieval", "extraction"]
    _write_json(project / "collected" / "summary.json", {"total_papers": 1, "platform_stats": {"pubmed": 1}})
    if order.index(through) >= 1:
        _write_jsonl(project / "filtered" / "included_papers.jsonl", [{"title": "Paper A"}])
    if order.index(through) >= 2:
        _write_json(project / "pdfs" / "download_report.json", {"success": 1, "failed": 0})
    if order.index(through) >= 3:
        _write_jsonl(project / "extraction" / "extraction_results.jsonl", [{"paper_id": "p1"}])


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
            for prerequisite_action in ("collect", "screen"):
                start_action(project, prerequisite_action)
                complete_action(project, prerequisite_action, {})
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

    def test_migration_rejects_semantically_invalid_artifacts_for_each_stage(self):
        cases = {
            "collection": lambda p: _write_json(p / "collected" / "summary.json", {"total_papers": "3", "platform_stats": {"pubmed": -1}}),
            "screening": lambda p: (_write_valid_legacy(p, "collection"), _write_jsonl(p / "filtered" / "included_papers.jsonl", [{}])),
            "retrieval": lambda p: (_write_valid_legacy(p, "screening"), _write_json(p / "pdfs" / "download_report.json", {"success": "1", "failed": 0})),
            "extraction": lambda p: (_write_valid_legacy(p, "retrieval"), _write_jsonl(p / "extraction" / "extraction_results.jsonl", [{}])),
            "categorization_mapping": lambda p: (_write_valid_legacy(p, "extraction"), _write_json(p / "categorization" / "categorization_mapping.json", {"mapping": [], "categories": ["A"]})),
            "categorization_categories": lambda p: (_write_valid_legacy(p, "extraction"), _write_json(p / "categorization" / "categorization_mapping.json", {"mapping": {}, "categories": [1]})),
            "categorization_empty_key": lambda p: (_write_valid_legacy(p, "extraction"), _write_json(p / "categorization" / "categorization_mapping.json", {"mapping": {"": "A"}, "categories": ["A"]})),
            "categorization_object_value": lambda p: (_write_valid_legacy(p, "extraction"), _write_json(p / "categorization" / "categorization_mapping.json", {"mapping": {"Paper": {"name": "A"}}, "categories": ["A"]})),
            "categorization_malformed_list": lambda p: (_write_valid_legacy(p, "extraction"), _write_json(p / "categorization" / "categorization_mapping.json", {"mapping": {"Paper": ["A", 2]}, "categories": ["A"]})),
            "categorization_empty_list": lambda p: (_write_valid_legacy(p, "extraction"), _write_json(p / "categorization" / "categorization_mapping.json", {"mapping": {"Paper": []}, "categories": ["A"]})),
            "categorization_unknown_category": lambda p: (_write_valid_legacy(p, "extraction"), _write_json(p / "categorization" / "categorization_mapping.json", {"mapping": {"Paper": ["Unknown"]}, "categories": ["A"]})),
        }
        for label, arrange in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp)
                arrange(project)
                state = migrate_legacy_workflow_state(project)
                stage = label.split("_", 1)[0]
                self.assertNotEqual(state["stages"][stage]["status"], "completed")

    def test_migration_accepts_real_r7_multiple_category_mapping_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            _write_valid_legacy(project, "extraction")
            _write_json(
                project / "categorization" / "categorization_mapping.json",
                {
                    "field": "methods",
                    "mode": "multiple",
                    "categories": ["Narrative Literature Review", "Systematic Search Across Scholarly Sources"],
                    "mapping": {
                        "Opportunities and challenges for ChatGPT and large language models in biomedicine and health": [
                            "Narrative Literature Review",
                            "Systematic Search Across Scholarly Sources",
                        ]
                    },
                },
            )

            state = migrate_legacy_workflow_state(project)

        self.assertEqual(state["stages"]["categorization"]["status"], "completed")

    def test_migration_accepts_empty_rows_only_with_coherent_zero_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            _write_json(project / "collected" / "summary.json", {"total_papers": 0, "platform_stats": {"pubmed": 0}})
            _write_jsonl(project / "filtered" / "included_papers.jsonl", [])
            _write_json(project / "filtered" / "screening_stats.json", {"total_screened": 0, "included_count": 0, "excluded_count": 0})
            _write_json(project / "pdfs" / "download_report.json", {"success": 0, "failed": 0})
            _write_jsonl(project / "extraction" / "extraction_results.jsonl", [])
            _write_json(project / "extraction" / "extraction_stats.json", {"processed": 0, "errors": 0})

            state = migrate_legacy_workflow_state(project)

        self.assertEqual([state["stages"][name]["status"] for name in STAGE_NAMES], ["completed", "completed", "completed", "completed", "ready"])

    def test_first_migration_and_action_start_are_serialized_without_lost_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            _write_valid_legacy(project, "collection")
            migration_write_entered = Event()
            starter_attempted = Event()
            starter_entered = Event()
            role = local()
            original_write = workflow_state._write
            actual_lock = workflow_state._project_lock(project)

            class ObservedProjectLock:
                def __enter__(self):
                    if getattr(role, "value", "") == "starter":
                        starter_attempted.set()
                    actual_lock.acquire()
                    if getattr(role, "value", "") == "starter":
                        starter_entered.set()
                    return self

                def __exit__(self, *_args):
                    actual_lock.release()

            def ordered_write(path, state):
                if getattr(role, "value", "") == "migrator" and state["stages"]["collection"]["status"] == "completed":
                    migration_write_entered.set()
                    self.assertTrue(starter_attempted.wait(timeout=1), "starter never attempted the project lock")
                    self.assertFalse(starter_entered.is_set())
                original_write(path, state)

            def migrate():
                role.value = "migrator"
                return load_workflow_state(project)

            def start():
                role.value = "starter"
                return start_action(project, "collect")

            with patch("reviewpilot_core.workflow_state._project_lock", return_value=ObservedProjectLock()), patch(
                "reviewpilot_core.workflow_state._write", side_effect=ordered_write
            ), ThreadPoolExecutor(max_workers=2) as executor:
                migration_future = executor.submit(migrate)
                self.assertTrue(migration_write_entered.wait(timeout=1), "migration never entered its controlled write")
                start_future = executor.submit(start)
                migration_future.result(timeout=2)
                start_future.result(timeout=2)

            state = load_workflow_state(project)
        self.assertEqual(state["stages"]["collection"]["status"], "running")
        self.assertEqual(state["stages"]["collection"]["attempt"], 1)

    def test_loader_validates_all_stage_metadata_types(self):
        invalid_values = {
            "updated_at": 123,
            "error": ["bad"],
            "counts": {"processed": "3"},
            "stale": 1,
            "last_valid": {"status": "completed", "counts": {"processed": "3"}},
        }
        for field, value in invalid_values.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp)
                state = initialize_workflow_state(project)
                state["stages"]["collection"][field] = value
                (project / "workflow_state.json").write_text(json.dumps(state), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_workflow_state(project)


if __name__ == "__main__":
    unittest.main()
