---
name: review-prompt-design
description: Write the review-specific guidance that screening and extraction instructions carry, from the review's objectives, confirmed criteria or schema, and the candidate records, without reviewer labels.
---

# Review Prompt Design

Screening and extraction instructions combine a fixed procedure with guidance written for one review. This skill writes that guidance. The guidance interprets the confirmed criteria or schema for the records this review will actually meet; it never adds eligibility requirements or fields of its own.

## Tasks

The Prompt Agent has two tasks, one per stage. Each request names its task and step; follow that section and the shared principles, and ignore the other sections.

| Task | Step | Runs when | Inputs | Output | Section |
|---|---|---|---|---|---|
| Screening | Screening guidance | The criteria are confirmed | Objectives, confirmed criteria, search concepts, candidate titles and abstracts | Review focus, definitions, include and exclude categories, tie-breakers; the Screening Agent places them before the criteria in every screening prompt | Screening guidance |
| Extraction | 1. Schema | Included papers are available | Objectives, requested fields, sample of included papers | The fields the Extraction Agent fills, with names, types, options and descriptions | Extraction schema |
| Extraction | 2. Coding rules | The schema is confirmed | Objectives, confirmed schema, included papers | Per-field rules deciding which option a borderline value is coded to; the Extraction Agent places them before the paper text in every extraction prompt | Extraction coding rules |

## Shared principles

- Ground every statement in the review's objectives and the confirmed criteria or schema. The candidate records show which kinds of work the review will meet; they are a map of the territory, and no rule targets, quotes or describes a single record.
- Judge records by their own work: what the study did, studied and reports. Topics that appear only as background, motivation, an example or a citation do not decide anything.
- Write categories, not keywords. Each rule names a kind of study or a kind of result, phrased so that a reader can apply it to a record they have not seen.
- Read the criteria through the review's evidence setting. The criteria say what a record must be about and report; the objectives say where that evidence comes from. Every review collects its evidence from a setting the objectives imply: natural populations, outbreaks and field samples when the objectives describe outbreaks, populations or surveillance; real users and deployments when they describe practice; evaluations of the tool, benchmarks included, when they describe how well a tool performs. A criterion phrased without a setting ("vector parameters", "transmission data", "evaluation") is read inside that setting. Work that produces the same kind of result outside the setting, such as laboratory experiments that reproduce transmission under controlled conditions, is near-miss work for the exclude list, unless an objective asks for it. Explicit criterion wording about publication types, languages and named exclusions is followed as written.
- Exclusion rests on what the record's own work is, never on what its abstract leaves out. A record is excluded when its work is positively of an excluded kind (it is a literature review; it uses no large language model; its setting lies outside the domain). A record whose abstract omits a required feature, such as the evaluation of a system the authors built, stays plausibly eligible.
- Use plain, declarative English in short lines. Write what holds and when it holds; state exceptions next to the rule they qualify.

## Screening guidance

The guidance has four parts, in this order. Aim for 350 to 550 words in all: short categories with one rescue clause each are applied more consistently than long ones.

