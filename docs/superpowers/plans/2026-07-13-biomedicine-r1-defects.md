# Biomedicine r1 project-creation blocker repair plan

> **Execution method:** Subagent-Driven Development. Use a fresh implementer, then an independent specification reviewer, then an independent code-quality reviewer. Critical or Important findings must be fixed and re-reviewed before the clean browser rerun.

**Goal:** Restore reliable Search Setup form submission so a user can create the exact frozen biomedicine project through visible UI controls, while preserving Quick Start dismissal and preventing duplicate chat/form submission.

**Architecture:** Keep the zero-build frontend and delegated root event listeners. Replace unconditional repaint for unbound clicks with a small production-used decision helper: ordinary/form clicks never repaint, while a click outside an open Quick Start popover repaints once to close it. Protect the decision with executable Node behavior tests and production-wiring contracts, then repeat the real browser scenario under a new `r2` identity.

**Evidence:** `docs/internal-testing/runs/2026-07-13-biomedicine-r1.md` and `docs/internal-testing/evidence/biomedicine-r1/01-setup-before-submit.png` / `02-setup-after-submit-attempt.png`. Two attempts reset the dialog, the server logged no `POST /projects`, and `output/inner-beta-biomedicine-r1` was absent.

**Root cause:** [frontend/app.js](/Users/zhaoqianxue/Desktop/UA/ReviewPilot/.worktrees/inner-beta-foundation/frontend/app.js) renders the setup submit button without `data-act`. The root `click` listener treats every click without `data-act` as a reason to call synchronous `paint()`. `paint()` replaces `root.innerHTML` before the browser performs the submit button's default activation, so the delegated `submit` listener never receives an event and `updateDraftFromForm()` / `POST /projects` never run. Confidence: high.

## Task 1: Protect delegated form submission from unbound-click repaint

**Files:**

- Modify: `frontend/app.js`
- Modify: `tests/test_frontend_behavior.py`
- Modify: `tests/test_frontend_contract.py`

### Step 1: Add failing executable behavior and wiring tests

Extend the production CommonJS exports with a pure decision helper used by the actual root click branch. The Node test must execute the production helper and assert:

```js
assert.equal(shouldPaintUnboundClick({ insideForm: true, insideChatInputArea: false, shouldCloseQuickStart: false }), false);
assert.equal(shouldPaintUnboundClick({ insideForm: true, insideChatInputArea: true, shouldCloseQuickStart: true }), false);
assert.equal(shouldPaintUnboundClick({ insideForm: false, insideChatInputArea: false, shouldCloseQuickStart: true }), true);
assert.equal(shouldPaintUnboundClick({ insideForm: false, insideChatInputArea: false, shouldCloseQuickStart: false }), false);
```

Add a source/wiring contract proving the delegated click listener computes form/chat/Quick Start context and invokes this helper in the `!t` branch. The current implementation must fail because no helper exists and the branch calls `paint()` unconditionally.

### Step 2: Implement the minimum event decision

The root click handler must capture whether Quick Start was open before changing state, identify whether the click is inside the chat input area or any form, and repaint an unbound click only when an open Quick Start must be visibly dismissed and the click is outside a form. Do not defer submission with timers and do not add `data-act` to the submit button as a one-off exception; inputs, keyword form submission, and future forms need the same invariant.

### Step 3: Run targeted tests

```bash
.venv-native/bin/python -m pytest tests/test_frontend_behavior.py tests/test_frontend_contract.py tests/test_web_app.py -q
```

Expected: all pass, no skips. Confirm the test would fail if the unbound branch is temporarily restored to unconditional `paint()`.

### Step 4: Run the full native suite and commit

```bash
.venv-native/bin/python -m pytest -q
git diff --check
git add frontend/app.js tests/test_frontend_behavior.py tests/test_frontend_contract.py
git commit -m "Preserve delegated form submission"
```

Expected: at least 306 tests pass, zero failures/errors/skips, existing warnings explicitly reported.

## Task 2: Clean browser rerun under biomedicine r2

**Files:**

- Create: `docs/internal-testing/runs/2026-07-13-biomedicine-r2.md`
- Create: `docs/internal-testing/evidence/biomedicine-r2/`
- Create only for newly discovered defects: a new evidence-backed child TDD plan

### Step 1: Reserve a clean identity and restart/reload the tested app

Use `inner-beta-biomedicine-r2`; abort if the API returns a different id. Record the fix commit SHA. Reload the local app after the code change and keep the browser viewport at `1280 × 800`.

### Step 2: Verify project creation through the real form

Fill the exact frozen catalog fields through visible controls. Before submitting, capture the dialog. After one click, require exactly one `POST /projects`, exact returned id `inner-beta-biomedicine-r2`, an adopted workspace, and a saved `search_conditions.json` whose projected fields match the catalog. Verify clicking setup inputs retains earlier values. Verify Keyword Add submits once and Quick Start still closes on an outside click without affecting chat Enter/button submission.

### Step 3: Continue the complete Scenario 1 flow

Through visible UI controls run collection, screening, PDF retrieval, schema generation/finalization, extraction, category suggestion, and categorization. Record every task id/status/timestamp, elapsed time, per-stage counts, artifacts, UI conclusion, assistant conclusion, and external failure. During a running action, reload once and require active-task recovery. Submit one duplicate action and require an actionable conflict with no second task.

### Step 4: Stop on any new product defect

Do not infer success from file existence. If UI/task/artifact state disagrees, metadata fallback is labeled PDF extraction, errors are hidden, outputs are silently replaced, or partial results are presented as success, mark the stage failed and create a new exact TDD child plan. External service failures are recorded with the protocol taxonomy and are not fabricated into product success.

### Step 5: Verify, review, and commit evidence

Run the relevant focused regression plus the complete native suite after any implementation. Independently review the r2 report against the evidence protocol and approved Scenario 1 acceptance criteria. Commit only reviewed, redacted evidence with message `Record repaired biomedicine inner-beta iteration`.

## Completion gate

This repair plan is complete only when Task 1 passes specification and quality review and r2 proves real form submission. The overall inner-beta goal remains active even if r2 later encounters another evidence-backed defect; do not proceed to HCI until the mandatory Scenario 1 remediation/state/partial/retry work and clean biomedicine disposition are complete.
