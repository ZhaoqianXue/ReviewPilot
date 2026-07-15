# ReviewPilot Agent Skill Architecture Setting

## Status and Authority

This document is the canonical architecture and implementation record for Agent Skills in ReviewPilot. It defines the boundary between system prompts and Agent Skills, the Skill catalog, agent-to-Skill assignments, activation and loading policy, traceability requirements, and engineering change controls.

Read this document together with [LEAD_AGENT_ARCHITECTURE.md](LEAD_AGENT_ARCHITECTURE.md), which remains authoritative for the one-Lead-plus-six-Sub-Agent roster, bounded orchestration, workflow order, model policy, and artifact contracts; [AGENT_MEMORY_ARCHITECTURE.md](AGENT_MEMORY_ARCHITECTURE.md), which remains authoritative for session and cross-project memory; and [LEAD_AGENT_UI_UX_BOUNDARY.md](LEAD_AGENT_UI_UX_BOUNDARY.md), which remains authoritative for how internal Agent behavior may appear in the product. [SYSTEM_OVERVIEW_FIGURE_PROMPT.md](SYSTEM_OVERVIEW_FIGURE_PROMPT.md) remains authoritative for the current publication-facing Agent-and-workflow figure and must not reinterpret Skills as additional Agents or stages.

This architecture is implemented in the Web App runtime as of 2026-07-15. The canonical packages live under `.agents/skills/`; `reviewpilot_core/skill_runtime.py` owns loading, validation, version identity, deterministic assignment, prompt injection, and internal activation provenance; and `reviewpilot_core/sub_agent_contracts.py` activates the selected Skill at the existing workflow boundary. Adding or editing a Skill folder without preserving these runtime responsibilities does not constitute a valid integration.

## Decision Summary

ReviewPilot will initially define **four reusable systematic-review Agent Skills**, not one Skill per Agent:

1. `systematic-review-search-strategy`
2. `evidence-screening`
3. `structured-evidence-extraction`
4. `evidence-synthesis-and-categorization`

Five of the seven named Agents use at least one Skill: the Lead Agent, `SearchConditionAgent`, `PromptAgent`, `FilteringAgent`, and `ExtractionAgent`. `CollectionAgent` and `DownloadAgent` do not use a Skill in their default paths.

The four Skills represent reusable review-method competencies. They do not mirror Python class boundaries. `PromptAgent` shares the screening Skill with `FilteringAgent` and the extraction Skill with `ExtractionAgent`; it does not receive a generic prompt-engineering Skill.

## Official Technical Basis

The setting is grounded in the OpenAI and Anthropic documentation available on 2026-07-15.

OpenAI defines a Skill as a reusable task workflow that packages instructions, resources, and optional scripts. Codex discovers Skills from lightweight metadata and loads the complete instructions only after selecting a Skill. OpenAI separates persistent project guidance from reusable workflows and recommends focused Skills with clear triggers.

Anthropic defines Agent Skills as reusable, filesystem-based packages containing metadata, procedural instructions, references, templates, and optional executable code. Its progressive-disclosure model has three levels: metadata is always available, the main instructions load when the Skill is triggered, and supporting resources or code are accessed only when needed. Anthropic's 2026 Managed Agents configuration separately represents the system prompt, tools, MCP servers, Skills, and multi-agent roster.

One technical nuance must remain explicit: a Skill and a system prompt are not necessarily two physically isolated model-input channels. Skill metadata may be placed in system context so the model can discover the Skill. The defensible distinction is responsibility and loading lifecycle: core identity and invariants are always active, while complete Skill procedures and resources are conditionally loaded.

## Five-Layer Responsibility Boundary