1. **Review focus.** One paragraph. First sentence: what the review collects and what it is for, taken from the review's objectives. Second sentence: when a record is relevant, stated as what the record's own work does, what kind of results it reports, and the setting those results come from ("…reports, estimates or models population-level quantities about the pathogen, or studies it in naturally infected or exposed people, hosts or vectors"). Use the objectives' terms for the quantities, outcomes or phenomena the review collects.
2. **Definitions.** Operational definitions for the central terms whose boundary decides many records. Define a term by what the record says or does ("counts as a large language model when the record calls it one or names a generative language model, for example …"), include common variants and named examples, and say how to treat records that describe the thing without naming it, usually as plausibly eligible. Add a definition for any qualifier that must be studied rather than merely present ("the interaction must be something the record studies; having a user interface is not enough on its own"). Two to four definitions are typical; write none when the criteria's terms are unambiguous.
3. **Include when the record's own work reports or will clearly produce at least one of:** six to ten categories that together cover every objective of the review and every kind of eligible work that recurs among the candidates. Build the categories from the candidates rather than by restating the criteria: group the records that are eligible under the focus and its setting by the concrete task, data or study type they share, and name each group with its typical forms and examples ("evaluation of an LLM on medical question answering, benchmarks or licensing-exam questions"; "estimates such as reproduction number, serial interval …"). Write each category as a noun phrase that completes the lead-in. Where a kind of work also exists outside the review's setting, the category names the setting ("genetic analysis of strains from human cases, outbreaks or naturally infected hosts"; "prevalence in field-sampled hosts or vectors").
   - Cover the activities that serve the domain as well as its core tasks: training and education of the domain's professionals, studies of how practitioners, patients or users use or perceive the tools, and research that uses the tool as an instrument, unless the criteria exclude them.
   - Add a category for outcome-defined work when the review asks how something changes people or practice ("effects of LLM assistance on people's writing, programming, learning, decisions or work").
   - Include a category for eligible work whose abstract under-describes it, phrased without conditions on what the abstract shows (for example "new LLM systems the authors built, even when the abstract does not yet describe the evaluation").
   - When the objectives accept reviews, include reviews that cover the review's subject.
   - Give every quantity, outcome or kind of result that the criteria or objectives name its own category, spelled out in the forms it takes among the candidates. Broad names need their forms written out: severity covers case fatality, hospitalisation, and the frequency or risk of severe, neurological, pregnancy-related or congenital outcomes among the affected population; performance covers accuracy, agreement and error analyses.
   - Add a category for studies of the review's subjects in their natural setting (affected people, field-sampled hosts, real users) that report findings on the review's topic even without summary statistics.
4. **Exclude when the record's own work is:** five to eight categories of near-miss work: the kinds of records among the candidates that share the review's vocabulary but whose own work cannot supply what the review collects. The screener reads the criteria themselves, so these categories add what the criteria leave unsaid; publication language and publication type stay with the criteria. Typical near-miss kinds are:
   - mechanism-only work (molecular, structural, cellular or immunological mechanisms) and laboratory-experimental work;
   - development or evaluation of treatments, vaccines, drugs, diagnostic tests or assays;
   - clinical care or management without the population-level data the review collects;
   - knowledge, attitude or perception surveys, and policy, preparedness, economic or health-system analyses without the review's quantities;
   - analyses limited to laboratory material, reference data or constructs rather than the review's natural setting;
   - reviews, editorials and commentaries limited to such topics;
   - for other domains: model-centric research where the review's setting is only an example, applications outside the review's domain, and models of a different kind than the review covers.

   Every exclude category applies a confirmed exclusion criterion, or the focus, to a concrete kind of work; a kind of work the criteria and the focus leave eligible stays off this list, and borderline kinds are left to the tie-breakers. Write each category as a noun phrase that completes the lead-in, naming the kind of work and, where useful, examples of it, and end it with the condition that would rescue it ("…, without data from naturally infected or exposed people"; "…, unless it also reports case counts or prevalence in a population"). The rescue condition names evidence from the review's setting that the excluded work by its nature lacks ("…, without data from naturally infected or exposed people, hosts or vectors"; "…, unless it also reports case counts or prevalence in a population"). Results that the excluded work itself produces, such as transmission or competence results from laboratory experiments or scores on a laboratory task, leave it excluded, so a rescue condition never lists them. Keep each rescue condition to one short clause.

   Then check every exclude category against every include category. A record whose own work reports a kind of result that an include category lists stays included, so each exclude category is phrased to leave such records out of its reach: name the work by what it lacks ("clinical management or treatment without data on outcome frequency, severity or risk in the population") rather than by its topic ("clinical outcome studies"). Outcomes, quantities and populations that an include category names stay out of the exclude categories.

