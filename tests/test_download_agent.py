import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.download_agent import DownloadAgent
from reviewpilot_core.project_store import read_json, read_jsonl


class DownloadAgentTests(unittest.TestCase):
    def test_run_uses_fast_pdf_downloader_batch_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            filtered_dir = project_dir / "filtered"
            filtered_dir.mkdir(parents=True)
            included_file = filtered_dir / "included_papers.jsonl"
            included_file.write_text(
                json.dumps({"id": "fast", "title": "Fast PDF", "url": "https://example.test/fast.pdf"}) + "\n",
                encoding="utf-8",
            )
            calls = []

            class FakeFastDownloader:
                def __init__(self, *, email, output_dir, enable_browser_fallback):
                    self.output_dir = Path(output_dir)
                    calls.append(("init", email, self.output_dir, enable_browser_fallback))

                def download_batch(self, papers, progress_callback=None, progress_file=None):
                    calls.append(("download_batch", [paper["title"] for paper in papers], progress_file))
                    pdf_path = self.output_dir / "row1_fast_2026_Fast_PDF_fast.pdf"
                    pdf_path.write_bytes(b"%PDF-1.4\n")
                    papers[0]["pdf_downloaded"] = True
                    papers[0]["pdf_path"] = str(pdf_path)
                    papers[0]["pdf_method"] = "direct_pdf"
                    return {
                        "total": 1,
                        "success": 1,
                        "failed": 0,
                        "by_method": {"direct_pdf": 1},
                        "downloaded": [{"title": "Fast PDF", "method": "direct_pdf", "path": str(pdf_path)}],
                        "failed_papers": [],
                    }

            with patch("utils.fast_pdf_downloader.FastCascadePDFDownloader", FakeFastDownloader):
                result = DownloadAgent(project_dir).run({"filtered_file": str(included_file)})

        self.assertEqual(result["success"], 1)
        self.assertEqual(calls[0][0], "init")
        self.assertEqual(calls[0][3], True)
        self.assertEqual(calls[1][0], "download_batch")

    def test_html_saved_with_pdf_extension_is_not_marked_downloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            filtered_dir = project_dir / "filtered"
            filtered_dir.mkdir(parents=True)
            included_file = filtered_dir / "included_papers.jsonl"
            included_file.write_text(
                json.dumps({"id": "html", "title": "HTML Landing Page", "url": "https://example.test/article"}) + "\n",
                encoding="utf-8",
            )

            class FakeFastDownloader:
                def __init__(self, *, email, output_dir, enable_browser_fallback):
                    self.output_dir = Path(output_dir)

                def download_batch(self, papers, progress_callback=None, progress_file=None):
                    (self.output_dir / "row1_pubmed_2026_HTML_Landing_Page_html.pdf").write_bytes(b"<!DOCTYPE html><html></html>")
                    return {"total": 1, "success": 1, "failed": 0, "failed_papers": []}

            with patch("utils.fast_pdf_downloader.FastCascadePDFDownloader", FakeFastDownloader):
                DownloadAgent(project_dir).run({"filtered_file": str(included_file)})

            report = read_json(project_dir / "pdfs" / "download_report.json")
            included = read_jsonl(included_file)

        self.assertEqual(report["success"], 0)
        self.assertEqual(report["failed"], 1)
        self.assertFalse(included[0]["pdf_downloaded"])
        self.assertTrue(included[0]["web_search_fallback_pending"])

    def test_run_writes_download_report_and_marks_paywalled_fallback_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            filtered_dir = project_dir / "filtered"
            filtered_dir.mkdir(parents=True)
            included_file = filtered_dir / "included_papers.jsonl"
            included_file.write_text(
                "\n".join(
                    [
                        json.dumps({"id": "keep", "title": "Open PDF", "url": "https://example.test/open.pdf"}),
                        json.dumps({"id": "closed", "title": "Closed PDF", "doi": "10.1000/closed"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            class FakeFastDownloader:
                def __init__(self, *, email, output_dir, enable_browser_fallback):
                    self.output_dir = Path(output_dir)

                def download_batch(self, papers, progress_callback=None, progress_file=None):
                    pdf_path = self.output_dir / "row1_openalex_2026_Open_PDF_keep.pdf"
                    pdf_path.write_bytes(b"%PDF-1.4\n")
                    papers[0]["pdf_downloaded"] = True
                    papers[0]["pdf_path"] = str(pdf_path)
                    return {
                        "total": 2,
                        "success": 1,
                        "failed": 1,
                        "skipped": 0,
                        "failed_papers": [
                            {"id": "closed", "title": "Closed PDF", "doi": "10.1000/closed", "failure_class": "publisher_paywalled"}
                        ],
                    }

            with patch("utils.fast_pdf_downloader.FastCascadePDFDownloader", FakeFastDownloader):
                result = DownloadAgent(project_dir).run({"filtered_file": str(included_file)})

            report = read_json(project_dir / "pdfs" / "download_report.json")
            included = read_jsonl(included_file)
            first_pdf_exists = Path(included[0]["pdf_path"]).exists()

        self.assertEqual(result["status"], "download_done")
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(report["success"], 1)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["subscribed_papers"][0]["title"], "Closed PDF")
        self.assertEqual(report["web_search_fallback_candidates"][0]["failure_class"], "publisher_paywalled")
        self.assertTrue(included[0]["pdf_downloaded"])
        self.assertTrue(first_pdf_exists)
        self.assertFalse(included[1]["pdf_downloaded"])
        self.assertEqual(included[1]["retrieval_status"], "subscribed_unavailable")
        self.assertTrue(included[1]["web_search_fallback_pending"])

    def test_run_records_unavailable_papers_separately_from_subscribed_papers(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            filtered_dir = project_dir / "filtered"
            filtered_dir.mkdir(parents=True)
            included_file = filtered_dir / "included_papers.jsonl"
            included_file.write_text(
                "\n".join(
                    [
                        json.dumps({"id": "closed", "title": "Closed PDF", "doi": "10.1000/closed"}),
                        json.dumps({"id": "missing", "title": "No Link PDF"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            class FakeFastDownloader:
                def __init__(self, *, email, output_dir, enable_browser_fallback):
                    self.output_dir = Path(output_dir)

                def download_batch(self, papers, progress_callback=None, progress_file=None):
                    return {
                        "total": 2,
                        "success": 0,
                        "failed": 2,
                        "failed_papers": [
                            {"id": "closed", "title": "Closed PDF", "doi": "10.1000/closed", "failure_class": "subscription_required"},
                            {"id": "missing", "title": "No Link PDF", "failure_class": "no_downloadable_pdf"},
                        ],
                    }

            with patch("utils.fast_pdf_downloader.FastCascadePDFDownloader", FakeFastDownloader):
                DownloadAgent(project_dir).run({"filtered_file": str(included_file)})

            report = read_json(project_dir / "pdfs" / "download_report.json")
            included = read_jsonl(included_file)

        self.assertEqual(report["subscribed_papers"][0]["title"], "Closed PDF")
        self.assertEqual(report["unavailable_papers"][0]["title"], "No Link PDF")
        self.assertEqual(included[0]["retrieval_status"], "subscribed_unavailable")
        self.assertEqual(included[1]["retrieval_status"], "unavailable")
        self.assertTrue(included[1]["web_search_fallback_pending"])
        self.assertTrue(included[1]["web_search_fallback_eligible"])


if __name__ == "__main__":
    unittest.main()
