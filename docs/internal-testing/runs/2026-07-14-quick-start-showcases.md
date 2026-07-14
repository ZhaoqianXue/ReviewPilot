# ReviewPilot Quick Start showcase runs

## Product acceptance

History must present the same three subjects as Quick Start and each History item must open a completed, inspectable review rather than an empty project template. The canonical History projects are `quick-start-biomedical-showcase`, `quick-start-hci-showcase`, and `quick-start-urban-showcase`. Older `inner-beta-*` and `qa-live-*` projects remain local audit evidence but are not selected while these canonical projects exist.

All three projects were created independently and run through collection, screening, full-text retrieval, schema finalization, extraction, categorization, and export. Each uses PubMed, arXiv, and OpenAlex with `max_results=10` and explicit source limits of 10 for every platform.

## Visible results

| History example | Identified / screened / included | PDFs retrieved | Extracted | Categorized | Groups | Workflow status |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| LLM for Biomedical | 30 / 29 / 7 | 4 / 7 | 7 | 7 | 4 | Complete; retrieval partial |
| LLM for HCI | 20 / 18 / 9 | 8 / 9 | 9 | 9 | 4 | Complete; retrieval partial |
| LLM for Urban | 30 / 28 / 11 | 10 / 11 | 11 | 10 | 3 | Complete; retrieval partial |

`Complete; retrieval partial` means the review reached finalized extraction and categorization while honestly retaining publisher-access failures. It does not mean missing PDFs were fabricated. Biomedical used three web-search fallbacks after four PDFs were retrieved; HCI used one fallback after eight PDFs were retrieved. Urban produced eleven extraction records after ten PDFs were retrieved, but the paywalled fallback for one paper contained no field-level evidence, so the final analysis correctly categorizes ten papers rather than inventing a category assignment.

Every stage in all three projects has `stale=false`, every project has `activeTask=null`, and every extraction schema is finalized. The History projection returns the three canonical project IDs in Quick Start order and does not attach `starterTopic`, so selecting an example opens its saved result instead of starting a new review.

## Export verification

All 21 allow-listed exports returned HTTP 200 with a non-empty `application/json` or `application/x-ndjson` response.

| Project | Endpoints | Total bytes | Result |
| --- | ---: | ---: | --- |
| `quick-start-biomedical-showcase` | 7 | 113,692 | PASS |
| `quick-start-hci-showcase` | 7 | 179,864 | PASS |
| `quick-start-urban-showcase` | 7 | 206,546 | PASS |
| **Total** | **21** | **500,102** | **PASS** |

## Regression protection

`test_history_prefers_completed_quick_start_showcases_over_inner_beta_runs` supplies canonical, QA, and older inner-beta projects together and asserts that History chooses the three `quick-start-*-showcase` IDs. The selection algorithm evaluates ID-prefix priority explicitly, preventing filesystem ordering or project recency from replacing a canonical showcase with an older partial run.
