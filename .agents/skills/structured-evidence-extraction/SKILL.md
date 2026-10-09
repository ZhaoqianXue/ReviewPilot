---
name: structured-evidence-extraction
description: Extract source-grounded structured evidence from papers, or from explicitly labeled web fallback sources, into a confirmed review schema. Use for ReviewPilot full-text extraction, fallback extraction, missingness handling, and extraction repair.
---

# Structured Evidence Extraction

## Extract evidence

1. Read the whole supplied source, including methods, results, tables, figure captions, appendices and availability or declaration statements, before deciding that something is not reported.
2. Populate a field when the source states the value or when the value necessarily follows from what the source describes, such as the structure, equations or procedure of the study's own method. Cite the passage that establishes it. Otherwise use the schema's missing representation.
3. Report what the study itself does or analyses. Leave out items mentioned only as background, motivation, related work, limitations or future work, and record a component of something the study does as part of that item rather than as a separate value.
4. When a field takes values from allowed categories, map the source's description to the category whose definition fits best. Use a catch-all category such as "Other" only for an entity that no specific category fits; an entity that fits a specific category takes that category alone.
5. When a field aggregates over several entities, such as models, arms, cohorts or sites, give each distinct entity its single best-fitting category and combine those categories. An entity with features of several categories takes the one category that best describes it as a whole. Draw categories only from the study's own distinct entities, leaving out sub-components, approximations and comparisons taken from other work.
6. Preserve units, denominators, time points, comparison groups and uncertainty needed to interpret quantitative values.
7. Keep source provenance explicit. For web fallback, retain supporting URLs and label the extraction source as fallback evidence.
8. Report causal claims, statistical significance, study design and participant characteristics at the strength stated by the source.
9. Check cross-field consistency before returning structured output.

## Cite evidence

1. Give supporting evidence for every populated field.
2. Copy each excerpt exactly as one continuous span of the supplied text, preferably the sentence that states the value, keeping its wording and equations as written and without ellipses or merged sentences.
3. When a value follows from the study's method but no sentence states it directly, quote the passage that describes that method and populate the field.
4. When a value rests on several passages, or a list field has several values, give one excerpt per passage.

## Quality checks

- Make every populated field traceable to the declared source.
- Preserve partial results when some fields are supported and others are not.
- Mark unreadable, contradictory, or unsupported evidence explicitly without fabricating completion.
- Distinguish `missing`, `not reported`, `not applicable`, and `not confirmable` when the output schema supports those states.