| Layer | Responsibility | ReviewPilot examples |
|---|---|---|
| System prompt | Always-on identity, authority, behavioral invariants, delegation boundary, tool boundary, stable output contract, and failure policy | Agent role; allowed actions; no unsupported claims; required escalation; response shape |
| Agent Skill | Reusable, conditionally loaded professional method, workflow, examples, references, templates, and optional deterministic helpers | Search-strategy design; evidence-screening method; extraction methodology; semantic synthesis |
| Project or task context | Facts that change by review project or run | Research question; search terms; date range; inclusion criteria; papers; extraction fields; user-confirmed categories |
| Tools and external services | Capabilities and current external data | Academic database search; web search; PDF retrieval; model calls; filesystem access |
| Deterministic code and contracts | Routing, state transitions, retries, validation, security checks, transactions, and exact transformations | Workflow ledger; action prerequisites; JSON/JSONL validation; artifact writes; retry publication |

These layers are complementary. A Skill must not become a substitute for missing tools, current project facts, or deterministic control logic.

Agent Memory is also not a Skill. Memory supplies bounded prior context or validated configurations; a Skill supplies the reusable procedure for acting on the current task. Skill activation must not grant direct access to the long-term memory store, and remembered content must remain advisory under the authority rules in [AGENT_MEMORY_ARCHITECTURE.md](AGENT_MEMORY_ARCHITECTURE.md).

## System Prompt Boundary

The system prompt for every Agent must remain small enough to be stable and reviewable. It owns rules that must apply even when no Skill is triggered:

- Agent identity and its single primary responsibility.
- Authority boundaries between the Lead Agent, the owning Sub Agent, the user, and deterministic services.
- Allowed delegation targets and prohibited stage bypasses.
- Stable input and output contracts.
- Rules against inventing evidence, citations, source availability, counts, or completion claims.
- Required handling of missing information, ambiguity, low confidence, and failed tools.
- Privacy, source-safety, and tool-use restrictions.
- Requirements to preserve artifact provenance and defer exact validation to code.

The system prompt must not contain the full systematic-review methodology, large source-specific references, long examples, or instructions used only in one workflow stage. Those belong in the relevant Skill.

Critical safety or integrity requirements must never exist only inside a conditionally loaded Skill. If a rule must hold for every invocation, it belongs in the system prompt, deterministic code, or both.

## Agent Skill Boundary

A capability qualifies as an Agent Skill when it satisfies most of the following conditions:

- It is reused across projects, tasks, or more than one Agent.
- It contains multi-step procedural knowledge rather than a one-line role description.
- It benefits from examples, reference material, templates, or conditional branches.
- Loading the complete method on every Agent turn would waste context.
- Its behavior and output quality can be tested independently from workflow routing.
- It requires model judgment but can still include deterministic validation helpers.
- It has a clear positive trigger and a clear non-trigger boundary.

A capability does not qualify merely because an Agent performs it. API routing, pagination, download retries, checksums, state transitions, exact parsing, and artifact validation remain deterministic implementation responsibilities.

## Agent-to-Skill Assignment

| Agent | Assigned Skill or Skills | Decision |
|---|---|---|
| Lead Agent | `evidence-synthesis-and-categorization` | The Lead's orchestration remains system/code behavior; only its Lead-owned final semantic synthesis uses a Skill |
| `SearchConditionAgent` | `systematic-review-search-strategy` | Search conceptualization and query design are reusable professional judgment |
| `PromptAgent` | `evidence-screening`; `structured-evidence-extraction` | Loads the method corresponding to the artifact it is generating; has no generic prompt-engineering Skill |
| `CollectionAgent` | None in the default path | Executes approved searches through deterministic database tools and preserves source results |
| `FilteringAgent` | `evidence-screening` | Applies operationalized inclusion and exclusion criteria to study records |
| `DownloadAgent` | None in the default path | Executes deterministic full-text retrieval and records retrieval status |
| `ExtractionAgent` | `structured-evidence-extraction` | Performs schema-grounded extraction from full text or explicitly labeled web evidence |

Categorization & Analysis remains a Lead-owned capability and does not create a seventh Sub Agent. Its Skill assignment does not change the roster defined in [LEAD_AGENT_ARCHITECTURE.md](LEAD_AGENT_ARCHITECTURE.md).

## Skill 1: `systematic-review-search-strategy`

### Purpose

Turn a research question into a reproducible, reviewable search strategy without silently changing the user's approved scope.

### Primary User

