# Task Plan: ReviewPilot Agent Skill Architecture and Runtime

## Goal
Implement the approved four-Skill architecture in the ReviewPilot Web App with deterministic action-to-Skill activation, real prompt injection, internal provenance, unchanged workflow authority, regression coverage, and synchronized architecture documentation.

## Current Phase
Complete

## Phases

### Phase 1: Requirements & Repository Baseline
- [x] Confirm the two research questions and official-source constraint
- [x] Recover ReviewPilot's current 1 Lead + 6 Sub Agent architecture
- [x] Isolate this research plan from prior project planning files
- **Status:** complete

### Phase 2: OpenAI Official Documentation Review
- [x] Fetch and inspect the current Codex manual sections on skills, AGENTS.md, prompts, and progressive disclosure
- [x] Identify any official OpenAI API/Agents SDK material that changes the boundary
- [x] Record exact claims, dates, URLs, and limitations in findings.md
- **Status:** complete

### Phase 3: Anthropic Official Documentation Review
- [x] Search 2026 official Anthropic documentation for Agent Skills, system prompts, progressive disclosure, and multi-agent use
- [x] Fetch and read the relevant primary pages
- [x] Record exact claims, dates, URLs, and limitations in findings.md
- **Status:** complete

### Phase 4: Boundary and Agent-by-Agent Decision
- [x] Derive a vendor-neutral system-prompt/skill boundary
- [x] Apply explicit skill-worthiness criteria to Lead Agent and all six Sub Agents
- [x] Determine the minimum defensible skill catalog and reject unjustified skills
- [x] Check recommendations against current ReviewPilot contracts and artifact workflow
- **Status:** complete

### Phase 5: Delivery
- [x] Cross-check all material claims against official sources
- [x] Deliver definitions, decision matrix, recommended skill count, and publication-oriented caveats
- **Status:** complete

### Phase 6: Canonical Agent Skill Architecture Document
- [x] Audit the scope and cross-reference conventions of the existing design documents
- [x] Create one standalone canonical Agent Skill architecture document containing the complete research-backed decision
- [x] Clearly separate current repository state, target architecture, non-goals, implementation sequence, and publication evaluation requirements
- **Status:** complete

### Phase 7: Documentation Cross-References and Verification
- [x] Add minimal reciprocal references from the Lead Agent architecture and other necessary documentation indexes/boundaries
- [x] Verify relative links, terminology, 1+6 Agent roster, four-skill catalog, and five-agent/two-agent assignment counts
- [x] Record the final document set and verification result
- **Status:** complete

### Phase 8: Runtime and Skill Packages
- [x] Implement a fail-loud project-local Skill loader and deterministic assignment table
- [x] Create and validate the four canonical Skill packages
- [x] Add internal activation provenance without exposing Skill controls in the Web UI
- **Status:** complete

### Phase 9: Agent Integration
- [x] Inject the selected Skill into SearchConditionAgent, PromptAgent relevance/schema paths, FilteringAgent, ExtractionAgent, and Lead-owned categorization
- [x] Prove CollectionAgent and DownloadAgent remain Skill-free
- [x] Preserve existing contracts, artifacts, routing, retries, and UI behavior
- **Status:** complete

### Phase 10: Verification
- [x] Add intent-focused loader, mapping, prompt-injection, provenance, and negative-path tests
- [x] Run focused Agent/workflow/Web tests, then the full suite with zero silent skips
- [x] Run Skill package validation and repository consistency checks
- **Status:** complete

### Phase 11: Engineering Documentation Handoff
- [x] Rewrite the canonical document from research roadmap to implemented engineering specification
- [x] Remove no-Skill baseline and AAAI experiment requirements
- [x] Verify reciprocal links, implementation claims, and final diff
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Use only official OpenAI and Anthropic sources for vendor claims | The user explicitly requested the latest vendor technical documentation and publication planning requires auditable primary evidence. |
| Treat ReviewPilot as a bounded artifact-first orchestrator, not an open-ended swarm | This matches the implemented LeadAgent -> explicit contract -> specialized agent architecture. |
| Recommend skills only where reusable procedural knowledge or packaged resources materially exceed a stable role prompt | Prevents relabeling every agent module as a skill solely for paper packaging. |
| Define four reusable review-method skills, not seven agent-named skills | Four methodological competencies are shared across judgment agents; Collection and Download remain deterministic; a one-skill-per-class design duplicates knowledge and weakens the scientific argument. |
| Keep Lead orchestration out of a skill | Stage order, authority, delegation rules, artifact contracts, and recovery are always-on invariants and code-level control logic, not conditionally loaded expertise. |
| Do not define a generic PromptAgent skill | PromptAgent should consume the screening and extraction skills corresponding to the artifact it is generating; “prompt engineering” is an implementation mechanism rather than a systematic-review capability. |
| Use `design/AGENT_SKILL_ARCHITECTURE.md` as the canonical Skill design record | The topic is substantial enough to require an independently reviewable document, while reciprocal links prevent drift from the Lead Agent and UI/UX architecture records. |
| Use deterministic action-to-Skill activation | Routing is code-owned; the model receives method instructions but never selects workflow authority. |
| Keep Skill packages project-local under `.agents/skills` | ReviewPilot needs a provider-neutral authored source that ships with the application and is loaded by its own runtime. |
| Do not create a no-Skill baseline or AAAI experiment harness now | The current goal is product completion; research evaluation is explicitly outside this implementation slice. |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| Official OpenAI use-case result could not be opened through a dynamic search-result click | Used a narrower official-domain search and the already-fetched current Codex manual; no unsupported claim depends on the failed click. |
| Completion checker reported 0/0 because it was called with `PLAN_ID` instead of the plan-file path | Inspected the helper interface and reran it with the isolated plan's absolute `task_plan.md` path. |
| The first relative-link audit used Ruby's default US-ASCII external encoding and stopped on Unicode typography in existing Markdown | Rerun the same deterministic audit with explicit UTF-8 input/output encoding; no file content was changed by the failed read. |
| Focused pytest produced no output because repository `.venv` is x86_64 on an arm64 host | Interrupted the Rosetta-blocked process; use a native arm64 Python environment, matching the repository's previously established test procedure. |
| Search Skill wrapper bypassed the module-level default LLM patch point | Resolve the existing `agents.search_condition_agent.query_llm` entry point at contract runtime, then wrap it; preserves dependency replacement and Skill injection. |
