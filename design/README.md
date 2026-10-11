# ReviewPilot design reference

## Architecture documents

| File | Authority |
|---|---|
| [`LEAD_AGENT_ARCHITECTURE.md`](LEAD_AGENT_ARCHITECTURE.md) | One Lead Agent, four Sub Agents, and two tools; bounded workflow, model policy, contracts, and artifact-first orchestration |
| [`AGENT_SKILL_ARCHITECTURE.md`](AGENT_SKILL_ARCHITECTURE.md) | Implemented system-prompt/Skill boundary, four-Skill catalog, deterministic activation, progressive loading, provenance, and engineering gates |
| [`AGENT_MEMORY_ARCHITECTURE.md`](AGENT_MEMORY_ARCHITECTURE.md) | Implemented invisible Lead-owned Session Memory and minimal Cross-project Memory runtime, including storage, promotion, retrieval, failure policy, API, UI, and verification gates |
| [`LEAD_AGENT_UI_UX_BOUNDARY.md`](LEAD_AGENT_UI_UX_BOUNDARY.md) | Canvas/chat responsibilities and the user-visible boundary of internal Agent and Skill behavior |
| [`FULL_TEXT_FALLBACK_DESIGN.md`](FULL_TEXT_FALLBACK_DESIGN.md) | Papers the Download agent cannot obtain: manual retrieval handoff with download detection, and web-assisted extraction with evidence tiers |
