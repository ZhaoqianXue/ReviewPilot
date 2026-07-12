import hashlib
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "docs" / "internal-testing" / "scenarios.json"
PROTOCOL_PATH = ROOT / "docs" / "internal-testing" / "README.md"
PROVENANCE_PATH = ROOT / "docs" / "internal-testing" / "provenance"

CATALOG_FIELDS = {
    "project_name",
    "description",
    "primary_topic",
    "domain",
    "search_terms",
    "platforms",
    "source_limits",
    "max_results",
    "date_start",
    "date_end",
}
SENSITIVE_KEY = re.compile(
    r"(?:api.?key|secret|token|password|credential|authorization|cookie|email|phone|user.?path|home.?dir)",
    re.IGNORECASE,
)
EXPECTED_SOURCES = {
    "biomedicine": (
        "output/llm-biomedicine-survey/search_conditions.json",
        "83c2277ba55cb6eecbbac3830421da93bc5305066037042e7b4b23caebe2da36",
    ),
    "hci": (
        "output/llm-for-human-computer-interaction-survey/search_conditions.json",
        "36dd295f83c713fd5ddd533ad0bf8372708690223c2e4d7135315f7dbea235b7",
    ),
    "spatial_authoring": (
        "output/ai-assisted-3d-spatial-authoring-vr-ar-mr/search_conditions.json",
        "9858dbcb5940bc54bb969fb240ede7ece6b3779f1a854150956636575fe8ff01",
    ),
}

EXPECTED_SEARCH_TERMS = {
    "biomedicine": '(biomedicine OR biomedical OR "life sciences" OR healthcare OR "health care" OR biomed* OR "electronic health record" OR EHR) AND (LLM OR "large language model" OR "large language modelling" OR "foundation model" OR GPT OR ChatGPT OR Claude OR Gemini OR Qwen OR DeepSeek)',
    "hci": '("human-computer interaction" OR HCI OR "human computer interaction" OR "interactive system" OR "user interface" OR "user experience" OR UX) AND ("large language model" OR LLM OR GPT OR ChatGPT OR Claude OR Gemini OR Qwen OR DeepSeek)',
    "spatial_authoring": '("AI-assisted" OR "AI assisted" OR "interactive AI" OR "intelligent assistant" OR "conversational AI" OR "generative AI" OR "AI co-creation" OR "large language model" OR LLM OR "foundation model") AND ("3D design" OR "3D modeling" OR "spatial authoring" OR "spatial creation" OR "3D scene" OR "virtual environment" OR VR OR "virtual reality" OR AR OR "augmented reality" OR MR OR "mixed reality" OR "extended reality" OR XR)',
}


class InnerBetaScenarioTests(unittest.TestCase):
    def assert_no_sensitive_keys(self, value):
        if isinstance(value, dict):
            for key, nested in value.items():
                self.assertIsNone(SENSITIVE_KEY.search(key), f"sensitive key is forbidden: {key}")
                self.assert_no_sensitive_keys(nested)
        elif isinstance(value, list):
            for nested in value:
                self.assert_no_sensitive_keys(nested)
        elif isinstance(value, str):
            self.assertFalse(value.startswith(("/Users/", "/home/")), f"personal absolute path is forbidden: {value}")

    def test_catalog_freezes_three_isolated_real_use_inputs(self):
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

        self.assertEqual(set(catalog), {"biomedicine", "hci", "spatial_authoring"})
        self.assertEqual(len({item["project_name"] for item in catalog.values()}), 3)
        for key, item in catalog.items():
            self.assertEqual(set(item), CATALOG_FIELDS)
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
        self.assert_no_sensitive_keys(catalog)

    def test_tracked_provenance_verifies_exact_catalog_payloads(self):
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        manifest = json.loads((PROVENANCE_PATH / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(set(manifest), {"version", "scenarios"})
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(set(manifest["scenarios"]), set(EXPECTED_SOURCES))
        for scenario, (source_path, source_sha256) in EXPECTED_SOURCES.items():
            entry = manifest["scenarios"][scenario]
            self.assertEqual(set(entry), {"snapshot", "snapshot_sha256", "source_path", "source_sha256"})
            self.assertEqual((entry["source_path"], entry["source_sha256"]), (source_path, source_sha256))
            snapshot_path = PROVENANCE_PATH / entry["snapshot"]
            snapshot_bytes = snapshot_path.read_bytes()
            self.assertEqual(hashlib.sha256(snapshot_bytes).hexdigest(), entry["snapshot_sha256"])
            snapshot = json.loads(snapshot_bytes)
            self.assertEqual(set(snapshot), {"scenario", "original_source", "authoritative_fields", "inner_beta_payload"})
            self.assertEqual(snapshot["scenario"], scenario)
            self.assertEqual(snapshot["original_source"], {"path": source_path, "sha256": source_sha256})
            self.assertEqual(
                set(snapshot["authoritative_fields"]),
                {"project_name", "description", "primary_topic", "domain", "search_terms", "platforms"},
            )
            self.assertEqual(snapshot["authoritative_fields"]["search_terms"], EXPECTED_SEARCH_TERMS[scenario])
            self.assertEqual(set(snapshot["inner_beta_payload"]), CATALOG_FIELDS)
            for shared_field in ("description", "primary_topic", "domain", "search_terms", "platforms"):
                self.assertEqual(snapshot["inner_beta_payload"][shared_field], snapshot["authoritative_fields"][shared_field])
            self.assertEqual(snapshot["inner_beta_payload"], catalog[scenario])
            self.assert_no_sensitive_keys(snapshot)
        self.assert_no_sensitive_keys(manifest)

    def test_sensitive_key_guard_rejects_recursive_violations(self):
        for violation in (
            {"api_token": "value"},
            {"nested": [{"password": "value"}]},
            {"path": "/Users/example/private/output"},
        ):
            with self.subTest(violation=violation), self.assertRaises(AssertionError):
                self.assert_no_sensitive_keys(violation)

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
            "catalog-to-persisted projection",
            "api-returned slug `id`",
            "max_results_per_platform",
            "system-generated or compatibility fields",
            "effective run cutoff",
            "max(all matching n, 0) + 1",
            "reserve the identity",
            "if it differs from the requested project id",
            "<yyyy-mm-dd>-<scenario-key>-r<n>.md",
            "evidence/<scenario-key>-r<n>/",
            "tested git commit sha",
            "occurrence timestamp",
            "attempt number",
            "retryability",
            "final resolution",
            "pii/personal identifiers",
            "local user paths",
            "copyrighted/full-text content",
            "credentials",
            "secret values",
        ):
            self.assertIn(phrase, protocol)


if __name__ == "__main__":
    unittest.main()
