# Urban LLM live workflow test

Date: September 12, 2026 (America/Phoenix). This is a small application test, not a systematic review or a coverage claim.

## Result

The ordinary session **Urban Planning Governance Live E2E 20260912** (`review-project`) completed all five steps at `http://127.0.0.1:5602/projects/review-project`. It was created with New Review; no example was copied or modified. All test inputs, saved content and this report use English.

- Search: OpenAlex returned 5 real records, with a limit of 5 and a publication window of January 1, 2023 through September 12, 2026. Urban planning and urban governance were alternative eligible settings.
- Screening: explicit inclusion/exclusion criteria were saved, refreshed and confirmed. Three clearly ineligible reviews were excluded through human review. Two candidates proceeded to retrieval.
- Full text: both real PDFs were obtained. The Industry 5.0 paper was then excluded because its conceptual framework did not establish an eligible urban application. Retrieval was rerun for the remaining cohort.
- Extraction: 11 fields were generated and confirmed; two PDFs were initially processed, then the final one-paper cohort was reprocessed successfully. The evidence preview showed both a located PDF excerpt with a page-1 link and an explicitly unconfirmed anchor. The located UrbanGPT objective was checked against the displayed source text.
- Categorization and export: the confirmed category was applied to UrbanGPT. Final state is **Complete**, with 1 included paper, 4 excluded papers, 1 retrieved PDF, 1 extracted row, 11 fields and 1 category. All 11 export endpoints returned HTTP 200; JSON/JSONL parsed, and file contents matched the source artifacts. The decisions endpoint's saved portion matched its source file.

The retained paper is [UrbanGPT: Spatio-Temporal Large Language Models](https://doi.org/10.1145/3637528.3671578). Other retrieved records were:

| Paper | Identifier | Final disposition |
| --- | --- | --- |
| Large language models empowered agent-based modeling and simulation: a survey and perspectives | 10.1057/s41599-024-03611-3 | Excluded review |
| Integrating large language model and digital twins in the context of industry 5.0: Framework, challenges and opportunities | 10.1016/j.rcim.2025.102982 | Excluded after full-text assessment |
| Large Language Models for Forecasting and Anomaly Detection: A Systematic Literature Review | 10.48550/arxiv.2402.10350 | Excluded review |
| Large Language Model based Multi-Agents: A Survey of Progress and Challenges | 10.48550/arxiv.2402.01680 | Excluded review |

## Reproduced defects and fixes

| Defect | Change | Verification |
| --- | --- | --- |
| Initial chat ignored explicit source, date and result-limit instructions; operational terms could enter the query | Interpret a validated optional settings object only for initial chat creation | A second ordinary browser session retained arXiv only, limit 5, both dates and the requested English title; live model and boundary tests passed |
| Focusing New Review could replace the input during typing | Insert the quick-start UI without repainting the focused editor | Ordinary browser creation completed with stable input |
| Setup edits replaced the synthesized initial exchange; an ordinary urban session could receive example starter text | Persist the initial user/assistant exchange; restrict example inference to protected examples | Initial chat hash unchanged after a setup edit and refresh; regression tests passed |
| Editable setup could display escaped quote entities | Decode display-escaped values when constructing the edit draft | Query editing and saving retained literal quotation marks |
| arXiv date filtering overwrote the numeric pagination offset | Separate date bounds from the pagination variable | Request offsets remained 0 and 1 with a date filter in regression tests |
| arXiv HTTP 429 was swallowed as successful empty search; network retries could be unbounded | Propagate HTTP failure, cap consecutive network failures and use HTTPS | A fresh real request ended in failed collection with the explicit HTTP 429 error; browser restored the blocked/retryable state; retry-bound tests passed |
| Wrapped literal quotations were falsely treated as unsupported screening evidence | Strip one balanced outer quotation pair only when it then matches the source exactly | Wrapped literals verified; altered/paraphrased text still remained unverified |
| Refreshing retrieval recovery jumped to a later stage | Restore the saved available step even when authoritative server data is loaded | Browser refresh stayed on recovery; session switching and final refresh retained separate states |
| Changing inclusion invalidated result files and also hid an unchanged confirmed schema | Bind schema confirmation to setup revision; retain it across result-only invalidation | Tests distinguish cohort changes from setup changes; the real rerun used the confirmed 11-field schema after legacy-marker reconfirmation |
| Human screening decisions updated canonical counts but left compatibility aliases stale | Update both count representations, including the last-valid ledger snapshot | Final ledger reports 1 included and 4 excluded consistently; targeted regression tests passed |
| Single-paper category/results text used plural grammar | Use count-aware labels | JavaScript syntax and frontend contracts passed |

The existing legacy schema marker in this test session was reconfirmed once through the normal API. Only this session's already-stale count aliases were repaired before finalization; other sessions were not migrated.

## Recovery and persistence checks

Refresh was exercised after criteria confirmation, during preview work, on retrieval recovery, after category confirmation and after finalization. Two newly created ordinary sessions were switched in both directions. A backend restart retained the completed state and the independent arXiv failure state.

An isolated server and data root exercised failed-only PDF retry using copies of two real retrieved PDFs: 1 success/1 failure remained partial after an injected repeated failure, then recovered to 2 successes/0 failures. The previously successful PDF hash did not change, only the selected failed item was retried, and no retry button remained after recovery. This was controlled fault injection with cached real PDFs, not a live publisher recovery.

The browser automation cancelled some native confirmation dialogs. For those rerun/retry confirmations, the same revision-bound backend confirmation contract was used, followed by browser verification. Native dialog acceptance is therefore not fully verified and was not classified as an application defect.

## Verification and preservation

- Full regression run: **850 tests passed**. After the final count and small text adjustments, **74 targeted tests passed**; JavaScript syntax passed. The full suite was not repeated after these final small changes.
- Export row counts: 1 included, 4 excluded, 0 date/duplicate removals, 1 extraction and 1 categorized result.
- Export hashes and consistency checks: [verification.json](evidence/urban-live-e2e/verification.json). The adjacent directory contains all 11 downloaded exports.
- Controlled retry evidence: [retry-verification.json](evidence/urban-live-e2e/retry-verification.json).
- A baseline covered 2,870 pre-existing output files. 2,869 remained byte-identical. The unrelated `output/pdf/ReviewPilot-NAACL2027.pdf` changed during the run (mtime September 12, 19:04:30 -0700). This task did not write or restore that file; its origin was not determined. No new output files were found outside the two new test projects. All pre-existing review-session files were unchanged.
- Pre-existing source edits were retained; no reset, checkout, commit or unrelated data cleanup was performed. The isolated retry server was stopped. The main app remains on port 5602 with the final code loaded.

## Unverified scope

Live arXiv success after its rate limit clears; live publisher failure-to-success retry; all native confirmation acceptance paths; exhaustive verification of every extracted claim; broad search/category quality with a larger corpus; concurrent users and other browsers. PDF access provenance remained unknown where the application could not establish it. These checks do not establish open-access licensing or literature completeness.
