# ReviewPilot Agent Memory Architecture

## Status and Authority

This document is the canonical engineering design for Agent Memory in the ReviewPilot Web App. It fixes the v1 runtime decisions, storage boundaries, ownership rules, integration points, API/UI behavior, failure policy, migration policy, tests, and implementation order.

**Implementation status:** Implemented and verified on 2026-07-15. The runtime is in `reviewpilot_core/agent_memory.py`, Lead-owned integration is in `agents/lead_agent.py`, the three private product endpoints are in `web_app.py`, the minimal user surface is in `frontend/app.js`, and focused contracts are in `tests/test_agent_memory.py`.

Read it together with [LEAD_AGENT_ARCHITECTURE.md](LEAD_AGENT_ARCHITECTURE.md), which remains authoritative for the one-Lead-plus-six-Sub-Agent roster and workflow; [AGENT_SKILL_ARCHITECTURE.md](AGENT_SKILL_ARCHITECTURE.md), which remains authoritative for reusable professional methods; and [LEAD_AGENT_UI_UX_BOUNDARY.md](LEAD_AGENT_UI_UX_BOUNDARY.md), which remains authoritative for canvas/chat interaction rules.

This is a production-development design. It does not require a research experiment, benchmark, paper claim, provider migration, or another product-decision checkpoint before implementation.

## Outcome

ReviewPilot v1 implements two Agent Memory capabilities:

1. **Session memory:** the Lead Agent remembers the relevant conversation within the current review project.
2. **Cross-project memory:** later projects can reuse validated search setups, screening profiles, extraction schemas, and categorization profiles from earlier projects.

Authoritative project artifacts are not Agent Memory. Search conditions, workflow state, screening decisions, retrieval results, extraction results, and categorization outputs remain the source of truth and always outrank remembered context.

## Fixed v1 Decisions

| Decision | v1 setting |
|---|---|
| Runtime | Keep the existing custom Python orchestration and `utils.llm.query_llm`; do not adopt the OpenAI Agents SDK solely for memory |
| Session scope | One conversation-memory scope per `project_id`; the current UI has one chat stream per project |
| Session transcript | Reuse `output/{project_id}/chat/messages.jsonl` |
| Session implementation | Lead Agent privately reads the existing project chat transcript; add no session store, state file, setting, endpoint, result field, or UI |
| Long-term store | Add one local SQLite database at `output/.agent_memory/memory.sqlite3` |
| User scope | One local user in v1; do not pretend the current app has multi-user isolation |
| Retrieval | Metadata filtering plus deterministic lexical scoring; no embedding service or vector database in v1 |
| Promotion | Promote only verified structured artifacts; never promote raw chat automatically |
| Ownership | Lead Agent owns memory retrieval and promotion; Sub Agents receive selected memory in their task input and do not write global memory directly |
| Failure behavior | Memory failure degrades memory only; the formal review workflow continues and records internal diagnostics without exposing memory internals to the frontend |
| UI | Session Memory has no surface. Settings exposes only a `Memory` toggle and `Clear memory`; `Cross-project Memory` remains an internal architecture term |
| Legacy code | Leave `utils/agent_memory.py` and `utils/memory.py` untouched but mark them as legacy/unwired; do not migrate their data in v1 |

## State and Authority Model

| Layer | Scope | Owner | Examples | Authority |
|---|---|---|---|---|
| Current request | One Lead Agent turn | User | New question, correction, requested change | Highest intent for the current turn |
| Project state | One review project | Deterministic services and artifact contracts | Workflow ledger, setup, prompts, papers, schema, results | Source of truth for facts and stage transitions |
| Session memory | One project conversation | Lead Agent | The complete project chat transcript | Advisory conversation context |
| Cross-project memory | Local ReviewPilot installation | Lead Agent memory service | Reusable validated configurations | Advisory suggestions only |
| Agent Skills | Agent/capability assignment | Skill runtime | Search, screening, extraction, synthesis methods | Procedural guidance, not remembered facts |

When two layers conflict, the Lead Agent applies this precedence:

```text
current user request
  > current validated project state
  > current session decisions and corrections
  > retrieved cross-project memory
```

A user-requested project change becomes authoritative only after the existing setup/action transaction validates and persists it.

## Current Runtime Gap

The Web App already writes chat messages to `chat/messages.jsonl`, but `LeadAgent._project_chat_prompt()` currently sends only project configuration and the newest message to the model. The UI can display conversation history while the model cannot use it. This is persistence without session memory.

