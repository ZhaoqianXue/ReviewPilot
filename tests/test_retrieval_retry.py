import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from reviewpilot_core.sub_agent_contracts import DownloadAgentContract
from reviewpilot_core.retrieval_retry import (
    ConfirmationRequired,
    InvalidRetryRequest,
    RevisionConflict,
    current_retry_snapshot,
    prepare_retry_request,
    run_retry_staging,
    retrieval_report_revision,
    stable_retry_id,
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
    write_jsonl(project / "filtered" / "included_papers.jsonl", [{"id": "ok-1"}, {"id": "failed-1"}, {"id": "failed-2"}])
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
                downloaded.append({"path": str(pdf), "title": row.get("title", "")})
            else:
                row.update(pdf_downloaded=False, retrieval_status="unavailable", pdf_failure_class="download_failed")
                row.pop("pdf_path", None)
                failed.append({"id": row["id"], "failure_class": "download_failed"})
        write_jsonl(included_path, rows)
        report = {"success": sum(outcomes), "failed": len(outcomes) - sum(outcomes),
                  "downloaded": downloaded, "failed_papers": failed}
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
            self.assertTrue(outcome.staging_path.exists())
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


if __name__ == "__main__":
    unittest.main()