`SearchConditionAgent`.

### Positive Triggers

- Create or revise Search Setup from a natural-language research question.
- Expand concepts, synonyms, abbreviations, spelling variants, or controlled vocabulary.
- Construct or diagnose Boolean queries.
- Adapt a search strategy to a supported academic source.
- Diagnose a search that is implausibly broad, narrow, or empty.

### Non-Triggers

- Executing an already approved database query.
- Paginating source results.
- Normalizing returned metadata.
- Retrying a failed source request without changing the query.

### Required Method Content

- Research-question decomposition into concept blocks.
- Synonym, abbreviation, terminology, and controlled-vocabulary expansion.
- Boolean construction and grouping rules.
- Source-specific syntax and capability differences.
- Date, language, venue, and document-type constraint handling.
- Sensitivity-versus-specificity reasoning.
- Query review and revision checklists.
- Explicit preservation of the user's approved research scope.

### Intended Resources

Source-specific query references, controlled-vocabulary guidance, worked query examples, a terminology matrix, and deterministic query-structure validation where useful.

### Quality Gate

The result must be syntactically reviewable, preserve the approved topic, expose material assumptions, and produce an artifact that `CollectionAgent` can execute without hidden query rewriting.

## Skill 2: `evidence-screening`

### Purpose

Operationalize inclusion and exclusion criteria and apply them consistently to candidate studies while minimizing unsupported exclusions.

### Primary Users

`PromptAgent` when generating the relevance criteria artifact and `FilteringAgent` when screening records.

### Positive Triggers

- Convert review scope into executable screening criteria.
- Generate or revise the relevance prompt.
- Screen a title and abstract.
- Reassess an uncertain or borderline record.
- Explain a screening decision using only available evidence.

### Non-Triggers

- Deduplication by deterministic similarity rules.
- Downloading full text.
- Extracting outcome fields from an included paper.
- Changing the approved research scope without user confirmation.

### Required Method Content

- Operational definitions for each inclusion and exclusion criterion.
- Decision order when multiple criteria apply.
- Include, exclude, and insufficient-evidence handling.
- Conservative treatment of missing abstracts or incomplete metadata.
- Standardized exclusion reasons and concise evidence-grounded rationales.
- Calibration examples, counterexamples, and borderline cases.
- Conflict and uncertainty escalation rules.

### Intended Resources

Criteria templates, calibrated screening examples, standardized reason labels, edge-case guidance, and output-validation checklists.

### Quality Gate

Every exclusion must identify an applicable approved criterion and supporting record evidence. Missing evidence must not be converted into a fabricated exclusion fact. The produced decisions and counts must still pass deterministic artifact reconciliation.

## Skill 3: `structured-evidence-extraction`

### Purpose

Design a review-specific extraction schema and extract supported field values with explicit source provenance, missingness, and confidence handling.

### Primary Users

`PromptAgent` when designing the extraction schema and `ExtractionAgent` when extracting evidence.

### Positive Triggers

- Generate or revise an extraction schema.
- Define fields, types, units, allowed values, or evidence requirements.
- Extract fields from an available full text.
- Perform the explicitly labeled web-evidence fallback for an unavailable full text.
- Reassess a failed or low-confidence extraction.

### Non-Triggers

- Downloading PDFs.
- Pretending that abstract, metadata, publisher pages, or web snippets are full text.
- Categorizing extracted values into themes.
- Filling an unsupported field by inference alone.

### Required Method Content

- Research-question-to-field decomposition.
- Field definitions, types, units, allowed values, and normalization rules.
- Evidence requirements and support thresholds per field.
- Distinction among missing, not reported, not applicable, and not confirmable.
- Full-text, metadata, and web-fallback source distinctions.
- Per-field provenance, source URL, evidence span, and confidence policy.
- Cross-field consistency checks and hallucination controls.
- Failure and partial-result handling.

### Intended Resources

Schema patterns, field-definition examples, provenance templates, extraction counterexamples, source-quality guidance, and deterministic schema/result validators.

### Quality Gate

