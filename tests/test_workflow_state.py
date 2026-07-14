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


def _terminal_result(action: str) -> dict:
    return {
        "collect": {"total": 1, "platform_stats": {"pubmed": 1}, "platform_errors": {}},
        "download-pdfs": {"success": 1, "failed": 0},
        "run-extraction": {"processed": 1, "errors": 0},
    }.get(action, {})


class WorkflowStateTests(unittest.TestCase):
    def test_retry_failed_downloads_uses_retrieval_stage_and_strict_download_outcomes(self):
        from reviewpilot_core.workflow_state import structured_action_outcome
        for result, expected, expected_error in (({"success": 2, "failed": 0}, "completed", None),
            ({"success": 1, "failed": 1}, "partial", None),
            ({"success": 0, "failed": 2}, "failed", "Action produced no successful outputs (2 failed).")):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(structured_action_outcome("retry-failed-downloads", result)[0], expected)
                project = Path(tmp); initialize_workflow_state(project)
                for prerequisite in ("collect", "screen"):
                    start_action(project, prerequisite); complete_action(project, prerequisite, _terminal_result(prerequisite))
                self.assertEqual(start_action(project, "retry-failed-downloads")["stages"]["retrieval"]["status"], "running")
                stage = complete_action(project, "retry-failed-downloads", result)["stages"]["retrieval"]
                self.assertEqual(stage["status"], expected)
                self.assertEqual(stage["counts"], {"succeeded": result["success"], "failed": result["failed"]})
                self.assertEqual(stage["error"], expected_error)

        valid_aliases = (
            {"stats": {"success": 2, "failed": 1}},
            {"success": 2, "failed": 1, "stats": {"successful": 2, "failed": 1}},
        )
        for result in valid_aliases:
            with self.subTest(valid_aliases=result):
                self.assertEqual(structured_action_outcome("retry-failed-downloads", result),
                    structured_action_outcome("download-pdfs", result))
        invalid_contracts = ({}, {"success": True, "failed": 0}, {"success": 1, "failed": "0"},
            {"success": 1}, {"failed": 0},
            {"success": 1, "failed": 0, "stats": {"successful": 2, "failed": 0}},
            {"success": 1, "failed": 0, "stats": {"success": 1, "failed": 1}})
        for invalid in invalid_contracts:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                structured_action_outcome("retry-failed-downloads", invalid)
    def test_starting_material_rerun_atomically_stales_current_and_downstream_outputs(self):
        for rerun_action, stage_name in (("collect", "collection"), ("download-pdfs", "retrieval"), ("run-extraction", "extraction")):
            with self.subTest(action=rerun_action), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp); initialize_workflow_state(project)
                for action in ("collect", "screen", "download-pdfs", "run-extraction", "categorize"):
                    start_action(project, action); complete_action(project, action, _terminal_result(action))
                state = start_action(project, rerun_action)
                index = STAGE_NAMES.index(stage_name)
                self.assertEqual(state["stages"][stage_name]["status"], "running")
                self.assertTrue(state["stages"][stage_name]["stale"])
                self.assertEqual(state["stages"][stage_name]["counts"], {})
                self.assertIsNone(state["stages"][stage_name]["error"])
                self.assertTrue(all(state["stages"][name]["stale"] for name in STAGE_NAMES[index + 1:]))

    def test_orphaned_material_rerun_stales_current_and_downstream_but_initial_orphan_does_not(self):
        from reviewpilot_core.workflow_state import reconcile_orphaned_running
        for rerun_action, stage_name in (("collect", "collection"), ("download-pdfs", "retrieval"), ("run-extraction", "extraction")):
            with self.subTest(action=rerun_action), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp); initialize_workflow_state(project)
                for action in ("collect", "screen", "download-pdfs", "run-extraction", "categorize"):
                    start_action(project, action); complete_action(project, action, _terminal_result(action))
                start_action(project, rerun_action)
                state = reconcile_orphaned_running(project)
                index = STAGE_NAMES.index(stage_name)
                self.assertEqual(state["stages"][stage_name]["status"], "failed")
                self.assertTrue(state["stages"][stage_name]["stale"])
                self.assertTrue(all(state["stages"][name]["stale"] for name in STAGE_NAMES[index + 1:]))
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp); initialize_workflow_state(project); start_action(project, "collect")
            initial = reconcile_orphaned_running(project)
        self.assertFalse(initial["stages"]["collection"]["stale"])

    def test_exception_failure_preserves_safe_detail_without_reusing_previous_counts(self):
        from reviewpilot_core.workflow_state import fail_action
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp); initialize_workflow_state(project)
            start_action(project, "collect"); complete_action(project, "collect", _terminal_result("collect"))
            running = start_action(project, "collect")
            self.assertEqual(running["stages"]["collection"]["counts"], {})
            state = fail_action(project, "collect", RuntimeError("token=secret https://private.example/patient"))
        stage = state["stages"]["collection"]
        self.assertEqual(stage["status"], "failed")
        self.assertTrue(stage["stale"])
        self.assertEqual(stage["counts"], {})
        self.assertEqual(stage["error"], "Action failed (RuntimeError).")
        self.assertNotIn("secret", stage["error"])

    def test_upstream_rerun_stales_downstream_structured_failure_report_without_last_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp); initialize_workflow_state(project)
            start_action(project, "collect"); complete_action(project, "collect", _terminal_result("collect"))
            start_action(project, "screen"); complete_action(project, "screen", {})
            start_action(project, "download-pdfs"); complete_action(project, "download-pdfs", {"success": 0, "failed": 1})
            self.assertIsNone(load_workflow_state(project)["stages"]["retrieval"]["last_valid"])
            state = start_action(project, "collect")
        self.assertTrue(state["stages"]["retrieval"]["stale"])

    def test_terminal_contracts_are_required_exact_and_consistent_across_root_and_nested_aliases(self):
        from reviewpilot_core.workflow_state import structured_action_outcome
        valid = [
            ("collect", {"total": 2, "total_papers": 2, "platform_stats": {"pubmed": 2, "arxiv": 0}, "platform_errors": {"arxiv": "503"}, "stats": {"total": 2, "platform_stats": {"pubmed": 2, "arxiv": 0}, "platform_errors": {"arxiv": "503"}}}),
            ("download-pdfs", {"success": 2, "pdf_count": 2, "failed": 1, "stats": {"successful": 2, "failed": 1}}),
            ("run-extraction", {"processed": 2, "success": 2, "errors": 1, "stats": {"processed": 2, "failed": 1}}),
        ]
        for action, result in valid:
            with self.subTest(action=action): self.assertEqual(structured_action_outcome(action, result)[0], "partial")
        invalid = [
            ("collect", {}),
            ("collect", {"total": 2, "platform_stats": {"pubmed": 1}, "platform_errors": {}}),
            ("collect", {"total": 0, "platform_stats": {}, "platform_errors": {"pubmed": "503"}}),
            ("collect", {"total": 1, "platform_stats": {"pubmed": 1}, "platform_errors": {"pubmed": "503"}}),
            ("collect", {"total": 1, "platform_stats": {"pubmed": 1}, "platform_errors": {}, "stats": {"total": 2, "platform_stats": {"pubmed": 1}, "platform_errors": {}}}),
            ("download-pdfs", {}),
            ("download-pdfs", {"success": 1, "failed": 0, "stats": {"success": 2, "failed": 0}}),
            ("run-extraction", {}),
            ("run-extraction", {"processed": 1, "errors": 0, "stats": {"processed": 1, "errors": 1}}),
        ]
        for action, result in invalid:
            with self.subTest(action=action, result=result), self.assertRaises(ValueError): structured_action_outcome(action, result)
    def test_structured_partial_outcomes_preserve_success_and_unlock_the_next_stage(self):
        cases = [
            ("collect", {"total": 3, "platform_stats": {"pubmed": 3, "arxiv": 0}, "platform_errors": {"arxiv": "timeout"}}, "collection", "screening", {"succeeded": 1, "failed": 1, "collected": 3}),
            ("download-pdfs", {"success": 2, "failed": 1}, "retrieval", "extraction", {"succeeded": 2, "failed": 1}),
            ("run-extraction", {"processed": 2, "errors": 1}, "extraction", "categorization", {"succeeded": 2, "failed": 1}),
        ]
        for action, result, stage_name, next_stage, expected_counts in cases:
            with self.subTest(action=action), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp); initialize_workflow_state(project)
                prerequisites = {"collect": (), "download-pdfs": ("collect", "screen"), "run-extraction": ("collect", "screen", "download-pdfs")}[action]
                for prerequisite in prerequisites:
                    start_action(project, prerequisite); complete_action(project, prerequisite, _terminal_result(prerequisite))
                start_action(project, action)
                state = complete_action(project, action, result)
                stage = state["stages"][stage_name]
                self.assertEqual(stage["status"], "partial")
                self.assertEqual(stage["counts"], expected_counts)
                self.assertEqual(stage["last_valid"]["status"], "partial")
                self.assertEqual(state["stages"][next_stage]["status"], "ready")

    def test_structured_zero_success_failures_block_the_next_stage(self):
        cases = [
            ("collect", {"total": 0, "platform_stats": {"pubmed": 0}, "platform_errors": {"pubmed": "timeout"}}, "collection", "screening"),
            ("download-pdfs", {"success": 0, "failed": 2}, "retrieval", "extraction"),
            ("run-extraction", {"processed": 0, "errors": 2}, "extraction", "categorization"),
        ]
        for action, result, stage_name, next_stage in cases:
            with self.subTest(action=action), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp); initialize_workflow_state(project)
                prerequisites = {"collect": (), "download-pdfs": ("collect", "screen"), "run-extraction": ("collect", "screen", "download-pdfs")}[action]
                for prerequisite in prerequisites:
                    start_action(project, prerequisite); complete_action(project, prerequisite, _terminal_result(prerequisite))
                start_action(project, action)
                state = complete_action(project, action, result)
                self.assertEqual(state["stages"][stage_name]["status"], "failed")
                self.assertEqual(state["stages"][stage_name]["last_valid"], None)
                self.assertEqual(state["stages"][next_stage]["status"], "not_started")

    def test_partial_prerequisite_is_valid_because_successful_outputs_are_authoritative(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp); initialize_workflow_state(project)
            start_action(project, "collect")
            complete_action(project, "collect", {"total": 1, "platform_stats": {"pubmed": 1, "arxiv": 0}, "platform_errors": {"arxiv": "timeout"}})
            state = start_action(project, "screen")
        self.assertEqual(state["stages"]["screening"]["status"], "running")

    def test_collection_partial_is_based_on_source_outcomes_even_when_successful_source_returns_zero_papers(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp); initialize_workflow_state(project); start_action(project, "collect")
            state = complete_action(project, "collect", {"total": 0, "platform_stats": {"pubmed": 0, "arxiv": 0}, "platform_errors": {"arxiv": "timeout"}})
        stage = state["stages"]["collection"]
        self.assertEqual(stage["status"], "partial")
        self.assertEqual(stage["counts"], {"succeeded": 1, "failed": 1, "collected": 0})

    def test_real_nested_stats_variants_drive_the_same_deterministic_outcomes(self):
        from reviewpilot_core.workflow_state import structured_action_outcome
        self.assertEqual(structured_action_outcome("collect", {"stats": {"platform_stats": {"pubmed": 0, "arxiv": 0}, "platform_errors": {"arxiv": "timeout"}, "total_papers": 0}}), ("partial", {"succeeded": 1, "failed": 1, "collected": 0}))
        self.assertEqual(structured_action_outcome("download-pdfs", {"stats": {"success": 2, "failed": 1}}), ("partial", {"succeeded": 2, "failed": 1}))
        self.assertEqual(structured_action_outcome("run-extraction", {"stats": {"processed": 0, "errors": 2}}), ("failed", {"succeeded": 0, "failed": 2}))

    def test_collection_success_sources_exclude_sources_that_also_report_errors(self):
        from reviewpilot_core.workflow_state import structured_action_outcome
        self.assertEqual(structured_action_outcome("collect", {"total": 0, "platform_stats": {"openalex": 0}, "platform_errors": {"openalex": "503"}})[0], "failed")
        self.assertEqual(structured_action_outcome("collect", {"total": 0, "platform_stats": {"pubmed": 0, "openalex": 0}, "platform_errors": {"openalex": "503"}})[0], "partial")

    def test_present_malformed_structured_contract_fields_fail_loud(self):
        from reviewpilot_core.workflow_state import structured_action_outcome
        cases = [("collect", {"platform_stats": "bad", "platform_errors": {"pubmed": "503"}}), ("collect", {"platform_stats": {"pubmed": -1}, "platform_errors": {}}), ("collect", {"platform_stats": {"pubmed": True}, "platform_errors": {}}), ("collect", {"platform_stats": {}, "platform_errors": []}), ("download-pdfs", {"success": 0, "failed": "2"}), ("download-pdfs", {"success": False, "failed": 1}), ("run-extraction", {"processed": -1, "errors": 2}), ("run-extraction", {"processed": 0, "errors": ["bad"]}), ("run-extraction", {"stats": "bad"})]
        for action, result in cases:
            with self.subTest(action=action, result=result), self.assertRaises(ValueError): structured_action_outcome(action, result)
        with self.assertRaises(ValueError): structured_action_outcome("download-pdfs", {})

    def test_terminal_rerun_invalidates_existing_downstream_and_failed_rerun_hides_current_output(self):
        for rerun_action, stage_name in (("collect", "collection"), ("download-pdfs", "retrieval"), ("run-extraction", "extraction")):
            for terminal_result, expected_status in (({"success": 1, "failed": 1}, "partial"), ({"success": 0, "failed": 1}, "failed"), ({"success": 2, "failed": 0}, "completed")):
                with self.subTest(action=rerun_action, status=expected_status), tempfile.TemporaryDirectory() as tmp:
                    project = Path(tmp); initialize_workflow_state(project)
                    for action in ("collect", "screen", "download-pdfs", "run-extraction", "categorize"): start_action(project, action); complete_action(project, action, _terminal_result(action))
                    start_action(project, rerun_action); result = dict(terminal_result)
                    if rerun_action == "collect": result = {"total": terminal_result["success"], "platform_stats": {**({"pubmed": terminal_result["success"]} if terminal_result["success"] else {}), **({"arxiv": 0} if terminal_result["failed"] else {})}, "platform_errors": ({"arxiv": "503"} if terminal_result["failed"] else {})}
                    elif rerun_action == "run-extraction": result = {"processed": terminal_result["success"], "errors": terminal_result["failed"]}
                    state = complete_action(project, rerun_action, result); index = STAGE_NAMES.index(stage_name)
                    self.assertEqual(state["stages"][stage_name]["status"], expected_status)
                    self.assertFalse(state["stages"][stage_name]["stale"])
                    self.assertTrue(all(state["stages"][name]["stale"] for name in STAGE_NAMES[index + 1:]))
                    self.assertIsNotNone(state["stages"][stage_name]["last_valid"])

    def test_initial_structured_failure_has_no_stale_last_valid_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp); initialize_workflow_state(project); start_action(project, "collect")
            stage = complete_action(project, "collect", {"total": 0, "platform_stats": {"openalex": 0}, "platform_errors": {"openalex": "503"}})["stages"]["collection"]
        self.assertEqual(stage["status"], "failed"); self.assertFalse(stage["stale"]); self.assertIsNone(stage["last_valid"])

    def test_escaped_failure_stales_prior_current_and_downstream_outputs(self):
        from reviewpilot_core.workflow_state import fail_action
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp); initialize_workflow_state(project)
            for action in ("collect","screen","download-pdfs","run-extraction"): start_action(project,action); complete_action(project,action,_terminal_result(action))
            start_action(project,"download-pdfs"); state=fail_action(project,"download-pdfs",ValueError("bad"))
        self.assertTrue(state["stages"]["retrieval"]["stale"]); self.assertTrue(state["stages"]["extraction"]["stale"])
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
                "collect": "collection", "screen": "screening", "download-pdfs": "retrieval", "retry-failed-downloads": "retrieval",
                "generate-schema": "extraction", "regenerate-schema": "extraction", "finalize-schema": "extraction", "edit-schema": "extraction",
                "run-extraction": "extraction", "finalize-and-run-extraction": "extraction", "suggest-categories": "categorization", "categorize": "categorization",
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            initialize_workflow_state(project)
            for prerequisite_action in ("collect", "screen"):
                start_action(project, prerequisite_action)
                complete_action(project, prerequisite_action, _terminal_result(prerequisite_action))
            start_action(project, "generate-schema")
            state = complete_action(project, "generate-schema", {"field_count": 7})
            self.assertEqual(state["stages"]["extraction"]["status"], "ready")
            self.assertEqual(state["stages"]["extraction"]["counts"], {"field_count": 7})

    def test_composite_schema_actions_have_ready_and_terminal_extraction_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            initialize_workflow_state(project)
            for action in ("collect", "screen", "download-pdfs"):
                start_action(project, action)
                complete_action(project, action, _terminal_result(action))
            start_action(project, "regenerate-schema")
            ready = complete_action(project, "regenerate-schema", {"field_count": 4})
            self.assertEqual(ready["stages"]["extraction"]["status"], "ready")
            start_action(project, "finalize-and-run-extraction")
            done = complete_action(project, "finalize-and-run-extraction", {"processed": 2, "errors": 1})

        self.assertEqual(done["stages"]["extraction"]["status"], "partial")
        self.assertEqual(done["stages"]["extraction"]["counts"], {"succeeded": 2, "failed": 1})

    def test_failed_attempt_preserves_last_valid_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            initialize_workflow_state(project)
            start_action(project, "collect")
            complete_action(project, "collect", {"total_papers": 12, "platform_stats": {"pubmed": 12}, "platform_errors": {}})
            start_action(project, "collect")
            from reviewpilot_core.workflow_state import fail_action
            state = fail_action(project, "collect", RuntimeError("/Users/private/secret"))

        stage = state["stages"]["collection"]
        self.assertEqual(stage["status"], "failed")
        self.assertEqual(stage["attempt"], 2)
        self.assertEqual(stage["last_valid"]["counts"], {"succeeded": 1, "failed": 0, "collected": 12})
        self.assertNotIn("private", stage["error"])

    def test_ready_only_action_preserves_stale_terminal_last_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            initialize_workflow_state(project)
            for action in ("collect", "screen", "download-pdfs", "run-extraction"):
                start_action(project, action)
                complete_action(project, action, _terminal_result(action))
            from reviewpilot_core.workflow_state import mark_stages_stale
            before = mark_stages_stale(project, ["extraction"])["stages"]["extraction"]["last_valid"]
            start_action(project, "generate-schema")
            stage = complete_action(project, "generate-schema", {"field_count": 4})["stages"]["extraction"]
        self.assertTrue(stage["stale"])
        self.assertEqual(stage["last_valid"], before)

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
