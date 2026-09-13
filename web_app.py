"""Starlette monolith entrypoint for the ReviewPilot frontend."""

from __future__ import annotations

import json
import re
import secrets
import sys
import tempfile
from html import unescape
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
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
from reviewpilot_core.agent_memory import CrossProjectMemoryService, MemoryStoreError
from reviewpilot_core.model_policy import DEFAULT_MAX_RESULTS_PER_PLATFORM, LEAD_AGENT_DEV_MODEL
from reviewpilot_core.atomic_files import atomic_write_json, atomic_write_jsonl
from reviewpilot_core.setup_revision import abandon_setup_transaction, affected_stages, begin_setup_transaction, finish_setup_transaction, mark_setup_transaction_aborting, materially_changes_dependencies, normalize_setup, promote_setup_transaction, reconcile_setup_transaction, setup_revision, stale_replacement_stages, update_setup_transaction_target
from reviewpilot_core.state_projection import EXPORT_ARTIFACTS, build_new_project_data, build_rp_data, export_artifact_path, list_projects, _history
from reviewpilot_core.extraction_preview import project_preview_projection, run_project_preview
from reviewpilot_core.task_runner import TaskConflictError, TaskRunner
from reviewpilot_core.project_store import read_json, read_jsonl
from reviewpilot_core import record_review
from reviewpilot_core.demo_projects import is_example, require_mutable, copy_example
from reviewpilot_core.evidence_support import local_pdf
from reviewpilot_core.configuration_reuse import configuration_options, preview_configuration, apply_configuration, ReuseConflict
from reviewpilot_core.screening_criteria import require_finalized_criteria, validate_criteria, criteria_state
from reviewpilot_core.workflow_state import complete_action, fail_action, initialize_workflow_state, load_workflow_state, mark_stages_stale, save_workflow_state, start_action
from reviewpilot_core.workflow_adapter import WorkflowActionAdapter
from reviewpilot_core.retrieval_retry import ConfirmationRequired as RetryConfirmationRequired, InvalidRetryRequest, RevisionConflict, merge_staged_retry_facts, prepare_retry_publication, prepare_retry_request
from reviewpilot_core.retrieval_retry_transaction import abandon_retry_transaction, abort_retry_transaction, apply_retry_transaction, begin_retry_transaction, publish_retry_transaction_pdfs, reconcile_retry_transaction, record_retry_transaction_target, retry_target_is_committed, run_retry_transaction_staging


OUTPUT_ROOT = ROOT / "output"
FRONTEND_DIR = ROOT / "frontend"
task_runner = TaskRunner()


class ConfirmationRequired(ValueError):
    def __init__(self, revision: str, stages: list[str]):
        self.revision = revision
        self.stages = stages
        super().__init__(f"Overwrite confirmation required for stale stages: {', '.join(stages)}")


class SetupRevisionConflict(ValueError):
    pass


def build_project_state(output_root: Path | str, project_id: str) -> dict:
    active_task = task_runner.active_for_project(project_id)
    project_path = Path(output_root) / project_id
    record_review.recover(project_path)
    retry_is_active = active_task is not None and active_task["action"] == "retry-failed-downloads"
    if (project_path / ".retrieval_retry_pending.json").exists() and not retry_is_active:
        reconcile_retry_transaction(project_path)
    state = build_rp_data(Path(output_root), project_id, active_action=active_task["action"] if active_task else None)
    from reviewpilot_core import workflow_decisions
    decisions = workflow_decisions.projection(project_path)
    state['categorizationWorkflow']['decisions'] = decisions
    if decisions['selection']:
        choice = decisions['selection']
        state['categorizationWorkflow'].update(selectedField=choice['field'], mode=choice['mode'], suggestedCategories=choice['categories'])
    if decisions['finalized']:
        state['project']['status'] = 'Complete'
    state["reviewWorkbench"] = record_review.projection(project_path)
    state["readOnlyExample"] = is_example(Path(output_root), project_id)
    state["exampleOrigin"] = read_json(project_path / "example_origin.json", None)
    state["activeTask"] = active_task
    return state


