import unittest
from unittest.mock import patch

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
