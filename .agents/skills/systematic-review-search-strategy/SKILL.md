---
name: systematic-review-search-strategy
description: Design and revise reproducible, scope-faithful concept strategies for systematic-review searches across scholarly sources.
---

# Systematic Review Search Strategy

A strategy is a list of concept blocks. Each block is one atomic concept with a canonical label, a role, an eligibility group, whether it is required for eligibility, and its equivalent query terms. The system builds the Boolean query from the blocks: terms of the concepts in one eligibility group are joined with OR, and required groups are joined with AND.

## Frame the scope

1. Preserve the stated research scope across its population or context, phenomenon, intervention or exposure, outcomes, and study design.
2. Identify the minimum set of distinct concepts needed to represent that scope.
3. Separate research scope from operational instructions. Database names, result limits, date controls, session names, and workflow-testing instructions are settings and stay outside concepts and query terms. Publication language, document type, and other record filters are also outside the strategy; the reply tells the user that the search cannot apply them.
4. Distinguish concepts that determine study eligibility from dimensions used only to describe or analyze the included evidence. Mark analytical dimensions as not required so they stay out of the query.
5. Represent every user-facing concept as one atomic concept at the specificity the user stated, with a concise canonical label. Include a widely recognized abbreviation in the label when it appears in the request or improves interpretation.
6. Keep alternatives within one conceptual dimension as separate concepts that share one eligibility group; a record satisfies the group by matching any of them. Concepts in one group share a role.
7. Treat a list of settings, domains, populations, or interventions as shared coverage when an eligible record may address any listed member, even when the wording joins the members with "and".
8. Use distinct required groups only when every eligible record must match at least one concept from every group. Keep phenomenon, population, condition, context, intervention or exposure, outcome, and study design in distinct groups when each is required.
9. Require the concepts that titles and abstracts reliably state: usually the population or condition, the intervention, exposure, or phenomenon, and a setting the user names. Outcomes, age or sex qualifiers, and study design are often unstated there, so they are concepts that are not required, with their terms kept for screening. Naming one of them in the research question describes the review's interest; it becomes required only when the user asks for the search itself to use it ("search on mortality", "restrict the search to randomized trials"). For "exercise for fatigue in adults with multiple sclerosis", multiple sclerosis and exercise are required, while fatigue and adults are not.
10. Retain names and entities that the research question explicitly places within scope.
11. Treat relational wording that states the review's interest as synthesis intent unless the relation itself is an eligibility requirement.

## Choose query terms

- Populate a concept's terms only with spelling variants, inflections, sufficiently specific abbreviations, historical names, and exact synonyms of its label, at the same specificity.
- Keep an abbreviation only when it is unambiguous across the literature the sources index; a short form with other common meanings in the field (ED for emergency department also means eating disorder and erectile dysfunction) stays out, and the spelled-out term carries the concept. An abbreviation the user wrote in parentheses may appear in the label and follows the same test before it becomes a term.
- Keep each term at the label's specificity. A broader umbrella (healthcare documentation for clinical documentation) or a sibling concept widens the review and belongs in scope only when the user asks for it.
- Retain a candidate term when a record using it would express the same concept at the same level of specificity.
- Use the minimum sufficient set of equivalents; stop once spelling, inflection, abbreviation, historical naming, and exact-synonym coverage is represented. The term limit is a cap, not a target.
- Broader categories, narrower instances, products, methods, applications, enabling architectures, and associated concepts are separate scope decisions; they enter retrieval only when the stated scope includes them.
- Write terms as plain, source-neutral text. Source adapters apply field tags, phrase quoting, and database syntax.

## Revise an existing strategy

- Treat the saved concept blocks as authoritative and change only what the current message asks for.
- Preserve every block, label, group, and term the message does not address.
- Narrowing the scope adds a required concept or removes alternatives; broadening adds alternatives to an existing group or removes a required concept.
- A request about sources, limits, or dates changes settings only.
- Answer questions without changing the strategy.
- State exactly what changed, describing only changes that the returned strategy contains.

## Quality checks

- Every concept is traceable to the stated research scope.
- Every term is equivalent enough to retrieve the same concept.
- Alternatives remain alternatives, and jointly required concepts remain intersections.
- Labels are distinct, concise, and nonredundant.
- Required concepts reflect eligibility rather than plans for later analysis.
