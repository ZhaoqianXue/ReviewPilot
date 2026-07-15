---
name: evidence-screening
description: Convert an approved review scope into operational eligibility criteria and apply them consistently to titles and abstracts. Use when ReviewPilot generates relevance instructions, screens candidate records, reassesses borderline records, or explains evidence-grounded inclusion and exclusion decisions.
---

# Evidence Screening

## Operationalize criteria

1. Translate each approved inclusion and exclusion rule into an observable condition that can be tested from the available title, abstract, and metadata.
2. Keep topic, population or context, intervention or phenomenon, study design, publication type, date, and language criteria distinct.
3. Define standardized exclusion reasons that map one-to-one to approved criteria. Do not create a new criterion during screening.
4. Specify how to handle missing abstracts, ambiguous terminology, protocols, reviews, editorials, duplicates, and borderline scope.

## Decide records

1. Read only the supplied record evidence.
2. Check deterministic metadata gates first when provided, then substantive eligibility.
3. Include when the record clearly meets all required criteria.
4. Exclude only when an approved exclusion criterion is supported by explicit record evidence.
5. Treat insufficient evidence conservatively: do not convert missing information into a fabricated exclusion fact. If the output contract is binary, retain the record when full text could resolve the uncertainty.
6. Give a concise rationale tied to the decisive criterion and evidence.

## Quality checks

- Apply the same threshold across records.
- Never infer an unreported population, method, outcome, or setting.
- Do not exclude merely because the title uses unfamiliar wording.
- Prefer false-positive retention over unsupported false exclusion during title/abstract screening.
- Return only the schema required by the current ReviewPilot prompt; do not change workflow state or artifact counts.
