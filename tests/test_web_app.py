import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

import web_app
from starlette.testclient import TestClient
from agents.extraction_agent import ExtractionAgent
from reviewpilot_core.state_projection import build_rp_data
from reviewpilot_core.task_runner import TaskRunner
from reviewpilot_core.screening_criteria import criteria_state, save_criteria
from web_app import create_project, render_index_html, render_workspace_html


def terminal_result(action: str) -> dict:
    return {
        "collect": {"total": 0, "platform_stats": {"pubmed": 0}, "platform_errors": {}},
        "download-pdfs": {"success": 0, "failed": 0},
        "run-extraction": {"processed": 0, "errors": 0},
    }.get(action, {})


class WebAppTests(unittest.TestCase):
    def test_preview_endpoint_and_action_use_public_index_without_mutating_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "demo"
            (project / "extraction").mkdir(parents=True)
            (project / "filtered").mkdir(parents=True)
            (project / "search_conditions.json").write_text(json.dumps({"project_name": "demo"}), encoding="utf-8")
            (project / "extraction" / "extraction_schema_draft.json").write_text(json.dumps({"fields": [{"name": "methods"}]}), encoding="utf-8")
            (project / "filtered" / "included_papers.jsonl").write_text(json.dumps({"id": "p1", "title": "Paper A"}) + "\n", encoding="utf-8")
            ledger_path = project / "workflow_state.json"
            ledger_path.write_text(json.dumps({"sentinel": "unchanged"}), encoding="utf-8")
            old_root, old_runner = web_app.OUTPUT_ROOT, web_app.task_runner
            try:
                web_app.OUTPUT_ROOT = root
                web_app.task_runner = TaskRunner(max_workers=1)
                client = TestClient(web_app.create_app())
                preview = client.get("/projects/demo/extraction-preview/0")
                self.assertEqual(preview.status_code, 200)
                self.assertEqual(preview.json()["paper"]["title"], "Paper A")
                with patch.object(web_app, "run_project_preview", return_value={"status": "preview_ready", "paper_index": 0, "total": 1}):
                    response = client.post("/projects/demo/actions/preview-extraction", json={"paper_index": 0})
                    task = web_app.task_runner.wait(response.json()["task_id"], timeout=2)
                ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            finally:
                web_app.task_runner.shutdown()
                web_app.task_runner = old_runner
                web_app.OUTPUT_ROOT = old_root

        self.assertEqual(response.status_code, 200)
        self.assertEqual(task["paper_index"], 0)
        self.assertEqual(task["status"], "completed")
        self.assertEqual(ledger, {"sentinel": "unchanged"})

    def test_preview_action_rejects_bool_and_out_of_range_indexes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "demo"
            project.mkdir()
            (project / "search_conditions.json").write_text(json.dumps({"project_name": "demo"}), encoding="utf-8")
            for index in (True, -1):
                with self.subTest(index=index), self.assertRaisesRegex(ValueError, "paper_index"):
                    web_app.submit_project_action(root, "demo", "preview-extraction", input_data={"paper_index": index})

    def test_structured_partial_and_zero_success_task_status_match_ledger_and_refresh_projection(self):
        for expected_status, data in (
            ("partial", {"total": 1, "platform_stats": {"pubmed": 1, "arxiv": 0}, "platform_errors": {"arxiv": "timeout"}}),
            ("failed", {"total": 0, "platform_stats": {"pubmed": 0}, "platform_errors": {"pubmed": "timeout"}}),
        ):
            with self.subTest(expected_status=expected_status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); project = root / expected_status; project.mkdir()
                (project / "search_conditions.json").write_text(json.dumps({"project_name": expected_status, "search_terms": "x", "platforms": ["pubmed"]}))
                from reviewpilot_core.workflow_state import initialize_workflow_state
                initialize_workflow_state(project)
                class FakeResult:
                    def to_dict(self):
                        return {"stage": "collection", "status": expected_status, "reply": "structured", "data": data}
                class FakeLeadAgent:
                    def __init__(self, *_args, **_kwargs): pass
                    def handle_message(self, **_kwargs): return FakeResult()
                runner_before = web_app.task_runner
                try:
                    web_app.task_runner = TaskRunner()
                    with patch.object(web_app, "LeadAgent", FakeLeadAgent):
                        task_id = web_app.submit_project_action(root, expected_status, "collect")
                        task = web_app.task_runner.wait(task_id, 2)
                    projected = build_rp_data(root, expected_status)
                finally:
                    web_app.task_runner.shutdown(); web_app.task_runner = runner_before
                self.assertEqual(task["status"], expected_status)
                self.assertEqual(projected["stageState"]["collection"]["status"], expected_status)
                self.assertEqual(projected["steps"][0]["status"], expected_status)

    def test_malformed_structured_result_escapes_worker_and_marks_task_and_ledger_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); project=root/"demo"; project.mkdir(); (project/"search_conditions.json").write_text(json.dumps({"project_name":"demo","search_terms":"x","platforms":["pubmed"]}))
            from reviewpilot_core.workflow_state import initialize_workflow_state
            initialize_workflow_state(project)
            class FakeResult:
                def to_dict(self): return {"stage":"collection","status":"completed","reply":"bad","data":{"platform_stats":[],"platform_errors":{}}}
            class FakeLeadAgent:
                def __init__(self,*_args,**_kwargs): pass
                def handle_message(self,**_kwargs): return FakeResult()
            previous=web_app.task_runner
            try:
                web_app.task_runner=TaskRunner()
                with patch.object(web_app,"LeadAgent",FakeLeadAgent): task_id=web_app.submit_project_action(root,"demo","collect")
                task=web_app.task_runner.wait(task_id,2); ledger=json.loads((project/"workflow_state.json").read_text())
            finally:
                web_app.task_runner.shutdown(); web_app.task_runner=previous
        self.assertEqual(task["status"],"failed"); self.assertEqual(ledger["stages"]["collection"]["status"],"failed")
    def test_submit_rejects_schema_actions_before_ledger_prerequisites_without_entering_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project = output_root / "demo"
            (project / "extraction").mkdir(parents=True)
            (project / "search_conditions.json").write_text(json.dumps({"project_name": "demo"}), encoding="utf-8")
            (project / "extraction" / "extraction_schema.json").write_text(json.dumps({"fields": [{"name": "x"}]}), encoding="utf-8")
            from reviewpilot_core.workflow_state import initialize_workflow_state
            initialize_workflow_state(project)

            for action in ("finalize-schema", "edit-schema"):
                with self.subTest(action=action), self.assertRaisesRegex(ValueError, "requires completed stage 'screening'"):
                    web_app.submit_project_action(output_root, "demo", action)
            ledger = json.loads((project / "workflow_state.json").read_text(encoding="utf-8"))

        self.assertEqual(ledger["stages"]["extraction"]["status"], "not_started")
        self.assertEqual(ledger["stages"]["extraction"]["attempt"], 0)

    def test_task_status_endpoint_does_not_expose_exception_paths(self):
        old_runner = web_app.task_runner
        try:
            web_app.task_runner = TaskRunner(max_workers=1)
            task_id = web_app.task_runner.submit("demo", "collect", lambda: (_ for _ in ()).throw(RuntimeError("/Users/alice/private.txt")))
            web_app.task_runner.wait(task_id, timeout=2)
            payload = TestClient(web_app.create_app()).get(f"/tasks/{task_id}").json()
        finally:
            web_app.task_runner.shutdown()
            web_app.task_runner = old_runner

        self.assertNotIn("/Users", payload["error"])
        self.assertIn("RuntimeError", payload["error"])

    def test_create_project_initializes_workflow_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project = create_project(output_root, {"project_name": "Ledger", "description": "Review ledgers"})
            ledger = json.loads((output_root / project["id"] / "workflow_state.json").read_text(encoding="utf-8"))

        self.assertEqual(ledger["stages"]["collection"]["status"], "ready")
        self.assertEqual(ledger["stages"]["screening"]["status"], "not_started")

    def test_action_lifecycle_persists_running_completed_and_failed(self):
        old_runner = web_app.task_runner
        release = Event()
        started = Event()
        try:
            web_app.task_runner = TaskRunner(max_workers=1)
            with tempfile.TemporaryDirectory() as tmp:
                output_root = Path(tmp)
                project_dir = output_root / "demo"
                project_dir.mkdir()
                (project_dir / "search_conditions.json").write_text(json.dumps({"project_name": "demo"}), encoding="utf-8")
                from reviewpilot_core.workflow_state import initialize_workflow_state
                initialize_workflow_state(project_dir)

                class FakeResult:
                    def to_dict(self):
                        return {"stage": "collection", "status": "completed", "data": {"total_papers": 9, "platform_stats": {"pubmed": 9}, "platform_errors": {}}}

                class FakeLeadAgent:
                    def __init__(self, output_root, llm_query=None):
                        pass
                    def handle_message(self, **kwargs):
                        started.set()
                        release.wait()
                        return FakeResult()

                with patch.object(web_app, "LeadAgent", FakeLeadAgent):
                    task_id = web_app.submit_project_action(output_root, "demo", "collect")
                    self.assertTrue(started.wait(1))
                    running = json.loads((project_dir / "workflow_state.json").read_text(encoding="utf-8"))
                    self.assertEqual(running["stages"]["collection"]["status"], "running")
                    projected = web_app.build_project_state(output_root, "demo")
                    self.assertEqual(projected["stageState"]["collection"]["status"], "running")
                    self.assertEqual(projected["steps"][0]["status"], "active")
                    release.set()
                    self.assertEqual(web_app.task_runner.wait(task_id, 2)["status"], "completed")
                completed = json.loads((project_dir / "workflow_state.json").read_text(encoding="utf-8"))
                self.assertEqual(completed["stages"]["collection"]["counts"], {"succeeded": 1, "failed": 0, "collected": 9})

                class FailingLeadAgent(FakeLeadAgent):
                    def handle_message(self, **kwargs):
                        raise RuntimeError("private failure /Users/name/file")

                with patch.object(web_app, "LeadAgent", FailingLeadAgent):
                    save_criteria(project_dir, criteria_state(project_dir), finalized=True)
                    failed_id = web_app.submit_project_action(output_root, "demo", "screen")
                    self.assertEqual(web_app.task_runner.wait(failed_id, 2)["status"], "failed")
                failed = json.loads((project_dir / "workflow_state.json").read_text(encoding="utf-8"))
                self.assertEqual(failed["stages"]["screening"]["status"], "failed")
                self.assertNotIn("Users", failed["stages"]["screening"]["error"])
        finally:
            release.set()
            web_app.task_runner.shutdown()
            web_app.task_runner = old_runner

    def test_project_state_reconciles_orphan_running_but_not_matching_active_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir()
            (project_dir / "search_conditions.json").write_text(json.dumps({"project_name": "demo"}), encoding="utf-8")
            from reviewpilot_core.workflow_state import initialize_workflow_state, start_action
            initialize_workflow_state(project_dir)
            start_action(project_dir, "collect")

            state = web_app.build_project_state(output_root, "demo")

        self.assertEqual(state["stageState"]["collection"]["status"], "failed")
        self.assertIn("restart", state["stageState"]["collection"]["error"])

    def test_project_exports_download_only_existing_allow_listed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            (project_dir / "prompts").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text('{"project_name":"demo"}', encoding="utf-8")
            (project_dir / "prompts" / "relevance_prompt.json").write_text('{"task":"screen"}', encoding="utf-8")
            with patch.object(web_app, "OUTPUT_ROOT", output_root):
                client = TestClient(web_app.create_app())
                response = client.get("/projects/demo/exports/search-setup")
                prompt = client.get("/projects/demo/exports/relevance-prompt")
                unknown = client.get("/projects/demo/exports/unknown")
                missing = client.get("/projects/demo/exports/included-papers")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertIn('attachment; filename="search_conditions.json"', response.headers["content-disposition"])
        self.assertEqual(prompt.status_code, 200)
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertNotIn(str(output_root), unknown.text + missing.text)

    def test_project_exports_reject_other_projects_traversal_and_non_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            demo = output_root / "demo"
            other = output_root / "other"
            demo.mkdir()
            other.mkdir()
            (demo / "search_conditions.json").write_text('{"project_name":"demo"}', encoding="utf-8")
            (other / "search_conditions.json").write_text('{"project_name":"other"}', encoding="utf-8")
            (demo / "filtered" / "included_papers.jsonl").mkdir(parents=True)
            (other / "secret.jsonl").write_text('{"secret":true}', encoding="utf-8")
            (output_root / "alias").symlink_to(other, target_is_directory=True)
            (demo / "extraction").mkdir()
            (demo / "extraction" / "extraction_results.jsonl").symlink_to(other / "secret.jsonl")
            (demo / "private.jsonl").write_text('{"same_project_secret":true}', encoding="utf-8")
            (demo / "categorization").mkdir()
            (demo / "categorization" / "categorized_results.jsonl").symlink_to(demo / "private.jsonl")
            (demo / "private-reports").mkdir()
            (demo / "private-reports" / "download_report.json").write_text('{"private":true}', encoding="utf-8")
            (demo / "pdfs").symlink_to(demo / "private-reports", target_is_directory=True)
            with patch.object(web_app, "OUTPUT_ROOT", output_root):
                client = TestClient(web_app.create_app())
                other_project = client.get("/projects/missing/exports/search-setup")
                traversal = client.get("/projects/demo/exports/..%2Fsearch-setup")
                directory = client.get("/projects/demo/exports/included-papers")
                escaped_symlink = client.get("/projects/demo/exports/extraction-results")
                same_project_symlink = client.get("/projects/demo/exports/categorized-results")
                intermediate_symlink = client.get("/projects/demo/exports/download-report")
                aliased_project = client.get("/projects/alias/exports/search-setup")
        self.assertEqual(other_project.status_code, 404)
        self.assertEqual(traversal.status_code, 404)
        self.assertEqual(directory.status_code, 404)
        self.assertEqual(escaped_symlink.status_code, 404)
        self.assertEqual(same_project_symlink.status_code, 404)
        self.assertEqual(intermediate_symlink.status_code, 404)
        self.assertEqual(aliased_project.status_code, 404)
        self.assertNotIn("same_project_secret", same_project_symlink.text)
        self.assertNotIn(str(output_root), same_project_symlink.text + intermediate_symlink.text)

    def test_render_workspace_injects_active_and_new_project_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "demo", "description": "Real project"}),
                encoding="utf-8",
            )
            html = render_workspace_html(output_root, "demo")

        self.assertIn("window.RP_DATA =", html)
        self.assertIn("window.RP_NEW_PROJECT_DATA =", html)
        self.assertIn('"id": "demo"', html)
        self.assertIn('"label": "Search Setup"', html)
        self.assertIn('"sub": "3 sources"', html)
        self.assertRegex(html, r'<script src="/static/app\.js\?v=\d+"></script>')
        self.assertNotIn('<script src="/static/app.js"></script>', html)
        self.assertNotIn('id="rp-new-project-form"', html)

    def test_workspace_is_default_route_and_project_routes_render_deep_links(self):
        old_output_root = web_app.OUTPUT_ROOT
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "demo", "description": "Real project"}),
                encoding="utf-8",
            )
            web_app.OUTPUT_ROOT = output_root
            client = TestClient(web_app.create_app())

            root = client.get("/", follow_redirects=False)
            new_project = client.get("/projects/new", follow_redirects=False)
            project = client.get("/projects/demo", follow_redirects=False)
            workspace = client.get("/workspace")
        web_app.OUTPUT_ROOT = old_output_root

        self.assertEqual(root.headers["location"], "/workspace")
        self.assertEqual(new_project.status_code, 200)
        self.assertIn('window.RP_DATA = {"isNewProject": true', new_project.text)
        self.assertEqual(project.status_code, 200)
        self.assertIn('window.RP_DATA = {"isNewProject": false, "project": {"id": "demo"', project.text)
        self.assertEqual(workspace.status_code, 200)
        self.assertIn("window.RP_DATA =", workspace.text)

    def test_render_index_injects_project_state_and_skips_demo_data_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "demo", "description": "Real project"}),
                encoding="utf-8",
            )

            html = render_index_html(output_root, "demo")

        self.assertIn("window.RP_DATA =", html)
        self.assertIn('"title": "demo"', html)
        self.assertIn("Real project", html)
        self.assertRegex(html, r'<script src="/static/app\.js\?v=\d+"></script>')
        self.assertNotIn('<script src="/static/app.js"></script>', html)
        self.assertNotIn('<script src="data.js"></script>', html)

    def test_render_index_escapes_closing_script_in_json_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "demo", "description": "</script><script>alert(1)</script>"}),
                encoding="utf-8",
            )

            html = render_index_html(output_root, "demo")

        self.assertIn("<\\/script><script>alert(1)<\\/script>", html)
        self.assertNotIn("</script><script>alert(1)</script>", html)

    def test_known_project_checks_project_ids_from_output_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "demo"}),
                encoding="utf-8",
            )

            self.assertTrue(web_app.known_project(output_root, "demo"))
            self.assertFalse(web_app.known_project(output_root, "missing"))
            self.assertFalse(web_app.known_project(output_root, ".."))

    def test_create_project_writes_old_compatible_search_conditions(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)

            project = create_project(
                output_root,
                {
                    "project_name": "My Review!",
                    "description": "Review AI in surgery",
                    "primary_topic": "AI",
                    "domain": "surgery",
                    "search_terms": "AI AND surgery",
                    "platforms": "pubmed, openalex",
                    "max_results": "25",
                    "date_start": "2020-01-01",
                    "date_end": "2026-12-31",
                    "model": "gpt-5-mini",
                },
            )

            config = json.loads((output_root / "my-review" / "search_conditions.json").read_text(encoding="utf-8"))

        self.assertEqual(project["id"], "my-review")
        self.assertEqual(config["project_name"], "My Review!")
        self.assertEqual(config["description"], "Review AI in surgery")
        self.assertEqual(config["platforms"], ["pubmed", "openalex"])
        self.assertEqual(config["search_queries"], [{"name": "main", "query": "AI AND surgery"}])
        self.assertEqual(config["date_range"], {"start": "2020-01-01", "end": "2026-12-31"})
        self.assertEqual(config["max_results"], 25)
        self.assertEqual(config["source_limits"], {"pubmed": 25, "openalex": 25})

    def test_create_project_unescapes_html_entities_from_frontend_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)

            project = create_project(
                output_root,
                {
                    "project_name": "LLM &amp; Medicine",
                    "description": "Review LLMs for medicine &amp; care",
                    "search_terms": "(&quot;large language model&quot; OR LLM) AND medicine",
                    "platforms": "pubmed",
                    "max_results": "10",
                },
            )

            config = json.loads((output_root / project["id"] / "search_conditions.json").read_text(encoding="utf-8"))

        self.assertEqual(config["project_name"], "LLM & Medicine")
        self.assertEqual(config["description"], "Review LLMs for medicine & care")
        self.assertEqual(config["search_terms"], '("large language model" OR LLM) AND medicine')
        self.assertEqual(config["search_queries"], [{"name": "main", "query": '("large language model" OR LLM) AND medicine'}])

    def test_create_project_defaults_to_development_lead_agent_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)

            create_project(
                output_root,
                {
                    "project_name": "Model Default Review",
                    "description": "Review LLM in biomedicine",
                    "platforms": "pubmed",
                },
            )

            config = json.loads((output_root / "model-default-review" / "search_conditions.json").read_text(encoding="utf-8"))

        self.assertEqual(config["model"], "gpt-5.4-mini")

    def test_create_project_defaults_max_results_per_source_to_ten(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)

            create_project(
                output_root,
                {
                    "project_name": "Default Limit Review",
                    "description": "Review LLMs in care delivery",
                    "platforms": "pubmed, openalex, arxiv",
                },
            )

            config = json.loads((output_root / "default-limit-review" / "search_conditions.json").read_text(encoding="utf-8"))

        self.assertEqual(config["max_results"], 10)
        self.assertEqual(config["source_limits"], {"pubmed": 10, "openalex": 10, "arxiv": 10})

    def test_create_project_defaults_to_frozen_platform_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)

            create_project(
                output_root,
                {
                    "project_name": "Default Source Order Review",
                    "description": "Review LLMs in clinical care",
                },
            )

            config = json.loads((output_root / "default-source-order-review" / "search_conditions.json").read_text(encoding="utf-8"))

        self.assertEqual(config["platforms"], ["pubmed", "arxiv", "openalex"])
        self.assertEqual(list(config["source_limits"]), ["pubmed", "arxiv", "openalex"])

    def test_create_project_preserves_explicit_platform_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)

            create_project(
                output_root,
                {
                    "project_name": "Explicit Source Order Review",
                    "description": "Review LLMs in clinical care",
                    "platforms": ["openalex", "pubmed", "arxiv"],
                },
            )

            config = json.loads((output_root / "explicit-source-order-review" / "search_conditions.json").read_text(encoding="utf-8"))

        self.assertEqual(config["platforms"], ["openalex", "pubmed", "arxiv"])
        self.assertEqual(list(config["source_limits"]), ["openalex", "pubmed", "arxiv"])

    def test_create_project_routes_search_setup_through_lead_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            calls = []

            class FakeLeadAgentResult:
                def __init__(self, search_conditions):
                    self.search_conditions = search_conditions

            class FakeLeadAgent:
                def __init__(self, output_root):
                    self.output_root = output_root

                def save_search_setup(self, project_id, config):
                    calls.append((self.output_root, project_id, dict(config)))
                    project_path = Path(self.output_root) / project_id
                    project_path.mkdir(parents=True, exist_ok=True)
                    agent_config = {**config, "project_path": str(project_path), "agent_marker": True}
                    (project_path / "search_conditions.json").write_text(
                        json.dumps(agent_config),
                        encoding="utf-8",
                    )
                    return FakeLeadAgentResult(agent_config)

            with patch.object(web_app, "LeadAgent", FakeLeadAgent):
                project = create_project(
                    output_root,
                    {
                        "project_name": "My Review!",
                        "description": "Review AI in surgery",
                        "primary_topic": "AI",
                        "domain": "surgery",
                        "search_terms": "AI AND surgery",
                        "platforms": "pubmed, openalex",
                        "max_results": "25",
                    },
                )

            config = json.loads((output_root / "my-review" / "search_conditions.json").read_text(encoding="utf-8"))

        self.assertEqual(project["id"], "my-review")
        self.assertEqual(calls[0][0], output_root)
        self.assertEqual(calls[0][1], "my-review")
        self.assertTrue(config["agent_marker"])

    def test_create_project_preserves_per_source_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)

            create_project(
                output_root,
                {
                    "project_name": "Per Source Review",
                    "description": "Review AI in surgery",
                    "platforms": "pubmed, openalex, arxiv",
                    "max_results": "50",
                    "source_limits": {"pubmed": "10", "openalex": "25", "arxiv": "75"},
                },
            )

            config = json.loads((output_root / "per-source-review" / "search_conditions.json").read_text(encoding="utf-8"))

        self.assertEqual(config["source_limits"], {"pubmed": 10, "openalex": 25, "arxiv": 75})
        self.assertEqual(config["max_results"], 75)

    def test_create_project_derives_search_terms_from_chat_topic_with_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            calls = []

            def fake_llm_query(*, text_prompt, system_prompt, model, provider):
                calls.append((text_prompt, system_prompt, model, provider))
                return (
                    json.dumps(
                        {
                            "reply": "LLM generated this Search Setup.",
                            "research_description": "Survey LLM systems in biomedicine",
                            "concept_blocks": [
                                {"label": "Large language models (LLMs)", "role": "phenomenon", "eligibility_group": "technology", "required_for_eligibility": True, "query_terms": ["large language model", "LLM"]},
                                {"label": "Biomedicine", "role": "context", "eligibility_group": "context", "required_for_eligibility": True, "query_terms": ["biomedicine", "biomedical"]},
                            ],
                        }
                    ),
                    {"model": "gpt-5.4-mini"},
                )

            with patch("agents.search_condition_agent.query_llm", fake_llm_query):
                project = create_project(
                    output_root,
                    {
                        "project_name": "LLM Biomedicine Search",
                        "description": "I want to do a survey in terms of LLM for biomedicine project. give me search terms.",
                        "derive_search_terms": True,
                        "platforms": "pubmed, openalex, arxiv",
                        "max_results": "50",
                    },
                )

            config = json.loads((output_root / "llm-biomedicine-search" / "search_conditions.json").read_text(encoding="utf-8"))

        self.assertEqual(project["id"], "llm-biomedicine-search")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], "gpt-5.4-mini")
        self.assertIn('<reviewpilot-agent-skill name="systematic-review-search-strategy"', calls[0][1])
        expected_query = '("large language model" OR LLM) AND (biomedicine OR biomedical)'
        self.assertEqual(project["title"], "LLM Biomedicine Search")
        self.assertEqual(config["project_name"], "LLM Biomedicine Search")
        self.assertEqual(config["search_terms"], expected_query)
        self.assertEqual(config["search_queries"], [{"name": "main", "query": expected_query}])
        self.assertEqual(config["primary_topic"], "Large language models (LLMs)")
        self.assertEqual(config["generated_by"], "llm")
        self.assertEqual(config["lead_agent_reply"], "LLM generated this Search Setup.")

    def test_create_project_uses_unique_slug_and_rejects_empty_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            (output_root / "demo").mkdir(parents=True)
            (output_root / "demo" / "search_conditions.json").write_text(
                json.dumps({"project_name": "demo"}),
                encoding="utf-8",
            )

            project = create_project(
                output_root,
                {
                    "project_name": "Demo",
                    "description": "A real review",
                    "search_terms": "",
                    "platforms": ["openalex"],
                },
            )

            with self.assertRaises(ValueError):
                create_project(output_root, {"project_name": "Empty", "description": "   "})

        self.assertEqual(project["id"], "demo-2")

    def test_update_project_setup_rewrites_existing_search_conditions(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "demo", "description": "Old", "platforms": ["pubmed"]}),
                encoding="utf-8",
            )

            project = web_app.update_project_setup(
                output_root,
                "demo",
                {
                    "project_name": "Updated",
                    "description": "New question",
                    "primary_topic": "AI",
                    "domain": "medicine",
                    "search_terms": "AI AND medicine",
                    "platforms": "pubmed, arxiv",
                    "max_results": "20",
                },
            )
            config = json.loads((project_dir / "search_conditions.json").read_text(encoding="utf-8"))

        self.assertEqual(project["id"], "demo")
        self.assertEqual(config["project_name"], "Updated")
        self.assertEqual(config["description"], "New question")
        self.assertEqual(config["platforms"], ["pubmed", "arxiv"])
        self.assertEqual(config["search_queries"], [{"name": "main", "query": "AI AND medicine"}])
        self.assertEqual(config["max_results"], 20)
        self.assertEqual(config["source_limits"], {"pubmed": 20, "arxiv": 20})

    def test_update_project_setup_routes_existing_project_through_lead_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "demo", "description": "Old", "platforms": ["pubmed"]}),
                encoding="utf-8",
            )
            calls = []

            class FakeLeadAgentResult:
                def __init__(self, search_conditions):
                    self.search_conditions = search_conditions

            class FakeLeadAgent:
                def __init__(self, output_root):
                    self.output_root = output_root

                def save_search_setup(self, project_id, config):
                    calls.append((self.output_root, project_id, dict(config)))
                    project_path = Path(self.output_root) / project_id
                    project_path.mkdir(parents=True, exist_ok=True)
                    agent_config = {**config, "project_path": str(project_path), "agent_marker": True}
                    (project_path / "search_conditions.json").write_text(
                        json.dumps(agent_config),
                        encoding="utf-8",
                    )
                    return FakeLeadAgentResult(agent_config)

            with patch.object(web_app, "LeadAgent", FakeLeadAgent):
                project = web_app.update_project_setup(
                    output_root,
                    "demo",
                    {
                        "project_name": "Renamed Review",
                        "description": "New question",
                        "primary_topic": "AI",
                        "domain": "medicine",
                        "search_terms": "AI AND medicine",
                        "platforms": "pubmed, arxiv",
                        "max_results": "20",
                    },
                )

            config = json.loads((project_dir / "search_conditions.json").read_text(encoding="utf-8"))

        self.assertEqual(project["id"], "demo")
        self.assertEqual(calls[0][0], output_root)
        self.assertEqual(calls[0][1], "demo")
        self.assertFalse((output_root / "renamed-review").exists())
        self.assertTrue(config["agent_marker"])

    def test_project_chat_routes_message_through_lead_agent(self):
        old_output_root = web_app.OUTPUT_ROOT
        old_task_runner = web_app.task_runner
        release = Event()
        task_id = None
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "demo", "description": "Review LLM in medicine", "platforms": ["pubmed"]}),
                encoding="utf-8",
            )
            calls = []

            class FakeLeadAgent:
                def __init__(self, output_root):
                    self.output_root = output_root

                def handle_message(self, project_id, message=None, action=None, context_step=None):
                    calls.append((self.output_root, project_id, message, context_step))
                    chat_dir = Path(self.output_root) / project_id / "chat"
                    chat_dir.mkdir(parents=True, exist_ok=True)
                    with (chat_dir / "messages.jsonl").open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps({"step": 1, "role": "u", "text": message}) + "\n")
                        handle.write(json.dumps({"step": 1, "role": "a", "text": "LLM project reply."}) + "\n")

                    class Result:
                        reply = "LLM project reply."

                        def to_dict(self):
                            return {"stage": "search_conditions", "status": "completed", "reply": self.reply}

                    return Result()

            web_app.OUTPUT_ROOT = output_root
            web_app.task_runner = TaskRunner(max_workers=1)
            task_id = web_app.task_runner.submit("demo", "collect", release.wait)
            try:
                client = TestClient(web_app.create_app())
                with patch.object(web_app, "LeadAgent", FakeLeadAgent):
                    response = client.post("/projects/demo/chat", json={"message": "What next?", "step": "extraction"})
            finally:
                release.set()
                web_app.task_runner.wait(task_id, timeout=2)
                web_app.task_runner.shutdown()
                web_app.OUTPUT_ROOT = old_output_root
                web_app.task_runner = old_task_runner

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reply"], "LLM project reply.")
        self.assertEqual(calls, [(output_root, "demo", "What next?", "extraction")])
        self.assertEqual(response.json()["lead_agent"]["stage"], "search_conditions")
        self.assertEqual(response.json()["state"]["activeTask"]["task_id"], task_id)
        self.assertEqual(response.json()["state"]["messages"][-1]["text"], "LLM project reply.")

    def test_run_action_rejects_unknown_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            project_dir.mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "demo"}),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                web_app.submit_project_action(output_root, "demo", "unknown-action")

    def test_project_action_rejects_concurrent_action_for_same_project(self):
        old_output_root = web_app.OUTPUT_ROOT
        old_task_runner = web_app.task_runner
        release = Event()
        started = Event()
        calls = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output_root = Path(tmp)
                project_dir = output_root / "demo"
                project_dir.mkdir(parents=True)
                (project_dir / "search_conditions.json").write_text(json.dumps({"project_name": "demo"}), encoding="utf-8")
                web_app.OUTPUT_ROOT = output_root
                web_app.task_runner = TaskRunner(max_workers=2)

                def blocking_collect():
                    started.set()
                    release.wait()

                first_id = web_app.task_runner.submit("demo", "collect", blocking_collect)
                self.assertTrue(started.wait(timeout=1))

                class FakeLeadAgent:
                    def __init__(self, output_root, llm_query=None):
                        calls.append("constructed")

                with patch.object(web_app, "LeadAgent", FakeLeadAgent):
                    response = TestClient(web_app.create_app()).post("/projects/demo/actions/screen")

                self.assertEqual(response.status_code, 409)
                self.assertIn("already has running action", response.json()["detail"])
                self.assertEqual(response.json()["active_task"]["action"], "collect")
                self.assertEqual(response.json()["active_task"]["task_id"], first_id)
                self.assertEqual(calls, [])
        finally:
            release.set()
            if 'first_id' in locals():
                web_app.task_runner.wait(first_id, timeout=2)
            web_app.OUTPUT_ROOT = old_output_root
            web_app.task_runner = old_task_runner

    def test_running_task_is_exposed_in_project_state_and_workspace_html(self):
        old_output_root = web_app.OUTPUT_ROOT
        old_task_runner = web_app.task_runner
        release = Event()
        started = Event()
        task_id = None
        temporary_task_runner = None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output_root = Path(tmp)
                project_dir = output_root / "demo"
                project_dir.mkdir(parents=True)
                (project_dir / "search_conditions.json").write_text(
                    json.dumps({"project_name": "demo"}), encoding="utf-8"
                )
                web_app.OUTPUT_ROOT = output_root
                temporary_task_runner = TaskRunner(max_workers=1)
                web_app.task_runner = temporary_task_runner

                def blocking_collect():
                    started.set()
                    release.wait()

                task_id = web_app.task_runner.submit("demo", "collect", blocking_collect)
                self.assertTrue(started.wait(timeout=1))

                response = TestClient(web_app.create_app()).get("/projects/demo/state")
                embedded = re.search(r"window\.RP_DATA = (.*?); window\.RP_NEW_PROJECT_DATA", render_workspace_html(output_root, "demo"))

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    {key: response.json()["activeTask"][key] for key in ("task_id", "action", "status")},
                    {"task_id": task_id, "action": "collect", "status": "running"},
                )
                self.assertIsNotNone(embedded)
                self.assertEqual(json.loads(embedded.group(1))["activeTask"], response.json()["activeTask"])

                release.set()
                web_app.task_runner.wait(task_id, timeout=2)
                self.assertIsNone(TestClient(web_app.create_app()).get("/projects/demo/state").json()["activeTask"])
        finally:
            release.set()
            if task_id and web_app.task_runner.get(task_id) and web_app.task_runner.get(task_id)["status"] == "running":
                web_app.task_runner.wait(task_id, timeout=2)
            if temporary_task_runner is not None:
                temporary_task_runner.shutdown()
            web_app.OUTPUT_ROOT = old_output_root
            web_app.task_runner = old_task_runner

    def test_run_action_routes_canvas_intent_through_lead_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            (project_dir / "filtered").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps(
                    {
                        "project_name": "demo",
                        "description": "Review robotics",
                        "primary_topic": "robotics",
                        "domain": "surgery",
                        "search_terms": "robotics AND surgery",
                        "platforms": ["openalex"],
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "filtered" / "included_papers.jsonl").write_text("", encoding="utf-8")
            (project_dir / "filtered" / "screening_stats.json").write_text(
                json.dumps({"included_count": 0, "excluded_count": 0}),
                encoding="utf-8",
            )
            calls = []

            class FakeLeadAgentResult:
                stage = "collection"
                status = "completed"
                reply = "Collection completed through Lead Agent."
                next_actions = ["run_screening"]
                artifacts = [str(project_dir / "collected" / "summary.json")]

                def to_dict(self):
                    return {
                        "stage": self.stage,
                        "status": self.status,
                        "reply": self.reply,
                        "next_actions": self.next_actions,
                        "artifacts": self.artifacts,
                        "data": {"total": 0, "platform_stats": {"openalex": 0}, "platform_errors": {}},
                    }

            class FakeLeadAgent:
                def __init__(self, output_root, llm_query=None):
                    self.output_root = output_root
                    self.llm_query = llm_query

                def handle_message(self, project_id, message=None, action=None):
                    calls.append((self.output_root, project_id, message, action))
                    return FakeLeadAgentResult()

            with patch.object(web_app, "LeadAgent", FakeLeadAgent):
                task_id = web_app.submit_project_action(output_root, "demo", "collect")
                task = web_app.task_runner.wait(task_id, timeout=2)

        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["result"]["stage"], "collection")
        self.assertEqual(calls, [(output_root, "demo", None, "collect")])

    def test_project_action_accepts_categorization_payload_for_canvas_flow(self):
        old_output_root = web_app.OUTPUT_ROOT
        old_task_runner = web_app.task_runner
        try:
            web_app.task_runner = web_app.TaskRunner()
            with tempfile.TemporaryDirectory() as tmp:
                output_root = Path(tmp)
                project_dir = output_root / "demo"
                project_dir.mkdir(parents=True)
                (project_dir / "search_conditions.json").write_text(json.dumps({"project_name": "demo"}), encoding="utf-8")
                from reviewpilot_core.workflow_state import complete_action, initialize_workflow_state, start_action
                initialize_workflow_state(project_dir)
                for completed_action in ("collect", "screen", "download-pdfs", "run-extraction"):
                    start_action(project_dir, completed_action)
                    complete_action(project_dir, completed_action, terminal_result(completed_action))
                calls = []

                class FakeLeadAgentResult:
                    stage = "categorization"
                    status = "completed"
                    reply = "Categories suggested."

                    def to_dict(self):
                        return {"stage": self.stage, "status": self.status, "reply": self.reply}

                class FakeLeadAgent:
                    def __init__(self, output_root, llm_query=None):
                        self.output_root = output_root

                    def handle_message(self, project_id, message=None, action=None, input_data=None):
                        calls.append((project_id, action, input_data))
                        return FakeLeadAgentResult()

                web_app.OUTPUT_ROOT = output_root
                client = TestClient(web_app.create_app())
                with patch.object(web_app, "LeadAgent", FakeLeadAgent):
                    response = client.post(
                        "/projects/demo/actions/suggest-categories",
                        json={"field": "methods", "mode": "multiple"},
                    )
                    task = web_app.task_runner.wait(response.json()["task_id"], timeout=2)
        finally:
            web_app.OUTPUT_ROOT = old_output_root
            web_app.task_runner = old_task_runner

        self.assertEqual(response.status_code, 200)
        self.assertEqual(task["status"], "completed")
        self.assertEqual(calls, [("demo", "suggest-categories", {"field": "methods", "mode": "multiple"})])

    def test_favicon_route_prevents_browser_console_404_noise(self):
        client = TestClient(web_app.create_app())

        response = client.get("/favicon.ico")

        self.assertEqual(response.status_code, 204)

    def test_run_action_submits_generate_schema_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo"
            (project_dir / "filtered").mkdir(parents=True)
            (project_dir / "search_conditions.json").write_text(
                json.dumps(
                    {
                        "project_name": "demo",
                        "description": "Review robotics",
                        "primary_topic": "robotics",
                        "domain": "surgery",
                        "search_terms": "robotics AND surgery",
                        "platforms": ["openalex"],
                    }
                ),
                encoding="utf-8",
            )
            (project_dir / "filtered" / "included_papers.jsonl").write_text("", encoding="utf-8")
            (project_dir / "filtered" / "screening_stats.json").write_text(
                json.dumps({"included_count": 0, "excluded_count": 0}),
                encoding="utf-8",
            )
            from reviewpilot_core.workflow_state import complete_action, initialize_workflow_state, start_action
            initialize_workflow_state(project_dir)
            for completed_action in ("collect", "screen"):
                start_action(project_dir, completed_action)
                complete_action(project_dir, completed_action, terminal_result(completed_action))

            def fake_llm(*args, **kwargs):
                if "Design an extraction schema" in kwargs.get("text_prompt", ""):
                    return (
                        json.dumps(
                            {
                                "fields": [
                                    {"name": "tool_type", "type": "Text", "description": "Tool type", "required": False, "example": "model"},
                                    {"name": "key_findings", "type": "Long text", "description": "Findings", "required": False, "example": "finding"},
                                    {"name": "limitations", "type": "Text", "description": "Limitations", "required": False, "example": "limitation"},
                                ]
                            }
                        ),
                        {},
                    )
                return (json.dumps({"reply": "LLM schema action reply."}), {})

            task_id = web_app.submit_project_action(output_root, "demo", "generate-schema", llm_query=fake_llm)
            task = web_app.task_runner.wait(task_id, timeout=2)

        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["result"]["stage"], "prompt_extraction")
        self.assertEqual(task["result"]["status"], "completed")
        self.assertEqual(task["result"]["data"]["status"], "schema_generated")
        self.assertEqual(
            task["result"]["reply"],
            "Draft extraction schema generated with 3 fields. Review it, then select Finalize Schema before running Information Extraction.",
        )
        self.assertEqual(task["result"]["next_actions"], ["finalize_schema"])

    def test_run_action_supports_remaining_contract_actions_offline(self):
        old_offline = os.environ.get("REVIEWPILOT_OFFLINE_ACTIONS")
        os.environ["REVIEWPILOT_OFFLINE_ACTIONS"] = "1"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output_root = Path(tmp)
                project_dir = output_root / "demo"
                project_dir.mkdir(parents=True)
                (project_dir / "search_conditions.json").write_text(
                    json.dumps(
                        {
                            "project_name": "demo",
                            "description": "Review robotics",
                            "primary_topic": "robotics",
                            "domain": "surgery",
                            "search_terms": "robotics AND surgery",
                            "platforms": ["openalex"],
                        }
                    ),
                    encoding="utf-8",
                )
                actions = ["collect", "screen", "generate-schema", "finalize-schema", "download-pdfs", "run-extraction", "categorize"]
                stages = []
                statuses = []

                def fake_llm(*args, **kwargs):
                    if "Design an extraction schema" in kwargs.get("text_prompt", ""):
                        return (
                            json.dumps(
                                {
                                    "fields": [
                                        {"name": "tool_type", "type": "Text", "description": "Tool type", "required": False, "example": "model"},
                                        {"name": "key_findings", "type": "Long text", "description": "Findings", "required": False, "example": "finding"},
                                        {"name": "limitations", "type": "Text", "description": "Limitations", "required": False, "example": "limitation"},
                                    ]
                                }
                            ),
                            {},
                        )
                    if "Create a semantic category plan" in kwargs.get("text_prompt", ""):
                        return (
                            json.dumps(
                                {
                                    "categories": ["AI Tools"],
                                    "category_descriptions": {"AI Tools": "AI tool studies"},
                                }
                            ),
                            {},
                        )
                    if "Assign the supplied paper evidence" in kwargs.get("text_prompt", ""):
                        return (json.dumps({"category": "AI Tools"}), {})
                    return (json.dumps({"reply": "LLM action reply."}), {})

                for action in actions:
                    if action == "screen":
                        save_criteria(project_dir, criteria_state(project_dir), finalized=True)
                    task_id = web_app.submit_project_action(output_root, "demo", action, llm_query=fake_llm)
                    task = web_app.task_runner.wait(task_id, timeout=2)
                    stages.append(task["result"]["stage"])
                    statuses.append(task["result"]["status"])
        finally:
            if old_offline is None:
                os.environ.pop("REVIEWPILOT_OFFLINE_ACTIONS", None)
            else:
                os.environ["REVIEWPILOT_OFFLINE_ACTIONS"] = old_offline

        self.assertEqual(
            stages,
            [
                "collection",
                "filtering",
                "prompt_extraction",
                "prompt_extraction",
                "download",
                "extraction",
                "categorization",
            ],
        )
        self.assertEqual(statuses, ["completed"] * len(actions))

    def test_chat_created_project_runs_contract_smoke_to_final_result_workspace(self):
        old_output_root = web_app.OUTPUT_ROOT
        old_task_runner = web_app.task_runner
        previous_main = __import__("sys").modules.get("main")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output_root = Path(tmp)
                web_app.OUTPUT_ROOT = output_root
                web_app.task_runner = TaskRunner()

                class FakeSearcher:
                    def search(self, **kwargs):
                        return {
                            "openalex": [
                                {
                                    "id": "open",
                                    "title": "Open LLM Paper",
                                    "source": "openalex",
                                    "year": 2025,
                                    "abstract": "LLM clinical triage support.",
                                    "url": "https://example.test/open.pdf",
                                },
                                {
                                    "id": "closed",
                                    "title": "Paywalled LLM Paper",
                                    "source": "openalex",
                                    "year": 2024,
                                    "abstract": "Deployment barriers for biomedical LLMs.",
                                    "doi": "10.1000/closed",
                                },
                            ]
                        }

                class FakeFastDownloader:
                    def __init__(self, *, email, output_dir, enable_browser_fallback):
                        self.output_dir = Path(output_dir)

                    def download_batch(self, papers, progress_callback=None, progress_file=None):
                        self.output_dir.mkdir(parents=True, exist_ok=True)
                        pdf_path = self.output_dir / "row1_openalex_2025_Open_LLM_Paper_open.pdf"
                        pdf_path.write_bytes(b"%PDF-1.4\n")
                        papers[0]["pdf_downloaded"] = True
                        papers[0]["pdf_path"] = str(pdf_path)
                        return {
                            "total": 2,
                            "success": 1,
                            "failed": 1,
                            "failed_papers": [
                                {"id": "closed", "title": "Paywalled LLM Paper", "doi": "10.1000/closed", "failure_class": "subscription_required"}
                            ],
                        }

                def fake_llm(*args, **kwargs):
                    prompt = kwargs.get("text_prompt", "")
                    if "Generate Search Setup" in prompt:
                        return (
                            json.dumps(
                                {
                                    "reply": "Search setup ready.",
                                    "research_description": "Survey LLMs in biomedicine",
                                    "concept_blocks": [
                                        {"label": "Large language models (LLMs)", "role": "phenomenon", "eligibility_group": "technology", "required_for_eligibility": True, "query_terms": ["large language model", "LLM"]},
                                        {"label": "Biomedicine", "role": "context", "eligibility_group": "context", "required_for_eligibility": True, "query_terms": ["biomedicine", "biomedical"]},
                                    ],
                                }
                            ),
                            {"total_tokens": 100},
                        )
                    if "Design a extraction schema" in prompt or "Design an extraction schema" in prompt:
                        return (
                            json.dumps(
                                {
                                    "fields": [
                                        {"name": "key_findings", "type": "Long text", "description": "Main findings", "required": False, "example": "finding"},
                                        {"name": "limitations", "type": "Long text", "description": "Limitations", "required": False, "example": "limitation"},
                                        {"name": "application_area", "type": "Text", "description": "Application area", "required": False, "example": "clinical care"},
                                    ]
                                }
                            ),
                            {"total_tokens": 50},
                        )
                    if "Create a semantic category plan" in prompt:
                        return (
                            json.dumps(
                                {
                                    "categories": ["Clinical Support", "Implementation"],
                                    "category_descriptions": {
                                        "Clinical Support": "Clinical use cases",
                                        "Implementation": "Deployment and adoption issues",
                                    },
                                }
                            ),
                            {"total_tokens": 30},
                        )
                    if "Assign the supplied paper evidence" in prompt:
                        category = "Implementation" if "barriers" in prompt else "Clinical Support"
                        return (json.dumps({"category": category}), {"total_tokens": 10})
                    if "PAPER EVIDENCE DATA" in prompt:
                        return (
                            json.dumps(
                                {
                                    "key_findings": "clinical triage support",
                                    "limitations": "single source smoke fixture",
                                    "application_area": "clinical triage",
                                }
                            ),
                            {"total_tokens": 20},
                        )
                    if "ReviewPilot canvas action completed" in prompt:
                        return (json.dumps({"reply": "Action completed."}), {"total_tokens": 8})
                    return ("true", {"total_tokens": 5})

                def fake_web_search(self, paper, extraction_prompt):
                    return (
                        json.dumps(
                            {
                                "key_findings": "deployment barriers for biomedical LLMs",
                                "limitations": "publisher page only",
                                "application_area": "implementation",
                                "source_urls": ["https://pubmed.ncbi.nlm.nih.gov/123"],
                                "confidence": "medium",
                            }
                        ),
                        {"total_tokens": 25},
                    )

                import sys

                sys.modules["main"] = __import__("types").SimpleNamespace(AcademicSearcher=lambda: FakeSearcher())
                client = TestClient(web_app.create_app())
                with patch("agents.search_condition_agent.query_llm", side_effect=fake_llm), patch(
                    "utils.llm.query_llm", side_effect=fake_llm
                ), patch("utils.fast_pdf_downloader.FastCascadePDFDownloader", FakeFastDownloader), patch.object(
                    ExtractionAgent, "_read_pdf", lambda self, path: "This paper reports clinical triage support."
                ), patch.object(ExtractionAgent, "_query_web_search_extraction", fake_web_search):
                    response = client.post(
                        "/projects",
                        json={
                            "project_name": "LLM Biomedicine Smoke",
                            "description": "Survey LLMs in biomedicine",
                            "platforms": "openalex",
                            "max_results": 2,
                            "derive_search_terms": True,
                        },
                    )
                    project_id = response.json()["id"]
                    stages = []
                    for action in ["collect", "screen", "generate-schema", "finalize-schema", "download-pdfs", "run-extraction", "categorize"]:
                        if action == "screen":
                            criteria_task = web_app.submit_project_action(output_root, project_id, "finalize-criteria", input_data=criteria_state(output_root / project_id))
                            self.assertEqual(web_app.task_runner.wait(criteria_task, timeout=5)["status"], "completed")
                        task_id = web_app.submit_project_action(output_root, project_id, action, llm_query=fake_llm)
                        task = web_app.task_runner.wait(task_id, timeout=5)
                        self.assertEqual(task["status"], "partial" if action == "download-pdfs" else "completed", task.get("error"))
                        stages.append(task["result"]["stage"])

                state = build_rp_data(output_root, project_id)

        finally:
            web_app.OUTPUT_ROOT = old_output_root
            web_app.task_runner = old_task_runner
            if previous_main is None:
                __import__("sys").modules.pop("main", None)
            else:
                __import__("sys").modules["main"] = previous_main

        self.assertEqual(stages, ["collection", "filtering", "prompt_extraction", "prompt_extraction", "download", "extraction", "categorization"])
        self.assertEqual(state["steps"][-1]["status"], "done")
        self.assertEqual(state["categorizationSummary"]["groups"], 2)
        self.assertEqual(state["retrievalSummary"]["unavailable"], 1)
        self.assertEqual(state["evidenceMatrix"][1]["extractionSource"], "web_search_fallback")
        self.assertEqual(state["evidenceMatrix"][1]["sourceUrls"], ["https://pubmed.ncbi.nlm.nih.gov/123"])
        self.assertTrue(all(item["exists"] for item in state["exportPackage"]))


if __name__ == "__main__":
    unittest.main()
