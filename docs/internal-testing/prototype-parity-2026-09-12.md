# ReviewPilot: Web App and Prototype functional comparison audit

Audit date: September 12, 2026 (America/Phoenix). Audit-time conclusion: **Full functional parity could not be established. The comparison found concrete gaps, approved product changes, and placeholder controls in the prototype itself.**

Historical status: this document preserves the pre-fix findings. See [G01–G09 fixes and acceptance](prototype-parity-2026-09-12-fixes.md) and [the subsequent urban live workflow test](urban-live-e2e-2026-09-12.md) for later verification. Original probe results are historical evidence, not the current pass/fail state.

This audit added documentation and repeatable diagnostic scripts only. It did not change product implementation, real projects or the three official examples. The preceding P0 implementation was included in the inspection; its passing single-paper example tests did not cover combinations such as multi-paper example navigation identified here.

## 1. Comparison baseline and evidence boundaries

- Runnable prototype: root-level `reviewpilot-ui.html`. Its decoded `__bundler/template`, after trimming leading/trailing whitespace, exactly matches `design/reviewpilot-ui.source.html`.
- Prototype source SHA-256: `52451664f14d9c5017e2bf4c07d7752af30a470b837226345ed279cb467f825b`.
- Effective prototype UI/logic is around lines 14070–14485; preceding content is mostly font and icon styles.
- Supplemental contracts: `design/LEAD_AGENT_UI_UX_BOUNDARY.md`, Step 4 prototype-parity design and recovery contracts, and user-confirmed memory/sidebar/P0 changes.
- Code baseline: HEAD `2fe48ee` **plus the uncommitted working tree**. HEAD alone cannot reproduce this audit; the adjacent manifest JSON records relevant file hashes.
- An isolated local browser environment covered ordinary projects, read-only examples, two extracted papers, failure states, saving and refresh. Diagnostic scripts use temporary directories without external models or search services. All 124 existing focused tests also passed.
- “Connected” means a UI entry, execution path and code/test evidence exist. It does not mean this audit tested every data scale, failure or real-model outcome for that function.

**Correction concerning inclusion/exclusion criteria: the committed prototype HTML has no inclusion/exclusion controls. Its Step 2 contains only counts, sources and View excluded. The interaction-boundary document explicitly requires criteria approval. Earlier descriptions of these controls as already present in this HTML were inaccurate.** The audit therefore uses three layers: runnable prototype, functional contracts and subsequent user decisions. Neither literal HTML copying nor omission of required research steps is appropriate.

## 2. Confirmed issues and post-fix acceptance conditions

Priorities: P0 = settings/screening scope can be silently executed incorrectly; P1 = a function cannot be completed, results cannot be viewed in full, or confirmation is lost; P2 = inadequate entry points, information or scope explanations. These are this audit's priorities, not a repeat of the preceding three-P0 list. Line numbers below refer to the audit snapshot.

