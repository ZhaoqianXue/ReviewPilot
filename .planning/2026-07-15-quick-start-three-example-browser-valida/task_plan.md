# Task Plan: Quick Start Three-Example Browser Validation

## Goal
Use the real ReviewPilot Web App as a user to rerun the three canonical Quick Start topics with PubMed, arXiv, and OpenAlex limited to 10 papers each, complete every workflow through categorization, and update the three canonical History examples with verified current artifacts.

## Current Phase
Phase 6: Regression and Handoff

## Success Criteria
- All three runs start through visible Quick Start controls in the in-app browser.
- Each saved setup has exactly `pubmed`, `arxiv`, and `openalex`, with every source limit equal to 10.
- Each run completes collection, screening, retrieval, schema finalization, extraction, and categorization without silently bypassing UI actions.
- History shows Biomedical, HCI, and Urban in Quick Start order and each opens the newly completed canonical project.
- Workflow ledgers, Skill activation traces, artifacts, export endpoints, and visible browser state agree.
- Any defect is reproduced, fixed surgically, regression-tested, and rerun from a clean affected example.

## Phases

### Phase 1: Runtime and Evidence Baseline
- [x] Recover exact Quick Start topics, canonical History IDs, existing artifacts, and launch configuration
- [x] Connect to the in-app browser and inspect the initial visible state
- [x] Preserve old canonical projects before replacement and establish fresh run IDs
- **Status:** completed

### Phase 2: Biomedical Browser Run
- [x] Start from the Biomedical Quick Start control and set all three source limits to 10
- [x] Complete all five visible workflow steps through categorization
- [x] Verify artifacts, Skill traces, exports, and History selection
- **Status:** completed

### Phase 3: HCI Browser Run
- [x] Start from the HCI Quick Start control and set all three source limits to 10
- [x] Complete all five visible workflow steps through categorization
- [x] Verify artifacts, Skill traces, exports, and History selection
- **Status:** completed

### Phase 4: Urban Browser Run
- [x] Start from the Urban Quick Start control and set all three source limits to 10
- [x] Complete all five visible workflow steps through categorization
- [x] Verify artifacts, Skill traces, exports, and History selection
- **Status:** completed

### Phase 5: History and Cross-Example Audit
- [x] Confirm the three History entries open the new canonical results in the visible UI
- [x] Reconcile stage counts, source counts, schema status, Skill traces, and 21 exports
- [x] Record visible browser evidence and update the canonical three-example History run record
- **Status:** completed

### Phase 6: Regression and Handoff
- [x] Run affected automated tests and static checks if product or documentation files change
- [x] Record unresolved external-source limitations without relabeling partial retrieval as complete
- [x] Deliver exact run IDs, counts, defects, fixes, and confidence
- **Status:** completed

## Decisions Made
| Decision | Rationale |
|---|---|
| Use new browser-created projects and replace canonical History projects only after successful completion | Prevents stale artifacts or failed partial runs from becoming the visible examples. |
| Use the three exact `STARTER_TOPICS` strings | The user asked for the product's Quick Start examples, not approximate subjects. |
| Require three sources × 10 results in persisted setup | This is the requested external-call boundary and must be verified before collection. |
| Treat History as both visible UI and authoritative state projection | A filesystem-only replacement is insufficient for a real-user acceptance test. |

## Errors Encountered
| Error | Attempt | Resolution |
|---|---:|---|
| Exact accessible name for the Biomedical starter button excluded the visible leading icon, producing count 0 | 1 | Do not retry the same locator; refresh the DOM and use the stable visible topic text scoped to a button. |
| Browser locator `waitFor(hidden)` was capped by the browser backend's short selector deadline despite a longer requested timeout | 1 | Stop using long locator waits; use short visible-state polling followed by a fresh DOM check, while leaving the real Web task running. |
| First click on the newly unlocked Biomedical retrieval step hit the browser backend's 3-second CDP selector deadline | 1 | Do not retry against stale state; refresh the DOM, verify whether navigation already occurred, and only click a newly confirmed unique control if still necessary. |
| Biomedical `Download PDFs` Playwright click hit the same short CDP deadline | 1 | Refresh visible state to determine whether the action started; if not, use the already proven unique bounding-box/coordinate fallback. |
| Biomedical Information Extraction step rectangle was above the current scrolled viewport, producing a negative click coordinate | 1 | Do not force the off-screen click; scroll the visible page to the stepper, refresh the DOM, and recompute the rectangle. |
| First coordinate-scroll point did not intersect an in-app-browser element | 1 | Inspect the actual viewport and scroll-container geometry before choosing a new scroll target; do not repeat the guessed point. |
| Information Extraction step remained horizontally outside the 320px viewport after vertical correction | 2 | The app is a desktop three-column interface; switch the browser to an explicit desktop viewport before further workflow interaction instead of forcing off-screen coordinates. |
| Delegated step click still timed out under Playwright at 1440×900 even though the step was visible and unique | 3 | Standardize remaining delegated step/action interactions on verified bounding rectangles plus browser coordinate clicks; do not retry Playwright clicking for this pattern. |
| Initial categorization scroll call used unsupported `deltaX`/`deltaY` names | 1 | Use the browser capability's documented `scrollX`/`scrollY` keys; the rejected call did not move or mutate the app. |