def render_workspace_html(output_root: Path | str = OUTPUT_ROOT, project_id: str | None = None) -> str:
    projects = list_projects(Path(output_root))
    active_project_id = project_id or (projects[0]["id"] if projects else "")
    state = build_project_state(output_root, active_project_id) if active_project_id else build_new_project_data(Path(output_root))
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
    html = html.replace('<script src="review.js"></script>', f'<script src="/static/review.js?v={(FRONTEND_DIR / "review.js").stat().st_mtime_ns}"></script>')
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
    return HTMLResponse(render_new_project_html(OUTPUT_ROOT))


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
    return JSONResponse(build_project_state(OUTPUT_ROOT, project_id))


async def session_history(request):
    return JSONResponse({"history": _history(Path(OUTPUT_ROOT), "")})


async def manage_session(request):
    project_id = request.path_params["project_id"]
    root = Path(OUTPUT_ROOT)
    if not known_project(root, project_id) or (root / project_id).is_symlink():
        raise HTTPException(404)
    if any(item["id"] == project_id for item in _history(root, "")[0]["items"]):
        raise HTTPException(403, "Example conversations cannot be renamed or deleted.")
    payload = {}
    if request.method == "PATCH":
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(400, "Invalid JSON.")
        title = payload.get("title") if isinstance(payload, dict) else None
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
            raise HTTPException(400, "Use a conversation name between 1 and 120 characters.")
    def mutate():
        path = root / project_id
        if not path.is_dir():
            raise HTTPException(404)
        if request.method == "PATCH":
            atomic_write_json(path / "session.json", {"title": title.strip()})
        else:
            trash = root / ".trash"
            trash.mkdir(exist_ok=True)
            if trash.is_symlink():
                raise HTTPException(409, "Invalid trash directory.")
            path.rename(trash / f"{project_id}-{secrets.token_hex(8)}")
    try:
        task_runner.run_if_idle(project_id, mutate)
    except TaskConflictError as exc:
        raise HTTPException(409, "This conversation has a running task. Try again when it finishes.") from exc
    return JSONResponse({"history": _history(root, "")})


class ProtectExamples(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        match = re.match(r"^/projects/([^/]+)(/.*)?$", request.url.path)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and match:
            project_id, suffix = match.group(1), match.group(2) or ""
            if is_example(Path(OUTPUT_ROOT), project_id) and suffix != "/copy-example":
                return JSONResponse({"detail": "This example is read-only. Create your own copy to edit or run it."}, status_code=403)
        return await call_next(request)


async def copy_example_api(request):
    project_id = request.path_params["project_id"]
    try:
        result = task_runner.run_if_idle(project_id, lambda: copy_example(Path(OUTPUT_ROOT), project_id))
        return JSONResponse(result, status_code=201)
    except TaskConflictError:
        return JSONResponse({"detail": "Wait for the example's current task to finish before copying."}, status_code=409)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


async def review_records_api(request):
    project_id = request.path_params["project_id"]
    if not known_project(OUTPUT_ROOT, project_id):
        raise HTTPException(404)
    project = Path(OUTPUT_ROOT) / project_id
    record_review.recover(project)
    try:
        operation = request.path_params.get("operation")
        if request.method == "GET":
            if operation == "field":
                return JSONResponse(record_review.field_detail(project, request.query_params.get("key"), request.query_params.get("field")))
            if operation == "pdf":
                record_key = request.query_params.get("key")
                row = record_review.unique_record(read_jsonl(project / "extraction/extraction_results.jsonl"), record_key)
                papers = [r for r in read_jsonl(project / "filtered/included_papers.jsonl") if record_review.key(r) == record_key]
                paper = record_review.unique_record(papers, record_key) if papers else {}
                path = local_pdf(project, row, paper)
                if path is None:
                    raise HTTPException(404)
                return FileResponse(path, media_type="application/pdf")
            return JSONResponse(record_review.projection(project))
        require_mutable(Path(OUTPUT_ROOT), project_id)
        payload = await _optional_json(request) or {}
        if operation not in {"screening", "field", "decisions"}:
            raise ValueError("Unknown review operation")
        from reviewpilot_core import workflow_decisions
        save = workflow_decisions.save if operation == 'decisions' else record_review.save_screening if operation == "screening" else record_review.save_field
        task_runner.run_if_idle(project_id, lambda: save(project, payload))
        return JSONResponse({"state": build_project_state(OUTPUT_ROOT, project_id)})
    except (TaskConflictError, record_review.ReviewConflict) as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)
    except (ValueError, TypeError) as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


