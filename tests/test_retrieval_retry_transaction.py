import base64
from copy import deepcopy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

from reviewpilot_core.atomic_files import atomic_write_json, atomic_write_jsonl
from reviewpilot_core.retrieval_retry import (
    RetryItem, RetryMergedFacts, RetryPreparation, RetryPublicationPlan, RetrySnapshot, current_retry_snapshot,
    merge_staged_retry_facts, prepare_retry_publication, prepare_retry_request,
    run_retry_staging,
)
from reviewpilot_core.retrieval_retry_transaction import (
    PENDING_RETRY_FILE,
    abandon_retry_transaction,
    abort_retry_transaction,
    begin_retry_transaction,
    reconcile_retry_transaction,
    record_retry_transaction_target,
)
from reviewpilot_core.workflow_state import complete_action, load_workflow_state, save_workflow_state, start_action, initialize_workflow_state


def retryable_project(project: Path) -> None:
    atomic_write_jsonl(project / "filtered" / "included_papers.jsonl", [
        {"id": "ok", "pdf_path": str(project / "pdfs" / "original.pdf")},
        {"id": "failed", "title": "Failed"},
    ])
    (project / "pdfs").mkdir(parents=True, exist_ok=True)
    (project / "pdfs" / "original.pdf").write_bytes(b"%PDF-old")
    atomic_write_json(project / "pdfs" / "download_report.json", {
        "success": 1, "failed": 1,
        "downloaded": [{"id": "ok", "path": str(project / "pdfs" / "original.pdf")}],
        "failed_papers": [{"id": "failed", "title": "Failed", "failure_class": "network"}],
    })
    initialize_workflow_state(project)
    start_action(project, "collect"); complete_action(project, "collect", {"total": 0, "platform_stats": {"x": 0}, "platform_errors": {}})
    start_action(project, "screen"); complete_action(project, "screen", {})
    start_action(project, "download-pdfs"); complete_action(project, "download-pdfs", {"success": 1, "failed": 1})


def preparation(project: Path):
    snapshot = current_retry_snapshot(project)
    ids = [item.retry_id for item in snapshot.items]
    return prepare_retry_request(project, {"failed_ids": ids, "report_revision": snapshot.report_revision,
        "retry_confirmation": {"expected_report_revision": snapshot.report_revision, "failed_ids": ids}})


def publication(project: Path, prepared: RetryPreparation, staging_name: str, *, succeeds: bool = True):
    def download(root: Path, project_id: str):
        staged = root / project_id
        rows = [json.loads(line) for line in (staged / "filtered" / "included_papers.jsonl").read_text().splitlines()]
        pdf = staged / "pdfs" / "retry.pdf"
        if succeeds:
            pdf.write_bytes(b"%PDF-1.7\nretry")
            rows[0].update(pdf_downloaded=True, pdf_path=str(pdf), retrieval_status="downloaded")
            downloaded = [{"title": rows[0].get("title", ""), "path": str(pdf)}]; failed = []
        else:
            rows[0].update(pdf_downloaded=False, retrieval_status="unavailable", pdf_failure_class="download_failed")
            downloaded = []; failed = [{"id": rows[0]["id"], "title": rows[0].get("title", ""),
                "doi": "", "url": "", "failure_class": "download_failed"}]
        atomic_write_jsonl(staged / "filtered" / "included_papers.jsonl", rows)
        atomic_write_json(staged / "pdfs" / "download_report.json", {
            "success": int(succeeds), "failed": int(not succeeds), "downloaded": downloaded,
            "failed_papers": failed, "pdf_count": int(succeeds), "attempted": 1,
        })
        return {"success": int(succeeds), "failed": int(not succeeds),
            "stats": {"success": int(succeeds), "failed": int(not succeeds)}}
    outcome = run_retry_staging(project, prepared, project / staging_name, download)
    merged = merge_staged_retry_facts(prepared, outcome)
    return prepare_retry_publication(project, prepared, outcome, merged)


def target_ledger(project: Path, plan) -> dict:
    ledger = load_workflow_state(project)
    stage = ledger["stages"]["retrieval"]
    stage.update(status=plan.merged_facts.status, counts=dict(plan.merged_facts.counts), stale=False,
        error=None if plan.merged_facts.status != "failed" else "Action produced no successful outputs (1 failed).")
    return ledger


class RetryAbortTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.project = Path(self.temp.name) / "project"
        self.project.mkdir(); retryable_project(self.project); self.preparation = preparation(self.project)
        self.staging_name = ".retrieval_retry_staging_case"

    def tearDown(self):
        abandon_retry_transaction(self.project); self.temp.cleanup()

    def test_begin_writes_path_safe_immutable_abort_marker_before_mutation(self):
        original = self.preparation.snapshot.mutable_fact_copies()
        handle = begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        marker = json.loads((self.project / PENDING_RETRY_FILE).read_text())
        self.assertEqual((marker["version"], marker["phase"]), (1, "abort"))
        self.assertNotIn(str(self.project), json.dumps(marker))
        self.assertEqual(marker["candidate_names"], [f"retry-{self.preparation.snapshot.report_revision}-{self.preparation.selected_ids[0]}.pdf"])
        self.preparation.snapshot.mutable_fact_copies()[0]["success"] = 999
        self.assertEqual(marker, json.loads(handle.marker_path.read_text()))
        self.assertEqual(original[2], self.preparation.snapshot.mutable_fact_copies()[2])

    def test_begin_rejects_polymorphic_or_noncanonical_preparation_without_reserving_project(self):
        class SnapshotSubclass(RetrySnapshot): pass
        class ItemSubclass(RetryItem): pass
        class EvilStr(str): pass
        class EvilInt(int): pass
        absolute_project = str(self.project)
        class AlwaysEqualDict(dict):
            def __eq__(self, other): return True
            def __ne__(self, other): return False
        class StatefulItems(dict):
            calls = 0
            def items(self):
                self.calls += 1
                if self.calls == 1:
                    return {**self, "success": 99}.items()
                return super().items()
        class ExplodingItems(dict):
            def items(self): raise ValueError(absolute_project)

        base = self.preparation
        snapshot = base.snapshot
        item = base.items[0]
        bad_item = ItemSubclass(item.retry_id, item.label, item.failure_class, item.report_index, item.included_index)
        stateful_backing = StatefulItems(snapshot.report)
        cases = [
            RetryPreparation(SnapshotSubclass(snapshot.report_revision, snapshot.report, snapshot.included, snapshot.ledger, snapshot.items), base.selected_ids, base.items, base.included_rows, base._project_identity),
            RetryPreparation(snapshot, base.selected_ids, (bad_item,), base.included_rows, base._project_identity),
            RetryPreparation(snapshot, (EvilStr(base.selected_ids[0]),), base.items, base.included_rows, base._project_identity),
            RetryPreparation(RetrySnapshot(snapshot.report_revision, snapshot.report, snapshot.included, snapshot.ledger,
                (RetryItem(item.retry_id, item.label, item.failure_class, EvilInt(item.report_index), item.included_index),)), base.selected_ids, base.items, base.included_rows, base._project_identity),
            RetryPreparation(RetrySnapshot(snapshot.report_revision, MappingProxyType(AlwaysEqualDict({**snapshot.report, "success": 99})), snapshot.included, snapshot.ledger, snapshot.items), base.selected_ids, base.items, base.included_rows, base._project_identity),
            RetryPreparation(RetrySnapshot(snapshot.report_revision, MappingProxyType({**snapshot.report,
                "downloaded": (MappingProxyType(AlwaysEqualDict({**snapshot.report["downloaded"][0], "id": "forged"})),)}), snapshot.included, snapshot.ledger, snapshot.items), base.selected_ids, base.items, base.included_rows, base._project_identity),
            RetryPreparation(RetrySnapshot(snapshot.report_revision, snapshot.report,
                (MappingProxyType(AlwaysEqualDict({**snapshot.included[1], "title": "forged"})), snapshot.included[0]), snapshot.ledger, snapshot.items), base.selected_ids, base.items, base.included_rows, base._project_identity),
            RetryPreparation(RetrySnapshot(snapshot.report_revision, MappingProxyType(stateful_backing), snapshot.included, snapshot.ledger, snapshot.items), base.selected_ids, base.items, base.included_rows, base._project_identity),
            RetryPreparation(RetrySnapshot(snapshot.report_revision, MappingProxyType(ExplodingItems(snapshot.report)), snapshot.included, snapshot.ledger, snapshot.items), base.selected_ids, base.items, base.included_rows, base._project_identity),
            RetryPreparation(snapshot, base.selected_ids,
                (RetryItem(item.retry_id, "forged", item.failure_class, item.report_index, item.included_index),), base.included_rows, base._project_identity),
        ]
        for forged in cases:
            with self.subTest(kind=type(forged.snapshot).__name__):
                with self.assertRaises(ValueError) as caught:
                    begin_retry_transaction(self.project, forged, self.staging_name, self.project / self.staging_name)
                self.assertNotIn(str(self.project), str(caught.exception))
                self.assertFalse((self.project / PENDING_RETRY_FILE).exists())
                begin_retry_transaction(self.project, base, self.staging_name, self.project / self.staging_name)
                self.assertTrue(abort_retry_transaction(self.project))
        self.assertEqual(stateful_backing.calls, 1)

    def test_begin_uses_fresh_authoritative_facts_for_abort_marker(self):
        snapshot = self.preparation.snapshot
        forged_report = MappingProxyType({**snapshot.report, "opaque": "forged"})
        forged = RetryPreparation(RetrySnapshot(snapshot.report_revision, forged_report, snapshot.included, snapshot.ledger, snapshot.items),
            self.preparation.selected_ids, self.preparation.items, self.preparation.included_rows, self.preparation._project_identity)
        with self.assertRaises(ValueError):
            begin_retry_transaction(self.project, forged, self.staging_name, self.project / self.staging_name)
        self.assertFalse((self.project / PENDING_RETRY_FILE).exists())

    def test_begin_releases_reservation_when_marker_encoding_fails(self):
        ledger = load_workflow_state(self.project)
        ledger["opaque"] = float("nan")
        save_workflow_state(self.project, ledger)

        with self.assertRaisesRegex(ValueError, r"^Retry transaction marker could not be written$") as caught:
            begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        self.assertNotIn(str(self.project), str(caught.exception))
        self.assertFalse((self.project / PENDING_RETRY_FILE).exists())

        ledger = load_workflow_state(self.project)
        ledger.pop("opaque")
        save_workflow_state(self.project, ledger)
        begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        self.assertTrue(abort_retry_transaction(self.project))

    def test_marker_round_trips_sentinel_shaped_values_and_absolute_keys_without_raw_paths(self):
        report_path = self.project / "pdfs" / "download_report.json"
        report = json.loads(report_path.read_text())
        report["opaque"] = [
            {"$reviewpilot_absolute_path": "L3RtcA=="},
            {"$reviewpilot_absolute_path": "not-base64!"},
            {str(self.project / "absolute-key"): str(self.project / "absolute-value")},
        ]
        atomic_write_json(report_path, report)
        prepared = preparation(self.project)
        before = prepared.snapshot.mutable_fact_copies()
        begin_retry_transaction(self.project, prepared, self.staging_name, self.project / self.staging_name)
        raw = (self.project / PENDING_RETRY_FILE).read_text()
        self.assertNotIn(str(self.project), raw)
        atomic_write_json(report_path, {"mutated": True})
        self.assertTrue(abort_retry_transaction(self.project))
        self.assertEqual(json.loads(report_path.read_text()), before[0])

    def test_marker_version_requires_exact_integer_one(self):
        for invalid in (True, 1.0, "1"):
            with self.subTest(invalid=invalid):
                begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
                abandon_retry_transaction(self.project)
                marker = self.project / PENDING_RETRY_FILE
                data = json.loads(marker.read_text()); data["version"] = invalid
                marker.write_text(json.dumps(data))
                with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)
                marker.unlink()

    def test_marker_rejects_noncanonical_duplicate_before_keys(self):
        begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        abandon_retry_transaction(self.project)
        marker = self.project / PENDING_RETRY_FILE
        data = json.loads(marker.read_text())
        raw = base64.b64decode(data["before_json_b64"])
        before = json.loads(raw)
        raw = raw[:-1] + b',"report":' + json.dumps(before["report"], sort_keys=True, separators=(",", ":")).encode() + b"}"
        data["before_json_b64"] = base64.b64encode(raw).decode()
        marker.write_text(json.dumps(data))
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)

    def test_begin_rejects_hardlinked_authoritative_files(self):
        paths = [self.project / "pdfs" / "download_report.json", self.project / "filtered" / "included_papers.jsonl", self.project / "workflow_state.json"]
        for index, path in enumerate(paths):
            with self.subTest(path=path.name):
                link = self.project / f"authority-link-{index}"
                os.link(path, link)
                with self.assertRaises(ValueError):
                    begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
                link.unlink()

    def test_reconcile_rejects_hardlinked_marker_authority_and_candidate_without_deleting_links(self):
        handle = begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        abandon_retry_transaction(self.project)
        marker_link = self.project / "marker-link"; os.link(handle.marker_path, marker_link)
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)
        self.assertTrue(handle.marker_path.exists()); self.assertTrue(marker_link.exists()); marker_link.unlink()

        authority = self.project / "workflow_state.json"; authority_link = self.project / "authority-link"; os.link(authority, authority_link)
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)
        self.assertTrue(authority.exists()); self.assertTrue(authority_link.exists()); authority_link.unlink()

        candidate = self.project / "pdfs" / handle.candidate_names[0]; candidate.write_bytes(b"%PDF-new")
        candidate_link = self.project / "candidate-link"; os.link(candidate, candidate_link)
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)
        self.assertTrue(candidate.exists()); self.assertTrue(candidate_link.exists())

    def test_begin_revalidates_and_rejects_every_collision_kind(self):
        name = f"retry-{self.preparation.snapshot.report_revision}-{self.preparation.selected_ids[0]}.pdf"
        for kind in ("file", "dir", "symlink", "broken"):
            with self.subTest(kind=kind):
                path = self.project / "pdfs" / name
                if kind == "file": path.write_bytes(b"x")
                elif kind == "dir": path.mkdir()
                else: path.symlink_to(self.project / ("missing" if kind == "broken" else "workflow_state.json"))
                with self.assertRaises(ValueError) as caught:
                    begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
                self.assertNotIn(str(self.project), str(caught.exception)); path.unlink() if not path.is_dir() else path.rmdir()

    def test_abort_restores_all_facts_and_removes_only_owned_names(self):
        before = self.preparation.snapshot.mutable_fact_copies()
        handle = begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        candidate = self.project / "pdfs" / handle.candidate_names[0]
        candidate.write_bytes(b"%PDF-new")
        keep = self.project / "pdfs" / "keep.pdf"; keep.write_bytes(b"keep")
        staging = self.project / self.staging_name; staging.mkdir(); (staging / "junk").write_text("x")
        atomic_write_json(self.project / "pdfs" / "download_report.json", {"mutated": True})
        atomic_write_jsonl(self.project / "filtered" / "included_papers.jsonl", [{"mutated": True}])
        ledger = load_workflow_state(self.project); ledger["stages"]["retrieval"]["status"] = "running"; save_workflow_state(self.project, ledger)
        self.assertTrue(abort_retry_transaction(self.project))
        self.assertEqual(json.loads((self.project / "pdfs" / "download_report.json").read_text()), before[0])
        self.assertEqual([json.loads(line) for line in (self.project / "filtered" / "included_papers.jsonl").read_text().splitlines()], before[1])
        self.assertEqual(load_workflow_state(self.project)["stages"]["retrieval"], before[2])
        self.assertFalse(candidate.exists()); self.assertFalse(staging.exists()); self.assertEqual(keep.read_bytes(), b"keep")
        self.assertFalse((self.project / PENDING_RETRY_FILE).exists())

    def test_live_reconcile_skips_but_abandon_allows_restart_recovery(self):
        begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        self.assertFalse(reconcile_retry_transaction(self.project))
        abandon_retry_transaction(self.project)
        self.assertTrue(reconcile_retry_transaction(self.project)); self.assertFalse(reconcile_retry_transaction(self.project))

    def test_every_abort_failure_keeps_the_complete_rollback_retryable(self):
        ledger = load_workflow_state(self.project)
        ledger["unknown_valid"] = {"preserve": [1, {"nested": True}]}
        save_workflow_state(self.project, ledger)
        self.preparation = preparation(self.project)
        before_report, before_included, _ = self.preparation.snapshot.mutable_fact_copies()
        before_ledger = load_workflow_state(self.project)
        resolved_project = self.project.resolve()
        keep = self.project / "pdfs" / "unowned-keep.pdf"
        keep.write_bytes(b"keep")
        cases = (
            ("report-write", "reconcile"),
            ("included-write", "reconcile"),
            ("ledger-write", "reconcile"),
            ("candidate-unlink", "abort"),
            ("staging-rmtree", "abort"),
            ("marker-unlink", "abort"),
        )

        for seam, recovery_mode in cases:
            with self.subTest(seam=seam, recovery_mode=recovery_mode):
                handle = begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
                marker = self.project / PENDING_RETRY_FILE
                candidate = self.project / "pdfs" / handle.candidate_names[0]
                candidate.write_bytes(b"%PDF-new")
                staging = self.project / self.staging_name
                staging.mkdir()
                (staging / "owned-junk").write_text("x")
                atomic_write_json(self.project / "pdfs" / "download_report.json", {"mutated": seam})
                atomic_write_jsonl(self.project / "filtered" / "included_papers.jsonl", [{"mutated": seam}])
                mutated_ledger = load_workflow_state(self.project)
                mutated_ledger["unknown_valid"] = {"mutated": seam}
                for index, stage in enumerate(mutated_ledger["stages"].values()):
                    stage["attempt"] += 100 + index
                    stage["counts"] = {"mutated": index}
                save_workflow_state(self.project, mutated_ledger)

                failed = {"once": False}
                real_unlink = Path.unlink
                real_rmtree = shutil.rmtree

                def fail_once(target):
                    if not failed["once"]:
                        failed["once"] = True
                        raise OSError(str(self.project / target))

                def injected_json(path, data, *args, **kwargs):
                    if Path(path) == resolved_project / "pdfs" / "download_report.json":
                        fail_once("report-write")
                    return atomic_write_json(path, data, *args, **kwargs)

                def injected_jsonl(path, records, *args, **kwargs):
                    if Path(path) == resolved_project / "filtered" / "included_papers.jsonl":
                        fail_once("included-write")
                    return atomic_write_jsonl(path, records, *args, **kwargs)

                def injected_ledger(project, state):
                    if Path(project).resolve() == self.project.resolve():
                        fail_once("ledger-write")
                    return save_workflow_state(project, state)

                def injected_unlink(path, *args, **kwargs):
                    target = resolved_project / PENDING_RETRY_FILE if seam == "marker-unlink" else resolved_project / "pdfs" / handle.candidate_names[0]
                    if Path(path) == target:
                        fail_once(seam)
                    return real_unlink(path, *args, **kwargs)

                def injected_rmtree(path, *args, **kwargs):
                    if Path(path) == resolved_project / self.staging_name:
                        fail_once("staging-rmtree")
                    return real_rmtree(path, *args, **kwargs)

                if seam == "report-write":
                    failure_patch = patch("reviewpilot_core.retrieval_retry_transaction.atomic_write_json", side_effect=injected_json)
                elif seam == "included-write":
                    failure_patch = patch("reviewpilot_core.retrieval_retry_transaction.atomic_write_jsonl", side_effect=injected_jsonl)
                elif seam == "ledger-write":
                    failure_patch = patch("reviewpilot_core.retrieval_retry_transaction.save_workflow_state", side_effect=injected_ledger)
                elif seam in {"candidate-unlink", "marker-unlink"}:
                    failure_patch = patch.object(Path, "unlink", autospec=True, side_effect=injected_unlink)
                else:
                    failure_patch = patch("reviewpilot_core.retrieval_retry_transaction.shutil.rmtree", side_effect=injected_rmtree)

                if recovery_mode == "reconcile":
                    abandon_retry_transaction(self.project)
                    recover = reconcile_retry_transaction
                else:
                    recover = abort_retry_transaction
                with failure_patch:
                    with self.assertRaises(ValueError) as caught:
                        recover(self.project)
                self.assertTrue(failed["once"])
                self.assertNotIn(str(self.project), str(caught.exception))
                self.assertTrue(marker.exists())

                # The marker is the durable promise that a partially completed
                # rollback can be repeated until every authoritative fact wins.
                self.assertTrue(reconcile_retry_transaction(self.project))
                self.assertEqual(json.loads((self.project / "pdfs" / "download_report.json").read_text()), before_report)
                self.assertEqual([json.loads(line) for line in (self.project / "filtered" / "included_papers.jsonl").read_text().splitlines()], before_included)
                self.assertEqual(load_workflow_state(self.project), before_ledger)
                self.assertFalse(candidate.exists())
                self.assertFalse(staging.exists())
                self.assertFalse(marker.exists())
                self.assertEqual(keep.read_bytes(), b"keep")

    def test_active_reservations_are_isolated_by_project(self):
        with tempfile.TemporaryDirectory() as second_temp:
            second_project = Path(second_temp) / "project"
            second_project.mkdir()
            retryable_project(second_project)
            second_preparation = preparation(second_project)
            second_staging = ".retrieval_retry_staging_second"
            try:
                first = begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
                second = begin_retry_transaction(second_project, second_preparation, second_staging, second_project / second_staging)
                first_marker = first.marker_path.read_bytes()
                with self.assertRaisesRegex(ValueError, "cannot begin safely|already active"):
                    begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
                self.assertEqual(first.marker_path.read_bytes(), first_marker)
                self.assertTrue(second.marker_path.exists())
                self.assertFalse(reconcile_retry_transaction(self.project))
                self.assertFalse(reconcile_retry_transaction(second_project))

                abandon_retry_transaction(self.project)
                abandon_retry_transaction(second_project)
                self.assertTrue(reconcile_retry_transaction(self.project))
                self.assertTrue(reconcile_retry_transaction(second_project))
            finally:
                abandon_retry_transaction(self.project)
                abandon_retry_transaction(second_project)

    def test_malformed_or_symlink_marker_and_unsafe_staging_fail_closed(self):
        marker = self.project / PENDING_RETRY_FILE
        marker.write_text("{}")
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)
        marker.unlink(); marker.symlink_to(self.project / "workflow_state.json")
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)
        marker.unlink(); begin_retry_transaction(self.project, self.preparation, self.staging_name, self.project / self.staging_name)
        abandon_retry_transaction(self.project); (self.project / self.staging_name).symlink_to(self.project / "filtered")
        with self.assertRaises(ValueError): reconcile_retry_transaction(self.project)


class RetryTargetTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.project = Path(self.temp.name).resolve() / "project"
        self.project.mkdir(); retryable_project(self.project); self.prepared = preparation(self.project)
        self.staging_name = ".retrieval_retry_staging_apply"
        self.handle = begin_retry_transaction(self.project, self.prepared, self.staging_name, self.project / self.staging_name)
        self.plan = publication(self.project, self.prepared, self.staging_name)
        self.ledger = target_ledger(self.project, self.plan)

    def tearDown(self):
        abandon_retry_transaction(self.project); self.temp.cleanup()

    def publish_target(self):
        report, included = self.plan.merged_facts.mutable_copies()
        pdf = self.plan.pdfs[0]
        pdf.destination_path.write_bytes(pdf.source_path.read_bytes())
        atomic_write_json(self.project / "pdfs" / "download_report.json", report)
        atomic_write_jsonl(self.project / "filtered" / "included_papers.jsonl", included)
        save_workflow_state(self.project, self.ledger)

    def forged_target(self, mutate_report):
        def freeze(value):
            if type(value) is dict:
                return MappingProxyType({key: freeze(item) for key, item in value.items()})
            if type(value) is list:
                return tuple(freeze(item) for item in value)
            return value

        report, _ = self.plan.merged_facts.mutable_copies()
        mutate_report(report)
        success, failed = report["success"], report["failed"]
        status = "partial" if success and failed else "completed" if success else "failed"
        counts = {"succeeded": success, "failed": failed}
        merged = RetryMergedFacts(
            self.plan.merged_facts.report_revision, freeze(report), self.plan.merged_facts.included,
            status, MappingProxyType(counts), self.plan.merged_facts.planned_pdfs,
        )
        plan = RetryPublicationPlan(self.plan.report_revision, merged, self.plan.pdfs)
        ledger = deepcopy(self.ledger)
        ledger["stages"]["retrieval"].update(status=status, counts=counts, stale=False, error=None)
        return plan, ledger

    def test_recorded_target_is_path_safe_immutable_and_abort_still_restores(self):
        before = self.prepared.snapshot.mutable_fact_copies()
        record_retry_transaction_target(self.project, self.plan, self.ledger)
        marker_path = self.project / PENDING_RETRY_FILE
        raw = marker_path.read_text(); marker = json.loads(raw)
        self.assertEqual(marker["phase"], "abort"); self.assertNotIn(str(self.project), raw)
        self.assertEqual(set(marker), {"version", "phase", "expected_revision", "selected_ids", "staging_name",
            "candidate_names", "before_json_b64", "target_json_b64"})
        self.ledger["stages"]["retrieval"]["counts"]["succeeded"] = 99
        self.assertEqual(raw, marker_path.read_text())
        self.publish_target()
        self.assertTrue(abort_retry_transaction(self.project))
        self.assertEqual(json.loads((self.project / "pdfs" / "download_report.json").read_text()), before[0])
        self.assertFalse(self.plan.pdfs[0].destination_path.exists())

    def test_record_rejects_ledger_mismatch_and_forged_plan_without_changing_marker(self):
        before = (self.project / PENDING_RETRY_FILE).read_bytes()
        bad = deepcopy(self.ledger); bad["stages"]["retrieval"]["counts"] = {"succeeded": 99, "failed": 0}
        with self.assertRaisesRegex(ValueError, "target is invalid") as caught:
            record_retry_transaction_target(self.project, self.plan, bad)
        self.assertNotIn(str(self.project), str(caught.exception)); self.assertEqual((self.project / PENDING_RETRY_FILE).read_bytes(), before)

    def test_record_rejects_every_target_that_recovery_cannot_decode_before_writing_marker(self):
        cases = (
            ("success-count", lambda report: report.update(success=999)),
            ("failed-count", lambda report: report.update(failed=1)),
            ("downloaded-type", lambda report: report.update(downloaded=report["downloaded"][-1])),
            ("failed-papers-type", lambda report: report.update(failed_papers={})),
        )
        for index, (label, mutate) in enumerate(cases):
            with self.subTest(label=label):
                plan, ledger = self.forged_target(mutate)
                self.assertIs(type(plan), RetryPublicationPlan)
                self.assertIs(type(plan.merged_facts), RetryMergedFacts)
                before = (self.project / PENDING_RETRY_FILE).read_bytes()
                with self.assertRaisesRegex(ValueError, r"^Retry transaction target is invalid$") as caught:
                    record_retry_transaction_target(self.project, plan, ledger)
                self.assertNotIn(str(self.project), str(caught.exception))
                self.assertEqual((self.project / PENDING_RETRY_FILE).read_bytes(), before)
                self.assertTrue(abort_retry_transaction(self.project))
                if index + 1 < len(cases):
                    self.handle = begin_retry_transaction(
                        self.project, self.prepared, self.staging_name, self.project / self.staging_name)
                    self.plan = publication(self.project, self.prepared, self.staging_name)
                    self.ledger = target_ledger(self.project, self.plan)

    def test_all_failure_target_records_an_empty_pdf_set_and_remains_abortable(self):
        self.assertTrue(abort_retry_transaction(self.project))
        self.handle = begin_retry_transaction(self.project, self.prepared, self.staging_name, self.project / self.staging_name)
        plan = publication(self.project, self.prepared, self.staging_name, succeeds=False)
        ledger = target_ledger(self.project, plan)
        record_retry_transaction_target(self.project, plan, ledger)
        marker = json.loads((self.project / PENDING_RETRY_FILE).read_text())
        target = json.loads(base64.b64decode(marker["target_json_b64"]))
        self.assertEqual(target["pdfs"], [])
        self.assertTrue(abort_retry_transaction(self.project))


if __name__ == "__main__": unittest.main()
