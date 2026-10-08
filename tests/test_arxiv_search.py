import unittest
import requests
from unittest.mock import Mock, patch

from searchers.arxiv_search import ArxivSearcher


class ArxivSearcherTests(unittest.TestCase):
    def test_exhausted_rate_limit_is_not_an_empty_success(self):
        response = Mock(status_code=429)
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        with patch("searchers.arxiv_search.requests.get", return_value=response) as get, patch("searchers.arxiv_search.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                ArxivSearcher().search('LLM AND urban', max_results=5)
        self.assertEqual(get.call_count, 3)

    def test_transient_server_error_is_retried(self):
        failed = Mock(status_code=500)
        ok = Mock(status_code=200, text="feed")
        searcher = ArxivSearcher()
        with patch("searchers.arxiv_search.requests.get", side_effect=[failed, ok]) as get, \
             patch.object(searcher, "_parse_response", side_effect=[[{"id": "a"}]]), \
             patch("searchers.arxiv_search.time.sleep"):
            rows = searcher._search_simple("urban", 1)
        self.assertEqual(rows, [{"id": "a"}])
        self.assertEqual(get.call_count, 2)

    def test_persistent_server_error_fails_after_bounded_retries(self):
        response = Mock(status_code=500)
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        with patch("searchers.arxiv_search.requests.get", return_value=response) as get, patch("searchers.arxiv_search.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
                ArxivSearcher().search('urban', max_results=5)
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

    def test_open_start_date_uses_arxivs_first_year_not_year_one(self):
        searcher = ArxivSearcher()
        with patch("searchers.arxiv_search.requests.get", return_value=Mock(status_code=200, text="feed")) as get, \
             patch.object(searcher, "_parse_response", return_value=[]):
            searcher.search("urban", max_results=1, date_range={"start": "", "end": "2026-10-08"})
        self.assertIn("submittedDate:[199101010000 TO 202610082359]", get.call_args.kwargs["params"]["search_query"])

    def test_boolean_query_is_one_native_title_abstract_request_and_is_recorded(self):
        searcher = ArxivSearcher()
        response = Mock(status_code=200, text="feed")
        with patch("searchers.arxiv_search.requests.get", return_value=response) as get, \
             patch.object(searcher, "_parse_response", side_effect=[[{"id": "a"}], []]), \
             patch("searchers.arxiv_search.time.sleep"):
            searcher.search('("machine learning" OR AI) AND ("virtual reality")', max_results=100)
        sent = get.call_args_list[0].kwargs["params"]["search_query"]
        self.assertEqual(sent, '(((ti:"machine learning" OR abs:"machine learning") OR (ti:AI OR abs:AI)) AND (ti:"virtual reality" OR abs:"virtual reality"))')
        self.assertEqual(searcher.last_query, sent)
        self.assertEqual(get.call_count, 2)

    def test_api_error_feed_fails_the_source_with_arxivs_explanation(self):
        feed = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/api/errors#bad</id>'
                '<title>Error</title><summary>start must be non-negative</summary></entry></feed>')
        for status in (200, 400):
            with self.subTest(status=status), patch("searchers.arxiv_search.requests.get", return_value=Mock(status_code=status, text=feed)):
                with self.assertRaisesRegex(RuntimeError, "start must be non-negative"):
                    ArxivSearcher().search("urban", max_results=5)

    def test_no_courtesy_wait_after_the_last_page(self):
        searcher = ArxivSearcher()
        with patch("searchers.arxiv_search.requests.get", return_value=Mock(status_code=200, text="feed")), \
             patch.object(searcher, "_parse_response", return_value=[{"id": "a"}]), \
             patch("searchers.arxiv_search.time.sleep") as sleep:
            searcher.search("urban", max_results=1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
