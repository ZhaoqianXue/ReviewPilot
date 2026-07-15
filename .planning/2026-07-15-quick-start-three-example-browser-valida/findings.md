# Findings & Decisions

- Exact Quick Start topics are Biomedical, HCI, and Urban Planning/Smart Cities, defined identically in `frontend/app.js` and `reviewpilot_core/state_projection.py`.
- Canonical History IDs are `quick-start-biomedical-showcase`, `quick-start-hci-showcase`, and `quick-start-urban-showcase`.
- History explicitly prefers `quick-start-*` projects and otherwise exposes the topic as a starter action.
- Existing canonical runs are dated 2026-07-14 and therefore cannot prove the new Agent Skill runtime.
- Each canonical setup already establishes the required source set and 10-per-platform shape; new runs must recreate this through the browser.
- The current Apple Silicon `.venv-native` directory is absent. A native arm64 test/runtime environment exists at `/tmp/reviewpilot-arm64-venv` from the immediately preceding verified task.
- The Web App was started from the current workspace at `127.0.0.1:5602` and opened successfully in the visible Codex in-app browser; `/` redirected to `/workspace` with title `ReviewPilot`.
- Initial browser state restored the unfinished legacy project `inner-beta-biomedicine-r3` at Step 4, demonstrating that session/history state is live rather than a clean fixture. The visible DOM exposes the normal workflow and chat controls; a fresh-project navigation is required before selecting a Quick Start topic.
- Biomedical Quick Start created a real project titled `How LLMs Are Used in Biomedical Research and Clinical Care`. The visible Search Setup showed PubMed, arXiv, and OpenAlex all checked with `Max results/platform = 10`, plus a 2020-01-01 open-ended date range.
- Biomedical collection visibly completed with exactly 30 records: PubMed 10, arXiv 10, OpenAlex 10, and no platform errors. The Paper Screening step became available.
- Biomedical screening completed 27 de-duplicated records with 22 included and 5 excluded. The first attempt to open retrieval encountered a browser-control deadline after the task had already completed; product state must be checked before any retry.
- After screening, the visual stepper rendered Full-Text Retrieval as available, but the filtered interactable DOM still exposed only `Run screening`; the step is implemented as a clickable generic element. Browser interaction therefore needs a verified element rectangle/coordinate fallback rather than a guessed semantic button.
- Biomedical retrieval completed 21/22 with one honest `article print failed` item. The UI explicitly allowed progression to Information Extraction while retaining the failed paper in recovery; no missing PDF was fabricated.
- The in-app browser viewport is 320×1094 and the document had scrolled to Y=214; the earlier scroll coordinate failed because X=500 was outside this narrow viewport. Use an in-viewport point for future scrolling.
- ReviewPilot's three-column desktop workflow cannot be reliably operated in the browser's 320px default viewport because the main stepper is horizontally off-screen. The test viewport was explicitly set to 1440×900, matching the product's established desktop acceptance size; it will be reset at completion.
- Biomedical Information Extraction initially renders an empty schema workbench and a single visible `Generate Schema` action, confirming that schema design remains a real user decision rather than an automatically bypassed backend step.
- Biomedical schema generation produced 10 topic-specific fields, including `llm_presence`, `biomedical_context`, `study_objective`, and `datasets_used`, and stopped for the expected user finalization decision.
- The live project ID is `how-llms-are-used-biomedical-4`. Extraction loaded all 22 included-paper records, found 21 local PDFs, and continued across the complete 22-record workload, which is the expected web-fallback behavior for the single retrieval failure.
- Biomedical extraction produced 22 paper records and 15 available categorization fields. The final UI recommends `Methods` because it has varied values and retains the explicit single-label versus multi-label choice before category generation.
- Biomedical category suggestion returned five method groups: evidence synthesis; perspective/commentary/conceptual framework; model development/fine-tuning; comparative evaluation/benchmarking; and guideline/consensus/expert recommendation.
- Biomedical paper categorization finished successfully and populated 22 result rows, of which 21 have non-empty category mappings because one paper has no `methods` value. The application does not consider the workflow terminal until the user additionally clicks `Finalize Project` after reviewing the export package.
- HCI populated 16 categorized-result rows and 13 non-empty `datasets_used_raw` mappings; Urban populated 15 rows and 15 non-empty `methods` mappings. Result-row coverage and non-empty category coverage must not be conflated.
- HCI Quick Start persisted and collected the requested 10 records from each of PubMed, arXiv, and OpenAlex, totaling 30 with no visible collection error.
- During active HCI extraction the header temporarily showed `Needs rerun`, but final task evidence showed 16/16 processed, zero errors, and automatic Step 5 advancement. It is a transient stale/dirty marker, not a failed extraction.
- HCI category suggestions covered published literature/corpora; user interaction/behavioral data; surveys/interviews; synthetic/annotated/derived artifacts; and incident/registry/public databases.
- Urban Quick Start also persisted and collected the requested PubMed 10, arXiv 10, and OpenAlex 10 before screening.
- Urban category suggestions cover survey/review synthesis; framework/architecture design; data transformation/representation learning; governance/planning/decision support; and evaluation/benchmarking/comparative analysis.

## Requirements
-

## Research Findings
-

## Technical Decisions
| Decision | Rationale |
|----------|-----------|

## Issues Encountered
| Issue | Resolution |
|-------|------------|

## Resources
-
