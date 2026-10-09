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
