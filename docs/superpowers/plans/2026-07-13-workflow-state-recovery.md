# Workflow state, invalidation, partial outcome, and failed-only retry plan

## Problem

The r7 happy path passes, but the current product still derives workflow completion from artifact existence. Setup updates can mix new configuration with old downstream outputs, real partial results are displayed as completed, and the Web App has no failed-only retry action. These are formal inner-beta integrity blockers.

## Task 1 — Explicit workflow-state ledger

### Goal

Create one persisted, atomic, project-scoped workflow ledger that becomes the sole source for stage status after an explicit one-time legacy migration.

### State contract

- File: `workflow_state.json` at the project root, written through the existing atomic JSON writer.
- Versioned document with `stages` for `collection`, `screening`, `retrieval`, `extraction`, and `categorization`.
- Every stage exposes exactly one of `not_started`, `ready`, `running`, `partial`, `failed`, or `completed`, plus attempt number, updated time, safe error summary, counts, `stale`, and last-valid metadata. `stale` is independent of the status enum.
- New projects start with collection `ready` and all later stages `not_started`.
- Action mapping is deterministic: collect→collection; screen→screening; download-pdfs→retrieval; generate/finalize/edit/run-extraction→extraction; suggest/categorize→categorization.
- Schema generation/finalization and category suggestion do not complete their workflow stage; they leave it `ready`. Only run-extraction and categorize complete those stages in Task 1.
- An action attempt records `running` before domain work, `failed` on an escaped exception, and the action-defined terminal state on success. A failed attempt preserves last-valid metadata.
- `build_rp_data()` reads stage state from the ledger, never from artifact existence when a ledger exists. The API projects both the six-state `stageState` and compatibility step status needed by the current UI.
- Legacy projects without a ledger are migrated exactly once from validated artifact evidence, store migration metadata, then use only the ledger on later reads.
- A persisted `running` stage without a matching in-process active task is reconciled to `failed` with a safe abnormal-termination message when project state is built after restart.

### Files and TDD

- Add `reviewpilot_core/workflow_state.py` and `tests/test_workflow_state.py` for schema validation, atomic initialization/update, legal states, action mapping, attempt/error/last-valid behavior, and one-time migration.
- Modify `web_app.py` project creation and action submission; add Web tests proving new ledger creation, running/failed/completed lifecycle, active-task projection, and restart reconciliation.
- Modify `reviewpilot_core/state_projection.py`; replace `_current_step()`/`_is_done()` as stage truth. Add tests showing that creating/removing an artifact after ledger creation cannot silently change stage state.
- Modify `frontend/app.js` minimally to retain compatibility while projecting stage status for future warning/error UI. Add contract tests for server-owned `stageState` and refresh behavior.
- Do not implement setup invalidation, partial classification, failed-only retry, or new visual design in Task 1.

### Verification

Run focused workflow-state, task-runner, Web, projection, and frontend tests; full native suite; `git diff --check`; then independent specification and quality reviews.

## Task 2 — Setup revision, impact preview, confirmation, and stale downstream stages

- Normalize the persisted setup payload and compute a stable revision.
- `PUT /setup` with a material change and current downstream results returns an impact list without writing unless confirmation includes the expected current revision.
- Confirmed change writes setup atomically, advances revision, marks dependent stages stale, preserves old artifacts, blocks stale exports/current-result projection, and rejects stale confirmations.
- Setup change is rejected while any project task is active.
- Re-running a stale stage that will replace last-valid output requires a second explicit overwrite confirmation listing affected stages.
- Tests cover no-op updates, every material input class, active-task conflict, stale revision, preserved artifacts, blocked exports, and UI confirmation.

## Task 3 — Real deterministic partial and failed outcomes

- Collection: some source success + some source failure→partial; zero success + failure→failed.
- Retrieval: success + failed→partial; zero success + failed→failed.
- Extraction: processed success + errors→partial; zero processed success + errors→failed.
- Outcome is derived from structured counts/contracts, never the LLM.
- Task status, ledger status, main canvas, activity, and assistant reply must agree.
- Frontend warning/error states show completed count, failed count/items, retryability, and next action.

## Task 4 — Failed-only PDF retry

- Add `retry-failed-downloads` using only stable failed IDs from the current non-stale download report revision.
- Never request, mutate, or replace successful items; atomically merge successful retry results with the previous valid report and included-paper metadata.
- Reject no-failure, unknown ID, stale report, revision conflict, duplicate task, and unconfirmed replacement cases.
- Browser E2E must prove one-success/one-retryable-failure becomes completed after only the failed item is requested again, including refresh between attempts.

## Release dependency

Tasks must execute in order and each must clear specification and quality review before the next begins. HCI and spatial real runs begin only after Task 4, because those runs are the evidence surface for partial and retry behavior.
