import unittest
import inspect

import config
from agents.extraction_agent import ExtractionAgent
from agents.filtering_agent import FilteringAgent
from reviewpilot_core.model_policy import (
    CATEGORIZATION_MODEL,
    COLLECTION_MODEL,
    DOWNLOAD_MODEL,
    ESCALATION_MODEL,
    EXTRACTION_MODEL,
    FILTERING_MODEL,
    HARD_REASONING_ESCALATION_MODEL,
    LEAD_AGENT_DEV_MODEL,
    LEAD_AGENT_PRODUCTION_MODEL,
    PROMPT_MODEL,
    SEARCH_CONDITION_MODEL,
    SUBSCRIBED_PAPER_FALLBACK_MODEL,
    project_model,
)
from utils.llm import MODEL_COSTS


class ModelPolicyTests(unittest.TestCase):
    def test_default_llm_model_matches_development_agent_policy(self):
        self.assertEqual(LEAD_AGENT_DEV_MODEL, "gpt-6-luna")
        self.assertEqual(SEARCH_CONDITION_MODEL, "gpt-6-luna")
        self.assertEqual(config.MODEL, "gpt-6-luna")

    def test_all_lead_and_sub_agent_models_are_centrally_defined(self):
        self.assertEqual(LEAD_AGENT_PRODUCTION_MODEL, "gpt-6-luna")
        self.assertEqual(PROMPT_MODEL, "gpt-6-luna")
        self.assertEqual(COLLECTION_MODEL, "gpt-6-luna")
        self.assertEqual(FILTERING_MODEL, "gpt-6-luna")
        self.assertEqual(DOWNLOAD_MODEL, "gpt-6-luna")
        self.assertEqual(EXTRACTION_MODEL, "gpt-6-luna")
        self.assertEqual(CATEGORIZATION_MODEL, "gpt-6-luna")
        self.assertEqual(SUBSCRIBED_PAPER_FALLBACK_MODEL, "gpt-6-luna")
        self.assertEqual(ESCALATION_MODEL, "gpt-6-luna")
        self.assertEqual(HARD_REASONING_ESCALATION_MODEL, "gpt-6-luna")

    def test_legacy_llm_sub_agents_default_to_model_policy(self):
        self.assertEqual(FilteringAgent(project_path=".").model, FILTERING_MODEL)
        self.assertEqual(ExtractionAgent(project_path=".").model, EXTRACTION_MODEL)

    def test_project_model_prefers_saved_setup_model_and_falls_back_to_role_default(self):
        self.assertEqual(project_model({"model": " gpt-5.4 "}, FILTERING_MODEL), "gpt-5.4")
        for config in ({}, {"model": ""}, {"model": "   "}, {"model": None}, {"model": 5}, None, []):
            with self.subTest(config=config):
                self.assertEqual(project_model(config, EXTRACTION_MODEL), EXTRACTION_MODEL)

    def test_model_cost_table_includes_target_models(self):
        self.assertIn(LEAD_AGENT_DEV_MODEL, MODEL_COSTS)
        self.assertIn(LEAD_AGENT_PRODUCTION_MODEL, MODEL_COSTS)


if __name__ == "__main__":
    unittest.main()
