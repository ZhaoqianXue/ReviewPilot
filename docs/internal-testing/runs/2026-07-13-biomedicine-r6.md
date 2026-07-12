# Biomedicine inner-beta run r6

## Reservation and identity

- Scenario key: `biomedicine`
- Requested project id: `inner-beta-biomedicine-r6`
- Run number: `6`
- Reservation timestamp: `2026-07-13T05:52:30+08:00`
- Tested Git commit: `0ebec46b335773ab52663c753a7b9c786c72b23c`
- Failed/invalid predecessors: r1–r5
- Report: `docs/internal-testing/runs/2026-07-13-biomedicine-r6.md`
- Evidence directory: `docs/internal-testing/evidence/biomedicine-r6/`
- API-returned id: `inner-beta-biomedicine-r6`
- Status: `failed; P1 export-access blocker after successful core workflow`

## Frozen input identity

- Scenario catalog SHA-256: `04727fb8ff40482cc6c3d633f7f73cfce215d7dafb1314687a3a1acf9f6a3b63`
- Provenance manifest SHA-256: `e5ef30f47976597991141f9c8769be43e37916fdf259011126df87cbe0187be4`
- Tracked biomedicine snapshot SHA-256: `35443b4bb71fece437b44c83a9b0cb8b4657bf3de0f05dda3ddb064a30650e8c`
- Historical source SHA-256 metadata: `83c2277ba55cb6eecbbac3830421da93bc5305066037042e7b4b23caebe2da36`
- Effective open-bound cutoff: `2026-07-13T05:52:30+08:00`

## Execution evidence

- Run start: `2026-07-13T05:52:30+08:00`
- Browser: Codex in-app browser
- Viewport: `1280 × 800` CSS pixels
- Application URL: `http://127.0.0.1:5602/workspace`

- Run end: `2026-07-13T06:02:13+08:00`
- Exact project id match: PASS
- Catalog-to-persisted projection: PASS for every required field; no unexpected persisted keys
- External failures: none reported
- User-visible absolute local path after extraction: absent

| Stage | Task / elapsed | Counts and conclusion | Disposition |
| --- | --- | --- | --- |
| Collection | `ec061523c2e947a28c2f6ce4eb9501c4` / 19.300 s | 5 PubMed + 5 arXiv + 5 OpenAlex; no errors | PASS |
| Duplicate collection | Same active task / HTTP 409 | No second task | PASS |
| Screening | `85b4ea17ff06406e8a101d46429f3b6b` / 27.704 s | 15 initial, 14 after exact dedup, 1 included, 13 excluded | PASS |
| Refresh recovery | Same screening task | Reload showed running at 00:07, then completion | PASS |
| PDF retrieval | `f1bea6c3f09b43c3b2a4f0148c06c75b` / 41.862 s | 1/1 downloaded, 0 failed | PASS |
| Schema generation | `f95d450ece21488d92dbc6003284aa0f` / 5.807 s | 10 fields; reply and UI require Finalize Schema | PASS |
| Schema finalization | `5bdef407dcc54e619e485ab626bd5a2d` / 0.030 s | UI/reply/next action require Information Extraction | PASS |
| Extraction | `718c847daf1e4e63a1271d84bc09afd3` / 9.035 s | 1 processed, 0 errors; no user-visible local path | PASS |
| Category suggestion | `b12008e1ac8b4386879efd1287dee347` / 3.832 s | 8 suggestions; UI and assistant require Confirm then Apply | PASS |
| Apply categorization | `563e1fc5d32b4c378790d44477865c0f` / 3.793 s | 8 categories assigned across 1 row; final analysis visible | PASS |
| Export | No UI action exists | Seven projected export artifacts all exist, but no final-canvas download control is rendered | FAIL |

## Finding

### BIO-R6-P1-007 — completed results cannot be exported from the Web App

- Expected: a completed internal user can download the projected export package without filesystem access, while server paths remain hidden and only allow-listed project artifacts are reachable.
- Actual: after `Finalize Project`, the canvas reports `Project Complete!` but exposes no Export Package or download link. The server state lists seven existing export artifacts, and the frontend intentionally omits them.
- Priority: `P1`; the scientific workflow completes and remains inspectable in the UI, but the required deliverable cannot leave the app.
- Classification: product defect, not an external-service failure.
- Reproduction: 1/1 completed r6 workflow.
- Rerun id: `inner-beta-biomedicine-r7`; r6 will not be reused after the fix.

## Evidence

- `01-setup-before-create.png` — SHA-256 `a2953d1fc66310508b9ba239658a31c432a7731d49a40ae3d160953bcca79aab`
- `05-final-result.png` — SHA-256 `3f3e63b6204325c2fa3d3f8b27139c017db1cc6bb6ac154266a19d7c6c7fd8db`
- `06-run-summary.json` — sanitized task, count, routing, privacy, and export-presence evidence

The screenshots were visually reviewed and contain no credential, personal identifier, local path, or full article text. The screening refresh was observed and verified against the same task id, but its screenshot buffer was not persisted; r7 must retain that screenshot before it can be a fully protocol-compliant confirmation run.

## Run disposition

- Core workflow disposition: `PASS`
- Formal scenario disposition: `FAIL`
- Release impact: formal inner beta remains blocked until allow-listed artifacts are downloadable from the final Web UI.
