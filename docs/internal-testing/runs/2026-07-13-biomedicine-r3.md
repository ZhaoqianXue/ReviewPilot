# Biomedicine inner-beta run r3

## Reservation and identity

- Scenario key: `biomedicine`
- Requested project id: `inner-beta-biomedicine-r3`
- Run number: `3`
- Reservation timestamp: `2026-07-13T04:29:39+08:00`
- Tested Git commit: `4ea0b6ba2e6d5ecfd2dc254828e6f1a7f8d41ff5`
- Failed predecessors: r1 at `9e92d8b77ee04618b0515e1521254630fa103889`; r2 at `4d49ef6bd02cc97fff096b7a3bc85c2b7a80c057`
- Report: `docs/internal-testing/runs/2026-07-13-biomedicine-r3.md`
- Evidence directory: `docs/internal-testing/evidence/biomedicine-r3/`
- Status: `failed; P2 schema next-action blocker`

## Frozen input identity

- Scenario catalog SHA-256: `04727fb8ff40482cc6c3d633f7f73cfce215d7dafb1314687a3a1acf9f6a3b63`
- Provenance manifest SHA-256: `e5ef30f47976597991141f9c8769be43e37916fdf259011126df87cbe0187be4`
- Tracked biomedicine snapshot SHA-256: `35443b4bb71fece437b44c83a9b0cb8b4657bf3de0f05dda3ddb064a30650e8c`
- Historical source SHA-256 metadata: `83c2277ba55cb6eecbbac3830421da93bc5305066037042e7b4b23caebe2da36`
- Effective open-bound cutoff: `2026-07-13T04:29:39+08:00`

## Execution evidence

- Run start: `2026-07-13T04:29:39+08:00`
- Run end: `2026-07-13T04:39:08+08:00`
- Browser: Codex in-app browser
- Viewport: `1280 × 800` CSS pixels
- Application URL: `http://127.0.0.1:5602/workspace`
- Initial health check: `GET /workspace` returned `200`
- API-returned project id: `inner-beta-biomedicine-r3`, exactly matching the reservation
- Runtime output path: `output/inner-beta-biomedicine-r3`
- Browser console warnings/errors: none observed
- External failures: none; PubMed, arXiv, OpenAlex, LLM screening/schema, and the selected PDF download all completed without a reported platform error

## Repaired configuration acceptance

The visible dialog, created project controls, API state, and saved `search_conditions.json` all agreed before collection:

| Projected field | Expected | Actual | Result |
| --- | --- | --- | --- |
| Project name and returned id | `inner-beta-biomedicine-r3` | Exact match | PASS |
| Description/topic/domain | Frozen catalog values | Exact match | PASS |
| Query and `search_queries[main]` | Frozen Boolean query | Exact match | PASS |
| Platforms | `pubmed`, `arxiv`, `openalex` | Exact ordered match | PASS |
| `max_results` / `max_results_per_platform` | `5` / `5` | `5` / `5` | PASS |
| Source limits | PubMed `5`, arXiv `5`, OpenAlex `5` | Exact match | PASS |
| Date range | `2023-01-01` to open upper bound | Exact match | PASS |

An additional browser check changed source limits to `3/4/5`, opened Search Setup, and saved with the unchanged global maximum `5`; UI and persisted JSON preserved `3/4/5`. The limits were then restored and persisted as `5/5/5` before collection. This proves an unchanged global maximum does not flatten differentiated limits.

The repaired delegated-form paths also passed: setup input focus retained prior fields, Keyword Add submitted once and the temporary keyword was removed, Quick Start opened on chat focus and closed on one outside click, and one chat-button submission produced one user message plus one assistant reply (`inner-beta-biomedicine-r3`).

## Workflow evidence

