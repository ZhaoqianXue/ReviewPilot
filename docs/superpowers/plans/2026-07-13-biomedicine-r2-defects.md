# Biomedicine r2 source-limit integrity repair plan

> **Execution method:** Subagent-Driven Development with a fresh implementer, independent specification review, and independent code-quality review before the clean r3 browser rerun.

**Goal:** Make the Search Setup dialog's visible `Max/source` value update every selected source limit when the user changes it, without flattening an existing project's deliberately differentiated per-source limits when the dialog value is unchanged.

**Evidence:** `docs/internal-testing/runs/2026-07-13-biomedicine-r2.md` and its two screenshots. The dialog showed `5`; the created UI and `search_conditions.json` showed `10` for all three sources, with exactly one successful project request and no collection call.

**Root cause:** `updateDraftFromForm()` merges `max_results` but leaves `state.setupDraft.source_limits` at its initialized `{10,10,10}`. `setupPayloadFromDraft()` then calls `selectedSourceLimits()` and derives `max_results` from that stale map, so the form value cannot affect the outgoing payload. Confidence: high.

## Task 1: Synchronize a changed dialog maximum into selected source limits

**Files:**

- Modify: `frontend/app.js`
- Modify: `tests/test_frontend_behavior.py`
- Modify: `tests/test_frontend_contract.py`

### Step 1: Add failing executable behavior tests

Add a production-used pure helper for applying the submitted dialog maximum to a setup draft. Execute it through the existing Node harness and prove:

- New project: prior max `10`, submitted max `5`, selected PubMed/OpenAlex/arXiv → every selected source becomes `5` and payload maximum is `5`.
- Existing differentiated limits `{pubmed:10, openalex:25, arxiv:75}` with unchanged submitted max `75` → preserve all three values.
- Existing differentiated limits with submitted max changed from `75` to `20` → set every selected source to `20`.
- Unselected sources are not introduced into the outgoing source-limit map.

Add a production-wiring contract proving `updateDraftFromForm()` captures the previous maximum before merging the form payload and invokes the helper. The current code must fail because it only performs a shallow merge.

### Step 2: Implement the minimum conditional propagation

Before merging the form payload, capture the previous normalized maximum. After merging, if and only if `payload.max_results` is present and differs numerically/string-normalized from the previous maximum, rebuild `source_limits` for the currently selected platforms using the submitted value. If the value is unchanged, preserve existing differentiated limits. Do not change backend normalization or canvas per-source editing behavior.

### Step 3: Verify and commit

```bash
.venv-native/bin/python -m pytest tests/test_frontend_behavior.py tests/test_frontend_contract.py tests/test_web_app.py -q
.venv-native/bin/python -m pytest -q
git diff --check
git add frontend/app.js tests/test_frontend_behavior.py tests/test_frontend_contract.py
git commit -m "Honor dialog source limits"
```

Expected: zero failures/errors/skips and pass count at least 308 plus existing subtests.

## Task 2: Align New Review's default platform order with frozen inputs

**Files:**

- Modify: `reviewpilot_core/state_projection.py`
- Modify: `web_app.py`
- Modify: `frontend/app.js`
- Modify: `tests/test_state_projection.py`
- Modify: `tests/test_web_app.py`
- Modify: `tests/test_frontend_contract.py`

### Step 1: Add failing order contracts

Require New Review state, backend platform fallback, frontend fallback/visible source order, and a project created without an explicit platform override to use the frozen authoritative order `pubmed`, `arxiv`, `openalex`. The current product literals `pubmed`, `openalex`, `arxiv` must fail these tests.

### Step 2: Change only canonical defaults

Update the new-project and missing-input fallback literals to the authoritative order. Preserve any explicit platform order supplied by a caller or existing project; do not sort user configuration and do not alter source membership.

### Step 3: Verify and commit

```bash
.venv-native/bin/python -m pytest tests/test_state_projection.py tests/test_web_app.py tests/test_frontend_contract.py -q
.venv-native/bin/python -m pytest -q
git diff --check
git add reviewpilot_core/state_projection.py web_app.py frontend/app.js tests/test_state_projection.py tests/test_web_app.py tests/test_frontend_contract.py
git commit -m "Align default source order with scenarios"
```

Expected: zero failures/errors/skips and explicit tests proving caller-supplied order remains unchanged.

## Task 3: Clean browser rerun under biomedicine r3

Reserve `inner-beta-biomedicine-r3`; abort if the API id differs. Reload the app at the reviewed fix commit, recreate the exact frozen input, and require the visible source controls plus persisted `max_results`, `max_results_per_platform`, and all selected `source_limits` to equal `5` before collection. Verify an unchanged setup save preserves deliberately differentiated source limits through a focused browser/API check.

Require the saved platform order to match the frozen catalog exactly. Continue the full Scenario 1 workflow only after every projected external-call control is correct. Any new product defect receives a new run id and child plan; external failures are documented without fabricated success.

## Completion gate

The repair is complete only after Tasks 1 and 2 each pass both reviews and r3 proves the dialog value, source controls, platform order, API state, and persisted artifact all agree. The overall inner-beta goal remains active until the complete biomedicine flow, mandatory state/partial/retry work, HCI, spatial-authoring, combined regression, and release handoff pass.
