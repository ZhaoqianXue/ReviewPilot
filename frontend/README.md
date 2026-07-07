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
- `POST /projects/{project_id}/actions/{action}` to run workflow stages.
- `GET /tasks/{task_id}` to poll background task status.

The primary source of truth remains the existing output folder shape:
`search_conditions.json`, `collected/`, `filtered/`, `pdfs/`, `extraction/`, and
`categorization/`.
