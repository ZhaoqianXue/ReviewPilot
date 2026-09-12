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
                            "research_description": "Review LLM systems in biomedicine",
                            "concept_blocks": [
                                {"label": "Large language models (LLMs)", "role": "phenomenon", "eligibility_group": "technology", "required_for_eligibility": True, "query_terms": ["large language model", "large language models", "LLM", "LLMs"]},
                                {"label": "Biomedicine", "role": "context", "eligibility_group": "context", "required_for_eligibility": True, "query_terms": ["biomedicine", "biomedical"]},
                            ],
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
                    "date_range": {"start": "2020-01-01", "end": "2024-12-31"},
                    "derive_search_terms": True,
                    "model": "gpt-5.4-mini",
                }
            )

            written = json.loads((output_root / "llm-chat-review" / "search_conditions.json").read_text(encoding="utf-8"))

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model"], "gpt-5.4-mini")
        expected_query = '("large language model" OR "large language models" OR LLM OR LLMs) AND (biomedicine OR biomedical)'
        self.assertEqual(result["project_name"], "LLM Chat Review")
        self.assertEqual(result["search_terms"], expected_query)
        self.assertEqual(result["search_queries"], [{"name": "main", "query": expected_query}])
        self.assertEqual(result["platforms"], ["pubmed", "arxiv", "openalex"])
        self.assertEqual(written["platforms"], ["pubmed", "arxiv", "openalex"])
        self.assertNotIn('"platforms"', calls[0]["text_prompt"])
        self.assertNotIn('"source_limits"', calls[0]["text_prompt"])
        self.assertNotIn('"max_results"', calls[0]["text_prompt"])
        self.assertNotIn('"date_range"', calls[0]["text_prompt"])
        self.assertEqual(result["date_range"], {"start": "2020-01-01", "end": "2024-12-31"})
        self.assertEqual(result["lead_agent_reply"], "I generated a search setup from the chat request.")
        self.assertEqual(written["search_terms"], expected_query)
        self.assertNotEqual(written["search_terms"], "Review LLM systems in biomedicine")

    def test_derive_search_terms_preserves_canvas_source_limits_over_llm_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)

            def fake_llm_query(**_kwargs):
                return (
                    json.dumps(
                        {
                            "reply": "I generated a search setup from the chat request.",
                            "research_description": "Review LLM systems in care delivery",
                            "concept_blocks": [
                                {"label": "Large language models (LLMs)", "role": "phenomenon", "eligibility_group": "technology", "required_for_eligibility": True, "query_terms": ["large language model", "LLM"]},
                                {"label": "Care delivery", "role": "context", "eligibility_group": "context", "required_for_eligibility": True, "query_terms": ["care delivery"]},
                            ],
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

    def test_display_keywords_reject_query_syntax_and_duplicates(self):
        invalid_values = [
            ["LLM*", "Biomedicine"],
            ["LLM OR GPT", "Biomedicine"],
            ['"large language model"', "Biomedicine"],
            ["Biomedicine", "biomedicine"],
        ]

        for keywords in invalid_values:
            with self.subTest(keywords=keywords), self.assertRaisesRegex(ValueError, "keywords"):
                SearchConditionAgent._validate_display_keywords(keywords)

    def test_concept_blocks_are_the_single_authority_for_labels_and_query(self):
        blocks = SearchConditionAgent._validate_concept_blocks([
            {"label": "Telemedicine", "role": "intervention_or_exposure", "eligibility_group": "intervention", "required_for_eligibility": True, "query_terms": ["telemedicine", "telehealth"]},
            {"label": "Medication adherence", "role": "outcome", "eligibility_group": "outcome", "required_for_eligibility": True, "query_terms": ["medication adherence"]},
            {"label": "Implementation context", "role": "analytical_dimension", "eligibility_group": "analysis", "required_for_eligibility": False, "query_terms": []},
        ])

        self.assertEqual([block["label"] for block in blocks], ["Telemedicine", "Medication adherence", "Implementation context"])
        self.assertEqual(SearchConditionAgent._build_boolean_query(blocks), '(telemedicine OR telehealth) AND ("medication adherence")')

    def test_atomic_alternative_contexts_are_separate_labels_in_one_or_group(self):
        blocks = SearchConditionAgent._validate_concept_blocks([
            {"label": "Large language models (LLMs)", "role": "phenomenon", "eligibility_group": "technology", "required_for_eligibility": True, "query_terms": ["large language model", "large language models", "LLM", "LLMs"]},
            {"label": "Biomedical research", "role": "context", "eligibility_group": "context", "required_for_eligibility": True, "query_terms": ["biomedical research"]},
            {"label": "Clinical care", "role": "context", "eligibility_group": "context", "required_for_eligibility": True, "query_terms": ["clinical care"]},
        ])

        self.assertEqual([block["label"] for block in blocks], ["Large language models (LLMs)", "Biomedical research", "Clinical care"])
        self.assertEqual(
            SearchConditionAgent._build_boolean_query(blocks),
            '("large language model" OR "large language models" OR LLM OR LLMs) AND ("biomedical research" OR "clinical care")',
        )

    def test_concept_block_validation_rejects_nonportable_or_incoherent_shapes(self):
        invalid_blocks = [
            [{"label": "Topic", "role": "unknown", "eligibility_group": "topic", "required_for_eligibility": True, "query_terms": ["topic"]}],
            [{"label": "Topic", "role": "phenomenon", "eligibility_group": "topic", "required_for_eligibility": "yes", "query_terms": ["topic"]}],
            [{"label": "Topic", "role": "phenomenon", "eligibility_group": "topic", "required_for_eligibility": True, "query_terms": ["topic*"]}],
            [{"label": "Topic", "role": "phenomenon", "eligibility_group": "topic", "required_for_eligibility": False, "query_terms": []}],
            [{"label": "Topic", "role": "phenomenon", "required_for_eligibility": True, "query_terms": ["topic"]}],
            [{"label": "Topic", "role": "phenomenon", "eligibility_group": "Topic group", "required_for_eligibility": True, "query_terms": ["topic"]}],
            [
                {"label": "Technology", "role": "phenomenon", "eligibility_group": "scope", "required_for_eligibility": True, "query_terms": ["technology"]},
                {"label": "Care", "role": "context", "eligibility_group": "scope", "required_for_eligibility": True, "query_terms": ["care"]},
            ],
        ]

        for blocks in invalid_blocks:
            with self.subTest(blocks=blocks), self.assertRaisesRegex(ValueError, "concept|eligibility|query_terms"):
                SearchConditionAgent._validate_concept_blocks(blocks)

    def test_search_setup_prompt_defines_keywords_as_scope_labels(self):
        agent = SearchConditionAgent()

        prompt = agent._llm_search_setup_prompt({}, "Review LLM use in clinical care")
        system_prompt = agent._llm_search_setup_system_prompt()

        self.assertIn('"concept_blocks"', prompt)
        self.assertIn("Return 1 to 8 concept blocks", prompt)
        self.assertIn("no more than 8 query terms", prompt)
        self.assertIn('"eligibility_group"', prompt)
        self.assertIn("one atomic user-facing concept", prompt)
        self.assertIn("Use only the keys shown in the schema", prompt)
        self.assertNotIn('"search_terms"', prompt)
        self.assertNotIn('"keywords"', prompt)
        self.assertNotIn('"platforms"', prompt)
        self.assertIn("scope-faithful concept strategies", system_prompt)
        self.assertIn("eligibility requirements from analytical dimensions", system_prompt)
        for history_specific_text in ("LLM-only", "Generative AI", "Foundation models", "Changing interaction", "Support and use"):
            self.assertNotIn(history_specific_text, system_prompt)
        self.assertNotIn("Do not", system_prompt)
        self.assertNotIn("do not", prompt)

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