The repository also contains two unused prototypes, `utils/agent_memory.py` and `utils/memory.py`. Neither participates in the production `web_app -> LeadAgent -> WorkflowActionAdapter -> SubAgentContract` path. They overlap in responsibility, disagree on the meaning of short-term memory, and do not provide the v1 authority and isolation contracts in this document.

## Runtime Architecture

```text
frontend chat or canvas action
  -> web_app
    -> LeadAgent
      -> private session context assembly (existing project chat JSONL)
      -> CrossProjectMemoryService
        -> LongTermMemoryStore (local SQLite)
        -> deterministic retriever and verified promoter
      -> WorkflowActionAdapter
        -> selected Sub Agent contract with bounded memory context
      -> artifact verification
    -> structured LeadAgentResult
  -> frontend refresh
```

Keep session assembly as a small private Lead Agent helper because it only reads the existing transcript. Add the cross-project implementation as a focused service under `reviewpilot_core`; do not expand `LeadAgent` into a SQLite storage class.

## Core Components

### Lead Agent Session Context Assembly

Responsibilities:

- Read normalized user/assistant messages from the existing project chat JSONL.
- Return every valid stored turn in file order, followed by the current user input.
- Reuse the repository's existing safe JSONL and path conventions.
- Remain private to Lead Agent prompt construction; it has no product-facing lifecycle.

### `LongTermMemoryStore`

Responsibilities:

- Initialize and migrate the SQLite schema by integer schema version.
- Insert idempotent memory items from verified structured project artifacts.
- Retrieve candidates by kind, domain, topic, status, and source project.
- Update `last_used_at` and `use_count` only after a memory is actually selected for model context.
- Clear or disable long-term memory through explicit settings operations.

### `CrossProjectMemoryService`

Responsibilities:

- Provide one interface to the Lead Agent.
- Retrieve action-relevant long-term memories and promote verified structured artifacts.
- Enforce type, size, scope, and source validation before any write.
- Return safe diagnostics instead of leaking paths or raw memory payloads into user-visible errors.
- Keep memory read/write failures separate from workflow success/failure.

### Lead Agent Context Assembly

Responsibilities:

- Compose stable Lead instructions, authoritative project state, the complete stored session transcript, selected cross-project memory, and the current input.
- Render memory as labeled advisory context, never as system instructions or formal project facts.

### `MemoryPromoter`

Responsibilities:

- Run only after the Lead Agent verifies an action's required artifacts.
- Convert supported artifacts into typed, bounded memory items through deterministic extraction.
- Never ask an LLM to decide whether arbitrary chat content should become global memory in v1.
- Write idempotently so action retries do not duplicate memory.

## Session Memory Contract

### Storage

```text
output/{project_id}/chat/
└── messages.jsonl
```

`messages.jsonl` remains the existing append-only conversation audit log. The implementation reads existing `u`/`a` records in file order and maps them to user/assistant prompt turns. It does not introduce a second transcript format or modify the frontend's visible chat-history behavior.

### Session Loading Policy

Session memory has no separate token budget, message limit, summarizer, compaction mechanism, reset boundary, or lifecycle control in v1. The project is assumed to have a small enough chat history for the selected model context window.

Only cross-project retrieval is bounded:

```text
LONG_TERM_MEMORY_ITEM_LIMIT = 5
LONG_TERM_MEMORY_CHARACTER_BUDGET = 6000
```

The Lead Agent reads every valid stored message in file order and appends the current user input exactly once. Starting another project naturally creates a different project conversation.

### Lead Agent Integration

Both general project chat and extraction-schema chat use the same private session-context helper. The current schema and current project artifacts remain separately loaded authoritative context; complete session history is not allowed to replace them.

There is no Session Memory label, control, reset operation, status indicator, endpoint, frontend payload, or user-visible failure state. A session read failure falls back to authoritative project context plus the current message and is recorded only in server-side diagnostics.

Sub Agents never receive the raw transcript. The Lead Agent may include a specific session decision or correction in a Sub Agent task packet only when it is relevant to that action.

## Cross-Project Long-Term Memory Contract

### SQLite Location and Settings

Store the database under the output root so the current local app has one predictable data boundary:

```text
output/.agent_memory/memory.sqlite3
```

Enable WAL mode, foreign keys, a finite busy timeout, explicit transactions, and schema versioning. The `.agent_memory` directory must never appear as a review project in navigation or exports.

### Memory Types

| Kind | Promoted from | Used by |
|---|---|---|
| `search_setup` | Verified `save-search-setup` result | Lead Agent and `SearchConditionAgent` when preparing a related project |
| `screening_profile` | Verified relevance prompt plus completed screening setup | `PromptAgent` and `FilteringAgent` |
| `extraction_schema` | User-finalized extraction schema | `PromptAgent` and `ExtractionAgent` |
| `categorization_profile` | User-applied categorization configuration | Lead-owned Categorization & Analysis |

