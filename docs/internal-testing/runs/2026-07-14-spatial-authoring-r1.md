# Spatial-authoring inner-beta run r1

## Reservation and identity

- Scenario key: `spatial_authoring`
- Project id: `inner-beta-spatial-authoring-r1`
- Run number: `1`
- Run window: `2026-07-14T08:58:45+08:00` to `2026-07-14T09:05:00+08:00`
- Starting Git commit: `284626a9b3a7bc17556b5960d0691280c4c881a8`
- Accepted candidate Git commit: `b8ab1807703700a439e29e76242e4ba98c1448db`
- Browser: Codex in-app browser
- Viewport: `1280 × 800` CSS pixels
- Application URL: `http://localhost:8000/workspace`
- Status: `PASS`

## Frozen input and setup

- Tracked provenance SHA-256: `9858dbcb5940bc54bb969fb240ede7ece6b3779f1a854150956636575fe8ff01`
- Topic: interactive AI-assisted 3D design and spatial authoring tools in VR/AR/MR
- Domain: HCI, immersive computing, and AI-assisted creative tools
- The exact tracked Boolean query was used, including AI-assisted/generative/conversational/LLM terms and 3D design/spatial authoring/VR/AR/MR/XR terms.
- Platform order and limits: `PubMed 5 → arXiv 5 → OpenAlex 5`
- Date range: `2020-01-01` to open end
- Model: `gpt-5.4-mini`
- API state and persisted Search Setup agreed on project identity, query, platform order, per-source limits, dates, and model before collection.

## Workflow disposition

| Stage | Task / elapsed | Counts and conclusion | Disposition |
| --- | --- | --- | --- |
| Collection | `af03157d33c1490ea24a949f1821489e` / 18.572 s | 5 PubMed + 5 arXiv + 5 OpenAlex; no platform errors | PASS |
| Screening | `d1d7d3ca175d434b817e5c66800aba1c` / 48.476 s | 15 initial, 15 after exact/similarity dedup, 1 included, 14 excluded by relevance | PASS |
| PDF retrieval | `c276cd5d85f5447cb61e1ebe0384fd7d` / 35.407 s | 1/1 PDF retrieved; 0 failed | PASS |
| Schema generation | `85e664058c8f4f8ca87c0f7fe49fd964` / 6.815 s | 11 domain-specific fields, including interaction modality, XR platform, assistance type, authoring task, and application context | PASS |
| Schema finalization | `52461403adc845cca96ab51c2f606691` / 0.012 s | Finalization marker persisted and Run Extraction unlocked | PASS |
| Information extraction | `7031f457f258446c9e9a89bb7a9dc3bc` / 22.020 s | 1/1 processed, 0 errors, no fallback; reported cost `$0.0129175` | PASS |
| Category suggestion | `26233c12bf114578922344c8c57c2140` / 6.361 s | The sample-size-aware cap produced exactly one broad category for one paper | PASS |
| Categorization | `6a409573d4f84a9886451eecc787e7d8` / 3.774 s | 1 row categorized in multiple mode as `AI-Enabled Interactive System Design` | PASS |

Only one of the 15 broad-search records passed strict relevance screening: `ImaginateAR: AI-Assisted In-Situ Authoring in Augmented Reality`. This low retention is a truthful outcome of the frozen scenario's deliberately broad interdisciplinary query, not a reason to relax screening or manufacture data. The one-paper result also exercised valid singular and minimum-cardinality behavior through retrieval, extraction, categorization, evidence rendering, and export.

## Defect disposition

| Defect | Severity | Evidence and root cause | Resolution | Verification |
| --- | --- | --- | --- | --- |
| `SPATIAL-R1-P2-001` one-item results rendered plural labels and pronouns | P2 | The completed canvas displayed `1 papers · 1 categories`; category confirmation displayed `1 categories`; the suggestion reply said `Review them` after generating one suggestion. Counts were correct, but all three user-facing messages used unconditional plural forms. | Commit `b8ab180` adds a shared deterministic count formatter to the frontend and a count-bound review pronoun in LeadAgent. | Red Node/Python tests failed before the fix; focused tests passed; browser reload displayed `1 paper · 1 category`, contained neither incorrect plural string, and retained all final outputs. |

## Export verification

All seven visible browser export links were present. The Search setup export was clicked through the browser with no navigation error or console message. Independent endpoint verification produced:

| Export | HTTP / media type | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Search setup | `200 application/json` | 2,378 | `06033225d38b1cc9bbbeb6283804798edf7662a553023bf1f5d4b86eed745765` |
| Relevance prompt | `200 application/json` | 5,150 | `cafa25744ee8345aa6fec8a0cbb5f7504872da63d9b8ee0934b2cdd13424e6eb` |
| Included papers | `200 application/x-ndjson` | 2,202 | `49da099764d48b0e1e745d5c63a8fa9b072022570092b6419722b2a41f6d2818` |
| Download report | `200 application/json` | 769 | `bc3d8c760e713f60030b7351a315db2b951941d02a8ed07c6f222c84c6900bcc` |
| Extraction results | `200 application/x-ndjson` | 8,846 | `165d709591d27ecb6a69fa9de811f779bf6911efdd02a3657bd331fecf1b9904` |
| Categorization mapping | `200 application/json` | 863 | `731b4511fc2661ddf30c8cc749b7efb00d526f6ce4b7c7059620f80fc0ea0172` |
| Categorized results | `200 application/x-ndjson` | 8,962 | `06a04b63bab12033defb78d1378adf9b6ccbbebdb90ba753a85bd2b96ac511a7` |

## Browser and automated quality gates

- Final UI: `1 paper · 1 category`, one populated category brief, one full-results row, and seven export links.
- Console: 0 warnings and 0 errors after the complete workflow and an actual export click.
- Responsive check: at `1280 × 800`, body width, document width, and viewport width were all 1,280 CSS pixels; no horizontal overflow.
- Empty-schema gate inherited and re-proved the HCI fix: zero fields exposed Generate Schema only; Finalize Schema appeared only after 11 fields were generated.
- Category suggestion inherited and re-proved the HCI small-sample fix: one paper yielded one reusable category rather than the former fixed 5–10 request.
- Automated verification on the accepted candidate commit: `702 tests`, `0 failures`, `0 errors`, `0 skipped`, 103.334 s.
- Known dependency warnings remained non-failing: deprecated `google.generativeai` package and Starlette `httpx` deprecation.

## Run disposition

- Product disposition: `PASS`
- Confidence: high
- Spatial-authoring example status: accepted for the inner-beta candidate.
- Remaining release work: run the joint three-example release audit and produce the final release/risk decision. The individual third-example pass does not by itself close the overall goal.
