# Extraction Schema P0 Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make legacy and new extraction schemas behave consistently while preserving the user's Step 4 editing context across refreshes.

**Architecture:** Extend artifact-backed finalized inference with explicit draft precedence, pass the visible step through the chat API, and preserve same-project UI state during data refresh. Keep schema mutation deterministic and gated by finalized state.

**Tech Stack:** Python 3.11, Starlette, vanilla JavaScript, pytest/unittest.

---

### Task 1: Legacy Finalized-State Compatibility

**Files:**
- Modify: `reviewpilot_core/extraction_schema.py`
- Test: `tests/test_extraction_schema.py`

- [ ] Add tests proving legacy schema plus non-empty extraction results is finalized, while an explicit draft without a marker remains a draft.
- [ ] Run `python -m pytest -q tests/test_extraction_schema.py` and confirm the new tests fail for the expected state-inference reason.
- [ ] Implement the minimal artifact precedence in `is_schema_finalized()`.
- [ ] Re-run the focused tests and confirm they pass.

### Task 2: Step-Aware Schema Chat

**Files:**
- Modify: `web_app.py`
- Modify: `agents/lead_agent.py`
- Test: `tests/test_lead_agent.py`
- Test: `tests/test_web_app.py`

- [ ] Add tests proving the chat API forwards `step: extraction`, a completed project routes Step 4 chat to schema handling, and finalized mutation is blocked without changing schema.
- [ ] Run the focused tests and confirm they fail for missing step propagation/routing.
- [ ] Add optional step context to `LeadAgent.handle_message()` and schema routing; block mutation commands while finalized.
- [ ] Pass the validated step from the Starlette endpoint.
- [ ] Re-run focused tests and confirm they pass.

### Task 3: Preserve Step 4 Across Refreshes

**Files:**
- Modify: `frontend/app.js`
- Test: `tests/test_frontend_contract.py`

- [ ] Add contract assertions for same-project view preservation, chat step payload, and visible-step optimistic messages.
- [ ] Run `python -m pytest -q tests/test_frontend_contract.py` and confirm failure against current code.
- [ ] Add `preserveView` behavior to `setData()` and use it for chat/action refreshes; derive optimistic message step from the visible workflow step.
- [ ] Re-run frontend contract tests and confirm they pass.

### Task 4: End-to-End Verification

**Files:**
- Verify only; no planned production edits.

- [ ] Run all extraction, lead-agent, web-app, state-projection, and frontend-contract tests.
- [ ] Run `python -m pytest -q` and confirm zero failures.
- [ ] Restart or reload the local app, exercise the QA project's Step 4 edit/chat/finalize flow, and confirm the selected step remains stable.
- [ ] Confirm the QA schema is restored to 10 fields and finalized after the browser test.
