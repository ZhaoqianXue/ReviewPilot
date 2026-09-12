---
name: structured-evidence-extraction
description: Design review-specific extraction schemas and extract source-grounded structured evidence from papers or explicitly labeled web fallback sources. Use for ReviewPilot schema generation, field definition, full-text extraction, fallback extraction, missingness handling, and extraction repair.
---

# Structured Evidence Extraction

## Design the schema

1. Derive fields from the review question and intended synthesis, not from incidental wording in a few papers.
2. Give every field a unique stable name, precise description, value type, unit or allowed values where applicable, and evidence requirement.
3. Separate raw reported values from normalized values when normalization could erase meaning.
4. Include fields that can be supported by the available evidence and contribute to the intended synthesis.
5. Distinguish `missing`, `not reported`, `not applicable`, and `not confirmable` when the output schema supports those states.

## Extract evidence

1. Match every claim to the declared source type and evidence supplied for the current record.
2. Populate a field when the source supports it; otherwise use the schema's unresolved or missing representation.
3. Preserve units, denominators, time points, comparison groups, and uncertainty needed to interpret quantitative values.
4. Keep source provenance explicit. For web fallback, retain supporting URLs and label the extraction source as fallback evidence.
5. Report causal claims, statistical significance, study design, and participant characteristics at the strength stated by the source.
6. Check cross-field consistency before returning structured output.

## Quality checks

- Make every populated field traceable to the declared source.
- Preserve partial results when some fields are supported and others are not.
- Mark unreadable, contradictory, or unsupported evidence explicitly without fabricating completion.
