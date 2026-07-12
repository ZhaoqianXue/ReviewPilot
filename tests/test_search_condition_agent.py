import json
import tempfile
import unittest
from pathlib import Path

from agents.search_condition_agent import SearchConditionAgent


class SearchConditionAgentTests(unittest.TestCase):
    def test_derive_search_terms_uses_llm_result_without_deterministic_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            calls = []

            def fake_llm_query(*, text_prompt, system_prompt, model, provider):
                calls.append(
                    {
                        "text_prompt": text_prompt,
                        "system_prompt": system_prompt,
                        "model": model,
                        "provider": provider,
                    }
                )
                return (
                    json.dumps(
                        {
                            "reply": "I generated a search setup from the chat request.",
                            "project_name": "LLM Biomedicine Review",
                            "research_description": "Review LLM systems in biomedicine",
                            "search_terms": "LLM_JSON_QUERY",
                            "search_queries": [{"name": "main", "query": "LLM_JSON_QUERY"}],
                            "platforms": ["pubmed", "openalex"],
                            "date_range": {"start": "2021-01-01", "end": ""},
                            "max_results": 25,
                            "source_limits": {"pubmed": 25, "openalex": 25},
                            "primary_topic": "LLM systems",
                            "domain": "biomedicine",
                            "extracted_concepts": {
                                "primary_topics": ["LLM systems"],
                                "domains": ["biomedicine"],
                                "methods": ["survey"],
                            },
                            "keywords": ["LLM systems", "biomedicine"],
                        }
                    ),
                    {"model": "test-model"},
                )

            agent = SearchConditionAgent(output_dir=str(output_root), llm_query=fake_llm_query)
            result = agent.run(
                {
                    "project_name": "LLM Chat Review",
                    "project_path": str(output_root / "llm-chat-review"),
                    "description": "Review LLM systems in biomedicine",
                    "search_terms": "Review LLM systems in biomedicine",
                    "platforms": ["pubmed", "arxiv", "openalex"],
                    "source_limits": {"pubmed": 50, "arxiv": 50, "openalex": 50},
                    "max_results": 50,
                    "derive_search_terms": True,
                    "model": "gpt-5.4-mini",
                }
            )

            written = json.loads((output_root / "llm-chat-review" / "search_conditions.json").read_text(encoding="utf-8"))

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model"], "gpt-5.4-mini")
        self.assertEqual(result["project_name"], "LLM Biomedicine Review")
        self.assertEqual(result["search_terms"], "LLM_JSON_QUERY")
        self.assertEqual(result["platforms"], ["pubmed", "arxiv", "openalex"])
        self.assertEqual(written["platforms"], ["pubmed", "arxiv", "openalex"])
        self.assertIn('"platforms": ["pubmed", "arxiv", "openalex"],', calls[0]["text_prompt"])
        self.assertEqual(result["date_range"], {"start": "2021-01-01", "end": ""})
        self.assertEqual(result["lead_agent_reply"], "I generated a search setup from the chat request.")
        self.assertEqual(written["search_terms"], "LLM_JSON_QUERY")
        self.assertNotEqual(written["search_terms"], "Review LLM systems in biomedicine")

    def test_derive_search_terms_preserves_canvas_source_limits_over_llm_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)

            def fake_llm_query(**_kwargs):
                return (
                    json.dumps(
                        {
                            "reply": "I generated a search setup from the chat request.",
                            "project_name": "LLM Care Review",
                            "research_description": "Review LLM systems in care delivery",
                            "search_terms": "LLM_JSON_QUERY",
                            "search_queries": [{"name": "main", "query": "LLM_JSON_QUERY"}],
                            "platforms": ["dblp", "openalex", "pubmed"],
                            "date_range": {"start": "2021-01-01", "end": ""},
                            "max_results": 50,
                            "source_limits": {"dblp": 50, "openalex": 50, "pubmed": 50},
                            "primary_topic": "LLM systems",
                            "domain": "care delivery",
                            "extracted_concepts": {
                                "primary_topics": ["LLM systems"],
                                "domains": ["care delivery"],
                                "methods": ["survey"],
                            },
                        }
                    ),
                    {"model": "test-model"},
                )

            agent = SearchConditionAgent(output_dir=str(output_root), llm_query=fake_llm_query)
            result = agent.run(
                {
                    "project_name": "LLM Chat Review",
                    "project_path": str(output_root / "llm-chat-review"),
                    "description": "Review LLM systems in care delivery",
                    "search_terms": "Review LLM systems in care delivery",
                    "platforms": ["pubmed", "arxiv", "openalex"],
                    "source_limits": {"pubmed": 10, "arxiv": 20, "openalex": 30},
                    "max_results": 10,
                    "derive_search_terms": True,
                    "model": "gpt-5.4-mini",
                }
            )

            written = json.loads((output_root / "llm-chat-review" / "search_conditions.json").read_text(encoding="utf-8"))

        self.assertEqual(result["platforms"], ["pubmed", "arxiv", "openalex"])
        self.assertEqual(result["max_results"], 30)
        self.assertEqual(result["max_results_per_platform"], 30)
        self.assertEqual(result["source_limits"], {"pubmed": 10, "arxiv": 20, "openalex": 30})
        self.assertEqual(written["platforms"], ["pubmed", "arxiv", "openalex"])
        self.assertEqual(written["source_limits"], {"pubmed": 10, "arxiv": 20, "openalex": 30})

    def test_derive_search_terms_fails_loudly_when_llm_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)

            def failing_llm_query(**_kwargs):
                raise RuntimeError("LLM unavailable")

            agent = SearchConditionAgent(output_dir=str(output_root), llm_query=failing_llm_query)

            with self.assertRaisesRegex(RuntimeError, "LLM unavailable"):
                agent.run(
                    {
                        "project_name": "Failing LLM Review",
                        "project_path": str(output_root / "failing-llm-review"),
                        "description": "Review LLM systems in biomedicine",
                        "search_terms": "Review LLM systems in biomedicine",
                        "platforms": ["pubmed"],
                        "derive_search_terms": True,
                        "model": "gpt-5.4-mini",
                    }
                )

        self.assertFalse((output_root / "failing-llm-review" / "search_conditions.json").exists())


if __name__ == "__main__":
    unittest.main()
