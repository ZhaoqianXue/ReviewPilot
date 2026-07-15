---
name: structured-evidence-extraction
description: Design review-specific extraction schemas and extract source-grounded structured evidence from papers or explicitly labeled web fallback sources. Use for ReviewPilot schema generation, field definition, full-text extraction, fallback extraction, missingness handling, and extraction repair.
---

# Structured Evidence Extraction

## Design the schema

1. Derive fields from the review question and intended synthesis, not from incidental wording in a few papers.
2. Give every field a unique stable name, precise description, value type, unit or allowed values where applicable, and evidence requirement.
3. Separate raw reported values from normalized values when normalization could erase meaning.
4. Include only fields that can be supported by the available evidence and used downstream.
5. Distinguish `missing`, `not reported`, `not applicable`, and `not confirmable` when the output schema supports those states.

## Extract evidence

1. Use only the supplied source. Never treat metadata, an abstract, publisher text, or a web snippet as full text.
2. Populate a field only when the source supports it. Otherwise use the schema's unresolved or missing representation.
3. Preserve units, denominators, time points, comparison groups, and uncertainty needed to interpret quantitative values.
4. Keep source provenance explicit. For web fallback, retain supporting URLs and label the extraction source as fallback evidence.
5. Do not infer causal claims, statistical significance, study design, or participant characteristics that are not reported.
6. Check cross-field consistency before returning structured output.

## Quality checks

- Return exactly the requested machine-readable shape.
- Do not add undeclared fields or prose outside the required output.
- Make every populated field traceable to the declared source.
- Preserve partial results when some fields are supported and others are not.
- Fail explicitly on unreadable or contradictory source material rather than fabricating completion.
