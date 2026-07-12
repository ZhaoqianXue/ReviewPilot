# Biomedicine inner-beta run r4

## Reservation and identity

- Scenario key: `biomedicine`
- Requested project id: `inner-beta-biomedicine-r4`
- Run number: `4`
- Reservation timestamp: `2026-07-13T04:51:30+08:00`
- Tested Git commit: `ab37dc6cb5ba85b1116065e79a9b40484923e859`
- Failed predecessors: r1 `9e92d8b`; r2 `4d49ef6`; r3 `4ea0b6b`
- Report: `docs/internal-testing/runs/2026-07-13-biomedicine-r4.md`
- Evidence directory: `docs/internal-testing/evidence/biomedicine-r4/`
- Status: `failed; P2 category-suggestion routing blocker`

## Frozen input identity

- Scenario catalog SHA-256: `04727fb8ff40482cc6c3d633f7f73cfce215d7dafb1314687a3a1acf9f6a3b63`
- Provenance manifest SHA-256: `e5ef30f47976597991141f9c8769be43e37916fdf259011126df87cbe0187be4`
- Tracked biomedicine snapshot SHA-256: `35443b4bb71fece437b44c83a9b0cb8b4657bf3de0f05dda3ddb064a30650e8c`
- Historical source SHA-256 metadata: `83c2277ba55cb6eecbbac3830421da93bc5305066037042e7b4b23caebe2da36`
- Effective open-bound cutoff: `2026-07-13T04:51:30+08:00`

## Execution evidence

- Run start: `2026-07-13T04:51:30+08:00`
- Run end: `2026-07-13T05:00:33+08:00`
- Browser: Codex in-app browser
- Viewport: `1280 × 800` CSS pixels
- Application URL: `http://127.0.0.1:5602/workspace`
- Tested project id: `inner-beta-biomedicine-r4`, exact API match
- External failures: none reported

## Configuration and workflow disposition

The catalog projection passed exactly: project id, description/topic/domain/query, ordered platforms `pubmed/arxiv/openalex`, both max fields `5`, every source limit `5`, and date range `2023-01-01` to open upper bound.

| Stage | Task / elapsed | Counts and conclusion | Disposition |
| --- | --- | --- | --- |
| Collection | `7c5625fe73764d58af4d7195ee2fa97f` / 19.789 s | 5 PubMed + 5 arXiv + 5 OpenAlex; no errors | PASS |
| Duplicate collection | Same active task / HTTP 409 | No second task | PASS |
| Screening | `bf152ba976cc433bbbc46a152903ceae` / 38.798 s | 15 initial, 14 after exact dedup, 1 included, 13 excluded | PASS |
| Refresh recovery | Same screening task | Reload showed running action at 00:18, then completion | PASS |
| PDF retrieval | `f0aadf1300e247b0905acabbda64c6e4` / 26.041 s | 1/1 downloaded, 0 failed | PASS |
| Schema generation | `dd92dc45d00b45c58efdeddceeede007` / 5.766 s | 10 fields; UI/reply/next_actions agree on Finalize Schema | PASS |
| Schema finalization | `4c192aff21aa4a8db69f670e259d0c57` / 0.011 s | Marker present; UI/reply/next_actions agree on Information Extraction | PASS |
| Extraction | `2e0aef9c91a14425a62f85a2556bbadc` / 32.486 s | 1 PDF processed, 0 errors, 0 fallback, cost `$0.00566625` | PASS |
| Category suggestion | `354dc793d8f048e4befc78099e43cdb8` / 7.479 s | 9 suggestions for `methods` | FAIL — assistant says complete/no next action while UI requires confirm/apply |
| Apply categorization/export | Not started | Blocked by the recorded routing defect | BLOCKED |

Artifacts through extraction exist under the r4 project: search conditions, three collection JSONL files and summary, screening outputs, one PDF and download report, schema draft/current/finalized marker, extraction prompt, and one-row extraction results. Final categorized outputs and export are absent because the run stopped before Apply Categorization.

## Findings

### BIO-R4-P2-005 — category suggestions are announced as completed categorization

- Expected: after suggestions are generated, the assistant and `next_actions` direct the user to review, Confirm Categories, and Apply Categorization; only the applied categorization may report completion/no next action.
- Actual: the UI displays nine editable suggestions plus `Confirm Categories`, while the task/persisted assistant reply says `Categorization completed ... No next canvas action is required.` and `next_actions` is empty.
- Priority: `P2`; the main UI remains actionable, but the assistant tells internal users to stop before results are applied.
- Classification: product defect, not an external service failure.
- Reproduction: 1/1 real suggestion action.
- Rerun id: `inner-beta-biomedicine-r5`; r4 will not be reused.

### BIO-R4-P3-006 — extraction reply exposes a local absolute path

The extraction assistant reply includes the local username/worktree/output path. This is unnecessary implementation detail and conflicts with the evidence protocol's personal-path redaction rule. The underlying task artifacts may retain machine paths for execution, but user-facing text must use a neutral artifact label. The unredacted category-conflict screenshot was intentionally omitted; evidence is the sanitized task summary.

## Evidence

- `01-setup-before-create.png` — SHA-256 `4f500b5643a39c5d983968b65f4f8a7f816f9d3a57e0db7e37f0b0d136229bad`
- `02-screening-resumed.png` — SHA-256 `8774312a2cdb52e9c640acba9286c7cf1161e9f098abd8017c4a39b4d1721b86`
- `03-schema-finalize-guidance.png` — SHA-256 `a0fdc84ee8b146f8f9ea45dae8a5e62e28376fec4b691da7f6130bb6342a7040`
- `05-run-summary.json` — sanitized configuration/task evidence; SHA-256 `c49eec2e6b07dce017023dd16985023a5689d590be9eda699de4b29112d039f1`

Screenshots were reviewed and contain no credential, personal identifier, local path, or article full text. The category conflict is represented by sanitized structured evidence because its live activity panel also exposed the separate absolute-path defect.

## Run disposition

- Product disposition: `FAIL`
- Release impact: formal inner beta remains blocked until category-suggestion routing is distinct from applied categorization and user-facing replies omit local absolute paths.
- Regression command: pending the child TDD implementation.