| ID | Priority | Issue | Evidence and impact | Post-fix acceptance condition |
|---|---|---|---|---|
| G01 | P0 | **Add/Remove keyword does not change the actual query** | `frontend/app.js:638` prioritizes nonempty setupDraft.search_terms and omits keywords/concept_blocks. Add/remove only modifies the keyword array, around lines 2449/2654. Adding auditmarker leaves the Boolean query as clinical AND AI; saving removes the new chip. | Edits produce reviewable concept/query changes; saving and collection consume the confirmed revision; refresh retains it. Preserve existing OR/AND structure rather than mechanically concatenating terms. |
| G02 | P0 | **Date execution differs from the UI** | The UI accepts full dates and says empty end date means today (`frontend/app.js:1473`). FilteringAgent._filter_by_date compares years only (`agents/filtering_agent.py:222`), leaves empty ends unbounded and drops unknown years. Probes retain January 15, 2025 despite a September 1 start and retain a 2099 record with an empty end. Contract date-key conversion is correct; the problem is not a lost key. | Define date precision, compare complete dates exactly, and explicitly review year-only/missing dates or state the policy. Resolve today to an explicit save/run boundary. Apply source date constraints with result limits in mind to avoid truncation-before-filtering coverage loss. |
| G03 | P1 | **Not all removed records are traceable individually** | Prototype View 242 excluded represents 312−70, including 64 duplicates. Current excluded_papers.jsonl contains only LLM relevance exclusions (`agents/filtering_agent.py:127`); date/exact/similar removals have counts only. A four-record probe removes three but exposes only one in the excluded file. | Show date exclusions, exact duplicates, similar duplicates and relevance exclusions separately. Preserve original identity, reason and duplicate group/retained item; reconcile totals with identified records. Do not call duplicates eligibility failures. |
| G04 | P1 | **Next paper does nothing in read-only examples** | The readOnlyExample allowlist at `frontend/app.js:2331` lacks preview-nav, although rendering exposes Next paper at line 1854. A two-paper example remains at 1/2 after clicking. | All three examples allow navigation through existing precomputed papers without copying or model calls. First/last boundaries work; read-only navigation is allowed while all writes remain rejected. |
| G05 | P1 | **Confirmed schemas lack a direct extraction rerun/recovery entry** | Backend ui_state.py:94 returns Run Extraction, but the frontend exposes Finalize Schema only for drafts (`frontend/app.js:2078`) and excludes generic actions from Step 4 (line 1310). The failed-state browser offers Regenerate without a retry using the original schema. | Finalized/failed/partial/stale states expose explicit run/recovery actions preserving the schema. Retry must not require regeneration. Long operations show progress, and failed-state review/sampling controls agree with backend rejection rules. |
| G06 | P1 | **Some confirmation/completion states exist only in the browser** | Confirm Categories, Skip Categorization and Finalize Project only update state.catDraft (`frontend/app.js:2463`); restore/setData rebuild it and lose flags (lines 422/724). Skip → Finalize displays Project Complete, but refresh restores setup and the header stays Active. Actual Apply Categorization results do persist and must be distinguished from confirmation. | Persist category confirmation, skipping and completion independently with revisions. Refresh/switch/restart must agree; completion must match the header, exports and invalidation after edits. |
| G07 | P1 | **Full Results silently truncates to 50 papers** | `reviewpilot_core/state_projection.py:1079` uses rows[:50], with no frontend total, pagination or first-50 notice (line 1995). A 65-row probe returns 50. Full-file exports may still be complete; browser visibility is the defect. | All results are browsable. Pagination, if used, shows total/current range/navigation. Verify paper 51 and the prototype's 70-paper scale. |
| G08 | P1 | **Full-text access categories are unsupported assertions** | The prototype distinguishes Open access / Via institution. _retrieval_summary (`reviewpilot_core/state_projection.py:709`) assigns all successes to openAccess and fixes viaInstitution at zero; full provenance-based counting is absent. | Count access methods from actual download provenance. Show unknown/retrieved when unproven; do not assert all PDFs are OA. Do not imply institutional support through a fixed number when it is unimplemented. |
| G09 | P1 | **Model-extracted field types lack deterministic validation** | The prototype shows Number / Text(list) / Select, but _validate_schema_data checks keys only (`agents/extraction_agent.py:543`). An integer field accepts “not a number.” Human corrections have partial type checks, so the paths differ. | Define shared types and enum/list contracts for model output and human correction. Invalid types explicitly fail or require repair. Unreported evidence remains explicitly missing; required must not force fabricated values. |
| G10 | P2 | **Adjust search criteria / Save setup is hard to discover** | The prototype has a labeled Adjust search criteria action. The current canvas offers only Run collection; complete editing/saving is hidden under an unlabeled assistant-header + (`frontend/app.js:2099`). | Provide a clearly named Search Setup edit/save entry with saved/draft feedback; do not require guessing the chat + control. |
| G11 | P2 | **Step counts and the final workflow overview are not displayed** | The prototype stepper shows sources, 70/312, 62/70, fields and groups. The current stepper shows status but omits its existing sub value (from line 1631). The backend supplies resultOverview, which is not rendered; the final page shows only extracted/fields. | Use consistent step counts and display identified → screened → included → retrieved → extracted → categorized, derived from verifiable state. |
| G12 | P2 | **Help / Home entries have no content** | Prototype goHome/goHelp are also no-ops, but design documents promise Home, Settings and Help. Current Help looks clickable without a handler (around `frontend/app.js:1551`); the logo does not navigate home. | Provide actual content/navigation or remove the clickable suggestion. Prototype no-ops are not completed functionality. Current Settings configuration reuse is user-approved; do not restore the obsolete memory toggle. |
| G13 | P2 | **Narrow windows hide all history navigation without an alternative** | `frontend/app.js:1498` hides the sidebar at ≤760px, with no drawer or replacement for new/history entries. This is outside the desktop prototype baseline but is a concrete Web App interaction gap. | If narrow windows are supported, keep creation/history reachable. Otherwise state supported desktop dimensions; hiding essential functions is not completed responsive support. |

