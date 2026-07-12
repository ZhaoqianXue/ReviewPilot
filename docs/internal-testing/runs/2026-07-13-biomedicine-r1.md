# Biomedicine inner-beta run r1

## Reservation and identity

- Scenario key: `biomedicine`
- Requested project id: `inner-beta-biomedicine-r1`
- Run number: `1`
- Reservation timestamp: `2026-07-13T03:30:18+08:00`
- Tested Git commit: `9e92d8b77ee04618b0515e1521254630fa103889`
- Report: `docs/internal-testing/runs/2026-07-13-biomedicine-r1.md`
- Evidence directory: `docs/internal-testing/evidence/biomedicine-r1/`
- Status: `failed; P1 project-creation blocker`

## Frozen input identity

- Scenario catalog SHA-256: `04727fb8ff40482cc6c3d633f7f73cfce215d7dafb1314687a3a1acf9f6a3b63`
- Provenance manifest SHA-256: `e5ef30f47976597991141f9c8769be43e37916fdf259011126df87cbe0187be4`
- Tracked biomedicine snapshot SHA-256: `35443b4bb71fece437b44c83a9b0cb8b4657bf3de0f05dda3ddb064a30650e8c`
- Historical source SHA-256 metadata: `83c2277ba55cb6eecbbac3830421da93bc5305066037042e7b4b23caebe2da36`
- Actual query: `(biomedicine OR biomedical OR "life sciences" OR healthcare OR "health care" OR biomed* OR "electronic health record" OR EHR) AND (LLM OR "large language model" OR "large language modelling" OR "foundation model" OR GPT OR ChatGPT OR Claude OR Gemini OR Qwen OR DeepSeek)`
- Platforms: `pubmed`, `arxiv`, `openalex`
- Per-source limits: `pubmed=5`, `arxiv=5`, `openalex=5`
- Date range: `2023-01-01` through an open upper bound
- Effective open-bound cutoff: `2026-07-13T03:30:18+08:00`

## Execution evidence

- Run start: `2026-07-13T03:30:18+08:00`
- Run end: `2026-07-13T03:33:49+08:00`
- Browser: Codex in-app browser
- Viewport: `1280 × 800` CSS pixels
- Application URL: `http://127.0.0.1:5602/workspace`
- Initial health check: `GET /workspace` returned `200`
- API-returned project id: absent; no create-project request reached the server
- Runtime output path: expected `output/inner-beta-biomedicine-r1`, absent
- Browser console warnings/errors: none
- External service operations/errors: none; the failure occurred before any external search or LLM workflow call

### Reproduction

1. Open a clean New Review and open Search Setup.
2. Fill every frozen biomedicine field, including project name `inner-beta-biomedicine-r1`, maximum per source `5`, start date `2023-01-01`, and an empty end date.
3. Visually confirm the filled dialog, then click `Create project` once.
4. Observe that the same dialog remains open but every field returns to its default/blank value; the workspace remains `Untitled review`.
5. Confirm that the server received no `POST /projects` and that `output/inner-beta-biomedicine-r1` was not created.

Expected: the form submits exactly once, the API returns the exact requested id, the workspace adopts the new project, and the saved setup can be checked against the catalog projection.

Actual: the click repaints the new-project state before form submission, discarding entered values without an error. Project creation never begins.

### Screenshots

- `docs/internal-testing/evidence/biomedicine-r1/01-setup-before-submit.png` — filled dialog immediately before the click; SHA-256 `ca71f376a9b7677bfa48077e425139bf7d91e771d1393f644dae710cc15c9cac`
- `docs/internal-testing/evidence/biomedicine-r1/02-setup-after-submit-attempt.png` — reset dialog and unchanged Untitled workspace after the click; SHA-256 `cb56da15fbf28ca71c2de771c7f4a50625472b68b479991a297942364a03d21a`

Both screenshots were reviewed. They contain the frozen public scenario input but no credentials, tokens, personal identifiers, local user paths, or full-text article content.

### Catalog-to-persisted projection

| Projected field | Expected | Actual | Result |
| --- | --- | --- | --- |
| Requested/persisted project name and returned id | `inner-beta-biomedicine-r1` | No project or API response | FAIL |
| `description` | Frozen catalog description | No persisted file | BLOCKED |
| `primary_topic` | Frozen catalog topic | No persisted file | BLOCKED |
| `domain` | Frozen catalog domain | No persisted file | BLOCKED |
| `search_terms` and `search_queries[main]` | Frozen Boolean query | No persisted file | BLOCKED |
| `platforms` | `pubmed`, `arxiv`, `openalex` | No persisted file | BLOCKED |
| `max_results` and `max_results_per_platform` | `5` | No persisted file | BLOCKED |
| `source_limits` | Each selected source `5` | No persisted file | BLOCKED |
| `date_range` | `{"start":"2023-01-01","end":""}` | No persisted file | BLOCKED |

### Stage disposition

| Stage | Task id/status | Counts | UI/artifact agreement | Disposition |
| --- | --- | --- | --- | --- |
| Search Setup / project creation | No task created | 0 projects created; 1 submission attempt | UI remained Untitled and output was absent | FAIL |
| Collection | Not started | 0 | No task/artifact | BLOCKED |
| Screening | Not started | 0 | No task/artifact | BLOCKED |
| PDF retrieval | Not started | 0 | No task/artifact | BLOCKED |
| Schema generation/finalization | Not started | 0 | No task/artifact | BLOCKED |
| Extraction | Not started | 0 | No task/artifact | BLOCKED |
| Category suggestion/categorization | Not started | 0 | No task/artifact | BLOCKED |

Refresh recovery and duplicate-action checks were not run because no project/action could be created. No stage is inferred from file existence and no external failure is mislabeled as product success.

## Finding and disposition

- Finding: `BIO-R1-P1-001 — Search Setup submit is preempted by root click repaint`
- Priority: `P1` — blocks every scenario at project creation, with silent loss of user-entered setup data
- Product/external classification: product defect
- Reproducibility: 2/2 observed attempts in the same clean browser session; the second attempt has before/after screenshots
- Regression command: pending the child TDD plan and implementation
- Rerun identity after a fix: `inner-beta-biomedicine-r2`; never reuse this failed `r1`
- Release impact: formal inner beta is blocked because a user cannot create a configured review through visible UI controls
- Run disposition: `FAIL`
