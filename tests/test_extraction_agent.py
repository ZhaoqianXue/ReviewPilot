import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.extraction_agent import ExtractionAgent
from reviewpilot_core.model_policy import EXTRACTION_MODEL
from reviewpilot_core.project_store import read_json, read_jsonl


class ExtractionAgentTests(unittest.TestCase):
    def test_extract_one_uses_production_pdf_path_without_writing_formal_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            pdf_path = project_dir / "pdfs" / "row1_paper.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.write_bytes(b"%PDF-1.4\n")
            paper = {"id": "p1", "title": "Paper A", "source": "pubmed", "pdf_path": str(pdf_path), "pdf_downloaded": True}
            prompt = {"system_prompt": "Return JSON.", "user_prompt_template": "Extract:\n{paper_text}"}
            agent = ExtractionAgent(
                project_dir,
                llm_query=lambda **kwargs: (json.dumps({"methods": "Survey"}), {"total_tokens": 1}),
                pdf_reader=lambda path: "paper text",
            )

            row = agent.extract_one(
                paper=paper,
                row_number=1,
                extraction_prompt=prompt,
                pdf_folder=project_dir / "pdfs",
                pdf_files=[pdf_path],
            )

        self.assertEqual(row["extraction_status"], "success")
        self.assertEqual(row["extraction_source"], "pdf")
        self.assertEqual(row["methods"], "Survey")
        self.assertFalse((project_dir / "extraction" / "extraction_results.jsonl").exists())
        self.assertFalse((project_dir / "extraction" / "extraction_stats.json").exists())

    def test_extract_one_supports_production_web_search_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            paper = {"id": "p1", "title": "Paper A", "web_search_fallback_pending": True, "pdf_downloaded": False}
            agent = ExtractionAgent(
                project_dir,
                web_search_query=lambda **kwargs: ({"methods": "Public evidence", "source_urls": ["https://example.test/a"]}, {}),
            )
            row = agent.extract_one(paper=paper, row_number=1, extraction_prompt={}, pdf_folder=project_dir / "pdfs", pdf_files=[])

        self.assertEqual(row["extraction_status"], "success")
        self.assertEqual(row["extraction_source"], "web_search_fallback")
        self.assertEqual(row["methods"], "Public evidence")

    def test_one_shot_writer_failure_rolls_back_pdf_and_web_results(self):
        for source in ("pdf", "web"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                project_dir = Path(tmp) / "demo"
                filtered_dir = project_dir / "filtered"
                filtered_dir.mkdir(parents=True)
                paper = {"id": source, "title": f"{source} paper", "pdf_downloaded": source == "pdf"}
                pdf_reader = None
                web_search_query = None
                if source == "pdf":
                    pdf_path = project_dir / "pdfs" / "row1.pdf"
                    pdf_path.parent.mkdir(parents=True)
                    pdf_path.write_bytes(b"%PDF-1.4\n")
                    paper["pdf_path"] = str(pdf_path)
                    pdf_reader = lambda path: "valid paper text"
                else:
                    paper["web_search_fallback_pending"] = True
                    web_search_query = lambda **kwargs: (
                        {"key_findings": "found", "source_urls": ["https://example.test/paper"]},
                        {},
                    )
                included_file = filtered_dir / "included_papers.jsonl"
                included_file.write_text(json.dumps(paper) + "\n", encoding="utf-8")
                output_file = project_dir / "extraction" / "extraction_results.jsonl"
                output_file.parent.mkdir(parents=True, exist_ok=True)
                previous = b'{"paper_id":"previous","extraction_status":"success"}\n'
                output_file.write_bytes(previous)
                agent = ExtractionAgent(
                    project_dir,
                    llm_query=lambda **kwargs: (json.dumps({"key_findings": "found"}), {}),
                    pdf_reader=pdf_reader,
                    web_search_query=web_search_query,
                )

                with patch("agents.extraction_agent.append_jsonl", side_effect=[OSError("disk full"), None]):
                    with self.assertRaisesRegex(OSError, "disk full"):
                        agent.run(
                            {
                                "filtered_file": str(included_file),
                                "download_folder": str(project_dir / "pdfs"),
                                "extraction_prompt": {},
                            }
                        )

                self.assertEqual(output_file.read_bytes(), previous)
                self.assertEqual(list(output_file.parent.glob(".*.tmp")), [])

    def test_direct_openai_extraction_uses_max_completion_tokens_for_gpt5_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            calls = []

            class FakeMessage:
                content = json.dumps({"key_findings": "ok"})

            class FakeChoice:
                message = FakeMessage()

            class FakeUsage:
                prompt_tokens = 10
                completion_tokens = 5

            class FakeCompletions:
                def create(self, **kwargs):
                    calls.append(kwargs)
                    return type("Response", (), {"choices": [FakeChoice()], "usage": FakeUsage()})()

            class FakeClient:
                chat = type("Chat", (), {"completions": FakeCompletions()})()

            agent = ExtractionAgent(project_dir)
            agent.client = FakeClient()
            response, _cost = agent._extract_with_llm("paper", "Return JSON.", "Return JSON for {paper_text}")

        self.assertEqual(json.loads(response)["key_findings"], "ok")
        self.assertIn("max_completion_tokens", calls[0])
        self.assertNotIn("max_tokens", calls[0])

    def test_extraction_template_with_json_example_does_not_break_formatting(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            agent = ExtractionAgent(project_dir, llm_query=lambda **kwargs: (json.dumps({"datasets_used": "MIMIC"}), {"total_tokens": 1}))

            response, _cost = agent._extract_with_llm(
                "paper text",
                "Return JSON.",
                'Return JSON like:\n{\n  "datasets_used": ""\n}\n\nPAPER:\n{paper_text}',
                llm_query=agent.llm_query,
            )

        self.assertEqual(json.loads(response)["datasets_used"], "MIMIC")

    def test_run_consumes_prompt_and_included_papers_and_writes_extraction_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            pdf_path = project_dir / "pdfs" / "row1_openalex_2026_Open_PDF_keep.pdf"
            filtered_dir = project_dir / "filtered"
            prompts_dir = project_dir / "prompts"
            pdf_path.parent.mkdir(parents=True)
            filtered_dir.mkdir(parents=True)
            prompts_dir.mkdir(parents=True)
            pdf_path.write_bytes(b"%PDF-1.4\n")
            included_file = filtered_dir / "included_papers.jsonl"
            included_file.write_text(
                "\n".join(
                    [
                        json.dumps({"id": "keep", "title": "Open PDF", "pdf_downloaded": True, "pdf_path": str(pdf_path)}),
                        json.dumps(
                            {
                                "id": "closed",
                                "title": "Closed PDF",
                                "pdf_downloaded": False,
                                "retrieval_status": "subscribed_unavailable",
                                "web_search_fallback_pending": True,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            extraction_prompt = {
                "prompt_type": "extraction",
                "system_prompt": "Extract structured paper data.",
                "user_prompt_template": "Extract fields from this paper:\n{paper_text}",
                "schema": {"fields": [{"name": "key_findings", "description": "Findings"}]},
            }
            (prompts_dir / "extraction_prompt.json").write_text(json.dumps(extraction_prompt), encoding="utf-8")
            calls = []
            web_search_calls = []

            def fake_reader(path):
                self.assertEqual(Path(path), pdf_path)
                return "The paper reports a 20 percent accuracy gain."

            def fake_llm_query(*, text_prompt, system_prompt, model, provider):
                calls.append((text_prompt, system_prompt, model, provider))
                return (json.dumps({"key_findings": "20 percent accuracy gain"}), {"total_tokens": 12})

            def fake_web_search_query(*, paper, extraction_prompt, model):
                web_search_calls.append((dict(paper), extraction_prompt, model))
                return (
                    {
                        "key_findings": "Public abstract reports clinical deployment barriers",
                        "source_urls": ["https://pubmed.ncbi.nlm.nih.gov/123"],
                        "confidence": "medium",
                    },
                    {"total_tokens": 20},
                )

            result = ExtractionAgent(
                project_dir,
                llm_query=fake_llm_query,
                pdf_reader=fake_reader,
                web_search_query=fake_web_search_query,
            ).run(
                {
                    "filtered_file": str(included_file),
                    "download_folder": str(project_dir / "pdfs"),
                    "extraction_prompt": extraction_prompt,
                }
            )
            rows = read_jsonl(project_dir / "extraction" / "extraction_results.jsonl")
            stats = read_json(project_dir / "extraction" / "extraction_stats.json")

        self.assertEqual(calls[0][2], EXTRACTION_MODEL)
        self.assertEqual(web_search_calls[0][0]["title"], "Closed PDF")
        self.assertEqual(web_search_calls[0][2], EXTRACTION_MODEL)
        self.assertIn("20 percent accuracy", calls[0][0])
        self.assertEqual(result["status"], "extraction_done")
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["web_search_fallback"], 1)
        self.assertEqual(result["pending_web_search_fallback"], 0)
        self.assertEqual(rows[0]["title"], "Open PDF")
        self.assertEqual(rows[0]["extraction_source"], "pdf")
        self.assertEqual(rows[0]["key_findings"], "20 percent accuracy gain")
        self.assertEqual(rows[1]["title"], "Closed PDF")
        self.assertEqual(rows[1]["extraction_source"], "web_search_fallback")
        self.assertEqual(rows[1]["extraction_status"], "success")
        self.assertEqual(rows[1]["source_urls"], ["https://pubmed.ncbi.nlm.nih.gov/123"])
        self.assertEqual(rows[1]["confidence"], "medium")
        self.assertEqual(rows[1]["key_findings"], "Public abstract reports clinical deployment barriers")
        self.assertEqual(stats["processed"], 2)
        self.assertEqual(stats["web_search_fallback"], 1)
        self.assertEqual(stats["pending_web_search_fallback"], 0)

    def test_run_sanitizes_pdf_text_with_illegal_unicode_before_llm_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            pdf_path = project_dir / "pdfs" / "row1_arxiv_2026_surrogate.pdf"
            filtered_dir = project_dir / "filtered"
            pdf_path.parent.mkdir(parents=True)
            filtered_dir.mkdir(parents=True)
            pdf_path.write_bytes(b"%PDF-1.4\n")
            included_file = filtered_dir / "included_papers.jsonl"
            included_file.write_text(
                json.dumps({"id": "paper-1", "title": "Surrogate PDF", "pdf_downloaded": True, "pdf_path": str(pdf_path)})
                + "\n",
                encoding="utf-8",
            )
            calls = []

            def fake_reader(path):
                self.assertEqual(Path(path), pdf_path)
                return "The PDF text contains an isolated surrogate: \ud835"

            def fake_llm_query(*, text_prompt, system_prompt, model, provider):
                text_prompt.encode("utf-8")
                system_prompt.encode("utf-8")
                self.assertNotIn("\ud835", text_prompt)
                calls.append(text_prompt)
                return (json.dumps({"key_findings": "surrogate handled"}), {"total_tokens": 10})

            result = ExtractionAgent(project_dir, llm_query=fake_llm_query, pdf_reader=fake_reader).run(
                {
                    "filtered_file": str(included_file),
                    "download_folder": str(project_dir / "pdfs"),
                    "extraction_prompt": {
                        "system_prompt": "Return JSON.",
                        "user_prompt_template": "Extract fields:\n{paper_text}",
                    },
                }
            )
            rows = read_jsonl(project_dir / "extraction" / "extraction_results.jsonl")

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["errors"], 0)
        self.assertEqual(rows[0]["extraction_status"], "success")
        self.assertEqual(rows[0]["key_findings"], "surrogate handled")
        self.assertEqual(len(calls), 1)

    def test_web_search_fallback_uses_responses_api_web_search_tool_when_no_injected_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            (project_dir / "filtered").mkdir(parents=True)
            (project_dir / "prompts").mkdir(parents=True)
            included_file = project_dir / "filtered" / "included_papers.jsonl"
            included_file.write_text(
                json.dumps(
                    {
                        "id": "closed",
                        "title": "Closed PDF",
                        "doi": "10.1000/example",
                        "pdf_downloaded": False,
                        "retrieval_status": "subscribed_unavailable",
                        "web_search_fallback_pending": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            extraction_prompt = {
                "prompt_type": "extraction",
                "schema": {"fields": [{"name": "key_findings", "description": "Findings"}]},
            }
            response_calls = []

            class FakeUsage:
                input_tokens = 10
                output_tokens = 5

            class FakeResponse:
                output_text = json.dumps(
                    {
                        "key_findings": "Found through web search",
                        "source_urls": ["https://example.org/paper"],
                        "confidence": "low",
                    }
                )
                usage = FakeUsage()

            class FakeResponses:
                def create(self, **kwargs):
                    response_calls.append(kwargs)
                    return FakeResponse()

            class FakeClient:
                responses = FakeResponses()

            agent = ExtractionAgent(project_dir)
            agent.client = FakeClient()
            result = agent.run(
                {
                    "filtered_file": str(included_file),
                    "download_folder": str(project_dir / "pdfs"),
                    "extraction_prompt": extraction_prompt,
                }
            )
            rows = read_jsonl(project_dir / "extraction" / "extraction_results.jsonl")

        self.assertEqual(result["web_search_fallback"], 1)
        self.assertEqual(response_calls[0]["tools"][0]["type"], "web_search")
        self.assertIn("allowed_domains", response_calls[0]["tools"][0]["filters"])
        self.assertNotIn("blocked_domains", response_calls[0]["tools"][0]["filters"])
        self.assertEqual(response_calls[0]["tool_choice"], "required")
        self.assertIn("web_search_call.action.sources", response_calls[0]["include"])
        self.assertEqual(rows[0]["extraction_source"], "web_search_fallback")
        self.assertEqual(rows[0]["source_urls"], ["https://example.org/paper"])

    def test_run_does_not_eagerly_initialize_client_for_overridden_web_search_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            (project_dir / "filtered").mkdir(parents=True)
            included_file = project_dir / "filtered" / "included_papers.jsonl"
            included_file.write_text(
                json.dumps(
                    {
                        "id": "fallback-only",
                        "title": "Fallback Only Paper",
                        "pdf_downloaded": False,
                        "web_search_fallback_pending": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            extraction_prompt = {
                "system_prompt": "Extract structured paper data.",
                "user_prompt_template": "Extract fields from this paper:\n{paper_text}",
                "schema": {"fields": [{"name": "key_findings", "description": "Findings"}]},
            }
            agent = ExtractionAgent(project_dir, llm_query=lambda **kwargs: ("{}", {"total_tokens": 0}))
            agent._query_web_search_extraction = lambda paper, prompt: (
                json.dumps(
                    {
                        "key_findings": "Recovered without credentials",
                        "source_urls": ["https://example.org/fallback-only"],
                        "confidence": "medium",
                    }
                ),
                {"total_tokens": 4},
            )

            def fail_client_initialization():
                raise AssertionError("fallback override must not initialize the OpenAI client")

            agent._init_client = fail_client_initialization
            result = agent.run(
                {
                    "filtered_file": str(included_file),
                    "download_folder": str(project_dir / "pdfs"),
                    "extraction_prompt": extraction_prompt,
                }
            )
            rows = read_jsonl(project_dir / "extraction" / "extraction_results.jsonl")

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["web_search_fallback"], 1)
        self.assertEqual(rows[0]["extraction_status"], "success")
        self.assertEqual(rows[0]["extraction_source"], "web_search_fallback")
        self.assertEqual(rows[0]["key_findings"], "Recovered without credentials")

    def test_web_search_fallback_uses_paper_metadata_url_when_response_has_no_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            (project_dir / "filtered").mkdir(parents=True)
            included_file = project_dir / "filtered" / "included_papers.jsonl"
            included_file.write_text(
                json.dumps(
                    {
                        "id": "closed",
                        "title": "Closed PDF",
                        "doi": "10.1000/example",
                        "url": "https://pubmed.ncbi.nlm.nih.gov/123",
                        "pdf_downloaded": False,
                        "retrieval_status": "unavailable",
                        "web_search_fallback_pending": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            def source_less_web_search_query(*, paper, extraction_prompt, model):
                return json.dumps({"key_findings": "Found but source field omitted"}), {"input_tokens": 10, "output_tokens": 5}

            result = ExtractionAgent(project_dir, web_search_query=source_less_web_search_query).run(
                {
                    "filtered_file": str(included_file),
                    "download_folder": str(project_dir / "pdfs"),
                    "extraction_prompt": {"schema": {"fields": [{"name": "key_findings"}]}},
                }
            )
            rows = read_jsonl(project_dir / "extraction" / "extraction_results.jsonl")

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["errors"], 0)
        self.assertEqual(result["web_search_fallback"], 1)
        self.assertEqual(result["pending_web_search_fallback"], 0)
        self.assertEqual(rows[0]["extraction_source"], "web_search_fallback")
        self.assertEqual(rows[0]["extraction_status"], "success")
        self.assertEqual(rows[0]["source_urls"], ["https://pubmed.ncbi.nlm.nih.gov/123", "https://doi.org/10.1000/example"])
        self.assertEqual(rows[0]["confidence"], "low")

    def test_failed_web_search_fallback_still_writes_source_and_confidence_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            (project_dir / "filtered").mkdir(parents=True)
            included_file = project_dir / "filtered" / "included_papers.jsonl"
            included_file.write_text(
                json.dumps(
                    {
                        "id": "closed",
                        "title": "Closed PDF",
                        "pdf_downloaded": False,
                        "retrieval_status": "subscribed_unavailable",
                        "web_search_fallback_pending": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            def failing_web_search_query(*, paper, extraction_prompt, model):
                raise RuntimeError("search unavailable")

            result = ExtractionAgent(project_dir, web_search_query=failing_web_search_query).run(
                {
                    "filtered_file": str(included_file),
                    "download_folder": str(project_dir / "pdfs"),
                    "extraction_prompt": {"schema": {"fields": [{"name": "key_findings"}]}},
                }
            )
            rows = read_jsonl(project_dir / "extraction" / "extraction_results.jsonl")

        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["pending_web_search_fallback"], 1)
        self.assertEqual(rows[0]["extraction_source"], "web_search_fallback")
        self.assertEqual(rows[0]["source_urls"], [])
        self.assertEqual(rows[0]["confidence"], "low")
        self.assertTrue(rows[0]["web_search_fallback_pending"])


if __name__ == "__main__":
    unittest.main()
