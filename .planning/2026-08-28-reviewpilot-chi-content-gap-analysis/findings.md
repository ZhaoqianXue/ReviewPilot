# Findings & Decisions

## Requirements
- Compare current `writing/CHI2027/ReviewPilotSubmission/main.tex` with the paper at DOI 10.1145/3772318.3791505.
- Identify missing writing content, not formatting differences.
- Return concrete, prioritized gaps; do not edit the manuscript.

## Research Findings
- The supplied DOI resolves to the ACM paper titled “Characterizing User-Reported Risks across LLM Chatbots.”
- The interactive ACM session passed the security verification and loaded the PDF.
- The in-app browser cannot export PDF-tab content through `tab.content.export()`; the PDF page exposes a `pageAssets` capability that may provide the observed PDF asset.
- The published artifact is 26 pages and uses the standard two-column ACM proceedings layout.
- Its first page contains a substantive abstract, CCS Concepts, keywords, and an Introduction that starts immediately with adoption scale, prior system-centered risk work, and the unresolved user-centered gap.
- The abstract states a concrete corpus/source (Reddit discussions), comparison scope (seven major LLM chatbots), analytical framework (NIST AI Risk Management Framework), differentiated findings by chatbot, cross-risk trade-offs, and a human-centered risk-mitigation implication.
- The PDF viewer exposes no inventory assets, so its `pageAssets` capability cannot directly bundle the PDF.
- The viewer download control did not create a matching local PDF and left the PDF view unchanged.
- The published paper is CHI 2026 Article 602, published April 13, 2026.
- An author-matched arXiv preprint exists as arXiv:2509.08912 under the earlier title “Towards Trustworthy AI: Characterizing User-Reported Risks across LLMs ‘In the Wild’.”
- The official DOI page is indexed with substantive full-text sections, including Methods, Results, Discussion, and Conclusion, so the comparison can remain grounded in primary and author-provided sources without a local ACM PDF.
- Indexed structure confirms a three-stage Methods pipeline with an overview figure, explicit research-question Results, a multi-part Discussion that interprets findings against system-centered views, and a bounded Conclusion with implications for designers, developers, and policymakers.
- The author-matched arXiv PDF is 19 pages and extracts to roughly 17,269 words.
- Its top-level structure is Abstract; Introduction; Related Work; Methods; RQ1 Results; RQ2 Results; Discussion; Limitations and Future Work; Conclusions; References; Appendix.
- Its Discussion contains at least three thematic subsections, and the Results contain detailed risk-specific subsections rather than one undifferentiated result block.
- Current ReviewPilot has an empty abstract, an Introduction body, an empty Related Works body, a substantial Methods body, an empty Results body, an empty Discussion body, and no Conclusion, Limitations/Future Work, References, CCS Concepts, or Keywords content.
- Current ReviewPilot Methods names architecture and modules but contains no evaluation study design, research questions, datasets/tasks, baselines, metrics, participant/procedure description, or analysis protocol.
- The published Introduction explicitly states RQ1 and RQ2, previews findings against each RQ, previews the Discussion, and ends with three contributions that correspond to reported evidence and implications.
- Its Related Work has two argumentative subsections: a system-centered risk taxonomy and user-reported LLM risks; each culminates in the exact gap the study addresses.
- Its Methods begins with a staged overview tied to Figure 1 and includes data-source rationale, collection dates/sources/keywords, raw and filtered sample counts, preprocessing rules, extraction schema and prompts, a 200-item human-coded ground truth, inter-coder reliability, model selection rationale, classification and text-similarity validation metrics, bottom-up topic modeling, human consensus labeling, knowledge-graph construction, and an ethics statement.
- Compared with that benchmark, ReviewPilot’s current Methods is a system description, not yet a reproducible CHI evaluation method: it explains how modules should work but does not state what data were used to test them, what ground truth exists, how outcomes are measured, or how human judgment is validated.
- The published Discussion is evidence-linked and organized around four interpretive jobs: hierarchy of user-perceived risks; technical design versus lived experience across products; utility-risk trade-offs; and concrete design/policy implications.
- Its Limitations section names three source-specific threats and corresponding future work: Reddit demographic coverage, bias/error from LLM annotation and topic modeling, and Reddit API ranking/coverage bias.
- Its Conclusion restates the study, methods, central quantitative and qualitative findings, and stakeholder implication without introducing new evidence.
- ReviewPilot’s Introduction currently claims that multiple case studies demonstrate reduced manual effort and preserved transparency/user control, but the manuscript contains no case-study protocol, no Results text, and no measurements supporting that claim.
- Current ReviewPilot contains no figures, tables, citations, or bibliography commands in `main.tex`; only the empty Results and Discussion headings match those terms.
- Current authored prose is approximately 449 words in Introduction plus 1,711 words in Methods; Related Works, Results, and Discussion contain only their headings, and Appendix is empty.
- The author-matched reference contains roughly 12,109 words before References. This is not itself a target length, but it confirms ReviewPilot is still an early structural draft rather than a submission-complete manuscript.
- The writing tree contains no authored ReviewPilot bibliography; the existing AAAI `custom.bib` was previously identified as template sample data.
- Fixed-string audit confirms current ReviewPilot has 0 figures, 0 tables, 0 citations, 0 bibliography commands, and 5 displayed equations.
- The arXiv HTML confirms the reference paper’s explicit RQs, evidence-linked contribution statements, staged method overview, dataset counts, human-coding protocol, inter-coder reliability, and model evaluation metrics.
- ReviewPilot’s exact current layout is: empty abstract at lines 20–21; four-paragraph Introduction at lines 28–34; empty Related Works at lines 36–37; system-description Methods at lines 39–112; empty Results at 114–115; empty Discussion at 117–118; empty Appendix after line 120.
- ReviewPilot’s contribution claim “multiple case studies” and “reduce the manual effort” appears at line 34, but there is no evaluation question, case-study definition, comparison condition, workload metric, or supporting result elsewhere in the manuscript.
- The current Methods contains multiple empirical-sounding thresholds and design choices (e.g., 0.80 deduplication, 0.7/0.5 PDF matching, memory retrieval confidence) without validation evidence or rationale tied to data.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Use the DOI-hosted PDF as the primary reference | It is the exact published artifact supplied by the user. |
| Use the migrated CHI `main.tex` as the current manuscript authority | It contains the same completed text as the AAAI source and is the target submission version. |

