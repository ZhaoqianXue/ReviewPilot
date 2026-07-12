# ReviewPilot Three-Scenario Inner-Beta Quality Design

## Objective

Use three real, materially different literature-review scenarios to exercise ReviewPilot from project creation through categorization, fix evidence-backed P0 and P1 defects in iterative vertical slices, and prove that the local Web App is ready for formal internal testing by 3–5 users without developer supervision.

## Scope and release boundary

The target is the existing local Starlette monolith served by `web_app.py` with the zero-build frontend in `frontend/`. The supported internal-test environment is local Chrome at 1280×800 and 1440×900. Public deployment, authentication, multi-user collaboration, cloud operations, and a framework migration are outside this cycle.

An inner-beta candidate must let a user create a project and complete collection, screening, full-text retrieval, extraction-schema preparation, information extraction, and categorization. P0 blockers and data-integrity defects must be zero. P1 defects must be fixed unless they are explicitly documented with an acceptable user-visible limitation and a practical workaround.

## Real scenarios

Every scenario is rerun under a new project identifier. Historical output can supply inputs and comparison evidence, but it cannot count as a passing result for the current version.

### Scenario 1 — LLMs in biomedicine

The authoritative input is `output/llm-biomedicine-survey/search_conditions.json`. It combines LLM/foundation-model terms with biomedicine, healthcare, life-science, and EHR terms; searches PubMed, arXiv, and OpenAlex; and starts at 2023-01-01. This scenario stresses biomedical terminology, PubMed/OpenAlex behavior, heterogeneous full-text access, and structured evidence extraction.

### Scenario 2 — LLMs in HCI

The authoritative input is `output/llm-for-human-computer-interaction-survey/search_conditions.json`. It combines HCI, interactive-system, UI, and UX terms with LLM terms; searches PubMed, arXiv, and OpenAlex; and starts at 2020-01-01. This scenario stresses preprints and conference-oriented records, cross-source deduplication, and mixed methodological reporting.

### Scenario 3 — AI-assisted spatial authoring

The authoritative input is `output/ai-assisted-3d-spatial-authoring-vr-ar-mr/search_conditions.json`. It combines AI-assisted, generative-AI, conversational-AI, and LLM terms with 3D design, spatial authoring, VR, AR, MR, and XR terms; searches PubMed, arXiv, and OpenAlex; and starts at 2020-01-01. This scenario stresses broad noisy retrieval, interdisciplinary screening, difficult PDF sources, and heterogeneous extraction fields.

The diagnostic run for each scenario limits every platform to five records. A confirmation run may increase the limit to ten records per platform only after the five-record pass is stable and the added external API or LLM spend is justified.

## Iteration architecture

The implementation proceeds as three vertical scenario loops followed by a combined regression. Each loop is: create a clean project, execute all six stages through the browser, capture evidence, classify defects, implement the minimum root-cause fix, add a regression test that expresses the user intent, and rerun the clean project. Work unrelated to defects exposed by these scenarios is excluded.

Each run records its input configuration, project identifier, stage outputs, background-task states, browser screenshots at decision and failure points, defect log, relevant commit, automated regression result, and final disposition. External failures are classified separately as platform variability, credential or quota limitations, or product defects. External instability does not require ReviewPilot to make the service succeed, but ReviewPilot must represent the result accurately and offer a safe recovery action where recovery is possible.

## Product architecture and data flow

The existing Starlette monolith, `reviewpilot_core` workflow layer, and zero-build frontend remain in place. Local boundaries may be extracted only when a real defect demonstrates that a responsibility cannot be understood or tested independently.

Every mutating workflow action follows one data path:

`user action → API validation → project-level task lock → background task → validated atomic stage output → state projection → frontend polling and rendering`

The output directory is the workflow source of truth. Browser storage may preserve view state and draft presentation details, but it must never override server workflow state. The frontend restores the active task, last successful stage, error details, and currently permitted actions from the backend after refresh or project re-entry.

