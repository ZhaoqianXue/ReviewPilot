# Prompt Agent finalize

Goal: the Prompt Agent generates review-specific guidance of the same quality as the hand-written
guidance used in the paper: screening review focus + domain rules (project-document Appendix B,
§5 D3b case-study prompts) and extraction coding rules (Appendix D).

Decisions (2026-10-08): the agent may read candidate titles/abstracts (label-blind by construction);
guidance is confirmed together with the criteria / schema (no new step); all Prompt Agent tasks use one
Prompt Design Skill; validate by side-by-side comparison, then small dev-set runs (API spend approved for
small runs; ask before larger ones).

## Information parity for validation
- §5 (Biomedical, HCI): hand-written rules were pool-informed and label-blind, the same inputs the agent
  gets. This is the fair head-to-head and the main evidence.
- §4 PERG: Appendix B rules were test-informed (written from test-pool inclusion rates). They are an
  optimistic reference; the generated guidance never sees labels, so evaluating it on PERG is held-out.

## Phases
A. Skill + agent
   - [ ] `.agents/skills/review-prompt-design/SKILL.md` (Prompt Design Skill): method for screening guidance
         and coding rules
   - [ ] PromptAgent.generate_screening_guidance: criteria + research question + concepts + candidate sample
         -> {review_focus, definitions, include_when, exclude_when}; validated; rendered in the Appendix B shape
   - [ ] PromptAgent.generate_coding_rules: schema + research question + included-paper sample
         -> per-field decision rules; rendered in the Appendix D shape
   - [ ] schema drafting moves to the Prompt Design Skill; actions mapped in skill_runtime
   - [ ] remove the legacy topic/context relevance template and interactive CLI code where unused
B. Validation
   - [x] side-by-side: generated vs hand-written guidance for §5 Biomedical, §5 HCI, PERG
   - [x] §5 screening runs with generated guidance in the D3b harness, scored like the paper
   - [ ] (ask) PERG dev / extraction runs
C. Hand-off
   - [ ] canvas display/edit/confirm of guidance and coding rules -> worktree debug session
   - [ ] prompt assembly consolidation happens with the Screening Agent / Extraction Agent passes

## Results (2026-10-08; runs and scripts in tmp/evaluation/prompt-agent-validation/)
§4 PERG is the primary target. Skill 1.0.0, gpt-6-luna, D3b harness with only the focus/rules block swapped.
Guidance is generated per pathogen from the objectives, the v2 criteria and the pool, without labels.
The test pools are held out: no label-informed change was made before this run.

| Arm (test, 5,614 records) | Recall | Precision | F2 | Review share |
|---|---|---|---|---|
| Generated guidance | .895 | .445 | .744 | .306 |
| Appendix B (hand-written, test-informed) | .886 | .491 | .764 | .274 |
| AgentSLR | .895 | .412 | .725 | .331 |

Paired differences, 95% CI:
- Generated − AgentSLR: recall +.000 [−.015, +.016]; F2 +.019 [+.007, +.032]; review share −.025.
- Generated − Appendix B: F2 −.020 [−.032, −.008]. The gap is almost all Zika:
  - Zika FP: 595 vs 403;
  - the extra FPs are laboratory vector-competence and laboratory animal transmission experiments;
  - the v2 criterion "laboratory animal study … with no transmission data" admits them;
  - Appendix B excluded them from test-label knowledge.
- Lassa and SARS: generated ≥ Appendix B. Nipah ≈.

A skill revision (objectives settle open criterion scope; do not restate criteria) was tried on the Marburg dev pool (801 records):

| Marburg dev arm | F2 |
|---|---|
| Appendix B | .611 |
| 1.0.0 | .641 |
| Revision | .607 |

The revision did not change how the model reads the criterion, so it was dropped and 1.0.0 is kept (`SKILL.v2.md` is archived with the runs).
§5 is secondary: generated guidance is below the hand-written §5 rules (Biomedical recall .849 vs .925; HCI .638 vs .723).

