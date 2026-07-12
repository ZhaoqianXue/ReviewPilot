# Native Baseline Isolation Repairs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove two reproducibility failures so the native full suite can establish a truthful baseline without local scratch files or LLM secrets.

**Architecture:** Preserve production dependency injection. A supplied PDF LLM or overridden web-search extractor must not trigger an unrelated eager credential load; the default web-search method keeps its existing lazy client initialization. Remove the stale test that imports an intentionally ignored benchmark script and retain the tracked production factory contract.

**Tech Stack:** Python 3.12 arm64, unittest/pytest, `unittest.mock`, ReviewPilot agent contracts.

---

### Task 1: Preserve web-search dependency injection during extraction

**Files:**
- Modify: `tests/test_extraction_agent.py`
- Modify: `agents/extraction_agent.py:120-129`

- [ ] Write a failing regression test that creates one paper with `web_search_fallback_pending=True` and no downloaded PDF, supplies a harmless `llm_query`, overrides `_query_web_search_extraction`, patches `_init_client` to raise if called, runs extraction, and asserts one successful `web_search_fallback` result.
- [ ] Run the isolated test and verify RED fails because `_init_client` is called before the override.
- [ ] Remove eager initialization for `needs_web_search_client`; initialize eagerly only when the direct PDF path needs a client and no `llm_query` was supplied. Do not change `_query_web_search_extraction()` lazy initialization.

```python
needs_pdf_llm_client = active_llm_query is None and any(
    not (paper.get("web_search_fallback_pending") and not paper.get("pdf_downloaded"))
    for paper in papers
)
if needs_pdf_llm_client:
    self._init_client()
```

- [ ] Run the new test GREEN, then all `tests/test_extraction_agent.py` and the failing Web smoke test.
- [ ] Run `git diff --check`, self-review, and commit with `Avoid eager web-search credential loading`.

### Task 2: Remove the ignored benchmark dependency and establish baseline

**Files:**
- Modify: `tests/test_fast_pdf_downloader.py:2612-2626`
- Create: `docs/internal-testing/2026-07-12-automated-baseline.md`

- [ ] Re-run the isolated benchmark test and record RED `FileNotFoundError` for `.benchmark_step3_download/benchmark_step3_download.py`.
- [ ] Delete only `test_benchmark_runner_can_create_optimized_downloader`. Keep and run the adjacent tracked `test_production_pdf_downloader_factory_defaults_to_fast_downloader` as the supported product contract.
- [ ] Run `.venv-native/bin/python -m pytest --collect-only -q`, then `.venv-native/bin/python -m pytest -q`.
- [ ] Write the baseline report with interpreter path/architecture/version, pytest version, exact commands, collected/passed/failed/error/skipped/warning counts, durations, excluded tests, local ignored `config.py` symlink prerequisite, and explicit statement that `secrets.txt` was not linked or used.
- [ ] Run `git diff --check`, self-review, and commit the deleted stale test plus report with `Establish native automated test baseline`.

## Completion gate

The repair is complete only when the targeted Web smoke and production downloader factory tests pass, full native collection and execution exit 0, zero tests are skipped, no secret file is required, and the report reproduces the exact observed totals.
