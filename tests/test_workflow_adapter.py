import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from reviewpilot_core.atomic_files import atomic_write_json, atomic_write_jsonl, atomic_write_text
from reviewpilot_core.model_policy import (
    CATEGORIZATION_MODEL,
    COLLECTION_MODEL,
    DOWNLOAD_MODEL,
    EXTRACTION_MODEL,
    FILTERING_MODEL,
    PROMPT_MODEL,
    SEARCH_CONDITION_MODEL,
)
from reviewpilot_core.project_store import read_json, read_jsonl
import reviewpilot_core.sub_agent_contracts as contracts
from reviewpilot_core.sub_agent_contracts import (
    CollectionAgentContract,
    CategorizationAnalysisContract,
    DownloadAgentContract,
    ExtractionAgentContract,
    FilteringAgentContract,
    PromptAgentContract,
)
from reviewpilot_core.workflow_adapter import WorkflowActionAdapter


class WorkflowActionAdapterTests(unittest.TestCase):
    def test_contract_normalizers_reject_alias_disagreement_and_artifact_count_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"; (project / "collected").mkdir(parents=True); (project / "pdfs").mkdir(); (project / "extraction").mkdir()
            (project / "collected" / "summary.json").write_text(json.dumps({"total_papers": 1, "platform_stats": {"pubmed": 1}, "platform_errors": {}}))
            (project / "collected" / "pubmed.jsonl").write_text("")
            with self.assertRaises(ValueError): CollectionAgentContract()._normalize_result(project, {"total": 2, "platform_stats": {"pubmed": 2}, "platform_errors": {}})
            (project / "pdfs" / "download_report.json").write_text(json.dumps({"success": 1, "failed": 0}))
            with self.assertRaises(ValueError): DownloadAgentContract()._normalize_result(project, {"success": 2, "failed": 0})
            (project / "extraction" / "extraction_results.jsonl").write_text(json.dumps({"title": "A", "extraction_status": "error"}) + "\n")
            with self.assertRaises(ValueError): ExtractionAgentContract()._normalize_result(project, {"processed": 1, "errors": 0})
    def test_extraction_contract_preserves_explicit_zero_and_requires_both_exact_counts(self):
        from reviewpilot_core.sub_agent_contracts import ExtractionAgentContract
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"; output = project / "extraction" / "extraction_results.jsonl"; output.parent.mkdir(parents=True)
            output.write_text(json.dumps({"title":"A","extraction_status":"error"}) + "\n" + json.dumps({"title":"B","extraction_status":"error"}) + "\n")
            contract = ExtractionAgentContract(); explicit = contract._normalize_result(project, {"processed": 0, "errors": 2})
            with self.assertRaises(ValueError): contract._normalize_result(project, {"errors": 2})
        self.assertEqual(explicit["processed"], 0)

    def test_builtin_contract_normalizers_reject_malformed_explicit_counts(self):
        from reviewpilot_core.sub_agent_contracts import DownloadAgentContract, ExtractionAgentContract
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp)/"demo"; (project/"pdfs").mkdir(parents=True); (project/"extraction").mkdir()
            for contract, result in ((DownloadAgentContract(), {"success":"1","failed":0}), (DownloadAgentContract(), {"success":True,"failed":0}), (ExtractionAgentContract(), {"processed":"0","errors":2}), (ExtractionAgentContract(), {"processed":0,"errors":-1})):
                with self.subTest(contract=type(contract).__name__, result=result), self.assertRaises(ValueError): contract._normalize_result(project,result)
    def test_sub_agent_normalization_outputs_use_shared_atomic_writers(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            filtered_dir = project_dir / "filtered"
            filtered_dir.mkdir(parents=True)

            with (
                patch("reviewpilot_core.sub_agent_contracts.atomic_write_json", wraps=atomic_write_json) as json_writer,
                patch("reviewpilot_core.sub_agent_contracts.atomic_write_jsonl", wraps=atomic_write_jsonl) as jsonl_writer,
                patch("reviewpilot_core.sub_agent_contracts.atomic_write_text", wraps=atomic_write_text) as text_writer,
            ):
                CollectionAgentContract()._write_offline_collection(project_dir, {})
                FilteringAgentContract()._normalize_result(
                    project_dir,
                    {"stats": {"initial_count": 0}},
                )

            json_paths = {call.args[0] for call in json_writer.call_args_list}
            self.assertIn(project_dir / "collected" / "summary.json", json_paths)
            self.assertIn(filtered_dir / "filtering_stats.json", json_paths)
            self.assertIn(filtered_dir / "screening_stats.json", json_paths)
            self.assertEqual(
                [call.args[0] for call in jsonl_writer.call_args_list],
                [filtered_dir / "filtered_papers.jsonl"],
            )
            self.assertEqual(
                {call.args[0] for call in text_writer.call_args_list},
                {filtered_dir / "included_papers.jsonl", filtered_dir / "excluded_papers.jsonl"},
            )

    def test_default_adapter_exposes_sub_agent_contract_metadata(self):
        adapter = WorkflowActionAdapter()

        self.assertEqual(adapter.contract_for("save-search-setup").agent_name, "SearchConditionAgent")
        self.assertEqual(adapter.contract_for("save-search-setup").stage, "search_conditions")
        self.assertEqual(adapter.contract_for("save-search-setup").model, SEARCH_CONDITION_MODEL)
        self.assertEqual(adapter.contract_for("generate-relevance-prompt").agent_name, "PromptAgent")
        self.assertEqual(adapter.contract_for("generate-relevance-prompt").stage, "prompt_relevance")
        self.assertEqual(adapter.contract_for("generate-relevance-prompt").model, PROMPT_MODEL)
        self.assertEqual(adapter.contract_for("collect").agent_name, "CollectionAgent")
        self.assertEqual(adapter.contract_for("collect").stage, "collection")
        self.assertEqual(adapter.contract_for("collect").model, COLLECTION_MODEL)
        self.assertEqual(adapter.contract_for("screen").agent_name, "FilteringAgent")
        self.assertEqual(adapter.contract_for("screen").model, FILTERING_MODEL)
        self.assertEqual(adapter.contract_for("generate-schema").agent_name, "PromptAgent")
        self.assertEqual(adapter.contract_for("generate-schema").model, PROMPT_MODEL)
        self.assertEqual(adapter.contract_for("download-pdfs").agent_name, "DownloadAgent")
        self.assertEqual(adapter.contract_for("download-pdfs").model, DOWNLOAD_MODEL)
        self.assertEqual(adapter.contract_for("run-extraction").agent_name, "ExtractionAgent")
        self.assertEqual(adapter.contract_for("run-extraction").model, EXTRACTION_MODEL)
        self.assertEqual(adapter.contract_for("categorize").agent_name, "LeadAgentCategorization")
        self.assertEqual(adapter.contract_for("categorize").stage, "categorization")
        self.assertEqual(adapter.contract_for("categorize").model, CATEGORIZATION_MODEL)

    def test_adapter_runs_contract_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            calls = []

            class FakeContract:
                action = "collect"
                agent_name = "CollectionAgent"
                stage = "collection"
                model = "fake-model"

                def run(self, output_root, project_id, llm_query=None, input_data=None):
                    calls.append((Path(output_root), project_id, llm_query(), input_data))
                    return {"status": "contract_done"}

            adapter = WorkflowActionAdapter(contracts=[FakeContract()])
            result = adapter.run("collect", output_root, "demo", llm_query=lambda: "llm")

        self.assertEqual(result, {"status": "contract_done"})
        self.assertEqual(calls, [(output_root, "demo", "llm", None)])

    def test_search_condition_contract_runs_search_condition_agent_with_setup_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            calls = []

            class FakeSearchConditionAgent:
                def __init__(self, output_dir, llm_query=None):
                    calls.append(("init", output_dir, llm_query))
                    self.output_dir = output_dir

                def run(self, input_data):
                    calls.append(("run", dict(input_data)))
                    project_path = Path(input_data["project_path"])
                    project_path.mkdir(parents=True)
                    search_conditions = {
                        **input_data,
                        "project_name": "Contract Review",
                        "search_terms": "LLM AND biomedicine",
                        "platforms": ["pubmed"],
                    }
                    (project_path / "search_conditions.json").write_text(json.dumps(search_conditions), encoding="utf-8")
                    return search_conditions

            def fake_llm_query():
                return "llm"

            self.assertTrue(hasattr(contracts, "SearchConditionAgentContract"))
            result = contracts.SearchConditionAgentContract(agent_cls=FakeSearchConditionAgent).run(
                output_root,
                "contract-review",
                llm_query=fake_llm_query,
                input_data={
                    "project_name": "Contract Review",
                    "description": "Review LLM in biomedicine",
                    "search_terms": "LLM AND biomedicine",
                    "platforms": ["pubmed"],
                },
            )
            written = read_json(output_root / "contract-review" / "search_conditions.json")

        self.assertEqual(calls[0], ("init", str(output_root), fake_llm_query))
        self.assertEqual(calls[1][0], "run")
        self.assertEqual(calls[1][1]["project_path"], str(output_root / "contract-review"))
        self.assertEqual(result["status"], "search_setup_done")
        self.assertEqual(result["search_conditions"]["project_name"], "Contract Review")
        self.assertEqual(written["search_terms"], "LLM AND biomedicine")

    def test_relevance_prompt_contract_runs_prompt_agent_for_prompt_relevance_stage(self):
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
                        "platforms": ["pubmed"],
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            class FakePromptAgent:
                def __init__(self, project_path, model, llm_query=None):
                    calls.append(("init", Path(project_path), model, llm_query))
                    self.project_path = Path(project_path)

                def generate_relevance_prompt(self, input_data):
                    calls.append(("generate_relevance_prompt", dict(input_data)))
                    prompt = {
                        "prompt_type": "relevance_check",
                        "task": "Include LLM systems in biomedicine.",
                        "instruction": "Return True or False.",
                    }
                    (self.project_path / "prompts").mkdir(parents=True)
                    (self.project_path / "prompts" / "relevance_prompt.json").write_text(json.dumps(prompt), encoding="utf-8")
                    return prompt

            def fake_llm_query():
                return "llm"

            self.assertTrue(hasattr(contracts, "RelevancePromptAgentContract"))
            result = contracts.RelevancePromptAgentContract(agent_cls=FakePromptAgent).run(output_root, "demo", llm_query=fake_llm_query)
            prompt = read_json(project_dir / "prompts" / "relevance_prompt.json")

        self.assertEqual(calls[0], ("init", project_dir, PROMPT_MODEL, fake_llm_query))
        self.assertEqual(calls[1][0], "generate_relevance_prompt")
        self.assertEqual(calls[1][1]["primary_topic"], "LLM systems")
        self.assertEqual(calls[1][1]["domain"], "biomedicine")
        self.assertEqual(result["status"], "relevance_prompt_generated")
        self.assertEqual(result["prompt_path"], str(project_dir / "prompts" / "relevance_prompt.json"))
        self.assertEqual(prompt["task"], "Include LLM systems in biomedicine.")

    def test_collection_contract_runs_collection_agent_against_search_conditions(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps(
                    {
                        "project_name": "Demo",
                        "search_terms": "LLM AND biomedicine",
                        "platforms": ["openalex"],
                        "max_results": 17,
                        "source_limits": {"openalex": 17},
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            class FakeCollectionAgent:
                def __init__(self, project_path):
                    calls.append(("init", Path(project_path)))
                    self.project_path = Path(project_path)

                def run(self, input_data):
                    calls.append(("run", dict(input_data)))
                    collected_dir = self.project_path / "collected"
                    collected_dir.mkdir(parents=True)
                    (collected_dir / "summary.json").write_text(
                        json.dumps({"total_papers": 2, "results": {"openalex": 2}, "platform_stats": {"openalex": 2}, "platform_errors": {}}),
                        encoding="utf-8",
                    )
                    (collected_dir / "openalex.jsonl").write_text(json.dumps({"id": "a"}) + "\n" + json.dumps({"id": "b"}) + "\n", encoding="utf-8")
                    return {
                        "collected_folder": str(collected_dir),
                        "total_papers": 2,
                        "platform_stats": {"openalex": 2},
                        "platform_errors": {},
                    }

            result = CollectionAgentContract(agent_cls=FakeCollectionAgent).run(output_root, "demo")
            summary = read_json(project_dir / "collected" / "summary.json")

        self.assertEqual(calls[0], ("init", project_dir))
        self.assertEqual(calls[1][0], "run")
        self.assertEqual(calls[1][1]["search_terms"], "LLM AND biomedicine")
        self.assertEqual(calls[1][1]["max_results_per_platform"], 17)
        self.assertEqual(result["status"], "collection_done")
        self.assertEqual(result["total"], 2)
        self.assertEqual(summary["platform_stats"], {"openalex": 2})

    def test_filtering_contract_runs_filtering_agent_against_collection_and_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            (project_dir / "collected").mkdir(parents=True)
            (project_dir / "prompts").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"date_range": {"start": "2020-01-01", "end": ""}}),
                encoding="utf-8",
            )
            (project_dir / "prompts" / "relevance_prompt.json").write_text(
                json.dumps(
                    {
                        "system_prompt": "Return true for relevant papers.",
                        "user_prompt_template": "{title}\n{abstract}",
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            class FakeFilteringAgent:
                def __init__(self, project_path, model):
                    calls.append(("init", Path(project_path), model))
                    self.project_path = Path(project_path)

                def run(self, input_data):
                    calls.append(("run", dict(input_data)))
                    filtered_dir = self.project_path / "filtered"
                    filtered_dir.mkdir(parents=True)
                    (filtered_dir / "filtered_papers.jsonl").write_text(
                        json.dumps({"title": "Keep", "is_relevant": True}) + "\n",
                        encoding="utf-8",
                    )
                    (filtered_dir / "filtering_stats.json").write_text(
                        json.dumps({"initial_count": 2, "after_similarity_dedup": 1, "final_count": 1}),
                        encoding="utf-8",
                    )
                    return {
                        "filtered_file": str(filtered_dir / "filtered_papers.jsonl"),
                        "filtered_count": 1,
                        "stats": {"initial_count": 2, "after_similarity_dedup": 1, "final_count": 1},
                    }

            result = FilteringAgentContract(agent_cls=FakeFilteringAgent).run(output_root, "demo")
            included = read_jsonl(project_dir / "filtered" / "included_papers.jsonl")
            excluded = read_jsonl(project_dir / "filtered" / "excluded_papers.jsonl")
            screening_stats = read_json(project_dir / "filtered" / "screening_stats.json")

        self.assertEqual(calls[0], ("init", project_dir, FILTERING_MODEL))
        self.assertEqual(calls[1][0], "run")
        self.assertEqual(calls[1][1]["collected_folder"], str(project_dir / "collected"))
        self.assertEqual(calls[1][1]["relevance_prompt"]["system_prompt"], "Return true for relevant papers.")
        self.assertEqual(calls[1][1]["date_range"], {"start_date": "2020-01-01", "end_date": ""})
        self.assertIs(calls[1][1]["auto_approve"], True)
        self.assertEqual(result["status"], "screening_done")
        self.assertEqual(result["included"], 1)
        self.assertEqual(result["excluded"], 0)
        self.assertEqual(included, [{"title": "Keep", "is_relevant": True}])
        self.assertEqual(excluded, [])
        self.assertEqual(screening_stats["total_screened"], 1)
        self.assertEqual(screening_stats["included_count"], 1)
        self.assertEqual(screening_stats["excluded_count"], 0)

    def test_prompt_contract_runs_prompt_agent_for_extraction_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            (project_dir / "filtered").mkdir(parents=True)
            (project_dir / "prompts").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps(
                    {
                        "project_name": "Demo",
                        "description": "Review AI tools for surgery",
                        "primary_topic": "AI tools",
                        "domain": "surgery",
                        "extraction_fields": "tool type, key findings",
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "prompts" / "relevance_prompt.json").write_text(
                json.dumps({"task": "Include AI tools in surgery."}),
                encoding="utf-8",
            )
            (project_dir / "filtered" / "included_papers.jsonl").write_text(
                json.dumps({"title": "Paper A", "abstract": "About surgical AI."}) + "\n",
                encoding="utf-8",
            )
            calls = []

            class FakePromptAgent:
                def __init__(self, project_path, model, llm_query=None):
                    calls.append(("init", Path(project_path), model, llm_query))
                    self.project_path = Path(project_path)

                def generate_extraction_prompt(self, input_data):
                    calls.append(("generate_extraction_prompt", dict(input_data)))
                    (self.project_path / "prompts").mkdir(parents=True, exist_ok=True)
                    (self.project_path / "extraction").mkdir(parents=True, exist_ok=True)
                    prompt = {
                        "prompt_type": "extraction",
                        "user_prompt_template": "Extract fields.",
                        "fields": ["tool type", "key findings"],
                    }
                    schema = {
                        "fields": [
                            {"name": "tool_type", "type": "Text", "description": "Tool type", "required": True},
                            {"name": "key_findings", "type": "Long text", "description": "Findings", "required": False},
                        ]
                    }
                    (self.project_path / "prompts" / "extraction_prompt.json").write_text(json.dumps(prompt), encoding="utf-8")
                    (self.project_path / "extraction" / "extraction_schema.json").write_text(json.dumps(schema), encoding="utf-8")
                    (self.project_path / "extraction" / "extraction_prompt.json").write_text(
                        json.dumps({"system_prompt": "system", "extraction_prompt": "Extract fields.", "schema": schema, "source": "llm"}),
                        encoding="utf-8",
                    )
                    return {"prompt": prompt, "schema": schema, "source": "llm"}

            def fake_llm_query():
                return "llm"

            result = PromptAgentContract(agent_cls=FakePromptAgent).run(output_root, "demo", llm_query=fake_llm_query)
            schema = read_json(project_dir / "extraction" / "extraction_schema.json")

        self.assertEqual(calls[0], ("init", project_dir, PROMPT_MODEL, fake_llm_query))
        self.assertEqual(calls[1][0], "generate_extraction_prompt")
        self.assertEqual(calls[1][1]["auto_approve"], True)
        self.assertEqual(calls[1][1]["relevance_prompt"]["task"], "Include AI tools in surgery.")
        self.assertEqual(calls[1][1]["included_papers"][0]["title"], "Paper A")
        self.assertEqual(result["status"], "schema_generated")
        self.assertEqual(result["source"], "llm")
        self.assertEqual(result["field_count"], 2)
        self.assertEqual(schema["fields"][0]["name"], "tool_type")

    def test_download_contract_runs_download_agent_against_included_papers(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            (project_dir / "filtered").mkdir(parents=True)
            (project_dir / "filtered" / "included_papers.jsonl").write_text(
                json.dumps({"title": "Paper A", "url": "https://example.test/a.pdf"}) + "\n",
                encoding="utf-8",
            )
            calls = []

            class FakeDownloadAgent:
                def __init__(self, project_path):
                    calls.append(("init", Path(project_path)))
                    self.project_path = Path(project_path)

                def run(self, input_data):
                    calls.append(("run", dict(input_data)))
                    pdf_dir = Path(input_data["download_folder"])
                    pdf_dir.mkdir(parents=True)
                    report = {"success": 1, "failed": 0, "downloaded": [{"title": "Paper A", "path": str(pdf_dir / "paper-a.pdf")}], "failed_papers": []}
                    (pdf_dir / "download_report.json").write_text(json.dumps(report), encoding="utf-8")
                    return {"download_folder": str(pdf_dir), "pdf_count": 1, "stats": report}

            result = DownloadAgentContract(agent_cls=FakeDownloadAgent).run(output_root, "demo")

        self.assertEqual(calls[0], ("init", project_dir))
        self.assertEqual(calls[1][0], "run")
        self.assertEqual(calls[1][1]["filtered_file"], str(project_dir / "filtered" / "included_papers.jsonl"))
        self.assertEqual(calls[1][1]["download_folder"], str(project_dir / "pdfs"))
        self.assertEqual(result["status"], "download_done")
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["failed"], 0)

    def test_extraction_contract_runs_extraction_agent_against_prompt_and_included_papers(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            (project_dir / "filtered").mkdir(parents=True)
            (project_dir / "prompts").mkdir(parents=True)
            (project_dir / "extraction").mkdir(parents=True)
            (project_dir / "pdfs").mkdir(parents=True)
            (project_dir / "filtered" / "included_papers.jsonl").write_text(
                json.dumps({"title": "Paper A", "pdf_downloaded": True}) + "\n",
                encoding="utf-8",
            )
            (project_dir / "prompts" / "extraction_prompt.json").write_text(
                json.dumps({"prompt_type": "extraction", "user_prompt_template": "Extract {paper_text}", "schema": {"fields": [{"name": "key_findings"}]}}),
                encoding="utf-8",
            )
            (project_dir / "pdfs" / "download_report.json").write_text(
                json.dumps({"success": 1, "failed": 0}),
                encoding="utf-8",
            )
            calls = []

            class FakeExtractionAgent:
                def __init__(self, project_path, model, llm_query=None):
                    calls.append(("init", Path(project_path), model, llm_query))
                    self.project_path = Path(project_path)

                def run(self, input_data):
                    calls.append(("run", dict(input_data)))
                    output_file = self.project_path / "extraction" / "extraction_results.jsonl"
                    output_file.write_text(json.dumps({"title": "Paper A", "key_findings": "Finding"}) + "\n", encoding="utf-8")
                    return {"output_file": str(output_file), "processed": 1, "errors": 0}

            def fake_llm_query():
                return "llm"

            result = ExtractionAgentContract(agent_cls=FakeExtractionAgent).run(output_root, "demo", llm_query=fake_llm_query)

        self.assertEqual(calls[0], ("init", project_dir, EXTRACTION_MODEL, fake_llm_query))
        self.assertEqual(calls[1][0], "run")
        self.assertEqual(calls[1][1]["filtered_file"], str(project_dir / "filtered" / "included_papers.jsonl"))
        self.assertEqual(calls[1][1]["download_folder"], str(project_dir / "pdfs"))
        self.assertEqual(calls[1][1]["extraction_prompt"]["prompt_type"], "extraction")
        self.assertEqual(calls[1][1]["download_report"], {"success": 1, "failed": 0})
        self.assertEqual(result["status"], "extraction_done")
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["errors"], 0)

    def test_adapter_rejects_unknown_action(self):
        adapter = WorkflowActionAdapter()

        with self.assertRaises(ValueError) as ctx:
            adapter.run("unknown-action", Path("/tmp"), "demo")

        self.assertIn("Unsupported action", str(ctx.exception))

    def test_categorization_contract_runs_lead_owned_result_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            (project_dir / "extraction").mkdir(parents=True)
            (project_dir / "extraction" / "extraction_results.jsonl").write_text(
                json.dumps({"title": "Paper A", "key_findings": "clinical diagnosis support"}) + "\n",
                encoding="utf-8",
            )

            def fake_llm(*, text_prompt, system_prompt):
                if "Create 3-8 meaningful categories" in text_prompt:
                    return (
                        json.dumps(
                            {
                                "field": "key_findings",
                                "categories": ["Clinical Decision Support"],
                                "category_descriptions": {"Clinical Decision Support": "Clinical support systems"},
                            }
                        ),
                        {},
                    )
                return ("Clinical Decision Support", {})

            result = CategorizationAnalysisContract().run(output_root, "demo", llm_query=fake_llm)
            mapping = read_json(project_dir / "categorization" / "categorization_mapping.json")
            rows = read_jsonl(project_dir / "categorization" / "categorized_results.jsonl")

        self.assertEqual(result["status"], "categorization_done")
        self.assertEqual(result["rows"], 1)
        self.assertEqual(mapping["field"], "key_findings")
        self.assertEqual(rows[0]["key_findings_category"], "Clinical Decision Support")


if __name__ == "__main__":
    unittest.main()
