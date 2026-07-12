# ReviewPilot inner-beta evidence protocol

This directory freezes the reproducible inputs and evidence requirements for the three real-use inner-beta scenarios. `scenarios.json` is the execution contract; changes require a reviewed catalog update and a passing `tests/test_inner_beta_scenarios.py` regression.

## Scenario provenance

The catalog preserves the exact `search_terms`, description, primary topic, and domain from these authoritative historical artifacts. Limits and dates are the bounded inner-beta execution settings approved in the design.

| Scenario | Historical source path | SHA-256 |
| --- | --- | --- |
| `biomedicine` | `output/llm-biomedicine-survey/search_conditions.json` | `83c2277ba55cb6eecbbac3830421da93bc5305066037042e7b4b23caebe2da36` |
| `hci` | `output/llm-for-human-computer-interaction-survey/search_conditions.json` | `36dd295f83c713fd5ddd533ad0bf8372708690223c2e4d7135315f7dbea235b7` |
| `spatial_authoring` | `output/ai-assisted-3d-spatial-authoring-vr-ar-mr/search_conditions.json` | `9858dbcb5940bc54bb969fb240ede7ece6b3779f1a854150956636575fe8ff01` |

For every execution, append `-r<N>` to the catalog's `project_name`, starting at `-r1` and increasing `N` for every rerun. Never reopen, overwrite, or continue a historical scenario output. Record the resulting rerun project id in the report.

## Required run evidence

Create one Markdown report per scenario and rerun. Capture all of the following:

- Start and end timestamp with timezone for the overall run and each workflow task.
- Browser name and browser viewport in CSS pixels.
- Runtime project id, project output path, task id, task status, and the observed terminal state.
- Input identity: scenario key; catalog revision as the SHA-256 of the executed `scenarios.json`; source revision as the historical source SHA-256 from the provenance table; actual `search_terms` query; platforms/sources; date range; and per-source limits. After saving setup, compare every catalog payload field against the persisted artifact and record the explicit field-by-field conclusion: **saved `search_conditions.json` equals the catalog** or list every mismatch.
- Relevant tested Git commit SHA for the application code exercised by the run. If fixes are applied, record both the failing run SHA and each rerun SHA.
- Per-stage counts for inputs, successes, failures, and output records, including collection counts by source.
- Every external error, classified as authentication, rate limit, timeout/network, source availability/access, malformed response, or unknown. Preserve the safe message and affected stage/item without including request headers or tokens.
- Screenshot paths for the initial setup, each terminal workflow state, visible errors or partial results, final review, and export result.
- Artifact paths for search conditions, collected papers, screening decisions, downloads, extraction outputs, synthesis/report outputs, and exports. Record absent expected artifacts explicitly.
- Defect priority for each finding (`P0` blocks all use, `P1` blocks the scenario or risks loss/corruption, `P2` materially degrades the workflow, `P3` is minor), reproduction steps, expected and actual behavior, and evidence links.
- The exact regression command and its exit result for each fix or confirmed behavior.
- Pass/fail disposition for the rerun and every stage, plus the release impact and remaining concerns.

The report must distinguish product defects from external service failures. A stage passes only when its UI terminal state, task status, per-stage counts, and artifact contents agree. A partial result is not a pass unless the product explicitly reports partial success and identifies every failed item.

## Credential safety

Credentials and secret values must never enter a report, screenshot, fixture, command transcript, committed artifact, task payload, error excerpt, or test output. Record only whether a required credential was configured and whether authentication succeeded. Review staged files and diffs before every evidence commit.