Additional semantic boundaries: Text (list), Select and Long text did not yet form a unified executable type system. Model settings mainly affect some Lead calls; subagents use fixed role models, so the header Model is not necessarily the model used by every task. Live sampling directly instantiates a worker instead of the formal contract's Skill-injection path; policy equivalence remains unproven. These require clarification in acceptance work, without inventing prototype commitments.

## 3. Function-by-function mapping

Sources: P = prototype display/interaction; D = design contract; U = subsequent explicit user-approved change. Code/test references are entry points, not claims that a test covers every semantic detail in its row. Judgments describe the pre-fix audit snapshot.

| ID | Area / function | Source | Audit judgment | Implementation / verification entry |
|---|---|---|---|---|
| F01 | Three columns and fixed sidebar | P | Retained on desktop; narrow-window gap G13 | frontend/app.js render, workspaceResponsiveStyle; browser |
| F02 | Logo returns Home | P/D | Prototype no-op; no current navigation, G12 | Prototype goHome; app render |
| F03 | New conversation | P/U | New Review creates from the first message | startNavigation, handleChatSubmit; test_session_navigation/history |
| F04 | History and switching | P/U | Real projects with asynchronous switch protection | selectProject, /sessions; session tests |
| F05 | Time-grouped history | P/U | Replaced by Examples / Chats ordered by recent activity | projectNavItem, session history; later sidebar request |
| F06 | Ordinary conversation search/rename/delete | U | Connected; examples protected | /sessions, manage_session; test_session_history |
| F07 | Settings | P/U | Opens explicit configuration reuse, as requested | memoryDialog, configuration_reuse; test_configuration_reuse |
| F08 | Help & support | P/D | Placeholder, G12 | App render |
| F09 | Project title/date/model | P | Real projection; completion/model semantics need clarification | workspaceHeader, state_projection; G06 and semantic boundaries |
| F10 | Five-step status/navigation | P | Gates, running, failed, partial and stale states connected | workflow_state, stepItem; frontend behavior tests |
| F11 | Per-step counts | P | Not displayed, G11 | Prototype steps.sub; current stepItem |
| F12 | Research question display/edit | P/D | Display retained; editing hidden, G10 | searchCanvas, setupDialog, PUT setup |
| F13 | Keyword display | P | Concept labels; server-authoritative restoration | _keywords, setupDraftFromData; projection tests |
| F14 | Add/remove keywords | D | Disconnected from actual query, G01 | setupPayloadFromDraft; probes and browser save |
| F15 | Sources and per-source limits | D | PubMed/arXiv/OpenAlex connected | sourceChecklist, CollectionAgent; web_app tests |
| F16 | Prototype's five sources and counts | P | Scope difference below; actual source counts visible in Step 2 | Prototype platforms; main.PLATFORMS; draftSources |
| F17 | Date range | D | Persistence connected; insufficient execution precision, G02 | FilteringAgentContract, _filter_by_date; probes |
| F18 | Setup save/edit and change impact | P/D | API/revision connected; entry gap G10 | update_project_setup, setup_revision; web/behavior tests |
| F19 | Run collection and source failures | D | Tasks and source outcomes connected | CollectionAgent, TaskRunner, workflow outcome |
| F20 | Inclusion/exclusion display/edit | D/U | Added to Step 2; no longer missing | screeningCriteriaPanel, screening_criteria; relevant subset of 124 tests |
| F21 | Criteria confirmation/stale rejection | D/U | Finalized/revision gates connected | require_finalized_criteria; test_screening_criteria |
| F22 | Deduplication/relevance screening | P/D | Execution connected; removal evidence incomplete, G03 | FilteringAgent.run; test_filtering_agent/probes |
| F23 | Identified/after-dedup/included counts | P | Connected; all removals must reconcile | _screening_metrics; record-review tests |
| F24 | View all excluded/removed records | P | Relevance exclusions only, G03 | excluded_papers, reviewWorkbench |
| F25 | Per-paper reason/criterion/human decision | U | Connected; missing legacy reasons labeled | record_review, frontend/review.js; test_record_review |
| F26 | PDF success/failure totals | P | Connected | _retrieval_summary, DownloadAgent |
| F27 | OA/institutional access classification | P | Fixed mapping, not proven capability, G08 | _retrieval_summary; probes |
| F28 | Recently retrieved list | P | Limited recent records connected | _retrieved, retrievalCanvas |
| F29 | Retry unavailable | P/D | Selected failed-item retry, revisions and task exclusion connected | retrieval_retry*; focused regressions |
| F30 | View local PDF source | U | Step 4 evidence dialog connected; Step 3 is not a file manager | review/pdf, evidence_support |
| F31 | Schema fields/descriptions/required table | P | Dynamic schema, not fixed at 16 fields | fieldsTable, load_schema_draft |
| F32 | Executable field-type constraints | P/D | Display exists; execution incomplete, G09 | _validate_schema_data, save_field; probes |
| F33 | Chat add/edit/remove schema fields | P/D | Draft editing and confirmation implemented | LeadAgent._handle_schema_message; schema/lead tests |
| F34 | Regenerate schema | P | Real generation and stale-state handling connected | extractionSchemaAction, regenerate-schema |
| F35 | Preview / previous / next paper | P | Ordinary projects connected; examples blocked, G04 | showExtractionPreview, preview-nav; browser |
| F36 | Single-paper preview generation/retry | P/D | API/cache connected | extraction_preview, preview-extraction; test_extraction_preview |
| F37 | Schema JSON view | P | Entry in draft decision card disappears after confirmation | schemaJsonDialog, extractionDecisionCard |
| F38 | Finalize Schema → formal extraction | P/D | Draft path uses a real composite action | finalize-and-run-extraction; Step 4 tests |
| F39 | Rerun/recover with original schema | D | Direct UI entry missing, G05 | ui_state.primary_action versus extractionCanvas |
| F40 | Field evidence/location/human correction | U | Connected; quotation location distinguished from semantic support | evidence_support, record_review; test_record_review |
| F41 | Categorization gates/automatic start | P/D | Changed to human review after extraction, with design basis | categorizeCanvas; later UI/UX Boundary |
| F42 | Category field/mode/suggestion/edit/apply | D | Connected; confirmation persistence gap G06 | categorizationSetupPanel, CategorizationAnalysis |
| F43 | Groups/counts/representative papers | P/D | Real statistics and limited representative samples connected | categoryBriefsPanel, categorization_analysis |
| F44 | Final whole-workflow overview | D | Backend data not rendered, G11 | resultOverview, categorizationMetrics |
| F45 | Full Results / distributions | D | Table silently capped at 50, G07; distributions show limited top items | _full_results, _distribution, fullResultsTable |
| F46 | Export | D | Raw JSON/JSONL downloads; not Excel/PRISMA reports | EXPORT_ARTIFACTS, exportPackageSection; export tests |
| F47 | Skip / Finalize Project | New current controls | Clickable but not persisted, G06 | catDraft flags; browser refresh reproduction |
| F48 | Chat send/history/draft | P/U | Local history/current authoritative project configuration connected | LeadAgent, messages.jsonl, session tests |
| F49 | Activity feedback/ongoing tasks | P | Implemented; Activity moved to assistant, history marked saved | activityMessage, monitorActiveTask; behavior tests |
| F50 | In-project memory/explicit cross-project reuse | U | Follows user-confirmed direction; older design superseded | project_decisions, configuration_reuse; relevant tests |
| F51 | Three read-only examples/independent copies | U | Protection/copy implemented; multi-paper browsing needs G04 | demo_projects, ProtectExamples; record_review tests |
| F52 | Live 1–5-paper sampling versus precomputed data | U | Saved separately without overwriting formal results; Skill-policy equivalence unverified | record_review.run_sample; sampling regressions |

