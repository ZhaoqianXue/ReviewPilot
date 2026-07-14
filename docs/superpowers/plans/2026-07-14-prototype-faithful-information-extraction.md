# Prototype-Faithful Information Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reproduce the Prototype's Information Extraction interface and decision flow while making Preview, JSON, Regenerate, and Finalize operate on real project data and tasks.

**Architecture:** Keep Prototype-faithful rendering in the existing zero-build frontend, add a focused backend preview service with a schema-revision cache, and reuse one ExtractionAgent single-paper primitive for preview and formal runs. Preview remains outside the workflow ledger; schema regeneration and finalize-plus-extraction are explicit composite workflow actions.

**Tech Stack:** Python 3.12, Starlette, vanilla JavaScript, JSON/JSONL artifacts, existing TaskRunner and workflow ledger, unittest/pytest, Node behavior tests, in-app Chromium.

---

### Task 1: Safe schema-ordered preview projection

**Files:**
- Create: `reviewpilot_core/extraction_preview.py`
- Create: `tests/test_extraction_preview.py`
- Modify: `reviewpilot_core/state_projection.py:226-312`
- Modify: `frontend/data.js:1-30`

- [ ] **Step 1: Write failing projection tests**

Create fixtures with two included papers, a normalized two-field schema, and a formal extraction row containing `pdf_file`, `extracted_at`, `extraction_cost_usd`, `extracted_data`, and unknown keys. Assert `project_preview_projection(project, 0)` returns schema fields in order, uses `"—"` for missing values, reports `1 / 2`, and contains none of the internal keys. Add tests for out-of-range indexes, an error row with a path-bearing exception, completed-result precedence, and a cache with the wrong schema revision.

```python
preview = project_preview_projection(project, 0)
self.assertEqual([field["name"] for field in preview["fields"]], ["methods", "key_findings"])
self.assertEqual(preview["fields"][1]["value"], "—")
self.assertEqual(preview["paper"]["title"], "Paper A")
self.assertNotIn("pdf_file", json.dumps(preview))
self.assertNotIn(str(project), json.dumps(preview))
```

- [ ] **Step 2: Run the tests and verify the red state**

Run: `pytest -q tests/test_extraction_preview.py`

Expected: collection fails because `reviewpilot_core.extraction_preview` does not exist.

- [ ] **Step 3: Implement the pure preview contract**

Implement `schema_revision(schema)`, `paper_key(paper, index)`, `load_preview_cache(project, schema)`, `write_preview_cache(project, schema, key, row)`, and `project_preview_projection(project, index)`. Canonicalize the schema with `json.dumps(..., sort_keys=True, separators=(",", ":"))`, hash with SHA-256, read included papers and formal results through strict project-store helpers, and project values only by iterating normalized `schema["fields"]`. Return `missing`, `ready`, or `error`; sanitize error text with the existing safe-text utility.

```python
def _field_rows(schema: dict, row: dict) -> list[dict]:
    extracted = row.get("extracted_data") if isinstance(row.get("extracted_data"), dict) else {}
    return [{
        "name": field["name"],
        "label": _label(field["name"]),
        "type": field["type"],
        "required": bool(field.get("required")),
        "value": _display_value(row.get(field["name"], extracted.get(field["name"]))) or "—",
    } for field in schema.get("fields", [])]
```

Replace legacy `previewFields`/`previewPaper` in `build_rp_data()` with `extractionPreview: project_preview_projection(path, 0)` and `schemaJson: schema`. Add matching empty defaults to new-project and frontend normalization data.

- [ ] **Step 4: Run focused tests**

Run: `pytest -q tests/test_extraction_preview.py tests/test_state_projection.py`

Expected: all tests pass and existing state-projection expectations are updated to the new contract.

- [ ] **Step 5: Commit**

```bash
git add reviewpilot_core/extraction_preview.py reviewpilot_core/state_projection.py frontend/data.js tests/test_extraction_preview.py tests/test_state_projection.py
git commit -m "Add safe extraction preview projection"
```

