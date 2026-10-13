"""Manual retrieval handoff: failure list, matching, upload, assignment and download-folder watch."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import pymupdf
from starlette.testclient import TestClient

import web_app
from agents.download_agent import DownloadAgent
from reviewpilot_core import manual_retrieval as mr
from reviewpilot_core.atomic_files import atomic_write_json, atomic_write_jsonl
from reviewpilot_core.project_store import read_json, read_jsonl
from reviewpilot_core.retrieval_retry import stable_retry_id
from reviewpilot_core.task_runner import TaskRunner
from reviewpilot_core.workflow_state import complete_action, initialize_workflow_state, load_workflow_state, start_action

PAPERS = [
    {"id": "ok", "title": "An Existing Paper", "source": "openalex", "doi": "10.1000/existing"},
    {"id": "38000001", "source": "pubmed", "doi": "10.2196/94036", "url": "https://pubmed.ncbi.nlm.nih.gov/38000001/",
     "title": "Structured Large Language Model Workflows for Motivational Interviewing in Health Behavior Change",
     "pdf_failure_class": "pdf_endpoint_cloudflare", "pdf_failure_classes": ["metadata_api_429", "pdf_endpoint_cloudflare"],
     "journal": "JMIR Formative Research", "year": "2026"},
    {"id": "W1", "source": "openalex", "doi": "10.1016/j.cell.2024.01.002", "url": "https://openalex.org/W1",
     "title": "Mapping Retinal Ganglion Cell Diversity with Single Cell Transcriptomics",
     "pdf_failure_class": "publisher_paywalled"},
    {"id": "W2", "source": "openalex", "doi": "10.1145/3613904.3642000", "url": "https://dl.acm.org/doi/10.1145/3613904.3642000",
     "title": "Designing Conversational Agents for Older Adults Living Alone at Home",
     "pdf_failure_class": "pdf_title_mismatch"},
]


def make_pdf(*lines: str, blank: bool = False) -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    if not blank:
        y = 72
        for line in lines:
            page.insert_text((54, y), line, fontsize=10)
            y += 16
    data = document.tobytes()
    document.close()
    return data


def article(paper: dict, *, with_doi: bool = True) -> bytes:
    lines = ["Journal of Examples 2026", paper["title"], "A. Author, B. Author"]
    if with_doi:
        lines.append(f"https://doi.org/{paper['doi']}")
    return make_pdf(*lines, "Abstract", "Background and methods of the study are described here.")


def build_project(project: Path) -> None:
    (project / "pdfs").mkdir(parents=True)
    existing = project / "pdfs" / "existing.pdf"
    existing.write_bytes(make_pdf("An Existing Paper"))
    rows, downloaded, failed = [], [], []
    for paper in PAPERS:
        row = {key: value for key, value in paper.items()}
        if paper["id"] == "ok":
            row.update(pdf_downloaded=True, pdf_path=str(existing), pdf_method="direct_pdf", retrieval_status="downloaded")
            downloaded.append({"title": row["title"], "path": str(existing), "method": "direct_pdf"})
        else:
            row.update(pdf_downloaded=False, retrieval_status="subscribed_unavailable" if "paywall" in row["pdf_failure_class"] else "unavailable",
                       web_search_fallback_pending=True, web_search_fallback_eligible=True)
            failed.append({"id": row["id"], "title": row["title"], "doi": row["doi"], "url": row.get("url", ""),
                           "failure_class": row["pdf_failure_class"], "retrieval_status": row["retrieval_status"],
                           "web_search_fallback_pending": True, "web_search_fallback_eligible": True})
        rows.append(row)
    atomic_write_json(project / "search_conditions.json", {"project_name": "demo", "platforms": ["pubmed"]})
    atomic_write_jsonl(project / "filtered/included_papers.jsonl", rows)
    atomic_write_json(project / "pdfs/download_report.json", {
        "success": 1, "failed": 3, "attempted": 4, "pdf_count": 1, "downloaded": downloaded, "failed_papers": failed,
        "subscribed_papers": [row for row in failed if row["retrieval_status"] == "subscribed_unavailable"],
        "unavailable_papers": [row for row in failed if row["retrieval_status"] == "unavailable"],
        "web_search_fallback_candidates": failed,
    })
    initialize_workflow_state(project)
    start_action(project, "collect")
    complete_action(project, "collect", {"total": 4, "platform_stats": {"pubmed": 4}, "platform_errors": {}})
    start_action(project, "screen")
    complete_action(project, "screen", {})
    start_action(project, "download-pdfs")
    complete_action(project, "download-pdfs", {"success": 1, "failed": 3})


def retry_id(index: int) -> str:
    return stable_retry_id(PAPERS[index])


class ReasonAndLinkTests(unittest.TestCase):
    def test_failure_classes_map_to_plain_reasons(self):
        cases = [
            ({"pdf_failure_class": "publisher_paywalled"}, "paywalled"),
            ({"pdf_failure_class": "pdf_endpoint_cloudflare"}, "blocked"),
            ({"pdf_failure_class": "antibot_challenge"}, "blocked"),
            ({"pdf_failure_class": "access_denied"}, "blocked"),
            ({"pdf_failure_class": "pmc_not_in_oa_cloud"}, "paywalled"),
            ({"pdf_failure_class": "pdf_title_mismatch"}, "not_found"),
            ({"pdf_failure_class": "download_failed", "doi": "10.1/x"}, "not_found"),
            ({"pdf_failure_class": "pdf_title_mismatch", "pdf_failure_classes": ["antibot_challenge", "pdf_title_mismatch"]}, "blocked"),
            ({"title": "Correction to: A study", "doi": "10.1/x"}, "not_article"),
            ({"pdf_failure_class": "no_downloadable_pdf"}, "not_article"),
        ]
        for row, expected in cases:
            with self.subTest(row=row):
                code, text = mr.failure_reason(row, {})
                self.assertEqual(code, expected)
                self.assertEqual(text, mr.REASONS[expected])

    def test_links_cover_doi_publisher_pubmed_pmc_and_library(self):
        row = {"doi": "10.2196/94036", "source": "pubmed", "id": "38000001", "pmcid": "PMC123456",
               "url": "https://pubmed.ncbi.nlm.nih.gov/38000001/", "title": "A title", "journal": "J", "year": "2026"}
        links = {link["kind"]: link["url"] for link in mr.paper_links(row, "https://resolver.example.edu/openurl")}
        self.assertEqual(links["doi"], "https://doi.org/10.2196/94036")
        self.assertEqual(links["pubmed"], "https://pubmed.ncbi.nlm.nih.gov/38000001/")
        self.assertEqual(links["pmc"], "https://pmc.ncbi.nlm.nih.gov/articles/PMC123456/")
        self.assertNotIn("publisher", links)
        self.assertTrue(links["library"].startswith("https://resolver.example.edu/openurl?url_ver=Z39.88-2004"))
        self.assertIn("rft_id=info%3Adoi%2F10.2196%2F94036", links["library"])

        acm = {"doi": "10.1145/1", "url": "https://dl.acm.org/doi/10.1145/1"}
        links = {link["kind"]: link["url"] for link in mr.paper_links(acm, "https://login.ezproxy.example.edu/login?url=")}
        self.assertEqual(links["publisher"], "https://dl.acm.org/doi/10.1145/1")
        self.assertEqual(links["library"], "https://login.ezproxy.example.edu/login?url=https%3A%2F%2Fdoi.org%2F10.1145%2F1")
        self.assertNotIn("library", {link["kind"] for link in mr.paper_links(acm, "")})

        # Without a DOI the proxy wraps the paper's own landing page; an index page alone gets no library link.
        ezproxy = "https://ezproxy.library.arizona.edu/login?url="
        landing = {"title": "No DOI", "url": "https://openalex.org/W1", "landing_page_url": "https://ieeexplore.ieee.org/document/1"}
        self.assertEqual(mr.library_link(landing, ezproxy), ezproxy + "https%3A%2F%2Fieeexplore.ieee.org%2Fdocument%2F1")
        self.assertEqual(mr.library_link({"title": "Index only", "url": "https://openalex.org/W2"}, ezproxy), "")

    def test_settings_validate_and_never_store_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(mr.load_settings(tmp), mr.DEFAULT_SETTINGS)
            saved = mr.save_settings(tmp, {"download_folder": "~/Desktop", "watch_enabled": False,
                                           "library_resolver": "https://login.ezproxy.example.edu/login?url="})
            self.assertEqual(saved["download_folder"], "~/Desktop")
            self.assertFalse(mr.load_settings(tmp)["watch_enabled"])
            for bad in ({"library_resolver": "https://user:secret@proxy.example.edu/login?url="},
                        {"library_resolver": "javascript:alert(1)"}, {"download_folder": "relative/folder"},
                        {"watch_enabled": "yes"}, {"password": "x"}):
                with self.subTest(bad=bad), self.assertRaises(mr.ManualRetrievalError):
                    mr.save_settings(tmp, bad)
            self.assertNotIn("secret", (Path(tmp) / mr.SETTINGS_FILE).read_text())


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [mr.Candidate(retry_id(i), PAPERS[i]["title"], PAPERS[i]["doi"], PAPERS[i]["title"]) for i in (1, 2, 3)]
        self.titles = [paper["title"] for paper in PAPERS]

    def test_doi_on_page_and_title_matches_are_confident(self):
        outcome = mr.match_pdf(article(PAPERS[1]), self.candidates, self.titles)
        self.assertEqual((outcome.status, outcome.retry_id, outcome.basis), ("matched", retry_id(1), "doi_on_page"))
        outcome = mr.match_pdf(article(PAPERS[2], with_doi=False), self.candidates, self.titles)
        self.assertEqual((outcome.status, outcome.retry_id, outcome.basis), ("matched", retry_id(2), "title"))

    def test_supplement_other_paper_scan_and_two_papers_do_not_auto_match(self):
        supplement = make_pdf("Multimedia Appendix 1", PAPERS[1]["title"], f"doi:{PAPERS[1]['doi']}")
        self.assertEqual(mr.match_pdf(supplement, self.candidates, self.titles).status, "supplement")
        other = make_pdf("Proceedings 2025", "A Completely Different Study of Soil Microbiomes in Arid Farmland", "Abstract")
        self.assertEqual(mr.match_pdf(other, self.candidates, self.titles).status, "no_match")
        self.assertEqual(mr.match_pdf(make_pdf(blank=True), self.candidates, self.titles).status, "no_text")
        both = make_pdf(PAPERS[2]["title"], f"doi {PAPERS[2]['doi']}", PAPERS[3]["title"], f"doi {PAPERS[3]['doi']}")
        outcome = mr.match_pdf(both, self.candidates, self.titles)
        self.assertEqual(outcome.status, "ambiguous")
        self.assertEqual({item["retry_id"] for item in outcome.candidates}, {retry_id(2), retry_id(3)})
        self.assertEqual(mr.match_pdf(b"<html>not a pdf</html>", self.candidates, self.titles).status, "invalid")

    def test_another_awaiting_papers_pdf_is_not_matched_to_the_awaiting_one(self):
        only_awaiting = [candidate for candidate in self.candidates if candidate.retry_id == retry_id(1)]
        self.assertEqual(mr.match_pdf(article(PAPERS[2]), only_awaiting, self.titles).status, "no_match")


class WebImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "output"
        self.project = self.root / "demo"
        self.project.mkdir(parents=True)
        build_project(self.project)
        self.downloads = Path(self.temp.name) / "Downloads"
        self.downloads.mkdir()
        mr.save_settings(self.root, {"download_folder": str(self.downloads)})
        self.old_root, self.old_runner = web_app.OUTPUT_ROOT, web_app.task_runner
        web_app.OUTPUT_ROOT = self.root
        web_app.task_runner = TaskRunner(max_workers=1)
        self.client = TestClient(web_app.create_app())

    def tearDown(self):
        web_app.task_runner.shutdown()
        web_app.task_runner, web_app.OUTPUT_ROOT = self.old_runner, self.old_root
        with web_app.download_watcher._guard:
            web_app.download_watcher._projects.clear()
        self.temp.cleanup()

    def upload(self, content: bytes, name: str, retry: str | None = None):
        query = f"?name={name}" + (f"&retry_id={retry}" if retry else "")
        return self.client.post(f"/projects/demo/manual-retrieval/files{query}", content=content,
                                headers={"Content-Type": "application/pdf"})

    def row(self, index: int) -> dict:
        return next(row for row in read_jsonl(self.project / "filtered/included_papers.jsonl") if row["id"] == PAPERS[index]["id"])

    def assert_imported(self, index: int, source: str, basis: str) -> dict:
        row = self.row(index)
        self.assertTrue(row["pdf_downloaded"])
        self.assertEqual(row["pdf_method"], "user_download")
        self.assertEqual(row["pdf_source"], source)
        self.assertEqual(row["pdf_match_evidence"]["basis"], basis)
        self.assertEqual(row["retrieval_status"], "downloaded")
        self.assertNotIn("pdf_failure_class", row)
        path = Path(row["pdf_path"])
        self.assertEqual(path.parent, (self.project / "pdfs").resolve())
        self.assertTrue(path.read_bytes().startswith(b"%PDF-"))
        report = read_json(self.project / "pdfs/download_report.json")
        self.assertIn(row["title"], [detail["title"] for detail in report["downloaded"]])
        self.assertNotIn(row["title"], [detail["title"] for detail in report["failed_papers"]])
        self.assertEqual(report["provided_by_user"] + report["obtained_automatically"], report["success"])
        self.assertEqual(load_workflow_state(self.project)["stages"]["retrieval"]["counts"]["succeeded"], report["success"])
        return row

    def test_failure_list_projection(self):
        state = self.client.get("/projects/demo/state").json()["manualRetrieval"]
        self.assertTrue(state["enabled"])
        reasons = {item["label"]: item["reason"] for item in state["items"]}
        self.assertEqual(reasons[PAPERS[1]["title"]], "Publisher site blocks automated download")
        self.assertEqual(reasons[PAPERS[2]["title"]], "Paywalled (no open copy found)")
        self.assertEqual(reasons[PAPERS[3]["title"]], "No full text found")
        acm = next(item for item in state["items"] if item["label"] == PAPERS[3]["title"])
        self.assertEqual([link["kind"] for link in acm["links"]], ["doi", "publisher"])
        self.assertEqual(state["counts"], {"automatic": 1, "user": 0})
        self.assertTrue(state["settings"]["watchEnabled"])

    def test_upload_matches_and_imports_through_the_retry_transaction(self):
        original = article(PAPERS[2], with_doi=False)
        response = self.upload(original, "cell.pdf")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["result"]["outcome"], "imported")
        self.assert_imported(2, "upload", "title")
        report = read_json(self.project / "pdfs/download_report.json")
        self.assertEqual((report["success"], report["failed"], report["provided_by_user"], report["obtained_automatically"]), (2, 2, 1, 1))
        state = response.json()["state"]
        self.assertEqual(state["retrievalSummary"]["providedByUser"], 1)
        self.assertEqual(state["manualRetrieval"]["recent"][0]["source"], "added by you")
        self.assertEqual(len(state["manualRetrieval"]["items"]), 2)
        self.assertFalse(list(self.project.glob(".retrieval_retry_*")))
        again = self.upload(original, "cell-copy.pdf")
        self.assertEqual(again.json()["result"]["outcome"], "duplicate")

    def test_manual_assignment_is_final_even_for_a_scan(self):
        response = self.upload(make_pdf(blank=True), "scan.pdf", retry_id(1))
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_imported(1, "manual_assignment", "user_choice")
        self.assertEqual(self.upload(b"not a pdf", "x.pdf", retry_id(2)).status_code, 400)

    def test_unmatched_upload_waits_for_the_users_choice(self):
        both = make_pdf(PAPERS[2]["title"], f"doi {PAPERS[2]['doi']}", PAPERS[3]["title"], f"doi {PAPERS[3]['doi']}")
        response = self.upload(both, "two.pdf")
        self.assertEqual(response.json()["result"]["outcome"], "pending")
        pending = response.json()["state"]["manualRetrieval"]["pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual({item["retryId"] for item in pending[0]["suggested"]}, {retry_id(2), retry_id(3)})
        self.assertTrue(self.row(2)["pdf_downloaded"] is False and self.row(3)["pdf_downloaded"] is False)
        inbox = list((self.project / mr.STATE_DIR / "inbox").iterdir())
        self.assertEqual(len(inbox), 1)
        chosen = self.client.post(f"/projects/demo/manual-retrieval/pending/{pending[0]['id']}", json={"retry_id": retry_id(3)})
        self.assertEqual(chosen.status_code, 200, chosen.text)
        self.assert_imported(3, "upload", "user_choice")
        self.assertEqual(chosen.json()["state"]["manualRetrieval"]["pending"], [])
        self.assertFalse(inbox[0].exists())

        scan = self.upload(make_pdf(blank=True), "scan.pdf").json()
        pending_id = scan["state"]["manualRetrieval"]["pending"][0]["id"]
        self.assertEqual(self.client.delete(f"/projects/demo/manual-retrieval/pending/{pending_id}").status_code, 200)
        self.assertEqual(list((self.project / mr.STATE_DIR / "inbox").iterdir()), [])

    def test_watch_imports_a_new_download_for_the_awaiting_paper(self):
        clicked = time.time() - 5
        old = self.downloads / "before-click.pdf"
        old.write_bytes(article(PAPERS[1]))
        os.utime(old, (clicked - 60, clicked - 60))
        response = self.client.post("/projects/demo/manual-retrieval/awaiting", json={"retry_id": retry_id(1)})
        self.assertEqual(response.json()["result"]["outcome"], "awaiting")
        self.assertTrue(response.json()["state"]["manualRetrieval"]["awaiting"])
        mr.mark_awaiting(self.project, retry_id(1), now=clicked)
        watcher = web_app.download_watcher
        (self.downloads / "partial.pdf.crdownload").write_bytes(b"%PDF-partial")
        supplement = self.downloads / "supplement.pdf"
        supplement.write_bytes(make_pdf("Multimedia Appendix 1", PAPERS[1]["title"], f"doi:{PAPERS[1]['doi']}"))
        other = self.downloads / "other-paper.pdf"
        other.write_bytes(article(PAPERS[2]))
        now = time.time()
        watcher.tick(now=now)            # sizes recorded
        watcher.tick(now=now + 2)        # stable: neither file is the awaiting paper
        self.assertFalse(self.row(1)["pdf_downloaded"] or self.row(2)["pdf_downloaded"])
        target = self.downloads / "jmir-94036.pdf"
        content = article(PAPERS[1])
        target.write_bytes(content)
        before = target.stat()
        watcher.tick(now=now + 3)
        self.assertFalse(self.row(1)["pdf_downloaded"])
        watcher.tick(now=now + 5)        # stable: matched and imported
        row = self.assert_imported(1, "download_watch", "doi_on_page")
        self.assertEqual(row["pdf_match_evidence"]["file_name"], "jmir-94036.pdf")
        self.assertEqual(target.read_bytes(), content)
        self.assertEqual(target.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertFalse(self.row(2)["pdf_downloaded"])
        state = self.client.get("/projects/demo/state").json()["manualRetrieval"]
        self.assertFalse(state["awaiting"])
        reasons = {item["fileName"]: item["reason"] for item in state["pending"]}
        self.assertEqual(set(reasons), {"supplement.pdf", "other-paper.pdf"})
        self.assertIn("supplementary", reasons["supplement.pdf"])
        self.assertNotIn("before-click.pdf", reasons)
        self.assertTrue(old.exists() and supplement.exists() and other.exists())
        self.assertEqual(list((self.project / mr.STATE_DIR).glob("inbox/*")), [])

    def test_watch_prompts_when_a_download_matches_two_awaiting_papers(self):
        clicked = time.time() - 5
        for index in (2, 3):
            mr.mark_awaiting(self.project, retry_id(index), now=clicked)
        web_app.download_watcher.watch("demo")
        both = self.downloads / "both.pdf"
        both.write_bytes(make_pdf(PAPERS[2]["title"], f"doi {PAPERS[2]['doi']}", PAPERS[3]["title"], f"doi {PAPERS[3]['doi']}"))
        now = time.time()
        web_app.download_watcher.tick(now=now)
        web_app.download_watcher.tick(now=now + 2)
        self.assertFalse(self.row(2)["pdf_downloaded"] or self.row(3)["pdf_downloaded"])
        pending = self.client.get("/projects/demo/state").json()["manualRetrieval"]["pending"]
        self.assertEqual({item["retryId"] for item in pending[0]["suggested"]}, {retry_id(2), retry_id(3)})
        chosen = self.client.post(f"/projects/demo/manual-retrieval/pending/{pending[0]['id']}", json={"retry_id": retry_id(2)})
        self.assertEqual(chosen.status_code, 200, chosen.text)
        self.assert_imported(2, "download_watch", "user_choice")
        self.assertTrue(both.exists())

    def test_three_sources_produce_the_same_records(self):
        self.upload(article(PAPERS[2]), "a.pdf")
        self.upload(article(PAPERS[3]), "b.pdf", retry_id(3))
        mr.mark_awaiting(self.project, retry_id(1), now=time.time() - 5)
        web_app.download_watcher.watch("demo")
        (self.downloads / "c.pdf").write_bytes(article(PAPERS[1]))
        now = time.time()
        web_app.download_watcher.tick(now=now)
        web_app.download_watcher.tick(now=now + 2)
        rows = [self.assert_imported(1, "download_watch", "doi_on_page"), self.assert_imported(2, "upload", "doi_on_page"),
                self.assert_imported(3, "manual_assignment", "user_choice")]
        provenance = [{key for key in row if key.startswith("pdf_") or key.startswith("retrieval")} for row in rows]
        self.assertEqual(provenance[0], provenance[1])
        self.assertEqual(provenance[1], provenance[2])
        report = read_json(self.project / "pdfs/download_report.json")
        self.assertEqual((report["success"], report["failed"], report["provided_by_user"]), (4, 0, 3))
        self.assertEqual(load_workflow_state(self.project)["stages"]["retrieval"]["status"], "completed")
        self.assertEqual({detail["pdf_source"] for detail in report["downloaded"] if detail.get("method") == "user_download"},
                         {"download_watch", "upload", "manual_assignment"})
        state = self.client.get("/projects/demo/state").json()
        self.assertFalse(state["manualRetrieval"]["enabled"])
        self.assertEqual(len(state["manualRetrieval"]["recent"]), 3)

    def test_watch_is_off_when_disabled_in_settings(self):
        self.assertEqual(self.client.put("/settings/retrieval", json={"watch_enabled": False}).status_code, 200)
        response = self.client.post("/projects/demo/manual-retrieval/awaiting", json={"retry_id": retry_id(1)})
        self.assertEqual(response.json()["result"]["outcome"], "not_watching")
        self.assertFalse(response.json()["state"]["manualRetrieval"]["awaiting"])
        self.assertEqual(self.client.put("/settings/retrieval", json={"library_resolver": "https://u:p@x.edu/?url="}).status_code, 400)

    def test_assessing_papers_without_full_text_is_off_until_turned_on(self):
        self.assertFalse(self.client.get("/settings/retrieval").json()["assessWithoutFullText"])
        self.assertEqual(self.client.put("/settings/retrieval", json={"assess_without_full_text": "on"}).status_code, 400)
        saved = self.client.put("/settings/retrieval", json={"assess_without_full_text": True})
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["assessWithoutFullText"])
        self.assertTrue(self.client.get("/projects/demo/state").json()["noFullText"]["enabled"])

    def test_malformed_json_is_a_plain_request_error(self):
        response = self.client.post("/projects/demo/manual-retrieval/awaiting", content=b"{not json",
                                    headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("valid JSON", response.json()["detail"])

    def test_examples_and_unknown_papers_are_refused(self):
        self.assertEqual(self.client.post("/projects/demo/manual-retrieval/awaiting", json={"retry_id": "0" * 64}).status_code, 400)
        self.assertEqual(self.client.post("/projects/missing/manual-retrieval/awaiting", json={"retry_id": "x"}).status_code, 404)


class DownloadAgentKeepsUserFilesTests(unittest.TestCase):
    def test_full_rerun_keeps_user_provided_pdfs_and_counts_them_apart(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "pdfs").mkdir(parents=True)
            mine = project / "pdfs" / "mine_user_download.pdf"
            mine.write_bytes(b"%PDF-1.4 mine")
            included = project / "filtered" / "included_papers.jsonl"
            atomic_write_jsonl(included, [
                {"id": "a", "title": "Mine", "pdf_downloaded": True, "pdf_path": str(mine), "pdf_method": "user_download",
                 "pdf_source": "upload", "pdf_match_evidence": {"basis": "title"}},
                {"id": "b", "title": "Theirs", "url": "https://example.test/b.pdf"},
            ])
            seen = []

            class FakeDownloader:
                def __init__(self, *, email, output_dir, enable_browser_fallback):
                    self.output_dir = Path(output_dir)

                def download_batch(self, papers, progress_callback=None, progress_file=None):
                    seen.extend(paper["title"] for paper in papers)
                    path = self.output_dir / "row2_theirs.pdf"
                    path.write_bytes(b"%PDF-1.4 theirs")
                    papers[0].update(pdf_downloaded=True, pdf_path=str(path), pdf_method="direct_pdf")
                    return {"success": 1, "failed": 0, "failed_papers": []}

            with patch("utils.fast_pdf_downloader.FastCascadePDFDownloader", FakeDownloader):
                DownloadAgent(project).run({"filtered_file": str(included), "download_folder": str(project / "pdfs")})
            report = read_json(project / "pdfs/download_report.json")
            self.assertEqual(seen, ["Theirs"])
            self.assertEqual((report["success"], report["provided_by_user"], report["obtained_automatically"]), (2, 1, 1))
            self.assertEqual(report["downloaded"][0]["pdf_source"], "upload")
            self.assertTrue(read_jsonl(included)[0]["pdf_downloaded"])


if __name__ == "__main__":
    unittest.main()


class PrimaryReasonTests(unittest.TestCase):
    def test_primary_access_denied_is_not_overridden_by_a_secondary_pmc_class(self):
        row = {"pdf_failure_class": "access_denied", "retrieval_status": "unavailable",
               "pdf_failure_classes": ["pmc_not_in_oa_cloud", "access_denied", "article_print_failed"]}
        self.assertEqual(mr.failure_reason(row, {})[0], "blocked")
        self.assertEqual(mr.failure_reason({"pdf_failure_class": "pdf_title_mismatch",
                                            "pdf_failure_classes": ["publisher_paywalled"]}, {})[0], "paywalled")


class CollectorLinkFieldsTests(unittest.TestCase):
    def test_pubmed_keeps_the_articles_own_pmcid_not_a_references(self):
        from searchers.pubmed import PubMedSearcher

        xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>38000001</PMID><Article>
          <ArticleTitle>A title</ArticleTitle><Journal><Title>J</Title><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue></Journal>
          </Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="pubmed">38000001</ArticleId>
          <ArticleId IdType="doi">10.1/abc</ArticleId><ArticleId IdType="pmc">PMC111</ArticleId></ArticleIdList>
          <ReferenceList><Reference><ArticleIdList><ArticleId IdType="pmc">PMC999</ArticleId></ArticleIdList></Reference></ReferenceList>
          </PubmedData></PubmedArticle><PubmedArticle><MedlineCitation><PMID>38000002</PMID><Article><ArticleTitle>B</ArticleTitle></Article></MedlineCitation>
          <PubmedData><ArticleIdList><ArticleId IdType="pubmed">38000002</ArticleId></ArticleIdList><ReferenceList><Reference><ArticleIdList>
          <ArticleId IdType="pmc">PMC999</ArticleId></ArticleIdList></Reference></ReferenceList></PubmedData></PubmedArticle></PubmedArticleSet>"""
        first, second = PubMedSearcher.__new__(PubMedSearcher)._parse_xml(xml)
        self.assertEqual(first["pmcid"], "PMC111")
        self.assertNotIn("pmcid", second)