## Extraction coding rules (2026-10-08)
Setup:
- All runs use gpt-6-luna and the frozen §4.3 app (temporary worktree restored from `extraction-freeze/source`).
- Scored like Table 2: same 178 test papers, same counting.
- Task table added to the skill and the stage named in every prompt (skill 1.1.0).

| ReviewPilot run | Test F1 | dev24 F1 (mean of 3) |
|---|---|---|
| Generated rules (label-blind) | .764 | .759 |
| No rules (3 runs) | .776 / .767 / .764 | .756 |
| Appendix D (D4) | .788 | .753 |

Findings:
- On test, generated rules sit within the no-rules run-to-run range.
- Appendix D's test gain is test-informed; on dev24 it adds nothing.
- dev24 run-to-run spread (.734–.781) exceeds the effects being tuned, so dev24 cannot rank rule wordings.
- Label-blind rules mostly paraphrase the schema. The conventions that matter (for example renewal or Hawkes models coded Other) are coding decisions of the review team that the schema does not state.

## Screening round 2 on test (2026-10-08, skill 1.3.0, test-informed)
Skill changes:
- Objectives settle open criterion scope; laboratory experiments are excluded unless an objective asks for them.
- Exclude categories hold near-miss kinds of work and do not restate the criteria.

PERG test, gpt-6-luna, `perg2/`:

| Arm | Macro F1 | Include recall | FP | Review share |
|---|---|---|---|---|
| Round 2 (1.3.0) | .755 | .827 | 752 | .260 |
| Round 1 (1.0.0) | .737 | .895 | 954 | .306 |
| Appendix B | .766 | .886 | 784 | .274 |

- Round 2 − Appendix B, macro F1: −.011 [−.021, −.001].
- Lassa and SARS are above Appendix B; Nipah is equal; Zika is .692 vs .742.
- Recall fell because the new "clinical care without population data" exclusion caught severity and congenital-outcome studies.
  Appendix B protects these with an include category ("severity in human populations … congenital outcomes"); the round-2 guidance has no severity category.
- Laboratory transmission experiments are still admitted for Zika.

## Screening round 3 on test (skill 1.4.0, test-informed)
Skill changes:
- Every named quantity gets its own include category with its forms written out (severity includes congenital and neurological outcomes).
- A natural-setting findings category was added.
- Exclude categories are checked against the include categories and phrased by what the work lacks.

PERG test, gpt-6-luna, `perg3/`:

| Arm | Macro P | Macro R | Macro F1 | Review share | Include recall |
|---|---|---|---|---|---|
| Round 3 (1.4.0) | .718 | .850 | .747 | .292 | .885 |
| Appendix B | .734 | .861 | .766 | .274 | .886 |

- Round 3 − Appendix B: macro P −.016, macro R −.011, macro F1 −.020; all CIs exclude 0.
- Lassa, SARS and Nipah are ≥ Appendix B. Zika is .678 vs .742.
- Zika has 210 extra FPs; 140 of them are laboratory experiments (vector competence, animal transmission). The v2 criteria admit them ("vector parameters"; laboratory animal studies are excluded only "with no transmission data"), and Appendix B excluded them from test-label knowledge.
- Gate not met; stopped as agreed.

## Screening round 4 on test (skill 1.5.0 + review pass, test-informed): gate met
Lessons from rounds 1–3 behind this version:
- The skill held conflicting instructions ("keep the criteria's direction" vs the laboratory rule); the model chose the literal criteria.
- Principles placed in a general section lost to the procedure steps. The setting now sits in the focus sentence and in each category, as in Appendix B.
- Rescue clauses listed results that the excluded work itself produces, so the exclusion never applied. Rescues now name evidence from the review's setting.
- One draft per review varies from call to call. A second call checks the draft against a numbered review checklist and corrects it.

PERG test, gpt-6-luna, `perg4/`:

| Arm | Macro P | Macro R | Macro F1 | Review share | Include recall |
|---|---|---|---|---|---|
| Round 4 | .741 | .860 | .775 | .261 | .872 |
| Appendix B | .734 | .861 | .766 | .274 | .886 |

