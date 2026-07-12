"""Starlette monolith entrypoint for the ReviewPilot frontend."""

from __future__ import annotations

import json
import re
import sys
from html import unescape
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent


def _prefer_local_package_imports() -> None:
    root = str(ROOT)
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)

    for package in ("agents", "utils"):
        module = sys.modules.get(package)
        module_file = getattr(module, "__file__", "") if module else ""
        if module_file and not Path(module_file).resolve().is_relative_to(ROOT):
            for name in list(sys.modules):
                if name == package or name.startswith(f"{package}."):
                    del sys.modules[name]


_prefer_local_package_imports()

from agents.lead_agent import LeadAgent
from reviewpilot_core.model_policy import DEFAULT_MAX_RESULTS_PER_PLATFORM, LEAD_AGENT_DEV_MODEL
from reviewpilot_core.state_projection import build_new_project_data, build_rp_data, list_projects
from reviewpilot_core.task_runner import TaskConflictError, TaskRunner


OUTPUT_ROOT = ROOT / "output"
FRONTEND_DIR = ROOT / "frontend"
task_runner = TaskRunner()


def render_workspace_html(output_root: Path | str = OUTPUT_ROOT, project_id: str | None = None) -> str:
    projects = list_projects(Path(output_root))
    active_project_id = project_id or (projects[0]["id"] if projects else "")
    state = build_rp_data(Path(output_root), active_project_id) if active_project_id else build_new_project_data(Path(output_root))
    return _render_html_with_state(state, build_new_project_data(Path(output_root)))


def render_index_html(output_root: Path | str = OUTPUT_ROOT, project_id: str | None = None) -> str:
    return render_workspace_html(output_root, project_id)


def render_new_project_html(output_root: Path | str = OUTPUT_ROOT) -> str:
    state = build_new_project_data(Path(output_root))
    return _render_html_with_state(state, state)


def _render_html_with_state(state: dict, new_project_state: dict) -> str:
    state_json = json.dumps(state, ensure_ascii=False).replace("</", "<\\/")
    new_state_json = json.dumps(new_project_state, ensure_ascii=False).replace("</", "<\\/")
    app_js_version = (FRONTEND_DIR / "app.js").stat().st_mtime_ns

    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace(
        '  <script src="data.js"></script>\n  <script src="app.js"></script>',
        f'  <script>window.RP_DATA = {state_json}; window.RP_NEW_PROJECT_DATA = {new_state_json};</script>\n  <script src="/static/app.js?v={app_js_version}"></script>',
    )
    return html


async def home(request):
    return RedirectResponse(url="/workspace")


async def favicon(request):
    return Response(status_code=204)


async def workspace_page(request):
    return HTMLResponse(render_workspace_html(OUTPUT_ROOT))


async def new_project_page(request):
    return HTMLResponse(render_new_project_html(OUTPUT_ROOT))


async def project_page(request):
    project_id = request.path_params["project_id"]
    if not known_project(OUTPUT_ROOT, project_id):
        raise HTTPException(status_code=404)
    return HTMLResponse(render_workspace_html(OUTPUT_ROOT, project_id))


async def project_state(request):
    project_id = request.path_params["project_id"]
    if not known_project(OUTPUT_ROOT, project_id):
        raise HTTPException(status_code=404)
    return JSONResponse(build_rp_data(OUTPUT_ROOT, project_id))


async def project_action(request):
    project_id = request.path_params["project_id"]
    action = request.path_params["action"]
    if not known_project(OUTPUT_ROOT, project_id):
        raise HTTPException(status_code=404)
    try:
        input_data = await _optional_json(request)
        task_id = submit_project_action(OUTPUT_ROOT, project_id, action, input_data=input_data)
    except TaskConflictError as exc:
        return JSONResponse({"detail": str(exc), "active_task": exc.task}, status_code=409)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"task_id": task_id, "status": "running"})


async def _optional_json(request) -> dict | None:
    body = await request.body()
    if not body:
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Action payload must be valid JSON") from exc
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError("Action payload must be a JSON object")
    return payload


async def project_chat(request):
    project_id = request.path_params["project_id"]
    if not known_project(OUTPUT_ROOT, project_id):
        raise HTTPException(status_code=404)
    try:
        payload = await request.json()
        message = str(payload.get("message") or "").strip()
        if not message:
            raise ValueError("message is required")
        context_step = str(payload.get("step") or "").strip()
        if context_step not in {"search", "screening", "retrieval", "extraction", "categorize"}:
            context_step = None
        result = LeadAgent(OUTPUT_ROOT).handle_message(project_id=project_id, message=message, context_step=context_step)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"reply": result.reply, "lead_agent": result.to_dict(), "state": build_rp_data(OUTPUT_ROOT, project_id)})


async def update_project_setup_api(request):
    project_id = request.path_params["project_id"]
    if not known_project(OUTPUT_ROOT, project_id):
        raise HTTPException(status_code=404)
    try:
        payload = await request.json()
        project = update_project_setup(OUTPUT_ROOT, project_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(project)


async def task_status(request):
    task_id = request.path_params["task_id"]
    task = task_runner.get(task_id)
    if task is None:
        raise HTTPException(status_code=404)
    return JSONResponse(task)


async def projects(request):
    return JSONResponse({"projects": list_projects(OUTPUT_ROOT)})


async def create_project_api(request):
    try:
        payload = await request.json()
        project = create_project(OUTPUT_ROOT, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(project, status_code=201, headers={"Location": "/workspace"})


def create_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/", home, methods=["GET"]),
            Route("/favicon.ico", favicon, methods=["GET"]),
            Route("/workspace", workspace_page, methods=["GET"]),
            Route("/projects", projects, methods=["GET"]),
            Route("/projects", create_project_api, methods=["POST"]),
            Route("/projects/new", new_project_page, methods=["GET"]),
            Route("/projects/{project_id}", project_page, methods=["GET"]),
            Route("/projects/{project_id}/state", project_state, methods=["GET"]),
            Route("/projects/{project_id}/chat", project_chat, methods=["POST"]),
            Route("/projects/{project_id}/setup", update_project_setup_api, methods=["PUT"]),
            Route("/projects/{project_id}/actions/{action}", project_action, methods=["POST"]),
            Route("/tasks/{task_id}", task_status, methods=["GET"]),
            Mount("/static", app=StaticFiles(directory=str(FRONTEND_DIR)), name="static"),
        ]
    )


