# ReviewPilot Quick Start showcase runs

> Historical record: these 2026-07-14 artifacts were replaced in the canonical History slots by the 2026-07-15 Agent Skill browser validation. See [`2026-07-15-agent-skill-quick-start-showcases.md`](2026-07-15-agent-skill-quick-start-showcases.md) for the current runs and counts.

## Product acceptance

History must present the same three subjects as Quick Start and each History item must open a completed, inspectable review rather than an empty project template. The canonical History projects are `quick-start-biomedical-showcase`, `quick-start-hci-showcase`, and `quick-start-urban-showcase`. Older `inner-beta-*` and `qa-live-*` projects remain local audit evidence but are not selected while these canonical projects exist.

All three projects were created independently and run through collection, screening, full-text retrieval, schema finalization, extraction, categorization, and export. Each uses PubMed, arXiv, and OpenAlex with `max_results=10` and explicit source limits of 10 for every platform.

## Visible results

| History example | Identified / screened / included | PDFs retrieved | Extracted | Categorized | Groups | Workflow status |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| LLM for Biomedical | 30 / 29 / 24 | 17 / 24 | 24 | 24 | 5 | Complete; retrieval partial |
| LLM for HCI | 30 / 28 / 18 | 15 / 18 | 18 | 18 | 5 | Complete; retrieval partial |
| LLM for Urban | 30 / 28 / 14 | 12 / 14 | 14 | 14 | 4 | Complete; retrieval partial |

The projects were rerun from Collection through Categorization on 2026-07-14 after the Information Extraction interaction work. Every live collection returned exactly 10 PubMed, 10 arXiv, and 10 OpenAlex records. Evidence-oriented screening excluded five of 29 Biomedical records, ten of 28 HCI records, and fourteen of 28 Urban records. No inclusion quota was used.

`Complete; retrieval partial` means the review reached finalized extraction and categorization while honestly retaining publisher-access failures. It does not mean missing PDFs were fabricated. Biomedical retrieved 17 PDFs and used seven web-search fallbacks; HCI retrieved 15 PDFs and used three fallbacks; Urban retrieved 12 PDFs and used two fallbacks. Extraction succeeded for all 56 included papers with zero errors and no pending fallback. Biomedical categorization uses `biomedical_task`; HCI and Urban use `methods`.

Every stage in all three projects has `stale=false`, every project has `activeTask=null`, and every extraction schema is finalized. The History projection returns the three canonical project IDs in Quick Start order and does not attach `starterTopic`, so selecting an example opens its saved result instead of starting a new review.

## Export verification

All 21 allow-listed exports returned HTTP 200 with a non-empty `application/json` or `application/x-ndjson` response.

| Project | Endpoints | Total bytes | Result |
| --- | ---: | ---: | --- |
| `quick-start-biomedical-showcase` | 7 | 401,609 | PASS |
| `quick-start-hci-showcase` | 7 | 257,236 | PASS |
| `quick-start-urban-showcase` | 7 | 199,972 | PASS |
| **Total** | **21** | **858,817** | **PASS** |

## Regression protection

`test_history_prefers_completed_quick_start_showcases_over_inner_beta_runs` supplies canonical, QA, and older inner-beta projects together and asserts that History chooses the three `quick-start-*-showcase` IDs. The selection algorithm evaluates ID-prefix priority explicitly, preventing filesystem ordering or project recency from replacing a canonical showcase with an older partial run.

`test_review_objective_does_not_require_candidates_to_be_reviews` protects the corrected screening semantics while retaining the topic/domain and exact `True`/`False` contracts. The clean full suite completed 722 tests and 492 subtests with zero failures, zero errors, and zero skips.