## 4. Differences that are not missing functionality

1. **Static values replaced by real project data.** Sixteen fields, 70 papers, 312 records, fixed times/models and two fixed groups are prototype demonstration data, not values to restore in code.
2. **Three current sources versus five prototype sources.** The prototype lists PubMed, IEEE Xplore, arXiv, ACM DL and Semantic Scholar; the current canvas lists PubMed, arXiv and OpenAlex. Historical work repeatedly uses the three-source boundary. IEEE/ACM/Semantic Scholar have no independent primary search adapters; using Semantic Scholar to find PDFs does not provide literature search. State the existing three-source capability explicitly. Restoring five sources would require a scope change with complete adapters and acceptance tests.
3. **Categorization no longer runs automatically as prototype copy suggests.** Later design requires field/mode selection, category review and application. Literal parity must not bypass human confirmation.
4. **Cross-project memory is not automatically injected.** The user approved explicit reuse of existing project configuration. The old Memory toggle / Clear memory design is no longer the target.
5. **Examples / Chats and create/rename/delete.** Later user requirements supersede static Today/Previous7days groupings and no-op history.
6. **Prototype placeholders.** Some new/history/Home/Settings/Help/header-more/send/JSON/Finalize controls lack real handlers; prototype Regenerate only forceUpdates. A visible button is not complete functionality, but its product intent still matters.
7. **No invented scope expansion.** CSV/XLSX, PRISMA figures, collaboration, login, cloud sync, voice and PDF upload have no explicit functional contracts in this runnable prototype; they are not labeled omissions here.

