import unittest
from unittest.mock import Mock, patch

import requests

from searchers.openalex import OpenAlexSearchError, OpenAlexSearcher


class OpenAlexSearchTests(unittest.TestCase):
    def test_search_uses_api_key_parameter_when_available(self):
        response = Mock()
        response.json.return_value = {
            "results": [
                {
                    "id": "https://openalex.org/W1",
                    "title": "Paper A",
                    "publication_year": 2026,
                    "authorships": [],
                    "primary_location": {},
                    "open_access": {},
                }
            ],
            "meta": {"next_cursor": None},
        }
        response.raise_for_status.return_value = None

        with patch("searchers.openalex.requests.get", return_value=response) as get:
            rows = OpenAlexSearcher(email="researcher@example.com", api_key="oa_test_key").search("LLM", max_results=1)

        self.assertEqual(rows[0]["id"], "W1")
        self.assertEqual(get.call_args.kwargs["params"]["api_key"], "oa_test_key")

    def test_search_raises_clear_error_when_openalex_search_is_rate_limited(self):
        response = Mock()
        response.json.return_value = {
            "error": "Search temporarily unavailable",
            "message": "Anonymous search is temporarily rate-limited due to heavy load. Please use a free API key.",
        }
        response.raise_for_status.side_effect = requests.HTTPError("503 Server Error", response=response)

        with patch("searchers.openalex.requests.get", return_value=response):
            with self.assertRaises(OpenAlexSearchError) as ctx:
                OpenAlexSearcher(email="researcher@example.com").search("LLM", max_results=1)

        self.assertIn("Anonymous search is temporarily rate-limited", str(ctx.exception))

    def test_boolean_query_preserves_native_logic_in_one_request(self):
        query = '("large language model" OR LLM OR "generative AI") AND (biomedical OR clinical)'
        param_sets = OpenAlexSearcher(email="researcher@example.com")._build_query_param_sets(query)

        self.assertEqual(len(param_sets), 1)
        self.assertEqual(
            param_sets[0],
            {"search": '((("large language model" OR LLM) OR "generative AI") AND (biomedical OR clinical))'},
        )

    def test_rate_limited_boolean_query_does_not_fan_out_more_requests(self):
        response = Mock()
        response.json.return_value = {
            "error": "Search temporarily unavailable",
            "message": "Anonymous search is temporarily rate-limited due to heavy load. Please use a free API key.",
        }
        response.raise_for_status.side_effect = requests.HTTPError("503 Server Error", response=response)
        query = '("large language model" OR LLM OR "generative AI") AND (biomedical OR clinical)'

        with patch("searchers.openalex.requests.get", return_value=response) as get:
            with self.assertRaises(OpenAlexSearchError):
                OpenAlexSearcher(email="researcher@example.com").search(query, max_results=5)

        self.assertEqual(get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