Only one mutating task may run for a project at a time. A duplicate submission returns the existing task or a clear conflict response; it must not consume the external API twice or overwrite output. Each stage exposes one of `not_started`, `ready`, `running`, `partial`, `failed`, or `completed`. The frontend must not infer completion solely from file existence.

Stage output is written to a temporary path and validated before atomic replacement. A failed attempt preserves the previous valid output and records the new failure. When upstream configuration changes, dependent downstream outputs become explicitly stale. The UI lists the affected stages and requires confirmation before recomputation can replace valid results.

## Error handling and recovery

The UI for every stage answers four questions: what happened, how much completed, what failed, and what the user can do next.

Retryable external errors include timeouts, rate limits, a source outage, and PDF-site blocking. Successful items remain available, failed sources or records are identified, and the primary recovery action retries only failed work. Correctable input errors include empty queries, conflicting schema fields, and categorization fields with no usable values. The UI retains the draft, identifies the exact invalid field, and prevents submission. Product and integrity errors include corrupt output, contradictory state, and abnormal task termination. They cannot appear as success; the UI shows a stable error identifier and actionable summary while technical details remain in logs.

Submitting a stage disables duplicate submission immediately and shows stage, elapsed time, and completed count. Refresh does not lose progress. Partial success is a distinct warning state, not a green completed state. Any retry that can replace valid output lists the affected data and requires confirmation. The assistant panel and main canvas must show the same task outcome.

## Verification design

### Environment gate

Before product changes, rebuild the repository virtual environment with a native arm64 Python. The current `.venv` links to an x86_64 Anaconda Python on an arm64 host. `pytest --collect-only` enters an uninterruptible wait even with plugin autoload disabled and a two-test pure-Python target, while the same pytest code collects those tests in 0.01 seconds under native arm64 Python. Test timeouts must not be used to conceal this environment fault.

### Automated verification layers

Unit and contract tests cover the stage state model, task mutual exclusion, atomic output replacement, downstream invalidation, error classification, API payloads, and frontend rendering contracts. Offline integration tests use fixed paper and PDF fixtures to cover the six-stage data path without external services. Browser E2E tests cover chat-based creation, form-based creation, refresh recovery, duplicate clicks, partial failure, retry-failed-only behavior, destructive rerun confirmation, and result export at both supported viewport sizes.

Stable defects found in real runs receive the smallest regression test that fails when the user intent is broken. Tests must encode why the behavior matters, not merely preserve an incidental implementation detail.

### Real-run acceptance matrix

For each of the three scenarios, evidence must show:

- A new project was created through the Web App with the intended query, sources, date range, and per-source limits.
- Collection produced source-level counts and represented a source failure accurately if one occurred.
- Screening produced inspectable included and excluded records without silent duplicate inflation.
- Full-text retrieval reported successes, failures, and partial completion, and supported safe failed-item retry.
- The extraction schema was reviewable and valid before extraction; extraction results retained traceable paper identity and did not present metadata fallback as PDF extraction.
- Categorization used a valid extracted field, produced inspectable assignments, and exported results without losing evidence links.
- Refresh and project re-entry restored truthful workflow and task state.
- No duplicate action silently launched duplicate external work or overwrote valid output.

## Release gate and deliverables

The candidate enters formal internal testing only when all three clean real runs complete the required journey; P0 blockers and integrity defects are zero; P1 dispositions are explicit; the full automated suite passes in the native environment; every skipped test is named with a reason; browser evidence covers refresh, failure, partial success, duplicate submission, and safe retry; and no unresolved contradiction exists between output files, API state, main canvas, and assistant messages.

The handoff includes an internal-test guide, the three scenario input definitions, a run and verification report, known limitations, log locations, and a structured issue-report template. Residual risk is stated directly; an unavailable or unverified requirement prevents the release-ready claim.
