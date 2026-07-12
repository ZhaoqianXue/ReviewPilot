# Biomedicine inner-beta run r2

## Reservation and identity

- Scenario key: `biomedicine`
- Requested project id: `inner-beta-biomedicine-r2`
- Run number: `2`
- Reservation timestamp: `2026-07-13T03:54:56+08:00`
- Tested Git commit: `4d49ef6bd02cc97fff096b7a3bc85c2b7a80c057`
- Failed predecessor: `docs/internal-testing/runs/2026-07-13-biomedicine-r1.md` at commit `9e92d8b77ee04618b0515e1521254630fa103889`
- Report: `docs/internal-testing/runs/2026-07-13-biomedicine-r2.md`
- Evidence directory: `docs/internal-testing/evidence/biomedicine-r2/`
- Status: `failed; P1 source-limit integrity blocker`

## Frozen input identity

- Scenario catalog SHA-256: `04727fb8ff40482cc6c3d633f7f73cfce215d7dafb1314687a3a1acf9f6a3b63`
- Provenance manifest SHA-256: `e5ef30f47976597991141f9c8769be43e37916fdf259011126df87cbe0187be4`
- Tracked biomedicine snapshot SHA-256: `35443b4bb71fece437b44c83a9b0cb8b4657bf3de0f05dda3ddb064a30650e8c`
- Historical source SHA-256 metadata: `83c2277ba55cb6eecbbac3830421da93bc5305066037042e7b4b23caebe2da36`
- Effective open-bound cutoff: `2026-07-13T03:54:56+08:00`

## Execution evidence

- Run start: `2026-07-13T03:54:56+08:00`
- Run end: `2026-07-13T03:57:36+08:00`
- Browser: Codex in-app browser
- Viewport: `1280 × 800` CSS pixels
- Application URL: `http://127.0.0.1:5602/workspace`
- Initial health check: `GET /workspace` returned `200`
- API-returned project id: `inner-beta-biomedicine-r2`, exactly matching the reserved id
- Runtime output path: `output/inner-beta-biomedicine-r2`
- Create request count: exactly one `POST /projects`, returned `201`
- Browser console warnings/errors: none observed
- External service operations/errors: none; collection was not started

### P1 repair verification

The r1 form-submission fix passed its real-browser acceptance check. Entering `inner-beta-biomedicine-r2`, clicking another setup input, and inspecting the fresh DOM showed the project name remained intact. After all fields were filled, one click on `Create project` detached the dialog, issued exactly one create request, returned the exact reserved id, and adopted the new workspace. No double submit occurred.

### New reproduction

1. Open the repaired Search Setup dialog for a clean New Review.
2. Fill the frozen fields and set the visible `Max/source` field to `5`.
3. Capture the filled dialog and click `Create project` once.
4. Observe the created project's three source controls: PubMed, OpenAlex, and arXiv each display `10` rather than `5`.
5. Inspect the saved configuration before collection. `max_results`, `max_results_per_platform`, and every `source_limits` entry are `10`.

Expected: the global dialog value `Max/source = 5` becomes the selected sources' per-source limits and both persisted max fields equal `5`.

Actual: the dialog value is discarded because the draft retains the initial per-source map `{10,10,10}`; payload construction recomputes the maximum from that stale map and persists `10` everywhere. Continuing would double the approved external retrieval budget.

### Screenshots

- `docs/internal-testing/evidence/biomedicine-r2/01-setup-before-create.png` — exact frozen input with visible `Max/source = 5`; SHA-256 `363b5484ebfdb0b88be2b456791ae3053a85c3e06b1024f084d8e27f9d6fe355`
- `docs/internal-testing/evidence/biomedicine-r2/02-created-source-limits-mismatch.png` — exact project id but all three source controls display `10`; SHA-256 `f5f51b55e7557cf3c5acbf3e86e10ecb57fedf936cfcef9a1888c3a04d615b4b`
- `docs/internal-testing/evidence/biomedicine-r2/03-persisted-projection.json` — redacted selected fields from the saved setup; SHA-256 `ea4c9170e3cc2dc20ab22542106a58bc80ad9c6669f3e56fe10c00cf714f0c70`

Both screenshots were reviewed and contain no credentials, tokens, personal identifiers, local paths, or article full text.

### Catalog-to-persisted projection

| Projected field | Expected | Actual | Result |
| --- | --- | --- | --- |
| Requested/persisted project name and returned id | `inner-beta-biomedicine-r2` | Exact match | PASS |
| `description` | Frozen catalog description | Exact match | PASS |
| `primary_topic` | Frozen catalog topic | Exact match | PASS |
| `domain` | Frozen catalog domain | Exact match | PASS |
| `search_terms` and `search_queries[main]` | Frozen Boolean query | Exact match | PASS |
| `platforms` | Catalog order `pubmed`, `arxiv`, `openalex` | Persisted `pubmed`, `openalex`, `arxiv` | FAIL — ordering differs; semantic impact to be assessed after limit blocker |
| `max_results` and `max_results_per_platform` | `5` | `10` | FAIL |
| `source_limits` | Each selected source `5` | Each selected source `10` | FAIL |
| `date_range` | `{"start":"2023-01-01","end":""}` | Exact match | PASS |

The platform set is correct but its order differs from the frozen historical input. Because the catalog and persisted schema both define `platforms` as an ordered list, this is recorded as a separate P2 reproducibility defect rather than averaged away as set equality.

### Stage disposition

| Stage | Task id/status | Counts | UI/artifact agreement | Disposition |
| --- | --- | --- | --- | --- |
| Project creation | No workflow task | 1 project created | UI, API id, and output directory agree | PASS |
| Search Setup projection | No workflow task | 3/3 source limits incorrect | UI and artifact agree on the wrong value | FAIL |
| Collection | Not started | 0 | No task/artifact | BLOCKED |
| Screening | Not started | 0 | No task/artifact | BLOCKED |
| PDF retrieval | Not started | 0 | No task/artifact | BLOCKED |
| Schema generation/finalization | Not started | 0 | No task/artifact | BLOCKED |
| Extraction | Not started | 0 | No task/artifact | BLOCKED |
| Category suggestion/categorization | Not started | 0 | No task/artifact | BLOCKED |

Refresh recovery, duplicate-action, Keyword Add, Quick Start dismissal, and chat non-double-submit checks were not completed because the saved external-call limit violated the frozen input. No external request was made under the wrong limit.

## Finding and disposition

- Finding: `BIO-R2-P1-002 — Search Setup global max is overwritten by stale per-source defaults`
- Priority: `P1` — blocks the approved scenario and silently doubles the external retrieval budget
- Finding: `BIO-R2-P2-003 — New Review default platform order differs from the frozen historical input`
- Priority: `P2` — source membership is correct, but ordered configuration and external-operation order are not reproducible
- Product/external classification: product defect
- Reproducibility: 1/1 clean r2 creation; the before/after UI and saved JSON all agree
- Regression command: pending the child TDD plan and implementation
- Rerun identity after a fix: `inner-beta-biomedicine-r3`; never reuse r2
- Release impact: formal inner beta remains blocked because the saved setup contradicts the user's visible numeric limit and the frozen ordered input
- Run disposition: `FAIL`
