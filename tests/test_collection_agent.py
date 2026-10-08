import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.collection_agent import CollectionAgent
from reviewpilot_core.project_store import read_json, read_jsonl
from searchers.sources import SourceResult, SourceSearchError


def fake_source(records_by_source=None, failures=(), transient=(), fail_times=None):
    """failures: always fail (not transient); transient: fail transiently fail_times[source] times (default: always)."""
    calls = []

    def search(source, query, *, max_results, date_range=None, output_file=None):
        calls.append({"source": source, "query": query, "max_results": max_results, "date_range": date_range})
        if source in failures:
            raise SourceSearchError(f"{source} unavailable", f"<{source}> {query}")
        if source in transient and sum(call["source"] == source for call in calls) <= (fail_times or {}).get(source, 99):
            raise SourceSearchError(f"{source} HTTP 429", f"<{source}> {query}", transient=True)
        records = [dict(row) for row in (records_by_source or {}).get(source, [])]
        return SourceResult(records, f"<{source}> {query}")
    return search, calls


class CollectionAgentTests(unittest.TestCase):
    def run_agent(self, input_data, project=None, **source_kwargs):
        search, calls = fake_source(**source_kwargs)
        with tempfile.TemporaryDirectory() as tmp, patch("agents.collection_agent.search_source", side_effect=search), \
             patch("agents.collection_agent.time.sleep") as sleep:
            project = project or Path(tmp) / "demo"
            result = CollectionAgent(project).run(input_data)
            summary = read_json(project / "collected" / "summary.json")
            rows = {source: read_jsonl(project / "collected" / f"{source}.jsonl") for source in summary["platforms"]}
        self.sleep = sleep
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

    def test_a_failed_source_records_the_query_it_attempted(self):
        _result, summary, _rows, _calls = self.run_agent(
            {"search_terms": "(AI)", "platforms": ["arxiv"]}, failures=("arxiv",))
        self.assertEqual(summary["executed_queries"]["arxiv"]["query"], "<arxiv> (AI)")

    def test_a_rejected_query_is_not_retried(self):
        _result, summary, _rows, calls = self.run_agent(
            {"search_terms": "(AI)", "platforms": ["pubmed", "arxiv"]}, failures=("arxiv",))
        self.assertEqual([call["source"] for call in calls], ["pubmed", "arxiv"])
        self.assertEqual(summary["executed_queries"]["arxiv"]["attempts"], 1)

    def test_a_transient_failure_is_retried_once_after_the_other_sources(self):
        _result, summary, rows, calls = self.run_agent(
            {"search_terms": "(AI)", "platforms": ["arxiv", "pubmed", "openalex"]},
            records_by_source={"arxiv": [{"id": "x"}], "pubmed": [{"id": "p"}]}, transient=("arxiv",), fail_times={"arxiv": 1})
        self.assertEqual([call["source"] for call in calls], ["arxiv", "pubmed", "openalex", "arxiv"])
        self.assertEqual(summary["platform_errors"], {})
        self.assertEqual(summary["platform_stats"]["arxiv"], 1)
        self.assertEqual(summary["executed_queries"]["arxiv"]["attempts"], 2)
        self.assertEqual(len(rows["arxiv"]), 1)

    def test_a_source_failing_its_deferred_retry_is_reported_not_retried_again(self):
        _result, summary, _rows, calls = self.run_agent(
            {"search_terms": "(AI)", "platforms": ["arxiv"]}, transient=("arxiv",))
        self.assertEqual(len(calls), 2)
        self.assertEqual(summary["platform_errors"], {"arxiv": "arxiv HTTP 429"})
        self.assertEqual(summary["executed_queries"]["arxiv"]["query"], "<arxiv> (AI)")
        self.sleep.assert_called_once()
        self.assertLessEqual(self.sleep.call_args.args[0], 15)

    def test_retrying_failed_sources_keeps_the_other_sources_records(self):
        setup = {"search_terms": "(AI)", "platforms": ["pubmed", "arxiv"], "source_limits": {"pubmed": 5, "arxiv": 5},
                 "date_range": {"start": "", "end": "2026-10-01"}}
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            _r, first, _rows, _calls = self.run_agent(setup, project=project, records_by_source={"pubmed": [{"id": "p"}]}, failures=("arxiv",))
            self.assertEqual(first["platform_errors"], {"arxiv": "arxiv unavailable"})
            result, summary, rows, calls = self.run_agent({**setup, "retry_sources": ["arxiv"]}, project=project,
                                                          records_by_source={"pubmed": [{"id": "new"}], "arxiv": [{"id": "x"}]})
        self.assertEqual([call["source"] for call in calls], ["arxiv"])
        self.assertEqual(summary["platform_errors"], {})
        self.assertEqual(summary["platform_stats"], {"pubmed": 1, "arxiv": 1})
        self.assertEqual([row["id"] for row in rows["pubmed"]], ["p"])
        self.assertEqual(summary["executed_queries"]["pubmed"], first["executed_queries"]["pubmed"])
        self.assertEqual(result["total_papers"], 2)

    def test_retry_is_refused_when_the_setup_changed_or_the_source_did_not_fail(self):
        setup = {"search_terms": "(AI)", "platforms": ["pubmed", "arxiv"], "date_range": {"start": "", "end": "2026-10-01"}}
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            self.run_agent(setup, project=project, failures=("arxiv",))
            for changed in ({**setup, "search_terms": "(ML)"}, {**setup, "source_limits": {"pubmed": 10, "arxiv": 20}},
                            {**setup, "date_range": {"start": "2020-01-01", "end": "2026-10-01"}}):
                with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "Only failed sources"):
                    self.run_agent({**changed, "retry_sources": ["arxiv"]}, project=project)
            with self.assertRaisesRegex(ValueError, "Only failed sources"):
                self.run_agent({**setup, "retry_sources": ["pubmed"]}, project=project)

    def test_requires_a_saved_query(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "saved search query"):
            CollectionAgent(Path(tmp) / "demo").run({"search_terms": "  "})


if __name__ == "__main__":
    unittest.main()
