# Findings & Decisions

## Requirements
- Search and read the newest 2026 official OpenAI and Anthropic technical documentation on agent skills.
- Define the boundary between system prompts and agent skills.
- Decide, for ReviewPilot's 1 Lead Agent and 6 Sub Agents, which require skills and which do not.
- Recommend a concrete number of skills suitable for an AAAI 2027 architecture plan.
- Do not confuse implementation packaging with a scientific contribution; surface weak or unsupported novelty claims.
- Persist the complete Agent Skill decision in a standalone Markdown architecture document comparable to the existing design records.
- Add only the cross-references necessary to make the new document discoverable and keep related architecture boundaries synchronized.
- Provide the implementation plan in natural language rather than code.

## Research Findings
- Repository baseline: the active web path is `web_app -> LeadAgent -> WorkflowActionAdapter -> explicit SubAgentContract -> concrete agent`; it is bounded, artifact-first, and mostly sequential.
- Agent roster: LeadAgent; SearchConditionAgent; PromptAgent; CollectionAgent; FilteringAgent; DownloadAgent; ExtractionAgent. Categorization is Lead-owned and is not a seventh Sub Agent.
- OpenAI current Codex manual (fetched 2026-07-15) defines a skill as a task-specific reusable capability that packages instructions, resources, and optional scripts so a workflow can be followed reliably.
- OpenAI distinguishes persistent project guidance (`AGENTS.md`) from skills: `AGENTS.md` shapes behavior on every task and should remain small; skills package repeatable workflows and domain expertise, including richer instructions, references, assets, and scripts.
- OpenAI skills use progressive disclosure: only metadata (`name`, `description`, path) is initially visible; full `SKILL.md` loads on selection; references/scripts load only when needed. This is a material context-management difference from always-loaded system/project instructions.
- OpenAI supports explicit or implicit skill activation. Implicit matching depends on a clear, bounded description; a skill can disable implicit invocation.
- OpenAI says to keep each skill focused on one job and prefer instructions over scripts unless deterministic behavior or external tooling is required.
- OpenAI's current customization map treats project guidance, skills, MCP, and subagents as complementary layers: guidance shapes default behavior, skills define reusable workflows, MCP supplies external tools/context, and subagents delegate specialized work.
- OpenAI's current subagent guidance says subagents have their own model/tool work and context, are useful for bounded parallel work and context isolation, and add token/coordination cost. This differs from ReviewPilot's current in-process sequential class modules.
- Anthropic's current Agent Skills overview defines skills as modular, reusable, filesystem-based resources containing instructions, metadata, and optional scripts/templates. It explicitly contrasts skills with conversation-level prompts for one-off tasks.
- Anthropic says skills provide capabilities beyond prompts alone because a VM/filesystem can hold executable code and reference material; progressive disclosure loads metadata first, `SKILL.md` when triggered, and resources/code as needed.
- Anthropic's 2026 Managed Agents API makes the separation first-class in the agent configuration: `system` defines behavior/persona; `skills` supply domain-specific context with progressive disclosure; `tools` and MCP servers supply executable/external capabilities; `multiagent` declares delegation targets.
- Anthropic's 2026 multi-agent documentation says each agent has its own configuration—model, system prompt, tools, MCP servers, and skills—and runs in a context-isolated thread. Therefore skill assignment should be agent-scoped, not indiscriminately global.
- Current Anthropic API skills require a code-execution container and the `skills-2025-10-02` beta header; Managed Agents uses the `managed-agents-2026-04-01` beta surface. This is an implementation constraint, not part of the conceptual definition.
- Anthropic's current system-prompt release notes confirm that a system prompt is present at conversation start and encourages general behavior; release-note prompts for claude.ai do not apply to the Claude API.
- Important boundary nuance: skills and system prompts are not necessarily physically separate model-input channels. Anthropic explicitly says skill metadata is loaded at startup and included in the system prompt; OpenAI likewise initially exposes skill metadata in context. The defensible distinction is lifecycle and responsibility: always-on identity/invariants versus discoverable, task-conditional procedural packages.
- Anthropic's three loading levels are: metadata always loaded; `SKILL.md` procedural instructions loaded on trigger; references/resources/code accessed only as needed. Scripts can run without placing their full source into the context, with only output consuming context.
- Anthropic authoring guidance says good skills are concise, well structured, tested on real usage, tested across intended model tiers, and should use clear workflows plus validation feedback loops.
- Anthropic recommends deterministic scripts for deterministic operations, not asking the model to regenerate them; reference material can remain large because it has no context cost until read.
- Anthropic's prompt guidance supports using system prompts for role/personality while placing most task content elsewhere. Its Managed Agents schema independently encodes this by separating `system` from `skills`, tools, MCP, and user events.
- OpenAI's current use-case catalog describes skills as workflows kept available for repeated work, reinforcing that repetition/reusability—not merely agent identity—is the trigger for skill packaging.
- Anthropic's 2026 Complete Guide says to start each skill with 2-3 concrete use cases, including trigger, steps, tools, embedded domain knowledge, and expected result.
- The 2026 guide describes skills as the knowledge/recipe layer on top of tools or MCP connectivity: tools determine what the agent can do; skills teach how to use those capabilities reliably.
- The 2026 guide requires baseline comparison to prove improvement with a skill. Suggested measurable dimensions include interaction turns, failed tool/API calls, token consumption, and consistent workflow completion. For ReviewPilot/AAAI this implies skill-vs-no-skill ablations, not a packaging-only demo.
- The guide treats composability as a design principle: skills should coexist and should not assume they are the only capability loaded. This supports shared cross-agent skills rather than one monolithic skill or one redundant skill per agent.
- The guide says critical validation should preferably be programmatic because code is deterministic and language interpretation is not; ReviewPilot's existing state, artifact validation, routing, and retry logic should therefore remain code/contracts, not be migrated into skills.
- The guide warns that too many enabled skills or loading all content degrades context/performance. This favors a small selectively assigned catalog rather than attaching every skill to every agent.
- Source conflict: the January 2026 PDF makes broad portability/distribution claims, while current live Anthropic docs describe surface-specific upload/sync/sharing limitations. Use the live docs for current operational claims and the PDF for design/evaluation guidance.
- Current-code agent classification: SearchConditionAgent has genuine LLM-backed semantic search setup; PromptAgent mixes deterministic templates with LLM extraction-schema design; FilteringAgent performs LLM relevance decisions; ExtractionAgent performs LLM PDF/web-grounded extraction. These are judgment-heavy candidates for domain skills.
- CollectionAgent is an AcademicSearcher/API wrapper with deterministic platform execution; DownloadAgent wraps FastCascadePDFDownloader and constructs reports deterministically. Their default paths do not need agent skills.
- LeadAgent's workflow routing, prerequisites, artifact verification, and state transitions are explicit code and must remain code/system invariants. Its natural-language chat and Lead-owned categorization/synthesis are the only plausible skill-bearing Lead capabilities.
- A skill-per-agent design would duplicate procedure across PromptAgent/FilteringAgent and PromptAgent/ExtractionAgent, undermine composability, and create an unjustified paper-packaging artifact. Skills should align with reusable review competencies, not Python class boundaries.
- Final recommended catalog: four skills—`systematic-review-search-strategy`, `evidence-screening`, `structured-evidence-extraction`, and `evidence-synthesis-and-categorization`.
- Agent assignment: LeadAgent gets only `evidence-synthesis-and-categorization`; SearchConditionAgent gets `systematic-review-search-strategy`; PromptAgent gets `evidence-screening` and `structured-evidence-extraction`; FilteringAgent gets `evidence-screening`; ExtractionAgent gets `structured-evidence-extraction`; CollectionAgent and DownloadAgent get no skill in their default paths.
- LeadAgent does not need a general orchestration skill: identity, authority, stage order, delegation permissions, artifact verification, recovery, and output contract are always-on system/code concerns. Its optional final semantic synthesis is a valid skill because it is a conditional, judgment-heavy, reusable domain workflow.
- SearchConditionAgent needs a skill because query conceptualization, controlled-vocabulary expansion, Boolean construction, database adaptation, and sensitivity/specificity diagnostics are reusable procedural expertise with substantial references/examples.
- PromptAgent should not receive a generic “prompt-engineering” skill. For relevance-prompt generation it should load the evidence-screening method; for extraction-schema generation it should load the structured-extraction method. The generated prompt remains a run artifact, not a skill.
- FilteringAgent needs the shared screening skill because inclusion/exclusion operationalization, uncertain evidence handling, calibrated decisions, and auditable rationales are semantic methods rather than deterministic transforms.
- ExtractionAgent needs the shared extraction skill because schema-grounded field extraction, provenance, missing-value policy, confidence, source support, and PDF/web-fallback distinctions require procedural domain judgment.
- CollectionAgent does not need a skill in the default path: it executes approved queries against configured sources. Query repair belongs to SearchConditionAgent; API routing, pagination, retries, and normalization belong in code/tools.
- DownloadAgent does not need a skill in the default path: URL resolution, download cascade, retry, checksums, file validation, and report construction are deterministic code/tool responsibilities. A future LLM-assisted paywall/repository recovery branch could justify a narrowly scoped fifth skill only after it has distinct use cases and evals.
- Four-skill scope is the minimum defensible publication design. The optional fifth retrieval-recovery skill should not be claimed in the initial paper unless that branch is actually implemented and evaluated.
- Runtime gap: the current repository contains no `SKILL.md`, `.agents/skills`, `.claude/skills`, skill registry, progressive loader, or skill-ID/container integration. It uses direct OpenAI/Anthropic client SDK calls, not an agent-skills runtime.
- Consequence: adding skill folders alone would be inert for the ReviewPilot application. The implementation plan needs an agent-scoped registry, deterministic action-to-skill activation, progressive content loading, provenance/telemetry showing which skill/version was used, and a no-skill baseline mode.
- For provider-neutral publication claims, use the open Agent Skills folder format as the authored source, but do not claim identical runtime semantics across vendors. Current OpenAI and Anthropic surfaces differ in discovery, upload, distribution, and execution environment.
- Minimum evaluation matrix for AAAI: no-skill baseline vs four-skill system; per-skill removal ablation; trigger precision/recall; stage task quality; artifact validity; human correction burden; token/cost/latency; cross-domain generalization; and model/provider robustness where claimed.
- Documentation audit: `LEAD_AGENT_ARCHITECTURE.md` is the normative parent for roster, workflow, contracts, and bounded autonomy; `LEAD_AGENT_UI_UX_BOUNDARY.md` owns presentation boundaries; `SYSTEM_OVERVIEW_FIGURE_PROMPT.md` owns the publication-facing system diagram; `design/README.md` is currently only a UI index; root `README.md` has no architecture-document index.
- Cross-reference decision: create `design/AGENT_SKILL_ARCHITECTURE.md`; add reciprocal links from the Lead architecture and UI boundary; add a design-document index to `design/README.md`; add an architecture-document entry point to root `README.md`; add the Skill document as a grounding source to the system-figure prompt without inventing Skill nodes in the current figure.
- The canonical Skill document must distinguish current repository state from target architecture so future readers cannot mistake a design decision for an implemented runtime.
- The canonical Skill document should capture the official-source basis, system/skill/context/tool/code boundary, four-skill catalog, agent assignment, deterministic activation policy, progressive loading, provenance, evaluation/ablation requirements, implementation order, non-goals, and conditions for a future fifth retrieval-recovery skill.
- Final document set: new canonical `design/AGENT_SKILL_ARCHITECTURE.md`; reciprocal normative link in `design/LEAD_AGENT_ARCHITECTURE.md`; product-visibility boundary link in `design/LEAD_AGENT_UI_UX_BOUNDARY.md`; discoverability indexes in `design/README.md` and root `README.md`; figure-grounding and no-extra-Agent guard in `design/SYSTEM_OVERVIEW_FIGURE_PROMPT.md`.
- Verification result: 20 relative Markdown links checked with zero missing targets; exactly four Skill sections; exactly seven Agent assignment rows; zero trailing-whitespace findings; tracked Markdown changes pass `git diff --check`.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Evaluate skills by reusable procedural depth, references/assets/scripts, cross-task reuse, and need for progressive loading | These are the properties that can justify a skill beyond a system-role instruction. |
| System prompt owns always-on identity, authority, invariants, allowed delegation, tool boundaries, stable output contract, and failure policy | These must apply even when no skill triggers and therefore cannot safely depend on conditional loading. |
| Skill owns conditional reusable procedure, examples, references, templates, and optional deterministic helpers | This matches both vendors' progressive-disclosure and workflow-packaging definitions. |
| Per-run research question, criteria, sources, dates, and extracted evidence remain user/project context | Dynamic project facts should not be frozen into reusable skills or always-on system instructions. |
| State transitions, routing, retry, validation, security checks, and artifact writes remain code/contracts | Deterministic operations should be programmatic and testable rather than delegated to language instructions. |
| Skill activation should be deterministic from ReviewPilot action/stage in the first implementation | The current workflow already has explicit action contracts; deterministic activation avoids conflating skill-selection errors with domain-method quality and follows the project's code-over-model routing rule. |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| The current Anthropic engineering deep-dive was originally published in October 2025, although the platform docs and Managed Agents surface are current in 2026 | Treat the 2025 article as architectural background only; anchor current implementation claims in 2026 platform documentation and the 2026 complete guide. |

