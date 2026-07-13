import json
import hashlib
import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from unittest.mock import Mock, patch

from reviewpilot_core.sub_agent_contracts import DownloadAgentContract
from reviewpilot_core.retrieval_retry import (
    ConfirmationRequired,
    InvalidRetryRequest,
    RevisionConflict,
    RetryItem,
    RetryMergedFacts,
    RetryPlannedPdf,
    RetryPreparation,
    RetryPublicationPlan,
    RetrySnapshot,
    StagedRetryOutcome,
    StagedRetryPdf,
    current_retry_snapshot,
    merge_staged_retry_facts,
    prepare_retry_request,
    prepare_retry_publication,
    run_retry_staging,
    retrieval_report_revision,
    stable_retry_id,
    _publication_pdf_fingerprint,
)
from reviewpilot_core.workflow_state import complete_action, initialize_workflow_state, load_workflow_state, start_action


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def partial_project(project: Path, *, failed_row=None, included=None) -> None:
    failed_row = failed_row or {"id": "failed-1", "title": "Failed paper", "failure_class": "paywall"}
    included = included or [{"id": "ok-1", "title": "Downloaded"}, {"id": "failed-1", "title": "Failed paper"}]
    write_jsonl(project / "filtered" / "included_papers.jsonl", included)
    write_json(project / "pdfs" / "download_report.json", {
        "success": 1,
        "failed": 1,
        "downloaded": [{"id": "ok-1", "title": "Downloaded"}],
        "failed_papers": [failed_row],
    })
    initialize_workflow_state(project)
    start_action(project, "collect")
    complete_action(project, "collect", {"total": 0, "platform_stats": {"pubmed": 0}, "platform_errors": {}})
    start_action(project, "screen")
    complete_action(project, "screen", {})
    start_action(project, "download-pdfs")
    complete_action(project, "download-pdfs", {"success": 1, "failed": 1})


def two_failure_project(project: Path) -> None:
    partial_project(project)
    write_jsonl(project / "filtered" / "included_papers.jsonl", [{"id": "ok-1"},
        {"id": "failed-1", "title": "Failed 1", "doi": "", "url": ""},
        {"id": "failed-2", "title": "Failed 2", "doi": "", "url": ""}])
    write_json(project / "pdfs" / "download_report.json", {
        "success": 1, "failed": 2, "downloaded": [{"id": "ok-1"}],
        "failed_papers": [{"id": "failed-1"}, {"id": "failed-2"}],
    })
    state = load_workflow_state(project); state["stages"]["retrieval"]["counts"]["failed"] = 2
    write_json(project / "workflow_state.json", state)


