# ReviewPilot formal inner-beta runbook

## Release status

Use commit `b8ab180` or a descendant containing documentation-only changes. This is a local, single-user inner-beta candidate. Do not deploy it publicly or share one output directory across concurrent users.

## Operator setup

1. On Apple Silicon, bootstrap and verify the native environment:

   ```bash
   PYTHON_BIN=python3 scripts/bootstrap_native_env.sh
   file .venv-native/bin/python
   .venv-native/bin/python -c 'import platform; print(platform.machine())'
   ```

2. Configure the ignored local `config.py` and `secrets.txt` files. Never paste credentials into a run report, screenshot, chat, or issue.
3. Start the candidate:

   ```bash
   .venv-native/bin/uvicorn web_app:app --host 127.0.0.1 --port 5602
   ```

4. Open `http://127.0.0.1:5602/workspace` in Chrome.

## Launch smoke check

Before inviting testers, one operator must complete both desktop checks:

- At 1280 × 800 and 1440 × 900, open Search Setup, the recovery panel, final analysis, and Export Package. Confirm that controls are reachable and there is no horizontal page overflow or clipped primary action.
- Create one disposable bounded project, set each source limit to no more than 5, and confirm that the API-returned project ID matches the requested name.
- Run one stage, refresh during execution, and confirm that the same task resumes rather than starting a duplicate.
- Confirm that an Export Package item downloads through the visible Web UI and that no local absolute path appears in the page or assistant reply.
- Record the date, operator, browser version, viewport, project ID, and PASS/FAIL. A failed item blocks invitations until triaged.

## Cohort protocol

Recruit 3–5 internal users who have not worked on the implementation. Give them only this runbook and their configured local instance; do not coach them through the workflow.

Each tester should:

1. Create a new project from a real research question and set bounded source limits.
2. Run collection and screening; inspect the included/excluded counts.
3. Retrieve full texts. If any fail, use the failed-only recovery controls and verify the confirmation describes only the selected items.
4. Generate a Schema, review at least one field, finalize it, and run extraction.
5. Generate category suggestions, edit or consolidate them when necessary, explicitly confirm, and apply categorization.
6. Download at least one JSON and one NDJSON artifact from Export Package.
7. Refresh once while a task is running and once after completion.
8. Report whether the entire flow was completed without developer help, where they hesitated, and any incorrect or unsafe result.

## Issue policy

- P0: security breach, unrecoverable corruption, or the app cannot be used at all. Stop the beta immediately.
- P1: a core stage is blocked, state/artifact truth disagrees, data can be silently overwritten, or required export is inaccessible. Stop affected testing and fix before resuming.
- P2: the workflow remains possible but guidance, recoverability, or result quality is materially degraded. Triage before the next cohort round.
- P3: cosmetic or minor clarity issue with no material workflow impact. Record and batch after higher priorities.

For every issue, record the project ID, timestamp/timezone, browser and viewport, visible action, expected and actual behavior, safe task ID/status, stage counts, and whether an external service was involved. Do not attach credentials, local absolute paths, personal information, or full copyrighted PDFs.

## Beta acceptance

The formal inner beta is validated only when all invited testers complete or provide a classified failure, at least three users finish project setup through categorization without developer intervention, P0 remains 0, no P1 remains open, every partial external failure is understandable and recoverable, exports are accessible, and both supported viewport checks pass. Until then, describe the product as “ready for formal inner beta,” not “human-validated.”
