import unittest
import requests
from unittest.mock import Mock, patch

from searchers.arxiv_search import ArxivSearcher


class RecordingArxivSearcher(ArxivSearcher):
    def __init__(self):
        super().__init__()
        self.simple_calls = []
        self.split_calls = []

    def _search_simple(self, query, max_results, categories=None, is_boolean_query=False, output_file=None):
        self.simple_calls.append(
            {
                "query": query,
                "max_results": max_results,
                "categories": categories,
                "is_boolean_query": is_boolean_query,
                "output_file": output_file,
            }
        )
        return [{"id": f"arxiv-{i}"} for i in range(max_results)]

    def _search_split(self, groups, max_results, categories=None, output_file=None):
        self.split_calls.append(
            {
                "groups": groups,
                "max_results": max_results,
                "categories": categories,
                "output_file": output_file,
            }
        )
        return []


class ArxivSearcherTests(unittest.TestCase):
    def test_exhausted_rate_limit_is_not_an_empty_success(self):
        response = Mock(status_code=429)
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        with patch("searchers.arxiv_search.requests.get", return_value=response) as get, patch("searchers.arxiv_search.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                ArxivSearcher().search('LLM AND urban', max_results=5)
        self.assertEqual(get.call_count, 3)

    def test_network_timeout_retries_are_bounded(self):
        with patch("searchers.arxiv_search.requests.get", side_effect=requests.Timeout) as get, patch("searchers.arxiv_search.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "3 attempts"):
                ArxivSearcher().search('urban', max_results=5)
        self.assertEqual(get.call_count, 3)

    def test_date_filter_keeps_numeric_pagination_offset(self):
        response = Mock(status_code=200, text="feed")
        searcher = ArxivSearcher()
        searcher._date_range = {"start": "2023-01-01", "end": "2026-09-12"}
        with patch("searchers.arxiv_search.requests.get", return_value=response) as get, \
             patch.object(searcher, "_parse_response", side_effect=[[{"id": "first"}], [{"id": "second"}]]), \
             patch("searchers.arxiv_search.time.sleep"):
            rows = searcher._search_simple("urban planning", 2)
        self.assertEqual([row["id"] for row in rows], ["first", "second"])
        self.assertEqual([call.kwargs["params"]["start"] for call in get.call_args_list], [0, 1])
        self.assertIn("submittedDate:[202301010000 TO 202609122359]", get.call_args.kwargs["params"]["search_query"])

    def test_complex_boolean_query_tries_native_query_before_split(self):
        searcher = RecordingArxivSearcher()

        results = searcher.search(
            '("AI-assisted" OR "machine learning") AND ("3D design" OR "virtual reality")',
            max_results=100,
        )

        self.assertEqual(len(results), 100)
        self.assertEqual(len(searcher.simple_calls), 1)
        self.assertEqual(searcher.split_calls, [])

    def test_rate_limited_native_query_does_not_continue_to_split_fallback(self):
        class RateLimitedNativeSearcher(RecordingArxivSearcher):
            def _search_simple(self, query, max_results, categories=None, is_boolean_query=False, output_file=None):
                self.simple_calls.append({"query": query})
                self._last_status_code = 429
                return []

        searcher = RateLimitedNativeSearcher()

        results = searcher.search(
            '("AI-assisted" OR "machine learning") AND ("3D design" OR "virtual reality")',
            max_results=100,
        )

        self.assertEqual(results, [])
        self.assertEqual(len(searcher.simple_calls), 1)
        self.assertEqual(searcher.split_calls, [])

    def test_split_fallback_respects_query_cap(self):
        class EmptySplitSearcher(ArxivSearcher):
            def __init__(self):
                super().__init__()
                self.simple_queries = []

            def _search_simple(self, query, max_results, categories=None, is_boolean_query=False, output_file=None):
                self.simple_queries.append(query)
                return []

        searcher = EmptySplitSearcher()

        with patch.dict("os.environ", {"REVIEWPILOT_ARXIV_MAX_SPLIT_QUERIES": "2"}):
            results = searcher._search_split((["a", "b", "c"], ["x", "y", "z"]), max_results=10)

        self.assertEqual(results, [])
        self.assertEqual(len(searcher.simple_queries), 2)


if __name__ == "__main__":
    unittest.main()
