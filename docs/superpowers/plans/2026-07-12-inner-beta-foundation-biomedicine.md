# ReviewPilot Inner-Beta Foundation and Biomedicine Iteration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a trustworthy native test/runtime baseline, remove known duplicate-task and destructive-output risks, and complete the first clean real Web App diagnostic with the LLM-biomedicine scenario.

**Architecture:** Keep the Starlette monolith and zero-build frontend. Add small, testable boundaries for task conflicts, active-task projection, and atomic artifact replacement; then run the first real scenario through the browser. Unknown defects discovered by the real run receive their own evidence-backed TDD plan before code changes.

**Tech Stack:** Python 3.12 arm64, Starlette, unittest/pytest, vanilla JavaScript, Playwright/in-app browser, JSON/JSONL project artifacts.

---

## Plan family and scope

This is plan 1 of 4. It produces working, testable foundation software and the authoritative Scenario 1 diagnostic. Plan 2 combines Scenario 1 remediation with the mandatory explicit stage-state model, downstream invalidation confirmation, partial-success semantics, and failed-item retry. Plan 3 covers the HCI real iteration. Plan 4 covers the spatial-authoring iteration, both viewport regressions, export verification, three-scenario combined audit, and release handoff. These approved requirements cannot be omitted even if the first diagnostic does not expose them. No unknown defect is guessed in this plan.

### Task 1: Create a reproducible native development environment

**Files:**
- Create: `requirements-dev.txt`
- Create: `scripts/bootstrap_native_env.sh`
- Modify: `.gitignore`
- Modify: `README.md:42-66`

- [ ] **Step 1: Record the failing environment evidence**

Run: `file .venv/bin/python && uname -m`
Expected: `.venv/bin/python` reports `x86_64`; host reports `arm64`.

- [ ] **Step 2: Add development dependencies**

```text
-r requirements.txt
pytest>=9.0,<10.0
httpx>=0.28,<1.0
```

Add `.venv-native/` under the existing Environment section in `.gitignore`.

- [ ] **Step 3: Add a fail-loud native bootstrap script**

```bash
#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
ENV_DIR="${ENV_DIR:-.venv-native}"
MACHINE="$($PYTHON_BIN -c 'import platform; print(platform.machine())')"
if [[ "$MACHINE" != "arm64" ]]; then
  echo "Refusing to create $ENV_DIR with $MACHINE Python; use a native arm64 interpreter." >&2
  exit 2
fi
"$PYTHON_BIN" -m venv "$ENV_DIR"
"$ENV_DIR/bin/python" -m pip install --upgrade pip
"$ENV_DIR/bin/python" -m pip install -r requirements-dev.txt
"$ENV_DIR/bin/python" -c 'import platform; assert platform.machine() == "arm64"'
```

- [ ] **Step 4: Verify the script rejects the current x86 interpreter**

Run: `PYTHON_BIN=.venv/bin/python ENV_DIR=/tmp/reviewpilot-invalid-env bash scripts/bootstrap_native_env.sh`
Expected: exit 2 with `Refusing to create` and no environment created.

- [ ] **Step 5: Build the native environment without deleting `.venv`**

Run: `PYTHON_BIN=/Users/zhaoqianxue/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 ENV_DIR=.venv-native bash scripts/bootstrap_native_env.sh`
Expected: exit 0; `.venv-native/bin/python` reports arm64.

- [ ] **Step 6: Update README commands to prefer `.venv-native` on Apple Silicon and explain the architecture check**

Run: `bash -n scripts/bootstrap_native_env.sh && .venv-native/bin/python -m pytest --version`
Expected: shell syntax passes and pytest 9.x prints immediately.

- [ ] **Step 7: Commit**

```bash
git add requirements-dev.txt scripts/bootstrap_native_env.sh .gitignore README.md
git commit -m "Add native Apple Silicon development environment"
```

### Task 2: Establish the automated baseline

