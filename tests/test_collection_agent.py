import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.collection_agent import CollectionAgent
from reviewpilot_core.project_store import read_json, read_jsonl
from searchers.sources import SourceResult


def fake_source(records_by_source=None, failures=()):
    calls = []

    def search(source, query, *, max_results, date_range=None, output_file=None):
        calls.append({"source": source, "query": query, "max_results": max_results, "date_range": date_range})
        if source in failures:
            raise RuntimeError(f"{source} unavailable")
        records = [dict(row) for row in (records_by_source or {}).get(source, [])]
        return SourceResult(records, f"<{source}> {query}")
    return search, calls


class CollectionAgentTests(unittest.TestCase):
    def run_agent(self, input_data, **source_kwargs):
        search, calls = fake_source(**source_kwargs)
        with tempfile.TemporaryDirectory() as tmp, patch("agents.collection_agent.search_source", side_effect=search):
            project = Path(tmp) / "demo"
            result = CollectionAgent(project).run(input_data)
            summary = read_json(project / "collected" / "summary.json")
            rows = {source: read_jsonl(project / "collected" / f"{source}.jsonl") for source in summary["platforms"]}
        return result, summary, rows, calls

    def test_defaults_to_the_three_sources_in_order(self):
        _result, summary, _rows, calls = self.run_agent({"search_terms": "(AI)"})
        self.assertEqual([call["source"] for call in calls], ["pubmed", "arxiv", "openalex"])
        self.assertEqual(summary["platforms"], ["pubmed", "arxiv", "openalex"])

    def test_writes_records_and_the_exact_query_each_source_executed(self):
        result, summary, rows, calls = self.run_agent(
            {"search_terms": "(AI) AND (surgery)", "platforms": ["openalex", "pubmed"],
             "source_limits": {"openalex": 1, "pubmed": 5}, "date_range": {"start": "2024-01-01", "end": "2025-01-01"}},
            records_by_source={"openalex": [{"id": "a"}, {"id": "b"}], "pubmed": [{"id": "p"}]})
        self.assertEqual([call["max_results"] for call in calls], [1, 5])
        self.assertEqual(calls[0]["date_range"], {"start": "2024-01-01", "end": "2025-01-01"})
        self.assertEqual(summary["platform_stats"], {"openalex": 1, "pubmed": 1})
        self.assertEqual(len(rows["openalex"]), 1)
        self.assertEqual(summary["executed_queries"]["openalex"]["query"], "<openalex> (AI) AND (surgery)")
        self.assertEqual(summary["executed_queries"]["pubmed"]["records"], 1)
        self.assertEqual(summary["coverage"]["fields"], "title and abstract")
        self.assertEqual(result["total_papers"], 2)

    def test_a_failed_source_is_recorded_with_zero_rows_and_others_still_run(self):
        _result, summary, rows, calls = self.run_agent(
            {"search_terms": "(AI)", "platforms": ["pubmed", "arxiv"]},
            records_by_source={"pubmed": [{"id": "p"}]}, failures=("arxiv",))
        self.assertEqual(summary["platform_errors"], {"arxiv": "arxiv unavailable"})
        self.assertEqual(summary["platform_stats"], {"pubmed": 1, "arxiv": 0})
        self.assertEqual(rows["arxiv"], [])
        self.assertEqual(summary["executed_queries"]["arxiv"]["error"], "arxiv unavailable")

    def test_requires_a_saved_query(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "saved search query"):
            CollectionAgent(Path(tmp) / "demo").run({"search_terms": "  "})


if __name__ == "__main__":
    unittest.main()
