"""Tests never reach a real LLM provider.

Every provider call in utils.llm reads its key through load_api_key, so blocking it
here turns an accidental live call (slow, paid, nondeterministic) into a clear
failure. Tests that exercise the provider client patch load_api_key themselves.
"""

import pytest


@pytest.fixture(autouse=True)
def block_live_llm_calls(monkeypatch):
    def refuse(provider: str) -> str:
        raise RuntimeError(f"Test attempted a live {provider} LLM call; patch the LLM query instead.")

    monkeypatch.setattr("utils.llm.load_api_key", refuse)