**Files:**
- Create: `docs/internal-testing/2026-07-12-automated-baseline.md`

- [ ] **Step 1: Collect tests under the native interpreter**

Run: `.venv-native/bin/python -m pytest --collect-only -q`
Expected: exit 0 with a non-zero `tests collected` summary and no hang.

- [ ] **Step 2: Run the full suite and capture exact totals, duration, failures, errors, and skips**

Run: `.venv-native/bin/python -m pytest -q`
Expected: exit 0. If it fails, stop this plan, invoke systematic-debugging, record the exact failing node ids, and create a focused repair plan before touching implementation.

- [ ] **Step 3: Write the baseline report with commands and verbatim summary lines**

The report must state the interpreter path/architecture, collected count, passed count, skipped count, duration, and any tests intentionally excluded. Do not write “all tests pass” if any item is skipped or excluded.

- [ ] **Step 4: Commit**

```bash
git add docs/internal-testing/2026-07-12-automated-baseline.md
git commit -m "Document native automated test baseline"
```

### Task 3: Reject duplicate mutating tasks and preserve actionable errors

**Files:**
- Modify: `reviewpilot_core/task_runner.py:11-59`
- Modify: `web_app.py:37-45,107-117`
- Modify: `frontend/app.js:392-404`
- Modify: `tests/test_task_runner.py`
- Modify: `tests/test_web_app.py`
- Modify: `tests/test_frontend_contract.py`

- [ ] **Step 1: Write failing task-runner tests**

```python
def test_submit_rejects_second_active_task_for_same_project(self):
    runner = TaskRunner(max_workers=2)
    started, release = Event(), Event()
    first_id = runner.submit("demo", "collect", lambda: (started.set(), release.wait(timeout=2)))
    self.assertTrue(started.wait(timeout=1))
    with self.assertRaises(TaskConflictError) as caught:
        runner.submit("demo", "screen", lambda: None)
    self.assertEqual(caught.exception.task["task_id"], first_id)
    release.set()
    runner.wait(first_id, timeout=2)

def test_different_projects_can_submit_while_one_is_active(self):
    runner = TaskRunner(max_workers=2)
    release = Event()
    first_id = runner.submit("one", "collect", lambda: release.wait(timeout=2))
    second_id = runner.submit("two", "collect", lambda: None)
    runner.wait(second_id, timeout=2)
    release.set()
    runner.wait(first_id, timeout=2)
```

- [ ] **Step 2: Run the tests to verify the missing conflict contract fails**

Run: `.venv-native/bin/python -m pytest tests/test_task_runner.py -q`
Expected: FAIL because `TaskConflictError` is undefined or duplicate submission is accepted.

- [ ] **Step 3: Implement the minimal conflict boundary**

```python
class TaskConflictError(RuntimeError):
    def __init__(self, task: dict):
        self.task = dict(task)
        super().__init__(f"Project '{task['project_id']}' already has running action '{task['action']}'")

def _active_for_project_unlocked(self, project_id: str) -> dict | None:
    return next((task for task in self._tasks.values() if task["project_id"] == project_id and task["status"] == "running"), None)

def active_for_project(self, project_id: str) -> dict | None:
    with self._registry_lock:
        task = self._active_for_project_unlocked(project_id)
        return dict(task) if task else None
```

Call `_active_for_project_unlocked()` inside the existing `submit()` registry lock and raise `TaskConflictError` before allocating a new task id.

- [ ] **Step 4: Add a failing Web API test for HTTP 409 and active-task context**

```python
runner = TaskRunner(max_workers=2)
started, release = Event(), Event()
runner.submit("demo", "collect", lambda: (started.set(), release.wait(timeout=2)))
self.assertTrue(started.wait(timeout=1))
old_runner, web_app.task_runner = web_app.task_runner, runner
try:
    response = TestClient(web_app.create_app()).post("/projects/demo/actions/screen")
finally:
    release.set()
    web_app.task_runner = old_runner
self.assertEqual(response.status_code, 409)
self.assertEqual(response.json()["active_task"]["action"], "collect")
self.assertIn("already has running action", response.json()["detail"])
```

