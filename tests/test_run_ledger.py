import json
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from reviewpilot_core import run_ledger
from reviewpilot_core.project_store import read_jsonl
from reviewpilot_core.task_runner import TaskRunner

ROOT = Path(__file__).resolve().parents[1]
RENDER = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('./frontend/app.js', 'utf8');
const slice = (from, to) => source.slice(source.indexOf(from), source.indexOf(to));
const context = vm.createContext({});
vm.runInContext(slice('function formatCount(', 'function createTaskPollRegistry('), context);
vm.runInContext(slice('  function runReportPanel(', '  function prismaFlowSvg('), context);
process.stdout.write(context.runReportPanel(JSON.parse(process.argv[1])));
"""


def render(report):
    result = subprocess.run(["node", "-e", RENDER, json.dumps(report)], cwd=ROOT, text=True, capture_output=True, timeout=10)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout


class RunLedgerTests(unittest.TestCase):
    def test_calls_in_worker_threads_are_filed_under_the_action_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            with run_ledger.activate(project, "screen"):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = [run_ledger.submit_in_context(pool, run_ledger.record_llm_call, "gpt-6-luna",
                                                            {"input_tokens": 100, "output_tokens": 10, "cost_usd": 0.001}) for _ in range(5)]
                    [future.result() for future in futures]
            run_ledger.record_llm_call("gpt-6-luna", {"cost_usd": 1.0})  # outside any activity: not recorded
            rows = read_jsonl(project / run_ledger.LEDGER)
            report = run_ledger.summary(project)
        self.assertEqual([row["kind"] for row in rows].count("llm"), 5)
        self.assertEqual(rows[-1]["kind"], "action")
        screening = report["stages"]["screening"]
        self.assertEqual((screening["actions"], screening["llm_calls"], screening["input_tokens"]), (1, 5, 500))
        self.assertAlmostEqual(screening["cost_usd"], 0.005)
        self.assertEqual(report["total"]["llm_calls"], 5)
        self.assertEqual(report["models"], ["gpt-6-luna"])

    def test_nested_activity_keeps_the_outer_stage_and_one_time_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            with run_ledger.activate(project, "chat:search"):
                with run_ledger.activate(project, "update-setup"):
                    run_ledger.record_llm_call("gpt-6-luna", {"cost_usd": 0.002})
            rows = read_jsonl(project / run_ledger.LEDGER)
        self.assertEqual([(row["kind"], row["stage"]) for row in rows], [("llm", "search"), ("action", "search")])

    def test_a_failed_action_is_recorded_as_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            with self.assertRaises(RuntimeError):
                with run_ledger.activate(project, "download-pdfs"):
                    raise RuntimeError("boom")
            self.assertEqual(read_jsonl(project / run_ledger.LEDGER)[0]["status"], "failed")

    def test_task_runner_times_actions_given_a_project_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            runner = TaskRunner()
            task_id = runner.submit("demo", "categorize", lambda: run_ledger.record_llm_call("gpt-6-luna", {"cost_usd": 0.01}) or {}, project_path=project)
            runner.wait(task_id, timeout=5)
            runner.shutdown()
            stage = run_ledger.summary(project)["stages"]["categorization"]
        self.assertEqual((stage["actions"], stage["llm_calls"], stage["cost_usd"]), (1, 1, 0.01))

    def test_progress_is_reported_while_the_activity_runs_and_cleared_after(self):
        from utils.human_interaction import show_progress
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            show_progress(1, 5)  # outside any activity: ignored
            self.assertIsNone(run_ledger.progress(project, "screen"))
            with run_ledger.activate(project, "screen"):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    run_ledger.submit_in_context(pool, show_progress, 3, 81).result()
                self.assertEqual(run_ledger.progress(project, "screen"), {"done": 3, "total": 81})
            self.assertIsNone(run_ledger.progress(project, "screen"))

    def test_human_corrections_come_from_the_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "filtered").mkdir()
            (project / "extraction").mkdir()
            included = [{"id": "a", "human_screening": {"decision": "include", "original_decision": "exclude"}},
                        {"id": "b", "human_screening": {"decision": "include", "original_decision": "include"}}, {"id": "c"}]
            (project / "filtered" / "included_papers.jsonl").write_text("\n".join(json.dumps(row) for row in included))
            rows = [{"human_fields": {"study_method": {"original_value": ["Survey"], "value": ["Survey", "Interview or think-aloud"]},
                                      "participant_count": {"original_value": 12, "value": 12}}}]
            (project / "extraction" / "extraction_results.jsonl").write_text(json.dumps(rows[0]))
            corrections = run_ledger.human_corrections(project)
        self.assertEqual(corrections, {"screening_reviewed": 2, "screening_overridden": 1,
                                       "extraction_fields_reviewed": 2, "extraction_fields_corrected": 1})


    def test_panel_lists_recorded_stages_with_totals_and_corrections(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            with run_ledger.activate(project, "screen"):
                run_ledger.record_llm_call("gpt-6-luna", {"input_tokens": 1000, "output_tokens": 100, "cost_usd": 0.0412})
            with run_ledger.activate(project, "download-pdfs"):
                pass
            html = render(run_ledger.summary(project))
        self.assertIn('data-ui="run-report"', html)
        self.assertIn("Paper Screening", html)
        self.assertIn("$0</td>", html)  # a stage without LLM calls costs nothing
        self.assertIn("Full-Text Retrieval", html)
        self.assertNotIn("Information Extraction", html)  # stages with nothing recorded are left out
        self.assertIn("$0.0412", html)
        self.assertIn("0 screening decisions changed (of 0 reviewed)", html)
        self.assertEqual(render(None), "")


if __name__ == "__main__":
    unittest.main()