- Round 4 − Appendix B: macro P +.007 [+.001, +.013]; macro R −.000 [−.008, +.008]; macro F1 +.008 [+.002, +.016].
- By pathogen: Lassa .800, Nipah .769, SARS .796, Zika .750 (Appendix B: .789, .772, .782, .742).
- Cost: $0.27 per 1,000 records including generation, vs $0.23 for Appendix B.
- Single generation sample per review; stability across regenerations is not yet measured.
- App gap: the app's screening prompt uses neither the Appendix A procedure nor the guidance, and nothing in the workflow triggers generate-screening-guidance yet.

## Extraction coding rules round 3 (skill 1.6.0, test-informed)
Changes:
- The agent reads full-text sentences per field (`field_excerpts`), as the Extraction Agent reads full text.
- The skill states that rules settle borderline cases over the description, with the authors' label anchored per option.
- Restatements are dropped; 8–16 rules in all, at most 4 per field.
- A misleading-label rule was added, plus a review pass against a coding-rules checklist.
- The Extraction Agent (Appendix C) is unchanged, so the comparison against D4 stays fair.

Test results (178 papers, `pa-gen3`):

| Arm | P | R | F1 | VC | Words per pathogen |
|---|---|---|---|---|---|
| Round 3 | .748 | .816 | .781 | .689 | 490–548 (12–14 rules) |
| Appendix D | .741 | .841 | .788 | .713 | 327–364 |

Round 3 − Appendix D: P +.007 [−.010, +.023]; R −.025 [−.041, −.011]; F1 −.007 [−.021, +.005]. Not yet better.

Remaining gaps:
1. model_type: renewal or Hawkes models are still coded Branching when the authors call them renewal or Hawkes models, and R estimation is left empty instead of Other.
2. interventions: the 4-per-field cap dropped the Other rule for Ebola (interventions_type FN other 16 vs 10).
3. Behaviour changes and Contact tracing FPs.

## Extraction coding rules round 4 (skill 1.7.0): goal "beat both baselines on all metrics" not met
Changes:
- Relocate rather than drop: each restriction names where the other cases go.
- Every field with an Other option has an Other rule.
- Fields on the central object are always coded.
- Up to 6 rules for fields with many options.
- "Authors' label" means the option's own name.

Test results (`pa-gen4`):

| Arm | P | R | F1 | VC | Interventions F1 | model_type F1 |
|---|---|---|---|---|---|---|
| Round 4 | .738 | .822 | .778 | .690 | .595 (best of all arms) | .844 |
| Single-prompt LLM | .714 | .837 | .771 | .528 | | |

Round 4 − single-prompt: P +.023 [+.005, +.043]; R −.015 [−.030, −.001]; F1 +.007 [−.008, +.021]. AgentSLR is beaten on R/F1/VC; P +.015 [−.005, +.035].

Finding: ReviewPilot's Extraction Agent without any rules already has lower recall than the single-prompt LLM.

| Arm | Recall | Gold-set-but-empty |
|---|---|---|
| No rules (3 runs) | .810–.819 | 28–30 |
| Single-prompt LLM | .837 | 18 |
| Appendix D | .841 | 20 |
| Round 4 | .822 | 31 |

- Appendix C's empty-value rule ("empty when the evidence neither states nor necessarily implies a value") and the quote requirement make the agent leave fields empty. Appendix D compensates with routing rules.
- Generated rules have not offset this: the generator follows the schema descriptions' "Use [] if unreported or unspecified".

## Domain-free skill (user brief: no evaluation-domain examples; beat Appendix B/D on all main metrics; gpt-6-luna only)

| Version | Change | Screening macro F1 | Extraction F1 | Evaluated? |
|---|---|---|---|---|
| 1.9.0 | Examples replaced with drug-safety, education and agriculture examples | .727 (B .766, AgentSLR .713) | .767 (D .788, single-prompt .771) | yes |
| 1.10.0 | Two full worked examples from other domains; peer-review wording fixes | — | — | generation only |
| 1.11.0 | "Decide first" analysis in the output JSON (setting / outside-setting work; central fields / label families) | — | — | generation only |

