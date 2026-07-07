import unittest
import inspect

import config
from agents.extraction_agent import ExtractionAgent
from agents.filtering_agent import FilteringAgent
from agents.coordinator import PipelineCoordinator
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
)
from utils.llm import MODEL_COSTS


class ModelPolicyTests(unittest.TestCase):
    def test_default_llm_model_matches_development_agent_policy(self):
        self.assertEqual(LEAD_AGENT_DEV_MODEL, "gpt-5.4-mini")
        self.assertEqual(SEARCH_CONDITION_MODEL, "gpt-5.4-mini")
        self.assertEqual(config.MODEL, "gpt-5.4-mini")

    def test_all_lead_and_sub_agent_models_are_centrally_defined(self):
        self.assertEqual(LEAD_AGENT_PRODUCTION_MODEL, "gpt-5.4")
        self.assertEqual(PROMPT_MODEL, "gpt-5.4-mini")
        self.assertEqual(COLLECTION_MODEL, "gpt-5.4-mini")
        self.assertEqual(FILTERING_MODEL, "gpt-5.4-mini")
        self.assertEqual(DOWNLOAD_MODEL, "gpt-5.4-mini")
        self.assertEqual(EXTRACTION_MODEL, "gpt-5.4-mini")
        self.assertEqual(CATEGORIZATION_MODEL, "gpt-5.4-mini")
        self.assertEqual(SUBSCRIBED_PAPER_FALLBACK_MODEL, "gpt-5.4-mini")
        self.assertEqual(ESCALATION_MODEL, "gpt-5.4")
        self.assertEqual(HARD_REASONING_ESCALATION_MODEL, "gpt-5.5")

    def test_legacy_llm_sub_agents_default_to_model_policy(self):
        self.assertEqual(FilteringAgent(project_path=".").model, FILTERING_MODEL)
        self.assertEqual(ExtractionAgent(project_path=".").model, EXTRACTION_MODEL)
        default_model = inspect.signature(PipelineCoordinator.run_pipeline).parameters["model"].default
        self.assertEqual(default_model, FILTERING_MODEL)

    def test_model_cost_table_includes_target_models(self):
        self.assertIn(LEAD_AGENT_DEV_MODEL, MODEL_COSTS)
        self.assertIn(LEAD_AGENT_PRODUCTION_MODEL, MODEL_COSTS)


if __name__ == "__main__":
    unittest.main()
