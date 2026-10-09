# Screening Agent finalize

Goal: the app screens with the instruction evaluated in the project document (Appendix A plus the Prompt Agent's
review guidance), so that the app reproduces Table 1.

## Decisions (user, 2026-10-08)
- (A-a) Keep the evidence fields: the decisive criterion and a verbatim excerpt.
- (B-a) The Prompt Agent drafts the guidance when the criteria are finalized (the collection is complete by then). The researcher confirms or edits it, and screening needs confirmed guidance.
- Acceptance: one app-path run on the PERG test pools.

## Done (uncommitted)
- `screening_evidence.py`:
  - Instruction in the Appendix A shape: guidance before the numbered criteria, five-level likelihood, plus criterion and quote fields.
  - The likelihood decides the outcome.
  - An exclusion whose quote is not found in the record, or whose rule is not a confirmed rule (exact or ≥60% content-word overlap with the criteria or a guidance line), is flagged `uncertain` (Needs review) rather than retained.
- `screening_guidance.py` (new):
  - States: draft, confirm (with edits and a revision check), stale when the criteria or collection change, failed.
  - The confirmed guidance is written into `relevance_prompt.json`; screening requires it.
  - A guidance change marks screening and downstream stages stale.
- `screening_criteria.save_criteria`: stores the new instruction; a criteria change withdraws the guidance.
- Lead Agent:
  - finalize-criteria drafts the guidance (a failure is recorded).
  - New actions generate-screening-guidance and confirm-screening-guidance; screen checks for confirmed guidance.
- web_app, task labels, state projection (`screeningGuidance`; step order finalize → Draft Guidance → Confirm Guidance → Run screening), frontend action metadata.
- FilteringAgent screens 16 records at a time; decisions and the log keep the input order.
- evidence-screening skill 3.0.0 is Appendix A's five-step procedure.
- Tests: new `test_screening_agent.py`; updated web_app smoke tests, pre-screening flow, screening criteria, record review, urban regressions, skill runtime.

## Acceptance (app path, PERG test, gpt-6-luna, guidance = the 1.12.0 perg10 drafts, about $1.89)
- Model decisions alone: macro P .695, R .834, F1 .715, share .328. The harness with the same guidance gave .697 / .834 / .717 / .325, so the app reproduces it.
- Strict gate (exclusion retained unless the rule matches exactly): F1 .591. Fuzzy gate: .679. Both rejected; flagging is used instead.
- With flagging the outcomes equal the model decisions. 3,538 of 3,775 exclusions are verified; 237 are flagged for review.

## Open
- §4 evaluation harness (`scripts/evaluation/app_project.finalize_criteria`) needs a guidance-confirm step. Four untracked evaluation tests fail until then; that is §4 tooling and needs the owner's go-ahead.
- Canvas display, editing and confirmation of the guidance: worktree debug session.
- Appendix A in the project document does not yet show the added criterion and quote fields.
- Commit pending approval.