## Section-by-Section Gap Map

| Content area | Published CHI benchmark | Current ReviewPilot | Missing content |
|---|---|---|---|
| Abstract | Problem, exact gap, data/method, concrete findings, implication | Empty | Entire substantive abstract; must be written after results exist |
| Introduction | Cited context, exact prior-work gap, two RQs, method/result preview, three evidence-aligned contributions | Four uncited paragraphs and three broad contribution claims | Citations, explicit research/evaluation questions, bounded claims, result-backed contribution wording |
| Related Work | Two synthesized streams, each tied to the study gap | Heading only | Systematic-review tools, LLM-assisted review automation, agentic/mixed-initiative systems, transparency and human-control literature, and a clear distinction from prior tools |
| System description | Staged overview figure plus rationale for every method component | Detailed descriptions of modules and formulas | One architecture/workflow figure, design goals, module-choice rationale, implementation/model/prompt/version details, and a reproducibility/artifact statement |
| Evaluation methods | Data rationale, collection protocol and counts, ground truth, human coding, reliability, model validation, metrics, ethics | Absent | Evaluation RQs, tasks/cases, datasets and gold labels, baselines, metrics, procedure, annotators/users, analysis plan, model settings, cost/latency protocol, ethics |
| Results | RQ-organized quantitative and qualitative evidence with figures, tables, statistical tests, examples | Heading only | End-to-end and module-level quantitative results, case-study evidence, comparison/ablation results, qualitative findings, failure cases |
| Discussion | Four evidence-linked interpretations and design/policy implications | Heading only | Interpretation rather than repetition, relation to prior work, human-AI design implications, trade-offs, applicability boundary |
| Limitations/Future Work | Three method-specific threats plus remedies | Absent | Database/API coverage, gold-label subjectivity, model/prompt drift, domain generalizability, access/copyright, privacy, cost, failure recovery, and user-study limitations |
| Conclusion | Study, method, findings, and stakeholder implication | Absent | Bounded conclusion supported by actual results |
| Scholarly apparatus | CCS, keywords, citations, references, figures/tables, appendix evidence | Empty/missing except five equations | Authored CCS/keywords, real bibliography, system/PRISMA/evaluation visuals, detailed prompts/configurations/case materials in appendix |

