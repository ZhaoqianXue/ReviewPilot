---
name: systematic-review-search-strategy
description: Design or revise reproducible systematic-review search concepts and source-aware Boolean queries. Use for ReviewPilot search setup, concept expansion, query translation across academic databases, and diagnosis of searches that are too broad, too narrow, or empty.
---

# Systematic Review Search Strategy

## Build the strategy

1. Preserve the user's review question, population or context, phenomenon or intervention, outcomes, study types, dates, languages, and document types. Do not silently broaden or narrow an approved scope.
2. Split the question into two to four indispensable concept blocks. Keep outcomes out of the query when they would suppress recall unless the user explicitly requires them.
3. Expand each block with spelling variants, abbreviations, historical terminology, and controlled-vocabulary candidates. Do not invent domain synonyms whose equivalence is uncertain.
4. Join synonyms with `OR`, concept blocks with `AND`, and exclusions only when a clearly irrelevant family can be removed without discarding eligible studies.
5. Translate syntax per selected source. Preserve the same conceptual strategy while adapting field tags, phrase syntax, wildcards, proximity operators, and controlled vocabulary.
6. Expose material assumptions and provide a reviewable query. Collection code, not the model, executes the approved query.

## Quality checks

- Keep parentheses balanced and Boolean operators explicit.
- Preserve meaningful phrases and avoid redundant variants.
- Prefer sensitivity during initial discovery; diagnose precision problems by concept block rather than adding arbitrary exclusions.
- When results are empty, test spelling, field tags, phrase restrictions, and the most restrictive block before changing scope.
- When results are too broad, strengthen the weakest indispensable block before adding outcome terms.
- Never claim controlled-vocabulary validity without source evidence.

## Output discipline

Return a reviewable search setup with the conceptual blocks, final query, selected sources, source-specific adaptations when needed, date constraints, and unresolved assumptions. Do not perform retrieval, pagination, deduplication, or download work.
