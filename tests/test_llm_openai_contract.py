import unittest
from unittest.mock import patch

from utils.llm import query_openai


class OpenAIContractTests(unittest.TestCase):
    def test_query_openai_uses_json_mode_only_when_requested(self):
        calls = []

        class FakeMessage:
            content = "ok"

        class FakeChoice:
            message = FakeMessage()

        class FakeUsage:
            prompt_tokens = 1
            completion_tokens = 1
            total_tokens = 2

        class FakeCompletions:
            def create(self, **kwargs):
                calls.append(kwargs)
                return type("Response", (), {"choices": [FakeChoice()], "usage": FakeUsage()})()

        class FakeClient:
            def __init__(self, api_key):
                self.chat = type("Chat", (), {"completions": FakeCompletions()})()

        with patch("utils.llm.load_api_key", return_value="sk-test"), patch("utils.llm.OpenAI", FakeClient):
            query_openai([{"role": "user", "content": "Return exactly true."}], "gpt-5.4-mini")
            query_openai([{"role": "user", "content": "Return ONLY valid JSON: {\"ok\": true}"}], "gpt-5.4-mini")

        self.assertNotIn("response_format", calls[0])
        self.assertEqual(calls[1]["response_format"], {"type": "json_object"})

    def test_query_openai_omits_temperature_for_models_that_reject_it(self):
        calls = []

        class FakeCompletions:
            def create(self, **kwargs):
                calls.append(kwargs)
                message = type("Message", (), {"content": "{}"})()
                usage = type("Usage", (), {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})()
                return type("Response", (), {"choices": [type("Choice", (), {"message": message})()], "usage": usage})()

        class FakeClient:
            def __init__(self, api_key):
                self.chat = type("Chat", (), {"completions": FakeCompletions()})()

        with patch("utils.llm.load_api_key", return_value="sk-test"), patch("utils.llm.OpenAI", FakeClient):
            for model in ("gpt-6-luna", "o4-mini", "gpt-5.4-mini"):
                query_openai([{"role": "user", "content": "Return JSON."}], model)

        self.assertNotIn("temperature", calls[0])
        self.assertNotIn("temperature", calls[1])
        self.assertEqual(calls[2]["temperature"], 0.3)


if __name__ == "__main__":
    unittest.main()
