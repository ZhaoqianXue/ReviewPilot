# ReviewPilot formal inner-beta release audit

## Decision

- Decision: `GO` to start a formal, local internal beta.
- Release candidate code: `b8ab1807703700a439e29e76242e4ba98c1448db` (`Use singular labels for one-item results`).
- Evidence/report HEAD at audit start: `b4cedc7`.
- Decision timestamp: `2026-07-14T09:13:24+08:00`.
- Confidence: high for the supported local single-user workflow; unknown for multi-user, public deployment, or hostile local-filesystem use because those modes are explicitly outside this release.
- Meaning of `GO`: the version is ready to be given to the planned 3–5 internal users. It does not claim that a human cohort has already completed the beta.

## Supported release boundary

The candidate supports one internal user running ReviewPilot locally in Chrome at the approved desktop sizes. It covers project setup, three-source collection, screening, PDF retrieval and failed-only recovery, schema generation/finalization, extraction, editable category review/application, refresh recovery, and allow-listed result export. Authentication, public deployment, concurrent multi-user collaboration, distributed workers, and defense against a same-user process that bypasses ReviewPilot's project lock are not part of this release.

## Three-example acceptance

| Frozen example | Accepted run | Identified / screened / included | Retrieved / extracted / categorized | Final status |
| --- | --- | --- | --- | --- |
| LLMs in biomedicine | `inner-beta-biomedicine-r7` | 15 / 14 / 1 | 1/1 / 1 / 1 | PASS |
| LLMs in human-computer interaction | `inner-beta-hci-r3` | 15 / 14 / 7 | 7/7 / 7 / 7 | PASS |
| AI-assisted spatial authoring | `inner-beta-spatial-authoring-r1` | 15 / 15 / 1 | 1/1 / 1 / 1 | PASS |

Each accepted run was created from its frozen scenario input as a new project and was driven through the visible Web App. The detailed source identity, timestamps, task IDs, model calls, field projections, counts, artifact checks, browser checks, and per-run hashes are retained in the linked run reports:

- `docs/internal-testing/runs/2026-07-13-biomedicine-r7.md`
- `docs/internal-testing/runs/2026-07-14-hci-r3.md`
- `docs/internal-testing/runs/2026-07-14-spatial-authoring-r1.md`

## Joint authoritative-state audit

The three `GET /projects/{id}/state` responses were read again from the running candidate on 2026-07-14. For every project:

- `activeTask` was absent.
- Collection, screening, retrieval, extraction, and categorization were `completed` with `stale=false`.
- `schemaWorkbench.status` was `finalized`.
- The identified, screened, included, retrieved, extracted, and categorized counts agreed with the accepted run.
- All seven `exportPackage` entries existed.
- The serialized user-facing state contained no `/Users/`, `file://`, or `secrets.txt` marker.

The final spatial-authoring canvas was also reloaded directly from its project route in the in-app Chromium browser. At a measured 1280-pixel content width, body and document widths both equaled the viewport, horizontal overflow was false, seven export links were visible, `1 paper · 1 category` was present, the incorrect singular plurals were absent, and the error console was empty. The individual runs retain the approved 1280 × 800 browser evidence; the responsive shell and category layouts are also protected by frontend contract tests. A separate 1440 × 900 human smoke check remains in the cohort launch checklist rather than being misrepresented as automation evidence.

## Joint export audit

All 21 allow-listed endpoints returned HTTP 200, the expected `application/json` or `application/x-ndjson` media type, and a non-empty body. Current endpoint content remained byte-identical to the hashes recorded in the HCI r3 and spatial r1 reports. The biomedicine project remained complete and downloadable; its current aggregate export size was 25,792 bytes. Exact per-artifact hashes remain in the individual evidence and can change only if the underlying ignored local run artifacts are intentionally modified.

| Project | Endpoints | JSON | NDJSON | Total bytes | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| `inner-beta-biomedicine-r7` | 7 | 4 | 3 | 25,792 | PASS |
| `inner-beta-hci-r3` | 7 | 4 | 3 | 127,319 | PASS |
| `inner-beta-spatial-authoring-r1` | 7 | 4 | 3 | 29,170 | PASS |
| **Total** | **21** | **12** | **9** | **182,281** | **PASS** |

## Defect and regression disposition

The real-use iterations exposed 12 product defects: 6 P1, 5 P2, and 1 P3. All were repaired and regression-protected before this decision. No P0 was found. The repaired areas include project submission, source-limit fidelity and ordering, deterministic workflow routing, path-safe assistant summaries, export access and symlink rejection, balanced editable labels, real DownloadAgent retry sidecars, zero-field Schema gating, sample-size-aware categories, and one-item grammar.

The final candidate completed 702 automated tests with 0 failures, 0 errors, and 0 skipped tests in 76.623 seconds. An earlier exact-code run also passed all 702 tests in 103.334 seconds. The suite includes state authority, atomic/stale transitions, task exclusion, external-error classification, retry confirmation and transactional recovery, export allow-list security, frontend contracts, three frozen scenarios, and one-item language regressions.

## Remaining risks

| Risk | Release effect | Control / next action |
| --- | --- | --- |
| No 3–5-person usability cohort has run yet | Does not block starting beta; prevents claiming beta validation | Run the cohort checklist in `docs/internal-testing/INNER_BETA_RUNBOOK.md` and record observed completion/help rates |
| 1440 × 900 requires an explicit human launch smoke check | Does not block candidate packaging; blocks closing the launch checklist | One operator verifies setup, recovery banner, final analysis, and export at that viewport before inviting the cohort |
| External source/PDF availability varies | Expected operational risk | Partial state, safe errors, failed-only selection, confirmation, and retry are implemented and tested |
| `google.generativeai` and Starlette/httpx emit deprecation warnings | Non-blocking maintenance debt | Migrate dependencies in a separate compatibility change after the beta candidate is frozen |
| Advisory locking assumes cooperating local processes and a stable project directory | Acceptable only for this single-user local release | Redesign ownership/root pinning before any multi-worker or hostile-filesystem expansion |
| Browser screenshot transport timed out on the long HCI final canvas | Evidence-tool limitation, not observed product failure | DOM, task, endpoint, console, and artifact evidence were retained; do not treat the missing screenshot as a product pass signal by itself |

## Release gate

P0 is 0; all discovered P1 and P2 defects are closed; three independent real examples passed; persisted states are terminal and non-stale; 21/21 exports pass; no user-facing local path was detected; and the complete automated suite passes with no skipped tests. The candidate therefore meets the engineering gate to start formal internal testing. Product validation remains open until the planned internal users complete the runbook without developer intervention.