| Stage | Task / elapsed | Counts | UI and artifact conclusion | Disposition |
| --- | --- | --- | --- | --- |
| Collection | `4fcdfba2d13e43c88ca93090a90b86d3` / 25.740 s | PubMed 5, arXiv 5, OpenAlex 5; total 15 | UI, task result, per-source JSONL and summary agree; no platform errors | PASS |
| Duplicate collection | Same active task / HTTP 409 | No second task | UI disabled the running control; API returned the active task and actionable detail | PASS |
| Screening | `04e659d13d344c448891b0bec7842b82` / 37.286 s | 15 initial, 14 after exact dedup, 1 included, 13 excluded | UI, task result and screening artifacts agree | PASS |
| Refresh recovery | Same screening task after reload | Running at 00:14 after reload | UI resumed the same action/task without resubmission and later adopted completion | PASS |
| PDF retrieval | `2e59ed9d15fd4e2f8f3b1be4edb517ff` / 26.390 s | 1 attempted, 1 PDF downloaded, 0 failed | UI, task result, PDF and download report agree; no metadata fallback was labeled PDF extraction | PASS |
| Schema generation | `5a1cdda9d3f64ee4a84c507306ec8f9c` / 8.224 s | 10 fields | Draft/current schema artifacts and UI field table agree | FAIL — assistant named the wrong next action |
| Schema finalization | Not started | 0 | Blocked by the recorded navigation defect | BLOCKED |
| Information extraction | Not started | 0 | Blocked | BLOCKED |
| Category suggestion/categorization | Not started | 0 | Blocked | BLOCKED |

### Artifacts

- Setup: `output/inner-beta-biomedicine-r3/search_conditions.json`
- Collection: `output/inner-beta-biomedicine-r3/collected/{pubmed,arxiv,openalex}.jsonl` and `collected/summary.json`
- Screening: `output/inner-beta-biomedicine-r3/filtered/{included_papers,excluded_papers}.jsonl` and `filtered/screening_stats.json`
- Full text: `output/inner-beta-biomedicine-r3/pdfs/opportunities_and_challenges_for_chatgpt_and_large_language_models_in_biomedicine_and_health_arxiv.pdf` and `pdfs/download_report.json`
- Schema: `output/inner-beta-biomedicine-r3/extraction/{extraction_schema_draft,extraction_schema,extraction_prompt}.json`
- Extraction results/categorization/export: absent because the run stopped before those actions

No local absolute path is copied into committed evidence. `docs/internal-testing/evidence/biomedicine-r3/08-run-summary.json` contains only the redacted projection and task evidence.

### Screenshots and hashes

- `01-setup-before-create.png` — SHA-256 `a1e8e6b97b7e93bab2f798b6b97f391950c40afb9916c62c2ba3c199904c1eaa`
- `02-created-config.png` — SHA-256 `9d95e5c92470c653680103741beda1f0bcecf2170f27780147577fe0c1db78c9`
- `04-screening-resumed-after-refresh.png` — SHA-256 `bb8614564c9f6c2121439fdb21a549150f0765fdafe89ab75a41391ed2115800`
- `05-screening-completed.png` — SHA-256 `1459c9c40b22ab9b2645350c7ffc7c48e336fc92cae72ff469088ae2215569d3`
- `06-download-completed.png` — SHA-256 `7fe69328b93f0b976e3388403ba9ae8140c79a48b63f35b50f455ed73da779fe`
- `07-schema-next-action-conflict.png` — SHA-256 `ec75b8db427460a003ead54f5577454e6e5dce16b0ccd09036a6e9aa550bd410`
- `08-run-summary.json` — SHA-256 `723de59c118c258c503b8b437b6289600dfedbdae1d6b745c7c1b2761cdd16f6`

All screenshots/artifacts were reviewed for credentials, tokens, personal identifiers, local user paths, and full-text article content. None is present in committed evidence.

## Finding and disposition

- Finding: `BIO-R3-P2-004 — generated draft schema announces Categorization instead of Finalize Schema`
- Priority: `P2` — the workflow can continue through the main UI, but the assistant directs internal users to skip two required actions and conflicts with the visible state
- Product/external classification: product defect
- Reproduction: 1/1 real schema generation; the task result, persisted chat row, assistant activity, and screenshot contain the same wrong next action
- Expected: after generating an unfinalized schema, the assistant says to review and finalize it; only after finalization may it direct the user to Information Extraction
- Actual: `Prompt extraction completed ... Next canvas action: Categorization & Analysis.` while the main UI says `Refine this draft ... finalize the schema before running extraction.`
- Regression command: pending the child TDD plan and implementation
- Clean rerun id: `inner-beta-biomedicine-r4`; never reuse r3
- Release impact: formal inner beta remains blocked because assistant guidance and deterministic workflow state disagree
- Run disposition: `FAIL`
