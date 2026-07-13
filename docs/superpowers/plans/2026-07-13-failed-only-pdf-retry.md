# Failed-only PDF Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a versioned, confirmed, failed-only PDF recovery flow that never requests or changes existing successes and survives refresh or process interruption without mixed authoritative artifacts.

**Architecture:** Build a small `retrieval_retry` core boundary in three independently reviewed increments. First expose strict stable identities, report revisions, and retry projection; then add isolated staging plus an abort-first report/included/ledger transaction and Web action; finally add the minimal UI confirmation and deterministic browser evidence.

**Tech Stack:** Python 3.12, Starlette, existing `TaskRunner`, `WorkflowActionAdapter`/`DownloadAgentContract`, atomic JSON/JSONL helpers, zero-build JavaScript, unittest/pytest, Node behavior tests, in-app Chromium.

---

## File map

- Create `reviewpilot_core/retrieval_retry.py`: strict retry identity, report revision, retry snapshot validation, durable marker/recovery, isolated staging, and merge transaction.
- Create `tests/test_retrieval_retry.py`: pure identity/revision, validation, staging isolation, merge, rollback, crash recovery, and integrity tests.
- Modify `reviewpilot_core/state_projection.py`: reconcile retry transactions before reads and expose path-safe `retrievalRecovery`.
- Modify `reviewpilot_core/workflow_state.py`: map `retry-failed-downloads` to retrieval and classify merged retrieval counts through the existing structured contract.
- Modify `web_app.py`: add confirmation/revision errors and the reserved background retry action without sending it through normal full-download handling.
- Modify `tests/test_workflow_state.py`, `tests/test_state_projection.py`, and `tests/test_web_app.py`: ledger, projection, API, confirmation, conflict, and refresh tests.
- Modify `frontend/app.js`, `tests/test_frontend_behavior.py`, and `tests/test_frontend_contract.py`: retry visibility, exact confirmation payload, cancellation, ownership, and authoritative refresh.
- Add browser evidence under `docs/superpowers/evidence/failed-only-retry/` only after the deterministic run completes; store no credentials, personal paths, or raw external errors.

### Task 4A1: Strict retry identity, revision, validation, and projection

**Files:**
- Create: `reviewpilot_core/retrieval_retry.py`
- Create: `tests/test_retrieval_retry.py`
- Modify: `reviewpilot_core/state_projection.py`
- Test: `tests/test_state_projection.py`

- [ ] **Step 1: Write failing identity and revision tests**

Add table-driven tests proving identity precedence `id → doi → url → title`, trim/case normalization, opaque deterministic SHA-256 IDs, duplicate/missing identity rejection, canonical report revision stability under dictionary key ordering, and revision changes when report content or retrieval ledger attempt/status changes.

```python
retry_id = stable_retry_id({"id": " PM-1 ", "doi": "ignored"})
assert retry_id == stable_retry_id({"id": "pm-1"})
assert "pm-1" not in retry_id
assert retrieval_report_revision(report, {"attempt": 2, "status": "partial"}) != retrieval_report_revision(report, {"attempt": 3, "status": "partial"})
```

- [ ] **Step 2: Run the identity tests and record RED**

Run: `.venv-native/bin/python -m pytest -q tests/test_retrieval_retry.py -k 'identity or revision'`

Expected: FAIL because `retrieval_retry` and its functions do not exist.

- [ ] **Step 3: Implement the minimal pure identity/revision functions**

Define these public functions with no filesystem mutation:

