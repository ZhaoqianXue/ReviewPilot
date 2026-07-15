# ReviewPilot design reference

## Architecture documents

| File | Authority |
|---|---|
| [`LEAD_AGENT_ARCHITECTURE.md`](LEAD_AGENT_ARCHITECTURE.md) | One Lead Agent plus six Sub Agents, bounded workflow, model policy, contracts, and artifact-first orchestration |
| [`AGENT_SKILL_ARCHITECTURE.md`](AGENT_SKILL_ARCHITECTURE.md) | Implemented system-prompt/Skill boundary, four-Skill catalog, deterministic activation, progressive loading, provenance, and engineering gates |
| [`AGENT_MEMORY_ARCHITECTURE.md`](AGENT_MEMORY_ARCHITECTURE.md) | Implemented invisible Lead-owned Session Memory and minimal Cross-project Memory runtime, including storage, promotion, retrieval, failure policy, API, UI, and verification gates |
| [`LEAD_AGENT_UI_UX_BOUNDARY.md`](LEAD_AGENT_UI_UX_BOUNDARY.md) | Canvas/chat responsibilities and the user-visible boundary of internal Agent and Skill behavior |
| [`SYSTEM_OVERVIEW_FIGURE_PROMPT.md`](SYSTEM_OVERVIEW_FIGURE_PROMPT.md) | Publication-facing system-overview figure specification |

## UI design files

Target UI: **Direction C · Ledger**. Three files, three jobs:

| File | Size | What it is | Use for |
|---|---|---|---|
| `DESIGN_SPEC.md` | 3 KB | Plain-text spec: colors, type, layout ratio, components | **Read this first.** Fast, complete index of every design decision. |
| `reviewpilot-ui.source.html` | 300 KB | The real markup + 100% inline CSS, extracted from the bundle | **Claude Code reads this** for exact px/hex/font/grid values. Readable & grep-able. |
| `reviewpilot-ui.bundle.html` | 7.4 MB | Self-contained Claude Artifacts bundle (gzip'd JS runtime + fonts + SVGs) | **Open in a browser** to see/screenshot the live interactive target. Do NOT ask Claude Code to read this whole — one line is 7 MB. |

## Notes on the source file
- `reviewpilot-ui.source.html` uses a small template DSL — `<sc-for>` (repeat per item),
  `<sc-if>` (conditional), `{{ x }}` (data binding), wrapped in `<x-dc>` / `<helmet>`.
  Ignore the DSL when reproducing visuals; the CSS and structure are standard.
- Colors are hard-coded hex (no CSS variables), so grepping `#1a365d`, `#9aa39b`, etc. finds
  every usage. Fonts are referenced by asset-UUID, not inlined — see `DESIGN_SPEC.md` for the
  Google Fonts names (Newsreader, Hanken Grotesk, IBM Plex Mono).
- It is NOT standalone-renderable (needs the runtime + font/SVG assets from the bundle).
  For pixel truth, open `reviewpilot-ui.bundle.html` in a browser.

## Paste-into-Claude-Code prompt
> Match ReviewPilot's UI to `design/DESIGN_SPEC.md` (the spec) and
> `design/reviewpilot-ui.source.html` (exact px/hex/fonts/grid — read or grep it).
> The visual target is `design/reviewpilot-ui.bundle.html` (open in a browser to see it).
> Reproduce the layout, colors, fonts, and components, then wire the real data in.

## Reality check
ReviewPilot now uses a small custom frontend served by `web_app.py`. These files
remain the visual reference for maintaining the optimized 3-column + horizontal
stepper layout while keeping it wired to the Python backend.