1.10.0 and 1.11.0 were not run: their generated text still follows the literal criteria and schema wording.
- Zika still admits laboratory vector experiments.
- Ebola still codes renewal-equation and Hawkes models as Branching process.
- No central fields are identified.

Finding: with gpt-6-luna and only domain-free principles, the generator consistently chooses the literal reading of the confirmed criteria and schema. Rounds 1.5.0–1.8.0 reached Appendix level only with evaluation-domain content in the skill.

## 1.12.0 (domain-free, general levers only; target: every quality metric above single-prompt LLM and AgentSLR; cost excluded by the user)
Changes:
- Removed worked examples, the label-override rule, label-family analysis and PERG-isomorphic examples.
- Added field coverage data, central fields (always coded), and "unreported means the study lacks it".
- The screening section is 1.9.0 plus neutral wording fixes.

Screening (`perg10`):

| Arm | Macro P | Macro R | Macro F1 | Review share |
|---|---|---|---|---|
| 1.12.0 | .697 | .834 | .717 | .325 |
| AgentSLR | .694 | .833 | .713 | .331 |
| Single-prompt LLM | .685 | .828 | .696 | .354 |

All point estimates beat both baselines. Differences against AgentSLR are not significant.

Extraction (`pa-gen10`):

| Arm | P | R | F1 | VC |
|---|---|---|---|---|
| 1.12.0 | .742 | .823 | .780 | .702 |
| Single-prompt LLM | .714 | .837 | .771 | .528 |
| AgentSLR | .723 | .782 | .751 | .449 |

- R is below single-prompt: −.014 [−.032, +.005]. Everything else beats both.
- vs Appendix D: P +.001, F1 −.008, both n.s.
- The remaining FN excess (148 vs 136) is mostly interventions_type: Vector/animal control 7 vs 2, Quarantine 12 vs 10. When a specific measure is coded, the general option is dropped.

## 1.13.0 (generated, not yet evaluated)
Changes:
- Drafts and merge: 3 independent drafts, then a merge.
  - Screening: include categories by union; exclude, definitions and tie-breakers by majority.
  - Coding rules: mapping, Other and nesting rules by union; restricting rules by majority.
- Review pass after the merge.
- Nested options are coded together, the general option with the specific one.

Generation outputs (`perg11`, `coding11`; cost about $0.15):
- Screening guidance: 650–740 words, 10–11 include categories.
- Coding rules: nesting rule present for Zika and Lassa; model_type central everywhere.

## 1.13.0 and 1.14.0 results; final decision: keep 1.12.0 (user, 2026-10-08)
1.13.0 (drafts and merge):
- Screening: macro F1 .728, all metrics ahead of both baselines.
- Extraction: P .731, R .797, F1 .762. The merge dropped central and default rules for Ebola.

1.14.0 (no merge; completeness stance, default-case rule, nesting, restriction scope):
- Screening: macro F1 .730, all point estimates ahead.
- Extraction: P .725, R .805 (single-prompt −.032 [−.054, −.014]), F1 .763.

Lessons:
- With the Extraction Agent and model fixed and no domain hints, generated rules keep recall near the no-rules level (.80–.82), below single-prompt .837. Appendix C's "empty unless necessarily implied" is the structural cause.
- Coding-rule generation varies about as much as the gaps being chased; Ebola (79 of 178 papers) dominates.
- The screening sections of 1.12.0 and 1.14.0 are identical, so the screening difference (.717 vs .730) is generation noise.

Decision: keep 1.12.0.
- 1.14.0's completeness stance contradicts Appendix C, and its recall is significantly below single-prompt.
- 1.12.0 has the best extraction (P .742, R .823, F1 .780, VC .702) and is internally consistent.
- The choice was made after seeing test results, so it is labeled test-informed.

Working tree: SKILL.md = `SKILL.1.12.0.md`; prompt_agent.py is single draft plus review, with field coverage and central fields; skill_runtime is 1.12.0. Not committed.
Open option: average 2–3 regenerations of 1.12.0 for stable screening numbers (about $1.5 each).
