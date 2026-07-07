import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

from agents.collection_agent import CollectionAgent
from reviewpilot_core.project_store import read_json, read_jsonl


class CollectionAgentTests(unittest.TestCase):
    def test_run_writes_collected_outputs_with_platform_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            calls = []

            class FakeSearcher:
                def search(self, **kwargs):
                    calls.append(kwargs)
                    return {"openalex": [{"title": "Paper A", "source": "openalex"}]}

            previous_main = sys.modules.get("main")
            sys.modules["main"] = types.SimpleNamespace(AcademicSearcher=lambda: FakeSearcher())
            try:
                result = CollectionAgent(project_dir).run(
                    {
                        "search_terms": "AI",
                        "platforms": ["openalex"],
                        "max_results_per_platform": 1,
                    }
                )
            finally:
                if previous_main is None:
                    sys.modules.pop("main", None)
                else:
                    sys.modules["main"] = previous_main

            summary = read_json(project_dir / "collected" / "summary.json")
            rows = read_jsonl(project_dir / "collected" / "openalex.jsonl")

        self.assertEqual(calls[0]["platforms"], ["openalex"])
        self.assertEqual(result["total_papers"], 1)
        self.assertEqual(summary["platform_stats"], {"openalex": 1})
        self.assertEqual(rows[0]["title"], "Paper A")

    def test_run_uses_per_source_limits_when_source_limits_are_supplied(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            calls = []

            class FakeSearcher:
                def search(self, **kwargs):
                    calls.append(kwargs)
                    platform = kwargs["platforms"][0]
                    return {platform: [{"title": platform, "source": platform}]}

            previous_main = sys.modules.get("main")
            sys.modules["main"] = types.SimpleNamespace(AcademicSearcher=lambda: FakeSearcher())
            try:
                result = CollectionAgent(project_dir).run(
                    {
                        "search_terms": "AI",
                        "platforms": ["pubmed", "openalex", "arxiv"],
                        "max_results_per_platform": 50,
                        "source_limits": {"pubmed": 10, "openalex": 25, "arxiv": 75},
                    }
                )
            finally:
                if previous_main is None:
                    sys.modules.pop("main", None)
                else:
                    sys.modules["main"] = previous_main

            summary = read_json(project_dir / "collected" / "summary.json")

        self.assertEqual([(call["platforms"], call["max_results"]) for call in calls], [(["pubmed"], 10), (["openalex"], 25), (["arxiv"], 75)])
        self.assertEqual(result["platform_stats"], {"pubmed": 1, "openalex": 1, "arxiv": 1})
        self.assertEqual(summary["total_papers"], 3)

    def test_run_records_platform_errors_in_collection_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"

            class FakeSearcher:
                last_errors = {"openalex": "503 Search temporarily unavailable"}

                def search(self, **_kwargs):
                    return {"openalex": []}

            previous_main = sys.modules.get("main")
            sys.modules["main"] = types.SimpleNamespace(AcademicSearcher=lambda: FakeSearcher())
            try:
                result = CollectionAgent(project_dir).run(
                    {
                        "search_terms": "LLM AND medicine",
                        "platforms": ["openalex"],
                        "max_results_per_platform": 10,
                    }
                )
            finally:
                if previous_main is None:
                    sys.modules.pop("main", None)
                else:
                    sys.modules["main"] = previous_main

            summary = read_json(project_dir / "collected" / "summary.json")

        self.assertEqual(result["platform_stats"], {"openalex": 0})
        self.assertEqual(result["platform_errors"], {"openalex": "503 Search temporarily unavailable"})
        self.assertEqual(summary["platform_errors"], {"openalex": "503 Search temporarily unavailable"})


if __name__ == "__main__":
    unittest.main()