Every populated field must be supported by the declared source. The output must preserve whether evidence came from full text or the web fallback, retain citations or URLs where required, leave unsupported values empty or explicitly unresolved, and pass deterministic schema and artifact validation.

## Skill 4: `evidence-synthesis-and-categorization`

### Purpose

Organize extracted evidence into interpretable semantic categories and produce a human-reviewable synthesis without creating paper-specific labels or overstating the evidence.

### Primary User

The Lead-owned Categorization & Analysis capability.

### Positive Triggers

- Recommend an extracted field for categorization.
- Suggest category names and definitions.
- Choose between single-label and multi-label categorization.
- Apply user-confirmed categories.
- Summarize category coverage and representative evidence.

### Non-Triggers

- Extracting fields from papers.
- Altering screening decisions.
- Replacing the user confirmation checkpoint.
- Treating generated themes as causal or statistical conclusions.

### Required Method Content

- Field-selection criteria.
- Category induction and granularity control.
- Prevention of one-paper-per-category fragmentation.
- Single-label and multi-label decision guidance.
- Coverage, overlap, empty-category, and singleton checks.
- Category definitions and representative evidence selection.
- Small-sample behavior and uncertainty handling.
- Mandatory researcher review and editable confirmation.

### Intended Resources

Category-quality rubrics, examples at different sample sizes, single-versus-multiple mode guidance, fragmentation counterexamples, and coverage-validation helpers.

### Quality Gate

Categories must be materially fewer and broader than papers when the sample permits, cover the evidence without unsupported interpretation, preserve the selected categorization mode, and remain editable before application.

## Why `CollectionAgent` Has No Default Skill

`CollectionAgent` receives an approved query and executes it against configured academic sources. Source invocation, pagination, rate-limit handling, metadata preservation, and collection artifact generation are tool and code responsibilities. It must not silently rewrite a query with an LLM.

When collection is too broad, narrow, or empty, the Lead Agent routes query revision back to `SearchConditionAgent`, which uses `systematic-review-search-strategy`, and then re-runs collection. This preserves reproducibility and ownership.

## Why `DownloadAgent` Has No Default Skill

`DownloadAgent` executes deterministic full-text retrieval, records availability, and writes the retrieval report. URL and identifier resolution, download cascades, retries, file checks, checksums, duplicate handling, and report reconciliation remain code and tool behavior.

A future `scholarly-fulltext-recovery` Skill is not part of the initial catalog. It may be proposed only if ReviewPilot adds a distinct LLM-assisted recovery workflow that judges repository versions, author manuscripts, publisher routes, or competing sources, and only after that workflow has concrete use cases, a bounded trigger, independent evaluations, and evidence that deterministic retrieval is insufficient.

## Why `PromptAgent` Has No Generic Prompt Skill

Prompt generation is an implementation mechanism, not a systematic-review competency. A generic prompt-engineering Skill would duplicate downstream methods and make it possible for the generated screening or extraction instructions to drift from the method used by the executing Agent.

For relevance-prompt generation, `PromptAgent` loads `evidence-screening`. For extraction-schema generation, it loads `structured-evidence-extraction`. The generated prompt and schema remain project artifacts; they do not become reusable Skills.

## Deterministic Activation Policy

The first implementation must activate Skills from the authoritative ReviewPilot action and stage, not from an unconstrained LLM routing decision. This isolates Skill quality from Skill-selection quality and matches the existing explicit workflow contracts.

| ReviewPilot action | Owning Agent | Skill loaded |
|---|---|---|
| Save Search Setup | `SearchConditionAgent` | `systematic-review-search-strategy` |
| Generate relevance criteria | `PromptAgent` | `evidence-screening` |
| Collect papers | `CollectionAgent` | None |
| Screen papers | `FilteringAgent` | `evidence-screening` |
| Generate extraction schema | `PromptAgent` | `structured-evidence-extraction` |
| Retrieve full texts | `DownloadAgent` | None |
| Run extraction | `ExtractionAgent` | `structured-evidence-extraction` |
| Suggest categories | Lead-owned categorization | `evidence-synthesis-and-categorization` |
| Apply categorization | Lead-owned categorization | `evidence-synthesis-and-categorization` |
| General Lead Agent conversation | Lead Agent | None unless it enters a named Skill-owned workflow |

