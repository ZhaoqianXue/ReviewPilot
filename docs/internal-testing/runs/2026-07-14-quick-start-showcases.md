# ReviewPilot Quick Start showcase runs

## Product acceptance

History must present the same three subjects as Quick Start and each History item must open a completed, inspectable review rather than an empty project template. The canonical History projects are `quick-start-biomedical-showcase`, `quick-start-hci-showcase`, and `quick-start-urban-showcase`. Older `inner-beta-*` and `qa-live-*` projects remain local audit evidence but are not selected while these canonical projects exist.

All three projects were created independently and run through collection, screening, full-text retrieval, schema finalization, extraction, categorization, and export. Each uses PubMed, arXiv, and OpenAlex with `max_results=10` and explicit source limits of 10 for every platform.

## Visible results

| History example | Identified / screened / included | PDFs retrieved | Extracted | Categorized | Groups | Workflow status |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| LLM for Biomedical | 30 / 29 / 24 | 17 / 24 | 24 | 24 | 4 | Complete; retrieval partial |
| LLM for HCI | 20 / 18 / 12 | 11 / 12 | 12 | 12 | 4 | Complete; retrieval partial |
| LLM for Urban | 30 / 28 / 14 | 12 / 14 | 14 | 14 | 2 | Complete; retrieval partial |

The projects were regenerated after correcting relevance screening so that review-intent words such as `survey` no longer require candidate papers themselves to be surveys. The previous Biomedical run excluded 22 of 29 screened papers; the corrected evidence-oriented prompt excludes five and includes 24. HCI increases from 9 to 12 included papers, and Urban increases from 11 to 14. No inclusion quota was used.

`Complete; retrieval partial` means the review reached finalized extraction and categorization while honestly retaining publisher-access failures. It does not mean missing PDFs were fabricated. After one failed-only retry, Biomedical retrieved 17 PDFs and used seven web-search fallbacks; HCI retrieved 11 PDFs and used one fallback; Urban retrieved 12 PDFs and used two fallbacks. Extraction succeeded for all 50 included papers with no pending fallback. Biomedical categorization uses `biomedical_task` rather than `methods` because that field has evidence for all 24 papers; HCI and Urban use `methods`.

Every stage in all three projects has `stale=false`, every project has `activeTask=null`, and every extraction schema is finalized. The History projection returns the three canonical project IDs in Quick Start order and does not attach `starterTopic`, so selecting an example opens its saved result instead of starting a new review.

## Export verification

All 21 allow-listed exports returned HTTP 200 with a non-empty `application/json` or `application/x-ndjson` response.

| Project | Endpoints | Total bytes | Result |
| --- | ---: | ---: | --- |
| `quick-start-biomedical-showcase` | 7 | 353,257 | PASS |
| `quick-start-hci-showcase` | 7 | 189,351 | PASS |
| `quick-start-urban-showcase` | 7 | 187,845 | PASS |
| **Total** | **21** | **730,453** | **PASS** |

## Regression protection

`test_history_prefers_completed_quick_start_showcases_over_inner_beta_runs` supplies canonical, QA, and older inner-beta projects together and asserts that History chooses the three `quick-start-*-showcase` IDs. The selection algorithm evaluates ID-prefix priority explicitly, preventing filesystem ordering or project recency from replacing a canonical showcase with an older partial run.

`test_review_objective_does_not_require_candidates_to_be_reviews` protects the corrected screening semantics while retaining the topic/domain and exact `True`/`False` contracts. The clean full suite completed 705 tests with zero failures and zero errors. An initial full-suite run had one unrelated PDF-downloader failure because a fixed shared `/tmp/reviewpilot-test-pdfs` directory contained a same-name test artifact; removing that test-only directory made the isolated test pass and the clean full suite pass.