## Implemented Product State
- The canonical authored source is four project-local `.agents/skills/*/SKILL.md` packages; no hosted vendor Skill API is required by the Web App runtime.
- `reviewpilot_core/skill_runtime.py` enforces the seven action assignments, package integrity, semantic versions, content hashes, prompt injection, and internal provenance.
- The Skill layer is active at the existing contract boundary and does not alter the Lead Agent roster, workflow actions, artifact ownership, or UI controls.
- Collection and Download remain deterministic and intentionally have no Skill assignment.

## Resources
- Local architecture: `design/LEAD_AGENT_ARCHITECTURE.md`
- Active implementation: `agents/lead_agent.py`, `reviewpilot_core/sub_agent_contracts.py`, `reviewpilot_core/workflow_adapter.py`
- OpenAI current Codex manual snapshot fetched 2026-07-15: `/var/folders/nv/7st69fc94wlfl60y57qczy0c0000gn/T/openai-docs-cache/codex-manual.md`
- OpenAI official Build skills: https://learn.chatgpt.com/docs/build-skills.md
- OpenAI official Customization overview: https://learn.chatgpt.com/docs/customization/overview.md
- OpenAI official AGENTS.md guidance: https://learn.chatgpt.com/docs/agent-configuration/agents-md.md
- OpenAI official Subagents guidance: https://learn.chatgpt.com/docs/agent-configuration/subagents.md
- Anthropic Agent Skills overview: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- Anthropic Managed Agents definition: https://platform.claude.com/docs/en/managed-agents/agent-setup
- Anthropic Managed Agents multi-agent sessions: https://platform.claude.com/docs/en/managed-agents/multi-agent
- Anthropic skill authoring best practices: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- Anthropic 2026 system prompt release notes: https://platform.claude.com/docs/en/release-notes/system-prompts
- Anthropic 2026 Complete Guide PDF: https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf
