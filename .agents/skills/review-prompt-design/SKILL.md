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
- Read the criteria through the review's evidence setting. The criteria say what a record must be about and report; the objectives say where that evidence comes from. Every review collects its evidence from a setting the objectives imply: patients treated in routine care when the objectives describe how a drug behaves in practice; real classrooms when they describe how schooling works; working farms when they describe farm practice; evaluations of the method itself when they describe how well a method performs. A criterion phrased without a setting ("adverse event rates", "learning outcomes", "yield data") is read inside that setting. Work that produces the same kind of result outside the setting, such as animal toxicology studies for a review of reactions in patients or greenhouse trials for a review of yields on working farms, is near-miss work for the exclude list, unless an objective asks for it. Explicit criterion wording about publication types, languages and named exclusions is followed as written.
- Exclusion rests on what the record's own work is, never on what its abstract leaves out. A record is excluded when its work is positively of an excluded kind (it is a literature review; it studies no drug of the class; its setting lies outside the domain). A record whose abstract omits a required feature, such as the outcome measure of a program the authors ran, stays plausibly eligible.
- Use plain, declarative English in short lines. Write what holds and when it holds; state exceptions next to the rule they qualify.

## Screening guidance

The guidance has four parts, in this order. Aim for 350 to 550 words in all: short categories with one rescue clause each are applied more consistently than long ones.

1. **Review focus.** One paragraph. First sentence: what the review collects and what it is for, taken from the review's objectives. Second sentence: when a record is relevant, stated as what the record's own work does, what kind of results it reports, and the setting those results come from ("…reports frequencies or risks of adverse reactions to the drug class, or describes such reactions in patients treated in routine care"). Use the objectives' terms for the quantities, outcomes or phenomena the review collects.
2. **Definitions.** Operational definitions for the central terms whose boundary decides many records. Define a term by what the record says or does ("counts as a biosimilar when the record calls it one or names an approved biosimilar product, for example …"), include common variants and named examples, and say how to treat records that describe the thing without naming it, usually as plausibly eligible. Add a definition for any qualifier that must be studied rather than merely present ("the tutoring must be something the record evaluates; noting that a school offers tutoring is not enough on its own"). Two to four definitions are typical; write none when the criteria's terms are unambiguous.
3. **Include when the record's own work reports or will clearly produce at least one of:** six to ten categories that together cover every objective of the review and every kind of eligible work that recurs among the candidates. Build the categories from the candidates rather than by restating the criteria: group the records that are eligible under the focus and its setting by the concrete task, data or study type they share, and name each group with its typical forms and examples ("evaluations of a reading program on standardized test scores, grades or teacher ratings"; "estimates such as incidence rate, reporting odds ratio, time to onset …"). Write each category as a noun phrase that completes the lead-in. Where a kind of work also exists outside the review's setting, the category names the setting ("yield measurements from working farms"; "pest counts from commercial fields").
   - Cover the activities that serve the domain as well as its core tasks: training and education of the domain's professionals, studies of how practitioners or recipients use or perceive the review's subject, and research that uses it as an instrument, unless the criteria exclude them.
   - Add a category for outcome-defined work when the review asks how something changes people or practice ("effects of a feedback program on students' learning, attendance or teachers' practice").
   - Include a category for eligible work whose abstract under-describes it, phrased without conditions on what the abstract shows (for example "new instructional programs the authors designed, even when the abstract does not yet describe the evaluation").
   - When the objectives accept reviews, include reviews that cover the review's subject.
   - Give every quantity, outcome or kind of result that the criteria or objectives name its own category, spelled out in the forms it takes among the candidates. Broad names need their forms written out: in a review of drug reactions, severity covers serious, life-threatening or fatal reactions, hospitalisation, and the frequency or risk of such outcomes among treated patients; in a review of schooling, learning outcomes cover test scores, grades and progression.
   - Add a category for studies of the review's subjects in their natural setting (treated patients, real classrooms, working farms) that report findings on the review's topic even without summary statistics.
4. **Exclude when the record's own work is:** five to eight categories of near-miss work: the kinds of records among the candidates that share the review's vocabulary but whose own work cannot supply what the review collects. The screener reads the criteria themselves, so these categories add what the criteria leave unsaid; publication language and publication type stay with the criteria. Typical near-miss kinds are:
   - work on mechanisms rather than on the outcomes the review collects (receptor or cell studies in a review of drug reactions), and controlled experiments outside the review's setting;
   - development or evaluation of a product or method (a new formulation, an assessment instrument, a crop variety) without the review's outcomes;
   - descriptions of practice (individual case management, lesson plans, farm-management advice) without the quantities the review collects;
   - surveys of attitudes or opinions, and policy or economic analyses, without the review's quantities;
   - analyses limited to materials outside the review's setting (reference samples, simulated data, research plots);
   - reviews, editorials and commentaries limited to such topics;
   - work in which the review's subject appears only as an example, applications outside the review's domain, and things of a different kind than the review covers.

   Every exclude category applies a confirmed exclusion criterion, or the focus, to a concrete kind of work; a kind of work the criteria and the focus leave eligible stays off this list, and borderline kinds are left to the tie-breakers. Write each category as a noun phrase that completes the lead-in, naming the kind of work and, where useful, examples of it, and end it with the condition that would rescue it ("…, without data from patients treated in routine care"; "…, unless it also reports reaction frequencies in a treated population"). The rescue condition names evidence from the review's setting that the excluded work by its nature lacks. Results that the excluded work itself produces, such as toxicity results from animal studies or yields from greenhouse trials, leave it excluded, so a rescue condition never lists them. Keep each rescue condition to one short clause.

   Then check every exclude category against every include category. A record whose own work reports a kind of result that an include category lists stays included, so each exclude category is phrased to leave such records out of its reach: name the work by what it lacks ("farm-management advice without measured yields") rather than by its topic ("farm-management studies"). Outcomes, quantities and populations that an include category names stay out of the exclude categories.

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

Coding rules settle the borderline cases of a confirmed schema so that every paper is coded the same way. The Extraction Agent reads the schema and the rules together. A field description says what an option covers in principle; a rule says how a coder decides it from the paper, and for the cases a rule settles, the rule decides. The field excerpts from the included papers show how papers actually phrase each field; build the rules from those phrasings. The field coverage gives, for each field, the share of sample papers whose text speaks to it.

Write only rules that settle a case the description leaves to judgment. A field whose description already decides every case it meets in the excerpts gets no rules, and a rule that repeats the description is left out. Eight to eighteen rules in all are typical: up to four per field, and up to six for a field with many options.

Rules move values; they keep values. Every value a study supports belongs somewhere, so:
- Prefer mapping rules, which say which option a phrasing belongs to ("one-to-one sessions and small-group catch-up are coded Tutoring"); they add correct values. Use a restricting rule ("only when …") for an option only where the excerpts show a recurring confusion, at most one per option, and let it name where the other cases go ("…; other cases are coded with the option whose description fits them, or Other").
- A field with an Other option has one rule listing what goes to Other.
- A field that classifies the review's central object, which every included paper has (the intervention in a review of trials, the crop in a review of yield studies), is always coded: Other when no named option fits. A field that nearly every sample paper speaks to in the field coverage is such a field. A field stays empty only when the study lacks the thing it classifies; a description's "unreported or unspecified" refers to a study that lacks it, and a study that has it is coded with the best-supported option.
- For fields that collect several values, code every option the study represents; the rules sharpen the boundaries between options and leave clear cases to the description.
- The preamble states the shared conventions in one or two sentences: code what the study's own work represents, and code every option it represents.

**Decide first.** Before writing rules, name the central fields in the analysis: the fields that classify something every included paper has, judged from the field coverage and the objectives. The rules and the preamble then code them for every paper.

For each field that needs rules, work through these questions.
1. **What do the description's examples settle?** Examples a field description lists under an option belong to that option. For methods, models or designs the description leaves unmentioned, code by the authors' presentation of their own work: an option applies when the authors use its name or a standard abbreviation of it, and work the authors name otherwise goes to the option whose description covers it, or to Other. An option that claims a design (cluster-randomized, multilevel, longitudinal) needs both the authors' name for it and the design itself ("Cluster-randomized only when the authors call the trial cluster-randomized and randomize whole schools or classes; a trial that randomizes pupils within schools is coded Randomized").
2. **Which phrasings map to which option?** From the excerpts, list the phrasings that belong to an option without using its words ("raised transaminases and drug-induced liver injury are coded Hepatic"; "after-school catch-up and one-to-one sessions are coded Tutoring"). When an option's label contains words that papers use for another option, say where those words go. Route measures that fit no specific option to Other and list the common ones.
3. **When is a broad option too broad?** Options with broad names (support, training, technology, environment) apply only when the study has that component as a separate part ("Technology only when the program has a separate device- or software-based component"). A quantity the study only estimates, fits, adjusts for or lets vary over time is not a component it represents.
4. **What does the study itself represent?** Code what the study's own model, measurement or implementation represents, even when the authors leave it unnamed: a trial that compares the drug with an inactive pill represents a placebo comparator. What the study only mentions, cites or discusses stays uncoded.

For yes/no fields, state the evidence that makes the answer yes and list the look-alikes that stay no (for a field on whether a protocol is publicly available: "available from the authors", "planned for publication", "summary only in the methods"). Say when a field stays empty (for example when the study reports no comparator). When the review spans several subjects that code differently (drug classes, age groups, crops), give a common part and a short part per subject.

Keep each rule to one line. Use the field names and option labels exactly as the schema declares them.

**Review checklist.** A draft of coding rules is checked against these points, one by one, and corrected where it falls short:
1. Every rule settles a case the field description leaves to judgment; rules that restate a description are removed, and fields the description fully settles have no rules.
2. Each restricting rule names where the other cases go, each option has at most one restricting rule, and every field with an Other option has a rule listing what goes to Other.
3. Fields that classify the review's central object are coded for every paper that has it, Other when no named option fits; the preamble and rules leave a field empty only when the study lacks the thing it classifies, and neither restates "use an empty value when unreported" as a general convention.
4. Examples a field description lists under an option stay with that option; other work is coded by the authors' own presentation, no rule widens an option with "or describes …" clauses, and design options require both the name and the design.
5. Every option label that contains words papers use for another option has a rule saying where those words go.
6. Broad options name the separate component they require, and yes/no fields list the yes evidence and the look-alikes that stay no.
7. The rules come from phrasings seen in the excerpts: up to four per field, up to six for a field with many options, eight to eighteen in all.
