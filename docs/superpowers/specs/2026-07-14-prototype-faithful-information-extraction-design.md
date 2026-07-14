# Prototype-Faithful Information Extraction Design

## Goal

Make ReviewPilot's Information Extraction experience faithfully reproduce the repository's interactive Prototype while connecting every visible control to real project data and tasks. The visual source of truth is `reviewpilot-ui.html` / `design/reviewpilot-ui.source.html`; the current extraction agent, schema artifacts, task runner, and workflow ledger remain the execution source of truth.

## Scope

This change covers Step 4 from schema generation through completed or partial extraction and handoff to Step 5. It includes the Schema fields and Preview on paper tabs, the assistant Decision needed card, schema JSON, regeneration, finalization, single-paper preview, formal extraction progress, result browsing, failure recovery, refresh recovery, and accessibility. It does not redesign search, screening, retrieval, or categorization, and it does not copy Prototype fixture values into real projects.

## Product Decisions

The selected direction is real-interaction replication. Prototype layout, hierarchy, labels, spacing, and decision flow are binding, but static Prototype buttons become functioning product operations. Counts, field names, paper titles, and values remain project-derived.

Finalizing a draft starts formal extraction automatically. The separate visible `Run Extraction` button is removed from Step 4. Existing API actions remain available for backward compatibility, but the Prototype-faithful UI uses `finalize-and-run-extraction`.

The Prototype's `Regenerate` control is the only visible schema replacement control. For a draft it generates a replacement draft; for a finalized or completed schema it uses a composite `regenerate-schema` action that reopens and regenerates the schema after the existing stale-output confirmation. The legacy `edit-schema` API remains supported but is not rendered in Step 4.

## Architecture

The frontend continues to use the existing zero-build `frontend/app.js` architecture. Extraction-specific rendering is decomposed into small functions inside that file because splitting one feature into a new frontend module would conflict with current project conventions.

A new `reviewpilot_core/extraction_preview.py` module owns the safe preview contract, schema revision hashing, cache validation, paper selection, and projection of extraction rows into ordered schema fields. `agents/extraction_agent.py` gains a reusable single-paper extraction primitive used by both formal extraction and preview, so preview cannot drift from production extraction behavior.

Preview is an auxiliary project task, not a workflow-stage transition. It runs through the existing `TaskRunner` for project-level mutual exclusion but never calls `start_action`, `complete_action`, or marks formal extraction artifacts stale. Formal composite actions continue through `LeadAgent` and the workflow ledger.

## State Model

Step 4 has six user-visible states:

1. `missing`: no schema exists; the canvas offers Generate Schema.
2. `draft`: the field table is reviewable; the canvas shows Regenerate and the assistant shows Decision needed with Finalize Schema, Preview, JSON, and Regenerate schema.
3. `previewing`: a single-paper task is running; schema actions are locked and the preview canvas shows the selected paper and an in-place progress state.
4. `running`: the schema is finalized and formal extraction is running; the decision card becomes a progress card and duplicate actions are disabled.
5. `completed` or `partial`: fields and extracted results remain browseable; successful and failed papers are distinguishable and Step 5 is reachable under existing workflow rules.
6. `failed`: the assistant shows a recovery card with Retry extraction and Regenerate schema. Existing successful prior output is shown only when the workflow ledger declares it valid.

The browser snapshot adds `previewIndex` and restores it only for the same project. Step, tab, paper index, and active task survive refresh. Project navigation resets preview state and uses the existing ownership guard so late responses cannot paint another project.

## Preview Data Contract

Project state replaces legacy `previewFields` and `previewPaper` with `extractionPreview` and adds `schemaJson`.

```json
{
  "extractionPreview": {
    "index": 0,
    "total": 24,
    "canPrevious": false,
    "canNext": true,
    "status": "ready",
    "paper": {"id": "stable-id", "title": "Paper title", "ref": "pubmed · 2026"},
    "fields": [
      {"name": "methods", "label": "Methods", "type": "Text", "required": false, "value": "..."}
    ],
    "source": "pdf",
    "error": ""
  },
  "schemaJson": {"fields": []}
}
```

Fields are emitted in schema order and include only schema-defined names. `pdf_file`, row numbers, timestamps, model, cost, status internals, `extracted_data`, source URLs, confidence metadata, and unknown keys are never projected as field rows. Missing values render as an em dash. A failed paper returns `status: error` and a safe error summary without paths or exception details.

Completed formal extraction rows take precedence over preview cache rows. Before formal extraction, preview results are stored atomically in `extraction/schema_preview.json` with version, canonical schema SHA-256 revision, and paper-keyed items. A cache with a different schema revision is ignored and replaced on the next successful preview.