```python
_IDENTITY_FIELDS = ("id", "doi", "url", "title")

def stable_retry_id(row: dict[str, Any]) -> str:
    if not isinstance(row, dict):
        raise ValueError("Retry paper must be an object")
    for field in _IDENTITY_FIELDS:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            identity = f"{field}:{value.strip().casefold()}"
            return hashlib.sha256(identity.encode("utf-8")).hexdigest()
    raise ValueError("Retry paper is missing a stable identity")

def retrieval_report_revision(report: dict[str, Any], stage: dict[str, Any]) -> str:
    payload = {
        "report": report,
        "retrieval": {"attempt": stage["attempt"], "status": stage["status"]},
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

Use canonical JSON with `sort_keys=True` and compact separators. Reject non-object rows, empty identity, ambiguous duplicate IDs, bool/non-integer counts, malformed detail arrays, and path-bearing user-visible labels. Do not use `pdf_count` to validate current success.

- [ ] **Step 4: Write failing current-snapshot validation tests**

Cover one-to-one mapping from every failed report entry to one included row; safe label/failure-class projection; completed/no-failure, stale, wrong ledger count, missing report, malformed report, duplicate retry IDs, and missing included row. The returned immutable snapshot must contain report revision, selected-safe item metadata, canonical report/included copies, and no local paths.

```python
snapshot = current_retry_snapshot(project)
assert snapshot.items == ({"retryId": expected_id, "label": "Closed paper", "failureClass": "publisher_paywalled"},)
assert str(project) not in json.dumps(snapshot.to_projection())
```

- [ ] **Step 5: Run validation tests and record RED, then implement GREEN**

Run: `.venv-native/bin/python -m pytest -q tests/test_retrieval_retry.py -k 'snapshot or malformed or duplicate or stale'`

Expected RED: missing validator/projection. Implement:

```python
@dataclass(frozen=True)
class RetryItem:
    retry_id: str
    label: str
    failure_class: str
    report_index: int
    included_index: int

@dataclass(frozen=True)
class RetrySnapshot:
    report_revision: str
    items: tuple[RetryItem, ...]
    report: dict[str, Any]
    included_rows: tuple[dict[str, Any], ...]
    ledger: dict[str, Any]
```

Expose the loader with exact signature `current_retry_snapshot(project_path: Path | str) -> RetrySnapshot`. It must read the authoritative ledger/report/included files once, validate every invariant listed above, build unique report/included identity maps, and return copied immutable snapshot members; on any mismatch it raises a path-free `ValueError`.

Reuse the strict production report contract and strict JSONL parsing instead of recreating a weaker parser. Require retrieval status `partial` or structured `failed`, `stale=False`, and `failed>0`.

- [ ] **Step 6: Add `retrievalRecovery` projection tests and implementation**

Test that `build_rp_data()` exposes `{canRetry: true, reportRevision, items}` only for a valid current non-stale failed report. Completed, stale, malformed, duplicate, and no-failure reports expose `{canRetry: false, reportRevision: "", items: []}` without crashing the whole project state; safe user-facing labels remain and all paths are absent.

- [ ] **Step 7: Verify Task 4A1 and commit**

Run:

```bash
.venv-native/bin/python -m pytest -q tests/test_retrieval_retry.py tests/test_state_projection.py
.venv-native/bin/python -m pytest -q
git diff --check
git status --short
```

Expected: all tests pass, zero skipped, only existing dependency warnings, clean diff check, and only intended files modified. Commit the exact files with message `Add retrieval retry identity contracts`.

### Task 4A2: Durable isolated retry transaction and Web action

**Files:**
- Modify: `reviewpilot_core/retrieval_retry.py`
- Modify: `reviewpilot_core/workflow_state.py`
- Modify: `reviewpilot_core/state_projection.py`
- Modify: `web_app.py`
- Modify: `tests/test_retrieval_retry.py`
- Modify: `tests/test_workflow_state.py`
- Modify: `tests/test_state_projection.py`
- Modify: `tests/test_web_app.py`

- [ ] **Step 1: Write failing request-validation and reservation tests**

Cover empty/non-list/duplicate/unknown IDs, no failures, stale report, changed report revision, missing or mismatched confirmation, and active task conflict. Each rejection must prove byte-identical report/included/ledger files, unchanged PDF hashes, zero downloader calls, and no marker/staging residue.

Use this request contract:

```json
{
  "failed_ids": ["opaque-id"],
  "report_revision": "current-revision",
  "retry_confirmation": {
    "expected_report_revision": "current-revision",
    "failed_ids": ["opaque-id"]
  }
}
```

The first valid unconfirmed request must return HTTP 409 with `confirmationRequired`, `expectedReportRevision`, and the exact selected IDs; confirmation is a second request.

- [ ] **Step 2: Run focused validation tests and record RED**

Run: `.venv-native/bin/python -m pytest -q tests/test_web_app.py tests/test_retrieval_retry.py -k 'retry and (confirm or revision or unknown or duplicate or stale or active)'`

Expected: FAIL because the action and transaction do not exist.

- [ ] **Step 3: Add action mapping and read-only prepare validation**

Add `retry-failed-downloads → retrieval` to the workflow action map and make structured outcome classification use the same `success/failed` contract as `download-pdfs`. Add Web exceptions that map confirmation to 409, revision conflict to 409, and malformed/no-current-recovery requests to 400. Do not begin a marker or call `start_action()` until all request and confirmation values match the current snapshot.

- [ ] **Step 4: Write failing staging-isolation and merge tests**

Use an injected `run_download(staging_root, staging_project_id)` fake that reads its staging included file. Assert it receives exactly the selected failed rows and never receives successful or unselected rows. Cover selected success, selected repeat failure, subset retry, aggregate completed/partial/failed, stable order, original successful and unselected row deep equality, original PDF hash preservation, collision refusal, and exact merged detail counts.

```python
def fake_run(staging_root, staging_project_id):
    rows = read_jsonl(Path(staging_root) / staging_project_id / "filtered" / "included_papers.jsonl")
    assert [stable_retry_id(row) for row in rows] == selected_ids
    return canonical_staging_result