- [ ] **Step 5: Return the conflict from `project_action()` without launching work**

```python
except TaskConflictError as exc:
    return JSONResponse({"detail": str(exc), "active_task": exc.task}, status_code=409)
```

- [ ] **Step 6: Make the frontend retain server error detail**

```javascript
if (!res.ok) {
  const body = await res.json().catch(() => ({}));
  throw new Error(body.detail || `Action failed: ${res.status}`);
}
```

Add a frontend contract assertion for `body.detail` and remove any assertion that accepts status-only action errors.

- [ ] **Step 7: Verify targeted and surrounding contracts**

Run: `.venv-native/bin/python -m pytest tests/test_task_runner.py tests/test_web_app.py tests/test_frontend_contract.py -q`
Expected: PASS with no skips.

- [ ] **Step 8: Commit**

```bash
git add reviewpilot_core/task_runner.py web_app.py frontend/app.js tests/test_task_runner.py tests/test_web_app.py tests/test_frontend_contract.py
git commit -m "Prevent duplicate project workflow tasks"
```

### Task 4: Restore active task state after browser refresh

**Files:**
- Modify: `web_app.py:48-52,100-104`
- Modify: `frontend/app.js:26-41,180-211,476-486,1357-1375`
- Modify: `tests/test_web_app.py`
- Modify: `tests/test_frontend_contract.py`

- [ ] **Step 1: Write failing API and rendered-state tests**

```python
task_id = web_app.task_runner.submit("demo", "collect", blocking_action)
state = TestClient(web_app.create_app()).get("/projects/demo/state").json()
self.assertEqual(state["activeTask"]["task_id"], task_id)
self.assertEqual(state["activeTask"]["action"], "collect")
self.assertEqual(state["activeTask"]["status"], "running")
self.assertIn(f'\\"task_id\\": \\"{task_id}\\"', web_app.render_workspace_html(output_root, "demo"))
```

- [ ] **Step 2: Add one state-construction helper and use it for JSON and SSR**

```python
def build_project_state(output_root: Path | str, project_id: str) -> dict:
    state = build_rp_data(Path(output_root), project_id)
    state["activeTask"] = task_runner.active_for_project(project_id)
    return state
```

Use this helper from `render_workspace_html()` and `project_state()` so refresh and polling cannot diverge.

- [ ] **Step 3: Write a failing frontend contract for resume behavior**

Require `normalizeData()` to retain `activeTask`, initial UI state to use its action and timestamp, and `mount()` to poll the existing task id before refreshing project state.

- [ ] **Step 4: Implement minimal resume behavior**

```javascript
activeTask: data.activeTask || null,
```

Initialize `actionPending` from `D.activeTask?.action`. Add `resumeActiveTask()` that calls `waitForTask(D.activeTask.task_id)`, refreshes server state with `{ preserveView: true }`, surfaces failure in `state.actionError`, and clears pending state in `finally`. Call it once after the initial `paint()`.

- [ ] **Step 5: Verify refresh contracts**

Run: `.venv-native/bin/python -m pytest tests/test_task_runner.py tests/test_web_app.py tests/test_frontend_contract.py -q`
Expected: PASS with no skips.

- [ ] **Step 6: Commit**

```bash
git add web_app.py frontend/app.js tests/test_web_app.py tests/test_frontend_contract.py
git commit -m "Restore running workflow task after refresh"
```

### Task 5: Make stage artifact replacement transactional

