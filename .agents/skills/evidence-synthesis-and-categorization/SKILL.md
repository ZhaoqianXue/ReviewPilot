---
name: evidence-synthesis-and-categorization
description: Organize extracted review evidence into broad, coherent, human-reviewable semantic categories and apply user-confirmed labels. Use for ReviewPilot category suggestion, category-plan generation, single-label or multi-label assignment, and coverage-oriented synthesis.
---

# Evidence Synthesis and Categorization

## Choose the basis

1. Categorize a substantive extracted field that is populated across enough papers to support comparison.
2. Prefer substantive evidence fields over identifiers, bibliographic metadata, technical status fields, and fields dominated by missing values.
3. Use single-label mode when categories represent mutually exclusive primary groupings. Use multi-label mode only when evidence can legitimately express several independent themes.

## Build categories

1. Induce broad, reusable categories from the supplied evidence.
2. Make categories materially fewer than papers when the sample permits and reuse labels across records.
3. Give categories distinct scopes and concise definitions. Merge near-duplicates and remove empty labels.
4. Use an unresolved route when the category design supports it and the evidence does not support a stronger match.
5. For small samples, prefer a few stable umbrella categories over fragile fine-grained themes.

## Apply categories

1. Assign labels from the supplied allowed list.
2. Base each assignment on the supplied field value and paper context, not outside knowledge.
3. In multi-label mode, return only clearly supported labels.
4. Treat harmless case and whitespace differences as the same allowed label.

## Quality checks

- Check coverage, overlap, singleton proliferation, empty categories, and definition ambiguity.
- Present descriptive categories as organization of the supplied evidence rather than causal, statistical, or universal conclusions.
