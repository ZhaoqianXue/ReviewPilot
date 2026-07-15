---
name: evidence-synthesis-and-categorization
description: Organize extracted review evidence into broad, coherent, human-reviewable semantic categories and apply user-confirmed labels. Use for ReviewPilot category suggestion, category-plan generation, single-label or multi-label assignment, and coverage-oriented synthesis.
---

# Evidence Synthesis and Categorization

## Choose the basis

1. Categorize a substantive extracted field that is populated across enough papers to support comparison.
2. Avoid identifiers, bibliographic metadata, technical status fields, and fields dominated by missing values.
3. Use single-label mode when categories represent mutually exclusive primary groupings. Use multi-label mode only when evidence can legitimately express several independent themes.

## Build categories

1. Induce broad, reusable categories from the supplied evidence.
2. Make categories materially fewer than papers when the sample permits; never create one label per paper.
3. Give categories distinct scopes and concise definitions. Merge near-duplicates and remove empty labels.
4. Preserve an `Other` or unresolved route only when the current contract permits it; do not force weak matches.
5. For small samples, prefer a few stable umbrella categories over fragile fine-grained themes.

## Apply categories

1. Assign only labels from the user-confirmed allowed list.
2. Base each assignment on the supplied field value and paper context, not outside knowledge.
3. In multi-label mode, return only clearly supported labels.
4. Normalize harmless case and whitespace differences, but never invent a new label.
5. Keep category suggestions editable and preserve the existing human confirmation checkpoint.

## Quality checks

- Check coverage, overlap, singleton proliferation, empty categories, and definition ambiguity.
- Do not present descriptive categories as causal, statistical, or universal conclusions.
- Return exactly the output form requested by the current call.