async def extraction_preview(request):
    project_id = request.path_params["project_id"]
    if not known_project(OUTPUT_ROOT, project_id):
        raise HTTPException(status_code=404)
    try:
        projection = project_preview_projection(Path(OUTPUT_ROOT) / project_id, request.path_params["paper_index"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(projection)


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
    except RetryConfirmationRequired as exc:
        return JSONResponse({"code": exc.code, "detail": str(exc), "confirmationRequired": True, "expectedReportRevision": exc.expected_report_revision, "failedIds": list(exc.failed_ids)}, status_code=409)
    except RevisionConflict as exc:
        return JSONResponse({"code": exc.code, "detail": str(exc), "expectedReportRevision": exc.expected_report_revision}, status_code=409)
    except InvalidRetryRequest as exc:
        return JSONResponse({"code": exc.code, "detail": str(exc)}, status_code=400)
    except ConfirmationRequired as exc:
        return JSONResponse({"detail": str(exc), "confirmationRequired": True, "expectedRevision": exc.revision, "affectedStages": exc.stages}, status_code=409)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"task_id": task_id, "status": "running"})


async def _optional_json(request) -> dict | None:
    body = await request.body()
    if not body:
        return None
    try:
        def invalid_constant(value):
            raise ValueError('JSON numbers must be finite.')
        payload = json.loads(body, parse_constant=invalid_constant)
        # Exponent overflow (for example 1e999) also produces infinity in Python.
        json.dumps(payload, allow_nan=False)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
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
        payload = await _optional_json(request) or {}
        message = str(payload.get("message") or "").strip()
        if not message:
            raise ValueError("message is required")
        context_step = str(payload.get("step") or "").strip()
        if context_step not in {"search", "screening", "retrieval", "extraction", "categorize"}:
            context_step = None
        def respond():
            return LeadAgent(OUTPUT_ROOT).handle_message(project_id=project_id, message=message, context_step=context_step)
        # Any chat can save messages or edit a draft schema. Keep deletion,
        # configuration changes and workflow tasks from racing that publication.
        result = task_runner.run_if_idle(project_id, respond)
    except TaskConflictError as exc:
        return JSONResponse({"detail": str(exc), "active_task": exc.task}, status_code=409)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"reply": result.reply, "lead_agent": result.to_dict(), "state": build_project_state(OUTPUT_ROOT, project_id)})


async def update_project_setup_api(request):
    project_id = request.path_params["project_id"]
    if not known_project(OUTPUT_ROOT, project_id):
        raise HTTPException(status_code=404)
    try:
        payload = await _optional_json(request) or {}
        project = update_project_setup(OUTPUT_ROOT, project_id, payload)
    except TaskConflictError as exc:
        return JSONResponse({"detail": str(exc), "active_task": exc.task}, status_code=409)
    except SetupRevisionConflict as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)
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


