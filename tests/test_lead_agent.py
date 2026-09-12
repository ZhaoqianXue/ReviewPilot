import json
import tempfile
import unittest
from pathlib import Path

from agents.lead_agent import LeadAgent
from reviewpilot_core.extraction_schema import finalize_schema, save_schema_draft
from reviewpilot_core.project_store import read_jsonl
from reviewpilot_core.state_projection import build_rp_data
from reviewpilot_core.workflow_state import complete_action, initialize_workflow_state, start_action


def ledger_through(project: Path, stage: str) -> None:
    initialize_workflow_state(project)
    actions = ["collect", "screen", "download-pdfs", "run-extraction", "categorize"]
    stages = ["collection", "screening", "retrieval", "extraction", "categorization"]
    for action in actions[: stages.index(stage) + 1]:
        start_action(project, action)
        result = {
            "collect": {"total": 1, "platform_stats": {"pubmed": 1}, "platform_errors": {}},
            "download-pdfs": {"success": 1, "failed": 0},
            "run-extraction": {"processed": 1, "errors": 0},
        }.get(action, {})
        complete_action(project, action, result)


class LeadAgentTests(unittest.TestCase):
    def test_finalize_and_run_extraction_finalizes_then_invokes_existing_extraction_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "demo"
            ledger_through(project, "retrieval")
            (project / "search_conditions.json").write_text(json.dumps({"project_name": "Demo"}), encoding="utf-8")
            (project / "filtered").mkdir(parents=True)
            (project / "filtered" / "included_papers.jsonl").write_text(json.dumps({"id": "p1", "title": "Paper A"}) + "\n", encoding="utf-8")
            (project / "filtered" / "screening_stats.json").write_text(json.dumps({"included": 1}), encoding="utf-8")
            (project / "pdfs").mkdir(parents=True)
            (project / "pdfs" / "download_report.json").write_text(json.dumps({"success": 1, "failed": 0}), encoding="utf-8")
            save_schema_draft(project, {"fields": [{"name": "methods", "type": "Text"}]})
            (project / "prompts").mkdir(parents=True)
            (project / "prompts" / "extraction_prompt.json").write_text(json.dumps({"extraction_prompt": "draft"}), encoding="utf-8")
            (project / "extraction" / "extraction_prompt.json").write_text(json.dumps({"extraction_prompt": "draft"}), encoding="utf-8")
            calls = []

            class Adapter:
                class Contract:
                    agent_name = "ExtractionAgent"; stage = "extraction"; model = "gpt-5.4-mini"
                def contract_for(self, action): return self.Contract()
                def run(self, action, output_root, project_id, **kwargs):
                    calls.append(action)
                    (project / "extraction" / "extraction_results.jsonl").write_text(json.dumps({"paper_id": "p1", "methods": "Survey"}) + "\n", encoding="utf-8")
                    return {"processed": 1, "errors": 0}

            result = LeadAgent(root, workflow_adapter=Adapter(), llm_query=lambda **kwargs: (json.dumps({"reply": "Extraction complete."}), {})).handle_message(
                project_id="demo", action="finalize-and-run-extraction"
            )
            marker_exists = (project / "extraction" / "schema_finalized.json").exists()

        self.assertEqual(calls, ["run-extraction"])
        self.assertTrue(marker_exists)
        self.assertEqual(result.data["processed"], 1)

    def test_regenerate_schema_reopens_finalized_schema_and_invokes_schema_generator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / "demo"
            ledger_through(project, "screening")
            (project / "search_conditions.json").write_text(json.dumps({"project_name": "Demo"}), encoding="utf-8")
            (project / "filtered").mkdir(parents=True)
            (project / "filtered" / "included_papers.jsonl").write_text(json.dumps({"id": "p1", "title": "Paper A"}) + "\n", encoding="utf-8")
            (project / "filtered" / "screening_stats.json").write_text(json.dumps({"included": 1}), encoding="utf-8")
            save_schema_draft(project, {"fields": [{"name": "old", "type": "Text"}]})
            finalize_schema(project)
            calls = []

            class Adapter:
                class Contract:
                    agent_name = "PromptAgent"; stage = "prompt_extraction"; model = "gpt-5.4-mini"
                def contract_for(self, action): return self.Contract()
                def run(self, action, output_root, project_id, **kwargs):
                    calls.append(action)
                    save_schema_draft(project, {"fields": [{"name": "new", "type": "Text"}]})
                    (project / "prompts").mkdir(parents=True, exist_ok=True)
                    (project / "prompts" / "extraction_prompt.json").write_text(json.dumps({"extraction_prompt": "new"}), encoding="utf-8")
                    (project / "extraction" / "extraction_prompt.json").write_text(json.dumps({"extraction_prompt": "new"}), encoding="utf-8")
                    return {"field_count": 1}

            result = LeadAgent(root, workflow_adapter=Adapter()).handle_message(project_id="demo", action="regenerate-schema")
            marker_exists = (project / "extraction" / "schema_finalized.json").exists()

        self.assertEqual(calls, ["generate-schema"])
        self.assertFalse(marker_exists)
        self.assertEqual(result.data["field_count"], 1)
    def test_partial_and_failed_replies_are_deterministic_and_do_not_ask_the_llm_to_classify_outcomes(self):
        def forbidden_llm(**_kwargs):
            raise AssertionError("outcome replies must not use the LLM")
        agent = LeadAgent(Path("/tmp"), llm_query=forbidden_llm)
        partial = agent._stage_reply("download", {"success": 2, "failed": 1, "outcome": "partial"}, action="download-pdfs")
        failed = agent._stage_reply("extraction", {"processed": 0, "errors": 2, "outcome": "failed"}, action="run-extraction")
        self.assertIn("2 available", partial)
        self.assertIn("1 failed", partial)
        self.assertIn("Next action: Information Extraction", partial)
        self.assertIn("recovery step", partial)
        self.assertIn("0 processed", failed)
        self.assertIn("2 errors", failed)
        self.assertIn("blocked", failed.lower())
        self.assertNotIn("Next action: Categorization", failed)
    def test_completed_stage_requirement_uses_ledger_before_artifact_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "collected").mkdir(parents=True)
            (project / "collected" / "summary.json").write_text(json.dumps({"total_papers": 3, "platform_stats": {"pubmed": 3}}), encoding="utf-8")
            initialize_workflow_state(project)

            with self.assertRaisesRegex(ValueError, "requires completed stage"):
                LeadAgent(Path(tmp))._require_completed_stage(project, "screen", "collection")

    def test_save_search_setup_delegates_to_search_condition_contract_and_verifies_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            calls = []

            class FakeWorkflowAdapter:
                class Contract:
                    agent_name = "SearchConditionAgent"
                    stage = "search_conditions"
                    model = "gpt-5.4-mini"

                def contract_for(self, action):
                    return self.Contract()

                def run(self, action, output_root, project_id, llm_query=None, input_data=None):
                    calls.append((action, Path(output_root), project_id, llm_query, dict(input_data or {})))
                    project_path = Path(output_root) / project_id
                    project_path.mkdir(parents=True, exist_ok=True)
                    search_conditions = {
                        **(input_data or {}),
                        "project_path": str(project_path),
                        "project_name": "Lead Agent Review",
                        "search_terms": "AI AND medicine",
                        "platforms": ["pubmed"],
                    }
                    (project_path / "search_conditions.json").write_text(
                        json.dumps(search_conditions),
                        encoding="utf-8",
                    )
                    return {"status": "search_setup_done", "search_conditions": search_conditions}

            lead_agent = LeadAgent(output_root, workflow_adapter=FakeWorkflowAdapter())
            result = lead_agent.save_search_setup(
                "lead-agent-review",
                {
                    "project_name": "Lead Agent Review",
                    "description": "Review AI in medicine",
                    "search_terms": "AI AND medicine",
                    "platforms": ["pubmed"],
                    "primary_topic": "AI",
                },
            )

            written = json.loads((output_root / "lead-agent-review" / "search_conditions.json").read_text(encoding="utf-8"))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.stage, "search_conditions")
        self.assertEqual(result.reply, "Search Setup is ready. Review it in the canvas, then run collection when you are ready.")
        self.assertEqual(result.search_conditions["project_name"], "Lead Agent Review")
        self.assertEqual(result.artifacts, [str(output_root / "lead-agent-review" / "search_conditions.json")])
        self.assertEqual(calls[0][0], "save-search-setup")
        self.assertEqual(calls[0][1], output_root)
        self.assertEqual(calls[0][2], "lead-agent-review")
        self.assertEqual(calls[0][4]["project_path"], str(output_root / "lead-agent-review"))
        self.assertEqual(written["search_terms"], "AI AND medicine")

    def test_reply_to_project_message_calls_llm_and_persists_chat_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps(
                    {
                        "project_name": "Demo",
                        "description": "Review LLM in medicine",
                        "search_terms": "LLM AND medicine",
                        "platforms": ["pubmed"],
                        "model": "gpt-5.4-mini",
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            def fake_llm_query(*, text_prompt, system_prompt, model, provider):
                calls.append((text_prompt, system_prompt, model, provider))
                return (json.dumps({"reply": "LLM answer for this project."}), {"input_tokens": 10})

            lead_agent = LeadAgent(output_root, llm_query=fake_llm_query)
            reply = lead_agent.reply_to_project_message("demo", "What should I do next?")
            chat_lines = (project_dir / "chat" / "messages.jsonl").read_text(encoding="utf-8").strip().splitlines()
            chat_rows = [json.loads(line) for line in chat_lines]

        self.assertEqual(reply, "LLM answer for this project.")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], "gpt-5.4-mini")
        self.assertEqual([row["role"] for row in chat_rows], ["u", "a"])
        self.assertEqual(chat_rows[0]["text"], "What should I do next?")
        self.assertEqual(chat_rows[1]["text"], "LLM answer for this project.")

    def test_handle_message_routes_chat_through_unified_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps(
                    {
                        "project_name": "Demo",
                        "description": "Review LLM in medicine",
                        "search_terms": "LLM AND medicine",
                        "platforms": ["pubmed"],
                        "model": "gpt-5.4-mini",
                    }
                ),
                encoding="utf-8",
            )

            def fake_llm_query(*, text_prompt, system_prompt, model, provider):
                return (json.dumps({"reply": "Unified Lead Agent reply."}), {"input_tokens": 12})

            result = LeadAgent(output_root, llm_query=fake_llm_query).handle_message(
                project_id="demo",
                message="What should I do next?",
            )

        self.assertEqual(result.stage, "search_conditions")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.reply, "Unified Lead Agent reply.")
        self.assertEqual(result.next_actions, ["edit_search", "run_collection"])

    def test_generic_project_chat_persists_visible_workflow_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "Demo", "description": "Review LLMs", "search_terms": "LLM", "platforms": ["openalex"]}),
                encoding="utf-8",
            )

            def fake_llm_query(**_kwargs):
                return (json.dumps({"reply": "Analysis answer."}), {})

            LeadAgent(output_root, llm_query=fake_llm_query).handle_message(
                project_id="demo",
                message="Summarize progress",
                context_step="categorize",
            )
            chat_rows = read_jsonl(project_dir / "chat" / "messages.jsonl")

        self.assertEqual([row["step"] for row in chat_rows], [5, 5])

    def test_extraction_schema_chat_adds_field_to_draft_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            (project_dir / "extraction").mkdir(parents=True)
            (project_dir / "pdfs").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps(
                    {
                        "project_name": "Demo",
                        "description": "Review LLM systems in medicine",
                        "primary_topic": "LLMs",
                        "domain": "medicine",
                        "search_terms": "LLM AND medicine",
                        "platforms": ["openalex"],
                        "model": "gpt-5.4-mini",
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "pdfs" / "download_report.json").write_text(json.dumps({"success": 1, "failed": 0}), encoding="utf-8")
            (project_dir / "extraction" / "extraction_schema_draft.json").write_text(
                json.dumps({"fields": [{"name": "methods", "type": "Text", "description": "Methods", "required": False, "example": "RCT"}]}),
                encoding="utf-8",
            )

            def fake_llm_query(*, text_prompt, system_prompt, model, provider):
                self.assertIn("translate a user's extraction-schema request", system_prompt)
                return (
                    json.dumps(
                        {
                            "action": "add_field",
                            "args": {
                                "name": "sample_size",
                                "description": "Number of participants or records",
                                "example": "128 patients",
                            },
                        }
                    ),
                    {},
                )

            result = LeadAgent(output_root, llm_query=fake_llm_query).handle_message(
                project_id="demo",
                message="Add a field for sample size",
            )
            draft = json.loads((project_dir / "extraction" / "extraction_schema_draft.json").read_text(encoding="utf-8"))
            chat_rows = read_jsonl(project_dir / "chat" / "messages.jsonl")

        self.assertEqual(result.stage, "prompt_extraction")
        self.assertEqual(result.data["action"], "add_field")
        self.assertEqual([field["name"] for field in draft["fields"]], ["methods", "sample_size"])
        self.assertIn("sample_size", result.reply)
        self.assertEqual([row["role"] for row in chat_rows], ["u", "a"])

    def test_extraction_schema_chat_answers_step_four_questions_without_mutating_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            (project_dir / "extraction").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps(
                    {
                        "project_name": "Demo",
                        "description": "Review LLM systems in medicine",
                        "search_terms": "LLM AND medicine",
                        "platforms": ["openalex"],
                        "model": "gpt-5.4-mini",
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "extraction" / "extraction_schema_draft.json").write_text(
                json.dumps({"fields": [{"name": "methods", "type": "Text", "description": "Methods", "required": False, "example": "RCT"}]}),
                encoding="utf-8",
            )

            def fake_llm_query(*, text_prompt, system_prompt, model, provider):
                return (json.dumps({"action": "answer_question", "args": {"response": "Finalize the schema before extraction."}}), {})

            result = LeadAgent(output_root, llm_query=fake_llm_query).handle_message(
                project_id="demo",
                message="How does extraction work?",
            )
            draft = json.loads((project_dir / "extraction" / "extraction_schema_draft.json").read_text(encoding="utf-8"))

        self.assertEqual(result.data["action"], "answer_question")
        self.assertEqual(result.reply, "Finalize the schema before extraction.")
        self.assertEqual([field["name"] for field in draft["fields"]], ["methods"])

    def test_step_four_chat_blocks_mutation_of_finalized_legacy_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            extraction_dir = project_dir / "extraction"
            extraction_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps(
                    {
                        "project_name": "Demo",
                        "description": "Review LLM systems in medicine",
                        "search_terms": "LLM AND medicine",
                        "platforms": ["openalex"],
                        "model": "gpt-5.4-mini",
                    }
                ),
                encoding="utf-8",
            )
            original_schema = {"fields": [{"name": "methods", "type": "Text", "description": "Methods"}]}
            (extraction_dir / "extraction_schema.json").write_text(json.dumps(original_schema), encoding="utf-8")
            (extraction_dir / "extraction_results.jsonl").write_text(
                json.dumps({"paper_id": "P1", "methods": "Survey"}) + "\n",
                encoding="utf-8",
            )

            def fake_llm_query(*, text_prompt, system_prompt, model, provider):
                return (
                    json.dumps(
                        {
                            "action": "add_field",
                            "args": {"name": "sample_size", "description": "Number of participants"},
                        }
                    ),
                    {},
                )

            result = LeadAgent(output_root, llm_query=fake_llm_query).handle_message(
                project_id="demo",
                message="Add sample size",
                context_step="extraction",
            )
            schema = json.loads((extraction_dir / "extraction_schema.json").read_text(encoding="utf-8"))

        self.assertEqual(result.stage, "prompt_extraction")
        self.assertEqual(result.data["status"], "schema_locked")
        self.assertIn("Edit Schema", result.reply)
        self.assertEqual(schema, original_schema)
        self.assertFalse((extraction_dir / "extraction_schema_draft.json").exists())

    def test_run_extraction_requires_finalized_schema_after_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            ledger_through(project_dir, "retrieval")
            (project_dir / "pdfs").mkdir(parents=True)
            (project_dir / "extraction").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "Demo", "description": "Review LLMs", "platforms": ["openalex"], "search_terms": "LLM"}),
                encoding="utf-8",
            )
            (project_dir / "pdfs" / "download_report.json").write_text(json.dumps({"success": 1, "failed": 0}), encoding="utf-8")
            (project_dir / "extraction" / "extraction_schema_draft.json").write_text(
                json.dumps({"fields": [{"name": "key_findings", "description": "Findings"}]}),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as ctx:
                LeadAgent(output_root).handle_message(project_id="demo", action="run-extraction")

        self.assertIn("requires finalized extraction schema", str(ctx.exception))

    def test_generate_schema_requires_edit_when_schema_is_finalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            ledger_through(project_dir, "screening")
            (project_dir / "filtered").mkdir(parents=True)
            (project_dir / "extraction").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "Demo", "description": "Review LLMs", "platforms": ["openalex"], "search_terms": "LLM"}),
                encoding="utf-8",
            )
            (project_dir / "filtered" / "included_papers.jsonl").write_text("", encoding="utf-8")
            (project_dir / "filtered" / "screening_stats.json").write_text(json.dumps({"included_count": 0}), encoding="utf-8")
            save_schema_draft(project_dir, {"fields": [{"name": "methods", "description": "Methods"}]})
            finalize_schema(project_dir)
            marker_before = (project_dir / "extraction" / "schema_finalized.json").read_text(encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Edit Schema"):
                LeadAgent(output_root).handle_message(project_id="demo", action="generate-schema")

            marker_after = (project_dir / "extraction" / "schema_finalized.json").read_text(encoding="utf-8")

        self.assertEqual(marker_after, marker_before)

    def test_schema_chat_show_prompt_returns_prompt_content_and_finalize_updates_next_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            (project_dir / "prompts").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "Demo", "description": "Review LLMs", "search_terms": "LLM", "platforms": ["openalex"]}),
                encoding="utf-8",
            )
            save_schema_draft(project_dir, {"fields": [{"name": "methods", "description": "Methods"}]})
            (project_dir / "prompts" / "extraction_prompt.json").write_text(
                json.dumps({"system_prompt": "Extract faithfully.", "user_prompt_template": "Return methods from {paper_text}."}),
                encoding="utf-8",
            )
            commands = iter(
                [
                    {"action": "show_prompt", "args": {}},
                    {"action": "finalize_extraction", "args": {}},
                ]
            )

            def fake_llm_query(**_kwargs):
                return (json.dumps(next(commands)), {})

            agent = LeadAgent(output_root, llm_query=fake_llm_query)
            shown = agent.handle_message(project_id="demo", message="Show prompt", context_step="extraction")
            finalized = agent.handle_message(project_id="demo", message="Finalize", context_step="extraction")

        self.assertIn("Extract faithfully.", shown.reply)
        self.assertIn("Return methods", shown.reply)
        self.assertEqual(finalized.next_actions, ["edit_schema", "run_extraction"])

    def test_handle_collect_action_generates_relevance_prompt_before_collection_and_verifies_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps(
                    {
                        "project_name": "Demo",
                        "description": "Review LLM systems in biomedicine",
                        "primary_topic": "LLM systems",
                        "domain": "biomedicine",
                        "search_terms": "LLM AND biomedicine",
                        "platforms": ["openalex"],
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            class FakeWorkflowAdapter:
                class Contract:
                    def __init__(self, action):
                        self.agent_name = "PromptAgent" if action == "generate-relevance-prompt" else "CollectionAgent"
                        self.stage = "prompt_relevance" if action == "generate-relevance-prompt" else "collection"
                        self.model = "gpt-5.4-mini"

                def contract_for(self, action):
                    return self.Contract(action)

                def run(self, action, root, project_id, llm_query=None, input_data=None):
                    calls.append((action, Path(root), project_id, llm_query is not None))
                    if action == "generate-relevance-prompt":
                        prompts_dir = Path(root) / project_id / "prompts"
                        prompts_dir.mkdir(parents=True)
                        (prompts_dir / "relevance_prompt.json").write_text(
                            json.dumps({"task": "Include LLM systems in biomedicine.", "instruction": "Return True or False."}),
                            encoding="utf-8",
                        )
                        return {"status": "relevance_prompt_generated"}
                    collected_dir = Path(root) / project_id / "collected"
                    collected_dir.mkdir(parents=True)
                    (collected_dir / "openalex.jsonl").write_text(
                        json.dumps({"title": "Paper A", "source": "openalex"}) + "\n",
                        encoding="utf-8",
                    )
                    (collected_dir / "summary.json").write_text(
                        json.dumps({"total_papers": 1, "platform_stats": {"openalex": 1}, "platform_errors": {}}),
                        encoding="utf-8",
                    )
                    return {
                        "status": "collection_done",
                        "total": 1,
                        "platform_stats": {"openalex": 1},
                        "platform_errors": {},
                    }

            def fake_llm_query(*args, **kwargs):
                return (json.dumps({"reply": "LLM action reply for CollectionAgent."}), {"input_tokens": 20})

            result = LeadAgent(output_root, workflow_adapter=FakeWorkflowAdapter(), llm_query=fake_llm_query).handle_message(
                project_id="demo",
                action="collect",
            )
            relevance_prompt = json.loads((project_dir / "prompts" / "relevance_prompt.json").read_text(encoding="utf-8"))
            chat_rows = read_jsonl(project_dir / "chat" / "messages.jsonl")

        self.assertEqual(calls, [("generate-relevance-prompt", output_root, "demo", True), ("collect", output_root, "demo", True)])
        self.assertEqual(result.stage, "collection")
        self.assertEqual(result.status, "completed")
        self.assertIn("LLM systems", relevance_prompt["task"])
        self.assertIn(str(project_dir / "prompts" / "relevance_prompt.json"), result.artifacts)
        self.assertIn(str(project_dir / "collected" / "summary.json"), result.artifacts)
        self.assertEqual(result.reply, "Collection completed: 1 paper collected. Next action: Paper Screening.")
        self.assertEqual(chat_rows[-1]["role"], "a")
        self.assertEqual(chat_rows[-1]["text"], "Collection completed: 1 paper collected. Next action: Paper Screening.")

    def test_handle_collect_action_fails_when_collection_artifact_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps(
                    {
                        "project_name": "Demo",
                        "description": "Review robotics",
                        "primary_topic": "robotics",
                        "domain": "surgery",
                        "search_terms": "robotics AND surgery",
                        "platforms": ["openalex"],
                    }
                ),
                encoding="utf-8",
            )

            class FakeWorkflowAdapter:
                class Contract:
                    def __init__(self, action):
                        self.agent_name = "PromptAgent" if action == "generate-relevance-prompt" else "CollectionAgent"
                        self.stage = "prompt_relevance" if action == "generate-relevance-prompt" else "collection"
                        self.model = "gpt-5.4-mini"

                def contract_for(self, action):
                    return self.Contract(action)

                def run(self, action, root, project_id, llm_query=None, input_data=None):
                    if action == "generate-relevance-prompt":
                        prompts_dir = Path(root) / project_id / "prompts"
                        prompts_dir.mkdir(parents=True)
                        (prompts_dir / "relevance_prompt.json").write_text(
                            json.dumps({"task": "Include robotics in surgery.", "instruction": "Return True or False."}),
                            encoding="utf-8",
                        )
                        return {"status": "relevance_prompt_generated"}
                    return {"status": "collection_done", "total": 0}

            lead_agent = LeadAgent(output_root, workflow_adapter=FakeWorkflowAdapter())

            with self.assertRaises(ValueError) as ctx:
                lead_agent.handle_message(project_id="demo", action="collect")

        self.assertIn("collection artifact was not written", str(ctx.exception))

    def test_stage_gate_rejects_screening_before_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps(
                    {
                        "project_name": "Demo",
                        "description": "Review robotics",
                        "primary_topic": "robotics",
                        "domain": "surgery",
                        "search_terms": "robotics AND surgery",
                        "platforms": ["openalex"],
                    }
                ),
                encoding="utf-8",
            )

            lead_agent = LeadAgent(output_root)

            with self.assertRaises(ValueError) as ctx:
                lead_agent.handle_message(project_id="demo", action="screen")

        self.assertIn("Action 'screen' requires completed stage 'collection'", str(ctx.exception))

    def test_generate_schema_action_writes_architecture_prompt_extraction_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            ledger_through(project_dir, "screening")
            (project_dir / "filtered").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps(
                    {
                        "project_name": "Demo",
                        "description": "Review AI tools for surgery",
                        "primary_topic": "AI tools",
                        "domain": "surgery",
                        "search_terms": "AI AND surgery",
                        "platforms": ["openalex"],
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "filtered" / "included_papers.jsonl").write_text(
                json.dumps({"title": "Paper A", "source": "openalex"}) + "\n",
                encoding="utf-8",
            )
            (project_dir / "filtered" / "screening_stats.json").write_text(
                json.dumps({"included_count": 1, "excluded_count": 0}),
                encoding="utf-8",
            )

            def fake_llm_query(*args, **kwargs):
                if "Design an extraction schema" in kwargs.get("text_prompt", ""):
                    return (
                        json.dumps(
                            {
                                "fields": [
                                    {"name": "tool_type", "type": "Text", "description": "AI tool type", "required": False, "example": "model"},
                                    {"name": "key_findings", "type": "Long text", "description": "Main findings", "required": False, "example": "finding"},
                                    {"name": "limitations", "type": "Text", "description": "Limitations", "required": False, "example": "limitation"},
                                ]
                            }
                        ),
                        {},
                    )
                return (json.dumps({"reply": "LLM extraction prompt reply."}), {})

            result = LeadAgent(output_root, llm_query=fake_llm_query).handle_message(
                project_id="demo",
                action="generate-schema",
            )
            architecture_prompt = json.loads((project_dir / "prompts" / "extraction_prompt.json").read_text(encoding="utf-8"))
            ui_schema = json.loads((project_dir / "extraction" / "extraction_schema.json").read_text(encoding="utf-8"))

        self.assertEqual(result.stage, "prompt_extraction")
        self.assertIn(str(project_dir / "prompts" / "extraction_prompt.json"), result.artifacts)
        self.assertEqual(architecture_prompt["prompt_type"], "extraction")
        self.assertEqual(ui_schema["fields"][0]["name"], "tool_type")

    def test_generate_schema_deterministically_routes_draft_to_finalization_and_persists_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            ledger_through(project_dir, "screening")
            (project_dir / "filtered").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps(
                    {
                        "project_name": "Demo",
                        "description": "Review AI tools for surgery",
                        "primary_topic": "AI tools",
                        "domain": "surgery",
                        "search_terms": "AI AND surgery",
                        "platforms": ["openalex"],
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "filtered" / "included_papers.jsonl").write_text(
                json.dumps({"title": "Paper A", "source": "openalex"}) + "\n",
                encoding="utf-8",
            )
            (project_dir / "filtered" / "screening_stats.json").write_text(
                json.dumps({"included_count": 1, "excluded_count": 0}),
                encoding="utf-8",
            )
            llm_calls = []

            def fake_llm_query(*args, **kwargs):
                llm_calls.append(kwargs.get("text_prompt", ""))
                if "Design an extraction schema" not in kwargs.get("text_prompt", ""):
                    raise AssertionError("Schema workflow routing must not call the LLM")
                return (
                    json.dumps(
                        {
                            "fields": [
                                {"name": f"field_{index}", "type": "Text", "description": f"Field {index}", "required": False, "example": f"Value {index}"}
                                for index in range(10)
                            ]
                        }
                    ),
                    {},
                )

            result = LeadAgent(output_root, llm_query=fake_llm_query).handle_message(
                project_id="demo",
                action="generate-schema",
            )
            chat_rows = read_jsonl(project_dir / "chat" / "messages.jsonl")

        self.assertEqual(len(llm_calls), 1)
        self.assertEqual(result.next_actions, ["finalize_schema"])
        self.assertIn("10 fields", result.reply)
        self.assertIn("review", result.reply.lower())
        self.assertIn("finalize", result.reply.lower())
        self.assertNotIn("Categorization", result.reply)
        self.assertEqual(chat_rows[-1]["text"], result.reply)
        self.assertEqual(chat_rows[-1]["stage"], "prompt_extraction")

    def test_generate_schema_reply_uses_singular_field_count(self):
        reply = LeadAgent(Path("unused"))._stage_reply("prompt_extraction", {"field_count": 1})

        self.assertIn("with 1 field.", reply)
        self.assertNotIn("1 fields", reply)

    def test_finalize_schema_routes_to_information_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "Demo", "platforms": ["openalex"], "search_terms": "AI"}),
                encoding="utf-8",
            )
            save_schema_draft(
                project_dir,
                {"fields": [{"name": "key_findings", "type": "Text", "description": "Findings"}]},
            )
            (project_dir / "prompts").mkdir(parents=True)
            (project_dir / "prompts" / "extraction_prompt.json").write_text(
                json.dumps({"system_prompt": "Extract faithfully.", "user_prompt_template": "Extract findings."}),
                encoding="utf-8",
            )
            (project_dir / "extraction" / "extraction_prompt.json").write_text(
                json.dumps({"system_prompt": "Extract faithfully.", "extraction_prompt": "Extract findings."}),
                encoding="utf-8",
            )

            result = LeadAgent(output_root).handle_message(project_id="demo", action="finalize-schema")
            marker_exists = (project_dir / "extraction" / "schema_finalized.json").exists()
            chat_rows = read_jsonl(project_dir / "chat" / "messages.jsonl")

        self.assertEqual(result.next_actions, ["run_extraction"])
        self.assertIn("Information Extraction", result.reply)
        self.assertTrue(marker_exists)
        self.assertEqual(chat_rows[-1]["text"], result.reply)
        self.assertEqual(chat_rows[-1]["stage"], "prompt_extraction")

    def test_download_action_requires_filtering_not_prompt_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            ledger_through(project_dir, "screening")
            (project_dir / "filtered").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "Demo", "description": "Review LLMs", "platforms": ["openalex"], "search_terms": "LLM"}),
                encoding="utf-8",
            )
            (project_dir / "filtered" / "included_papers.jsonl").write_text(
                json.dumps({"title": "Paper A", "source": "openalex"}) + "\n",
                encoding="utf-8",
            )
            (project_dir / "filtered" / "screening_stats.json").write_text(
                json.dumps({"included_count": 1, "excluded_count": 0}),
                encoding="utf-8",
            )
            calls = []

            class FakeWorkflowAdapter:
                class Contract:
                    agent_name = "DownloadAgent"
                    stage = "download"
                    model = "gpt-5.4-mini"

                def contract_for(self, _action):
                    return self.Contract()

                def run(self, action, output_root, project_id, llm_query=None, input_data=None):
                    calls.append(action)
                    pdf_dir = Path(output_root) / project_id / "pdfs"
                    pdf_dir.mkdir(parents=True)
                    (pdf_dir / "download_report.json").write_text(json.dumps({"success": 0, "failed": 1}), encoding="utf-8")
                    return {"status": "download_done", "success": 0, "failed": 1}

            def fake_llm_query(*args, **kwargs):
                return (json.dumps({"reply": "Download attempted."}), {})

            result = LeadAgent(output_root, workflow_adapter=FakeWorkflowAdapter(), llm_query=fake_llm_query).handle_message(
                project_id="demo",
                action="download-pdfs",
            )

        self.assertEqual(calls, ["download-pdfs"])
        self.assertEqual(result.stage, "download")

    def test_download_stage_reply_prompt_keeps_web_search_fallback_inside_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            ledger_through(project_dir, "screening")
            (project_dir / "filtered").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "Demo", "description": "Review LLMs", "platforms": ["openalex"], "search_terms": "LLM"}),
                encoding="utf-8",
            )
            (project_dir / "filtered" / "included_papers.jsonl").write_text(
                json.dumps({"title": "Paper A", "source": "openalex"}) + "\n",
                encoding="utf-8",
            )
            (project_dir / "filtered" / "screening_stats.json").write_text(
                json.dumps({"included_count": 1, "excluded_count": 0}),
                encoding="utf-8",
            )
            seen_prompts = []

            class FakeWorkflowAdapter:
                class Contract:
                    agent_name = "DownloadAgent"
                    stage = "download"
                    model = "gpt-5.4-mini"

                def contract_for(self, _action):
                    return self.Contract()

                def run(self, action, output_root, project_id, llm_query=None, input_data=None):
                    pdf_dir = Path(output_root) / project_id / "pdfs"
                    pdf_dir.mkdir(parents=True)
                    (pdf_dir / "download_report.json").write_text(
                        json.dumps({"success": 0, "failed": 1, "web_search_fallback_candidates": [{"title": "Paper A"}]}),
                        encoding="utf-8",
                    )
                    return {"status": "download_done", "success": 0, "failed": 1, "web_search_fallback_candidates": [{"title": "Paper A"}]}

            def fake_llm_query(*args, **kwargs):
                seen_prompts.append(kwargs["text_prompt"])
                return (json.dumps({"reply": "Download attempted. Run Information Extraction next."}), {})

            result = LeadAgent(output_root, workflow_adapter=FakeWorkflowAdapter(), llm_query=fake_llm_query).handle_message(
                project_id="demo",
                action="download-pdfs",
            )

        self.assertEqual(seen_prompts, [])
        self.assertEqual(result.status, "failed")
        self.assertIn("blocked", result.reply)
        self.assertIn("ExtractionAgent will use web-search fallback", result.reply)

    def test_stage_reply_uses_deterministic_next_canvas_action_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "Demo", "description": "Review LLMs", "platforms": ["pubmed"], "search_terms": "LLM"}),
                encoding="utf-8",
            )
            seen_prompts = []

            class FakeWorkflowAdapter:
                class Contract:
                    agent_name = "CollectionAgent"
                    stage = "collection"
                    model = "gpt-5.4-mini"

                def contract_for(self, _action):
                    return self.Contract()

                def run(self, action, output_root, project_id, llm_query=None, input_data=None):
                    project = Path(output_root) / project_id
                    (project / "prompts").mkdir(parents=True, exist_ok=True)
                    (project / "prompts" / "relevance_prompt.json").write_text(json.dumps({"task": "screen"}), encoding="utf-8")
                    (project / "collected").mkdir(parents=True, exist_ok=True)
                    (project / "collected" / "summary.json").write_text(
                        json.dumps({"total_papers": 0, "platform_stats": {}, "platform_errors": {}}),
                        encoding="utf-8",
                    )
                    return {"status": "collection_done", "total": 0, "platform_stats": {}, "platform_errors": {}}

            def fake_llm_query(*args, **kwargs):
                seen_prompts.append(kwargs["text_prompt"])
                return (json.dumps({"reply": "Collection completed. Next canvas action: Paper Screening."}), {})

            result = LeadAgent(output_root, workflow_adapter=FakeWorkflowAdapter(), llm_query=fake_llm_query).handle_message(
                project_id="demo",
                action="collect",
            )

        self.assertEqual(seen_prompts, [])
        self.assertEqual(result.reply, "Collection completed: 0 papers collected. Next action: Paper Screening.")

    def test_run_extraction_uses_finalized_schema_after_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            ledger_through(project_dir, "retrieval")
            (project_dir / "filtered").mkdir(parents=True)
            (project_dir / "pdfs").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "Demo", "description": "Review LLMs", "platforms": ["openalex"], "search_terms": "LLM"}),
                encoding="utf-8",
            )
            (project_dir / "filtered" / "included_papers.jsonl").write_text(
                json.dumps({"title": "Paper A", "source": "openalex"}) + "\n",
                encoding="utf-8",
            )
            (project_dir / "filtered" / "screening_stats.json").write_text(
                json.dumps({"included_count": 1, "excluded_count": 0}),
                encoding="utf-8",
            )
            (project_dir / "pdfs" / "download_report.json").write_text(json.dumps({"success": 0, "failed": 1}), encoding="utf-8")
            save_schema_draft(project_dir, {"fields": [{"name": "key_findings", "type": "Text", "description": "Findings"}]})
            finalize_schema(project_dir)
            calls = []

            class FakeWorkflowAdapter:
                class Contract:
                    agent_name = "TestAgent"
                    stage = "test"
                    model = "gpt-5.4-mini"

                def contract_for(self, _action):
                    return self.Contract()

                def run(self, action, output_root, project_id, llm_query=None, input_data=None):
                    calls.append(action)
                    project = Path(output_root) / project_id
                    if action == "run-extraction":
                        (project / "extraction").mkdir(parents=True, exist_ok=True)
                        (project / "extraction" / "extraction_results.jsonl").write_text(
                            json.dumps({"title": "Paper A", "key_findings": "Finding"}) + "\n",
                            encoding="utf-8",
                        )
                        return {"status": "extraction_done", "processed": 1, "errors": 0}
                    raise AssertionError(action)

            def fake_llm_query(*args, **kwargs):
                return (json.dumps({"reply": "Extraction completed."}), {})

            result = LeadAgent(output_root, workflow_adapter=FakeWorkflowAdapter(), llm_query=fake_llm_query).handle_message(
                project_id="demo",
                action="run-extraction",
            )

        self.assertEqual(calls, ["run-extraction"])
        self.assertEqual(result.stage, "extraction")

    def test_extraction_stage_reply_redacts_nested_local_paths_without_mutating_machine_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            ledger_through(project_dir, "retrieval")
            (project_dir / "filtered").mkdir(parents=True)
            (project_dir / "pdfs").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "Demo", "platforms": ["openalex"], "search_terms": "LLM"}),
                encoding="utf-8",
            )
            (project_dir / "filtered" / "included_papers.jsonl").write_text(
                json.dumps({"title": "Paper A", "source": "openalex"}) + "\n",
                encoding="utf-8",
            )
            (project_dir / "filtered" / "screening_stats.json").write_text(
                json.dumps({"included_count": 1, "excluded_count": 0}),
                encoding="utf-8",
            )
            (project_dir / "pdfs" / "download_report.json").write_text(
                json.dumps({"success": 1, "failed": 0}),
                encoding="utf-8",
            )
            save_schema_draft(project_dir, {"fields": [{"name": "finding", "type": "Text", "description": "Finding"}]})
            finalize_schema(project_dir)
            unix_path = "/Users/private-user/ReviewPilot/output/demo/extraction/results.jsonl"
            windows_path = r"C:\Users\private-user\ReviewPilot\output\demo\report.json"
            unix_path_with_spaces = "/Users/private-user/Review Pilot/out.json"
            windows_path_with_spaces = r"C:\Users\Alice Smith\ReviewPilot\out.json"
            unc_path = r"\\server\share\private-user\out.json"
            workflow_result = {
                "status": "extraction_done",
                "outcome": "partial",
                "processed": 3,
                "failed": 1,
                "output_path": unix_path,
                "details": {
                    "artifacts": [
                        {"report_file": windows_path},
                        {"quoted_report": unix_path_with_spaces},
                        {"spaced_report": windows_path_with_spaces},
                        {"network_report": unc_path},
                    ]
                },
            }
            seen_prompts = []

            class FakeWorkflowAdapter:
                class Contract:
                    agent_name = "ExtractionAgent"
                    stage = "extraction"
                    model = "gpt-5.4-mini"

                def contract_for(self, _action):
                    return self.Contract()

                def run(self, action, output_root, project_id, llm_query=None, input_data=None):
                    if action != "run-extraction":
                        raise AssertionError(action)
                    project = Path(output_root) / project_id
                    (project / "extraction" / "extraction_results.jsonl").write_text(
                        json.dumps({"title": "Paper A", "finding": "Result"}) + "\n",
                        encoding="utf-8",
                    )
                    return workflow_result

            def fake_llm_query(*args, **kwargs):
                prompt = kwargs["text_prompt"]
                seen_prompts.append(prompt)
                return (
                    json.dumps(
                        {
                            "reply": (
                                "Unsafe generated reply: /Users/private-user/out,final.json with 99 processed no delimiter; "
                                f"drive {windows_path_with_spaces})! private-tail; "
                                f"network {unc_path}! server-tail. Wrong next action: Export."
                            )
                        }
                    ),
                    {},
                )

            result = LeadAgent(
                output_root,
                workflow_adapter=FakeWorkflowAdapter(),
                llm_query=fake_llm_query,
            ).handle_message(project_id="demo", action="run-extraction")
            chat_rows = read_jsonl(project_dir / "chat" / "messages.jsonl")
            projected = build_rp_data(output_root, "demo")

        self.assertEqual(seen_prompts, [])
        self.assertEqual(result.data["output_path"], unix_path)
        self.assertEqual(result.data["details"]["artifacts"][0]["report_file"], windows_path)
        self.assertEqual(result.data["details"]["artifacts"][1]["quoted_report"], unix_path_with_spaces)
        self.assertEqual(result.data["details"]["artifacts"][2]["spaced_report"], windows_path_with_spaces)
        self.assertEqual(result.data["details"]["artifacts"][3]["network_report"], unc_path)
        self.assertEqual(
            result.artifacts,
            [
                str(project_dir / "prompts" / "extraction_prompt.json"),
                str(project_dir / "extraction" / "extraction_results.jsonl"),
            ],
        )
        self.assertEqual(
            result.reply,
            "Information Extraction partially completed: 3 processed, 1 failed. Next action: Categorization & Analysis. Failed items remain retryable in the recovery step.",
        )
        self.assertNotIn("private-user", result.reply)
        self.assertNotIn("Alice Smith", result.reply)
        self.assertNotIn("Review Pilot", result.reply)
        self.assertNotIn("\\\\server\\share", result.reply)
        self.assertNotIn("other-user", result.reply)
        self.assertNotIn("private-tail", result.reply)
        self.assertNotIn("server-tail", result.reply)
        self.assertNotIn("99 processed", result.reply)
        self.assertNotIn("Wrong next action", result.reply)
        self.assertNotIn("/Users/", result.reply)
        self.assertNotIn("C:\\Users\\", result.reply)
        self.assertIn("Next action: Categorization & Analysis", result.reply)
        self.assertEqual(chat_rows[-1]["text"], result.reply)
        self.assertIn("3 processed", result.reply)
        self.assertIn("1 failed", result.reply)
        self.assertIn("recovery step", result.reply)
        self.assertIn("3 processed", chat_rows[-1]["text"])
        self.assertIn("1 failed", chat_rows[-1]["text"])
        projected_activity = json.dumps(projected["activityByStep"])
        self.assertNotIn("private-user", projected_activity)
        self.assertNotIn("Alice Smith", projected_activity)
        self.assertNotIn("server", projected_activity.lower())
        self.assertNotIn("private-tail", projected_activity)
        self.assertIn("3 processed", projected_activity)
        self.assertIn("1 failed", projected_activity)
        self.assertIn("partially completed", projected_activity)
        self.assertIn("recovery step", projected_activity)
        self.assertIn("Next action: Categorization & Analysis", projected_activity)

    def test_unsafe_stage_reply_shapes_all_fall_back_to_structured_summary(self):
        unsafe_replies = [
            "Saved to /Users/private-user/out,final.json with no delimiter 99 processed",
            "Saved:/Users/private-user/out.json with no separating space",
            "Saved to file:///Users/private-user/out.json",
            "Saved to file://server/share/private-user/out.json",
            "Saved to //server/share/private-user/out.json",
            "Saved to ///Users/private-user/out.json",
            "Saved to ////server/share/private-user/out.json",
            r"Saved to C:\Users\Alice Smith\out.json) with 99 processed!",
            r"Saved to \\server\share\private-user\out.json! tail-secret",
        ]

        for unsafe_reply in unsafe_replies:
            with self.subTest(reply=unsafe_reply):
                def fake_llm_query(**_kwargs):
                    return (json.dumps({"reply": unsafe_reply}), {})

                reply = LeadAgent(Path("unused"), llm_query=fake_llm_query)._stage_reply(
                    "extraction",
                    {"status": "extraction_done", "processed": 3, "failed": 1},
                )

                self.assertEqual(
                    reply,
                    "Information Extraction completed: 3 processed, 1 failed. Next action: Categorization & Analysis.",
                )

    def test_prompt_sanitization_catches_colon_and_file_uri_path_forms_without_hiding_web_urls(self):
        agent = LeadAgent(Path("unused"))
        values = {
            "colon": "Saved:/Users/private-user/out.json",
            "file_unix": "file:///Users/private-user/out.json",
            "file_network": "file://server/share/private-user/out.json",
            "network": "//server/share/private-user/out.json",
            "repeated_root": "///Users/private-user/out.json",
            "repeated_network_root": "////server/share/private-user/out.json",
            "web": "https://example.org/review/results",
            "plain_file_label": "file: ready for review",
        }

        sanitized = agent._sanitize_prompt_value(values)

        self.assertEqual(sanitized["colon"], "project artifact")
        self.assertEqual(sanitized["file_unix"], "project artifact")
        self.assertEqual(sanitized["file_network"], "project artifact")
        self.assertEqual(sanitized["network"], "project artifact")
        self.assertEqual(sanitized["repeated_root"], "project artifact")
        self.assertEqual(sanitized["repeated_network_root"], "project artifact")
        self.assertEqual(sanitized["web"], values["web"])
        self.assertEqual(sanitized["plain_file_label"], values["plain_file_label"])

    def test_unsafe_stage_fallback_uses_supported_contract_keys_and_stage_guidance(self):
        cases = [
            (
                "collection",
                {"status": "collection_done", "total": 4, "total_papers": 4},
                "Collection completed: 4 papers collected. Next action: Paper Screening.",
            ),
            (
                "filtering",
                {"status": "screening_done", "included": 3, "excluded": 1},
                "Paper Screening completed: 3 included, 1 excluded. Next action: Full-Text Retrieval.",
            ),
            (
                "download",
                {
                    "status": "download_done",
                    "success": 2,
                    "failed": 1,
                    "stats": {"web_search_fallback_candidates": [{"title": "A"}]},
                },
                "Full-Text Retrieval completed: 2 available, 1 failed. Next action: Information Extraction. ExtractionAgent will use web-search fallback for eligible unavailable papers.",
            ),
            (
                "extraction",
                {"status": "extraction_done", "processed": 3, "errors": 1},
                "Information Extraction completed: 3 processed, 1 error. Next action: Categorization & Analysis.",
            ),
            (
                "extraction",
                {"status": "extraction_done", "processed": 3, "errors": 2},
                "Information Extraction completed: 3 processed, 2 errors. Next action: Categorization & Analysis.",
            ),
            (
                "categorization",
                {"status": "categorization_done", "categories": 2, "rows": 5},
                "Categorization & Analysis completed: 2 categories, 5 rows categorized. No next canvas action is required.",
            ),
        ]

        for stage, result, expected in cases:
            with self.subTest(stage=stage, result=result):
                def fake_llm_query(**_kwargs):
                    return (json.dumps({"reply": "Unsafe file:///Users/private-user/result.json tail"}), {})

                reply = LeadAgent(Path("unused"), llm_query=fake_llm_query)._stage_reply(stage, result)

                self.assertEqual(reply, expected)

    def test_lead_agent_uses_workflow_adapter_for_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps(
                    {
                        "project_name": "Demo",
                        "description": "Review LLM systems in biomedicine",
                        "primary_topic": "LLM systems",
                        "domain": "biomedicine",
                        "search_terms": "LLM AND biomedicine",
                        "platforms": ["openalex"],
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            class FakeWorkflowAdapter:
                class Contract:
                    def __init__(self, action):
                        self.agent_name = "PromptAgent" if action == "generate-relevance-prompt" else "CollectionAgent"
                        self.stage = "prompt_relevance" if action == "generate-relevance-prompt" else "collection"
                        self.model = "gpt-5.4-mini"

                def contract_for(self, action):
                    return self.Contract(action)

                def run(self, action, output_root, project_id, llm_query=None, input_data=None):
                    calls.append((action, Path(output_root), project_id, llm_query is not None))
                    if action == "generate-relevance-prompt":
                        prompts_dir = Path(output_root) / project_id / "prompts"
                        prompts_dir.mkdir(parents=True)
                        (prompts_dir / "relevance_prompt.json").write_text(
                            json.dumps({"task": "Include LLM systems in biomedicine.", "instruction": "Return True or False."}),
                            encoding="utf-8",
                        )
                        return {"status": "relevance_prompt_generated"}
                    collected_dir = Path(output_root) / project_id / "collected"
                    collected_dir.mkdir(parents=True)
                    (collected_dir / "summary.json").write_text(
                        json.dumps({"total_papers": 0, "platform_stats": {}, "platform_errors": {}}),
                        encoding="utf-8",
                    )
                    return {"status": "collection_done", "total": 0, "platform_stats": {}, "platform_errors": {}}

            def fake_llm_query(*args, **kwargs):
                return (json.dumps({"reply": "LLM adapter reply."}), {})

            result = LeadAgent(output_root, workflow_adapter=FakeWorkflowAdapter(), llm_query=fake_llm_query).handle_message(
                project_id="demo",
                action="collect",
            )

        self.assertEqual(result.stage, "collection")
        self.assertEqual(calls, [("generate-relevance-prompt", output_root, "demo", True), ("collect", output_root, "demo", True)])
        self.assertEqual(result.data["sub_agent"], "CollectionAgent")
        self.assertEqual(result.data["contract_stage"], "collection")

    def test_suggest_categories_routes_to_review_confirmation_and_application_without_second_llm_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            ledger_through(project_dir, "extraction")
            (project_dir / "extraction").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps(
                    {
                        "project_name": "Demo",
                        "description": "Review LLM systems in biomedicine",
                        "search_terms": "LLM AND biomedicine",
                        "platforms": ["openalex"],
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "extraction" / "extraction_results.jsonl").write_text(
                json.dumps({"title": "Paper A", "methods": "Clinical benchmark"}) + "\n",
                encoding="utf-8",
            )
            llm_calls = []

            def fake_llm_query(*args, **kwargs):
                llm_calls.append(kwargs.get("text_prompt", ""))
                if "Create a semantic category plan" not in kwargs.get("text_prompt", ""):
                    raise AssertionError("Category suggestion routing must not call a second LLM")
                return (
                    json.dumps(
                        {
                            "categories": ["Clinical studies"],
                            "category_descriptions": {
                                "Clinical studies": "Clinical applications",
                            },
                        }
                    ),
                    {},
                )

            result = LeadAgent(output_root, llm_query=fake_llm_query).handle_message(
                project_id="demo",
                action="suggest-categories",
                input_data={"field": "methods", "mode": "multiple"},
            )
            chat_rows = read_jsonl(project_dir / "chat" / "messages.jsonl")

        self.assertEqual(len(llm_calls), 1)
        self.assertEqual(
            result.reply,
            "Generated 1 category suggestion for methods. Review it, select Confirm Categories, then select Apply Categorization.",
        )
        self.assertIn("review", result.reply.lower())
        self.assertIn("Confirm Categories", result.reply)
        self.assertIn("Apply Categorization", result.reply)
        self.assertNotIn("completed", result.reply.lower())
        self.assertNotIn("no next", result.reply.lower())
        self.assertEqual(result.next_actions, ["confirm_categories", "apply_categorization"])
        self.assertEqual(chat_rows[-1]["text"], result.reply)

    def test_category_suggestion_reply_uses_singular_and_neutral_count_wording(self):
        lead_agent = LeadAgent(Path("unused"))

        singular = lead_agent._stage_reply(
            "categorization",
            {"field": "methods", "categories": 1},
            action="suggest-categories",
        )

        self.assertIn("Generated 1 category suggestion for methods.", singular)
        self.assertIn("Review it, select Confirm Categories", singular)
        self.assertNotIn("Review them", singular)
        self.assertNotIn("1 category suggestions", singular)
        for malformed_count in (True, "nine"):
            with self.subTest(categories=malformed_count):
                reply = lead_agent._stage_reply(
                    "categorization",
                    {"field": "methods", "categories": malformed_count},
                    action="suggest-categories",
                )
                self.assertIn("Generated category suggestions for methods.", reply)
                self.assertNotIn(str(malformed_count), reply)

    def test_categorize_advances_to_final_user_facing_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            ledger_through(project_dir, "extraction")
            (project_dir / "extraction").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps(
                    {
                        "project_name": "Demo",
                        "description": "Review LLM systems in biomedicine",
                        "search_terms": "LLM AND biomedicine",
                        "platforms": ["openalex"],
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "extraction" / "extraction_results.jsonl").write_text(
                json.dumps({"title": "Paper A", "key_findings": "clinical diagnosis"}) + "\n",
                encoding="utf-8",
            )

            def fake_llm_query(*args, **kwargs):
                if "Create a semantic category plan" in kwargs.get("text_prompt", ""):
                    return (
                        json.dumps(
                            {
                                "categories": ["Clinical Decision Support"],
                                "category_descriptions": {"Clinical Decision Support": "Clinical support systems"},
                            }
                        ),
                        {},
                    )
                if "Assign the supplied paper evidence" in kwargs.get("text_prompt", ""):
                    return (json.dumps({"category": "Clinical Decision Support"}), {})
                return (json.dumps({"reply": "Categorization completed."}), {})

            result = LeadAgent(output_root, llm_query=fake_llm_query).handle_message(
                project_id="demo",
                action="categorize",
            )
            mapping = json.loads((project_dir / "categorization" / "categorization_mapping.json").read_text(encoding="utf-8"))

        self.assertEqual(result.stage, "categorization")
        self.assertEqual(result.data["contract_stage"], "categorization")
        self.assertEqual(result.data["sub_agent"], "LeadAgentCategorization")
        self.assertEqual(result.data["status"], "categorization_done")
        self.assertEqual(result.reply, "Categorization & Analysis completed: 1 category, 1 row categorized. No next canvas action is required.")
        self.assertEqual(result.next_actions, [])
        self.assertIn("Clinical Decision Support", mapping["categories"])


if __name__ == "__main__":
    unittest.main()
