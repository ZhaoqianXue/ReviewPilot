import unittest
from unittest.mock import Mock, patch

import requests

from searchers.http_retry import MAX_RETRY_AFTER, TransientSourceError, get_with_retry
from searchers.openalex import OpenAlexSearchError, OpenAlexSearcher
from searchers.pubmed import PubMedSearcher
from searchers.sources import SourceSearchError, search_source


def response(status, headers=None, payload=None, text=""):
    item = Mock(status_code=status, headers=headers or {}, text=text)
    item.json.return_value = payload if payload is not None else {}
    if status >= 400:
        item.raise_for_status.side_effect = requests.HTTPError(f"{status} error", response=item)
    else:
        item.raise_for_status.return_value = None
    return item


class HttpRetryPolicyTests(unittest.TestCase):
    def call(self, side_effect, delays=(5, 20)):
        with patch("searchers.http_retry.requests.get", side_effect=side_effect) as get, \
             patch("searchers.http_retry.time.sleep") as sleep:
            try:
                result = get_with_retry("https://example.org", params={}, label="Src", delays=delays, timeout=1)
            except TransientSourceError as exc:
                result = exc
        return result, get, sleep

    def test_non_transient_status_is_returned_without_retrying(self):
        result, get, sleep = self.call([response(400)])
        self.assertEqual(result.status_code, 400)
        self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()

    def test_transient_failures_back_off_then_succeed(self):
        result, get, sleep = self.call([response(503), requests.Timeout(), response(200)])
        self.assertEqual(result.status_code, 200)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 20])

    def test_retry_after_lengthens_a_wait_but_is_capped(self):
        _result, _get, sleep = self.call([response(429, {"Retry-After": "12"}), response(429, {"Retry-After": "9999"}), response(200)])
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [12, MAX_RETRY_AFTER])

    def test_exhausted_attempts_raise_a_transient_error_with_the_api_message(self):
        result, get, _sleep = self.call([response(503, payload={"message": "Heavy load"})] * 3)
        self.assertIsInstance(result, TransientSourceError)
        self.assertIn("HTTP 503 after 3 attempts (Heavy load)", str(result))
        self.assertEqual(get.call_count, 3)


class AdapterFailureTests(unittest.TestCase):
    def test_pubmed_fetch_failure_fails_the_source_instead_of_returning_fewer_records(self):
        search = response(200, payload={"esearchresult": {"idlist": ["1", "2"]}})
        with patch("searchers.http_retry.requests.get", side_effect=[search] + [response(502)] * 3), \
             patch("searchers.http_retry.time.sleep"), patch("searchers.pubmed.time.sleep"):
            with self.assertRaisesRegex(TransientSourceError, "PubMed returned HTTP 502"):
                PubMedSearcher(email="fixture@example.org").search("AI", max_results=2)

    def test_pubmed_reports_a_rejected_query(self):
        rejected = response(200, payload={"esearchresult": {"ERROR": "Invalid query"}})
        with patch("searchers.http_retry.requests.get", return_value=rejected):
            with self.assertRaisesRegex(RuntimeError, "PubMed rejected the query: Invalid query"):
                PubMedSearcher(email="fixture@example.org").search("AI", max_results=2)

    def test_openalex_error_mid_pagination_is_not_reported_as_a_complete_result(self):
        page = response(200, payload={"results": [{"id": "https://openalex.org/W1", "title": "A"}], "meta": {"next_cursor": "next"}})
        with patch("searchers.http_retry.requests.get", side_effect=[page, response(403, payload={"message": "Forbidden"})]), \
             patch("searchers.openalex.time.sleep"):
            with self.assertRaisesRegex(OpenAlexSearchError, "Forbidden"):
                OpenAlexSearcher(email="fixture@example.org").search("AI", max_results=5)

    def test_failed_search_carries_the_attempted_query_and_transience(self):
        with patch("searchers.http_retry.requests.get", return_value=response(429)), \
             patch("searchers.http_retry.time.sleep"):
            with self.assertRaises(SourceSearchError) as ctx:
                search_source("arxiv", "(AI) AND (surgery)", max_results=5)
        self.assertTrue(ctx.exception.transient)
        self.assertEqual(ctx.exception.executed_query, "((ti:AI OR abs:AI) AND (ti:surgery OR abs:surgery))")

    def test_rejected_query_is_not_transient(self):
        with patch("searchers.http_retry.requests.get", return_value=response(400, text="<feed/>")):
            with self.assertRaises(SourceSearchError) as ctx:
                search_source("arxiv", "(AI)", max_results=5)
        self.assertFalse(ctx.exception.transient)
        self.assertIn("HTTP 400", str(ctx.exception))
        self.assertTrue(ctx.exception.executed_query)


if __name__ == "__main__":
    unittest.main()