def known_project(output_root: Path | str, project_id: str) -> bool:
    if project_id in {"", ".", ".."}:
        return False
    return any(project["id"] == project_id for project in list_projects(Path(output_root)))


def create_project(output_root: Path | str, payload: dict) -> dict:
    config = _setup_config(payload)
    project_id = _unique_project_id(Path(output_root), _slugify(config["project_name"]))
    search_conditions = _run_lead_agent_search_setup(output_root, project_id, config)
    return {"id": project_id, "title": search_conditions["project_name"], "path": search_conditions["project_path"]}


def update_project_setup(output_root: Path | str, project_id: str, payload: dict) -> dict:
    if not known_project(output_root, project_id):
        raise ValueError("project not found")
    config = _setup_config(payload)
    search_conditions = _run_lead_agent_search_setup(output_root, project_id, config)
    return {"id": project_id, "title": search_conditions["project_name"], "path": search_conditions["project_path"]}


def _run_lead_agent_search_setup(output_root: Path | str, project_id: str, config: dict) -> dict:
    result = LeadAgent(Path(output_root)).save_search_setup(project_id, config)
    return result.search_conditions


def _setup_config(payload: dict) -> dict:
    title = _payload_text(payload, "project_name", "title")
    description = _payload_text(payload, "description", "research_question")
    if not title:
        raise ValueError("project_name is required")
    if not description:
        raise ValueError("description is required")

    platforms = _normalize_platforms(payload.get("platforms"))
    search_terms = _payload_text(payload, "search_terms") or description
    max_results = _positive_int(payload.get("max_results"), default=DEFAULT_MAX_RESULTS_PER_PLATFORM)
    source_limits = _normalize_source_limits(payload.get("source_limits"), platforms, default=max_results)
    max_results = max(source_limits.values()) if source_limits else max_results
    derive_search_terms = bool(payload.get("derive_search_terms"))
    return {
        "project_name": title,
        "description": description,
        "primary_topic": _payload_text(payload, "primary_topic") or ("" if derive_search_terms else title),
        "domain": _payload_text(payload, "domain"),
        "search_terms": search_terms,
        "search_queries": [{"name": "main", "query": search_terms}],
        "platforms": platforms,
        "max_results": max_results,
        "source_limits": source_limits,
        "date_range": {
            "start": _payload_text(payload, "date_start", "start"),
            "end": _payload_text(payload, "date_end", "end"),
        },
        "model": _payload_text(payload, "model") or LEAD_AGENT_DEV_MODEL,
        "derive_search_terms": derive_search_terms,
    }


def _payload_text(payload: dict, *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = unescape(str(value).strip())
        if text:
            return text
    return ""


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "review-project"


def _unique_project_id(output_root: Path, base: str) -> str:
    candidate = base
    suffix = 2
    while (output_root / candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _normalize_platforms(value) -> list[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list):
        items = value
    else:
        items = ["pubmed", "openalex", "arxiv"]
    platforms = [str(item).strip().lower() for item in items if str(item).strip()]
    return platforms or ["pubmed", "openalex", "arxiv"]


def _normalize_source_limits(value, platforms: list[str], default: int) -> dict[str, int]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    if not isinstance(value, dict):
        value = {}
    return {platform: _positive_int(value.get(platform), default=default) for platform in platforms}


def _positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def submit_project_action(output_root: Path | str, project_id: str, action: str, llm_query=None, input_data: dict | None = None) -> str:
    supported_actions = {"collect", "screen", "download-pdfs", "generate-schema", "finalize-schema", "edit-schema", "run-extraction", "suggest-categories", "categorize"}
    if action not in supported_actions:
        raise ValueError(f"Unsupported action: {action}")

    def run_action():
        agent = LeadAgent(Path(output_root), llm_query=llm_query)
        if input_data is None:
            return agent.handle_message(project_id=project_id, action=action).to_dict()
        return agent.handle_message(project_id=project_id, action=action, input_data=input_data).to_dict()

    return task_runner.submit(
        project_id,
        action,
        run_action,
    )


def _empty_state() -> dict:
    return {
        "isNewProject": False,
        "project": {"title": "ReviewPilot", "status": "No project", "model": "", "date": ""},
        "steps": [],
        "optionalCapabilities": [],
        "fields": [],
        "platforms": [],
        "keywords": [],
        "groups": [],
        "retrieved": [],
        "previewFields": [],
        "messages": [{"step": 1, "role": "a", "text": "No saved project found."}],
        "activityByStep": {},
        "quietLabels": {},
        "quietActions": {},
        "ctxLabels": {},
        "history": [],
        "screeningMetrics": {"identified": 0, "afterDedup": 0, "included": 0},
        "retrievalSummary": {"retrieved": 0, "total": 0, "openAccess": 0, "viaInstitution": 0, "unavailable": 0},
        "categorizationSummary": {"papers": 0, "groups": 0},
    }


app = create_app()