def authoritative_fingerprint(project: Path) -> tuple[bytes, bytes, bytes, tuple[tuple[str, str], ...]]:
    files = (
        project / "pdfs" / "download_report.json",
        project / "filtered" / "included_papers.jsonl",
        project / "workflow_state.json",
    )
    pdfs = tuple(sorted(
        (path.relative_to(project).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in (project / "pdfs").rglob("*") if path.is_file() and path.name != "download_report.json"
    ))
    return files[0].read_bytes(), files[1].read_bytes(), files[2].read_bytes(), pdfs


def assert_no_retry_writes(test: unittest.TestCase, project: Path, before) -> None:
    test.assertEqual(authoritative_fingerprint(project), before)
    test.assertFalse((project / ".retrieval_retry_pending.json").exists())
    test.assertFalse(any("retrieval_retry" in path.name for path in project.iterdir()))


def confirmed_preparation(project: Path, selected: list[str] | None = None):
    snapshot = current_retry_snapshot(project)
    selected = selected or [item.retry_id for item in snapshot.items]
    return prepare_retry_request(project, {
        "failed_ids": selected,
        "report_revision": snapshot.report_revision,
        "retry_confirmation": {"expected_report_revision": snapshot.report_revision, "failed_ids": selected},
    })


def fake_download(outcomes: list[bool], *, mutate=None):
    def run(root: Path, project_id: str):
        staging = root / project_id
        included_path = staging / "filtered" / "included_papers.jsonl"
        rows = [json.loads(line) for line in included_path.read_text().splitlines()]
        downloaded, failed = [], []
        for index, (row, succeeds) in enumerate(zip(rows, outcomes), 1):
            if succeeds:
                pdf = staging / "pdfs" / f"row{index}_paper.pdf"
                pdf.parent.mkdir(parents=True, exist_ok=True)
                pdf.write_bytes(b"%PDF-1.7\nretry")
                row.update(pdf_downloaded=True, pdf_path=str(pdf), retrieval_status="downloaded")
                detail = {"path": str(pdf), "title": row.get("title", "")}
                downloaded.append(detail)
            else:
                row.update(pdf_downloaded=False, retrieval_status="unavailable", pdf_failure_class="download_failed")
                row.pop("pdf_path", None)
                failed.append({"id": row.get("id", ""), "title": row.get("title", ""),
                    "doi": row.get("doi", ""), "url": row.get("url", ""),
                    "failure_class": "download_failed"})
        write_jsonl(included_path, rows)
        report = {"success": sum(outcomes), "failed": len(outcomes) - sum(outcomes),
                  "downloaded": downloaded, "failed_papers": failed,
                  "pdf_count": sum(outcomes), "attempted": len(outcomes)}
        write_json(staging / "pdfs" / "download_report.json", report)
        if mutate:
            mutate(staging, rows, report)
        return {"success": report["success"], "failed": report["failed"], "stats": {
            "success": report["success"], "failed": report["failed"]}}
    return run


class RetrievalRetryTests(unittest.TestCase):
    def test_real_download_contract_output_is_accepted_with_only_downloader_patched(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"; two_failure_project(project)
            calls = []
            class FakeFastDownloader:
                def __init__(self, *, email, output_dir, enable_browser_fallback): self.output_dir = Path(output_dir)
                def close(self): pass
                def download_batch(self, papers, progress_callback=None, progress_file=None):
                    calls.append([paper["id"] for paper in papers])
                    pdf = self.output_dir / "row1_retry.pdf"; pdf.write_bytes(b"%PDF-1.7\n")
                    papers[0].update(pdf_downloaded=True, pdf_path=str(pdf), pdf_method="direct_pdf")
                    papers[1].update(pdf_downloaded=False, pdf_failure_class="publisher_paywalled",
                        pdf_failure_detail="closed", pdf_failure_classes=["publisher_paywalled"], pdf_error="failed")
                    return {"success": 1, "failed": 1, "failed_papers": [{"id": papers[1]["id"],
                        "title": papers[1].get("title", ""), "failure_class": "publisher_paywalled"}]}
            callback = DownloadAgentContract().run
            with patch("utils.fast_pdf_downloader.FastCascadePDFDownloader", FakeFastDownloader):
                outcome = run_retry_staging(project, confirmed_preparation(project),
                    project / ".retrieval_retry_staging_case", callback)
            self.assertEqual(calls, [["failed-1", "failed-2"]])
            self.assertEqual(outcome.updated_rows[0]["pdf_method"], "direct_pdf")
            self.assertEqual(outcome.updated_rows[1]["pdf_failure_detail"], "closed")
            self.assertEqual(outcome.updated_rows[1]["retrieval_status"], "subscribed_unavailable")

    def test_staging_retries_only_selected_in_request_order_and_preserves_source_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"; two_failure_project(project)
            rows = [{"id": "ok-1"}, {"id": "failed-1", "custom": {"keep": [1]}, "pdf_downloaded": False,
                     "pdf_path": "/old", "retrieval_status": "unavailable", "pdf_failure_class": "old",
                     "web_search_fallback_pending": True}, {"id": "failed-2", "custom": {"keep": [2]}}]
            write_jsonl(project / "filtered" / "included_papers.jsonl", rows)
            snapshot = current_retry_snapshot(project); selected = [snapshot.items[1].retry_id, snapshot.items[0].retry_id]
            preparation = confirmed_preparation(project, selected)
            before = authoritative_fingerprint(project); seen = {"calls": 0}
            def callback(root, project_id):
                seen["calls"] += 1
                self.assertNotIn(project.name, project_id)
                staged = [json.loads(line) for line in (root / project_id / "filtered" / "included_papers.jsonl").read_text().splitlines()]
                seen["rows"] = staged
                return fake_download([True, False])(root, project_id)
            outcome = run_retry_staging(project, preparation, project / ".retrieval_retry_staging_case", callback)
            self.assertEqual([row["id"] for row in seen["rows"]], ["failed-2", "failed-1"])
            self.assertEqual(seen["calls"], 1)
            self.assertEqual(seen["rows"][1]["custom"], {"keep": [1]})
            self.assertFalse(any(key.startswith("web_search_fallback_") or key in {
                "pdf_downloaded", "pdf_path", "retrieval_status", "pdf_failure_class"} for row in seen["rows"] for key in row))
            self.assertEqual(outcome.selected_ids, tuple(selected))
            self.assertEqual([row["id"] for row in outcome.updated_rows], ["failed-2", "failed-1"])
            self.assertEqual(len(outcome.successful_pdfs), 1)
            self.assertTrue(outcome.staging_root.exists())
            self.assertEqual(outcome.staging_project_path.parent, outcome.staging_root)
            self.assertEqual(authoritative_fingerprint(project), before)
            with self.assertRaises(TypeError): outcome.updated_rows[0]["id"] = "changed"
            with self.assertRaises(TypeError): outcome.report["success"] = 9

    def test_staging_accepts_all_success_partial_and_all_failure(self):
        for flags in ([True, True], [True, False], [False, False]):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp) / "project"; two_failure_project(project)
                outcome = run_retry_staging(project, confirmed_preparation(project),
                    project / ".retrieval_retry_staging_case", fake_download(flags))
                self.assertEqual(outcome.report["success"], sum(flags))
                self.assertEqual(len(outcome.successful_pdfs), sum(flags))

    def test_staging_canonicalizes_success_failure_and_report_classification_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"; two_failure_project(project)
            outcome = run_retry_staging(project, confirmed_preparation(project),
                project / ".retrieval_retry_staging_case", fake_download([True, False]))
            success, failed = outcome.updated_rows
            self.assertEqual(success["retrieval_status"], "downloaded")
            self.assertFalse(any(key in success for key in ("pdf_failure_class", "pdf_failure_detail",
                "pdf_failure_classes", "pdf_error", "web_search_fallback_pending", "web_search_fallback_eligible")))
            self.assertEqual(failed["retrieval_status"], "unavailable")
            self.assertIs(failed["web_search_fallback_pending"], True)
            self.assertIs(failed["web_search_fallback_eligible"], True)
            failed_detail = outcome.report["failed_papers"][0]
            self.assertEqual(failed_detail["retrieval_status"], "unavailable")
            self.assertEqual(outcome.report["web_search_fallback_candidates"], (failed_detail,))
            self.assertEqual(outcome.report["unavailable_papers"], (failed_detail,))
            self.assertEqual(outcome.report["subscribed_papers"], ())

    def test_staging_rejects_noncanonical_success_failure_and_classification_lists(self):
        def success_row(staging, rows, report):
            rows[0].update(retrieval_status="unavailable", pdf_failure_class="download_failed")
            write_jsonl(staging / "filtered" / "included_papers.jsonl", rows)
        def success_report(staging, rows, report):
            report["downloaded"][0].update(retrieval_status="unavailable", web_search_fallback_pending=False)
            write_json(staging / "pdfs" / "download_report.json", report)
        def failed_report(staging, rows, report):
            report["failed_papers"][0].update(retrieval_status="downloaded", web_search_fallback_pending=False)
            write_json(staging / "pdfs" / "download_report.json", report)
        def wrong_list_member(staging, rows, report):
            report["unavailable_papers"] = [{"id": "unknown", "failure_class": "download_failed"}]
            write_json(staging / "pdfs" / "download_report.json", report)
        def wrong_list_facts(staging, rows, report):
            report["web_search_fallback_candidates"] = [{**report["failed_papers"][0], "web_search_fallback_eligible": False}]
            write_json(staging / "pdfs" / "download_report.json", report)
        for mutation in (success_row, success_report, failed_report, wrong_list_member, wrong_list_facts):
            with self.subTest(mutation=mutation.__name__), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp) / "project"; two_failure_project(project); staging = project / ".retrieval_retry_staging_case"
                with self.assertRaises(ValueError):
                    run_retry_staging(project, confirmed_preparation(project), staging,
                        fake_download([True, False], mutate=mutation))
                self.assertFalse(staging.exists())

    def test_staging_enforces_symmetric_success_and_failure_schema(self):
        def success_flag(staging, rows, report):
            report["downloaded"][0]["pdf_downloaded"] = False
            write_json(staging / "pdfs" / "download_report.json", report)
        def success_path(staging, rows, report):
            report["downloaded"][0]["pdf_path"] = str(staging / "pdfs" / "other.pdf")
            write_json(staging / "pdfs" / "download_report.json", report)
        def failed_flag(staging, rows, report):
            report["failed_papers"][0]["pdf_downloaded"] = True
            write_json(staging / "pdfs" / "download_report.json", report)
        def failed_path(staging, rows, report):
            report["failed_papers"][0]["path"] = "/forged.pdf"
            write_json(staging / "pdfs" / "download_report.json", report)
        def failed_method(staging, rows, report):
            rows[1]["pdf_method"] = "forged"; report["failed_papers"][0]["pdf_method"] = "forged"
            write_jsonl(staging / "filtered" / "included_papers.jsonl", rows)
            write_json(staging / "pdfs" / "download_report.json", report)
        for mutation in (success_flag, success_path, failed_flag, failed_path, failed_method):
            with self.subTest(mutation=mutation.__name__), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp) / "project"; two_failure_project(project); staging = project / ".retrieval_retry_staging_case"
                with self.assertRaises(ValueError):
                    run_retry_staging(project, confirmed_preparation(project), staging,
                        fake_download([True, False], mutate=mutation))
                self.assertFalse(staging.exists())

    def test_staging_accepts_matching_success_aliases_and_failed_false_or_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"; two_failure_project(project)
            def aliases(staging, rows, report):
                report["downloaded"][0].update(pdf_downloaded=True, pdf_path=report["downloaded"][0]["path"])
                report["failed_papers"][0]["pdf_downloaded"] = False
                write_json(staging / "pdfs" / "download_report.json", report)
            outcome = run_retry_staging(project, confirmed_preparation(project),
                project / ".retrieval_retry_staging_case", fake_download([True, False], mutate=aliases))
            success = outcome.report["downloaded"][0]; failed = outcome.report["failed_papers"][0]
            self.assertIs(success["pdf_downloaded"], True)
            self.assertEqual(success["pdf_path"], success["path"])
            self.assertIs(failed["pdf_downloaded"], False)
            self.assertNotIn("path", failed); self.assertNotIn("pdf_path", failed); self.assertNotIn("pdf_method", failed)

    def test_staging_rejects_invalid_location_and_cleans_its_own_failures(self):
        for kind in ("wrong-name", "nested", "existing", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp) / "project"; two_failure_project(project); preparation = confirmed_preparation(project)
                staging = project / ("bad" if kind == "wrong-name" else ".retrieval_retry_staging_case")
                if kind == "nested": staging = project / "nested" / staging.name
                elif kind == "existing": staging.mkdir()
                elif kind == "symlink": staging.symlink_to(Path(tmp), target_is_directory=True)
                before = authoritative_fingerprint(project)
                with self.assertRaises(ValueError): run_retry_staging(project, preparation, staging, fake_download([True, True]))
                self.assertEqual(authoritative_fingerprint(project), before)

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"; two_failure_project(project); preparation = confirmed_preparation(project)
            staging = project / ".retrieval_retry_staging_case"
            with self.assertRaisesRegex(ValueError, "^Retry downloader failed$"):
                run_retry_staging(project, preparation, staging, lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
            self.assertFalse(staging.exists())

    def test_staging_preparation_is_bound_to_its_source_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first"; second = Path(tmp) / "second"
            two_failure_project(first); two_failure_project(second)
            preparation = confirmed_preparation(first)
            first_before = authoritative_fingerprint(first); second_before = authoritative_fingerprint(second)
            staging = second / ".retrieval_retry_staging_case"
            callback = Mock()
            with self.assertRaises(ValueError):
                run_retry_staging(second, preparation, staging, callback)
            callback.assert_not_called()
            self.assertFalse(staging.exists())
            self.assertEqual(authoritative_fingerprint(first), first_before)
            self.assertEqual(authoritative_fingerprint(second), second_before)

    def test_staging_rejects_result_report_identity_and_count_conflicts(self):
        mutations = {
            "result conflict": lambda staging, rows, report: None,
            "unknown failure": lambda staging, rows, report: (report["failed_papers"][0].update(id="unknown"), write_json(staging / "pdfs" / "download_report.json", report)),
            "duplicate failure": lambda staging, rows, report: (report.update(failed=2, failed_papers=[report["failed_papers"][0]] * 2), write_json(staging / "pdfs" / "download_report.json", report)),
            "row reorder": lambda staging, rows, report: write_jsonl(staging / "filtered" / "included_papers.jsonl", list(reversed(rows))),
            "source mutation": lambda staging, rows, report: (rows[0].update(title="changed"), write_jsonl(staging / "filtered" / "included_papers.jsonl", rows)),
            "unknown pdf mutation": lambda staging, rows, report: (rows[0].update(pdf_unexpected="forged"), write_jsonl(staging / "filtered" / "included_papers.jsonl", rows)),
            "unknown web fallback mutation": lambda staging, rows, report: (rows[0].update(web_search_fallback_internal_path="/private"), write_jsonl(staging / "filtered" / "included_papers.jsonl", rows)),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp) / "project"; two_failure_project(project); preparation = confirmed_preparation(project)
                callback = fake_download([False, False], mutate=mutation)
                if name == "result conflict":
                    base = callback
                    callback = lambda root, pid: {**base(root, pid), "success": 1}
                staging = project / ".retrieval_retry_staging_case"
                with self.assertRaises(ValueError): run_retry_staging(project, preparation, staging, callback)
                self.assertFalse(staging.exists())

    def test_staging_rejects_conflicting_failure_and_report_identity_facts(self):
        def failure_class(staging, rows, report):
            rows[0]["pdf_failure_class"] = "subscription_closed"
            write_jsonl(staging / "filtered" / "included_papers.jsonl", rows)
        def report_doi(staging, rows, report):
            report["failed_papers"][0]["doi"] = "10.1/forged"
            write_json(staging / "pdfs" / "download_report.json", report)
        def report_title(staging, rows, report):
            report["failed_papers"][0]["title"] = "Forged title"
            write_json(staging / "pdfs" / "download_report.json", report)
        def bad_status(staging, rows, report):
            rows[0]["retrieval_status"] = "subscribed_unavailable"
            write_jsonl(staging / "filtered" / "included_papers.jsonl", rows)
        for mutation in (failure_class, report_doi, report_title, bad_status):
            with self.subTest(mutation=mutation.__name__), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp) / "project"; two_failure_project(project); staging = project / ".retrieval_retry_staging_case"
                write_jsonl(project / "filtered" / "included_papers.jsonl", [
                    {"id": "ok-1"}, {"id": "failed-1", "doi": "10.1/source", "title": "Source title"}, {"id": "failed-2"}])
                with self.assertRaises(ValueError):
                    run_retry_staging(project, confirmed_preparation(project), staging,
                        fake_download([False, False], mutate=mutation))
                self.assertFalse(staging.exists())

    def test_staging_rejects_invalid_pdf_content_paths_symlinks_and_extras(self):
        def invalid_content(staging, rows, report):
            Path(rows[0]["pdf_path"]).write_bytes(b"<html>")
        def extra_pdf(staging, rows, report):
            (staging / "pdfs" / "extra.pdf").write_bytes(b"%PDF-extra")
        def symlink_pdf(staging, rows, report):
            path = Path(rows[0]["pdf_path"]); external = staging.parent / "external.pdf"; external.write_bytes(b"%PDF-x")
            path.unlink(); path.symlink_to(external)
        def lexical_escape(staging, rows, report):
            rows[0]["pdf_path"] = str(staging / "pdfs" / ".." / "pdfs" / "row1_paper.pdf")
            report["downloaded"][0]["path"] = rows[0]["pdf_path"]
            write_jsonl(staging / "filtered" / "included_papers.jsonl", rows)
            write_json(staging / "pdfs" / "download_report.json", report)
        for name, mutation in (("content", invalid_content), ("extra", extra_pdf), ("symlink", symlink_pdf), ("escape", lexical_escape)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp) / "project"; two_failure_project(project); staging = project / ".retrieval_retry_staging_case"
                with self.assertRaises(ValueError):
                    run_retry_staging(project, confirmed_preparation(project), staging, fake_download([True, False], mutate=mutation))
                self.assertFalse(staging.exists())

    def test_staging_rejects_malformed_flags_details_and_authority_symlinks(self):
        def bad_flag(staging, rows, report):
            rows[0]["pdf_downloaded"] = 1
            write_jsonl(staging / "filtered" / "included_papers.jsonl", rows)
        def bad_details(staging, rows, report):
            report["downloaded"] = ["not-an-object"]
            write_json(staging / "pdfs" / "download_report.json", report)
        def linked_report(staging, rows, report):
            path = staging / "pdfs" / "download_report.json"; external = staging.parent / "outside-report.json"
            external.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(external)
        for mutation in (bad_flag, bad_details, linked_report):
            with self.subTest(mutation=mutation.__name__), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp) / "project"; two_failure_project(project); staging = project / ".retrieval_retry_staging_case"
                before = authoritative_fingerprint(project)
                with self.assertRaises(ValueError) as raised:
                    run_retry_staging(project, confirmed_preparation(project), staging, fake_download([True, False], mutate=mutation))
                self.assertNotIn(str(Path(tmp)), str(raised.exception))
                self.assertFalse(staging.exists())
                self.assertEqual(authoritative_fingerprint(project), before)

    def test_staging_cleanup_removes_root_replaced_by_regular_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"; two_failure_project(project); staging = project / ".retrieval_retry_staging_case"
            def replace_root(root, project_id):
                import shutil
                shutil.rmtree(root); root.write_text("hostile", encoding="utf-8")
                return {"success": 0, "failed": 2, "stats": {"success": 0, "failed": 2}}
            with self.assertRaises(ValueError): run_retry_staging(project, confirmed_preparation(project), staging, replace_root)
            self.assertFalse(staging.exists())

    def test_staging_creation_errors_are_path_free_and_leave_no_residue(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"; two_failure_project(project)
            staging = project / (".retrieval_retry_staging_" + "x" * 300)
            with self.assertRaises(ValueError) as raised:
                run_retry_staging(project, confirmed_preparation(project), staging, Mock())
            self.assertNotIn(str(project), str(raised.exception))
            self.assertNotIn(staging.name, {path.name for path in project.iterdir()})
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"; two_failure_project(project); staging = project / ".retrieval_retry_staging_case"
            with patch("reviewpilot_core.retrieval_retry.atomic_write_jsonl", side_effect=OSError(str(project))):
                with self.assertRaisesRegex(ValueError, "^Retry staging setup failed$") as raised:
                    run_retry_staging(project, confirmed_preparation(project), staging, Mock())
            self.assertNotIn(str(project), str(raised.exception))
            self.assertFalse(staging.exists())

    def test_authoritative_fingerprint_io_errors_are_path_free_before_and_after_callback(self):
        for phase in ("before", "after"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp) / "project"; two_failure_project(project)
                authoritative_pdf = project / "pdfs" / "existing.pdf"; authoritative_pdf.write_bytes(b"%PDF-existing")
                staging = project / ".retrieval_retry_staging_case"; callback = Mock(side_effect=fake_download([True, False]))
                original = Path.open; reads = 0
                def guarded(path, *args, **kwargs):
                    nonlocal reads
                    if path == authoritative_pdf and (args[0] if args else kwargs.get("mode", "r")) == "rb":
                        reads += 1
                        if phase == "before" or reads > 1:
                            raise OSError(str(authoritative_pdf))
                    return original(path, *args, **kwargs)
                with patch.object(Path, "open", guarded):
                    with self.assertRaisesRegex(ValueError, "^Authoritative retry facts are unavailable$") as raised:
                        run_retry_staging(project, confirmed_preparation(project), staging, callback)
                self.assertNotIn(str(project), str(raised.exception))
                self.assertEqual(callback.call_count, 0 if phase == "before" else 1)
                self.assertFalse(staging.exists())

    def test_authoritative_pdf_fingerprint_streams_without_read_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"; two_failure_project(project)
            authoritative_pdf = project / "pdfs" / "existing.pdf"; authoritative_pdf.write_bytes(b"%PDF-existing")
            original = Path.read_bytes
            def guarded(path):
                if path == authoritative_pdf: raise AssertionError("authoritative PDF read_bytes must not be used")
                return original(path)
            with patch.object(Path, "read_bytes", guarded):
                outcome = run_retry_staging(project, confirmed_preparation(project),
                    project / ".retrieval_retry_staging_case", fake_download([True, False]))
            self.assertTrue(outcome.staging_project_path.exists())

    def test_staging_pdf_header_validation_does_not_load_entire_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"; two_failure_project(project)
            original = Path.read_bytes
            def guarded(path):
                if ".retrieval_retry_staging_" in str(path) and path.suffix == ".pdf":
                    raise AssertionError("staging PDF read_bytes must not be used")
                return original(path)
            with patch.object(Path, "read_bytes", guarded):
                outcome = run_retry_staging(project, confirmed_preparation(project),
                    project / ".retrieval_retry_staging_case", fake_download([True, False]))
            self.assertEqual(len(outcome.successful_pdfs), 1)

    def test_publication_pdf_fingerprint_rejects_symlink_swap_before_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "pdfs"; parent.mkdir()
            source = parent / "source.pdf"; source.write_bytes(b"%PDF-original")
            external = Path(tmp) / "external.pdf"; external.write_bytes(b"%PDF-external")
            resolved_parent = parent.resolve(strict=True)
            original_resolve = Path.resolve
            swapped = False

            def swap_after_precheck(path, *args, **kwargs):
                nonlocal swapped
                resolved = original_resolve(path, *args, **kwargs)
                if path == source and not swapped:
                    swapped = True
                    source.unlink()
                    source.symlink_to(external)
                return resolved

            with patch.object(Path, "resolve", swap_after_precheck):
                with self.assertRaises(ValueError) as raised:
                    _publication_pdf_fingerprint(source, resolved_parent)
            self.assertTrue(swapped)
            self.assertNotIn(str(Path(tmp)), str(raised.exception))

    def test_publication_pdf_fingerprint_remains_safe_without_o_nofollow(self):
        had_nofollow = hasattr(os, "O_NOFOLLOW")
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if had_nofollow:
            del os.O_NOFOLLOW
        try:
            with tempfile.TemporaryDirectory() as tmp:
                parent = Path(tmp) / "pdfs"; parent.mkdir()
                source = parent / "source.pdf"; payload = b" \n%PDF-portable"
                source.write_bytes(payload)
                size, digest, identity = _publication_pdf_fingerprint(
                    source, parent.resolve(strict=True))
                self.assertEqual((size, digest), (len(payload), hashlib.sha256(payload).hexdigest()))
                self.assertEqual(identity, (source.stat().st_dev, source.stat().st_ino))

            with tempfile.TemporaryDirectory() as tmp:
                parent = Path(tmp) / "pdfs"; parent.mkdir()
                source = parent / "source.pdf"; source.write_bytes(b"%PDF-original")
                external = Path(tmp) / "external.pdf"; external.write_bytes(b"%PDF-external")
                original_open = os.open
                swapped = False

                def swap_between_lstat_and_open(path, flags, *args, **kwargs):
                    nonlocal swapped
                    if Path(path) == source and not swapped:
                        swapped = True
                        source.unlink()
                        source.symlink_to(external)
                    return original_open(path, flags, *args, **kwargs)

                with patch("reviewpilot_core.retrieval_retry.os.open", swap_between_lstat_and_open):
                    with self.assertRaises(ValueError) as raised:
                        _publication_pdf_fingerprint(source, parent.resolve(strict=True))
                self.assertTrue(swapped)
                self.assertNotIn(str(Path(tmp)), str(raised.exception))
        finally:
            if had_nofollow:
                os.O_NOFOLLOW = nofollow

    def test_publication_pdf_fingerprint_rejects_append_after_stream_eof(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "pdfs"; parent.mkdir()
            source = parent / "source.pdf"; source.write_bytes(b"%PDF-original")
            identity = (source.stat().st_dev, source.stat().st_ino)
            original_read = os.read
            appended = False

            def append_at_eof(fd, size):
                nonlocal appended
                chunk = original_read(fd, size)
                opened = os.fstat(fd)
                if not chunk and (opened.st_dev, opened.st_ino) == identity and not appended:
                    appended = True
                    with source.open("ab") as stream:
                        stream.write(b"-concurrent-append")
                return chunk

            with patch("reviewpilot_core.retrieval_retry.os.read", append_at_eof):
                with self.assertRaises(ValueError):
                    _publication_pdf_fingerprint(source, parent.resolve(strict=True))
            self.assertTrue(appended)

    def test_publication_pdf_fingerprint_rejects_same_size_overwrite_after_stream_eof(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "pdfs"; parent.mkdir()
            source = parent / "source.pdf"; payload = b"%PDF-original"
            source.write_bytes(payload)
            os.utime(source, ns=(1_000_000_000, 1_000_000_000))
            identity = (source.stat().st_dev, source.stat().st_ino)
            original_read = os.read
            overwritten = False

            def overwrite_at_eof(fd, size):
                nonlocal overwritten
                chunk = original_read(fd, size)
                opened = os.fstat(fd)
                if not chunk and (opened.st_dev, opened.st_ino) == identity and not overwritten:
                    overwritten = True
                    with source.open("r+b") as stream:
                        stream.write(b"%PDF-replaced")
                        stream.flush()
                        os.fsync(stream.fileno())
                return chunk

            with patch("reviewpilot_core.retrieval_retry.os.read", overwrite_at_eof):
                with self.assertRaises(ValueError):
                    _publication_pdf_fingerprint(source, parent.resolve(strict=True))
            self.assertTrue(overwritten)

    def test_staging_rejects_count_contract_variants_duplicate_paths_and_symlink_boundaries(self):
        for location in ("root", "stats"):
            for value in (True, "1", None, 9):
                with self.subTest(location=location, value=value), tempfile.TemporaryDirectory() as tmp:
                    project = Path(tmp) / "project"; two_failure_project(project); staging = project / ".retrieval_retry_staging_case"
                    base = fake_download([True, False])
                    def callback(root, pid, location=location, value=value):
                        result = base(root, pid)
                        if location == "root": result["success"] = value
                        else: result["stats"]["success"] = value
                        return result
                    with self.assertRaises(ValueError): run_retry_staging(project, confirmed_preparation(project), staging, callback)
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"; two_failure_project(project); staging = project / ".retrieval_retry_staging_case"
            def duplicate(staged, rows, report):
                report.update(success=2, failed=0, failed_papers=[], downloaded=report["downloaded"] * 2)
                rows[1].update(pdf_downloaded=True, pdf_path=rows[0]["pdf_path"], retrieval_status="downloaded")
                write_jsonl(staged / "filtered" / "included_papers.jsonl", rows); write_json(staged / "pdfs" / "download_report.json", report)
            with self.assertRaises(ValueError):
                run_retry_staging(project, confirmed_preparation(project), staging, fake_download([True, False], mutate=duplicate))

    def test_staging_rejects_symlinks_at_every_staging_boundary(self):
        for boundary in ("root", "project", "filtered", "pdfs", "included"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp) / "project"; two_failure_project(project); staging = project / ".retrieval_retry_staging_case"
                base = fake_download([True, False])
                def callback(root, pid, boundary=boundary):
                    import shutil
                    result = base(root, pid); staged = root / pid
                    targets = {"root": root, "project": staged, "filtered": staged / "filtered",
                        "pdfs": staged / "pdfs", "included": staged / "filtered" / "included_papers.jsonl"}
                    target = targets[boundary]; external = project / f"external-{boundary}"
                    shutil.move(str(target), str(external)); target.symlink_to(external, target_is_directory=external.is_dir())
                    return result
                with self.assertRaises(ValueError): run_retry_staging(project, confirmed_preparation(project), staging, callback)
                self.assertFalse(staging.exists())
    def test_prepare_rejects_malformed_selection_without_writing(self):
        cases = (
            ([], InvalidRetryRequest),
            ({}, InvalidRetryRequest),
            ({"failed_ids": "bad", "report_revision": "x"}, InvalidRetryRequest),
            ({"failed_ids": [], "report_revision": "x"}, InvalidRetryRequest),
            ({"failed_ids": [1], "report_revision": "x"}, InvalidRetryRequest),
            ({"failed_ids": [""], "report_revision": "x"}, InvalidRetryRequest),
            ({"failed_ids": ["x", "x"], "report_revision": "x"}, InvalidRetryRequest),
            ({"failed_ids": ["unknown"], "report_revision": "x"}, InvalidRetryRequest),
        )
        for payload, error in cases:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp); partial_project(project)
                if isinstance(payload, dict) and payload.get("report_revision") == "x":
                    payload = {**payload, "report_revision": current_retry_snapshot(project).report_revision}
                before = authoritative_fingerprint(project)
                with self.assertRaises(error): prepare_retry_request(project, payload)
                assert_no_retry_writes(self, project, before)

    def test_prepare_rejects_missing_or_invalid_revision_without_writing(self):
        for revision in (None, 1, "", "changed"):
            with self.subTest(revision=revision), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp); partial_project(project)
                retry_id = current_retry_snapshot(project).items[0].retry_id
                payload = {"failed_ids": [retry_id]}
                if revision is not None: payload["report_revision"] = revision
                before = authoritative_fingerprint(project)
                error = RevisionConflict if revision == "changed" else InvalidRetryRequest
                with self.assertRaises(error): prepare_retry_request(project, payload)
                assert_no_retry_writes(self, project, before)

    def test_unconfirmed_request_returns_only_frozen_server_confirmation_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp); partial_project(project)
            snapshot = current_retry_snapshot(project); retry_id = snapshot.items[0].retry_id
            before = authoritative_fingerprint(project)
            with self.assertRaises(ConfirmationRequired) as raised:
                prepare_retry_request(project, {"failed_ids": [retry_id], "report_revision": snapshot.report_revision})
            self.assertEqual(raised.exception.code, "confirmation_required")
            self.assertEqual(raised.exception.expected_report_revision, snapshot.report_revision)
            self.assertEqual(raised.exception.failed_ids, (retry_id,))
            assert_no_retry_writes(self, project, before)

    def test_unconfirmed_and_confirmed_selection_order_is_exact_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp); two_failure_project(project)
            snapshot = current_retry_snapshot(project)
            requested = [snapshot.items[1].retry_id, snapshot.items[0].retry_id]
            before = authoritative_fingerprint(project)
            with self.assertRaises(ConfirmationRequired) as raised:
                prepare_retry_request(project, {"failed_ids": requested, "report_revision": snapshot.report_revision})
            self.assertEqual(raised.exception.failed_ids, tuple(requested))
            assert_no_retry_writes(self, project, before)

            payload = {"failed_ids": requested, "report_revision": snapshot.report_revision,
                "retry_confirmation": {"expected_report_revision": snapshot.report_revision, "failed_ids": list(reversed(requested))}}
            with self.assertRaises(InvalidRetryRequest): prepare_retry_request(project, payload)
            assert_no_retry_writes(self, project, before)

    def test_prepare_rejects_malformed_or_mismatched_confirmation_without_writing(self):
        for confirmation, error in ((None, InvalidRetryRequest), ({}, InvalidRetryRequest),
            ({"expected_report_revision": 1, "failed_ids": []}, InvalidRetryRequest),
            ({"expected_report_revision": "changed", "failed_ids": []}, RevisionConflict),
            ({"expected_report_revision": "CURRENT", "failed_ids": []}, InvalidRetryRequest)):
            with self.subTest(confirmation=confirmation, error=error), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp); partial_project(project)
                snapshot = current_retry_snapshot(project); retry_id = snapshot.items[0].retry_id
                value = confirmation
                if isinstance(value, dict):
                    value = dict(value)
                    if value.get("expected_report_revision") == "CURRENT": value["expected_report_revision"] = snapshot.report_revision
                payload = {"failed_ids": [retry_id], "report_revision": snapshot.report_revision, "retry_confirmation": value}
                before = authoritative_fingerprint(project)
                with self.assertRaises(error): prepare_retry_request(project, payload)
                assert_no_retry_writes(self, project, before)

    def test_prepare_rejects_unavailable_current_recovery_without_writing(self):
        for condition in ("stale", "malformed", "no_failures"):
            with self.subTest(condition=condition), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp); partial_project(project)
                snapshot = current_retry_snapshot(project)
                if condition == "stale":
                    state = load_workflow_state(project); state["stages"]["retrieval"]["stale"] = True
                    write_json(project / "workflow_state.json", state)
                elif condition == "malformed":
                    (project / "pdfs" / "download_report.json").write_text("not-json", encoding="utf-8")
                else:
                    write_json(project / "pdfs" / "download_report.json", {
                        "success": 2, "failed": 0, "downloaded": [{"id": "ok-1"}, {"id": "failed-1"}], "failed_papers": []})
                    state = load_workflow_state(project); state["stages"]["retrieval"].update(
                        status="completed", counts={"succeeded": 2, "failed": 0}, error=None, stale=False)
                    write_json(project / "workflow_state.json", state)
                before = authoritative_fingerprint(project)
                with self.assertRaises(InvalidRetryRequest):
                    prepare_retry_request(project, {"failed_ids": [snapshot.items[0].retry_id], "report_revision": snapshot.report_revision})
                assert_no_retry_writes(self, project, before)

    def test_confirmed_prepare_reads_one_snapshot_and_returns_immutable_selected_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp); two_failure_project(project)
            snapshot = current_retry_snapshot(project); selected = (snapshot.items[1].retry_id, snapshot.items[0].retry_id)
            payload = {"failed_ids": list(selected), "report_revision": snapshot.report_revision,
                "retry_confirmation": {"expected_report_revision": snapshot.report_revision, "failed_ids": list(selected)}}
            with patch("reviewpilot_core.retrieval_retry.current_retry_snapshot", wraps=current_retry_snapshot) as current:
                prepared = prepare_retry_request(project, payload)
            self.assertEqual(current.call_count, 1)
            self.assertEqual(prepared.selected_ids, selected)
            self.assertEqual(prepared.items, (snapshot.items[1], snapshot.items[0]))
            self.assertEqual([row["id"] for row in prepared.included_rows], ["failed-2", "failed-1"])
            with self.assertRaises(TypeError): prepared.included_rows[0]["id"] = "changed"
    def test_stable_identity_is_normalized_opaque_and_uses_frozen_precedence(self):
        identifier = stable_retry_id({"id": "  AbC  ", "doi": "10.1/OTHER", "title": "Visible"})
        self.assertEqual(identifier, stable_retry_id({"id": "abc"}))
        self.assertEqual(len(identifier), 64)
        self.assertNotIn("abc", identifier)
        self.assertNotEqual(identifier, stable_retry_id({"doi": "abc"}))
        with self.assertRaises(ValueError):
            stable_retry_id({"id": "", "doi": 5})
        with self.assertRaises(ValueError):
            stable_retry_id([])

    def test_stable_identity_precedence_is_id_then_doi_then_url_then_title(self):
        row = {"id": "I", "doi": "D", "url": "U", "title": "T"}
        for discarded in ((), ("id",), ("id", "doi"), ("id", "doi", "url")):
            candidate = {key: value for key, value in row.items() if key not in discarded}
            expected_field = next(key for key in ("id", "doi", "url", "title") if key in candidate)
            self.assertEqual(stable_retry_id(candidate), stable_retry_id({expected_field: candidate[expected_field]}))

    def test_report_revision_is_canonical_and_changes_with_report_or_stage_identity(self):
        report = {"failed": 1, "success": 0, "downloaded": [], "failed_papers": [{"id": "a"}]}
        stage = {"attempt": 2, "status": "failed", "counts": {"succeeded": 0, "failed": 1}}
        revision = retrieval_report_revision(report, stage)
        self.assertEqual(revision, retrieval_report_revision(dict(reversed(list(report.items()))), {"status": "failed", "attempt": 2}))
        self.assertNotEqual(revision, retrieval_report_revision({**report, "note": "changed"}, stage))
        self.assertNotEqual(revision, retrieval_report_revision(report, {"attempt": 3, "status": "failed"}))
        self.assertNotEqual(revision, retrieval_report_revision(report, {"attempt": 2, "status": "partial"}))

    def test_valid_partial_snapshot_maps_failed_item_and_is_detached_from_disk_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            partial_project(project)
            snapshot = current_retry_snapshot(project)
            fresh = current_retry_snapshot(project)
        self.assertEqual(snapshot.items[0].report_index, 0)
        self.assertEqual(snapshot.items[0].included_index, 1)
        self.assertEqual(fresh.report["failed_papers"][0]["title"], "Failed paper")
        self.assertEqual(fresh.included[1]["title"], "Failed paper")

    def test_snapshot_authoritative_facts_are_recursively_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp); partial_project(project)
            snapshot = current_retry_snapshot(project)
            operations = (
                lambda: snapshot.report.__setitem__("failed", 2),
                lambda: snapshot.report["failed_papers"][0].__setitem__("id", "changed"),
                lambda: snapshot.report["failed_papers"].append({"id": "extra"}),
                lambda: snapshot.report.__delitem__("success"),
                lambda: snapshot.included[0].__setitem__("id", "changed"),
                lambda: snapshot.included.append({"id": "extra"}),
                lambda: snapshot.included.__delitem__(0),
                lambda: snapshot.ledger["counts"].__setitem__("failed", 9),
                lambda: snapshot.ledger.__delitem__("status"),
            )
            for operation in operations:
                with self.subTest(operation=operation), self.assertRaises((AttributeError, TypeError)):
                    operation()

    def test_mutable_fact_copies_are_plain_isolated_deep_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp); partial_project(project)
            snapshot = current_retry_snapshot(project)
            first_report, first_included, first_ledger = snapshot.mutable_fact_copies()
            second_report, second_included, second_ledger = snapshot.mutable_fact_copies()
            first_report["failed_papers"][0]["id"] = "changed"
            first_report["failed_papers"].append({"id": "extra"})
            del first_report["downloaded"]
            first_included[0]["id"] = "changed"
            first_included.append({"id": "extra"})
            del first_included[1]
            first_ledger["counts"]["failed"] = 9
            del first_ledger["error"]
        self.assertEqual(second_report["failed_papers"], [{"id": "failed-1", "title": "Failed paper", "failure_class": "paywall"}])
        self.assertEqual(len(second_included), 2); self.assertEqual(second_ledger["counts"]["failed"], 1)
        self.assertEqual(snapshot.report["failed_papers"][0]["id"], "failed-1")
        self.assertEqual(snapshot.included[0]["id"], "ok-1"); self.assertEqual(snapshot.ledger["counts"]["failed"], 1)

    def test_structured_failed_snapshot_is_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            partial_project(project)
            report = {"success": 0, "failed": 1, "downloaded": [], "failed_papers": [{"id": "failed-1", "title": "Failed"}]}
            write_json(project / "pdfs" / "download_report.json", report)
            state = load_workflow_state(project)
            stage = state["stages"]["retrieval"]
            stage.update(status="failed", counts={"succeeded": 0, "failed": 1}, error="Action produced no successful outputs (1 failed).", stale=False)
            write_json(project / "workflow_state.json", state)
            snapshot = current_retry_snapshot(project)
        self.assertEqual(snapshot.ledger["status"], "failed")

    def test_rejects_noncurrent_or_inconsistent_facts(self):
        mutations = {
            "completed": lambda p, r, s: s.update(status="completed"),
            "stale": lambda p, r, s: s.update(stale=True),
            "no failure": lambda p, r, s: (r.update(failed=0, failed_papers=[]), s["counts"].update(failed=0)),
            "ledger conflict": lambda p, r, s: s["counts"].update(failed=2),
            "detail conflict": lambda p, r, s: r.update(failed=2),
            "missing included mapping": lambda p, r, s: write_jsonl(p / "filtered" / "included_papers.jsonl", [{"id": "ok-1"}]),
            "duplicate included identity": lambda p, r, s: write_jsonl(p / "filtered" / "included_papers.jsonl", [{"id": "failed-1"}, {"id": "FAILED-1"}]),
            "malformed failed row": lambda p, r, s: r.update(failed_papers=["bad"]),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp)
                partial_project(project)
                report = json.loads((project / "pdfs" / "download_report.json").read_text())
                state = load_workflow_state(project)
                stage = state["stages"]["retrieval"]
                mutate(project, report, stage)
                write_json(project / "pdfs" / "download_report.json", report)
                write_json(project / "workflow_state.json", state)
                with self.assertRaises(ValueError):
                    current_retry_snapshot(project)

    def test_rejects_duplicate_failed_ids_and_malformed_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            partial_project(project)
            report = json.loads((project / "pdfs" / "download_report.json").read_text())
            report.update(failed=2, failed_papers=[{"id": "failed-1"}, {"id": "FAILED-1"}])
            write_json(project / "pdfs" / "download_report.json", report)
            state = load_workflow_state(project); state["stages"]["retrieval"]["counts"]["failed"] = 2
            write_json(project / "workflow_state.json", state)
            with self.assertRaises(ValueError):
                current_retry_snapshot(project)
            (project / "filtered" / "included_papers.jsonl").write_text('{"id":"ok"}\nnot-json\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                current_retry_snapshot(project)

    def test_rejects_invalid_report_counts(self):
        for value in (True, "1", 1.0, -1):
            for key in ("success", "failed"):
                with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as tmp:
                    project = Path(tmp); partial_project(project)
                    report = json.loads((project / "pdfs" / "download_report.json").read_text()); report[key] = value
                    write_json(project / "pdfs" / "download_report.json", report)
                    with self.assertRaises(ValueError): current_retry_snapshot(project)

    def test_ledger_classification_must_exactly_match_report(self):
        cases = [
            (0, 1, "partial", None, False),
            (1, 1, "failed", "Action produced no successful outputs (1 failed).", False),
            (0, 1, "failed", "Action produced no successful outputs (1 failed). forged", False),
            (1, 1, "partial", None, True),
            (0, 1, "failed", "Action produced no successful outputs (1 failed).", True),
        ]
        for success, failed, status, error, accepted in cases:
            with self.subTest(success=success, status=status, error=error), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp); partial_project(project)
                write_json(project / "pdfs" / "download_report.json", {"success": success, "failed": failed, "downloaded": [{"id": "ok-1"}][:success], "failed_papers": [{"id": "failed-1"}]})
                state = load_workflow_state(project); stage = state["stages"]["retrieval"]
                stage.update(status=status, error=error, counts={"succeeded": success, "failed": failed}, stale=False)
                write_json(project / "workflow_state.json", state)
                if accepted: self.assertEqual(current_retry_snapshot(project).ledger["status"], status)
                else:
                    with self.assertRaises(ValueError): current_retry_snapshot(project)

    def test_rejects_symlinks_at_every_authoritative_boundary(self):
        boundaries = ("project", "ledger", "pdfs", "report", "filtered", "included")
        for boundary in boundaries:
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp); project = base / "project"; project.mkdir(); partial_project(project)
                external = base / "external"; external.mkdir()
                if boundary == "project":
                    link = base / "linked-project"; link.symlink_to(project, target_is_directory=True); target = link
                else:
                    target = project
                    paths = {"ledger": project / "workflow_state.json", "pdfs": project / "pdfs", "report": project / "pdfs" / "download_report.json", "filtered": project / "filtered", "included": project / "filtered" / "included_papers.jsonl"}
                    path = paths[boundary]
                    if path.is_dir():
                        replacement = external / boundary; replacement.mkdir();
                        for child in path.iterdir(): (replacement / child.name).write_bytes(child.read_bytes())
                        for child in path.iterdir(): child.unlink()
                        path.rmdir(); path.symlink_to(replacement, target_is_directory=True)
                    else:
                        replacement = external / path.name; replacement.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(replacement)
                with self.assertRaisesRegex(ValueError, "^Authoritative retry facts are unavailable$"):
                    current_retry_snapshot(target)

    def test_safe_display_candidates_fall_through_before_generic(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            partial_project(project, failed_row={"id": "failed-1", "title": "/Users/private/paper.pdf", "failure_class": "C:\\private\\error", "error": "paywall"})
            item = current_retry_snapshot(project).items[0]
        self.assertEqual(item.label, "failed-1"); self.assertEqual(item.failure_class, "paywall")

    def test_snapshot_ledger_is_detached_from_disk_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp); partial_project(project)
            snapshot = current_retry_snapshot(project)
            with self.assertRaises(TypeError): snapshot.ledger["status"] = "completed"
            self.assertEqual(current_retry_snapshot(project).ledger["status"], "partial")


