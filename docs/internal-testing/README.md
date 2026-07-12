# ReviewPilot inner-beta evidence protocol

This directory freezes the reproducible inputs and evidence requirements for the three real-use inner-beta scenarios. `scenarios.json` is the execution contract; changes require a reviewed catalog update and a passing `tests/test_inner_beta_scenarios.py` regression.

## Scenario provenance

The catalog preserves the exact `search_terms`, description, primary topic, and domain from these authoritative historical artifacts. Limits and dates are the bounded inner-beta execution settings approved in the design.

| Scenario | Historical source path | SHA-256 |
| --- | --- | --- |
| `biomedicine` | `output/llm-biomedicine-survey/search_conditions.json` | `83c2277ba55cb6eecbbac3830421da93bc5305066037042e7b4b23caebe2da36` |
| `hci` | `output/llm-for-human-computer-interaction-survey/search_conditions.json` | `36dd295f83c713fd5ddd533ad0bf8372708690223c2e4d7135315f7dbea235b7` |
| `spatial_authoring` | `output/ai-assisted-3d-spatial-authoring-vr-ar-mr/search_conditions.json` | `9858dbcb5940bc54bb969fb240ede7ece6b3779f1a854150956636575fe8ff01` |

The immutable, sanitized snapshots in `provenance/` make this linkage verifiable from a clean checkout; the ignored historical output files are not required. `provenance/manifest.json` binds each snapshot hash to its original relative source path and SHA-256. The original historical SHA is provenance metadata, while the tracked snapshot and its manifest hash are the executable verification source. Do not edit a snapshot in place after a recorded run; add a new manifest revision and explain the input change.

## Deterministic run identity

For every execution, append `-r<N>` to the catalog's `project_name`. Determine the next `N` by scanning both exact output project IDs matching `<catalog-project_name>-r<N>` and tracked run reports matching `docs/internal-testing/runs/<YYYY-MM-DD>-<scenario-key>-r<N>.md`; choose `max(all matching N, 0) + 1`. Never fill a numbering gap or reuse an identity.

Before calling the create-project API, reserve the identity by creating both the exact report path `docs/internal-testing/runs/<YYYY-MM-DD>-<scenario-key>-r<N>.md` and exact evidence directory `docs/internal-testing/evidence/<scenario-key>-r<N>/`. The report header must record the scenario key, requested rerun project id, `N`, reservation timestamp, and tested Git commit SHA. Abort rather than overwrite if either path already exists. Record the API-returned id; if it differs from the requested project id, mark the run failed and abort all workflow stages. Never reopen, overwrite, or continue a historical scenario output.

## Catalog-to-persisted projection

The Web App normalizes the catalog payload before writing `search_conditions.json`, so literal file equality is neither expected nor valid. Compare and report a result for every projected field:

| Catalog/runtime value | Persisted/API expectation |
| --- | --- |
| `project_name` plus `-r<N>` | Requested `project_name`, persisted `project_name`, and API-returned slug `id` all equal the requested project id |
| `description`, `primary_topic`, `domain` | Same-named persisted fields equal the catalog values |
| `search_terms` | Persisted `search_terms` equals it and `search_queries` equals `[{"name": "main", "query": <search_terms>}]` |
| `platforms` | Persisted `platforms` equals the ordered catalog list |
| `max_results` | Persisted `max_results` and `max_results_per_platform` equal it |
| `source_limits` | Persisted per-source limits equal the catalog object |
| `date_start`, `date_end` | Persisted `date_range` equals `{"start": <date_start>, "end": <date_end>}` |

The following system-generated or compatibility fields are ignored for value equality after checking that no unexpected or sensitive data is present: `project_path`, `research_description`, `extracted_concepts`, `extraction_fields`, `arxiv_query`, `model`, `derive_search_terms`, `keywords`, `lead_agent_reply`, `llm_usage`, and `generated_by`. Any other persisted field is unexpected and must be reported. For `project_path`, still verify that its final path component equals the requested project id, but never copy the local user path into evidence.

An empty catalog `date_end` is an open upper bound, not a frozen end date. Record the effective run cutoff (the run start date/time) and any source-specific interpretation of the open bound.

## Required run evidence

Create one Markdown report per scenario and rerun. Capture all of the following:

- Start and end timestamp with timezone for the overall run and each workflow task.
- Browser name and browser viewport in CSS pixels.
- Runtime project id, project output path, task id, task status, and the observed terminal state.
- Input identity: scenario key; catalog revision as the SHA-256 of the executed `scenarios.json`; tracked snapshot and manifest revisions; source revision as historical source SHA-256 metadata; actual `search_terms` query; platforms/sources; date range and effective run cutoff; and per-source limits. After saving setup, record a field-by-field pass/fail with actual and expected values for every catalog-to-persisted projected field listed above.
- Relevant tested Git commit SHA for the application code exercised by the run. If fixes are applied, record both the failing run SHA and each rerun SHA.
- Per-stage counts for inputs, successes, failures, and output records, including collection counts by source.
- Every external error, classified as authentication, rate limit, timeout/network, source availability/access, malformed response, or unknown. Record occurrence timestamp, operation and affected stage/item, safe HTTP status code when available, attempt number, retryability, recovery action, and final resolution. Preserve only a safe message without request headers or tokens.
- Screenshot paths for the initial setup, each terminal workflow state, visible errors or partial results, final review, and export result.
- Artifact paths for search conditions, collected papers, screening decisions, downloads, extraction outputs, synthesis/report outputs, and exports. Record absent expected artifacts explicitly.
- Defect priority for each finding (`P0` blocks all use, `P1` blocks the scenario or risks loss/corruption, `P2` materially degrades the workflow, `P3` is minor), reproduction steps, expected and actual behavior, and evidence links.
- The exact regression command and its exit result for each fix or confirmed behavior.
- Pass/fail disposition for the rerun and every stage, plus the release impact and remaining concerns.

The report must distinguish product defects from external service failures. A stage passes only when its UI terminal state, task status, per-stage counts, and artifact contents agree. A partial result is not a pass unless the product explicitly reports partial success and identifies every failed item.

## Credential safety

Credentials and secret values must never enter a report, screenshot, fixture, command transcript, committed artifact, task payload, error excerpt, or test output. Record only whether a required credential was configured and whether authentication succeeded. Review staged files and diffs before every evidence commit.

Before committing, review every screenshot and artifact for credentials, PII/personal identifiers, local user paths, and copyrighted/full-text content; redact or omit them as appropriate while preserving enough metadata to reproduce the product behavior.
