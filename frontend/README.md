# ReviewPilot frontend

This directory contains the optimized zero-build frontend used by the Starlette
monolith in `web_app.py`.

```
frontend/
├── index.html   fonts, icons, reset CSS, and mount point
├── app.js       real project UI, workflow actions, project creation, state refresh
└── data.js      demo-only fallback for opening index.html directly
```

## Run it

Use the Python monolith from the repository root:

```bash
.venv/bin/uvicorn web_app:app --host 127.0.0.1 --port 5602 --reload
```

Open http://127.0.0.1:5602.
For frontend-only edits, keep the server running and refresh the page. For
Python edits, `--reload` restarts the app automatically.

## Data flow

`web_app.py` injects `window.RP_DATA` from `output/{project}/` and serves
`/static/app.js`. The static `data.js` file is not used by the migrated app; it is
only a design/demo fallback for direct file viewing.

The frontend calls:

- `POST /projects` to create a project.
- `GET /projects/{project_id}/state` to refresh state.
- `PUT /projects/{project_id}/setup` to save the search setup (concept blocks, sources, limits, dates); a change that makes results stale first returns the affected stages for confirmation.
- `DELETE /projects/{project_id}/setup/draft` to discard an imported or chat-proposed search setup draft.
- `POST /projects/{project_id}/chat` to send a chat message; the `step` field routes it (for example `search` revises the search setup and `screening` revises criteria).
- `GET /projects/{project_id}/configuration-reuse` and `POST /projects/{project_id}/configuration-reuse/{operation}` to list, preview, and import configurations from other local projects.
- `PATCH` / `DELETE /projects/{project_id}` to rename or delete a conversation (deletion moves it to `output/.trash`).
- `POST /projects/{project_id}/actions/{action}` to run workflow stages.
- `GET /tasks/{task_id}` to poll background task status.

Errors return JSON with a `detail` field carrying the reason, which the workspace shows to the user.

The primary source of truth remains the existing output folder shape:
`search_conditions.json`, `collected/`, `filtered/`, `pdfs/`, `extraction/`, and
`categorization/`.