class RetryMergeFactsTests(unittest.TestCase):
    def fixture(self, selected_names=("f2", "f1"), outcomes=(False, True)):
        project = Path("/nonexistent/reviewpilot-project")
        rows = [
            {"id": "ok", "legacy": {"keep": [1]}},
            {"id": "f1", "pdf_downloaded": False, "pdf_failure_class": "paywall", "legacy": {"keep": [2]},
                "retry_id": "source-owned", "_retry_note": "source-owned", "staging_label": "source-owned",
                "title": "f1 title", "authors": ["A"], "custom": {"nested": "f1"}},
            {"id": "f2", "pdf_downloaded": False, "pdf_failure_class": "network", "legacy": {"keep": [3]},
                "retry_id": "source-owned", "_retry_note": "source-owned", "staging_label": "source-owned",
                "title": "f2 title", "authors": ["A"], "custom": {"nested": "f2"}},
        ]
        report = {
            "success": 1, "failed": 2,
            "downloaded": [{"id": "ok", "path": "/authoritative/original.pdf", "legacy": {"keep": [1]}}],
            "failed_papers": [
                {"id": "f1", "failure_class": "paywall", "legacy": {"keep": [2]}},
                {"id": "f2", "failure_class": "network", "legacy": {"keep": [3]}},
            ],
            "pdf_count": 1, "attempted": 3,
        }
        ledger = {"attempt": 2, "status": "partial", "counts": {"succeeded": 1, "failed": 2},
            "error": None, "stale": False}
        revision = retrieval_report_revision(report, ledger)
        ids = {name: stable_retry_id({"id": name}) for name in ("f1", "f2")}
        items = {
            "f1": RetryItem(ids["f1"], "f1", "paywall", 0, 1),
            "f2": RetryItem(ids["f2"], "f2", "network", 1, 2),
        }
        snapshot = RetrySnapshot(revision, report, tuple(rows), ledger, (items["f1"], items["f2"]))
        selected_ids = tuple(ids[name] for name in selected_names)
        preparation = RetryPreparation(snapshot, selected_ids, tuple(items[name] for name in selected_names),
            tuple(rows[items[name].included_index] for name in selected_names), project)
        staging_root = project / ".retrieval_retry_staging_case"
        staging_project = staging_root / "retry"
        updated, downloaded, failed, pdfs = [], [], [], []
        for index, (name, succeeds) in enumerate(zip(selected_names, outcomes), 1):
            row = {"id": name, "legacy": {"keep": [items[name].included_index + 1]},
                "retry_id": "source-owned", "_retry_note": "source-owned", "staging_label": "source-owned",
                "title": f"{name} title", "authors": ["A"], "custom": {"nested": name}}
            if succeeds:
                source = staging_project / "pdfs" / f"row{index}.pdf"
                row.update(pdf_downloaded=True, pdf_path=str(source), pdf_method="direct",
                    retrieval_status="downloaded")
                downloaded.append({"id": name, "title": f"{name} title", "authors": ["A"],
                    "custom": {"nested": name}, "path": str(source), "pdf_path": str(source),
                    "pdf_downloaded": True, "retrieval_status": "downloaded"})
                pdfs.append(StagedRetryPdf(ids[name], source))
            else:
                failure_class = "network" if name == "f2" else "paywall"
                status = "unavailable" if name == "f2" else "subscribed_unavailable"
                row.update(pdf_downloaded=False, pdf_failure_class=failure_class,
                    pdf_failure_detail="raw-row", pdf_failure_classes=[failure_class], pdf_error="raw-row-error",
                    retrieval_status=status, web_search_fallback_pending=True, web_search_fallback_eligible=True)
                failed.append({"id": name, "title": f"{name} title", "authors": ["A"],
                    "custom": {"nested": name}, "failure_class": failure_class, "failure_detail": "raw-report",
                    "failure_classes": [failure_class], "error": "raw-report-error", "pdf_failure_class": failure_class,
                    "pdf_failure_detail": "alias",
                    "pdf_failure_classes": [failure_class], "pdf_error": "alias-error", "pdf_downloaded": False,
                    "retrieval_status": status, "web_search_fallback_pending": True,
                    "web_search_fallback_eligible": True})
            updated.append(row)
        staged_report = {"success": sum(outcomes), "failed": len(outcomes) - sum(outcomes),
            "downloaded": downloaded, "failed_papers": failed,
            "pdf_count": sum(outcomes), "attempted": len(outcomes)}
        staged_report["subscribed_papers"] = [row for row in failed if row["retrieval_status"] == "subscribed_unavailable"]
        staged_report["unavailable_papers"] = [row for row in failed if row["retrieval_status"] == "unavailable"]
        staged_report["web_search_fallback_candidates"] = list(failed)
        outcome = StagedRetryOutcome(staging_root=staging_root, staging_project_path=staging_project,
            report_revision=revision, selected_ids=selected_ids, updated_rows=tuple(updated),
            report=staged_report, successful_pdfs=tuple(pdfs))
        return preparation, outcome

    def test_merge_preserves_unselected_facts_and_replaces_selected_in_authoritative_order(self):
        preparation, outcome = self.fixture(("f2",), (False,))
        facts = merge_staged_retry_facts(preparation, outcome)
        report, included = facts.mutable_copies()
        self.assertEqual(report["downloaded"], preparation.snapshot.report["downloaded"])
        self.assertEqual(report["failed_papers"][0], preparation.snapshot.report["failed_papers"][0])
        self.assertEqual(included[:2], list(preparation.snapshot.included[:2]))
        self.assertEqual([row["id"] for row in report["failed_papers"]], ["f1", "f2"])
        self.assertFalse(any(key in report["failed_papers"][1] for key in (
            "failure_detail", "failure_classes", "error", "pdf_failure_detail", "pdf_failure_classes", "pdf_error", "retry_id", "staging_note")))

    def test_merge_success_uses_revision_bound_destination_and_strips_staging_diagnostics(self):
        preparation, outcome = self.fixture()
        facts = merge_staged_retry_facts(preparation, outcome)
        report, included = facts.mutable_copies()
        retry_id = preparation.selected_ids[1]
        destination = preparation._project_identity / "pdfs" / f"retry-{preparation.snapshot.report_revision}-{retry_id}.pdf"
        self.assertEqual(facts.planned_pdfs[0].destination_path, destination)
        self.assertEqual(report["downloaded"][-1]["path"], str(destination))
        self.assertEqual(report["downloaded"][-1]["pdf_path"], str(destination))
        self.assertEqual(included[1]["pdf_path"], str(destination))
        serialized = json.dumps([report, included], sort_keys=True)
        self.assertNotIn(str(outcome.staging_root), serialized)
        self.assertNotIn("raw-row", serialized); self.assertNotIn("raw-report", serialized)
        self.assertEqual(included[1]["retry_id"], "source-owned")
        self.assertEqual(included[1]["_retry_note"], "source-owned")
        self.assertEqual(included[1]["staging_label"], "source-owned")

    def test_merge_recomputes_total_counts_classification_and_aggregate_status(self):
        cases = ((('f2', 'f1'), (True, True), "completed", 3, 0),
            (("f2",), (True,), "partial", 2, 1),
            (("f2", "f1"), (False, False), "partial", 1, 2))
        for selected, outcomes, status, success, failed in cases:
            with self.subTest(selected=selected, outcomes=outcomes):
                facts = merge_staged_retry_facts(*self.fixture(selected, outcomes))
                report, _ = facts.mutable_copies()
                self.assertEqual((facts.status, facts.counts["succeeded"], facts.counts["failed"]), (status, success, failed))
                self.assertEqual((report["success"], report["failed"], report["pdf_count"], report["attempted"]),
                    (success, failed, success, success + failed))
                self.assertEqual(len(report["downloaded"]), success); self.assertEqual(len(report["failed_papers"]), failed)
                self.assertEqual(report["web_search_fallback_candidates"], report["failed_papers"])

    def test_merge_all_repeat_fail_without_prior_success_is_failed(self):
        preparation, outcome = self.fixture(("f2", "f1"), (False, False))
        report = {**preparation.snapshot.report, "success": 0, "downloaded": [], "pdf_count": 0, "attempted": 2}
        ledger = {**preparation.snapshot.ledger, "status": "failed", "counts": {"succeeded": 0, "failed": 2},
            "error": "Action produced no successful outputs (2 failed)."}
        revision = retrieval_report_revision(report, ledger)
        snapshot = RetrySnapshot(revision, report, preparation.snapshot.included, ledger, preparation.snapshot.items)
        preparation = RetryPreparation(snapshot, preparation.selected_ids, preparation.items,
            preparation.included_rows, preparation._project_identity)
        facts = merge_staged_retry_facts(preparation, replace(outcome, report_revision=revision))
        self.assertEqual((facts.status, facts.counts["succeeded"], facts.counts["failed"]), ("failed", 0, 2))

    def test_merge_is_deterministic_revision_bound_and_recursively_immutable(self):
        preparation, outcome = self.fixture(("f2",), (True,))
        original_report = json.loads(json.dumps(outcome.report)); original_rows = json.loads(json.dumps(outcome.updated_rows))
        first = merge_staged_retry_facts(preparation, outcome)
        second = merge_staged_retry_facts(preparation, outcome)
        self.assertEqual(outcome.report, original_report); self.assertEqual(list(outcome.updated_rows), original_rows)
        self.assertEqual(first.planned_pdfs, second.planned_pdfs)
        with self.assertRaises(TypeError): first.report["success"] = 9
        with self.assertRaises(TypeError): first.included[0]["id"] = "changed"
        mutable_report, mutable_included = first.mutable_copies(); mutable_report["success"] = 9; mutable_included[0]["id"] = "changed"
        self.assertNotEqual(first.report["success"], 9); self.assertNotEqual(first.included[0]["id"], "changed")
        changed_ledger = {**preparation.snapshot.ledger, "attempt": 3}
        changed = RetrySnapshot(retrieval_report_revision(preparation.snapshot.report, changed_ledger),
            preparation.snapshot.report, preparation.snapshot.included, changed_ledger, preparation.snapshot.items)
        changed_preparation = RetryPreparation(changed, preparation.selected_ids, preparation.items,
            preparation.included_rows, preparation._project_identity)
        with self.assertRaises(ValueError):
            merge_staged_retry_facts(changed_preparation, outcome)
        changed_outcome = replace(outcome, report_revision=changed.report_revision)
        changed_destination = merge_staged_retry_facts(changed_preparation, changed_outcome).planned_pdfs[0].destination_path
        self.assertNotEqual(first.planned_pdfs[0].destination_path, changed_destination)

    def test_merge_rejects_malformed_or_cross_project_contracts_without_filesystem_access(self):
        preparation, outcome = self.fixture()
        mutations = (
            lambda p, o: replace(o, staging_root=o.staging_root.parent / "other"),
            lambda p, o: replace(o, selected_ids=tuple(reversed(o.selected_ids))),
            lambda p, o: replace(o, updated_rows=tuple(reversed(o.updated_rows))),
            lambda p, o: replace(o, report={**o.report, "success": 2}),
            lambda p, o: replace(o, successful_pdfs=o.successful_pdfs + o.successful_pdfs),
            lambda p, o: replace(o, updated_rows=({**o.updated_rows[0], "pdf_downloaded": True}, *o.updated_rows[1:])),
            lambda p, o: replace(o, updated_rows=({**o.updated_rows[0], "title": "mutated"}, *o.updated_rows[1:])),
            lambda p, o: replace(o, updated_rows=({**o.updated_rows[0], "unknown": {"nested": True}}, *o.updated_rows[1:])),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                merge_staged_retry_facts(preparation, mutate(preparation, outcome))

        duplicate_rows = (*preparation.snapshot.included, preparation.snapshot.included[0])
        duplicate_snapshot = RetrySnapshot(preparation.snapshot.report_revision, preparation.snapshot.report,
            duplicate_rows, preparation.snapshot.ledger, preparation.snapshot.items)
        duplicate_preparation = RetryPreparation(duplicate_snapshot, preparation.selected_ids, preparation.items,
            preparation.included_rows, preparation._project_identity)
        with self.assertRaises(ValueError):
            merge_staged_retry_facts(duplicate_preparation, outcome)

    def test_merge_revalidates_canonical_staged_primary_schema_and_classification_lists(self):
        preparation, outcome = self.fixture()
        mutations = []
        success_index = 1
        success_row = dict(outcome.updated_rows[success_index])
        mutations.append(replace(outcome, updated_rows=(outcome.updated_rows[0], {**success_row, "retrieval_status": "unavailable"})))
        mutations.append(replace(outcome, updated_rows=(outcome.updated_rows[0], {**success_row, "pdf_failure_class": "network"})))
        failed_row = dict(outcome.updated_rows[0])
        mutations.append(replace(outcome, updated_rows=({**failed_row, "web_search_fallback_pending": False}, outcome.updated_rows[1])))
        mutations.append(replace(outcome, updated_rows=({**failed_row, "pdf_method": "forged"}, outcome.updated_rows[1])))
        downloaded = [dict(row) for row in outcome.report["downloaded"]]
        mutations.append(replace(outcome, report={**outcome.report,
            "downloaded": [{**downloaded[0], "pdf_path": "/wrong.pdf"}]}))
        failed = [dict(row) for row in outcome.report["failed_papers"]]
        mutations.append(replace(outcome, report={**outcome.report,
            "failed_papers": [{**failed[0], "failure_class": "paywall"}]}))
        mutations.append(replace(outcome, report={**outcome.report,
            "failed_papers": [{**failed[0], "method": "forged"}]}))
        mutations.append(replace(outcome, report={**outcome.report, "unavailable_papers": []}))
        mutations.append(replace(outcome, report={**outcome.report,
            "downloaded": [{**downloaded[0], "staging_private": "unknown"}]}))
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                merge_staged_retry_facts(preparation, mutation)

    def test_merge_requires_all_report_detail_provenance_from_corresponding_selected_row(self):
        preparation, outcome = self.fixture()
        for field, forged in (("title", "forged"), ("authors", ["forged"]), ("custom", {"nested": "forged"})):
            downloaded = [dict(row) for row in outcome.report["downloaded"]]
            downloaded[0][field] = forged
            with self.subTest(kind="success", field=field), self.assertRaises(ValueError):
                merge_staged_retry_facts(preparation, replace(outcome,
                    report={**outcome.report, "downloaded": downloaded}))

            failure = {**outcome.report["failed_papers"][0], field: forged}
            forged_report = {**outcome.report, "failed_papers": [failure],
                "unavailable_papers": [failure], "web_search_fallback_candidates": [failure]}
            with self.subTest(kind="failure", field=field), self.assertRaises(ValueError):
                merge_staged_retry_facts(preparation, replace(outcome, report=forged_report))

    def test_merge_matches_downloader_empty_identity_defaults_and_success_method_aliases(self):
        preparation, outcome = self.fixture()
        downloaded = [{**outcome.report["downloaded"][0], "doi": "", "url": "",
            "method": "direct", "pdf_method": "direct"}]
        allowed = replace(outcome, report={**outcome.report, "downloaded": downloaded})
        merge_staged_retry_facts(preparation, allowed)

        failure = {**outcome.report["failed_papers"][0], "doi": "", "url": ""}
        allowed_failure = replace(outcome, report={**outcome.report, "failed_papers": [failure],
            "unavailable_papers": [failure], "web_search_fallback_candidates": [failure]})
        merge_staged_retry_facts(preparation, allowed_failure)

        for field, value in (("doi", "10.1/forged"), ("url", "https://forged.invalid"),
                ("method", "forged"), ("pdf_method", "forged")):
            forged = [{**outcome.report["downloaded"][0], field: value}]
            with self.subTest(field=field), self.assertRaises(ValueError):
                merge_staged_retry_facts(preparation, replace(outcome,
                    report={**outcome.report, "downloaded": forged}))

        present_preparation, present_outcome = self.fixture(("f2",), (True,))
        present_row = {**present_preparation.included_rows[0], "doi": None}
        included = list(present_preparation.snapshot.included)
        included[present_preparation.items[0].included_index] = present_row
        present_snapshot = replace(present_preparation.snapshot, included=tuple(included))
        present_preparation = replace(present_preparation, snapshot=present_snapshot, included_rows=(present_row,))
        present_outcome = replace(present_outcome, updated_rows=({**present_outcome.updated_rows[0], "doi": None},))
        matching = [{**present_outcome.report["downloaded"][0], "doi": None}]
        merge_staged_retry_facts(present_preparation,
            replace(present_outcome, report={**present_outcome.report, "downloaded": matching}))
        wrong_empty = [{**present_outcome.report["downloaded"][0], "doi": ""}]
        with self.assertRaises(ValueError):
            merge_staged_retry_facts(present_preparation,
                replace(present_outcome, report={**present_outcome.report, "downloaded": wrong_empty}))

    def test_merge_requires_nonempty_textual_success_methods_even_when_row_and_aliases_match(self):
        preparation, outcome = self.fixture()
        for value in ({}, [], True, 1, None, "", "   "):
            rows = (outcome.updated_rows[0], {**outcome.updated_rows[1], "pdf_method": value})
            downloaded = [{**outcome.report["downloaded"][0], "method": value, "pdf_method": value}]
            malicious = replace(outcome, updated_rows=rows,
                report={**outcome.report, "downloaded": downloaded})
            with self.subTest(value=value), self.assertRaises(ValueError):
                merge_staged_retry_facts(preparation, malicious)

    def test_merge_requires_strict_staged_report_pdf_count_and_attempted(self):
        preparation, outcome = self.fixture()
        for key, values in (("pdf_count", (None, True, "1", -1, 999)),
                ("attempted", (None, True, "2", -1, 999))):
            for value in values:
                report = dict(outcome.report)
                if value is None: report.pop(key)
                else: report[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    merge_staged_retry_facts(preparation, replace(outcome, report=report))

    def test_merge_revalidates_current_snapshot_items_and_preserves_legacy_report_metadata_contract(self):
        preparation, outcome = self.fixture(("f2",), (True,))
        report = {**preparation.snapshot.report, "pdf_count": 999, "attempted": 999}
        revision = retrieval_report_revision(report, preparation.snapshot.ledger)
        snapshot = RetrySnapshot(revision, report, preparation.snapshot.included,
            preparation.snapshot.ledger, preparation.snapshot.items)
        preparation = RetryPreparation(snapshot, preparation.selected_ids, preparation.items,
            preparation.included_rows, preparation._project_identity)
        outcome = replace(outcome, report_revision=revision)
        merged = merge_staged_retry_facts(preparation, outcome)
        merged_report, _ = merged.mutable_copies()
        self.assertEqual((merged_report["pdf_count"], merged_report["attempted"]), (2, 3))

        bad_item = replace(preparation.snapshot.items[0], label="forged")
        bad_snapshot = replace(snapshot, items=(bad_item, *snapshot.items[1:]))
        bad_preparation = replace(preparation, snapshot=bad_snapshot)
        with self.assertRaises(ValueError): merge_staged_retry_facts(bad_preparation, outcome)

        bad_failures = [dict(row) for row in report["failed_papers"]]
        bad_failures[0]["retrieval_status"] = "unavailable"
        bad_report = {**report, "failed_papers": bad_failures}
        bad_revision = retrieval_report_revision(bad_report, snapshot.ledger)
        bad_snapshot = replace(snapshot, report_revision=bad_revision, report=bad_report)
        with self.assertRaises(ValueError):
            merge_staged_retry_facts(replace(preparation, snapshot=bad_snapshot),
                replace(outcome, report_revision=bad_revision))

        stale_ledger = {**snapshot.ledger, "stale": True}
        stale_revision = retrieval_report_revision(snapshot.report, stale_ledger)
        stale_snapshot = replace(snapshot, report_revision=stale_revision, ledger=stale_ledger)
        with self.assertRaises(ValueError):
            merge_staged_retry_facts(replace(preparation, snapshot=stale_snapshot),
                replace(outcome, report_revision=stale_revision))

    def test_merge_is_pure_when_all_path_filesystem_methods_raise(self):
        preparation, outcome = self.fixture()
        with patch.object(Path, "exists", side_effect=AssertionError("filesystem access")), \
                patch.object(Path, "is_file", side_effect=AssertionError("filesystem access")), \
                patch.object(Path, "resolve", side_effect=AssertionError("filesystem access")), \
                patch.object(Path, "open", side_effect=AssertionError("filesystem access")), \
                patch.object(Path, "stat", side_effect=AssertionError("filesystem access")):
            facts = merge_staged_retry_facts(preparation, outcome)
        self.assertEqual(facts.report_revision, preparation.snapshot.report_revision)


class RetryPublicationPlanTests(unittest.TestCase):
    def fixture(self, outcomes=(True, False)):
        temporary = tempfile.TemporaryDirectory()
        project = Path(temporary.name).resolve()
        two_failure_project(project)
        (project / "pdfs" / "original.pdf").write_bytes(b"%PDF-1.7\noriginal")
        preparation = confirmed_preparation(project)
        staging = preparation._project_identity / ".retrieval_retry_staging_publish"
        outcome = run_retry_staging(project, preparation, staging, fake_download(list(outcomes)))
        merged = merge_staged_retry_facts(preparation, outcome)
        return temporary, project, preparation, outcome, merged

    def test_plan_freezes_ordered_source_integrity_and_changes_nothing(self):
        temporary, project, preparation, outcome, merged = self.fixture((True, True))
        with temporary:
            before = authoritative_fingerprint(project)
            plan = prepare_retry_publication(project, preparation, outcome, merged)
            self.assertEqual(plan.report_revision, preparation.snapshot.report_revision)
            self.assertEqual(plan.merged_facts, merged)
            self.assertEqual(tuple(pdf.retry_id for pdf in plan.pdfs), preparation.selected_ids)
            for planned, source in zip(plan.pdfs, outcome.successful_pdfs):
                payload = source.source_path.read_bytes()
                self.assertEqual(planned.source_path, source.source_path)
                self.assertEqual(planned.source_size, len(payload))
                self.assertEqual(planned.source_sha256, hashlib.sha256(payload).hexdigest())
            with self.assertRaises(Exception): plan.pdfs[0].source_size = 0
            self.assertEqual(authoritative_fingerprint(project), before)

    def test_partial_and_all_failure_plans_are_valid(self):
        for outcomes, expected in (((True, False), 1), ((False, False), 0)):
            temporary, project, preparation, outcome, merged = self.fixture(outcomes)
            with temporary:
                plan = prepare_retry_publication(project, preparation, outcome, merged)
                self.assertEqual(len(plan.pdfs), expected)

    def test_rejects_current_authority_drift_even_when_revision_is_unchanged(self):
        mutations = (
            lambda project: write_jsonl(project / "filtered" / "included_papers.jsonl",
                [{"id": "ok-1", "new": True}, {"id": "failed-1", "title": "Failed 1", "doi": "", "url": ""},
                 {"id": "failed-2", "title": "Failed 2", "doi": "", "url": ""}]),
            lambda project: write_json(project / "pdfs" / "download_report.json",
                {**json.loads((project / "pdfs" / "download_report.json").read_text()), "new": True}),
            lambda project: self._mutate_retrieval_ledger(project),
        )
        for mutate in mutations:
            temporary, project, preparation, outcome, merged = self.fixture()
            with temporary, self.subTest(mutate=mutate):
                before = authoritative_fingerprint(project); mutate(project); changed = authoritative_fingerprint(project)
                self.assertNotEqual(before, changed)
                with self.assertRaisesRegex(ValueError, r"^Retry publication preparation failed$"):
                    prepare_retry_publication(project, preparation, outcome, merged)
                self.assertEqual(authoritative_fingerprint(project), changed)

    def test_relative_project_cannot_be_rebound_by_mapping_materialization_chdir(self):
        class ChdirOnFirstItems(dict):
            def __init__(self, value, destination):
                super().__init__(value)
                self.destination = destination
                self.reads = 0

            def items(self):
                self.reads += 1
                if self.reads == 1:
                    os.chdir(self.destination)
                return super().items()

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            first_root = base / "A"
            project = first_root / "project"
            project.mkdir(parents=True)
            two_failure_project(project)
            (project / "pdfs" / "original.pdf").write_bytes(b"%PDF-1.7\noriginal")
            preparation = confirmed_preparation(project)
            staging = project / ".retrieval_retry_staging_relative"
            outcome = run_retry_staging(project, preparation, staging, fake_download([True, False]))
            merged = merge_staged_retry_facts(preparation, outcome)

            second_root = base / "B"
            second_root.mkdir()
            alternate = second_root / "project"
            shutil.copytree(project, alternate)
            report_path = project / "pdfs" / "download_report.json"
            write_json(report_path, {**json.loads(report_path.read_text()), "drift": True})
            project_before = authoritative_fingerprint(project)
            alternate_before = authoritative_fingerprint(alternate)
            backing = ChdirOnFirstItems(dict(merged.report), second_root)
            forged_merged = replace(merged, report=MappingProxyType(backing))

            original_cwd = Path.cwd()
            try:
                os.chdir(first_root)
                with self.assertRaisesRegex(ValueError, r"^Retry publication preparation failed$"):
                    prepare_retry_publication(Path("project"), preparation, outcome, forged_merged)
            finally:
                os.chdir(original_cwd)

            self.assertEqual(backing.reads, 1)
            self.assertEqual(authoritative_fingerprint(project), project_before)
            self.assertEqual(authoritative_fingerprint(alternate), alternate_before)

    def test_accepts_direct_relative_project_but_rejects_project_symlink(self):
        temporary, project, preparation, outcome, merged = self.fixture()
        with temporary:
            link = project / "linked-project"
            link.symlink_to(project, target_is_directory=True)
            before = authoritative_fingerprint(project)
            original_cwd = Path.cwd()
            try:
                os.chdir(project.parent)
                plan = prepare_retry_publication(Path(project.name), preparation, outcome, merged)
                self.assertEqual(plan.report_revision, preparation.snapshot.report_revision)
                with self.assertRaisesRegex(ValueError, r"^Retry publication preparation failed$"):
                    prepare_retry_publication(link, preparation, outcome, merged)
            finally:
                os.chdir(original_cwd)
            self.assertEqual(authoritative_fingerprint(project), before)

    def test_rejects_forged_or_cross_bound_inputs(self):
        temporary, project, preparation, outcome, merged = self.fixture()
        with temporary:
            forged_report = {**merged.report, "success": 99}
            cases = (
                replace(merged, report=forged_report),
                replace(merged, status="completed"),
                replace(merged, counts={"succeeded": 99, "failed": 0}),
                replace(merged, planned_pdfs=()),
                replace(merged, report_revision="0" * 64),
            )
            for forged in cases:
                with self.subTest(forged=forged), self.assertRaises(ValueError):
                    prepare_retry_publication(project, preparation, outcome, forged)
            with self.assertRaises(ValueError):
                prepare_retry_publication(project, replace(preparation, _project_identity=Path("/tmp/other")), outcome, merged)

    def test_rejects_polymorphic_security_values_before_comparison(self):
        class EvilStr(str):
            def __eq__(self, other): return True
            def __ne__(self, other): return False

            __hash__ = str.__hash__

        class EvilInt(int):
            def __eq__(self, other): return True
            def __ne__(self, other): return False

            __hash__ = int.__hash__

        temporary, project, preparation, outcome, merged = self.fixture()
        with temporary:
            source = outcome.successful_pdfs[0]
            forged_snapshot = replace(preparation.snapshot, report_revision=EvilStr("0" * 64))
            with self.assertRaises(ValueError):
                prepare_retry_publication(project, replace(preparation, snapshot=forged_snapshot), outcome, merged)
            with self.assertRaises(ValueError):
                prepare_retry_publication(project,
                    replace(preparation, selected_ids=tuple(EvilStr(value) for value in preparation.selected_ids)),
                    outcome, merged)
            for forged_outcome in (
                replace(outcome, report_revision=EvilStr("0" * 64)),
                replace(outcome, successful_pdfs=(replace(source, source_size=EvilInt(0)),)),
                replace(outcome, successful_pdfs=(replace(source, source_sha256=EvilStr("0" * 64)),)),
            ):
                with self.subTest(forged_outcome=forged_outcome), self.assertRaises(ValueError):
                    prepare_retry_publication(project, preparation, forged_outcome, merged)

            forged_nested_report = replace(merged, report={**merged.report, "success": EvilInt(99)})
            with self.assertRaises(ValueError):
                prepare_retry_publication(project, preparation, outcome, forged_nested_report)

    def test_rejects_forged_top_level_mapping_proxy_backing_before_comparison(self):
        class AlwaysEqualDict(dict):
            def __eq__(self, other): return True
            def __ne__(self, other): return False

        temporary, project, preparation, outcome, merged = self.fixture()
        with temporary:
            forged_report = MappingProxyType(AlwaysEqualDict({**merged.report, "success": 99}))
            with self.assertRaisesRegex(ValueError, r"^Retry publication preparation failed$"):
                prepare_retry_publication(project, preparation, outcome,
                    replace(merged, report=forged_report))

    def test_rejects_forged_nested_mapping_proxy_backing_before_comparison(self):
        class AlwaysEqualDict(dict):
            def __eq__(self, other): return True
            def __ne__(self, other): return False

        temporary, project, preparation, outcome, merged = self.fixture()
        with temporary:
            downloaded = list(merged.report["downloaded"])
            downloaded[0] = MappingProxyType(AlwaysEqualDict({**downloaded[0], "title": "forged"}))
            nested_report = MappingProxyType({**merged.report, "downloaded": tuple(downloaded)})
            with self.assertRaisesRegex(ValueError, r"^Retry publication preparation failed$"):
                prepare_retry_publication(project, preparation, outcome,
                    replace(merged, report=nested_report))

    def test_rejects_first_stateful_mapping_snapshot_even_when_later_values_are_valid(self):
        class StatefulDict(dict):
            def __init__(self, valid, forged):
                super().__init__(valid)
                self.forged = forged
                self.reads = 0

            def items(self):
                self.reads += 1
                if self.reads == 1:
                    return self.forged.items()
                return super().items()

        temporary, project, preparation, outcome, merged = self.fixture()
        with temporary:
            backing = StatefulDict(dict(merged.report), {**merged.report, "success": 99})
            with self.assertRaisesRegex(ValueError, r"^Retry publication preparation failed$"):
                prepare_retry_publication(project, preparation, outcome,
                    replace(merged, report=MappingProxyType(backing)))
            self.assertEqual(backing.reads, 1)

    def test_masks_mapping_backing_exceptions_without_leaking_paths(self):
        leaked_path = "/private/retry/secret.pdf"

        class RaisingItemsDict(dict):
            def items(self):
                raise Exception(leaked_path)

        temporary, project, preparation, outcome, merged = self.fixture()
        with temporary:
            forged = replace(merged, report=MappingProxyType(RaisingItemsDict(merged.report)))
            with self.assertRaisesRegex(ValueError, r"^Retry publication preparation failed$") as raised:
                prepare_retry_publication(project, preparation, outcome, forged)
            self.assertNotIn(leaked_path, str(raised.exception))

    def test_rejects_dataclass_and_concrete_path_subclasses_at_publication_boundary(self):
        class PreparationSubclass(RetryPreparation): pass
        class SnapshotSubclass(RetrySnapshot): pass
        class OutcomeSubclass(StagedRetryOutcome): pass
        class StagedPdfSubclass(StagedRetryPdf): pass
        class MergedFactsSubclass(RetryMergedFacts): pass
        class PlannedPdfSubclass(RetryPlannedPdf): pass
        class ConcretePathSubclass(type(Path.cwd())): pass

        temporary, project, preparation, outcome, merged = self.fixture()
        with temporary:
            prep_subclass = PreparationSubclass(preparation.snapshot, preparation.selected_ids,
                preparation.items, preparation.included_rows, preparation._project_identity)
            snapshot_subclass = SnapshotSubclass(preparation.snapshot.report_revision,
                preparation.snapshot.report, preparation.snapshot.included, preparation.snapshot.ledger,
                preparation.snapshot.items)
            outcome_subclass = OutcomeSubclass(outcome.staging_root, outcome.staging_project_path,
                outcome.report_revision, outcome.selected_ids, outcome.updated_rows, outcome.report,
                outcome.successful_pdfs)
            staged_pdf = outcome.successful_pdfs[0]
            staged_pdf_subclass = StagedPdfSubclass(staged_pdf.retry_id, staged_pdf.source_path,
                staged_pdf.source_size, staged_pdf.source_sha256)
            merged_subclass = MergedFactsSubclass(merged.report_revision, merged.report, merged.included,
                merged.status, merged.counts, merged.planned_pdfs)
            planned_pdf = merged.planned_pdfs[0]
            planned_pdf_subclass = PlannedPdfSubclass(planned_pdf.retry_id, planned_pdf.source_path,
                planned_pdf.destination_path)
            path_subclass = ConcretePathSubclass(str(outcome.staging_root))
            cases = (
                (prep_subclass, outcome, merged),
                (replace(preparation, snapshot=snapshot_subclass), outcome, merged),
                (preparation, outcome_subclass, merged),
                (preparation, replace(outcome, successful_pdfs=(staged_pdf_subclass,)), merged),
                (preparation, outcome, merged_subclass),
                (preparation, outcome, replace(merged, planned_pdfs=(planned_pdf_subclass,))),
                (preparation, replace(outcome, staging_root=path_subclass), merged),
            )
            for forged_preparation, forged_outcome, forged_merged in cases:
                with self.subTest(case=(type(forged_preparation), type(forged_outcome), type(forged_merged))), \
                        self.assertRaises(ValueError):
                    prepare_retry_publication(project, forged_preparation, forged_outcome, forged_merged)

    def test_rejects_source_mutation_invalid_pdf_aliases_and_boundary_replacement(self):
        mutations = (
            lambda project, outcome, merged: outcome.successful_pdfs[0].source_path.write_bytes(b"%PDF-1.7\nchanged"),
            lambda project, outcome, merged: outcome.successful_pdfs[0].source_path.write_bytes(b"not a pdf"),
            lambda project, outcome, merged: outcome.successful_pdfs[0].source_path.write_bytes(b""),
            lambda project, outcome, merged: self._replace_with_symlink(outcome.successful_pdfs[0].source_path,
                project / "pdfs" / "original.pdf"),
            lambda project, outcome, merged: self._replace_directory_with_symlink(
                outcome.staging_project_path / "pdfs", project / "pdfs"),
        )
        for mutate in mutations:
            temporary, project, preparation, outcome, merged = self.fixture()
            with temporary, self.subTest(mutate=mutate):
                mutate(project, outcome, merged)
                before = authoritative_fingerprint(project)
                with self.assertRaises(ValueError): prepare_retry_publication(project, preparation, outcome, merged)
                self.assertEqual(authoritative_fingerprint(project), before)

    def test_rejects_every_destination_collision_kind_including_broken_symlink(self):
        makers = (
            lambda path: path.write_bytes(b"occupied"),
            lambda path: path.mkdir(),
            lambda path: path.symlink_to(path.parent / "original.pdf"),
            lambda path: path.symlink_to(path.parent / "missing.pdf"),
        )
        for make in makers:
            temporary, project, preparation, outcome, merged = self.fixture()
            with temporary, self.subTest(make=make):
                destination = merged.planned_pdfs[0].destination_path; make(destination)
                before = authoritative_fingerprint(project)
                with self.assertRaises(ValueError): prepare_retry_publication(project, preparation, outcome, merged)
                self.assertEqual(authoritative_fingerprint(project), before)

    @staticmethod
    def _replace_with_symlink(path, target):
        path.unlink(); path.symlink_to(target)

    @staticmethod
    def _replace_directory_with_symlink(path, target):
        for child in path.iterdir(): child.unlink()
        path.rmdir(); path.symlink_to(target, target_is_directory=True)

    @staticmethod
    def _mutate_retrieval_ledger(project):
        state = json.loads((project / "workflow_state.json").read_text())
        state["stages"]["retrieval"]["new"] = True
        write_json(project / "workflow_state.json", state)


if __name__ == "__main__":
    unittest.main()