Close with the tie-breakers the review needs, typically:
- a review, perspective or position paper is kept when it also reports results from its own study, when the criteria otherwise exclude such publications;
- when no abstract is available, judge from the title and keep the record unless the title itself shows an excluded publication type or a topic outside the review.

**Review checklist.** A draft is checked against these points, one by one, and corrected where it falls short:
1. The review focus names the setting the review's evidence comes from.
2. Every quantity, outcome or kind of result that the criteria or objectives name has its own include category with its forms written out (severity with its outcome forms, for example).
3. One include category covers findings from the review's subjects in their natural setting, even without summary statistics.
4. Every include category traces to an objective or criterion and sits inside the review's setting; work of the same kind outside the setting has an exclude category.
5. Exclude categories hold near-miss kinds of work rather than restated criteria; each traces to an exclusion criterion or to the focus and its setting.
6. Each rescue condition is one short clause naming evidence from the review's setting that the excluded work lacks.
7. No exclude category reaches a record that an include category lists, and no category contradicts a criterion's explicit wording.
8. The guidance runs to 350–550 words.

## Extraction schema

1. Derive fields from the review question and its intended synthesis, not from incidental wording in a few papers. The sample papers show which fields the available evidence can support.
2. Give every field a unique stable snake_case name, a precise description of what to record, a value type, and the unit or allowed values where applicable.
3. Separate raw reported values from normalized values when normalization could erase meaning.
4. Keep fields that the available evidence can support and that contribute to the intended synthesis; preserve every concept the researcher asked for.
5. Leave bibliographic metadata (title, authors, year, identifiers, source, links) to the system.

## Extraction coding rules

Coding rules settle how borderline values are coded so that every paper is coded the same way. The Extraction Agent already reads the schema, so a rule that restates a field description adds nothing; each rule decides a case the description leaves open. Write rules only for fields whose values need judgment: categorical fields with options, yes/no fields, classifications, and fields whose boundary the schema description leaves open. Leave fields of directly reported values (counts, dates, names) to the schema description unless the sample papers show a recurring ambiguity.

For each field that needs rules, work through four questions.
1. **Whose label decides?** Code an option when the authors present their own work in that option's terms, not when a coder could reclassify the method into it. Name the methods that resemble an option but that authors usually present under another name, and send them to Other (for example "only when the authors present their model as a branching process with an offspring distribution; renewal-equation, Hawkes or other self-exciting models and likelihood estimation of reproduction numbers are coded Other"). This holds even when the schema description lists such methods under the option: the authors' presentation is what a coder can verify.
2. **Which paper terms map to which option?** For every option, list the terms papers use for it that do not contain the option's own words ("isolation of cases or contacts is coded Quarantine"; "community, health-care and funeral transmission between people is coded Human to human (direct contact)"). Route measures that fit no specific option to Other, and list the common ones (generic control measures, lockdowns, travel restrictions, surveillance, awareness campaigns).
3. **When is a broad option too broad?** Options with broad names (behaviour, treatment, hospital, care, setting) apply only when the study represents that mechanism as its own component ("Behaviour changes only when the model explicitly represents a change in contact or protective behaviour"; "Hospitals only when the model has a separate hospital component"). A parameter that is only fitted or allowed to vary over time is not an intervention or mechanism.
4. **What is implied by the study itself?** Code what the study's own model, measurement or implementation represents, even when the authors do not name it: a model of person-to-person spread represents a human-to-human route. What the study only mentions, cites or discusses stays uncoded; keep options that need explicit representation (a separate airborne route, sexual transmission) for studies that represent them explicitly.

Also:
- For yes/no fields, state the evidence that makes the answer yes and list the look-alikes that stay no ("available on request", "data-only repositories", "scripts that only reproduce figures").
- Say when the field stays empty (for example when the study models no intervention).
- When the review spans several subjects that code differently (pathogens, populations, domains), give a common part and a short part per subject.

Keep each rule to one line. Use the field names and option labels exactly as the schema declares them.