```

- [ ] **Step 5: Implement isolated staging and deterministic merge**

Create staging only below the authoritative project, reject all symlink roots/files, and use `DownloadAgentContract` against the staging project in production. Assign collision-free destination names derived from report revision and retry ID; never overwrite an existing file. Strip internal staging/retry fields before target publication. Build merged report lists from previous canonical entries plus retry results, recompute classification lists from remaining failures, and validate the target through the same strict retrieval contract before publication.

- [ ] **Step 6: Write failing abort/apply recovery tests**

Inject failures after marker creation, after `start_action`, after new PDF copy, after report write, after included write, after terminal ledger write, and after `phase=apply`. For every pre-promotion failure/restart, assert exact old report/included/ledger restoration and new-PDF/staging cleanup. For post-promotion failure/restart, assert idempotent target report/included/ledger and retained new PDF. Add refresh-during-active coverage proving recovery does not abort a live in-process retry.

- [ ] **Step 7: Implement abort-first marker and reconciliation**

Use a project marker such as `.retrieval_retry_pending.json` and an in-process active-project set. The marker begins with `phase=abort`, exact old snapshots, selected IDs, expected revision, and planned new file names before any authoritative mutation. Publish target files and terminal ledger while still abortable, persist target snapshots, promote to `phase=apply`, then clean marker/staging. `build_rp_data()` and retry submission reconcile abandoned markers before reading or validating, but skip reconciliation for the live active retry. Ordinary exceptions invoke abort restoration instead of leaving retrieval failed/stale.

- [ ] **Step 8: Add Web task lifecycle and downstream-stale tests**

Prove task reservation rejects a simultaneous retry or setup/action mutation; active state projects `retry-failed-downloads`; running retry blocks current/downstream exports; success leaves material downstream stale; exception restores the exact previous downstream ledger; task terminal status matches merged aggregate status; and task errors expose no raw paths or exception messages.

- [ ] **Step 9: Verify Task 4A2 and commit**

Run:

```bash
.venv-native/bin/python -m pytest -q tests/test_retrieval_retry.py tests/test_workflow_state.py tests/test_state_projection.py tests/test_web_app.py tests/test_task_runner.py
.venv-native/bin/python -m pytest -q
git diff --check
git status --short
```

Expected: all tests pass, zero skipped, only existing warnings, and clean diff check. Commit only the listed backend/test files with message `Add transactional failed PDF retry`.

### Task 4B: Frontend confirmation, refresh recovery, and browser proof

**Files:**
- Modify: `frontend/app.js`
- Modify: `tests/test_frontend_behavior.py`
- Modify: `tests/test_frontend_contract.py`
- Modify: `tests/test_web_app.py` only if the deterministic browser harness needs an existing injection seam
- Add: `docs/superpowers/evidence/failed-only-retry/README.md`
- Add: `docs/superpowers/evidence/failed-only-retry/report.json`
- Add: path-safe screenshots captured by the browser run

- [ ] **Step 1: Write failing frontend rendering and payload tests**

Test that a valid `retrievalRecovery` renders one `Retry failed downloads` control with failed count, never embeds IDs in unsafe HTML attributes without escaping, and is absent when `canRetry=false`. A click must submit all current IDs and revision, interpret 409 confirmation, show the exact replacement prompt, and resubmit the frozen IDs/revision inside `retry_confirmation`. Cancellation sends no second request.

- [ ] **Step 2: Run frontend tests and record RED**

Run: `.venv-native/bin/python -m pytest -q tests/test_frontend_behavior.py tests/test_frontend_contract.py`

Expected: FAIL because the control and confirmation handler do not exist.

- [ ] **Step 3: Implement the minimal frontend flow**

Extend normalized state with a default empty `retrievalRecovery`. Render the action inside the existing retrieval outcome area, route it through `postAction('retry-failed-downloads', payload)`, and add a dedicated confirmation branch using the server-returned exact report revision and IDs. Reuse the existing active-task owner, duplicate-click disabling, task monitor, exception refresh, and project navigation guards; do not add a second poller.

- [ ] **Step 4: Add executable async ownership tests**

Use the existing Node test seam to prove double click submits once, refresh attaches to the same active retry task, project switch prevents stale paint, a structured partial retry refreshes the banner with remaining IDs, a completed retry removes the banner, and an escaped task error refreshes the original retryable state before displaying the safe error.

- [ ] **Step 5: Verify automated Task 4B tests**

Run:

```bash
.venv-native/bin/python -m pytest -q tests/test_frontend_behavior.py tests/test_frontend_contract.py tests/test_web_app.py tests/test_state_projection.py tests/test_retrieval_retry.py
.venv-native/bin/python -m pytest -q
git diff --check
```

Expected: all tests pass, zero skipped, only existing warnings.

- [ ] **Step 6: Run deterministic browser E2E at 1280×800**

Start a local server using production UI/API and an injected deterministic staging downloader. Seed one canonical successful included row/PDF and one current retryable failed row/report with a partial retrieval ledger. Record the successful row object and SHA-256 before the run. Open the project, click retry, confirm, refresh while the task is running, and wait for authoritative completion.

Assert from server-side call evidence that the staging downloader received only the failed retry ID. Assert after completion: original success object deep-equal; original PDF SHA-256 unchanged; failed row now downloaded; merged report `success=2, failed=0`; retrieval ledger completed/non-stale; retry banner absent; Information Extraction enabled; no path appears in projected JSON or visible UI.

- [ ] **Step 7: Repeat browser layout check at 1440×900 and save evidence**

At 1440×900 verify the retry banner/control, confirmation, running state, and completed retrieval layout have no overlap, clipping, horizontal overflow, inaccessible control, or stale action. Store path-safe screenshots and a redacted `report.json` containing project ID, viewport, task ID hash, before/after counts, retry call IDs, preserved-file hashes, and PASS/FAIL assertions. Do not store local absolute paths.

- [ ] **Step 8: Commit Task 4B**

Run `git status --short`, inspect every evidence file for credentials/personal paths, run `git diff --check`, and commit only frontend/tests/evidence with message `Expose failed-only PDF retry recovery`.

## Final Task 4 release gate

After each task, dispatch an independent specification reviewer and only after approval dispatch an independent code-quality reviewer. Fix and re-review every Critical or Important item before moving forward. After Task 4B, run the full native suite once more from the exact final SHA, verify a clean worktree, and only then unblock the HCI real example.