## 5. Why many passing tests still missed these issues

- Historical prototype-parity acceptance covered Step 4 only, not all five steps.
- Some tests checked labels, handlers or backend JSON fields without following edit → payload → save → downstream consumption. G01/G11 expose disconnected frontend/backend pieces.
- A single-paper example cannot test Next paper. G04 combines read/write permissions with multi-paper navigation and needs at least two papers.
- Existing tests checked that resultOverview existed, not that users could see it; Full Results lacked a >50-row boundary case.
- Conflicting design documents lacked consistent superseded markers: older memory documentation described automatic reuse, while UI/UX Boundary required plain-text chat and later Step 4 parity required assistant decision cards. Following one alone could undo another requirement.

## 6. Acceptance rules to prevent omissions

Use F01–F52 as a traceability table. Each iteration should record the function ID, currently authoritative requirement, entry, execution path, saved artifact, invalidation/recovery behavior and evidence. Allowed statuses are implemented and verified / unresolved gap / approved replacement / prototype placeholder awaiting decision / unverified. An absent record is not evidence of implementation.

Required behavioral checks, beyond source-string matching:

- **A01 Search constraints end to end:** add/remove keywords and edit dates → inspect proposed strategy → save → run → inspect constraints received by the worker → refresh.
- **A02 All removals reconcile:** use mixed date exclusions, exact duplicates, similar duplicates, clear exclusions and uncertain records; every disposition/reason is inspectable and counts balance.
- **A03 Multi-paper read-only examples:** first/middle/last papers are reachable without writes or model calls; copies are editable without changing originals.
- **A04 Schema state matrix:** missing/draft/finalized/running/partial/failed/stale/completed; visible actions agree with backend eligibility.
- **A05 Decision persistence:** category confirmation, Skip, Finalize, human screening changes and field corrections survive refresh, switching and restart; old windows cannot overwrite newer revisions.
- **A06 Complete results boundaries:** 0, 1, 2, 50, 51 and 70 papers; every paper is reachable and list/pagination/total/export agree.
- **A07 Types and provenance:** Number/list/select, uncertain values, PDF/web/no source and unknown download access have explicit executable/display contracts.
- **A08 Reachable entries:** actually click Help, Settings, search editing, schema JSON and failure recovery. Supported narrow windows must retain history and creation access.

The audit includes [seven local diagnostic results](prototype-parity-2026-09-12-probes.json) and [repeatable probes](scripts/prototype_parity_probes.py). Run:

```bash
.venv/bin/python docs/internal-testing/scripts/prototype_parity_probes.py
```

**At the pre-fix audit snapshot these probes exited 1 because they reported confirmed gaps; this was not an all-passing regression claim.** They cover G01/G02/G03/G07/G08/G09. Other issues require the browser/state-matrix checks above; these probes do not establish complete functional coverage. Later passing results are linked at the top of this report.

## 7. Completed work and exclusions

Completed: prototype decoding/source equivalence, 52-function mapping, code tracing, 124 existing tests, seven deterministic diagnostics, prototype/Web App browser comparison, and reproduction of keyword-save loss, example navigation failure, completion loss after refresh and extraction-recovery gaps.

Not performed in this audit: another real-source/model-quality evaluation, institutional-network verification, large-scale performance testing or pixel-level parity at every supported size. This audit did not implement G01–G13 fixes, so its historical result cannot be labeled “all omissions eliminated.”