The active action selects one method. `PromptAgent` must not load both of its assigned Skills simultaneously merely because both are available to it.

## Progressive Loading Policy

ReviewPilot's loading lifecycle has three levels:

1. **Assigned metadata:** An Agent sees only the names, descriptions, versions, and paths of Skills assigned to that Agent. It does not receive the global catalog.
2. **Triggered instructions:** When an authoritative action selects a Skill, the runtime loads that Skill's main instructions into the Agent context.
3. **On-demand resources:** References, examples, templates, and helper scripts are accessed only when required by the selected workflow branch.

The system must not concatenate all four Skill bodies into every model request. It must not expose the complete Skill catalog to deterministic Agents that have no Skill assignment.

## Composition Rules

- A Skill must work without assuming it is the only capability in the runtime.
- Shared Skills must have one canonical source, not copied variants per Agent.
- An Agent may have more than one assigned Skill, but the current action selects the active one.
- A Skill may call or reference deterministic helpers, but the helper remains testable independently of the model.
- A Skill must not change workflow authority, action prerequisites, or artifact ownership.
- A Skill must not mutate another Agent's authoritative artifacts directly.
- Skill content may guide judgment; deterministic contracts decide whether the result is accepted.

## Skill Identity, Versioning, and Provenance

Every Skill activation is written to the project-internal `.reviewpilot/skill_activations.jsonl` record. The implemented record includes:

- Skill name and semantic version.
- Content hash of the loaded Skill package.
- Owning Agent and ReviewPilot action.
- Deterministic activation reason.
- Loaded references, templates, and executed helper scripts.
- Model when known plus a nullable provider field for future adapter-level enrichment.

Token, latency, cost, terminal outcome, and artifact-validation status remain owned by the existing model usage and workflow records rather than being duplicated into the activation event. They may be correlated later by action and project if product diagnostics require it.

Skill traces are internal evaluation and debugging records. The UI should continue to show review progress and business results, not raw Skill loading events, hidden prompts, or internal reasoning traces. This follows [LEAD_AGENT_UI_UX_BOUNDARY.md](LEAD_AGENT_UI_UX_BOUNDARY.md).

## Artifact-First Integration

Agent Skills do not replace the artifact-first architecture. A Skill-guided Agent still receives bounded inputs, writes the same stage-owned artifacts, and returns through the same explicit contract. The Lead Agent still verifies required artifacts before advancing.

Skill activation metadata should be linked to the resulting stage record or evaluation trace without changing the scientific content of the artifact. A valid Skill trace cannot make an invalid artifact acceptable, and a missing Skill trace must not be silently interpreted as successful Skill use.

## Provider-Neutral Authoring and Provider-Specific Runtime

The four Skills should use the open Agent Skills folder format as their canonical authored source where practical. This supports reviewability and reduces duplicated content.

ReviewPilot must not claim that OpenAI and Anthropic execute Skills identically. Current discovery, upload, sharing, sandbox, dependency, and invocation behavior differs across their products. Provider adapters may package or deliver the same authored Skill differently, but they must preserve the same versioned method, assignment, activation reason, and evaluation identity.

Cross-provider equivalence is an empirical claim. It requires provider-specific integration tests and outcome evaluation; it cannot be inferred from a shared folder format.

## Implemented Runtime

The runtime is deliberately small and provider-neutral:

1. `SKILL_ASSIGNMENTS` maps each authoritative action to exactly one owning Agent and one Skill. There is no model-selected routing.
2. `SkillRegistry` resolves the project-local package, rejects missing, symlinked, malformed, misnamed, unversioned, or incorrectly assigned Skills, and computes the package SHA-256 identity.
3. `bind_skill_llm_query` wraps the existing provider-neutral LLM callable and appends one delimited Skill instruction block to its `system_prompt`.
4. `PromptAgent` relevance generation stores the screening Skill in the generated relevance system prompt; the later filtering wrapper detects the same marker and does not duplicate it.
5. Activation writes an internal provenance event before Agent execution. The visible Web workflow, action names, artifacts, and confirmation controls are unchanged.
6. `CollectionAgent` and `DownloadAgent` have no assignment and fail if code attempts to activate a Skill for their actions.

