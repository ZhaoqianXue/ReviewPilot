import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.lead_agent import LeadAgent
from agents.search_condition_agent import SearchConditionAgent
from starlette.testclient import TestClient
from reviewpilot_core.agent_memory import (
    CrossProjectMemoryService,
    LongTermMemoryStore,
    MemoryStoreError,
)
import web_app


class LongTermMemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temporary_directory.name) / "output"
        self.output_root.mkdir()
        self.store = LongTermMemoryStore(self.output_root)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def search_payload(self, query="cancer screening"):
        return {
            "search_terms": query,
            "platforms": ["pubmed", "openalex"],
            "date_range": {"start": "2020", "end": "2026"},
            "source_limits": {"pubmed": 10, "openalex": 10},
            "keywords": ["screening"],
        }

    def promote_search(self, project_id="source-project", revision="r1", query="cancer screening"):
        return self.store.promote(
            kind="search_setup",
            project_id=project_id,
            payload=self.search_payload(query),
            source_artifact="search_conditions.json",
            source_revision=revision,
            domain="medicine",
            topic="cancer screening",
        )

    def test_settings_default_on_and_persist(self):
        self.assertTrue(self.store.is_enabled())
        self.assertFalse(self.store.set_enabled(False))
        self.assertFalse(LongTermMemoryStore(self.output_root).is_enabled())
        self.assertTrue(self.store.set_enabled(True))

    def test_promote_is_idempotent_and_retrieval_excludes_current_project(self):
        first = self.promote_search()
        second = self.promote_search()
        self.assertEqual(first, second)

        records = self.store.retrieve(
            kinds=["search_setup"],
            project_id="target-project",
            domain="medicine",
            topic="cancer screening",
        )
        self.assertEqual([record.memory_id for record in records], [first])
        self.assertEqual(records[0].source_project_id, "source-project")
        self.assertEqual(records[0].payload["search_terms"], "cancer screening")
        self.assertEqual(
            self.store.retrieve(
                kinds=["search_setup"],
                project_id="source-project",
                domain="medicine",
                topic="cancer screening",
            ),
            [],
        )

    def test_new_revision_supersedes_the_old_project_memory(self):
        self.promote_search(revision="r1", query="old cancer query")
        newest = self.promote_search(revision="r2", query="new cancer query")
        records = self.store.retrieve(
            kinds=["search_setup"],
            project_id="target-project",
            domain="medicine",
            topic="cancer query",
        )
        self.assertEqual([record.memory_id for record in records], [newest])
        self.assertEqual(records[0].payload["search_terms"], "new cancer query")

    def test_disable_preserves_rows_and_clear_preserves_setting(self):
        self.promote_search()
        self.store.set_enabled(False)
        self.assertEqual(
            self.store.retrieve(
                kinds=["search_setup"],
                project_id="target-project",
                domain="medicine",
                topic="cancer screening",
            ),
            [],
        )
        self.store.clear()
        self.assertFalse(self.store.is_enabled())
        self.store.set_enabled(True)
        self.assertEqual(
            self.store.retrieve(
                kinds=["search_setup"],
                project_id="target-project",
                domain="medicine",
                topic="cancer screening",
            ),
            [],
        )

    def test_rejects_unknown_fields_and_unsafe_artifact_paths(self):
        with self.assertRaises(ValueError):
            self.store.promote(
                kind="search_setup",
                project_id="source-project",
                payload={**self.search_payload(), "raw_paper_text": "forbidden"},
                source_artifact="search_conditions.json",
                source_revision="r1",
            )
        with self.assertRaises(ValueError):
            self.store.promote(
                kind="search_setup",
                project_id="source-project",
                payload=self.search_payload(),
                source_artifact="../secret.json",
                source_revision="r1",
            )

    def test_rejects_symlinked_memory_directory(self):
        other = Path(self.temporary_directory.name) / "other"
        other.mkdir()
        memory_dir = self.output_root / ".agent_memory"
        memory_dir.symlink_to(other, target_is_directory=True)
        with self.assertRaises(MemoryStoreError):
            LongTermMemoryStore(self.output_root).is_enabled()

    def test_concurrent_promotions_are_serialized_by_sqlite(self):
        errors = []

        def promote(index):
            try:
                LongTermMemoryStore(self.output_root).promote(
                    kind="search_setup",
                    project_id=f"project-{index}",
                    payload=self.search_payload(f"cancer screening {index}"),
                    source_artifact="search_conditions.json",
                    source_revision="r1",
                    domain="medicine",
                    topic="cancer screening",
                )
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=promote, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        records = self.store.retrieve(
            kinds=["search_setup"],
            project_id="target-project",
            domain="medicine",
            topic="cancer screening",
        )
        self.assertEqual(len(records), 5)


class CrossProjectMemoryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temporary_directory.name) / "output"
        self.output_root.mkdir()
        self.service = CrossProjectMemoryService(self.output_root)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_service_renders_bounded_advisory_context(self):
        promoted = self.service.promote(
            kind="extraction_schema",
            project_id="source-project",
            payload={
                "fields": [
                    {
                        "name": "study_design",
                        "type": "Text",
                        "description": "Study design",
                        "required": False,
                        "example": "randomized trial",
                    }
                ]
            },
            source_artifact="extraction/extraction_schema.json",
            source_revision="schema-r1",
            domain="medicine",
            topic="cancer",
        )
        self.assertTrue(promoted)
        context = self.service.retrieve_context(
            kinds=["extraction_schema"],
            project_id="target-project",
            domain="medicine",
            topic="cancer",
        )
        self.assertIn("Advisory memory from earlier projects", context)
        self.assertIn("study_design", context)
        self.assertIn("source-project", context)
        self.assertLessEqual(len(context), 6000)

    def test_retrieve_failure_degrades_without_path_in_diagnostic(self):
        with patch.object(self.service.store, "retrieve", side_effect=MemoryStoreError("/private/secret/memory.sqlite3")):
            with self.assertLogs("reviewpilot.agent_memory", level="WARNING") as captured:
                context = self.service.retrieve_context(
                    kinds=["search_setup"],
                    project_id="target-project",
                    domain="medicine",
                    topic="cancer",
                )
        self.assertEqual(context, "")
        combined = "\n".join(captured.output)
        self.assertIn("long_term_read_failed", combined)
        self.assertNotIn("/private/secret", combined)

    def test_all_four_memory_kinds_can_be_promoted_and_retrieved(self):
        cases = {
            "search_setup": {"search_terms": "cancer", "platforms": ["pubmed"]},
            "screening_profile": {
                "system_prompt": "Screen for cancer intervention studies",
                "user_prompt_template": "{title}\n{abstract}",
            },
            "extraction_schema": {
                "fields": [
                    {
                        "name": "population",
                        "type": "Text",
                        "description": "Study population",
                        "required": False,
                        "example": "adults",
                    }
                ]
            },
            "categorization_profile": {
                "field": "study_design",
                "mode": "single",
                "categories": ["Trials", "Observational"],
                "category_descriptions": {"Trials": "Experimental studies"},
            },
        }
        for index, (kind, payload) in enumerate(cases.items()):
            with self.subTest(kind=kind):
                self.assertTrue(
                    self.service.promote(
                        kind=kind,
                        project_id=f"source-{index}",
                        payload=payload,
                        source_artifact=f"artifacts/{kind}.json",
                        source_revision="r1",
                        domain="medicine",
                        topic="cancer",
                    )
                )
                context = self.service.retrieve_context(
                    kinds=[kind],
                    project_id="target-project",
                    domain="medicine",
                    topic="cancer",
                )
                self.assertIn(kind, context)
                self.assertIn(f"source-{index}", context)


class _RecordingWorkflowAdapter:
    def __init__(self, *, write_search_setup=False):
        self.calls = []
        self.write_search_setup = write_search_setup

    def run(self, action, output_root, project_id, llm_query=None, input_data=None):
        self.calls.append((action, Path(output_root), project_id, dict(input_data or {})))
        if action == "save-search-setup":
            config = {key: value for key, value in dict(input_data or {}).items() if key != "memory_context"}
            project_path = Path(output_root) / project_id
            if self.write_search_setup:
                project_path.mkdir(parents=True, exist_ok=True)
                (project_path / "search_conditions.json").write_text(json.dumps(config), encoding="utf-8")
            return {"status": "search_setup_done", "search_conditions": config}
        return {"status": "ok"}

    def contract_for(self, action):
        return SimpleNamespace(agent_name="FakeAgent", stage="fake", model="fake-model")


class LeadAgentMemoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temporary_directory.name) / "output"
        self.output_root.mkdir()
        self.project_path = self.output_root / "target-project"
        self.project_path.mkdir()
        self.config = {
            "project_name": "Target review",
            "description": "Cancer screening interventions",
            "search_terms": "cancer AND screening",
            "platforms": ["pubmed"],
            "date_range": {},
            "primary_topic": "cancer screening",
            "domain": "medicine",
        }
        (self.project_path / "search_conditions.json").write_text(json.dumps(self.config), encoding="utf-8")
        self.memory = CrossProjectMemoryService(self.output_root)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write_chat(self, rows):
        chat_dir = self.project_path / "chat"
        chat_dir.mkdir(exist_ok=True)
        with (chat_dir / "messages.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

    def test_project_chat_prompt_uses_complete_stored_history_and_current_input_once(self):
        self.write_chat(
            [
                {"role": "u", "text": "First user turn"},
                {"role": "a", "text": "First assistant turn"},
                {"role": "tool", "text": "ignored"},
                {"role": "u", "text": ""},
            ]
        )
        captured = {}

        def fake_llm(**kwargs):
            captured.update(kwargs)
            return json.dumps({"reply": "Second assistant turn"}), {}

        agent = LeadAgent(self.output_root, llm_query=fake_llm, memory_service=self.memory)
        agent.reply_to_project_message("target-project", "Second user turn")
        prompt = captured["text_prompt"]
        self.assertIn("First user turn", prompt)
        self.assertIn("First assistant turn", prompt)
        self.assertNotIn("ignored", prompt)
        self.assertEqual(prompt.count("Second user turn"), 1)
        self.assertNotIn("Session Memory", prompt)
        self.assertFalse((self.project_path / "chat" / "session_state.json").exists())

    def test_schema_chat_command_uses_the_same_stored_history(self):
        self.write_chat([{"role": "u", "text": "Keep the population field"}, {"role": "a", "text": "Understood"}])
        captured = {}

        def fake_llm(**kwargs):
            captured.update(kwargs)
            return json.dumps({"action": "answer_question", "args": {"response": "Yes"}}), {}

        agent = LeadAgent(self.output_root, llm_query=fake_llm, memory_service=self.memory)
        agent._schema_command(
            self.config,
            {"fields": [{"name": "population", "type": "Text"}]},
            "Should we retain it?",
            project_path=self.project_path,
        )
        prompt = captured["text_prompt"]
        self.assertIn("Keep the population field", prompt)
        self.assertIn("Understood", prompt)
        self.assertEqual(prompt.count("Should we retain it?"), 1)

    def test_legacy_cross_project_context_is_not_automatically_injected(self):
        self.memory.promote(
            kind="extraction_schema",
            project_id="source-project",
            payload={
                "fields": [
                    {
                        "name": "study_design",
                        "type": "Text",
                        "description": "Study design",
                        "required": False,
                        "example": "cohort",
                    }
                ]
            },
            source_artifact="extraction/extraction_schema.json",
            source_revision="r1",
            domain="medicine",
            topic="cancer screening",
        )
        adapter = _RecordingWorkflowAdapter()
        agent = LeadAgent(self.output_root, workflow_adapter=adapter, memory_service=self.memory)
        agent._call_workflow_action("generate-schema", "target-project", input_data={"current": "value"})
        sent = adapter.calls[-1][3]
        self.assertEqual(sent["current"], "value")
        self.assertNotIn("memory_context", sent)

    def test_verified_search_setup_is_snapshotted_locally_without_cross_project_promotion(self):
        source_path = self.output_root / "source-project"
        adapter = _RecordingWorkflowAdapter(write_search_setup=True)
        agent = LeadAgent(self.output_root, workflow_adapter=adapter, memory_service=self.memory)
        result = agent.save_search_setup("source-project", self.config)
        self.assertEqual(result.data, {})
        self.assertNotIn("memory", result.to_dict())
        context = self.memory.retrieve_context(
            kinds=["search_setup"],
            project_id="target-project",
            domain="medicine",
            topic="cancer screening",
        )
        self.assertEqual(context, "")
        snapshot = json.loads((source_path / "memory/confirmed_decisions.json").read_text())
        self.assertEqual(snapshot["search_setup"]["configuration"]["search_terms"], "cancer AND screening")

    def test_failed_search_setup_verification_does_not_promote(self):
        adapter = _RecordingWorkflowAdapter(write_search_setup=False)
        agent = LeadAgent(self.output_root, workflow_adapter=adapter, memory_service=self.memory)
        with self.assertRaises(ValueError):
            agent.save_search_setup("source-project", self.config)
        context = self.memory.retrieve_context(
            kinds=["search_setup"],
            project_id="target-project",
            domain="medicine",
            topic="cancer screening",
        )
        self.assertEqual(context, "")

    def test_advisory_search_memory_is_not_persisted_as_project_state(self):
        config = {
            **self.config,
            "project_path": str(self.output_root / "new-project"),
            "max_results": 5,
            "source_limits": {"pubmed": 5},
            "memory_context": "Advisory memory from source-project",
        }
        result = SearchConditionAgent(output_dir=str(self.output_root)).run(config)
        persisted = json.loads((self.output_root / "new-project" / "search_conditions.json").read_text(encoding="utf-8"))
        self.assertNotIn("memory_context", result)
        self.assertNotIn("memory_context", persisted)


class MemoryWebContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temporary_directory.name) / "output"
        self.output_root.mkdir()
        self.output_patch = patch.object(web_app, "OUTPUT_ROOT", self.output_root)
        self.output_patch.start()
        self.client = TestClient(web_app.create_app())

    def tearDown(self):
        self.output_patch.stop()
        self.temporary_directory.cleanup()

    def test_settings_api_returns_only_enabled_boolean(self):
        response = self.client.get("/memory/settings")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"cross_project_memory_enabled": False})

        response = self.client.put(
            "/memory/settings",
            json={"cross_project_memory_enabled": False},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"cross_project_memory_enabled": False})
        self.assertEqual(self.client.get("/memory/settings").json(), {"cross_project_memory_enabled": False})

    def test_settings_api_rejects_extra_or_non_boolean_fields(self):
        for payload in (
            {"cross_project_memory_enabled": "yes"},
            {"cross_project_memory_enabled": True, "items": []},
            {},
        ):
            with self.subTest(payload=payload):
                response = self.client.put("/memory/settings", json=payload)
                self.assertEqual(response.status_code, 400)

    def test_clear_returns_no_count_and_preserves_enabled_setting(self):
        service = CrossProjectMemoryService(self.output_root)
        service.promote(
            kind="search_setup",
            project_id="source-project",
            payload={"search_terms": "cancer", "platforms": ["pubmed"]},
            source_artifact="search_conditions.json",
            source_revision="r1",
            domain="medicine",
            topic="cancer",
        )
        self.client.put("/memory/settings", json={"cross_project_memory_enabled": False})
        response = self.client.delete("/memory")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"cleared": True})
        self.assertEqual(self.client.get("/memory/settings").json(), {"cross_project_memory_enabled": False})

    def test_store_failure_returns_generic_error_without_path(self):
        other = Path(self.temporary_directory.name) / "other"
        other.mkdir()
        (self.output_root / ".agent_memory").symlink_to(other, target_is_directory=True)
        response = self.client.get("/memory/settings")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"cross_project_memory_enabled": False})
        self.assertNotIn(str(other), response.text)

    def test_frontend_exposes_only_minimal_memory_copy(self):
        source = (Path(__file__).parents[1] / "frontend" / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-act="open-memory"', source)
        self.assertIn('Reuse project configuration', source)
        self.assertIn('Import as draft', source)
        self.assertNotIn('data-act="toggle-memory"', source)
        self.assertNotIn('Reuse validated memory from previous projects.', source)

    def test_production_lead_imports_only_the_accepted_memory_runtime(self):
        source = (Path(__file__).parents[1] / "agents" / "lead_agent.py").read_text(encoding="utf-8")
        self.assertIn("reviewpilot_core.agent_memory", source)
        self.assertNotIn("utils.agent_memory", source)
        self.assertNotIn("utils.memory", source)


if __name__ == "__main__":
    unittest.main()