class RelatedTitleTests(unittest.TestCase):
    def test_doi_on_the_front_page_wins_over_a_related_papers_title_words(self):
        mine = {"title": "Physicians Outperform Large Language Models in Pediatric Discharge Summary Generation", "doi": "10.1542/hpeds.2025-008569"}
        related = {"title": "Physician- and Large Language Model-Generated Hospital Discharge Summaries", "doi": "10.1001/jamainternmed.2025.0821"}
        candidates = [mr.Candidate("a" * 64, mine["title"], mine["doi"], "mine"), mr.Candidate("b" * 64, related["title"], related["doi"], "related")]
        content = make_pdf("Hospital Pediatrics", mine["title"], f"https://doi.org/{mine['doi']}", "Abstract",
                           "Discharge summaries condense the hospital course for the next clinician.",
                           "We evaluated a large language model against physician-written summaries.")
        outcome = mr.match_pdf(content, candidates, [mine["title"], related["title"]])
        self.assertEqual((outcome.status, outcome.retry_id, outcome.basis), ("matched", "a" * 64, "doi_on_page"))
        no_doi = make_pdf("Hospital Pediatrics", mine["title"], "Abstract",
                          "We evaluated a large language model against physician-written hospital discharge summaries.")
        outcome = mr.match_pdf(no_doi, candidates, [mine["title"], related["title"]])
        self.assertEqual((outcome.status, outcome.retry_id, outcome.basis), ("matched", "a" * 64, "title"))
