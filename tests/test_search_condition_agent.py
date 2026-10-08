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
                            "title": "Test review",
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
                            "title": "Test review",
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

    def test_derive_prompt_carries_data_and_contract_while_method_rules_live_in_the_skill(self):
        agent = SearchConditionAgent()
        prompt = agent._derive_prompt({}, "Review LLM use in clinical care", interpret_settings=False)

        self.assertIn('"concept_blocks"', prompt)
        self.assertIn('"eligibility_group"', prompt)
        self.assertIn("Return 1 to 12 concept blocks", prompt)
        self.assertNotIn('"search_settings"', prompt)
        self.assertNotIn('"search_terms"', prompt)
        self.assertNotIn("atomic", prompt)
        self.assertNotIn("exact synonyms", prompt)
        self.assertNotIn("do not", prompt.lower())
        refine = agent._refine_prompt({"description": "Q"}, [], "Add X", [])
        for text in (prompt, refine):
            self.assertNotIn("never", text.lower())
            self.assertNotIn("Database names", text)  # the settings rule lives in the skill
            self.assertIn("a concept that is not required has 0 to 12", text)
            self.assertNotIn("population_or_context", text)
        self.assertIn("search-strategy skill", agent.system_prompt())
        skill = (Path(__file__).resolve().parents[1] / ".agents/skills/systematic-review-search-strategy/SKILL.md").read_text()
        for rule in ("one atomic concept", "exact synonyms", "Revise an existing strategy", "shared coverage",
                     "Database names", "Publication language", "unambiguous", "broader umbrella"):
            self.assertIn(rule, skill)
        self.assertNotIn("Controlled-vocabulary", skill)
        self.assertNotIn("languages, and document types", skill)

    def test_provided_concept_blocks_are_saved_without_an_llm_call(self):
        blocks = [
            {"label": "Telemedicine", "role": "intervention_or_exposure", "eligibility_group": "intervention", "required_for_eligibility": True, "query_terms": ["telemedicine", "telehealth"]},
            {"label": "Alzheimer's disease", "role": "condition", "eligibility_group": "condition", "required_for_eligibility": True, "query_terms": ["Alzheimer's disease"]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            agent = SearchConditionAgent(output_dir=tmp, llm_query=lambda **_kwargs: self.fail("no LLM call expected"))
            result = agent.run({"project_name": "Telemedicine", "project_path": str(Path(tmp) / "t"), "description": "Telemedicine for Alzheimer's",
                                "concept_blocks": blocks, "platforms": ["pubmed"], "source_limits": {"pubmed": 25}})
        self.assertEqual(result["search_terms"], '(telemedicine OR telehealth) AND ("Alzheimer\'s disease")')
        self.assertEqual(result["keywords"], ["Telemedicine", "Alzheimer's disease"])
        self.assertEqual(result["primary_topic"], "Telemedicine")
        self.assertEqual(result["domain"], "Alzheimer's disease")
        self.assertEqual(result["source_limits"], {"pubmed": 25})
        self.assertEqual(result["generated_by"], "user")

    def test_legacy_query_becomes_concept_blocks_and_unsupported_shapes_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = SearchConditionAgent(output_dir=tmp)
            base = {"project_name": "Legacy", "project_path": str(Path(tmp) / "legacy"), "description": "Legacy review", "platforms": ["pubmed"]}
            result = agent.run({**base, "search_terms": '(LLM OR "large language model") AND (radiology)'})
            self.assertEqual([block["query_terms"] for block in result["concept_blocks"]], [["LLM", "large language model"], ["radiology"]])
            with self.assertRaisesRegex(ValueError, "AND together groups"):
                agent.run({**base, "search_terms": "(LLM OR GPT) AND NOT radiology"})
            with self.assertRaisesRegex(ValueError, "Unsupported search source"):
                agent.run({**base, "search_terms": "LLM", "platforms": ["scopus"]})

    def test_refine_returns_a_complete_setup_without_writing(self):
        current = {"project_name": "Chatbots", "description": "LLM chatbots for mental health", "platforms": ["pubmed", "arxiv"],
                   "source_limits": {"pubmed": 10, "arxiv": 10}, "date_range": {"start": "", "end": "2026-10-07"},
                   "concept_blocks": [
                       {"label": "Large language models (LLMs)", "role": "phenomenon", "eligibility_group": "technology", "required_for_eligibility": True, "query_terms": ["large language model", "LLM"]},
                       {"label": "Mental health", "role": "context", "eligibility_group": "context", "required_for_eligibility": True, "query_terms": ["mental health"]}]}
        added = current["concept_blocks"] + [{"label": "Chatbots", "role": "intervention_or_exposure", "eligibility_group": "system", "required_for_eligibility": True, "query_terms": ["chatbot", "conversational agent"]}]
        cases = (
            ({"reply": "Added Chatbots as a required concept.", "concept_blocks": added, "search_settings": {"max_results": 25}}, True),
            ({"reply": "The setup covers LLMs and mental health.", "concept_blocks": None, "search_settings": {}}, False),
        )
        for reply, changed in cases:
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as tmp:
                prompts = []
                agent = SearchConditionAgent(output_dir=tmp, llm_query=lambda *, text_prompt, **_kwargs: (prompts.append(text_prompt), (json.dumps(reply), {}))[1])
                result = agent.refine({"current": {**current, "project_path": tmp}, "message": "Require chatbots and use 25 results per source"})
                self.assertEqual(result["changed"], changed)
                self.assertFalse((Path(tmp) / "search_conditions.json").exists())
                self.assertIn('"Mental health"', prompts[0])
                if changed:
                    self.assertEqual([block["label"] for block in result["config"]["concept_blocks"]], ["Large language models (LLMs)", "Mental health", "Chatbots"])
                    self.assertEqual(result["config"]["source_limits"], {"pubmed": 25, "arxiv": 25})
                    self.assertEqual(result["config"]["date_range"]["end"], "2026-10-07")

    def test_malformed_llm_setup_gets_one_corrective_retry(self):
        valid = json.dumps({
            "reply": "Setup ready.",
            "title": "Test review",
            "research_description": "Review LLM systems in biomedicine",
            "concept_blocks": [
                {"label": "Large language models (LLMs)", "role": "phenomenon", "eligibility_group": "technology", "required_for_eligibility": True, "query_terms": ["large language model", "LLM"]},
            ],
        })
        for replies, recovers in ((["not json", valid], True), (["not json", "still not json"], False)):
            with self.subTest(recovers=recovers), tempfile.TemporaryDirectory() as tmp:
                output_root = Path(tmp)
                prompts = []

                def fake_llm_query(*, text_prompt, **_kwargs):
                    prompts.append(text_prompt)
                    return replies[len(prompts) - 1], {}

                agent = SearchConditionAgent(output_dir=str(output_root), llm_query=fake_llm_query)
                config = {"project_name": "Retry Review", "project_path": str(output_root / "retry-review"), "description": "Review LLM systems in biomedicine", "search_terms": "Review LLM systems in biomedicine", "platforms": ["pubmed"], "derive_search_terms": True, "model": "gpt-5.4-mini"}
                if recovers:
                    self.assertEqual(agent.run(config)["lead_agent_reply"], "Setup ready.")
                else:
                    with self.assertRaisesRegex(ValueError, "valid JSON"):
                        agent.run(config)
                self.assertEqual(len(prompts), 2)
                self.assertIn("previous response was rejected", prompts[1])

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
