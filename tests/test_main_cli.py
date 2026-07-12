import sys
import types
import unittest
from unittest.mock import patch

import main


class MainCliTests(unittest.TestCase):
    def test_missing_config_defaults_to_frozen_platform_order(self):
        calls = []

        class FakeSearcher:
            def __init__(self, _user_config):
                pass

            def search(self, query, **kwargs):
                calls.append((query, kwargs))
                return {}

            def save_results(self, *_args, **_kwargs):
                pass

        empty_config = types.ModuleType("config")
        with patch.dict(sys.modules, {"config": empty_config}), patch.object(
            main, "AcademicSearcher", FakeSearcher
        ), patch.object(sys, "argv", ["main.py", "AI"]):
            main.main()

        self.assertEqual(calls[0][1]["platforms"], ["pubmed", "arxiv", "openalex"])


if __name__ == "__main__":
    unittest.main()