### Task 2: Real single-paper preview and revision-safe cache

**Files:**
- Modify: `agents/extraction_agent.py:76-230`
- Modify: `reviewpilot_core/extraction_preview.py`
- Modify: `tests/test_extraction_agent.py`
- Modify: `tests/test_extraction_preview.py`

- [ ] **Step 1: Write failing agent and cache tests**

Assert `ExtractionAgent.extract_one(...)` returns the same flattened success record used by `run()`, supports PDF and web-search fallback, and does not create `extraction_results.jsonl` or `extraction_stats.json`. Assert `run_project_preview(project, index, ...)` writes one atomic cache item, preserves cached siblings, replaces the cache when the schema revision changes, and refuses publication when the schema changes during execution.

- [ ] **Step 2: Verify failure before implementation**

Run: `pytest -q tests/test_extraction_agent.py tests/test_extraction_preview.py`

Expected: failures report missing `extract_one` and `run_project_preview`.

- [ ] **Step 3: Extract one production code path**

Move the existing per-paper PDF/fallback branch into `extract_one(paper, row_number, extraction_prompt, pdf_folder, pdf_files, ...) -> dict`; make `run()` call it without changing formal output counts or artifacts. Build draft preview prompts with `build_extraction_prompts(config, schema)` without finalizing the schema.

```python
def run_project_preview(project: Path, index: int, *, llm_query=None, pdf_reader=None, web_search_query=None) -> dict:
    schema = load_schema_draft(project)
    before = schema_revision(schema)
    paper, total = _paper_at(project, index)
    prompt = draft_extraction_prompt(project, schema)
    agent = ExtractionAgent(project, llm_query=llm_query, pdf_reader=pdf_reader,
                            web_search_query=web_search_query)
    row = agent.extract_one(paper=paper, row_number=index + 1,
                            extraction_prompt=prompt,
                            pdf_folder=project / "pdfs",
                            pdf_files=sorted((project / "pdfs").glob("*.pdf")))
    if schema_revision(load_schema_draft(project)) != before:
        raise ValueError("Extraction schema changed while preview was running")
    write_preview_cache(project, schema, paper_key(paper, index), row)
    return {"status": "preview_ready", "paper_index": index, "total": total}
```

- [ ] **Step 4: Run agent, preview, and extraction regression tests**

Run: `pytest -q tests/test_extraction_agent.py tests/test_extraction_preview.py tests/test_workflow_adapter.py`

Expected: all pass; formal extraction artifact/count contracts remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add agents/extraction_agent.py reviewpilot_core/extraction_preview.py tests/test_extraction_agent.py tests/test_extraction_preview.py
git commit -m "Add real single-paper extraction previews"
```

### Task 3: Preview HTTP API and refresh-safe task metadata

**Files:**
- Modify: `reviewpilot_core/task_runner.py:20-55`
- Modify: `web_app.py:120-165,251-263,438-490`
- Modify: `tests/test_task_runner.py`
- Modify: `tests/test_web_app.py`

- [ ] **Step 1: Write failing API tests**

Cover `GET /projects/demo/extraction-preview/0`, invalid indexes, unknown projects, `POST .../preview-extraction`, active-project conflicts, safe task failures, and a task record containing `paper_index` after refresh. Assert preview completion does not change `workflow_state.json` or formal extraction files.

- [ ] **Step 2: Verify the red state**

Run: `pytest -q tests/test_task_runner.py tests/test_web_app.py -k 'preview or metadata'`

Expected: routes and task metadata are absent.

- [ ] **Step 3: Add bounded task metadata and preview routes**

Extend `TaskRunner.submit(..., metadata=None)` to copy only JSON-scalar metadata into the public task record. Add `GET /projects/{project_id}/extraction-preview/{paper_index:int}`. Special-case `preview-extraction` in `submit_project_action`: require an integer `paper_index` that is not bool, submit `run_project_preview`, attach `{"paper_index": index}`, and do not call workflow ledger functions.

```python
if action == "preview-extraction":
    index = (input_data or {}).get("paper_index")
    if isinstance(index, bool) or not isinstance(index, int):
        raise ValueError("paper_index must be an integer")
    return task_runner.submit(project_id, action,
        lambda: run_project_preview(project_path, index, llm_query=llm_query),
        metadata={"paper_index": index})
