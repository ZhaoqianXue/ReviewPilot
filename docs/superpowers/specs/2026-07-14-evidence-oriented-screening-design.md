# Evidence-oriented relevance screening design

## Problem

ReviewPilot currently asks whether each candidate paper is directly "about" the configured `primary_topic`. When setup generation phrases that topic as a review activity, such as `Survey of large language models for biomedicine`, screening incorrectly requires each candidate to be a survey. Primary studies, methods, datasets, benchmarks, applications, and evaluations that should provide evidence to the user's review are therefore rejected.

The three Quick Start showcase runs prove that deduplication is not the cause of the low inclusion counts. Biomedical removed 22 of 29 screened papers at the binary relevance step, including clearly in-scope biomedical LLM studies.

## Decision

Relevance screening will answer whether a paper provides substantive evidence relevant to the user's review question, not whether the paper has the same publication type or wording as the review objective.

The generated prompt will:

- retain the existing requirement for both topical and domain relevance;
- explicitly allow primary studies, methods, systems, datasets, benchmarks, applications, evaluations, and existing reviews;
- explicitly state that words such as `survey`, `review`, or `mapping` in the project objective describe the user's synthesis activity and must not become required paper types;
- continue to use only title and abstract and return exactly `True` or `False`;
- continue excluding merely incidental mentions and papers missing either the topic or domain criterion.

This changes only PromptAgent's relevance-prompt semantics. It does not loosen date filtering, deduplication, retrieval, extraction, or categorization.

## Data flow

`search_conditions.json` remains authoritative. PromptAgent receives `primary_topic` and `domain`, generates the revised evidence-oriented prompt, and writes the same `prompts/relevance_prompt.json` contract. Filtering consumes that artifact without interface changes.

Existing completed projects are immutable evidence of their historical run. The three canonical Quick Start showcase projects will regenerate the prompt, rerun screening from their already collected source records, and rerun every downstream stage whose inputs change.

## Acceptance criteria

1. A regression test proves that a review-oriented topic generates an evidence-oriented prompt that does not require candidate papers themselves to be surveys.
2. Existing topic/domain exclusion language and the exact `True`/`False` output contract remain protected.
3. PromptAgent tests, related workflow tests, frontend contracts, and the full automated suite pass without skips.
4. Each canonical showcase ends with no active task, no stale stage, a finalized schema, complete extraction/categorization, and seven valid exports.
5. History continues to show exactly the three canonical Quick Start examples with `max_results=10` and source limits of 10 per platform.

## Non-goals

This change does not add probabilistic scores, human adjudication queues, multi-label exclusion reasons, or an inclusion-count target. Those would be separate product changes. The system must not manipulate screening merely to produce a visually larger example.