Do not store paper text, extracted paper facts, API credentials, raw tool results, hidden reasoning, full chats, absolute local paths, or unverified LLM suggestions as long-term memory.

### Data Model

```sql
CREATE TABLE memory_items (
    memory_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT '',
    memory_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    searchable_text TEXT NOT NULL,
    source_project_id TEXT NOT NULL,
    source_artifact TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    status TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT,
    use_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(kind, memory_key, source_project_id, source_revision, content_digest)
);

CREATE TABLE memory_settings (
    setting_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Allowed statuses are `active`, `superseded`, and `deleted`. Deletion may physically remove rows in the single-user local v1, but all API operations must still return a deterministic result and never expose the database path.

`source_artifact` is a project-relative logical artifact name, not an absolute path. `source_revision` is the existing setup revision, schema digest, or a deterministic artifact digest appropriate to the memory kind.

### Promotion Rules

Promotion occurs at these exact verified boundaries:

| Boundary | Memory write |
|---|---|
| `LeadAgent.save_search_setup()` after artifact verification | Upsert `search_setup` |
| `screen` after filtering artifacts and ledger completion | Upsert `screening_profile` |
| `finalize-schema` and `finalize-and-run-extraction` after finalized-schema verification | Upsert `extraction_schema` |
| `categorize` after categorization artifact verification | Upsert `categorization_profile` |

Do not promote collection counts, screening outcomes, download success, or extraction values. They are project results, not reusable memory.

Action retries must be idempotent. A changed verified artifact creates a new active memory item and may mark the previous item with the same kind, key, and source project as superseded. No memory from a different project is silently overwritten.

### Retrieval Rules

Retrieval is stage-aware and deterministic:

1. Select only enabled, active items of the allowed kind.
2. Exclude the current project by default so the project does not retrieve a duplicated copy of its own authoritative state.
3. Filter candidates by exact or empty domain first; allow topic-only fallback only when too few candidates remain.
4. Score normalized token overlap across current topic/domain and `searchable_text`.
5. Break ties by most recently updated, then stable `memory_id` order.
6. Return at most five items and at most 6,000 rendered characters.
7. Attach `memory_id`, kind, source project ID, source revision, and a short payload summary to the Lead Agent context.

Embeddings, vector stores, semantic rerankers, and background consolidation are deliberately deferred. The initial project count is small, deterministic lexical retrieval is inspectable, and it adds no external service or migration burden.

### Injection Rules

Retrieved memory must be rendered under a clearly labeled block:

```text
Advisory memory from earlier projects:
- [extraction_schema | source project: ... | revision: ...] ...

Use these items only as suggestions. Current user input and current project artifacts take precedence. Do not claim that a remembered item is part of the current project unless it is explicitly adopted and persisted.
```

The model never receives raw SQLite rows or arbitrary payload JSON. Each memory kind has a dedicated bounded renderer.

## Agent Access Matrix

| Component | Session read | Long-term read | Long-term write |
|---|---:|---:|---:|
| Lead Agent chat | Yes | Stage-relevant items | Through verified promoter only |
| `SearchConditionAgent` | No | `search_setup` | No |
| `PromptAgent` relevance path | No | `screening_profile` | No |
| `CollectionAgent` | No | No | No |
| `FilteringAgent` | No | `screening_profile` selected by Lead | No |
| `PromptAgent` extraction path | No | `extraction_schema` | No |
| `DownloadAgent` | No | No | No |
| `ExtractionAgent` | No | `extraction_schema` selected by Lead | No |
| Lead-owned categorization | No raw transcript | `categorization_profile` | No direct write |
| Memory promoter | No model context | No retrieval | Yes, after artifact verification |

Memory is data, not an Agent Skill. Skills define how an Agent performs a reusable method; memory supplies prior validated configurations or examples to that method. Loading a Skill must never grant direct access to the long-term store.

## Web API and UI

### API

Add three local endpoints. None concerns session memory:

| Method and path | Behavior |
|---|---|
| `GET /memory/settings` | Return only whether cross-project memory is enabled |
| `PUT /memory/settings` | Enable or disable cross-project retrieval and promotion; existing rows remain stored |
| `DELETE /memory` | Clear all cross-project memory after the frontend confirmation |

`GET /memory/settings` and a successful `PUT /memory/settings` return only:

```json
{
  "cross_project_memory_enabled": true
}
```

`PUT /memory/settings` accepts the same single boolean field. A successful `DELETE /memory` returns only `{"cleared": true}` and does not change the enabled setting.

The API never lists memory items, counts, kinds, source projects, retrieval scores, internal identifiers, revisions, payloads, database paths, or session state.

### Minimal Settings Surface

Add these controls to the existing Settings surface; do not create a dedicated Memory page, dashboard, or management dialog:

```text
Memory    [On/Off]

