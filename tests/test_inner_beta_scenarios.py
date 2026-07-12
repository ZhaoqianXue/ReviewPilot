import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "docs" / "internal-testing" / "scenarios.json"
PROTOCOL_PATH = ROOT / "docs" / "internal-testing" / "README.md"

EXPECTED_SEARCH_TERMS = {
    "biomedicine": '(biomedicine OR biomedical OR "life sciences" OR healthcare OR "health care" OR biomed* OR "electronic health record" OR EHR) AND (LLM OR "large language model" OR "large language modelling" OR "foundation model" OR GPT OR ChatGPT OR Claude OR Gemini OR Qwen OR DeepSeek)',
    "hci": '("human-computer interaction" OR HCI OR "human computer interaction" OR "interactive system" OR "user interface" OR "user experience" OR UX) AND ("large language model" OR LLM OR GPT OR ChatGPT OR Claude OR Gemini OR Qwen OR DeepSeek)',
    "spatial_authoring": '("AI-assisted" OR "AI assisted" OR "interactive AI" OR "intelligent assistant" OR "conversational AI" OR "generative AI" OR "AI co-creation" OR "large language model" OR LLM OR "foundation model") AND ("3D design" OR "3D modeling" OR "spatial authoring" OR "spatial creation" OR "3D scene" OR "virtual environment" OR VR OR "virtual reality" OR AR OR "augmented reality" OR MR OR "mixed reality" OR "extended reality" OR XR)',
}


class InnerBetaScenarioTests(unittest.TestCase):
    def test_catalog_freezes_three_isolated_real_use_inputs(self):
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

        self.assertEqual(set(catalog), {"biomedicine", "hci", "spatial_authoring"})
        self.assertEqual(len({item["project_name"] for item in catalog.values()}), 3)
        for key, item in catalog.items():
            self.assertEqual(item["platforms"], ["pubmed", "arxiv", "openalex"])
            self.assertEqual(item["source_limits"], {"pubmed": 5, "arxiv": 5, "openalex": 5})
            self.assertEqual(item["max_results"], 5)
            self.assertEqual(item["search_terms"], EXPECTED_SEARCH_TERMS[key])
            self.assertIn(" AND ", item["search_terms"])
            self.assertEqual(item["date_end"], "")
            self.assertTrue(item["description"])
            self.assertTrue(item["primary_topic"])
            self.assertTrue(item["domain"])
        self.assertEqual(catalog["biomedicine"]["date_start"], "2023-01-01")
        self.assertEqual(catalog["hci"]["date_start"], "2020-01-01")
        self.assertEqual(catalog["spatial_authoring"]["date_start"], "2020-01-01")

    def test_protocol_requires_reproducible_evidence_without_credentials(self):
        protocol = PROTOCOL_PATH.read_text(encoding="utf-8").lower()

        for phrase in (
            "-r<n>",
            "timestamp",
            "viewport",
            "task id",
            "task status",
            "per-stage counts",
            "external error",
            "screenshot",
            "artifact paths",
            "defect priority",
            "regression command",
            "rerun project id",
            "pass/fail disposition",
            "scenario key",
            "catalog revision",
            "source revision",
            "actual `search_terms`",
            "platforms/sources",
            "date range",
            "per-source limits",
            "field-by-field",
            "saved `search_conditions.json` equals the catalog",
            "tested git commit sha",
            "credentials",
            "secret values",
        ):
            self.assertIn(phrase, protocol)


if __name__ == "__main__":
    unittest.main()
