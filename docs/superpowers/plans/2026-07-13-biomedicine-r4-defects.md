# Biomedicine r4 final-stage repair plan

> **Execution method:** Subagent-Driven Development. Each task receives a fresh implementer and independent specification/quality review before r5.

**Goal:** Distinguish category suggestions from applied categorization in assistant routing, and remove local absolute paths from user-facing stage replies, so the final workflow can be completed without misleading or privacy-leaking guidance.

**Evidence:** `docs/internal-testing/runs/2026-07-13-biomedicine-r4.md` and the sanitized r4 task summary. Category suggestion returned nine editable values but claimed categorization was complete with no next action. The preceding extraction reply exposed a local absolute output path.

## Task 1: Route category suggestions to review, confirm, and apply

**Files:**

- Modify: `agents/lead_agent.py`
- Modify: `tests/test_lead_agent.py`
- Modify if endpoint contract coverage is needed: `tests/test_web_app.py`

### Step 1: Add failing action-aware routing tests

For `suggest-categories`, assert the returned and persisted reply is deterministic, names the selected field and suggestion count, tells the user to review/confirm/apply, does not say categorization is completed, and does not call a second LLM for routing. Require `next_actions == ["confirm_categories", "apply_categorization"]`.

For the real `categorize` action, preserve final completion semantics: the applied result may say categorization is complete and must return no next action. The current implementation must fail because `_stage_reply()` and `_next_actions()` receive only stage `categorization` and cannot distinguish the two actions.

### Step 2: Add the minimum action-aware deterministic branch

Pass the canvas action into reply/next-action selection. Special-case `suggest-categories` using only structured `field` and integer category count. Keep applied categorization's existing final summary and empty next-actions behavior. Do not alter the categorization engine or frontend confirmation state.

### Step 3: Verify and commit

```bash
.venv-native/bin/python -m pytest tests/test_lead_agent.py tests/test_web_app.py -q
.venv-native/bin/python -m pytest -q
git diff --check
git add agents/lead_agent.py tests/test_lead_agent.py tests/test_web_app.py
git commit -m "Route category suggestions to confirmation"
```

## Task 2: Remove local absolute paths from user-facing replies

**Files:**

- Modify: `agents/lead_agent.py`
- Modify: `tests/test_lead_agent.py`
- Modify if shared display sanitization is used: the exact shared module and its focused test

### Step 1: Add failing privacy tests

Feed an extraction structured result containing Unix and Windows absolute output paths. Assert the prompt sent to the summary LLM contains no username/path and the returned/persisted user-facing reply contains no absolute path even if a mocked model echoes one. Counts and stage outcome must remain available. Artifact fields in `LeadAgentResult.artifacts` remain unchanged for machine use.

### Step 2: Implement deterministic display sanitization

Create one small recursive prompt sanitizer for path-like structured fields and one final-reply sanitizer as defense in depth. Replace absolute path values with a neutral phrase such as `project artifact`; do not expose usernames or worktree directories. Do not mutate the workflow result or machine artifact list.

### Step 3: Verify and commit

```bash
.venv-native/bin/python -m pytest tests/test_lead_agent.py tests/test_state_projection.py -q
.venv-native/bin/python -m pytest -q
git diff --check
git add agents/lead_agent.py tests/test_lead_agent.py tests/test_state_projection.py
git commit -m "Redact local paths from stage replies"
```

## Task 3: Clean complete browser rerun under r5

Reserve `inner-beta-biomedicine-r5`, repeat the frozen workflow, and require every prior gate to pass. At category suggestion, require UI, reply, persisted chat, and `next_actions` to direct Confirm/Apply. Confirm categories and apply categorization, then verify final UI/artifacts/export and that no visible assistant reply contains a local path. Any new product defect receives a new run id and plan.

## Completion gate

Both implementation tasks must pass spec and quality review before r5. The biomedicine scenario is complete only when r5 reaches an applied final categorization and evidence/export disposition; the overall inner-beta goal remains active through mandatory state/partial/retry, HCI, spatial-authoring, combined regression, and release handoff.