Reuse validated memory from previous projects.

Clear memory
```

The user-facing product term is always `Memory`. `Cross-project Memory` is reserved for architecture, code, API, logs, and tests where it must be distinguished from invisible Session Memory.

The toggle defaults to on. Turning it off disables both retrieval and new promotion without deleting stored rows. `Clear memory` uses one native confirmation, clears all stored cross-project memory, and leaves the current toggle value unchanged. There is no item browser, count, category, source-project display, per-item action, health status, or advanced settings.

Do not use the term `Session Memory` anywhere in the frontend. Do not add memory widgets to the workflow canvas, Agent activity feeds, chat cards, or project pages. During normal work, cross-project-memory failure falls back to no retrieved memory and is not rendered as workflow content. If a direct Settings update or clear request fails, show only the existing generic operation-failed treatment.

## Internal Diagnostics Boundary

Do not add memory fields to `LeadAgentResult`, Web API workflow results, frontend state, or DOM. Session message counts, retrieved memory IDs, retrieval scores, promotion events, and degraded flags remain internal. The memory service and Lead Agent log stable symbolic diagnostic codes such as `session_read_failed`, `long_term_read_failed`, and `promotion_failed`; exception strings, raw payloads, and local paths never enter user-visible responses.

## Failure and Concurrency Policy

- A session read failure falls back to current project context plus the current message.
- A long-term read failure returns no long-term items.
- A promotion failure does not roll back a successfully verified workflow artifact.
- Every degraded path logs a stable diagnostic code and the underlying exception internally without changing the public Lead Agent result contract.
- SQLite writes use explicit transactions and are short; model calls never occur inside a database transaction.
- The local single-process v1 uses SQLite WAL plus busy timeout for concurrent chat/action access.
- Clearing memory is serialized with promotion. A clear that wins the lock leaves the store empty; an already-running verified action may promote only after the clear transaction finishes.

## Data Safety

- Cap every stored string and JSON payload before writing.
- Reject non-finite numbers, non-object payloads, unsupported kinds, and unknown statuses.
- Store only project-relative logical artifact identifiers.
- Reuse `safe_text` checks before rendering selected cross-project memory into a model prompt.
- Never store secrets, API keys, environment variables, full PDFs, full paper text, or hidden reasoning.
- Do not deserialize arbitrary Python objects; memory payloads are strict JSON.
- Do not allow memory content to introduce system/developer instructions. Render it as quoted advisory data under a fixed Lead Agent instruction.

## Migration and Legacy Policy

There is no automatic import from `memory/runs`, `memory/preferences.json`, `utils/agent_memory.py`, `utils/memory.py`, or `~/.reviewpilot/user_memory.db` in v1. Their schemas and provenance are not reliable enough for silent promotion.

Add a module-level legacy notice to both unused utility modules and a source contract test proving the production Lead Agent imports only `reviewpilot_core.agent_memory`. Do not delete legacy files in the memory implementation change; deletion is a separate cleanup after the new runtime is stable.

Existing projects require no bulk migration. Session context treats existing chat messages as ordered by file position, and the first successful supported action can promote a new cross-project item from the project's current verified artifact.

## One-Turn Implementation Plan

The implementation is intentionally ordered so one coding Agent can complete it without product questions or intermediate approvals.

### Step 1 — Baseline and Contracts

Read the immediate callers and run the focused baseline:

```text
tests/test_lead_agent.py
tests/test_web_app.py
tests/test_frontend_contract.py
tests/test_frontend_behavior.py
tests/test_workflow_adapter.py
tests/test_atomic_files.py
```

Add failing tests for private session assembly, cross-project storage/promotion/retrieval, degraded behavior, the three endpoints, and minimal Settings wiring before production changes.

### Step 2 — Memory Core

Create:

```text
reviewpilot_core/agent_memory.py
tests/test_agent_memory.py
```

Implement policy constants, strict JSON validation, `LongTermMemoryStore`, `CrossProjectMemoryService`, deterministic retrieval, verified promotion, internal diagnostics, and SQLite initialization. Do not implement session storage or lifecycle management in this module.

Exit gate: all `test_agent_memory` tests pass, including malformed files, symlinks, idempotency, conflicts, disabled memory, clear, concurrency smoke tests, and path-free diagnostics.

### Step 3 — Lead Agent Session Integration

Modify `agents/lead_agent.py` so general chat and schema chat share one private helper that reads the existing project transcript and assembles it into model context. Preserve the current LLM JSON response contracts and existing visible chat behavior.

Exit gate: consecutive chat turns prove each model input contains every valid earlier project-chat turn in file order plus the current user input exactly once. Source and behavior tests prove there is no session state file, endpoint, control, label, result field, or DOM content.

### Step 4 — Cross-Project Integration

Instantiate the memory service in `LeadAgent`, retrieve allowed kinds before supported actions, pass bounded `memory_context` through explicit Sub-Agent contracts, and promote only after existing artifact verification succeeds.

Modify only the relevant contracts and Agents. Collection and Download paths remain memory-free.

Exit gate: project B receives an eligible memory from project A; project A does not retrieve its own duplicated item; current project artifacts override conflicting memory; failed actions do not promote; retries are idempotent.

### Step 5 — API and Settings UI

Add the three routes and handlers in `web_app.py`. Wire the existing Settings row in `frontend/app.js` to the single `Memory` toggle and `Clear memory` action, then add focused behavior/source-contract tests.

Exit gate: enable/disable and confirmed clear work without page reload; GET returns only `cross_project_memory_enabled`; no item list, counts, kinds, source projects, memory identifiers, session controls, absolute paths, or raw payloads appear in responses or DOM; ordinary workflow layout remains unchanged.

### Step 6 — Legacy Marking and Documentation Alignment

Mark the two unused utility modules as legacy/unwired without deleting them. Update README architecture summaries only where the runtime behavior changed. Keep this design document as the canonical memory contract.

### Step 7 — Verification

Run in this order:

1. `tests/test_agent_memory.py`
2. Lead Agent, workflow adapter, Web API, frontend contract, and frontend behavior tests
3. Architecture cleanup tests
4. Full test suite with zero failures, errors, and skips
5. `git diff --check`
6. One automated two-project integration flow proves that project A promotes Cross-project Memory and project B receives it through the existing Lead/Sub-Agent contract; one isolated browser smoke flow proves that the toggle disables and re-enables memory, confirmed clear removes stored rows, Session Memory and internal Cross-project terminology have no visible surface, and the console remains clean

The coding turn is complete only when all six checks pass or a concrete blocker is reported with the exact skipped work. No step requires user confirmation unless implementation discovers a material conflict with concurrent uncommitted changes in the same lines.

## Expected File Scope

| File | Change |
|---|---|
| `reviewpilot_core/agent_memory.py` | New accepted memory runtime |
| `agents/lead_agent.py` | Private transcript assembly, cross-project retrieval, verified promotion, internal diagnostics |
| `reviewpilot_core/sub_agent_contracts.py` | Pass bounded memory context only to eligible Agents |
| Eligible Agent modules | Consume typed advisory context without changing output contracts |
| `web_app.py` | Cross-project setting read/write and clear endpoints |
| `frontend/app.js` | One `Memory` toggle and one `Clear memory` action in Settings |
| `utils/agent_memory.py` | Legacy notice only |
| `utils/memory.py` | Legacy notice only |
| Tests | New memory tests plus focused integration and UI contracts |
| Design/README files | Cross-links and implemented-status updates |

## Completion Criteria

Agent Memory is implemented only when all of the following are true:

- The second Lead Agent turn uses relevant context from the first turn.
- Every valid stored project-chat message enters Lead Agent context in file order without altering the chat transcript.
- Session Memory has no state file, setting, endpoint, result field, label, control, status, or other frontend representation.
- At least the four structured cross-project memory kinds can be promoted and retrieved.
- Retrieved memory is stage-scoped, bounded, provenance-bearing, and advisory.
- Current project artifacts always win conflicts.
- Sub Agents cannot read or write the global store directly.
- Memory failure never corrupts or falsely advances workflow state.
- Settings exposes only one `Memory` toggle and one confirmed `Clear memory` action.
- The settings API returns only the cross-project enabled boolean and never exposes stored-memory metadata.
- Legacy prototypes are not imported by production code.
- Focused and full tests pass with no skips, and the browser smoke flow is clean.

## Official Technical Basis

- OpenAI, [Agents SDK Sessions](https://openai.github.io/openai-agents-python/sessions/): session-scoped history, bounded retrieval, merge control, and persistence backends.
- OpenAI, [Agents SDK Agent memory](https://openai.github.io/openai-agents-python/sandbox/memory/): separation of conversational sessions from distilled cross-run memory, progressive disclosure, consolidation, staleness, and isolation.
- Anthropic, [Memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool): client-controlled persistent storage, just-in-time retrieval, lifecycle limits, and path-scoped operations.