## Claim-Evidence Gaps in Current ReviewPilot

| Current claim | Evidence required but absent |
|---|---|
| “complete systematic review pipeline” | Scope definition, demonstrated task completion, PRISMA flow and counts, unsupported-step boundaries |
| “reliable intent detection” | Intent dataset, labels, metric, comparator, error analysis |
| “memory system that improves with use” | Longitudinal or repeated-project comparison, retrieval relevance, no-memory ablation, contamination/privacy analysis |
| “reduce the manual effort” | Manual baseline, time/actions/workload measure, sample size, statistical or case-level evidence |
| “maintaining transparency and user control” | Operational definitions, interface mechanisms, user/task evidence, observed override/recovery behavior |
| “multiple case studies spanning diverse domains” | Named domains, protocols, inputs, outputs, ground truth, per-case results, cross-case synthesis |
| PRISMA compliance | Identification/screening/eligibility/inclusion accounting, protocol mapping, reproducible exclusion reasons, flow diagram |

## Evaluation Content Needed to Support the Current Scope

- Search/query generation: expert or librarian comparison, known-set retrieval/coverage, query validity by database.
- Screening: human gold labels, precision/recall/F1 with special attention to false exclusions, inter-rater agreement, error taxonomy.
- PDF collection: acquisition success rate, document-title match precision, failure reasons by source.
- Structured extraction: field-level accuracy/completeness/hallucination rate against expert annotations.
- Categorization: label agreement, coverage/coherence, single-versus-multiple-label behavior.
- End-to-end workflow: completion rate, time/interaction burden, provenance/reproducibility, recovery from interruption.
- Human-centered claims: researcher study or rigorous field/case evaluation of usability, trust, transparency, control, correction, and workload.
- Comparisons/ablations: manual workflow, existing review/deep-research tools, non-agentic pipeline, no-memory condition, single-source search, and other components directly tied to claimed contributions.
- Efficiency and boundary evidence: latency, token/API cost, scale limits, failure modes, model and prompt sensitivity, domain transfer.

## Priority

1. Critical: define evaluation questions and collect the evidence needed for the already-stated contribution claims.
2. Critical: write Evaluation Methods and Results; without these, this is a system specification rather than a research paper.
3. Major: write Related Work with real citations and sharpen novelty relative to existing systematic-review and deep-research tools.
4. Major: write Discussion, limitations, design implications, and a bounded conclusion.
5. Major: either validate the memory/improvement, manual-effort, transparency/control, and PRISMA-compliance claims or narrow them.
6. Finalization: write the abstract last, then add CCS concepts, keywords, references, figures/tables, and appendix material.

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| PDF-tab content export command is unsupported by the in-app browser | Inspect and use the page-assets capability instead of repeating the unsupported export. |
| The PDF viewer exposes an empty page-assets inventory | Use the viewer’s own download control rather than asset bundling. |
| Viewer download did not create a local file | Extract the PDF text through the viewer’s selectable text layer instead. |
| Selecting all text in the PDF viewer returned an empty clipboard | Use the author-matched arXiv full text and the official DOI HTML/full-text index for semantic extraction. |
| First structural-count command used regex mode with unescaped braces and produced parse errors | Do not use those counts; rerun with fixed-string matching before reporting figure/table/citation totals. |
| Opening the DOI landing page through the web fetcher returned 403 even though search indexing exposed its content | Use the successfully loaded ACM browser PDF plus the author-matched arXiv full text; cite the DOI and arXiv directly. |

## Resources
- https://dl.acm.org/doi/pdf/10.1145/3772318.3791505
- https://doi.org/10.1145/3772318.3791505
- https://arxiv.org/abs/2509.08912
- `writing/CHI2027/ReviewPilotSubmission/main.tex`
