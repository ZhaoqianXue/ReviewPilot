# Biomedicine inner-beta run r5

## Reservation and identity

- Scenario key: `biomedicine`
- Requested project id: `inner-beta-biomedicine-r5`
- Run number: `5`
- Reservation timestamp: `2026-07-13T05:50:53+08:00`
- Tested Git commit: `0ebec46b335773ab52663c753a7b9c786c72b23c`
- Failed predecessors: r1–r4
- Report: `docs/internal-testing/runs/2026-07-13-biomedicine-r5.md`
- Evidence directory: `docs/internal-testing/evidence/biomedicine-r5/`
- API-returned id: `llm-biomedicine-search`
- Status: `failed; identity mismatch before workflow execution`

## Frozen input identity

- Scenario catalog SHA-256: `04727fb8ff40482cc6c3d633f7f73cfce215d7dafb1314687a3a1acf9f6a3b63`
- Provenance manifest SHA-256: `e5ef30f47976597991141f9c8769be43e37916fdf259011126df87cbe0187be4`
- Tracked biomedicine snapshot SHA-256: `35443b4bb71fece437b44c83a9b0cb8b4657bf3de0f05dda3ddb064a30650e8c`
- Historical source SHA-256 metadata: `83c2277ba55cb6eecbbac3830421da93bc5305066037042e7b4b23caebe2da36`
- Effective open-bound cutoff: `2026-07-13T05:50:53+08:00`

## Execution evidence

- Run start: `2026-07-13T05:50:53+08:00`
- Browser: Codex in-app browser
- Viewport: `1280 × 800` CSS pixels
- Application URL: `http://127.0.0.1:5602/workspace`

## Run disposition

- Run end: `2026-07-13T05:52:30+08:00`
- Expected: the create-project request and API response both use `inner-beta-biomedicine-r5`.
- Actual: the chat-first new-review path derived and submitted `llm-biomedicine-search`; the server recorded one successful `POST /projects` for that different id.
- Safety action: aborted immediately. Collection, screening, retrieval, schema, extraction, and categorization were not started; no workflow task id exists and no external search/PDF action ran.
- Classification: execution-route mismatch discovered by the identity protocol, not counted as a product regression. The assistant-header setup button is the UI route that exposes the explicit project-name form; r6 will use it before sending chat.
- Product disposition: `INVALID / ABORTED`
- Release impact: none; this run is not eligible as confirmation evidence.
