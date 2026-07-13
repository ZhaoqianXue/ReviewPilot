import json
import tempfile
import unittest
from pathlib import Path

from reviewpilot_core.state_projection import build_new_project_data, build_rp_data, export_artifact_path, list_projects
from reviewpilot_core.workflow_state import STAGE_NAMES, complete_action, initialize_workflow_state, start_action


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_legacy_stage_chain(project: Path, through: str) -> None:
    order = ["collection", "screening", "retrieval", "extraction", "categorization"]
    if order.index(through) >= 0:
        write_json(project / "collected" / "summary.json", {"total_papers": 1, "platform_stats": {"pubmed": 1}})
    if order.index(through) >= 1:
        write_jsonl(project / "filtered" / "included_papers.jsonl", [{"title": "Legacy paper"}])
    if order.index(through) >= 2:
        write_json(project / "pdfs" / "download_report.json", {"success": 1, "failed": 0})
    if order.index(through) >= 3:
        write_jsonl(project / "extraction" / "extraction_results.jsonl", [{"title": "Legacy paper"}])
    if order.index(through) >= 4:
        write_json(project / "categorization" / "categorization_mapping.json", {"mapping": {}})


class StateProjectionTests(unittest.TestCase):
    def test_partial_retrieval_projection_agrees_across_canvas_activity_and_recovery_notice_after_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / "partial"; project.mkdir()
            write_json(project / "search_conditions.json", {"project_name": "partial", "platforms": ["pubmed"]})
            write_jsonl(project / "filtered" / "included_papers.jsonl", [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}, {"id": "c", "title": "C"}])
            write_json(project / "pdfs" / "download_report.json", {"success": 2, "failed": 1, "failed_papers": [{"id": "c", "title": "C", "failure_class": "paywall"}]})
            initialize_workflow_state(project)
            for action in ("collect", "screen"):
                start_action(project, action); complete_action(project, action, {})
            start_action(project, "download-pdfs"); complete_action(project, "download-pdfs", {"success": 2, "failed": 1})
            data = build_rp_data(root, "partial")
            refreshed = build_rp_data(root, "partial")
        self.assertEqual(data["stageState"]["retrieval"]["status"], "partial")
        self.assertEqual(data["steps"][2]["status"], "partial")
        self.assertEqual(data["steps"][3]["status"], "active")
        self.assertEqual(data["workflowNotices"]["retrieval"]["failedItems"], ["C"])
        self.assertEqual(data["workflowNotices"]["retrieval"]["nextAction"], "Information Extraction")
        self.assertTrue(data["workflowNotices"]["retrieval"]["retryable"])
        self.assertIn("2 completed", " ".join(line["msg"] for line in data["activityByStep"]["retrieval"]))
        self.assertEqual(refreshed["workflowNotices"], data["workflowNotices"])

    def test_failed_items_come_from_structured_artifacts_and_redact_local_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = {
                "collection": ({"total_papers": 1, "platform_stats": {"pubmed": 1}, "platform_errors": {"arxiv": "/Users/alice/token timed out"}}, "collect", "arXiv"),
                "retrieval": ({"success": 1, "failed": 1, "failed_papers": [{"title": "Paper B", "failure_detail": "/Users/alice/private.pdf"}]}, "download-pdfs", "Paper B"),
                "extraction": ({"processed": 1, "errors": 1}, "run-extraction", "Paper C"),
            }
            for name, (artifact, action, expected_item) in cases.items():
                project = root / name; project.mkdir(); write_json(project / "search_conditions.json", {"project_name": name, "platforms": ["pubmed"]})
                initialize_workflow_state(project)
                prerequisites = {"collect": (), "download-pdfs": ("collect", "screen"), "run-extraction": ("collect", "screen", "download-pdfs")}[action]
                for prerequisite in prerequisites:
                    start_action(project, prerequisite); complete_action(project, prerequisite, {})
                if name == "collection": write_json(project / "collected" / "summary.json", artifact)
                elif name == "retrieval": write_json(project / "pdfs" / "download_report.json", artifact)
                else:
                    write_json(project / "extraction" / "extraction_stats.json", artifact)
                    write_jsonl(project / "extraction" / "extraction_results.jsonl", [{"title": "Paper A", "extraction_status": "success"}, {"title": "Paper C", "extraction_status": "error", "error_message": "/Users/alice/private.pdf"}])
                start_action(project, action); complete_action(project, action, artifact)
                projected = build_rp_data(root, name)
                notice = projected["workflowNotices"][name]
                self.assertIn(expected_item, notice["failedItems"])
                self.assertNotIn("/Users", json.dumps(notice))
                if name == "extraction":
                    self.assertNotIn("Extraction is complete", json.dumps(projected["messages"]))

    def test_failed_item_labels_reject_every_supported_absolute_path_form_but_keep_doi_and_title(self):
        from reviewpilot_core.state_projection import _safe_failed_item
        unsafe = ["/home/alice/private.pdf", "C:\\secret\\paper.pdf", "\\\\server\\share\\paper.pdf", "file:///tmp/paper.pdf", "//server/share/paper.pdf", "///tmp/paper.pdf"]
        for value in unsafe:
            with self.subTest(value=value):
                self.assertEqual(_safe_failed_item({"title": value}), "Unidentified item")
        self.assertEqual(_safe_failed_item({"title": "A valid paper title"}), "A valid paper title")
        self.assertEqual(_safe_failed_item({"doi": "10.1000/review.42"}), "10.1000/review.42")

    def test_outcome_assistant_messages_are_reconstructed_from_ledger_and_artifacts_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = [
                ("collection", "collect", {"total":1,"platform_stats":{"pubmed":1},"platform_errors":{"arxiv":"503"}}, lambda p: write_json(p/"collected"/"summary.json", {"total_papers":1,"platform_stats":{"pubmed":1},"platform_errors":{"arxiv":"503"}}), "partially completed"),
                ("retrieval", "download-pdfs", {"success":1,"failed":1}, lambda p: write_json(p/"pdfs"/"download_report.json", {"success":1,"failed":1,"failed_papers":[{"title":"B"}]}), "partially completed"),
                ("extraction", "run-extraction", {"processed":0,"errors":1}, lambda p: write_jsonl(p/"extraction"/"extraction_results.jsonl", [{"title":"C","extraction_status":"error"}]), "failed"),
            ]
            for name, action, result, artifact_writer, expected in cases:
                project=root/name; project.mkdir(); write_json(project/"search_conditions.json", {"project_name":name,"platforms":["pubmed"]}); initialize_workflow_state(project)
                for prerequisite in {"collect":(),"download-pdfs":("collect","screen"),"run-extraction":("collect","screen","download-pdfs")}[action]: start_action(project, prerequisite); complete_action(project, prerequisite, {})
                artifact_writer(project); start_action(project, action); complete_action(project, action, result)
                write_jsonl(project/"chat"/"messages.jsonl", [{"step":1,"role":"a","source":"canvas_action","stage":name,"text":"LLM says completed /Users/alice/private.txt"}])
                payload = json.dumps(build_rp_data(root, name))
                self.assertIn(expected, payload); self.assertNotIn("LLM says completed", payload); self.assertNotIn("/Users/alice", payload)

    def test_platform_error_paths_are_genericized_across_entire_projected_json(self):
        unsafe = ["/home/a/key", "C:\\secret\\key", "\\\\server\\share\\key", "file:///tmp/key", "///tmp/key"]
        for value in unsafe:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); project=root/"p"; project.mkdir(); write_json(project/"search_conditions.json", {"project_name":"p","platforms":["pubmed"]})
                write_json(project/"collected"/"summary.json", {"total_papers":0,"platform_stats":{"pubmed":0},"platform_errors":{"pubmed":f"failed reading {value}"}})
                initialize_workflow_state(project); start_action(project,"collect"); complete_action(project,"collect", {"total":0,"platform_stats":{"pubmed":0},"platform_errors":{"pubmed":f"failed reading {value}"}})
                payload=json.dumps(build_rp_data(root,"p")); self.assertNotIn(value, payload); self.assertIn("details hidden", payload)

    def test_extraction_failed_items_scan_past_two_hundred_success_rows_using_one_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); project=root/"p"; project.mkdir(); write_json(project/"search_conditions.json", {"project_name":"p","platforms":["pubmed"]}); initialize_workflow_state(project)
            for action in ("collect","screen","download-pdfs"): start_action(project,action); complete_action(project,action,{})
            rows=[{"title":f"S{i}","extraction_status":"success"} for i in range(200)] + [{"title":"Late failure","extraction_status":"error"}]
            write_jsonl(project/"extraction"/"extraction_results.jsonl", rows); start_action(project,"run-extraction"); complete_action(project,"run-extraction", {"processed":200,"errors":1})
            data=build_rp_data(root,"p")
        self.assertEqual(data["workflowNotices"]["extraction"]["failedItems"], ["Late failure"])

    def test_terminal_rerun_stale_policy_blocks_mismatched_current_and_downstream_exports(self):
        cases = [
            ("collect", "collection", "relevance-prompt", "included-papers", {"total":1,"platform_stats":{"pubmed":1},"platform_errors":{"arxiv":"503"}}),
            ("download-pdfs", "retrieval", "download-report", "extraction-results", {"success":1,"failed":1}),
            ("run-extraction", "extraction", "extraction-results", "categorization-mapping", {"processed":1,"errors":1}),
        ]
        for action, stage_name, current_export, downstream_export, partial_result in cases:
            for result, current_should_export in ((partial_result, True), ({**partial_result, **({"total":0,"platform_stats":{},"platform_errors":{"pubmed":"503"}} if action=="collect" else ({"success":0,"failed":1} if action=="download-pdfs" else {"processed":0,"errors":1}))}, True), ({**partial_result, **({"platform_errors":{}} if action=="collect" else ({"failed":0} if action=="download-pdfs" else {"errors":0}))}, True)):
                with self.subTest(action=action, result=result), tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp); project=root/"p"; project.mkdir(); write_json(project/"search_conditions.json", {"project_name":"p","platforms":["pubmed"]}); initialize_workflow_state(project)
                    write_json(project/"prompts"/"relevance_prompt.json", {"task":"x"}); write_jsonl(project/"filtered"/"included_papers.jsonl", [{"title":"A"}]); write_json(project/"pdfs"/"download_report.json", {"success":1,"failed":0}); write_jsonl(project/"extraction"/"extraction_results.jsonl", [{"title":"A","extraction_status":"success"}]); write_json(project/"categorization"/"categorization_mapping.json", {"categories":["A"],"mapping":{"A":"A"}})
                    for first in ("collect","screen","download-pdfs","run-extraction","categorize"): start_action(project,first); complete_action(project,first,{})
                    start_action(project,action); complete_action(project,action,result); data=build_rp_data(root,"p")
                    self.assertEqual(export_artifact_path(project,current_export) is not None, current_should_export)
                    self.assertIsNone(export_artifact_path(project,downstream_export))
                    self.assertTrue(any(stage["stale"] for name, stage in data["stageState"].items() if STAGE_NAMES.index(name)>STAGE_NAMES.index(stage_name)))

    def test_exception_rerun_does_not_reconstruct_an_outcome_from_stale_artifacts(self):
        from reviewpilot_core.workflow_state import fail_action
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / "p"; project.mkdir()
            write_json(project / "search_conditions.json", {"project_name": "p", "platforms": ["pubmed"]})
            write_json(project / "pdfs" / "download_report.json", {"success": 1, "failed": 1, "failed_papers": [{"title": "Old failure"}]})
            initialize_workflow_state(project)
            for action in ("collect", "screen", "download-pdfs"):
                start_action(project, action); complete_action(project, action, {"success": 1, "failed": 1} if action == "download-pdfs" else {})
            start_action(project, "download-pdfs"); fail_action(project, "download-pdfs", RuntimeError("crashed"))
            data = build_rp_data(root, "p")

        self.assertNotIn("retrieval", data["workflowNotices"])
        self.assertNotIn("Old failure", json.dumps(data))
    def test_stale_activity_does_not_recount_pdfs_or_replay_old_canvas_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / "stale-activity"; project.mkdir()
            (project / "search_conditions.json").write_text(json.dumps({"project_name":"stale-activity","description":"Q","platforms":["pubmed"]}))
            initialize_workflow_state(project)
            for action in ("collect", "screen", "download-pdfs"):
                start_action(project, action); complete_action(project, action, {})
            (project / "pdfs").mkdir(); (project / "pdfs" / "old.pdf").write_bytes(b"old")
            (project / "chat").mkdir(); (project / "chat" / "messages.jsonl").write_text(json.dumps({"role":"a","source":"canvas_action","stage":"download","text":"Download completed: 9 PDFs"}) + "\n")
            from reviewpilot_core.workflow_state import mark_stages_stale
            mark_stages_stale(project, ["retrieval"])
            activity = build_rp_data(root, "stale-activity")["activityByStep"]["retrieval"]
        self.assertEqual(activity, [{"t": "--:--:--", "tag": "retrieval", "msg": "0 PDFs fetched"}])
    def test_existing_ledger_is_sole_stage_truth_despite_artifact_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "ledger-project"
            write_json(project_dir / "search_conditions.json", {"project_name": "ledger-project"})
            initialize_workflow_state(project_dir)
            write_json(project_dir / "categorization" / "categorization_mapping.json", {"mapping": {"A": "x"}})

            before = build_rp_data(output_root, "ledger-project")
            start_action(project_dir, "collect")
            complete_action(project_dir, "collect", {"total_papers": 3})
            (project_dir / "categorization" / "categorization_mapping.json").unlink()
            after = build_rp_data(output_root, "ledger-project")

        self.assertEqual(before["stageState"]["collection"]["status"], "ready")
        self.assertEqual(before["steps"][-1]["status"], "todo")
        self.assertEqual(after["stageState"]["collection"]["status"], "completed")
        self.assertEqual(after["steps"][0]["status"], "done")

    def test_new_project_state_exposes_five_user_workflow_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = build_new_project_data(Path(tmp))

        self.assertTrue(data["isNewProject"])
        self.assertEqual(data["project"]["title"], "Untitled review")
        self.assertEqual(data["project"]["model"], "gpt-5.4-mini")
        self.assertEqual(
            [(step["key"], step["label"], step["status"], step["sub"]) for step in data["steps"]],
            [
                ("search", "Search Setup", "active", "3 sources"),
                ("screening", "Paper Screening", "todo", "0 / 0"),
                ("retrieval", "Full-Text Retrieval", "todo", "0 / 0"),
                ("extraction", "Information Extraction", "todo", "0 fields"),
                ("categorize", "Categorization & Analysis", "todo", "0 groups"),
            ],
        )
        self.assertEqual(data["optionalCapabilities"], [])
        self.assertIn("Welcome to **ReviewPilot**!", data["messages"][0]["text"])
        self.assertIn("ReviewPilot helps you turn a research topic into a literature review:", data["messages"][0]["text"])
        self.assertIn("**5. Categorization & Analysis**", data["messages"][0]["text"])
        self.assertIn("final Categorization & Analysis report", data["messages"][0]["text"])
        self.assertIn("**Choose a starter topic on the canvas, or describe your own topic in the chat.**", data["messages"][0]["text"])
        self.assertNotIn("Example:", data["messages"][0]["text"])
        self.assertNotIn("ReviewPilot turns your topic into a five-step review", data["messages"][0]["text"])
        self.assertNotIn("Tell me the topic. I'll handle search setup", data["messages"][0]["text"])
        self.assertNotIn("lead-agent workspace", data["messages"][0]["text"])
        self.assertNotIn("lead agent", data["messages"][0]["text"].lower())
        self.assertNotIn("specialist agents", data["messages"][0]["text"])
        self.assertNotIn("I'll guide you through a systematic literature review", data["messages"][0]["text"])
        self.assertNotIn("Final output:", data["messages"][0]["text"])
        self.assertNotIn("What are you researching?", data["messages"][0]["text"])
        self.assertNotIn("Define the research question", data["messages"][0]["text"])
        self.assertEqual(data["setup"]["max_results"], 10)
        self.assertEqual(data["setup"]["platforms"], ["pubmed", "arxiv", "openalex"])
        self.assertEqual(list(data["setup"]["source_limits"]), ["pubmed", "arxiv", "openalex"])
        self.assertEqual(data["setup"]["source_limits"], {"pubmed": 10, "openalex": 10, "arxiv": 10})
        self.assertEqual(data["platforms"], [["PubMed", 0], ["arXiv", 0], ["Openalex", 0]])
        self.assertEqual(data["history"][0]["label"], "Historys")
        self.assertEqual(
            data["history"][0]["items"][0],
            {"id": "", "title": "Untitled review", "active": True, "isNewProject": True},
        )

    def test_demo_history_limits_sidebar_to_new_and_three_showcase_directions(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            write_json(output_root / "qa-live-llm-biomedical-20260701-181434" / "search_conditions.json", {"project_name": "LLM for Biomedical Systematic Review"})
            write_json(output_root / "qa-live-llm-hci-20260701-181434" / "search_conditions.json", {"project_name": "LLM for Human-Computer Interaction Review"})
            write_json(output_root / "qa-live-llm-urban-20260701-181434" / "search_conditions.json", {"project_name": "LLM for Urban Planning and Smart Cities"})
            write_json(output_root / "unrelated-newer-project" / "search_conditions.json", {"project_name": "Unrelated Project"})

            data = build_rp_data(output_root, "qa-live-llm-hci-20260701-181434")

        items = data["history"][0]["items"]
        self.assertEqual(
            [(item["id"], item["title"], item["active"]) for item in items],
            [
                ("", "Untitled review", False),
                ("qa-live-llm-biomedical-20260701-181434", "LLM for Biomedical", False),
                ("qa-live-llm-hci-20260701-181434", "LLM for HCI", True),
                ("qa-live-llm-urban-20260701-181434", "LLM for Urban", False),
            ],
        )
        self.assertTrue(items[0]["isNewProject"])

    def test_demo_history_prefers_live_showcase_runs_over_older_keyword_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            write_json(output_root / "how-llms-are-used-biomedical" / "search_conditions.json", {"project_name": "How LLMs Are Used in Biomedical Research and Clinical Care"})
            write_json(output_root / "llms-human-computer-interaction" / "search_conditions.json", {"project_name": "LLMs Human-Computer Interaction"})
            write_json(output_root / "how-llms-support-urban-planning" / "search_conditions.json", {"project_name": "How LLMs Support Urban Planning"})
            write_json(output_root / "qa-live-llm-biomedical-20260701-181434" / "search_conditions.json", {"project_name": "LLM for Biomedical Systematic Review"})
            write_json(output_root / "qa-live-llm-hci-20260701-181434" / "search_conditions.json", {"project_name": "LLM for Human-Computer Interaction Review"})
            write_json(output_root / "qa-live-llm-urban-20260701-181434" / "search_conditions.json", {"project_name": "LLM for Urban Planning and Smart Cities"})

            data = build_rp_data(output_root, "qa-live-llm-biomedical-20260701-181434")

        self.assertEqual(
            [item["id"] for item in data["history"][0]["items"]],
            [
                "",
                "qa-live-llm-biomedical-20260701-181434",
                "qa-live-llm-hci-20260701-181434",
                "qa-live-llm-urban-20260701-181434",
            ],
        )

    def test_demo_examples_start_with_real_new_review_conversation_shape(self):
        cases = [
            (
                "qa-live-llm-biomedical-20260701-181434",
                "LLM for Biomedical Systematic Review",
                "I want to review how LLMs are used in biomedical research and clinical care.",
            ),
            (
                "qa-live-llm-hci-20260701-181434",
                "LLM for Human-Computer Interaction Review",
                "I want to review how LLMs are changing human-computer interaction.",
            ),
            (
                "qa-live-llm-urban-20260701-181434",
                "LLM for Urban Planning and Smart Cities",
                "I want to review how LLMs support urban planning and smart cities.",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            for project_id, project_name, _topic in cases:
                write_json(
                    output_root / project_id / "search_conditions.json",
                    {
                        "project_name": project_name,
                        "description": f"{project_name}: systematic review setup.",
                        "lead_agent_reply": "Prepared a search setup for this review.",
                    },
                )

            projected = {project_id: build_rp_data(output_root, project_id) for project_id, _project_name, _topic in cases}

        for project_id, _project_name, topic in cases:
            messages = projected[project_id]["messages"]
            self.assertIn("Welcome to **ReviewPilot**!", messages[0]["text"])
            self.assertEqual(messages[1], {"step": 1, "role": "u", "text": topic})
            self.assertEqual(messages[2]["text"], "Prepared a search setup for this review.")
            self.assertNotEqual(messages[1]["text"], "LLM for Biomedical")
            self.assertNotEqual(messages[1]["text"], "LLM for HCI")
            self.assertNotEqual(messages[1]["text"], "LLM for Urban")

    def test_lists_projects_with_search_conditions_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            write_json(output_root / "valid" / "search_conditions.json", {"project_name": "valid"})
            (output_root / "scratch").mkdir()

            projects = list_projects(output_root)

        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["id"], "valid")
        self.assertEqual(projects[0]["title"], "valid")

    def test_project_projection_unescapes_legacy_html_entities_for_display(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "legacy-escaped"
            write_json(
                project_dir / "search_conditions.json",
                {
                    "project_name": "LLM &amp; Medicine",
                    "description": "Survey &quot;LLM&quot; in medicine",
                    "platforms": ["pubmed"],
                    "search_terms": "(&quot;large language model&quot; OR LLM) AND medicine",
                    "date_range": {"start": "2022-01-01", "end": ""},
                },
            )

            projects = list_projects(output_root)
            data = build_rp_data(output_root, "legacy-escaped")

        self.assertEqual(projects[0]["title"], "LLM & Medicine")
        self.assertEqual(data["project"]["title"], "LLM & Medicine")
        self.assertEqual(data["setup"]["search_terms"], '("large language model" OR LLM) AND medicine')
        overview = {item["label"]: item["value"] for item in data["resultOverview"]}
        self.assertEqual(overview["Search strategy"], '("large language model" OR LLM) AND medicine')
        self.assertNotIn("&quot;", data["messages"][0]["text"])

    def test_non_demo_projects_do_not_enter_demo_history_sidebar(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            write_json(output_root / "alpha" / "search_conditions.json", {"project_name": "Alpha"})
            write_json(output_root / "beta" / "search_conditions.json", {"project_name": "Beta"})

            data = build_rp_data(output_root, "alpha")

        history_items = data["history"][0]["items"]
        self.assertEqual(history_items, [{"id": "", "title": "Untitled review", "active": False, "isNewProject": True}])

    def test_canvas_action_chat_messages_render_as_activity_not_chat_bubbles(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "rerun-project"
            write_json(project_dir / "search_conditions.json", {"project_name": "rerun-project", "description": "Survey HCI"})
            write_jsonl(
                project_dir / "chat" / "messages.jsonl",
                [
                    {
                        "step": 4,
                        "role": "a",
                        "text": "Extraction completed: 19 of 20 PDFs processed, with 1 error.",
                        "source": "canvas_action",
                        "stage": "extraction",
                    },
                    {
                        "step": 4,
                        "role": "a",
                        "text": "Prompt extraction completed successfully: a 10-field schema was generated by the LLM.",
                        "source": "canvas_action",
                        "stage": "prompt_extraction",
                    },
                    {"step": 4, "role": "u", "text": "Please explain the extraction error."},
                    {
                        "step": 4,
                        "role": "a",
                        "text": "Extraction completed successfully: 20/20 PDFs processed with 0 errors.",
                        "source": "canvas_action",
                        "stage": "extraction",
                    },
                ],
            )

            data = build_rp_data(output_root, "rerun-project")

        texts = [message["text"] for message in data["messages"]]
        self.assertNotIn("Extraction completed: 19 of 20 PDFs processed, with 1 error.", texts)
        self.assertNotIn("Extraction completed successfully: 20/20 PDFs processed with 0 errors.", texts)
        self.assertNotIn("Prompt extraction completed successfully: a 10-field schema was generated by the LLM.", texts)
        self.assertIn("Please explain the extraction error.", texts)
        extraction_activity = [line["msg"] for line in data["activityByStep"]["extraction"]]
        self.assertNotIn("Extraction completed: 19 of 20 PDFs processed, with 1 error.", extraction_activity)
        self.assertIn("Extraction completed successfully: 20/20 PDFs processed with 0 errors.", extraction_activity)
        self.assertIn("Prompt extraction completed successfully: a 10-field schema was generated by the LLM.", extraction_activity)

    def test_builds_frontend_data_from_existing_project_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo-project"
            write_json(
                project_dir / "search_conditions.json",
                {
                    "project_name": "demo-project",
                    "description": "Survey retrieval augmented generation in medicine",
                    "primary_topic": "RAG",
                    "domain": "Medicine",
                    "platforms": ["pubmed", "openalex"],
                    "search_terms": "RAG AND medicine",
                    "search_queries": [{"name": "main", "query": "RAG AND medicine"}],
                    "lead_agent_reply": "LLM generated the Search Setup. Review the canvas before collection.",
                    "source_limits": {"pubmed": 10, "openalex": 25},
                    "date_range": {"start": "2021-01-01", "end": "2026-01-01"},
                },
            )
            write_json(
                project_dir / "collected" / "summary.json",
                {"total_papers": 12, "platform_stats": {"pubmed": 7, "openalex": 5}},
            )
            write_json(
                project_dir / "filtered" / "screening_stats.json",
                {"initial": 12, "after_dedup": 10, "total_screened": 10, "included_count": 4, "excluded_count": 6},
            )
            write_jsonl(
                project_dir / "filtered" / "included_papers.jsonl",
                [
                    {"title": "First paper", "source": "pubmed", "year": 2025},
                    {"title": "Second paper", "source": "openalex", "year": 2024},
                    {"title": "Third paper", "source": "pubmed", "year": 2023},
                    {"title": "Fourth paper", "source": "openalex", "year": 2022},
                ],
            )
            write_json(
                project_dir / "pdfs" / "download_report.json",
                {"success": 3, "failed": 1},
            )
            (project_dir / "pdfs" / "paper.pdf").write_text("%PDF", encoding="utf-8")

            data = build_rp_data(output_root, "demo-project")

        self.assertEqual(data["project"]["title"], "demo-project")
        self.assertEqual(data["researchQuestion"], "Survey retrieval augmented generation in medicine")
        self.assertEqual(data["project"]["status"], "Active · Step 4 of 5")
        self.assertEqual([step["status"] for step in data["steps"]], ["done", "done", "done", "active", "todo"])
        self.assertEqual(data["steps"][1]["sub"], "4 / 12")
        self.assertEqual(data["steps"][2]["sub"], "3 / 4")
        self.assertEqual(data["screeningMetrics"], {"identified": 12, "afterDedup": 10, "included": 4})
        self.assertEqual(data["retrievalSummary"], {"retrieved": 3, "total": 4, "openAccess": 3, "viaInstitution": 0, "unavailable": 1})
        self.assertEqual(data["setup"]["source_limits"], {"pubmed": 10, "openalex": 25})
        self.assertIn("Welcome to **ReviewPilot**!", data["messages"][0]["text"])
        self.assertEqual(data["messages"][1]["text"], "Survey retrieval augmented generation in medicine")
        self.assertEqual(data["messages"][2]["text"], "LLM generated the Search Setup. Review the canvas before collection.")
        self.assertNotIn("Lead Agent generated this Search Setup", data["messages"][1]["text"])
        self.assertFalse(any("3 PDFs retrieved" in message["text"] for message in data["messages"]))
        self.assertTrue(any("3 PDFs fetched" in line["msg"] for line in data["activityByStep"]["retrieval"]))
        self.assertEqual(data["platforms"], [["PubMed", 7], ["Openalex", 5]])
        self.assertEqual(data["retrieved"][0]["t"], "First paper")
        self.assertEqual(data["fields"], [])
        self.assertEqual(data["keywords"], ["RAG", "Medicine"])
        self.assertIn("Survey retrieval augmented generation", data["messages"][1]["text"])

    def test_collection_platform_errors_surface_in_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "demo-project"
            write_json(
                project_dir / "search_conditions.json",
                {
                    "project_name": "demo-project",
                    "description": "Survey LLMs",
                    "platforms": ["openalex"],
                    "search_terms": "LLM",
                },
            )
            write_json(
                project_dir / "collected" / "summary.json",
                {
                    "total_papers": 0,
                    "platform_stats": {"openalex": 0},
                    "platform_errors": {"openalex": "503 Search temporarily unavailable"},
                },
            )

            data = build_rp_data(output_root, "demo-project")

        self.assertIn(
            {"t": "--:--:--", "tag": "openalex", "msg": "503 Search temporarily unavailable"},
            data["activityByStep"]["search"],
        )
        self.assertEqual(
            data["platformIssues"],
            [
                {
                    "platform": "openalex",
                    "label": "Openalex",
                    "message": "503 Search temporarily unavailable",
                    "severity": "warning",
                }
            ],
        )

    def test_includes_schema_and_preview_when_extraction_outputs_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "extracted-project"
            write_json(project_dir / "search_conditions.json", {"project_name": "extracted-project"})
            write_legacy_stage_chain(project_dir, "extraction")
            write_json(
                project_dir / "extraction" / "extraction_schema.json",
                {
                    "fields": [
                        {"name": "sample_size", "type": "Number", "description": "Participants", "required": True},
                        {"name": "finding", "description": "Main finding"},
                    ]
                },
            )
            write_jsonl(
                project_dir / "extraction" / "extraction_results.jsonl",
                [{"title": "Extracted paper", "sample_size": 42, "finding": "Worked"}],
            )

            data = build_rp_data(output_root, "extracted-project")

        self.assertEqual([step["status"] for step in data["steps"]], ["done", "done", "done", "done", "active"])
        self.assertEqual(data["project"]["status"], "Active · Step 5 of 5")
        self.assertIn("Extraction is complete. Choose a field to categorize for final analysis.", [message["text"] for message in data["messages"]])
        self.assertEqual(data["fields"][0], ["sample_size", "Number", "Participants", True])
        self.assertEqual(data["fields"][1], ["finding", "Text", "Main finding", False])
        self.assertIn({"k": "sample_size", "v": "42"}, data["previewFields"])
        self.assertEqual(data["previewPaper"]["title"], "Extracted paper")
        self.assertEqual(data["previewPaper"]["ref"], "extraction_results.jsonl")

    def test_download_report_marks_retrieval_step_done_even_without_successful_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "download-attempted"
            write_json(project_dir / "search_conditions.json", {"project_name": "download-attempted"})
            write_legacy_stage_chain(project_dir, "retrieval")
            write_jsonl(project_dir / "filtered" / "included_papers.jsonl", [{"title": "Paper A"}])
            write_json(project_dir / "pdfs" / "download_report.json", {"success": 0, "failed": 1})

            data = build_rp_data(output_root, "download-attempted")

        self.assertEqual(data["steps"][2]["status"], "done")
        self.assertEqual(data["steps"][2]["sub"], "0 / 1")

    def test_categorization_groups_include_counts_from_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "categorized"
            write_json(project_dir / "search_conditions.json", {"project_name": "categorized"})
            write_legacy_stage_chain(project_dir, "categorization")
            write_json(
                project_dir / "categorization" / "categorization_mapping.json",
                {
                    "categories": ["Clinical", "NLP"],
                    "category_descriptions": {"Clinical": "Clinical tools", "NLP": "Language systems"},
                    "mapping": {"Paper A": "Clinical", "Paper B": "NLP", "Paper C": "NLP"},
                },
            )

            data = build_rp_data(output_root, "categorized")

        self.assertEqual(
            data["groups"],
            [
                {"name": "Clinical", "n": 1, "desc": "Clinical tools"},
                {"name": "NLP", "n": 2, "desc": "Language systems"},
            ],
        )
        self.assertEqual(data["categorizationSummary"], {"papers": 3, "groups": 2})
        self.assertEqual(data["steps"][-1]["key"], "categorize")
        self.assertEqual(data["steps"][-1]["status"], "done")
        self.assertEqual(data["optionalCapabilities"], [])

    def test_categorization_summary_counts_papers_not_comma_fragments(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "categorized-with-comma-labels"
            write_json(project_dir / "search_conditions.json", {"project_name": "categorized"})
            write_json(
                project_dir / "categorization" / "categorization_mapping.json",
                {
                    "categories": [
                        "Task, application, and biomedical domain",
                        "Limitations, risks, and implementation concerns",
                    ],
                    "mapping": {
                        "Paper A": "Task, application, and biomedical domain",
                        "Paper B": "Limitations, risks, and implementation concerns",
                    },
                },
            )

            data = build_rp_data(output_root, "categorized-with-comma-labels")

        self.assertEqual(
            data["groups"],
            [
                {"name": "Task, application, and biomedical domain", "n": 1, "desc": ""},
                {"name": "Limitations, risks, and implementation concerns", "n": 1, "desc": ""},
            ],
        )
        self.assertEqual(data["categorizationSummary"], {"papers": 2, "groups": 2})
        self.assertIn({"label": "Categorized", "value": "2"}, data["resultOverview"])

    def test_categorization_summary_uses_populated_groups_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "categorized-with-empty-group"
            write_json(project_dir / "search_conditions.json", {"project_name": "categorized"})
            write_json(
                project_dir / "categorization" / "categorization_mapping.json",
                {
                    "categories": ["Used group", "Empty group"],
                    "category_descriptions": {"Used group": "Has papers", "Empty group": "No assigned papers"},
                    "mapping": {"Paper A": "Used group", "Paper B": "Used group"},
                },
            )

            data = build_rp_data(output_root, "categorized-with-empty-group")

        self.assertEqual(data["groups"], [{"name": "Used group", "n": 2, "desc": "Has papers"}])
        self.assertEqual(data["categorizationSummary"], {"papers": 2, "groups": 1})
        self.assertEqual([category["name"] for category in data["categorizationAnalysis"]["categories"]], ["Used group"])

    def test_final_result_workspace_exposes_overview_matrix_analysis_and_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "final-report"
            write_json(
                project_dir / "search_conditions.json",
                {
                    "project_name": "final-report",
                    "description": "Survey LLMs in biomedicine",
                    "search_terms": "LLM AND biomedicine",
                    "platforms": ["pubmed", "openalex"],
                    "date_range": {"start": "2020-01-01", "end": ""},
                },
            )
            write_json(
                project_dir / "collected" / "summary.json",
                {"total_papers": 9, "platform_stats": {"pubmed": 5, "openalex": 4}},
            )
            write_json(
                project_dir / "filtered" / "screening_stats.json",
                {"total_screened": 7, "included_count": 2, "excluded_count": 5},
            )
            write_jsonl(
                project_dir / "filtered" / "included_papers.jsonl",
                [
                    {"id": "p1", "title": "Clinical LLM", "source": "pubmed", "year": 2025, "pdf_downloaded": True, "retrieval_status": "downloaded"},
                    {"id": "p2", "title": "Paywalled LLM", "source": "openalex", "year": 2024, "pdf_downloaded": False, "retrieval_status": "subscribed_unavailable"},
                ],
            )
            write_json(
                project_dir / "pdfs" / "download_report.json",
                {"success": 1, "failed": 1, "subscribed_papers": [{"title": "Paywalled LLM"}]},
            )
            write_json(
                project_dir / "prompts" / "relevance_prompt.json",
                {"task": "Include biomedical LLM papers."},
            )
            write_json(
                project_dir / "prompts" / "extraction_prompt.json",
                {"prompt_type": "extraction", "schema": {"fields": [{"name": "key_findings"}]}},
            )
            write_jsonl(
                project_dir / "extraction" / "extraction_results.jsonl",
                [
                    {"paper_id": "p1", "title": "Clinical LLM", "source": "pubmed", "year": 2025, "key_findings": "clinical triage support", "extraction_source": "pdf"},
                    {
                        "paper_id": "p2",
                        "title": "Paywalled LLM",
                        "source": "openalex",
                        "year": 2024,
                        "key_findings": "deployment barriers",
                        "extraction_source": "web_search_fallback",
                        "source_urls": ["https://pubmed.ncbi.nlm.nih.gov/123"],
                        "confidence": "medium",
                    },
                ],
            )
            write_json(
                project_dir / "categorization" / "categorization_mapping.json",
                {
                    "field": "key_findings",
                    "categories": ["Clinical Support", "Implementation"],
                    "category_descriptions": {"Clinical Support": "Clinical use cases", "Implementation": "Deployment issues"},
                    "mapping": {"Clinical LLM": "Clinical Support", "Paywalled LLM": "Implementation"},
                },
            )
            write_jsonl(
                project_dir / "categorization" / "categorized_results.jsonl",
                [
                    {"paper_id": "p1", "title": "Clinical LLM", "key_findings": "clinical triage support", "category": "Clinical Support"},
                    {"paper_id": "p2", "title": "Paywalled LLM", "key_findings": "deployment barriers", "category": "Implementation"},
                ],
            )

            data = build_rp_data(output_root, "final-report")

        self.assertEqual(data["resultOverview"][0], {"label": "Identified", "value": "9"})
        self.assertIn({"label": "Subscribed/unavailable", "value": "1"}, data["resultOverview"])
        self.assertEqual(data["evidenceMatrix"][0]["title"], "Clinical LLM")
        self.assertEqual(data["evidenceMatrix"][1]["extractionSource"], "web_search_fallback")
        self.assertEqual(data["evidenceMatrix"][1]["sourceUrls"], ["https://pubmed.ncbi.nlm.nih.gov/123"])
        self.assertEqual(data["categorizationAnalysis"]["field"], "key_findings")
        self.assertEqual(data["categorizationAnalysis"]["categories"][0]["name"], "Clinical Support")
        self.assertEqual(data["categorizationAnalysis"]["categories"][0]["papers"][0]["title"], "Clinical LLM")
        self.assertEqual(data["categorizationAnalysis"]["categories"][0]["papers"][0]["evidence"], "clinical triage support")
        self.assertEqual(
            [item["label"] for item in data["exportPackage"]],
            [
                "Search setup",
                "Relevance prompt",
                "Included papers",
                "Download report",
                "Extraction results",
                "Categorization mapping",
                "Categorized results",
            ],
        )
        self.assertEqual(
            [item["key"] for item in data["exportPackage"]],
            [
                "search-setup",
                "relevance-prompt",
                "included-papers",
                "download-report",
                "extraction-results",
                "categorization-mapping",
                "categorized-results",
            ],
        )
        self.assertEqual(
            data["exportPackage"][0]["downloadUrl"],
            "/projects/final-report/exports/search-setup",
        )
        self.assertNotIn("path", data["exportPackage"][0])
        self.assertTrue(all(item["exists"] for item in data["exportPackage"]))

    def test_categorization_workflow_projects_prototype_step_five_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "prototype-categorization-flow"
            write_json(project_dir / "search_conditions.json", {"project_name": "prototype-categorization-flow"})
            write_json(
                project_dir / "extraction" / "extraction_schema.json",
                {
                    "fields": [
                        {"name": "methods", "type": "Text", "description": "Study methods"},
                        {"name": "dataset", "type": "Text", "description": "Dataset"},
                    ]
                },
            )
            write_jsonl(
                project_dir / "extraction" / "extraction_results.jsonl",
                [
                    {"paper_id": "p1", "title": "Paper A", "extraction_status": "success", "methods": "Interview study", "dataset": "Clinician interviews"},
                    {"paper_id": "p2", "title": "Paper B", "extraction_status": "success", "methods": "Benchmark evaluation", "dataset": "MIMIC-IV"},
                    {"paper_id": "p3", "title": "Paper C", "extraction_status": "success", "methods": "Benchmark evaluation", "dataset": "MIMIC-IV"},
                ],
            )
            write_json(
                project_dir / "categorization" / "suggested_categories.json",
                {
                    "field": "methods",
                    "mode": "multiple",
                    "categories": ["Qualitative studies", "Benchmark studies"],
                    "category_descriptions": {"Benchmark studies": "Evaluation papers"},
                },
            )

            data = build_rp_data(output_root, "prototype-categorization-flow")

        workflow = data["categorizationWorkflow"]
        self.assertEqual(workflow["metrics"], {"papersExtracted": 3, "fields": 2})
        self.assertEqual(workflow["fieldNames"], ["methods", "dataset"])
        self.assertEqual(workflow["recommendedField"], "dataset")
        self.assertEqual(workflow["selectedField"], "methods")
        self.assertEqual(workflow["mode"], "multiple")
        self.assertEqual(workflow["suggestedCategories"], ["Qualitative studies", "Benchmark studies"])
        self.assertEqual(workflow["categoryDescriptions"], {"Benchmark studies": "Evaluation papers"})
        self.assertEqual(workflow["fieldProfiles"]["methods"]["papersWithValue"], 3)
        self.assertEqual(workflow["fieldProfiles"]["methods"]["uniqueValuesCount"], 2)
        self.assertEqual(workflow["fieldProfiles"]["methods"]["sampleValues"], ["Interview study", "Benchmark evaluation"])
        self.assertEqual(workflow["analysisDistributions"][0]["field"], "methods")
        self.assertEqual(workflow["analysisDistributions"][0]["values"][0], {"label": "Benchmark evaluation", "count": 2})
        self.assertEqual(workflow["fullResults"]["columns"], ["title", "methods", "dataset"])
        self.assertEqual(workflow["fullResults"]["rows"][0]["title"], "Paper A")
        self.assertFalse(workflow["done"])

    def test_categorization_analysis_handles_multiple_category_assignments_for_briefs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "multi-category-report"
            write_json(project_dir / "search_conditions.json", {"project_name": "multi-category-report"})
            write_json(
                project_dir / "categorization" / "categorization_mapping.json",
                {
                    "field": "methods",
                    "mode": "multiple",
                    "categories": ["Benchmarking", "Clinical workflow"],
                    "category_descriptions": {
                        "Benchmarking": "Evaluation against tasks or benchmarks",
                        "Clinical workflow": "Workflow or deployment evidence",
                    },
                    "mapping": {"Paper A": ["Benchmarking", "Clinical workflow"]},
                },
            )
            write_jsonl(
                project_dir / "categorization" / "categorized_results.jsonl",
                [
                    {
                        "paper_id": "p1",
                        "title": "Paper A",
                        "methods": "Benchmarked in clinical workflow simulation",
                        "category": ["Benchmarking", "Clinical workflow"],
                    }
                ],
            )

            data = build_rp_data(output_root, "multi-category-report")

        briefs = data["categorizationAnalysis"]["categories"]
        self.assertEqual([brief["name"] for brief in briefs], ["Benchmarking", "Clinical workflow"])
        self.assertEqual([brief["count"] for brief in briefs], [1, 1])
        self.assertEqual(briefs[0]["papers"][0]["title"], "Paper A")
        self.assertEqual(briefs[1]["papers"][0]["evidence"], "Benchmarked in clinical workflow simulation")

    def test_final_result_workspace_formats_nested_evidence_as_readable_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "nested-evidence-report"
            long_methods = (
                "Mixed-methods evaluation with clinician interviews, benchmark tasks, "
                "error analysis, and workflow simulation across multiple deployment contexts."
            )
            write_json(
                project_dir / "search_conditions.json",
                {
                    "project_name": "nested-evidence-report",
                    "description": "Survey LLMs in biomedicine",
                    "platforms": ["pubmed"],
                    "search_terms": "LLM AND biomedicine",
                },
            )
            write_jsonl(
                project_dir / "extraction" / "extraction_results.jsonl",
                [
                    {
                        "paper_id": "p1",
                        "title": "Nested Evidence Paper",
                        "source": "pubmed",
                        "year": 2025,
                        "datasets_used": ["MIMIC-IV", {"name": "UK Biobank", "size": "large cohort"}],
                        "methods": {"approach": long_methods, "models": ["GPT-4", "Claude"]},
                        "key_findings": {"primary": "LLMs can support triage", "caveats": ["bias", "workflow fit"]},
                        "extraction_source": "pdf",
                    },
                ],
            )
            write_json(
                project_dir / "categorization" / "categorization_mapping.json",
                {
                    "field": "key_findings",
                    "categories": ["Clinical Support"],
                    "mapping": {"Nested Evidence Paper": "Clinical Support"},
                },
            )
            write_jsonl(
                project_dir / "categorization" / "categorized_results.jsonl",
                [
                    {
                        "paper_id": "p1",
                        "title": "Nested Evidence Paper",
                        "key_findings": {"primary": "LLMs can support triage", "caveats": ["bias", "workflow fit"]},
                        "category": "Clinical Support",
                    },
                ],
            )

            data = build_rp_data(output_root, "nested-evidence-report")

        field_values = [field["value"] for field in data["evidenceMatrix"][0]["fields"]]
        joined_values = " ".join(field_values)
        category_evidence = data["categorizationAnalysis"]["categories"][0]["papers"][0]["evidence"]

        self.assertIn("MIMIC-IV", joined_values)
        self.assertIn("name: UK Biobank", joined_values)
        self.assertIn("size: large cohort", joined_values)
        self.assertIn("approach: Mixed-methods evaluation", joined_values)
        self.assertIn("models: GPT-4; Claude", joined_values)
        self.assertIn("primary: LLMs can support triage", category_evidence)
        self.assertIn("caveats: bias; workflow fit", category_evidence)
        self.assertNotIn("{", joined_values + category_evidence)
        self.assertNotIn("}", joined_values + category_evidence)
        self.assertNotIn("[", joined_values + category_evidence)
        self.assertNotIn("]", joined_values + category_evidence)
        self.assertNotIn("'primary':", category_evidence)
        self.assertNotIn("...", joined_values + category_evidence)

    def test_categorization_analysis_ignores_metadata_only_extracted_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            project_dir = output_root / "metadata-only-category-evidence"
            write_json(project_dir / "search_conditions.json", {"project_name": "metadata-only-category-evidence"})
            write_json(
                project_dir / "categorization" / "categorization_mapping.json",
                {
                    "field": "extracted_data",
                    "categories": ["Study metadata and relevance"],
                    "mapping": {"Metadata-only paper": "Study metadata and relevance"},
                },
            )
            write_jsonl(
                project_dir / "categorization" / "categorized_results.jsonl",
                [
                    {
                        "paper_id": "p1",
                        "title": "Metadata-only paper",
                        "extracted_data": {
                            "datasets_used": "",
                            "methods": "",
                            "key_findings": "",
                            "source_urls": ["https://example.test/paper"],
                            "confidence": "low",
                        },
                        "category": "Study metadata and relevance",
                    },
                ],
            )

            data = build_rp_data(output_root, "metadata-only-category-evidence")

        paper = data["categorizationAnalysis"]["categories"][0]["papers"][0]
        self.assertEqual(paper["title"], "Metadata-only paper")
        self.assertEqual(paper["evidence"], "")


if __name__ == "__main__":
    unittest.main()
