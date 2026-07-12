# Biomedicine inner-beta run r7

## Reservation and identity

- Scenario key: `biomedicine`
- Requested project id: `inner-beta-biomedicine-r7`
- Run number: `7`
- Reservation timestamp: `2026-07-13T06:18:18+08:00`
- Tested Git commit: `f38b680fd1622434e05e11014c1dd54797ed9b67`
- Failed/invalid predecessors: r1–r6
- Report: `docs/internal-testing/runs/2026-07-13-biomedicine-r7.md`
- Evidence directory: `docs/internal-testing/evidence/biomedicine-r7/`
- API-returned id: `inner-beta-biomedicine-r7`
- Status: `passed`

## Frozen input identity

- Scenario catalog SHA-256: `04727fb8ff40482cc6c3d633f7f73cfce215d7dafb1314687a3a1acf9f6a3b63`
- Provenance manifest SHA-256: `e5ef30f47976597991141f9c8769be43e37916fdf259011126df87cbe0187be4`
- Tracked biomedicine snapshot SHA-256: `35443b4bb71fece437b44c83a9b0cb8b4657bf3de0f05dda3ddb064a30650e8c`
- Historical source SHA-256 metadata: `83c2277ba55cb6eecbbac3830421da93bc5305066037042e7b4b23caebe2da36`
- Effective open-bound cutoff: `2026-07-13T06:18:18+08:00`

## Execution evidence

- Run start: `2026-07-13T06:18:18+08:00`
- Browser: Codex in-app browser
- Viewport: `1280 × 800` CSS pixels
- Application URL: `http://127.0.0.1:5602/workspace`

- Run end: `2026-07-13T06:30:19+08:00`
- Exact project id match: PASS
- Catalog-to-persisted projection: PASS for every required field; no unexpected persisted keys
- External failures: none reported
- User-visible absolute local path after extraction: absent

## Workflow disposition

| Stage | Task / elapsed | Counts and conclusion | Disposition |
| --- | --- | --- | --- |
| Collection | `6cce97909ab642fc830b54edc6b17bc0` / 19.958 s | 5 PubMed + 5 arXiv + 5 OpenAlex; no errors | PASS |
| Duplicate collection | Same active task / HTTP 409 | No second task | PASS |
| Screening | `d3624804e0224a639754655b601cc781` / 30.338 s | 15 initial, 14 after exact dedup, 1 included, 13 excluded | PASS |
| Refresh recovery | Same screening task | Reload showed running at 00:07; screenshot retained; then completion | PASS |
| PDF retrieval | `e3f449a8020942b3b0239a364e782e14` / 37.493 s | 1/1 downloaded, 0 failed | PASS |
| Schema generation | `d493c2fd072f4fb09aa0d867d6d28e58` / 5.784 s | 10 fields; UI/reply/next action require Finalize Schema | PASS |
| Schema finalization | `67d80a7e432143169a267d74010ada80` / 0.009 s | UI/reply/next action require Information Extraction | PASS |
| Extraction | `4aabcd444f5c4207b46d6d867f74697d` / 8.921 s | 1 processed, 0 errors; no user-visible local path | PASS |
| Category suggestion | `f9d436f18a9a4d3cbc32014dd87dac29` / 2.979 s | 5 suggestions; UI and assistant require Confirm then Apply | PASS |
| Apply categorization | `fc6778a29af04f31a4dfb0ae17fc84de` / 4.354 s | 5 categories assigned across 1 row; terminal assistant route | PASS |
| Export | Browser click + seven HTTP checks | Seven existing controls; browser GET 200; downloaded Search setup SHA-256 equals source; all attachments have correct JSON/NDJSON types and filenames | PASS |

All expected artifacts exist: search setup, relevance prompt, three collected source files and summary, included/excluded/screening outputs, download report and one PDF, extraction schema/prompt/finalization marker/results, suggested categories, categorization mapping, and categorized results.

## Evidence

- `01-setup-before-create.png` — SHA-256 `c3fd87c6cd5fc9aa4a44d94a9d0257c41e9757d043830616ab890998b1528613`
- `02-screening-resumed.png` — SHA-256 `94103c639f314488e03b45b725303b91496b080fabc4e3efd8a5c4ccb614b633`
- `03-schema-finalize-guidance.png` — SHA-256 `3f1fcf6d6143ec5308c973c97987aaacb06c89be82122b50a50f2f58b4906a7b`
- `05-final-export-package.png` — SHA-256 `ace4fae5a44285ec70533799756ea068338837b4a050825c0960c7618594e86a`
- `07-run-summary.json` — sanitized configuration, task, routing, privacy, and export evidence

All retained screenshots were visually reviewed and contain no credential, personal identifier, local path, or full article text. The category-confirmation screen was verified through the live DOM snapshot and terminal task evidence; its screenshot was discarded because the browser capture contained opaque rendering artifacts.

## Run disposition

- Product disposition: `PASS`
- Release impact: the biomedicine example now satisfies its complete inner-beta workflow contract at 1280 × 800, including recovery, privacy, categorization confirmation, and user-accessible exports.
- Remaining scope: HCI and spatial-authoring examples, explicit partial/retry/downstream-invalidation behavior, second viewport, and combined release audit remain unverified.