**Files:**
- Create: `reviewpilot_core/atomic_files.py`
- Create: `tests/test_atomic_files.py`
- Modify: `utils/jsonl_handler.py:65-102,171-180`
- Modify: `reviewpilot_core/extraction_schema.py:234-236`
- Modify: `reviewpilot_core/categorization_analysis.py:128-158`
- Modify: `reviewpilot_core/sub_agent_contracts.py:142-267`
- Modify: `agents/extraction_agent.py:135 onward`
- Modify: `tests/test_extraction_agent.py`
- Modify: `tests/test_extraction_schema.py`
- Modify: `tests/test_categorization_analysis.py`
- Modify: `tests/test_workflow_adapter.py`

- [ ] **Step 1: Write failing atomicity tests**

```python
def test_atomic_output_preserves_previous_file_on_failure(self):
    target = self.root / "result.jsonl"
    target.write_text('{"old": true}\n', encoding="utf-8")
    with self.assertRaises(RuntimeError):
        with atomic_output_path(target) as pending:
            pending.write_text('{"new": true}\n', encoding="utf-8")
            raise RuntimeError("abort")
    self.assertEqual(target.read_text(encoding="utf-8"), '{"old": true}\n')
    self.assertEqual(list(self.root.glob(".*.tmp")), [])
```

Also test successful text, JSON, and JSONL replacement.

- [ ] **Step 2: Implement one shared atomic primitive**

```python
@contextmanager
def atomic_output_path(target: Path | str):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(fd)
    pending = Path(name)
    try:
        yield pending
        os.replace(pending, target)
    finally:
        pending.unlink(missing_ok=True)
```

Build `atomic_write_text`, `atomic_write_json`, and `atomic_write_jsonl` on this context manager.

- [ ] **Step 3: Route shared JSON/JSONL writers and core direct writers through the primitive**

Replace overwrite-mode writes in `utils/jsonl_handler.py`, extraction schema, categorization outputs, and sub-agent normalization outputs. Append-only logs and downloader progress journals remain append-only; they are not final stage artifacts.

- [ ] **Step 4: Make extraction results transactional**

Wrap the entire extraction result loop in `with atomic_output_path(output_file) as pending_output:` and send every `append_jsonl` call to `pending_output`. Only normal completion replaces `extraction_results.jsonl`; an escaping exception preserves the previous valid file.

- [ ] **Step 5: Add an extraction regression test that injects an escaping writer failure and proves the old result remains unchanged**

Run: `.venv-native/bin/python -m pytest tests/test_atomic_files.py tests/test_extraction_agent.py tests/test_extraction_schema.py tests/test_categorization_analysis.py tests/test_workflow_adapter.py -q`
Expected: PASS with no skips.

- [ ] **Step 6: Run the full suite**

Run: `.venv-native/bin/python -m pytest -q`
Expected: same or greater pass count than Task 2, zero failures/errors, and every skip explicitly unchanged.

- [ ] **Step 7: Commit**

```bash
git add reviewpilot_core/atomic_files.py utils/jsonl_handler.py reviewpilot_core/extraction_schema.py reviewpilot_core/categorization_analysis.py reviewpilot_core/sub_agent_contracts.py agents/extraction_agent.py tests/test_atomic_files.py tests/test_extraction_agent.py tests/test_extraction_schema.py tests/test_categorization_analysis.py tests/test_workflow_adapter.py
git commit -m "Write workflow artifacts atomically"
```

### Task 6: Freeze the three real scenario inputs and evidence protocol

**Files:**
- Create: `docs/internal-testing/scenarios.json`
- Create: `docs/internal-testing/README.md`
- Create: `tests/test_inner_beta_scenarios.py`

- [ ] **Step 1: Write a failing fixture contract test**

