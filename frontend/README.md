# ReviewPilot — faithful frontend reproduction

A pixel-faithful, **zero-build** reproduction of the "Direction C · Ledger" design
(`design/reviewpilot-ui.bundle.html`), rebuilt as clean, editable source.

```
frontend/
├── index.html   fonts (Google) + icons (Phosphor CDN) + reset/scrollbar CSS + mount point
├── data.js      ALL demo data — the only file you swap to wire the real backend
└── app.js       view-model derivation + template + state/click wiring (no framework)
```

## Run it

It's static — any of these work:

```bash
# from the repo root
python3 -m http.server 5599 --directory frontend
# then open http://localhost:5599
```

Or just double-click `frontend/index.html`. (Fonts + icons load from CDNs, so the
first paint needs an internet connection; everything else is local.)

## How it maps to the original

- The design's template DSL was expanded to vanilla JS: `<sc-for>` → `.map()`,
  `<sc-if>` → ternary, `{{ x }}` → `${...}`, `style-hover` → JS hover listeners.
- Every inline style is reproduced verbatim, so colors/spacing/type match exactly.
- Fonts: Newsreader / Hanken Grotesk / IBM Plex Mono via Google Fonts.
- Icons: Phosphor (`ph` / `ph-fill`) via `@phosphor-icons/web` CDN — same classes the design used.
- Interactivity preserved: click the workflow stepper to switch steps; Schema fields ↔
  Preview on paper tabs; chat auto-scrolls. Default state = Information Extraction · Schema fields.

## Wire it to the Python backend

`app.js` reads everything from `window.RP_DATA` (defined in `data.js`). Replace the
static literals with a fetch from your backend, keeping the same shapes:

```js
// data.js
const res = await fetch('/api/review/state');   // your Python endpoint
window.RP_DATA = await res.json();
```

You'd expose ReviewPilot's existing pipeline (`searchers/`, `extract_info.py`, `main.py`)
behind a tiny REST layer (FastAPI/Flask) returning `{ project, steps, fields, platforms,
keywords, groups, retrieved, previewFields, messages, activityByStep, quietLabels,
ctxLabels, history }`. No changes to `app.js` are needed.

## Note on Streamlit

This is a standalone frontend, not a Streamlit view — Streamlit can't reproduce this
3-column + stepper layout faithfully. Treat this as the path to replacing/augmenting the
Streamlit UI with a custom frontend, or as the exact visual contract to build against.