```

- [ ] **Step 4: Run API regressions**

Run: `pytest -q tests/test_task_runner.py tests/test_web_app.py`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add reviewpilot_core/task_runner.py web_app.py tests/test_task_runner.py tests/test_web_app.py
git commit -m "Expose asynchronous paper preview API"
```

### Task 4: Prototype decision actions and workflow transitions

**Files:**
- Modify: `reviewpilot_core/workflow_state.py:18-40,101-130,160-190`
- Modify: `agents/lead_agent.py:210-270,416-525`
- Modify: `web_app.py:438-490`
- Modify: `tests/test_workflow_state.py`
- Modify: `tests/test_lead_agent.py`
- Modify: `tests/test_web_app.py`

- [ ] **Step 1: Write failing composite-action tests**

Assert `regenerate-schema` produces a draft/ready extraction stage and makes downstream material output stale only after confirmation. Assert `finalize-and-run-extraction` finalizes the schema, invokes exactly one existing `run-extraction` contract, returns its counts, and completes/partials/fails the extraction stage correctly. Assert extraction failure leaves the finalized marker intact and supports retry.

- [ ] **Step 2: Verify the red state**

Run: `pytest -q tests/test_workflow_state.py tests/test_lead_agent.py tests/test_web_app.py -k 'regenerate_schema or finalize_and_run'`

Expected: unsupported-action failures.

- [ ] **Step 3: Implement the two explicit composites**

Add both actions to `ACTION_STAGES`, prerequisites, readiness/count handling, the web allowlist, and action-stage mapping. In LeadAgent, `regenerate-schema` reopens a finalized schema then calls the existing `generate-schema` contract; `finalize-and-run-extraction` calls `finalize_schema()`, ensures the production prompt, then calls the existing `run-extraction` contract. Return deterministic chat text and next actions; do not add duplicate sub-agent contracts.

```python
if action == "finalize-and-run-extraction":
    self._verify_stage_artifacts(project_path, "prompt_extraction")
    finalize_schema(project_path)
    artifacts.append(str(self._ensure_extraction_prompt(project_path, config)))
    result = self._call_workflow_action("run-extraction", project_id)
    artifacts.extend(self._verify_stage_artifacts(project_path, "extraction"))
    return self._action_result(project_path, "extraction", result, artifacts, action="run-extraction")
```

- [ ] **Step 4: Run workflow and web regressions**