## HTTP and Task Contracts

`GET /projects/{project_id}/extraction-preview/{paper_index}` returns the safe projection for exactly one zero-based included-paper index. Invalid indexes return 400, unknown projects return 404, and stale or absent cache returns `status: missing` rather than fabricating values.

`POST /projects/{project_id}/actions/preview-extraction` accepts `{"paper_index": 0}`. It requires a non-empty draft or finalized schema and an included paper. The task record carries the public paper index so refresh can restore the correct progress state. A preview task conflicts with every other active project task.

`POST .../finalize-and-run-extraction` verifies the draft, finalizes it, creates the production prompt, and invokes the existing formal extraction contract inside one task. If extraction fails after finalization, the schema remains finalized and the extraction stage is failed, enabling retry without pretending the schema was rolled back.

`POST .../regenerate-schema` reopens a finalized schema when necessary and invokes the existing schema generator. Existing stale-output confirmation remains mandatory when valid extraction or categorization results would be replaced.

## Interface Fidelity

At 1280 × 860 the three-column shell, widths, hairlines, typography, colors, tab placement, dense field grid, and assistant card match the Prototype inline styles. Dynamic data may change line wrapping but must not change component geometry.

The Draft assistant card reproduces the Prototype hierarchy: Decision needed label, dynamic finalization sentence, full-width Finalize Schema button, side-by-side Preview and JSON buttons, and quiet Regenerate schema button. Preview selects the Preview on paper tab and launches the selected paper only when no current-revision result exists. JSON opens a read-only modal using the normalized schema and returns focus to its trigger on close.

Preview reproduces the title/ref header, previous and next buttons, `current / total` counter, and two-column field/value table. Navigation is bounded, keyboard accessible, and lazy: cached or formal results render immediately; missing results show a Generate preview control rather than launching costly work merely by paging.

Tabs use actual buttons with `role=tab`, `aria-selected`, and keyboard focus while preserving Prototype appearance. Icon-only navigation buttons have accessible labels. Pending actions use `aria-busy`; errors use an appropriate live region.

## Error and Concurrency Handling

Preview failures do not alter the formal extraction stage or delete a valid cache entry for another paper. A schema revision change while preview is running prevents publication of the stale result. Missing PDF cases use the same web-search fallback policy as formal extraction; if neither evidence source succeeds, the preview shows a safe per-paper error and allows retry.

Finalize-and-run is project-exclusive. Duplicate clicks are disabled immediately and server conflicts remain authoritative. Refresh reconnects to the active task. Partial formal results remain browseable, failed papers are labelled, and the existing workflow outcome banner provides counts and retryability.

## Compatibility and Migration

No project artifact migration is required. Existing completed projects derive preview rows from `extraction_results.jsonl`; old draft projects have no cache until the user requests a preview. Legacy `previewFields` and `previewPaper` are removed only after all frontend and state-projection tests use the new contract. Existing `finalize-schema`, `edit-schema`, and `run-extraction` endpoints remain supported for CLI and saved clients.

The three Quick Start histories must render their existing real extraction results through the new projection. Their artifacts are not regenerated solely for this UI change.

## Verification

Backend tests cover schema-ordered safe projection, cache revision invalidation, PDF and web fallback preview, no mutation of formal artifacts, invalid indexes, project task conflicts, composite action ledger transitions, partial/failed results, legacy projects, and path-safe errors.

Frontend tests cover the Decision needed card, exact action routing, tab semantics, JSON modal, bounded navigation, lazy preview behavior, project ownership, refresh restoration, pending/failed states, and the absence of internal metadata. Existing full-suite tests must pass without skips.

Browser acceptance uses a deterministic fixture at 1280 × 860 and compares the Draft fields view and Preview view against the Prototype for shell geometry, tab/button placement, card hierarchy, table density, scrolling, keyboard navigation, and refresh recovery. Acceptance also exercises all three Quick Start histories and verifies that every included result is reachable without exposing raw metadata.

## Acceptance Criteria

- All Prototype Information Extraction controls are visible in the same hierarchy and are functional.
- A draft can be previewed on any included paper before finalization.
- Finalize starts formal extraction automatically and survives refresh.
- Preview exposes only ordered schema fields and supports every included paper.
- JSON shows the normalized real schema.
- Regenerate works for both draft and finalized projects with overwrite protection.
- Completed, partial, failed, and legacy projects remain reviewable.
- No internal path, raw extraction dictionary, model cost, or hidden metadata appears in the Step 4 canvas.
- Focus order, labels, busy states, and keyboard navigation are usable.
- Focused tests, the complete suite, and browser acceptance all pass with zero skipped checks.
