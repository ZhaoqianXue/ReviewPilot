# ReviewPilot Agent Skill Quick Start browser validation

## Acceptance scope

The three Quick Start topics were selected and completed through the visible ReviewPilot Web App on 2026-07-15. Each project was created from its actual starter button, retained PubMed, arXiv, and OpenAlex, used a source limit of 10 for every platform, and proceeded through collection, screening, full-text retrieval, extraction-schema generation and finalization, information extraction, category suggestion and confirmation, category assignment, and `Finalize Project`.

After all three projects displayed `Project Complete!`, the previous 2026-07-14 canonical directories were backed up under `tmp/quick-start-history-backup-20260715`. The completed browser-created projects were promoted to `quick-start-biomedical-showcase`, `quick-start-hci-showcase`, and `quick-start-urban-showcase`. The restarted Web App exposed `LLM for Biomedical`, `LLM for HCI`, and `LLM for Urban` in Quick Start order, and each History entry was clicked and verified to open the corresponding Jul 15 result.

## Current canonical results

| History example | Identified | Source split | After de-dup | Included | PDFs | Web fallback | Extracted / errors | Result rows | Non-empty mappings | Used groups | Status |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| LLM for Biomedical | 30 | 10 / 10 / 10 | 27 | 22 | 21 / 22 | 1 | 22 / 0 | 22 | 21 | 5 | Complete; retrieval partial |
| LLM for HCI | 30 | 10 / 10 / 10 | 29 | 16 | 15 / 16 | 1 | 16 / 0 | 16 | 13 | 3 | Complete; retrieval partial |
| LLM for Urban | 30 | 10 / 10 / 10 | 30 | 15 | 11 / 15 | 4 | 15 / 0 | 15 | 15 | 5 | Complete; retrieval partial |

The source split is PubMed / arXiv / OpenAlex. All three collections reported an empty `platform_errors` object. Partial retrieval is preserved honestly: Biomedical has one `article_print_failed` paper, HCI has one failed item, and Urban has four failed items. Extraction processed every included record through PDF or web-search fallback, reported zero errors, and left zero pending fallbacks.

`Result rows` counts `categorized_results.jsonl` rows. `Non-empty mappings` counts papers assigned to at least one category. The difference is intentional when the chosen categorization field is empty: Biomedical categorized `methods` for 21 of 22 papers; HCI categorized `datasets_used_raw` for 13 of 16 papers; Urban categorized `methods` for all 15. The result files still retain every included paper.

## Agent Skill evidence

Every project contains `.reviewpilot/skill_activations.jsonl`. The canonical runs recorded 8 Biomedical activations, 8 HCI activations, and 7 Urban activations. Across the three projects the traces cover all four runtime Skills and their intended agent boundaries:

- `systematic-review-search-strategy` on `SearchConditionAgent` for `save-search-setup`;
- `evidence-screening` on `PromptAgent` for `generate-relevance-prompt` and on `FilteringAgent` for `screen`;
- `structured-evidence-extraction` on `PromptAgent` for `generate-schema` and on `ExtractionAgent` for `run-extraction`;
- `evidence-synthesis-and-categorization` on `LeadAgentCategorization` for `suggest-categories` and `categorize`.

Biomedical has a second setup activation because the visible setup was persisted before collection. HCI has a second schema-generation activation because the first visible click did not refresh the zero-field UI before a single fresh-control retry. Both final schemas are finalized and all downstream stages are current.

## State and export audit

All three state endpoints return `activeTask: null`. Collection, screening, extraction, and categorization are completed; retrieval is explicitly partial; every stage has `stale: false`; every schema workbench reports `finalized`; and every project exposes all seven allow-listed exports.

All 21 exports returned HTTP 200 with non-empty `application/json` or `application/x-ndjson` bodies.

| Project | Endpoints | Total bytes | Result |
| --- | ---: | ---: | --- |
| `quick-start-biomedical-showcase` | 7 | 313,938 | PASS |
| `quick-start-hci-showcase` | 7 | 305,828 | PASS |
| `quick-start-urban-showcase` | 7 | 190,728 | PASS |
| **Total** | **21** | **810,494** | **PASS** |

## Browser observations

No ReviewPilot action error appeared during any project run or History verification. The in-app browser backend imposed a short selector deadline on generic step elements, so visible, unique element rectangles were used for coordinate interaction after refreshing state. This was a browser-control limitation rather than an application failure. The Web App correctly prevented downstream actions while tasks were active, preserved retryable retrieval failures, required explicit schema finalization and category confirmation, separated category-plan confirmation from paper assignment, and required terminal project finalization.

## Regression result

The native arm64 full suite completed after the History replacement: `727 passed, 494 subtests passed, 7 warnings` in 67.22 seconds, with zero failures, zero errors, and zero skips. The warnings are the existing deprecated `google.generativeai`, SWIG type metadata, and Starlette/httpx integration warnings.