Run: `pytest -q tests/test_workflow_state.py tests/test_lead_agent.py tests/test_web_app.py tests/test_workflow_adapter.py`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add reviewpilot_core/workflow_state.py agents/lead_agent.py web_app.py tests/test_workflow_state.py tests/test_lead_agent.py tests/test_web_app.py
git commit -m "Add prototype extraction decision actions"
```

### Task 5: Reproduce the Prototype Step 4 interface

This task owns the accessibility acceptance criteria as well as visual fidelity: tab semantics, accessible names, focus restoration, live busy/error announcements, and keyboard navigation must pass together.

**Files:**
- Modify: `frontend/app.js:210-370,390-435,920-990,1413-1446,1611-1680,1810-1955`
- Modify: `tests/test_frontend_behavior.py`
- Modify: `tests/test_frontend_contract.py`

- [ ] **Step 1: Write failing frontend tests**

Add Node behavior tests for bounded preview navigation, same-project `previewIndex` restoration, stale-response ownership, lazy preview action routing, JSON dialog open/close, and action selection by schema state. Add source-contract tests for Prototype labels and hierarchy, `role="tab"`, `aria-selected`, previous/next labels, Decision needed, Finalize Schema, Preview, JSON, Regenerate schema, and the absence of visible Edit Schema / Run Extraction buttons.

- [ ] **Step 2: Verify the red state**

Run: `pytest -q tests/test_frontend_behavior.py tests/test_frontend_contract.py`

Expected: new assertions fail against the current Step 4 markup.

- [ ] **Step 3: Implement state and data loading**

Add `previewIndex`, `previewLoading`, and `schemaJsonOpen` to UI state and snapshot persistence. Add `fetchExtractionPreview(projectId, index)` with project-generation ownership, update `D.extractionPreview`, and reload the selected preview after a preview task completes. Project changes reset the index to zero.

- [ ] **Step 4: Implement Prototype-faithful rendering and actions**

Replace extraction tabs with accessible button tabs using the Prototype inline values. Render the schema grid unchanged in geometry. Render preview title/ref, bounded caret buttons, counter, status/error/Generate preview states, and schema-only field rows. Insert a Draft-only assistant decision card after chat messages. Route Finalize to `finalize-and-run-extraction`, Preview to the preview tab plus lazy task, JSON to a read-only schema modal, and Regenerate to `generate-schema` or `regenerate-schema` by workbench state. Pending and failed cards must preserve the same width, border, and typography hierarchy.

```javascript
function extractionDecisionCard(v) {
  if (!v.isExtraction || v.schemaWorkbench.status !== 'draft') return '';
  return `<section data-ui="extraction-decision" style="margin-left:26px;border:1px solid #c8d8e8;border-radius:13px;padding:13px;background:#fffefc;">
    <div data-ui="decision-label">Decision needed</div>
    <div>Finalize the ${v.allFields.length}-field schema, or preview it on a paper first.</div>
    <button data-act="action" data-action="finalize-and-run-extraction">Finalize Schema</button>
    <button data-act="open-preview">Preview</button><button data-act="schema-json">JSON</button>
    <button data-act="action" data-action="generate-schema">Regenerate schema</button>
  </section>`;
}
```

- [ ] **Step 5: Run frontend and projection tests**

Run: `pytest -q tests/test_frontend_behavior.py tests/test_frontend_contract.py tests/test_state_projection.py tests/test_extraction_preview.py`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/app.js tests/test_frontend_behavior.py tests/test_frontend_contract.py
git commit -m "Reproduce prototype extraction interactions"
```

### Task 6: Browser acceptance, Quick Start validation, and full regression

**Files:**
- Create: `docs/internal-testing/runs/2026-07-14-information-extraction-prototype-parity.md`
- Modify only if a test exposes a scoped defect: files already listed in Tasks 1-5

- [ ] **Step 1: Run the complete automated suite without skips**

Run: `pytest -q`

Expected: every test passes; report any skipped test as a failure rather than claiming completion.

- [ ] **Step 2: Run deterministic browser acceptance at 1280 × 860**

Open the Prototype and web app side by side. Verify Draft fields and Preview views for three-column widths, stepper/tab placement, Decision needed hierarchy, dense field rows, navigation controls, JSON modal, focus order, and no console errors. Exercise Generate → Preview papers 1, 2, and last → JSON → Regenerate → Finalize → running refresh → completed/partial result browsing.

- [ ] **Step 3: Validate all three Quick Start histories**

For Biomedical, HCI, and Urban, confirm every included paper is reachable by preview navigation, successful rows show schema fields only, failed rows are explicit, counts agree with artifacts, and none of `pdf_file`, `row_number`, `extracted_at`, `extraction_model`, `extraction_cost_usd`, or raw `extracted_data` appears.

- [ ] **Step 4: Record evidence and exact results**

Write the run document with commit SHA, commands, pass/fail counts, project-by-project paper counts, screenshots used, console-log result, deviations, and zero silently skipped checks.

- [ ] **Step 5: Commit acceptance evidence**

```bash
git add docs/internal-testing/runs/2026-07-14-information-extraction-prototype-parity.md
git commit -m "Verify prototype extraction parity"
```
