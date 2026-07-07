# ReviewPilot Lead Agent UI/UX Boundary

## Relationship to Architecture
This document defines how the Lead Agent architecture should appear in the web app. It must be read together with [LEAD_AGENT_ARCHITECTURE.md](LEAD_AGENT_ARCHITECTURE.md), which defines the Lead Agent, six evidence Sub Agents, the internal seven-stage evidence pipeline, the user-facing five-step workflow, model policy, and artifact contracts.

The Lead Agent architecture is primarily a backend kernel change. It should not substantially redesign ReviewPilot's frontend appearance, layout, or interaction model.

## Core Principle
Keep the current ReviewPilot web app experience. The Lead Agent changes who decides and orchestrates work behind the scenes; it should not make the UI look like a new product.

The existing three-column layout, sidebar, canvas, stepper, right-side chat, visual language, and workflow rhythm should remain substantially intact. Small text/content changes are allowed only when they help the existing UI accurately reflect the Lead Agent kernel and the final Categorization & Analysis step.

## Area Responsibilities
| Area | Primary responsibility | Allowed interactions | Not allowed |
|---|---|---|---|
| Sidebar | Project navigation, history, settings/help | Project selection and navigation | Workflow decisions or agent controls |
| Top bar / stepper | Project identity and stage navigation/status | Stage/tab navigation if already supported | Complex workflow actions or agent controls |
| Canvas | Business state, data review, setup editing, workflow decisions, final result presentation | Buttons, forms, checkboxes, source selection, date range edits, Add keyword, approve/retry/run actions, categorization controls, export controls | Long agent conversation or hidden orchestration details |
| Chat | Text-only conversation with the Lead Agent | User text input and Lead Agent text responses | Buttons, cards with click actions, decision controls, menus, checkboxes |

## Canvas Is the Operation Area
All click interactions must happen in the canvas. If the user needs to click, choose, approve, retry, add, remove, toggle, edit, or run something, that interaction belongs in the canvas.

Examples of canvas interactions:

- Add keyword.
- Toggle PubMed, OpenAlex, arXiv, or other sources.
- Edit date range.
- Save Search Setup.
- Run collection.
- Approve relevance criteria.
- Run screening.
- Retry unavailable PDFs.
- Approve extraction schema.
- Run extraction.
- Apply categorization.
- Review final categorized results and export files.

The canvas should continue to show business content for the current stage: search setup, screening results, retrieval status, extraction schema/results, and Categorization & Analysis outputs. It should not become an agent activity console.

## Chat Is Text-Only
The chat box is for natural language input and Lead Agent text output. It must not contain clickable action buttons, approval cards, decision controls, or embedded workflow menus.

Good chat behavior:

```text
I drafted the extraction schema. Review it in the canvas and approve or revise it there.
```

```text
DownloadAgent could not retrieve 8 subscribed papers. I will route them through the web-search fallback during extraction unless you adjust retrieval settings in the canvas.
```

Bad chat behavior:

```text
[Approve Schema] [Regenerate] [Preview JSON]
```

Those controls belong in the canvas.

## Lead Agent Visibility
The Lead Agent may explain progress in chat, but agent/stage status should not be surfaced as primary canvas content. The canvas should show the business result, not the internal orchestration.

Acceptable chat text:

```text
CollectionAgent finished searching the approved sources and found 312 records.
```

Acceptable canvas result:

```text
312 records identified, grouped by source.
```

Avoid canvas content such as:

```text
Lead Agent is dispatching CollectionAgent.
```

Internal agent traces are useful for logs and debugging, but they should not dominate the user-facing canvas.

## Event Flow
Canvas interactions and chat text are both valid inputs to the Lead Agent, but they have different shapes.

Canvas flow:

```text
Canvas click or edit
  -> frontend emits structured intent
  -> Lead Agent handles intent
  -> Sub Agent runs if needed
  -> artifacts update
  -> canvas refreshes business state
  -> chat receives explanatory Lead Agent text
```

Chat flow:

```text
User text input
  -> Lead Agent interprets natural language
  -> if the request changes setup or stage intent, Lead Agent updates artifacts or prepares a canvas-visible change
  -> canvas reflects the resulting business state
  -> chat replies in text only
```

## Structured Intents From Canvas
Canvas controls should send structured intents to the Lead Agent. Users should not see these intent names; they are a backend contract.

Examples:

| Canvas action | Structured intent |
|---|---|
| Save Search Setup | `save_search_setup` |
| Run collection | `run_collection` |
| Approve relevance criteria | `approve_relevance_prompt` |
| Run screening | `run_screening` |
| Retry unavailable PDFs | `retry_unavailable_pdfs` |
| Approve extraction schema | `approve_extraction_schema` |
| Run extraction | `run_extraction` |
| Apply categorization | `apply_categorization` |
| Export final results | `export_results` |

## Final Result Presentation
After extraction completes, the user-facing workflow should move to **Step 5: Categorization & Analysis**. The canvas should show the final review workspace rather than treating extraction as the endpoint.

The Step 5 canvas should prioritize:

- A concise review overview with identified, screened, included, retrieved, extracted, and categorized counts.
- A categorization panel with field selection, category mode, generated/editable categories, and an Apply Categorization control.
- Category groups with counts and representative papers or extracted evidence.
- Export affordances for extraction and categorization artifacts.

The chat may explain what happened and recommend a field to categorize, but it must not contain clickable category approval controls. Those controls belong in the canvas.

The important boundary is that these intents are triggered by canvas UI controls, not by clickable controls inside chat.

## Frontend-Preserving Rule
When implementing [LEAD_AGENT_ARCHITECTURE.md](LEAD_AGENT_ARCHITECTURE.md), prefer backend and state-projection changes over frontend redesign. The existing frontend should remain recognizable.

Allowed UI changes:

- Update chat copy so it is clearly Lead Agent text.
- Add or adjust canvas controls when a workflow decision needs a visible click target.
- Update canvas labels to reflect artifact-backed state.
- Add subtle text in chat explaining which Sub Agent completed work.
- Restore Categorization & Analysis as the fifth step when the backend projection exposes it.

Avoided UI changes:

- Replacing the current app with a chat-first interface.
- Moving workflow decisions into chat cards.
- Adding agent activity widgets to the canvas.
- Reworking the layout around agent internals.
- Making the frontend infer pipeline stage from chat text.
- Hiding final categorization behind chat-only instructions or a secondary optional panel.

## Product Invariant
ReviewPilot should feel like the same web app with a stronger internal research engine. The user should mostly notice better continuity, better stage handling, clearer recovery, and more reliable results, not a dramatically different interface.
