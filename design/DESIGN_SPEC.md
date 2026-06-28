# ReviewPilot — UI Design Spec (Direction C · Ledger)

**Visual source of truth:** `reviewpilot-ui.html` — open it in any browser. It is the exact,
interactive target (self-contained, works offline). Everything below is a quick reference;
the precise px/hex values live inline in that file's markup.

## Layout
3-column app shell, designed at 1280 × 860.
- **Sidebar : Canvas (main) : Assistant** width ratio = **1.5 : 6 : 3.5** (≈ 175 / 698 / 407 px at 1280 wide).
- Panels separated by 1px hairlines (`#e5e7eb`). No drop shadows anywhere in-app — depth comes
  from hairlines + surface-tint shifts only.

## Color
| Role | Value |
|---|---|
| Brand / action / icons / headings / active | `#1a365d` (navy) |
| Soft brand fill (active pills, chips, avatars) | `#eaf0f7` |
| Hover surface | `#eef4fb` |
| Page & card surface | `#fffefc` (warm off-white — never pure `#fff`) |
| Primary text | `#1a1a1a` · body/assistant text `#1f2937` |
| Secondary text | `#6b746c` · muted labels `#9aa39b` |
| Hairlines | `#e5e7eb` (structural), `#eef0ee` (inner) |
| Button/input borders | `#d8e2f0`, `#e0e4df` · inactive track `#e3e8ef`, `#cdd5e0` |

Secondary depth tone = the same navy at lower opacity (e.g. the logo's second page).

## Type (Google Fonts)
- **Newsreader** (300–400, serif) — display / section / project titles
- **Hanken Grotesk** (400–600) — all UI & body
- **IBM Plex Mono** (400–500) — numbers, counts, timestamps, code-ish labels
- Tight tracking, roughly -0.01 to -0.03em.

## Icons
Phosphor (`@phosphor-icons/web`), regular + fill weights.

## Components
- **Sidebar** — logo + wordmark (= Home, no separate home button); "New conversation" button;
  History list grouped by time (Today / Previous 7 days, active item navy-tinted); Settings + Help
  pinned at the bottom. Always visible (no collapse).
- **Top bar** — project title with inline meta on one row (`● Active · gpt-5-mini · Jun 18, 2026`);
  directly beneath it a **horizontal workflow stepper**: 5 nodes on a connector line
  (done = filled navy check · active = navy ring · todo = gray circle; line navy up to the current
  step, gray after), each node with a name + mono sub-count. The active step is underlined and also
  acts as a tab.
- **Canvas** — tabbed content (Schema fields / Preview on paper) over a dense data table
  (mono row numbers, type pills, required check marks).
- **Assistant** — header (logo + "ReviewPilot" + context line); message list (assistant = tinted
  bubble with avatar, user = navy bubble); reply input.

## Workflow steps
Search Setup → Paper Screening → Full-Text Retrieval → Information Extraction → Categorization.

## Implementation notes
- Styling is **100% inline** in `reviewpilot-ui.html` — read or screenshot it for exact values.
- The markup uses a small template syntax that maps cleanly to any framework:
  `<sc-for>` = repeat per item · `<sc-if>` = conditional · `{{ x }}` = data binding.
- Radii: cards/buttons ~10–16px, pills 999px.