The implementation does not upload packages to a hosted OpenAI or Anthropic Skills API. The checked-in package is the canonical method source; existing model adapters receive the same validated instructions through their current system-prompt channel.

## Engineering Verification

The required product gates are:

- Validate all four packages with the Skill format validator.
- Test all seven positive action assignments and the Collection/Download negative boundary.
- Test assignment mismatch, malformed package, content hashing, prompt injection, and provenance.
- Run the affected Agent, workflow-adapter, Lead Agent, and Web App tests.
- Run the full repository suite with zero failures, errors, or silent skips.
- Keep routing, retries, transactions, artifact validation, and UI behavior unchanged.

Research baselines, ablations, paper experiments, and cross-provider quality claims are outside this engineering implementation. They are not prerequisites for developing or shipping the Skill-enabled Web App.

## UI/UX Boundary

Agent Skills are an internal capability layer. Users interact with Search Setup, screening criteria, extraction schemas, categorization suggestions, and review results, not with Skill packages or loading traces.

The UI may explain that ReviewPilot applied a structured review method when that explanation improves trust, but it must not add Skill selectors, Skill activity widgets, hidden-prompt viewers, or raw activation logs to the primary canvas unless a separate product decision establishes a user need. Existing user approvals and editable review checkpoints remain the control surface.

## Non-Goals

- Do not create seven Agent-named Skills to mirror the roster.
- Do not create a generic Lead orchestration Skill.
- Do not create a generic prompt-engineering Skill for `PromptAgent`.
- Do not move routing, stage order, retry, transaction, or artifact validation into Skill prose.
- Do not assign Skills to `CollectionAgent` or `DownloadAgent` merely to make every Agent appear skill-enabled.
- Do not introduce implicit LLM Skill routing in the initial implementation.
- Do not expose internal Skill traces as primary UI content.
- Do not claim a fifth retrieval-recovery Skill before that workflow and its evaluation exist.

## Change Control

Changes to the four-Skill catalog, agent assignments, deterministic activation table, or runtime contract require an explicit architecture update to this document and a consistency review against [LEAD_AGENT_ARCHITECTURE.md](LEAD_AGENT_ARCHITECTURE.md).

Promoting Categorization & Analysis into a seventh Sub Agent, moving query repair into `CollectionAgent`, adding LLM-assisted recovery to `DownloadAgent`, or enabling model-selected Skill routing are separate architecture decisions. They must not be introduced by silently editing a Skill description.

## References

- OpenAI, [Build skills](https://learn.chatgpt.com/docs/build-skills): reusable workflow packages, Skill structure, progressive disclosure, explicit and implicit invocation, and authoring guidance.
- OpenAI, [Customization](https://learn.chatgpt.com/docs/customization/overview): boundary among persistent guidance, Skills, MCP, and subagents.
- OpenAI, [Custom instructions with AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md): always-on project guidance and instruction layering.
- OpenAI, [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents): specialized agent contexts, delegation, and coordination cost.
- Anthropic, [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview): filesystem-based Skills, three-level progressive disclosure, Skill structure, and runtime constraints.
- Anthropic, [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices): focused workflows, descriptions and triggers, validation loops, deterministic helpers, and evaluation.
- Anthropic, [Define your agent](https://platform.claude.com/docs/en/managed-agents/agent-setup): separation of system prompt, tools, MCP servers, Skills, and multi-agent configuration.
- Anthropic, [Multi-agent sessions](https://platform.claude.com/docs/en/managed-agents/multi-agent): agent-scoped configuration and context-isolated agent threads.
- Anthropic, [The Complete Guide to Building Skills for Claude](https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf): use-case planning, composability, testing, baseline comparison, distribution, and troubleshooting.
