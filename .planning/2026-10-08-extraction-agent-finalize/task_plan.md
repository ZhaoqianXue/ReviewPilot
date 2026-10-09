# Extraction Agent finalize

## Decisions (user, 2026-10-08)
- (A) Adopt the §4.3-tuned extraction skill and task instruction, together with the evidence matcher, the gpt-6-luna model defaults, temperature handling and project_model that Table 2 ran on.
- (B) Try a looser empty-value rule once; adopt only if P, R, F1 and VC do not drop.
- (C) Ownership split with the web-assisted extraction fallback session: it owns the no-PDF path and the full-text eligibility field.

## Done (in main via 260eca2, swept in by the screening-canvas merge, plus this commit)
- Coding rules in the app (`reviewpilot_core/coding_rules.py`):
  - finalize-schema and finalize-and-run-extraction draft the rules with the Prompt Agent from the finalized schema and the included papers' full text.
  - New actions generate-coding-rules and confirm-coding-rules; run-extraction requires confirmed rules.
  - The rules go into `prompts/extraction_prompt.json` right before "PAPER EVIDENCE DATA:" (the Appendix D position).
  - The rules go stale when the schema or the set of included papers changes. Retrieval rewriting PDF paths does not make them stale.
- `build_extraction_prompts(config, schema, coding_rules="")`.
- State projection `codingRules`; extraction step order Finalize Schema → Draft Coding Rules → Confirm Coding Rules → Run extraction.
- ExtractionAgent.run extracts 8 papers at a time and writes results in input order.
- structured-evidence-extraction skill 3.0.0: "Design the schema" removed (the Prompt Agent owns it).
- Tests: `tests/test_extraction_agent_finalize.py`; Lead Agent and web app tests updated.

## Acceptance (app path, Table 2's 178 papers, gpt-6-luna)
Run in a copy of the tree without the unfinished full-text eligibility field.

| Arm | P | R | F1 | VC |
|---|---|---|---|---|
| Current instruction + generated rules (1.12.0) | .737 | .810 | .772 | .662 |
| Evaluation harness, same rules | .742 | .823 | .780 | .702 |
| Looser empty-value rule + generated rules | .724 | .788 | .755 | — |
| Looser empty-value rule + Appendix D | .739 | .833 | .783 | — |

- App path − harness, F1: −.008 [−.019, +.001]. The app reproduces the evaluated result within noise.
- Looser rule − current, F1: −.017 [−.031, −.004]. Rejected; the current instruction is kept.

## Open
- Canvas for drafting, editing and confirming the coding rules: handed to the screening-canvas session.
- §4 evaluation tooling (scripts/evaluation app path) needs the guidance and coding-rule confirm steps. Six evaluation tests fail until then; the frozen-screening test is a seventh.
- The full-text eligibility field (unfinished, owned by another session) was swept into main by 260eca2. It adds a field to every finalized schema.
