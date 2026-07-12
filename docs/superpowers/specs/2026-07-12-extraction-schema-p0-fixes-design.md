# Extraction Schema P0 Fixes Design

## Scope

Fix two release-blocking defects found in browser acceptance testing: legacy completed schemas are misclassified as drafts, and Step 4 is lost whenever chat or a canvas action refreshes project state. Do not redesign the preview, status copy, or layout in this change.

## Backend State Model

A schema is finalized when a new `schema_finalized.json` marker exists. For backward compatibility, a legacy project is also finalized when it has a current extraction schema and non-empty extraction results, provided no draft artifact exists. `Edit Schema` creates the draft artifact and clears the marker, so an explicitly reopened schema is always a draft even when historical extraction results remain.

The browser sends the visible workflow step with each chat request. Step 4 requests use schema routing regardless of the project's latest pipeline stage. A finalized schema may answer read-only schema questions, but mutation commands return an explicit instruction to use `Edit Schema`; they never fall through to generic chat or claim a mutation occurred.

## Frontend State Model

`setData()` accepts an explicit preserve-view option. Chat and canvas actions use it when refreshing the same project, retaining a still-valid step and the fields/preview tab. Project selection and project creation continue to reset to the project's initial step. Optimistic chat messages use the visible step number so server reconciliation does not duplicate them.

## Tests

Backend tests cover legacy finalized inference, explicit draft precedence, Step 4 routing for completed projects, and mutation blocking while finalized. Frontend contract tests cover step/tab preservation, the chat step payload, and visible-step optimistic messages. Browser acceptance repeats edit, chat mutation, and finalize against the QA project while confirming Step 4 remains selected.