async def memory_settings(request):
    # Retire the legacy automatic-injection setting, including previously enabled stores.
    if request.method == "PUT":
        try:
            payload = await _optional_json(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not isinstance(payload, dict) or set(payload) != {"cross_project_memory_enabled"} or payload["cross_project_memory_enabled"] is not False:
            raise HTTPException(status_code=400, detail="Automatic memory is disabled. Use Reuse project configuration.")
    return JSONResponse({"cross_project_memory_enabled": False})


async def project_configuration_reuse(request):
    project_id = request.path_params["project_id"]
    try:
        if request.method == "GET":
            return JSONResponse({"configurations": configuration_options(Path(OUTPUT_ROOT), project_id, busy=task_runner.active_for_project)})
        payload = await _optional_json(request) or {}
        source_id = payload.get("source_project_id")
        if request.path_params.get("operation") not in {"preview", "apply"}:
            raise ValueError("Unsupported configuration reuse operation")
        operation = apply_configuration if request.path_params.get("operation") == "apply" else preview_configuration
        result = task_runner.run_if_idle(source_id, lambda: task_runner.run_if_idle(project_id, lambda: operation(Path(OUTPUT_ROOT), project_id, payload)))
        return JSONResponse(result)
    except TaskConflictError as exc:
        return JSONResponse({"detail": str(exc), "active_task": exc.task}, status_code=409)
    except ReuseConflict as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def clear_memory(request):
    try:
        CrossProjectMemoryService(OUTPUT_ROOT).clear()
    except MemoryStoreError:
        return JSONResponse({"detail": "Memory is unavailable"}, status_code=503)
    return JSONResponse({"cleared": True})


async def project_export(request):
    project_id = request.path_params["project_id"]
    export_key = request.path_params["export_key"]
    artifact = EXPORT_ARTIFACTS.get(export_key)
    if not artifact or not known_project(OUTPUT_ROOT, project_id):
        raise HTTPException(status_code=404)
    _label, relative_path, media_type = artifact
    artifact_path = export_artifact_path(Path(OUTPUT_ROOT) / project_id, export_key)
    if artifact_path is None:
        raise HTTPException(status_code=404)
    if export_key == 'workflow-decisions':
        from reviewpilot_core import workflow_decisions
        return JSONResponse({'current': workflow_decisions.projection(Path(OUTPUT_ROOT) / project_id),
                             'saved': read_json(artifact_path, {})},
                            headers={'Content-Disposition': 'attachment; filename="workflow_decisions.json"'})
    return FileResponse(
        artifact_path,
        media_type=media_type,
        filename=relative_path.name,
        content_disposition_type="attachment",
    )


async def create_project_api(request):
    try:
        payload = await _optional_json(request) or {}
        project = create_project(OUTPUT_ROOT, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(project, status_code=201, headers={"Location": "/workspace"})


def create_app() -> Starlette:
    return Starlette(
        middleware=[Middleware(ProtectExamples)],
        routes=[
            Route("/", home, methods=["GET"]),
            Route("/favicon.ico", favicon, methods=["GET"]),
            Route("/workspace", workspace_page, methods=["GET"]),
            Route("/projects", projects, methods=["GET"]),
            Route("/sessions", session_history, methods=["GET"]),
            Route("/projects/{project_id}", manage_session, methods=["PATCH", "DELETE"]),
            Route("/projects", create_project_api, methods=["POST"]),
            Route("/projects/{project_id}/configuration-reuse", project_configuration_reuse, methods=["GET"]),
            Route("/projects/{project_id}/configuration-reuse/{operation}", project_configuration_reuse, methods=["POST"]),
            Route("/memory/settings", memory_settings, methods=["GET", "PUT"]),
            Route("/memory", clear_memory, methods=["DELETE"]),
            Route("/projects/new", new_project_page, methods=["GET"]),
            Route("/projects/{project_id}", project_page, methods=["GET"]),
            Route("/projects/{project_id}/state", project_state, methods=["GET"]),
            Route("/projects/{project_id}/copy-example", copy_example_api, methods=["POST"]),
            Route("/projects/{project_id}/review", review_records_api, methods=["GET"]),
            Route("/projects/{project_id}/review/{operation}", review_records_api, methods=["GET", "PATCH"]),
            Route("/projects/{project_id}/extraction-preview/{paper_index:int}", extraction_preview, methods=["GET"]),
            Route("/projects/{project_id}/exports/{export_key}", project_export, methods=["GET"]),
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
    search_conditions = {**normalize_setup(config), **search_conditions}
    search_conditions["setup_revision"] = setup_revision(search_conditions)
    atomic_write_json(Path(output_root) / project_id / "search_conditions.json", search_conditions)
    initial_messages = [{"step": 1, "role": "u", "text": config["description"], "source": "initial_setup"}]
    if search_conditions.get("lead_agent_reply"):
        initial_messages.append({"step": 1, "role": "a", "text": search_conditions["lead_agent_reply"], "source": "initial_setup"})
    atomic_write_jsonl(Path(output_root) / project_id / "chat/messages.jsonl", initial_messages)
    initialize_workflow_state(Path(output_root) / project_id)
    return {"id": project_id, "title": search_conditions["project_name"], "path": search_conditions["project_path"]}


def update_project_setup(output_root: Path | str, project_id: str, payload: dict) -> dict:
    require_mutable(Path(output_root), project_id)
    if not known_project(output_root, project_id):
        raise ValueError("project not found")
    active = task_runner.active_for_project(project_id)
    if active:
        raise TaskConflictError(active)
    config = _setup_config(payload)
    confirmation = payload.get("confirmation") if isinstance(payload.get("confirmation"), dict) else {}
    project_path = Path(output_root) / project_id
    imported = read_json(project_path / "memory/search_setup_draft.json", {})
    if imported and not config.get("derive_search_terms") and all(config.get(key) == imported.get(key) for key in ("search_terms", "description", "primary_topic", "domain")):
        for key in ("search_queries", "concept_blocks", "keywords", "primary_synonyms", "domain_synonyms"):
            if key in imported:
                config[key] = imported[key]

    def transact():
        reconcile_setup_transaction(project_path)
        current = json.loads((project_path / "search_conditions.json").read_text(encoding="utf-8"))
        current_revision = setup_revision(current)
        next_revision = setup_revision(config)
        changed = current_revision != next_revision
        impacts = affected_stages(project_path) if changed and materially_changes_dependencies(current, config) else []
        expected = confirmation.get("expected_revision")
        if expected is not None and expected != current_revision:
            raise SetupRevisionConflict("Setup confirmation revision is stale; refresh and review the new impact")
        if impacts and expected is None:
            return {"id": project_id, "confirmationRequired": True, "expectedRevision": current_revision, "proposedRevision": next_revision, "affectedStages": impacts}
        if not changed:
            (project_path / "memory/search_setup_draft.json").unlink(missing_ok=True)
            return {"id": project_id, "title": current.get("project_name") or project_id, "confirmationRequired": False, "setupRevision": current_revision}
        ledger_before = load_workflow_state(project_path)
        pending = begin_setup_transaction(project_path, current, config, impacts)
        try:
            search_conditions = _run_lead_agent_search_setup(output_root, project_id, config)
            persisted = {**normalize_setup(config), **search_conditions}
            persisted["setup_revision"] = setup_revision(persisted)
            persisted.setdefault("project_path", str(project_path))
            update_setup_transaction_target(pending, persisted)
            atomic_write_json(project_path / "search_conditions.json", persisted)
            if impacts:
                mark_stages_stale(project_path, impacts)
            promote_setup_transaction(pending)
        except Exception:
            try:
                mark_setup_transaction_aborting(pending)
            except Exception:
                pass
            try:
                atomic_write_json(project_path / "search_conditions.json", current)
                save_workflow_state(project_path, ledger_before)
            except Exception:
                raise
            finish_setup_transaction(project_path)
            raise
        else:
            finish_setup_transaction(project_path)
            (project_path / "memory/search_setup_draft.json").unlink(missing_ok=True)
            return {"id": project_id, "title": persisted["project_name"], "path": persisted["project_path"], "confirmationRequired": False, "setupRevision": persisted["setup_revision"], "affectedStages": impacts}
        finally:
            abandon_setup_transaction(project_path)

    if hasattr(task_runner, "run_if_idle"):
        return task_runner.run_if_idle(project_id, transact)
    return transact()


def _run_lead_agent_search_setup(output_root: Path | str, project_id: str, config: dict) -> dict:
    result = LeadAgent(Path(output_root)).save_search_setup(project_id, config)
    return result.search_conditions


def _setup_config(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('Search setup must be a JSON object.')
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
    if not derive_search_terms and _payload_text(payload, 'search_terms'):
        from reviewpilot_core.query_syntax import parse
        parse(search_terms)
    from reviewpilot_core.publication_dates import resolve_range
    bounds = resolve_range({"start": _payload_text(payload, "date_start", "start"), "end": _payload_text(payload, "date_end", "end")})
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
        "date_range": bounds,
        "model": _payload_text(payload, "model") or LEAD_AGENT_DEV_MODEL,
        "derive_search_terms": derive_search_terms,
        "interpret_chat_settings": payload.get("interpret_chat_settings") is True,
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
        items = ["pubmed", "arxiv", "openalex"]
    platforms = [str(item).strip().lower() for item in items if str(item).strip()]
    return platforms or ["pubmed", "arxiv", "openalex"]


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
    require_mutable(Path(output_root), project_id)
    record_review.recover(Path(output_root) / project_id)
    if action == "review-sample":
        return task_runner.submit(project_id, action, lambda: record_review.run_sample(Path(output_root) / project_id, input_data or {}, llm_query=llm_query))
    supported_actions = {"edit-criteria", "save-criteria", "finalize-criteria", "collect", "screen", "download-pdfs", "retry-failed-downloads", "generate-schema", "regenerate-schema", "finalize-schema", "edit-schema", "run-extraction", "finalize-and-run-extraction", "preview-extraction", "suggest-categories", "categorize"}
    if action not in supported_actions:
        raise ValueError(f"Unsupported action: {action}")

    if action == "retry-failed-downloads":
        return _submit_retry_action(output_root, project_id, input_data, llm_query)

    project_path = Path(output_root) / project_id
    if action in {"save-criteria", "finalize-criteria", "edit-criteria"}:
        if action != "edit-criteria":
            validate_criteria(input_data or {})
        if (input_data or {}).get("revision") != criteria_state(project_path)["revision"]:
            raise ValueError("Screening criteria changed. Refresh before saving.")
        return task_runner.submit(project_id, action, lambda: LeadAgent(Path(output_root), llm_query=llm_query).handle_message(
            project_id=project_id, action=action, input_data=input_data).to_dict())
    if action == "preview-extraction":
        index = (input_data or {}).get("paper_index") if isinstance(input_data, dict) else None
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("paper_index must be a non-negative integer")
        project_preview_projection(project_path, index)
        return task_runner.submit(
            project_id,
            action,
            lambda: run_project_preview(project_path, index, llm_query=llm_query),
            metadata={"paper_index": index},
        )
    action_stage = {"collect": "collection", "screen": "screening", "download-pdfs": "retrieval", "generate-schema": "extraction", "regenerate-schema": "extraction", "finalize-schema": "extraction", "edit-schema": "extraction", "run-extraction": "extraction", "finalize-and-run-extraction": "extraction", "suggest-categories": "categorization", "categorize": "categorization"}[action]
    confirmation = input_data.get("overwrite_confirmation") if isinstance(input_data, dict) and isinstance(input_data.get("overwrite_confirmation"), dict) else {}
    if confirmation:
        input_data = {key: value for key, value in input_data.items() if key != "overwrite_confirmation"}
    previous_state: list[dict] = []

    def prepare_action():
        reconcile_setup_transaction(project_path)
        if action == "collect" and (project_path / "memory/search_setup_draft.json").exists():
            raise ValueError("Review and save the imported search setup before running collection.")
        if action == "screen":
            require_finalized_criteria(project_path)
        if action in {'categorize', 'suggest-categories'} and (input_data or {}).get('decision_revision'):
            from reviewpilot_core import workflow_decisions
            if input_data['decision_revision'] != workflow_decisions.revision(project_path):
                raise ValueError('Project decisions changed. Refresh before applying categorization.')
        replacements = stale_replacement_stages(project_path, action_stage)
        current_setup = json.loads((project_path / "search_conditions.json").read_text(encoding="utf-8"))
        if replacements and (confirmation.get("expected_revision") != setup_revision(current_setup) or confirmation.get("affected_stages") != replacements):
            raise ConfirmationRequired(setup_revision(current_setup), replacements)
        previous_state.append(load_workflow_state(project_path))
        start_action(project_path, action)

    def rollback_action():
        if previous_state:
            save_workflow_state(project_path, previous_state[0])

    def run_action():
        try:
            agent = LeadAgent(Path(output_root), llm_query=llm_query)
            if input_data is None:
                result = agent.handle_message(project_id=project_id, action=action).to_dict()
            else:
                result = agent.handle_message(project_id=project_id, action=action, input_data=input_data).to_dict()
        except Exception as exc:
            fail_action(project_path, action, exc)
            raise
        try:
            complete_action(project_path, action, result.get("data") if isinstance(result.get("data"), dict) else result)
        except Exception as exc:
            fail_action(project_path, action, exc)
            raise
        return result

    return task_runner.submit(
        project_id,
        action,
        run_action,
        prepare=prepare_action,
        rollback=rollback_action,
    )


def _retry_target_ledger(before_ledger: dict, success: int, failed: int) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        oracle = Path(directory)
        save_workflow_state(oracle, before_ledger)
        start_action(oracle, "retry-failed-downloads")
        return complete_action(
            oracle,
            "retry-failed-downloads",
            {"success": success, "failed": failed},
        )


def _submit_retry_action(output_root: Path | str, project_id: str, input_data: dict | None, llm_query=None) -> str:
    project = Path(output_root) / project_id
    context: dict = {}

    def prepare_action():
        reconcile_setup_transaction(project)
        reconcile_retry_transaction(project)
        preparation = prepare_retry_request(project, input_data)
        before_ledger = load_workflow_state(project)
        staging_name = f".retrieval_retry_staging_{secrets.token_hex(16)}"
        handle = begin_retry_transaction(project, preparation, staging_name, project / staging_name)
        context.update(preparation=preparation, before_ledger=before_ledger, handle=handle)

    def rollback_action():
        handle = context.get("handle")
        if handle is not None:
            abort_retry_transaction(project, handle.transaction_id)

    def run_action():
        preparation = context["preparation"]
        handle = context["handle"]
        plan = None
        target_ledger = None
        apply_started = False
        try:
            outcome = run_retry_transaction_staging(
                project,
                preparation,
                lambda root, retry_project_id: WorkflowActionAdapter().run(
                    "download-pdfs", root, retry_project_id, llm_query=llm_query
                ),
            )
            merged = merge_staged_retry_facts(preparation, outcome)
            plan = prepare_retry_publication(project, preparation, outcome, merged)
            counts = merged.counts
            target_ledger = _retry_target_ledger(
                context["before_ledger"], counts["succeeded"], counts["failed"]
            )
            record_retry_transaction_target(project, plan, target_ledger)
            publish_retry_transaction_pdfs(project)
            result = {
                "stage": "retrieval",
                "status": merged.status,
                "data": {
                    "success": counts["succeeded"],
                    "failed": counts["failed"],
                    "retried": len(preparation.selected_ids),
                    "recovered": len(outcome.successful_pdfs),
                },
            }
            apply_started = True
            try:
                apply_retry_transaction(project, handle.transaction_id)
            except Exception:
                abandon_retry_transaction(project, handle.transaction_id)
                try:
                    reconcile_retry_transaction(project)
                except ValueError:
                    pass
                if plan is not None and target_ledger is not None and retry_target_is_committed(project, plan, target_ledger):
                    return result
                raise
            return result
        except Exception:
            if not apply_started:
                try:
                    abort_retry_transaction(project, handle.transaction_id)
                except ValueError:
                    abandon_retry_transaction(project, handle.transaction_id)
                    try:
                        reconcile_retry_transaction(project)
                    except ValueError:
                        pass
            raise
        finally:
            abandon_retry_transaction(project, handle.transaction_id)

    return task_runner.submit(
        project_id,
        "retry-failed-downloads",
        run_action,
        prepare=prepare_action,
        rollback=rollback_action,
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
        "extractionPreview": {"index": 0, "total": 0, "canPrevious": False, "canNext": False, "status": "missing", "paper": {"id": "", "title": "No paper preview available", "ref": ""}, "fields": [], "source": "", "error": ""},
        "schemaJson": {"fields": []},
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
