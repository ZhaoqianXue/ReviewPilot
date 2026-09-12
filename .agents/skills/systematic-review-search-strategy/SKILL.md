---
name: systematic-review-search-strategy
description: Design reproducible, scope-faithful concept strategies for systematic-review searches across scholarly sources.
---

# Systematic Review Search Strategy

## Frame the scope

1. Preserve the stated research scope across its population or context, phenomenon, intervention or exposure, outcomes, study design, dates, languages, and document types.
2. Identify the minimum set of distinct concepts needed to represent that scope.
3. Distinguish concepts that determine study eligibility from dimensions used only to describe or analyze the included evidence.
4. Represent every user-facing concept as one atomic concept with its own concise canonical label, including a widely recognized abbreviation when it improves interpretation.
5. Coordinate alternatives within the same conceptual dimension in one Boolean group. Keep phenomenon, population, condition, context, intervention or exposure, outcome, and study design in distinct groups when every eligible record must satisfy those dimensions.
6. Interpret a list of settings, domains, populations, or interventions as shared coverage when a record may address any listed member; conjunction in the wording alone does not establish required co-occurrence.
7. Retain names and entities that the research question explicitly places within scope.
8. Treat relational wording that states the review's interest as synthesis intent unless the relation itself is an eligibility requirement.

## Build the concept strategy

- Expand each eligibility concept with supported spelling variants, inflections, sufficiently specific abbreviations, historical terminology, and exact synonyms.
- Retain a candidate term when a record using that term would express the same concept at the same level of specificity.
- Use the minimum sufficient set of supported equivalents; expansion stops once spelling, inflection, abbreviation, historical naming, and exact-synonym coverage is represented.
- Populate synonym sets only with lexical equivalents at the same specificity. Broader categories, narrower instances, products, methods, applications, enabling architectures, and associated concepts remain separate scope decisions and enter retrieval only when the stated scope includes them.
- Combine equivalent terms within a concept using `OR` and combine eligibility concepts using `AND`.
- Use exclusions as explicit scope decisions only when they remove a clearly irrelevant family without discarding eligible studies.
- Express terms independently of database-specific field tags, wildcard conventions, or proximity syntax so the same conceptual strategy can be translated faithfully for each source.
- Include analytical dimensions in the scope representation and include them in the retrieval query only when they determine eligibility.

## Quality checks

- Every concept is traceable to the stated research scope.
- Every term in a synonym set is equivalent enough to retrieve the same concept.
- Alternative contexts remain alternatives, while jointly required concepts remain intersections.
- Concept labels are distinct, concise, and nonredundant.
- Required concepts reflect eligibility rather than optional plans for later analysis.
- Meaningful phrases remain intact and Boolean relationships remain explicit.
- Controlled-vocabulary candidates remain provisional until supported by source evidence.
