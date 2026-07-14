# Evidence-Oriented Screening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent review-intent wording from excluding relevant primary evidence during title/abstract screening.

**Architecture:** Keep the existing `search_conditions.json` and binary filtering interfaces unchanged. Change only PromptAgent's generated screening semantics from publication-type equivalence to substantive evidence relevance, then regenerate and rerun every affected stage in the three canonical Quick Start projects.

**Tech Stack:** Python 3, `unittest`, FastAPI/Uvicorn, existing ReviewPilot agents and workflow APIs.

---

### Task 1: Protect evidence-oriented prompt semantics

**Files:**
- Modify: `tests/test_prompt_agent.py`
- Modify: `agents/prompt_agent.py`

- [ ] **Step 1: Write the failing regression test**

Add a test that calls `_build_relevance_prompt` with `primary_topic="Survey of large language models for biomedicine"` and `domain="Biomedical informatics and health AI"`. Assert that its instruction says candidate papers may be primary studies, methods, systems, datasets, benchmarks, applications, evaluations, or reviews; says the candidate itself need not be a survey/review; and still requires substantive relevance to both topic and domain.

- [ ] **Step 2: Verify RED**

Run `python -m unittest tests.test_prompt_agent.PromptAgentTests.test_review_objective_does_not_require_candidates_to_be_reviews` and expect an assertion failure because the current prompt requires the paper to concern the literal survey phrasing.

- [ ] **Step 3: Implement the minimal prompt change**

In `_build_relevance_prompt`, replace publication-type-equivalence wording with evidence-oriented wording. Preserve the title/abstract-only input and exact binary output contract. Add explicit positive evidence types and explicit review-intent guidance; do not change function signatures or artifact keys.

- [ ] **Step 4: Verify GREEN and related tests**

Run `python -m unittest tests.test_prompt_agent tests.test_workflow_adapter tests.test_lead_agent` and expect all tests to pass.

- [ ] **Step 5: Commit**

Commit `tests/test_prompt_agent.py` and `agents/prompt_agent.py` with message `Fix review-intent relevance screening`.

### Task 2: Run regression suites

**Files:**
- No source changes expected.

- [ ] **Step 1: Run focused behavior suites**

Run `python -m unittest tests.test_prompt_agent tests.test_filtering_agent tests.test_workflow_adapter tests.test_lead_agent tests.test_web_app tests.test_state_projection tests.test_frontend_contract` and require zero failures, errors, and skips.

- [ ] **Step 2: Run static checks**

Run `node --check frontend/app.js` and `git diff --check`; both must exit 0.

- [ ] **Step 3: Run the full suite**

Run `python -m unittest discover -s tests`, record the exact test count, and require zero failures and errors.

### Task 3: Regenerate three canonical showcases

**Files:**
- Regenerate ignored run artifacts under `output/quick-start-*-showcase/`.

- [ ] **Step 1: Regenerate each relevance prompt**

For Biomedical, HCI, and Urban, run the existing `generate-relevance-prompt` workflow action against the unchanged authoritative setup and verify the saved prompt contains the evidence-oriented language.

- [ ] **Step 2: Rerun screening from collected records**

Run the existing screening action for each project. Record identified, post-dedup, included, and excluded counts; do not impose an inclusion quota.

- [ ] **Step 3: Rerun invalidated downstream stages**

For each project, run retrieval, finalize a schema based on the revised included corpus, run extraction with legitimate web fallbacks where PDFs remain unavailable, and run categorization. Retry only the workflow's supported failed-only retrieval path.

- [ ] **Step 4: Verify authoritative state**

Require `activeTask=null`, `stale=false` for every stage, terminal collection/screening/extraction/categorization, finalized schema, and seven existing export entries for every project.

### Task 4: Freeze evidence and reopen the product

**Files:**
- Modify: `docs/internal-testing/runs/2026-07-14-quick-start-showcases.md`

- [ ] **Step 1: Update the run report**

Replace the prior counts with the regenerated counts, document remaining publisher-access failures and fallbacks, and record per-project export bytes plus the 21-endpoint aggregate.

- [ ] **Step 2: Verify History contract**

Read each live state and assert that History lists Biomedical, HCI, and Urban canonical IDs in Quick Start order, each setup has `max_results=10` and three source limits of 10, and no History item has `starterTopic`.

- [ ] **Step 3: Commit evidence**

Commit the updated run report with message `Refresh Quick Start showcases after screening fix`.

- [ ] **Step 4: Restart and open**

Restart Uvicorn at `127.0.0.1:5602`, repeat the authoritative-state checks against the restarted service, and open `/workspace` for the user.
