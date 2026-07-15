# Agent Memory Architecture Plan

## Goal

Define a production-oriented, one-turn implementation plan for adding session and cross-project Agent Memory to the current ReviewPilot Web App, without changing the one-Lead-plus-six-Sub-Agent roster or blocking on research evaluation.

## Success Criteria

- A standalone `design/AGENT_MEMORY_ARCHITECTURE.md` makes all v1 decisions needed for implementation.
- The design is linked from the Lead Agent, Agent Skill, UI/UX, system overview, and design index documents where relevant.
- The implementation plan names exact modules, data contracts, integration points, tests, and completion gates.
- The plan can be executed in one uninterrupted coding turn with no product decision checkpoints.

## Phases

| Phase | Status | Exit condition |
|---|---|---|
| 1. Restore repository context and inspect active memory/UI boundaries | complete | Existing plans, dirty worktree, memory prototypes, and production call paths are understood |
| 2. Make v1 architecture decisions | complete | Storage, ownership, retrieval, promotion, API/UI, migration, and failure policies are fixed |
| 3. Write and cross-link the design | complete | Standalone design and related design references are complete |
| 4. Verify document consistency | complete | Links, Markdown diff, and architecture terminology checks pass |
| 5. Simplify session memory by removing context management | complete | Session memory replays every stored project message; no limit, summary, token budget, or compaction remains |
| 6. Enforce invisible session and minimal cross-project UX | complete | Session memory is private transcript assembly with no separate lifecycle or surface; cross-project Settings contains only one toggle and one clear-all action |
| 7. Inspect current callers and establish implementation baseline | complete | Immediate Lead/API/frontend/contracts are understood; native ARM focused baseline is 167 passed plus 63 subtests |
| 8. Implement and test Cross-project Memory core | complete | Nine focused tests pass for SQLite settings, promotion, retrieval, validation, degradation, clear, symlink rejection, and concurrency |
| 9. Integrate invisible Session Memory and cross-project context | complete | Lead prompts use full project history; four typed kinds retrieve/promote through existing contracts without changing public results |
| 10. Implement minimal Memory API and Settings UI | complete | Three routes plus only `Memory` and `Clear memory` controls pass API/frontend contracts |
| 11. Run focused, full, and browser verification | complete | 749 tests and 501 subtests pass; static checks and isolated browser smoke complete with no failures, skips, or console errors |

## Constraints

- Preserve all unrelated and concurrent user/agent changes in the dirty worktree.
- Do not switch `.planning/.active_plan`; another task currently owns it.
- Implement the approved runtime fully in this turn; do not add features outside the approved v1 contract.
- Optimize the implementation plan for a single uninterrupted future coding turn.

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Root planning files belong to an older completed inner-beta effort and `.planning/.active_plan` belongs to an Agent Skill task | 1 | Created an isolated plan directory without changing the active-plan pointer |
| Cross-link patch failed because concurrent Agent Skill work changed the Lead Agent architecture wording | 1 | Re-read all current document heads and switched to narrow context patches without reverting concurrent changes |
| Focused baseline under repository `.venv` produced no output and remained in uninterruptible emulation state | 1 | Identified `.venv/bin/python` as x86_64; terminated only the Memory-owned process and reran with `/tmp/reviewpilot-arm64-venv`, yielding 167 passed plus 63 subtests in 2.89s |