```python
catalog = json.loads((ROOT / "docs/internal-testing/scenarios.json").read_text(encoding="utf-8"))
self.assertEqual(set(catalog), {"biomedicine", "hci", "spatial_authoring"})
self.assertEqual(len({item["project_name"] for item in catalog.values()}), 3)
for item in catalog.values():
    self.assertEqual(item["platforms"], ["pubmed", "arxiv", "openalex"])
    self.assertEqual(item["source_limits"], {"pubmed": 5, "arxiv": 5, "openalex": 5})
    self.assertEqual(item["max_results"], 5)
    self.assertIn(" AND ", item["search_terms"])
self.assertEqual(catalog["biomedicine"]["date_start"], "2023-01-01")
self.assertEqual(catalog["hci"]["date_start"], "2020-01-01")
self.assertEqual(catalog["spatial_authoring"]["date_start"], "2020-01-01")
```

- [ ] **Step 2: Add the scenario catalog using the exact `search_terms` from the three authoritative historical `search_conditions.json` files**

Each payload contains `project_name`, `description`, `primary_topic`, `domain`, `search_terms`, `platforms`, `source_limits`, `max_results: 5`, `date_start`, and an empty `date_end`. The run-time project name appends `-r<N>` so historical output is never reused.

- [ ] **Step 3: Add the evidence protocol**

Document required timestamps, browser viewport, task id/status, per-stage counts, external error classification, screenshots, artifact paths, defect priority, regression command, rerun project id, and pass/fail disposition. State that credentials and secret values must never enter the report.

- [ ] **Step 4: Verify and commit**

Run: `.venv-native/bin/python -m pytest tests/test_inner_beta_scenarios.py -q`
Expected: PASS with 3 scenarios validated.

```bash
git add docs/internal-testing/scenarios.json docs/internal-testing/README.md tests/test_inner_beta_scenarios.py
git commit -m "Define inner-beta real-use scenarios"
```

### Task 7: Execute the first real browser iteration — biomedicine

**Files:**
- Create: `docs/internal-testing/runs/2026-07-12-biomedicine-r1.md`
- Create only if defects exist: `docs/superpowers/plans/2026-07-12-biomedicine-r1-defects.md`

- [ ] **Step 1: Start the real app with the native environment**

Run: `.venv-native/bin/uvicorn web_app:app --host 127.0.0.1 --port 5602`
Expected: server remains healthy and `GET /workspace` returns 200.

- [ ] **Step 2: Use the browser at 1280×800 to create a clean project from `biomedicine` with every source limit set to 5**

Verify the saved `search_conditions.json` matches the catalog before running collection.

- [ ] **Step 3: Execute collection, screening, PDF retrieval, schema generation/finalization, extraction, category suggestion, and categorization through visible UI controls**

At every stage record task id, elapsed time, counts, output artifact, UI conclusion, assistant conclusion, and any external failure. Refresh once during a running action and verify the active action resumes. Attempt one duplicate action while running and verify no second task is created.

- [ ] **Step 4: Audit the run against every Scenario 1 acceptance item in the approved design**

Do not call the run passed if a stage is inferred only from file existence, an error is hidden, output is silently overwritten, metadata fallback is labeled PDF extraction, or UI and artifact state disagree.

- [ ] **Step 5: If any product defect appears, stop implementation and create the exact child TDD plan**

The child plan must name reproduction evidence, root cause, exact files, failing test code, minimal fix, targeted verification, full regression, and clean rerun id. External platform failures without a product defect are documented, not “fixed” with fabricated success.

- [ ] **Step 6: Commit the diagnostic evidence**

```bash
git add docs/internal-testing/runs/2026-07-12-biomedicine-r1.md
# Run this second command only when the defect plan exists:
git add docs/superpowers/plans/2026-07-12-biomedicine-r1-defects.md
git commit -m "Record first biomedicine inner-beta iteration"
```

## Completion checkpoint

This plan is complete only when the native full suite is trustworthy, duplicate work is blocked, refresh restores the active task, final stage artifacts are transactional, the three inputs are frozen, and the first clean biomedicine browser run has an evidence-backed disposition. The overall goal remains active until Scenario 1 remediation, HCI, spatial-authoring, combined regression, and the release handoff all satisfy the approved design.
