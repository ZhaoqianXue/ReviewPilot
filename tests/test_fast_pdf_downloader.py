import importlib.util
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path


class FakeResponse:
    def __init__(self, url, content=b"", headers=None, status_code=200, json_data=None):
        self.url = url
        self.content = content
        self.headers = headers or {}
        self.status_code = status_code
        self._json_data = json_data
        if json_data is not None and not content:
            content = json.dumps(json_data).encode("utf-8")
            self.content = content
        self.text = content.decode("utf-8", errors="ignore")

    def json(self):
        if self._json_data is not None:
            return self._json_data
        return json.loads(self.text)


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.get_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.response


class FakeSessionByUrl:
    def __init__(self, get_responses=None, head_responses=None):
        self.get_responses = get_responses or {}
        self.head_responses = head_responses or {}
        self.post_responses = {}
        self.get_calls = []
        self.head_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        response = self.get_responses[url]
        response.url = url
        return response

    def head(self, url, **kwargs):
        self.head_calls.append((url, kwargs))
        response = self.head_responses[url]
        response.url = url
        return response

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        response = self.post_responses[url]
        response.url = url
        return response


class FakeSequenceSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.get_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("Unexpected GET call")
        return self.responses.pop(0)


class FakeRaisingSession:
    def __init__(self, exc):
        self.exc = exc
        self.get_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        raise self.exc


class Slow429SemanticScholarSession:
    def __init__(self):
        self.get_calls = []
        self.lock = threading.Lock()

    def get(self, url, **kwargs):
        with self.lock:
            self.get_calls.append((url, kwargs))
        time.sleep(0.05)
        return FakeResponse(
            url,
            content=b'{"code": 429}',
            headers={"content-type": "application/json"},
            status_code=429,
        )


def make_text_pdf_bytes(text):
    escaped = (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\n", ") Tj T* (")
    )
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET\n".encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
    ]
    parts = [b"%PDF-1.4\n"]
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(sum(len(part) for part in parts))
        parts.append(f"{index} 0 obj\n".encode("ascii"))
        parts.append(obj)
        parts.append(b"\nendobj\n")
    xref_offset = sum(len(part) for part in parts)
    parts.append(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    parts.append(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        parts.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    parts.append(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return b"".join(parts)


class FastPdfDownloaderTests(unittest.TestCase):
    def test_fast_downloader_defaults_to_eight_batch_workers(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        old_value = os.environ.pop("REVIEWPILOT_FAST_PDF_WORKERS", None)
        try:
            downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
            self.assertEqual(downloader.batch_workers, 8)
        finally:
            if old_value is not None:
                os.environ["REVIEWPILOT_FAST_PDF_WORKERS"] = old_value

    def test_fast_downloader_default_browser_profile_is_run_scoped(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        first = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        second = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        self.assertNotEqual(first.browser_user_data_dir, second.browser_user_data_dir)
        self.assertIn("runs", first.browser_user_data_dir.parts)
        self.assertIn("runs", second.browser_user_data_dir.parts)

    def test_fast_downloader_preserves_explicit_browser_profile_dir(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        explicit_dir = Path("/tmp/reviewpilot-test-pdfs/explicit-profile")
        downloader = FastCascadePDFDownloader(
            output_dir=Path("/tmp/reviewpilot-test-pdfs"),
            browser_user_data_dir=explicit_dir,
        )

        self.assertEqual(downloader.browser_user_data_dir, explicit_dir)

    def test_fast_downloader_curl_transport_includes_safari17(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        self.assertIn("safari17_0", downloader.curl_cffi_impersonates)

    def test_create_pdf_downloader_does_not_default_to_example_unpaywall_email(self):
        from utils.pdf_downloader import create_pdf_downloader

        old_values = {
            name: os.environ.pop(name, None)
            for name in ("UNPAYWALL_EMAIL", "REVIEWPILOT_API_EMAIL", "REVIEWPILOT_EMAIL")
        }
        try:
            downloader = create_pdf_downloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
            self.assertNotEqual(downloader.email, "research@example.com")
            self.assertEqual(downloader.email, "")
        finally:
            for name, value in old_values.items():
                if value is not None:
                    os.environ[name] = value

    def test_semantic_scholar_cache_path_is_shared_across_runs(self):
        from utils.fast_pdf_downloader import shared_semantic_scholar_cache_path

        old_value = os.environ.pop("SEMANTIC_SCHOLAR_CACHE", None)
        try:
            self.assertEqual(
                shared_semantic_scholar_cache_path(),
                Path(".cache/semantic_scholar_open_access.json"),
            )
        finally:
            if old_value is not None:
                os.environ["SEMANTIC_SCHOLAR_CACHE"] = old_value

    def test_semantic_scholar_cache_path_honors_environment_override(self):
        from utils.fast_pdf_downloader import shared_semantic_scholar_cache_path

        old_value = os.environ.get("SEMANTIC_SCHOLAR_CACHE")
        try:
            os.environ["SEMANTIC_SCHOLAR_CACHE"] = "/tmp/reviewpilot-s2-cache.json"
            self.assertEqual(
                shared_semantic_scholar_cache_path(),
                Path("/tmp/reviewpilot-s2-cache.json"),
            )
        finally:
            if old_value is None:
                os.environ.pop("SEMANTIC_SCHOLAR_CACHE", None)
            else:
                os.environ["SEMANTIC_SCHOLAR_CACHE"] = old_value

    def test_extracts_static_pdf_urls_from_meta_and_anchor_tags(self):
        from utils.fast_pdf_downloader import extract_static_pdf_urls

        html = """
        <html>
          <head>
            <meta name="citation_pdf_url" content="/articles/example.pdf">
          </head>
          <body>
            <a href="../download/full.pdf?download=1">PDF</a>
            <a href="/supplement/table.csv">Data</a>
          </body>
        </html>
        """

        urls = extract_static_pdf_urls(html, "https://journal.example.com/papers/abc/index.html")

        self.assertEqual(
            urls[:2],
            [
                "https://journal.example.com/articles/example.pdf",
                "https://journal.example.com/papers/download/full.pdf?download=1",
            ],
        )

    def test_extracts_static_pdf_urls_keeps_download_pdf_endpoint(self):
        from utils.fast_pdf_downloader import extract_static_pdf_urls

        html = """
        <html>
          <head>
            <meta name="citation_pdf_url" content="/journal/download_pdf.php?doi=10.4014/jmb.2511.11050">
          </head>
          <body>
            <a href="/journal/download_pdf.php?doi=10.4014/jmb.2511.11050">Download PDF</a>
          </body>
        </html>
        """

        self.assertEqual(
            extract_static_pdf_urls(html, "https://www.jmb.or.kr/journal/view.html"),
            ["https://www.jmb.or.kr/journal/download_pdf.php?doi=10.4014/jmb.2511.11050"],
        )

    def test_fast_downloader_does_not_invoke_selenium_for_html_response(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class TrackingDownloader(FastCascadePDFDownloader):
            selenium_called = False

            def _try_selenium_download(self, url, title, paper_id=None):
                self.selenium_called = True
                return None

        downloader = TrackingDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        downloader.session = FakeSession(
            FakeResponse(
                "https://publisher.example.com/article",
                content=b"<!DOCTYPE html><html><body>No PDF</body></html>",
                headers={"content-type": "text/html"},
            )
        )

        result = downloader._download_pdf(
            "https://publisher.example.com/article",
            "No PDF paper",
            "static_html",
            "PTEST",
        )

        self.assertIsNone(result)
        self.assertFalse(downloader.selenium_called)

    def test_fast_downloader_rejects_html_pdf_link_when_title_does_not_match_pdf_text(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        title = (
            "Development of Large Language Model Specialized into Microbiome Datasets: "
            "an Application of Self-Evaluation and Scoring Comparison with Conventional "
            "Natural Language Processing Markers."
        )
        article_url = "https://journal.example.com/articles/10.4014/jmb.2511.11050"
        attachment_url = "https://journal.example.com/articles/table.pdf"
        html = b"""
        <!DOCTYPE html>
        <html><body><a class="download-pdf" href="/articles/table.pdf">PDF</a></body></html>
        """
        attachment_pdf = make_text_pdf_bytes(
            "J. Microbiol. Biotechnol. 2026. 36: e2511050\n"
            "https://doi.org/10.4014/jmb.2511.11050\n"
            "Table 1. Rubric dimensions and definitions for human expert evaluation "
            "of large language model responses in microbiome research."
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = FastCascadePDFDownloader(output_dir=Path(tmp_dir))
            downloader.session = FakeSessionByUrl(
                get_responses={
                    article_url: FakeResponse(article_url, content=html, headers={"content-type": "text/html"}),
                    attachment_url: FakeResponse(
                        attachment_url,
                        content=attachment_pdf,
                        headers={"content-type": "application/pdf"},
                    ),
                }
            )

            result = downloader._download_pdf(article_url, title, "doi_static_html", "PTEST")

            self.assertIsNone(result)
            self.assertEqual(downloader._last_failure_class, "pdf_title_mismatch")
            self.assertEqual(list(Path(tmp_dir).glob("*.pdf")), [])

    def test_pdf_title_match_rejects_body_token_overlap_when_front_matter_title_differs(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        expected_title = (
            "Development of Large Language Model Specialized into Microbiome Datasets: "
            "an Application of Self-Evaluation and Scoring Comparison with Conventional "
            "Natural Language Processing Markers."
        )
        wrong_pdf = make_text_pdf_bytes(
            "A benchmark for large language models in bioinformatics\n"
            "Varuni Sarwal, Gaia Andreoletti, Viorel Munteanu\n"
            + ("Unrelated front matter text. " * 160)
            + "This benchmark discusses datasets, model evaluation, scoring, natural "
            "language processing, and development of bioinformatics systems."
        )
        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        matches, detail = downloader._pdf_title_match_result(wrong_pdf, expected_title)

        self.assertFalse(matches)
        self.assertIn("front matter title token coverage", detail)

    def test_pdf_title_match_rejects_wrong_preprint_for_short_domain_title(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        expected_title = "A Review of Applying Large Language Models in Healthcare"
        wrong_pdf = make_text_pdf_bytes(
            "Adding layers of information to scRNA-seq data using pre-trained language models\n"
            "Sonia Maria Krissmer, Jonatan Menger, Johan Rollin\n"
            "Abstract\n"
            "Pre-trained language models promise to enrich analyses of single-cell data "
            "in biomedical literature and healthcare applications."
        )
        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        matches, detail = downloader._pdf_title_match_result(wrong_pdf, expected_title)

        self.assertFalse(matches)
        self.assertIn("front matter title token coverage", detail)

    def test_pdf_title_match_accepts_compacted_front_matter_title(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        expected_title = (
            "Survey on Reasoning Capabilities and Accessibility of Large "
            "Language Models Using Biology-related Questions"
        )
        compacted_pdf = make_text_pdf_bytes(
            "SurveyonReasoningCapabilitiesandAccessibilityofLarge"
            "LanguageModelsUsingBiology-relatedQuestions\n"
            "Michael Ackerman\nAbstract\n"
        )
        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        matches, detail = downloader._pdf_title_match_result(compacted_pdf, expected_title)

        self.assertTrue(matches)
        self.assertIsNone(detail)

    def test_pdf_title_match_accepts_short_compacted_front_matter_title(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        expected_title = "Foundation Model in Biomedicine"
        compacted_pdf = make_text_pdf_bytes(
            "FoundationModelinBiomedicine\n"
            "Xiangrui Liu, Yuanyuan Zhang\nAbstract\n"
        )
        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        matches, detail = downloader._pdf_title_match_result(compacted_pdf, expected_title)

        self.assertTrue(matches)
        self.assertIsNone(detail)

    def test_build_quality_audit_marks_matching_downloaded_pdf_ok(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "paper.pdf"
            title = "Improving large language model applications in biomedicine with retrieval augmented generation"
            pdf_path.write_bytes(make_text_pdf_bytes(f"{title}\nAbstract Introduction Methods Results References"))
            downloader = FastCascadePDFDownloader(output_dir=Path(tmpdir))

            audit = downloader.build_quality_audit([
                {
                    "paper_id": "P0072",
                    "title": title,
                    "doi": "10.1093/jamia/ocaf008",
                    "pdf_downloaded": True,
                    "pdf_path": str(pdf_path),
                    "pdf_method": "europepmc",
                }
            ])

            self.assertEqual(audit["summary"], {
                "total_downloaded": 1,
                "suspect_count": 0,
                "missing_count": 0,
            })
            self.assertEqual(audit["records"][0]["status"], "ok")
            self.assertFalse(audit["records"][0]["suspect"])

    def test_fast_downloader_accepts_html_pdf_link_when_title_matches_pdf_text(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        title = (
            "Development of Large Language Model Specialized into Microbiome Datasets: "
            "an Application of Self-Evaluation and Scoring Comparison with Conventional "
            "Natural Language Processing Markers."
        )
        article_url = "https://journal.example.com/articles/10.4014/jmb.2511.11050"
        pdf_url = "https://journal.example.com/articles/full.pdf"
        html = b"""
        <!DOCTYPE html>
        <html><body><a class="download-pdf" href="/articles/full.pdf">PDF</a></body></html>
        """
        full_text_pdf = make_text_pdf_bytes(
            title
            + "\nJ. Microbiol. Biotechnol. 2026. 36: e2511050\n"
            "This article evaluates natural language processing markers in microbiome datasets."
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = FastCascadePDFDownloader(output_dir=Path(tmp_dir))
            downloader.session = FakeSessionByUrl(
                get_responses={
                    article_url: FakeResponse(article_url, content=html, headers={"content-type": "text/html"}),
                    pdf_url: FakeResponse(
                        pdf_url,
                        content=full_text_pdf,
                        headers={"content-type": "application/pdf"},
                    ),
                }
            )

            result = downloader._download_pdf(article_url, title, "doi_static_html", "PTEST")

            self.assertIsNotNone(result)
            self.assertEqual(result.read_bytes(), full_text_pdf)
            self.assertEqual(downloader._last_success_class, "html_pdf_link")

    def test_pdf_endpoint_network_error_tries_curl_cffi_fallback(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class CurlFallbackDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.curl_calls = []

            def _download_pdf_with_curl_cffi(self, url, title, method, paper_id=None):
                self.curl_calls.append((url, title, method, paper_id))
                return Path("/tmp/reviewpilot-test-pdfs/curl.pdf")

        url = "https://www.jmb.or.kr/journal/download_pdf.php?doi=10.4014/jmb.2511.11050"
        downloader = CurlFallbackDownloader()
        downloader.session = FakeRaisingSession(ConnectionError("closed"))

        result = downloader._download_pdf(url, "JMB paper", "doi_static_html", "PTEST")

        self.assertEqual(result, Path("/tmp/reviewpilot-test-pdfs/curl.pdf"))
        self.assertEqual(downloader.curl_calls, [(url, "JMB paper", "doi_static_html", "PTEST")])

    def test_static_html_resolver_does_not_return_verified_article_page_when_pdf_candidates_are_supplements(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        title = (
            "Development of Large Language Model Specialized into Microbiome Datasets: "
            "an Application of Self-Evaluation and Scoring Comparison with Conventional "
            "Natural Language Processing Markers."
        )
        article_url = "https://journal.example.com/view.html?doi=10.4014/jmb.2511.11050"
        supplement_url = "https://pdf.example.com/JMB036--6953_Supple0.pdf"
        figure_url = "https://pdf.example.com/download.php?f_name=jmb-36-e2511050-f1.jpg"
        html = f"""
        <!DOCTYPE html>
        <html>
          <head><title>{title}</title></head>
          <body>
            <h1>{title}</h1>
            <a href="{supplement_url}">Supplementary PDF</a>
            <a href="{figure_url}">Figure download</a>
            <section>Abstract</section>
            <section>Introduction</section>
            <section>Methods</section>
            <section>Results</section>
            <section>References</section>
          </body>
        </html>
        """.encode("utf-8")

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        downloader.session = FakeSessionByUrl(
            get_responses={
                article_url: FakeResponse(article_url, content=html, headers={"content-type": "text/html"}),
            },
            head_responses={
                supplement_url: FakeResponse(
                    supplement_url,
                    headers={"content-type": "application/pdf"},
                ),
                figure_url: FakeResponse(
                    figure_url,
                    headers={"content-type": "image/jpeg"},
                ),
            },
        )

        result = downloader._try_static_html_pdf(article_url, title)

        self.assertIsNone(result)

    def test_fast_downloader_uses_browser_fallback_only_for_bot_blocked_html(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class BrowserFallbackDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=True,
                )
                self.browser_urls = []

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                self.browser_urls.append(url)
                return Path("/tmp/reviewpilot-test-pdfs/from-browser.pdf")

        downloader = BrowserFallbackDownloader()
        downloader.session = FakeSession(
            FakeResponse(
                "https://pmc.example.com/pdf",
                content=b"<html><title>Checking your browser - reCAPTCHA</title></html>",
                headers={"content-type": "text/html"},
            )
        )

        result = downloader._download_pdf("https://pmc.example.com/pdf", "Bot paper", "pmc", "PTEST")

        self.assertEqual(result, Path("/tmp/reviewpilot-test-pdfs/from-browser.pdf"))
        self.assertEqual(downloader.browser_urls, ["https://pmc.example.com/pdf"])
        self.assertEqual(downloader.browser_fallback_attempts, 1)

    def test_fast_downloader_uses_browser_fallback_for_403_pdf_html(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class BrowserFallbackDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=True,
                )
                self.browser_urls = []

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                self.browser_urls.append(url)
                return Path("/tmp/reviewpilot-test-pdfs/from-browser.pdf")

        downloader = BrowserFallbackDownloader()
        downloader.session = FakeSession(
            FakeResponse(
                "https://www.biorxiv.org/content/10.64898/example.full.pdf",
                content=b"<!DOCTYPE html><html><body>Access denied</body></html>",
                headers={"content-type": "text/html; charset=UTF-8"},
                status_code=403,
            )
        )

        result = downloader._download_pdf(
            "https://www.biorxiv.org/content/10.64898/example.full.pdf",
            "403 paper",
            "biorxiv",
            "PTEST",
        )

        self.assertEqual(result, Path("/tmp/reviewpilot-test-pdfs/from-browser.pdf"))
        self.assertEqual(
            downloader.browser_urls,
            ["https://www.biorxiv.org/content/10.64898/example.full.pdf"],
        )
        self.assertEqual(downloader.browser_fallback_attempts, 1)

    def test_pdf_endpoint_cloudflare_uses_curl_cffi_before_browser_fallback(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class CurlTransportDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=True,
                )
                self.curl_urls = []
                self.browser_urls = []

            def _download_pdf_with_curl_cffi(self, url, title, method, paper_id=None):
                self.curl_urls.append(url)
                self._last_success_class = "curl_cffi_pdf"
                return Path("/tmp/reviewpilot-test-pdfs/from-curl-cffi.pdf")

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                self.browser_urls.append(url)
                return Path("/tmp/reviewpilot-test-pdfs/from-browser.pdf")

        downloader = CurlTransportDownloader()
        downloader.session = FakeSession(
            FakeResponse(
                "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1111/jerd.13046",
                content=b"<!DOCTYPE html><html><title>Just a moment...</title>Cloudflare</html>",
                headers={"content-type": "text/html; charset=UTF-8"},
                status_code=403,
            )
        )

        result = downloader._download_pdf(
            "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1111/jerd.13046",
            "Wiley paper",
            "semantic_scholar",
            "P0022",
        )

        self.assertEqual(result, Path("/tmp/reviewpilot-test-pdfs/from-curl-cffi.pdf"))
        self.assertEqual(
            downloader.curl_urls,
            ["https://onlinelibrary.wiley.com/doi/pdfdirect/10.1111/jerd.13046"],
        )
        self.assertEqual(downloader.browser_urls, [])
        self.assertEqual(downloader._last_success_class, "curl_cffi_pdf")

    def test_sciencedirect_tdm_pdf_endpoint_does_not_use_browser_fallback(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class BrowserTrackingDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=True,
                )
                self.browser_urls = []

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                self.browser_urls.append(url)
                return Path("/tmp/reviewpilot-test-pdfs/browser.pdf")

        url = "https://www.sciencedirect.com/science/article/pii/S1386505626002030/pdfft"
        downloader = BrowserTrackingDownloader()
        downloader.session = FakeSession(
            FakeResponse(
                url,
                content=b"<!DOCTYPE html><html><head><meta name='tdm-reservation' content='1'><meta name='tdm-policy' content='https://www.elsevier.com/tdm/tdmrep-policy.json'></head></html>",
                headers={"content-type": "text/html; charset=UTF-8"},
                status_code=403,
            )
        )

        result = downloader._download_pdf(
            url,
            "Empowering open medium-sized generative language models",
            "publisher_elsevier",
            "P0012",
        )

        self.assertIsNone(result)
        self.assertEqual(downloader.browser_urls, [])
        self.assertEqual(downloader._last_failure_class, "pdf_endpoint_tdm_blocked")

    def test_jove_aws_waf_pdf_endpoint_does_not_use_browser_fallback(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class BrowserTrackingDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=True,
                )
                self.browser_urls = []

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                self.browser_urls.append(url)
                return Path("/tmp/reviewpilot-test-pdfs/browser.pdf")

        url = "https://app.jove.com/pdf/69390"
        downloader = BrowserTrackingDownloader()
        downloader.session = FakeSession(
            FakeResponse(
                url,
                content=b"<!DOCTYPE html><html><script>window.awsWafCookieDomainList=['jove.com']; window.gokuProps={};</script></html>",
                headers={"content-type": "text/html; charset=UTF-8"},
                status_code=202,
            )
        )

        result = downloader._download_pdf(
            url,
            "Few-Shot and Zero-Shot Biomedical Named Entity Recognition",
            "publisher_jove",
            "P0016",
        )

        self.assertIsNone(result)
        self.assertEqual(downloader.browser_urls, [])
        self.assertEqual(downloader._last_failure_class, "pdf_endpoint_waf")

    def test_techrxiv_pdf_endpoint_cloudflare_does_not_use_browser_fallback(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class BrowserTrackingDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=True,
                )
                self.browser_urls = []

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                self.browser_urls.append(url)
                return Path("/tmp/reviewpilot-test-pdfs/browser.pdf")

        url = "https://www.techrxiv.org/doi/pdf/10.36227/techrxiv.177223091.15657802"
        downloader = BrowserTrackingDownloader()
        downloader.session = FakeSession(
            FakeResponse(
                url,
                content=b"<!DOCTYPE html><html><title>Just a moment...</title>Cloudflare</html>",
                headers={"content-type": "text/html; charset=UTF-8"},
                status_code=403,
            )
        )

        result = downloader._download_pdf(
            url,
            "Multimodal Large Language Models in Biomedicine and Healthcare",
            "publisher_techrxiv",
            "P0087",
        )

        self.assertIsNone(result)
        self.assertEqual(downloader.browser_urls, [])
        self.assertEqual(downloader._last_failure_class, "pdf_endpoint_cloudflare")

    def test_techrxiv_verified_article_print_warms_pdf_challenge_first(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class TechrxivWarmupDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=True,
                )
                self.browser_urls = []

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                self.browser_urls.append((url, method))
                if "/doi/pdf/" in url:
                    return None
                if "/doi/full/" in url and any("/doi/pdf/" in seen_url for seen_url, _ in self.browser_urls):
                    self._last_success_class = "article_printable"
                    return Path("/tmp/reviewpilot-test-pdfs/techrxiv-print.pdf")
                return None

        url = "https://www.techrxiv.org/doi/full/10.36227/techrxiv.177223091.15657802/v1"
        downloader = TechrxivWarmupDownloader()
        downloader.session = FakeSession(
            FakeResponse(
                url,
                content=b"<!DOCTYPE html><html><title>Just a moment...</title>Cloudflare</html>",
                headers={"content-type": "text/html; charset=UTF-8"},
                status_code=403,
            )
        )

        result = downloader._download_pdf(
            url,
            "Multimodal Large Language Models in Biomedicine and Healthcare",
            "verified_article_print_pdf",
            "P0087",
        )

        self.assertEqual(result, Path("/tmp/reviewpilot-test-pdfs/techrxiv-print.pdf"))
        self.assertEqual(
            downloader.browser_urls,
            [
                (
                    "https://www.techrxiv.org/doi/pdf/10.36227/techrxiv.177223091.15657802",
                    "publisher_techrxiv",
                ),
                (url, "verified_article_print_pdf"),
            ],
        )

    def test_fast_downloader_prefers_wiley_doi_over_broad_journal_mapping(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class TrackingDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.download_calls = []

            def _download_pdf(self, url, title, method, paper_id=None):
                self.download_calls.append((method, url))
                if method == "publisher_wiley":
                    return Path("/tmp/reviewpilot-test-pdfs/wiley.pdf")
                return None

        downloader = TrackingDownloader()

        success, method, result = downloader.download({
            "paper_id": "P0022",
            "title": "Implications of large language models such as ChatGPT for dental medicine",
            "doi": "10.1111/jerd.13046",
            "journal": "Journal of Esthetic and Restorative Dentistry",
            "source": "openalex",
        })

        self.assertTrue(success)
        self.assertEqual(method, "publisher_wiley")
        self.assertEqual(result, "/tmp/reviewpilot-test-pdfs/wiley.pdf")
        self.assertIn(
            ("publisher_wiley", "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1111/jerd.13046"),
            downloader.download_calls,
        )

    def test_fast_downloader_extracts_ieee_arnumber_from_non_200_doi_redirect(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        downloader.session = FakeSequenceSession([
            FakeResponse(
                "https://ieeexplore.ieee.org/document/10819409/",
                content=b"",
                status_code=202,
            ),
        ])

        result = downloader._try_ieee("10.1109/access.2024.3524588", None)

        self.assertEqual(
            result,
            "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=10819409",
        )

    def test_fast_downloader_resolves_ieee_computer_society_pdf_from_doi(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        session = FakeSessionByUrl()
        session.post_responses["https://www.computer.org/csdl/api/v1/graphql"] = FakeResponse(
            "https://www.computer.org/csdl/api/v1/graphql",
            json_data={
                "data": {
                    "article": {
                        "id": "2fuM4qwDXY4",
                        "fno": "11475912",
                        "pubType": "trans",
                        "idPrefix": "tg",
                        "issueNum": "05",
                        "year": "2026",
                    }
                }
            },
        )
        downloader.session = session

        result = downloader._try_ieee("10.1109/TVCG.2026.3680620", None)

        self.assertEqual(
            result,
            "https://www.computer.org/csdl/api/v1/periodical/trans/tg/2026/05/11475912/2fuM4qwDXY4/download-article/pdf",
        )
        self.assertEqual(
            session.post_calls[0][1]["json"]["variables"],
            {"doi": "10.1109/TVCG.2026.3680620"},
        )

    def test_fast_downloader_resolves_ieee_computer_society_preprint_pdf_endpoint(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        session = FakeSessionByUrl()
        session.post_responses["https://www.computer.org/csdl/api/v1/graphql"] = FakeResponse(
            "https://www.computer.org/csdl/api/v1/graphql",
            json_data={
                "data": {
                    "article": {
                        "id": "2hbkL5JSDle",
                        "fno": "11554911",
                        "pubType": "mags",
                        "idPrefix": "cg",
                        "issueNum": "01",
                        "year": "5555",
                    }
                }
            },
        )
        downloader.session = session

        result = downloader._try_ieee("10.1109/MCG.2026.3701906", None)

        self.assertEqual(
            result,
            "https://www.computer.org/csdl/api/v1/periodical/mags/cg/5555/01/11554911/2hbkL5JSDle/download-article/pdf",
        )

    def test_fast_downloader_marks_ieee_computer_society_paywalled_article(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        session = FakeSessionByUrl()
        session.post_responses["https://www.computer.org/csdl/api/v1/graphql"] = FakeResponse(
            "https://www.computer.org/csdl/api/v1/graphql",
            json_data={
                "data": {
                    "article": {
                        "id": "2hbkL5JSDle",
                        "fno": "11554911",
                        "pubType": "mags",
                        "idPrefix": "cg",
                        "issueNum": "01",
                        "year": "5555",
                        "hasPdf": True,
                        "isOpenAccess": False,
                        "showBuyMe": True,
                    }
                }
            },
        )
        downloader.session = session

        self.assertIsNone(downloader._try_ieee("10.1109/MCG.2026.3701906", None))
        self.assertEqual(downloader._last_failure_class, "publisher_paywalled")

    def test_fast_downloader_downloads_ieee_computer_society_pdf_from_doi(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        title = "GenTouchVR: Generating a Touchable Virtual Reality Environment from a Single Image"
        pdf_url = "https://www.computer.org/csdl/api/v1/periodical/trans/tg/2026/05/11475912/2fuM4qwDXY4/download-article/pdf"
        session = FakeSessionByUrl(
            get_responses={
                pdf_url: FakeResponse(
                    pdf_url,
                    content=make_text_pdf_bytes(title),
                    headers={"content-type": "application/pdf"},
                    status_code=200,
                ),
            },
        )
        session.post_responses["https://www.computer.org/csdl/api/v1/graphql"] = FakeResponse(
            "https://www.computer.org/csdl/api/v1/graphql",
            json_data={
                "data": {
                    "article": {
                        "id": "2fuM4qwDXY4",
                        "fno": "11475912",
                        "pubType": "trans",
                        "idPrefix": "tg",
                        "issueNum": "05",
                        "year": "2026",
                    }
                }
            },
        )
        downloader = FastCascadePDFDownloader(output_dir=Path(tempfile.mkdtemp()))
        downloader.session = session

        success, method, result = downloader.download({
            "paper_id": "P0004",
            "title": title,
            "doi": "10.1109/TVCG.2026.3680620",
            "journal": "IEEE Transactions on Visualization and Computer Graphics",
            "source": "pubmed",
            "url": "https://pubmed.ncbi.nlm.nih.gov/41945823/",
        })

        self.assertTrue(success)
        self.assertEqual(method, "publisher_ieee")
        self.assertTrue(result.endswith(".pdf"))
        self.assertEqual(
            session.get_calls[0][1]["headers"]["Accept"],
            "application/pdf,*/*",
        )
        self.assertEqual(
            session.get_calls[0][1]["headers"]["Referer"],
            "https://www.computer.org/csdl/journal/tg/2026/05/11475912/2fuM4qwDXY4",
        )

    def test_fast_downloader_extracts_cureus_pdf_from_doi_html(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        pdf_url = "https://www.cureus.com/articles/138667-artificial-hallucinations-in-chatgpt-implications-in-scientific-writing.pdf"
        downloader.session = FakeSessionByUrl(
            get_responses={
                "https://doi.org/10.7759/cureus.35179": FakeResponse(
                    "https://www.cureus.com/articles/138667-artificial-hallucinations-in-chatgpt-implications-in-scientific-writing",
                    content=f"""
                    <html>
                      <head>
                        <meta name="citation_pdf_url" content="{pdf_url}">
                      </head>
                    </html>
                    """.encode("utf-8"),
                    headers={"content-type": "text/html"},
                ),
            },
            head_responses={
                pdf_url: FakeResponse(
                    pdf_url,
                    content=b"",
                    headers={"content-type": "application/pdf"},
                    status_code=200,
                ),
            },
        )

        result = downloader._try_cureus("10.7759/cureus.35179")

        self.assertEqual(result, pdf_url)

    def test_fast_downloader_prefers_asco_doi_over_informatics_substring(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class TrackingDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.download_calls = []

            def _download_pdf(self, url, title, method, paper_id=None):
                self.download_calls.append((method, url))
                if method == "publisher_asco":
                    return Path("/tmp/reviewpilot-test-pdfs/asco.pdf")
                return None

        downloader = TrackingDownloader()

        success, method, result = downloader.download({
            "paper_id": "P0007",
            "title": "Large Language Model-Based Classification of Case Report Abstracts",
            "doi": "10.1200/CCI-25-00386",
            "journal": "JCO clinical cancer informatics",
            "source": "pubmed",
        })

        self.assertTrue(success)
        self.assertEqual(method, "publisher_asco")
        self.assertEqual(result, "/tmp/reviewpilot-test-pdfs/asco.pdf")
        self.assertIn(
            ("publisher_asco", "https://ascopubs.org/doi/10.1200/CCI-25-00386"),
            downloader.download_calls,
        )

    def test_fast_downloader_allows_browser_print_only_for_real_asco_article(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        self.assertTrue(downloader._is_verified_article_page(
            url="https://publisher.example.com/doi/10.1200/CCI-25-00386",
            page_title="Large Language Model-Based Classification of Case Report Abstracts | JCO Clinical Cancer Informatics",
            page_html="<html><body><h1>Large Language Model-Based Classification of Case Report Abstracts</h1><section>Abstract</section><section>Introduction</section><section>Methods</section><section>Results</section><section>References</section></body></html>",
            expected_title="Large Language Model-Based Classification of Case Report Abstracts",
        ))
        self.assertFalse(downloader._is_verified_article_page(
            url="https://ascopubs.org/doi/pdf/10.1200/CCI-25-00386",
            page_title="Just a moment...",
            page_html="<html><body>Cloudflare challenge</body></html>",
            expected_title="Large Language Model-Based Classification of Case Report Abstracts",
        ))
        self.assertFalse(downloader._is_verified_article_page(
            url="https://ascopubs.org/doi/10.1200/CCI-25-00386",
            page_title="Large Language Model-Based Classification of Case Report Abstracts | JCO Clinical Cancer Informatics",
            page_html="""
                <html><body>
                  <h1>Large Language Model-Based Classification of Case Report Abstracts</h1>
                  <a href="#purchase-options">Get Access</a>
                  <section>Abstract</section>
                  <section>Introduction</section>
                  <section>Methods</section>
                  <section>Results</section>
                  <section>References</section>
                </body></html>
            """,
            expected_title="Large Language Model-Based Classification of Case Report Abstracts",
        ))

    def test_article_print_pdf_rejects_paywalled_access_page_even_when_title_matches(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        title = "Large Language Model-Based Classification of Case Report Abstracts"
        content = make_text_pdf_bytes(
            f"{title}\n"
            "Get Access\n"
            "View all available purchase options and get full access to this article.\n"
            "Abstract Introduction Methods Results Discussion References"
        )
        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        self.assertFalse(downloader._article_print_pdf_is_acceptable(content, title))
        self.assertEqual(downloader._last_failure_class, "article_print_paywalled")

    def test_article_print_pdf_rejects_ieee_purchase_details_page_even_when_title_matches(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        title = "LlymX: Multimodal LLM-Augmented XR for Context-Aware Information Access"
        content = make_text_pdf_bytes(
            f"{title}\n"
            "Abstract: Extended Reality and Large Language Models can support context-aware information access.\n"
            "Publisher: IEEE Cite This PDF\n"
            "Purchase Details PAYMENT OPTIONS VIEW PURCHASED DOCUMENTS\n"
            "Recommended for You About IEEE Xplore Contact Us Help"
        )
        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        self.assertFalse(downloader._article_print_pdf_is_acceptable(content, title))
        self.assertEqual(downloader._last_failure_class, "article_print_paywalled")

    def test_article_print_pdf_rejects_publisher_overview_page_even_when_title_matches(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        title = "The Large Language Models on Biomedical Data Analysis: A Survey"
        content = make_text_pdf_bytes(
            f"{title}\n"
            "Publisher: IEEE Cite This PDF All Authors 64 Cites in Papers 1841 FullText Views\n"
            "Abstract Document Sections I. Introduction II. Background of Large Language Model "
            "III. Applications of Large Language Models in Biomedical Data Analysis "
            "IV. Challenges V. Conclusion Authors Figures References Citations Keywords Metrics More Like This"
        )
        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        self.assertFalse(downloader._article_print_pdf_is_acceptable(content, title))
        self.assertEqual(downloader._last_failure_class, "article_print_incomplete")

    def test_extracts_article_preprint_doi_from_publisher_html(self):
        from utils.fast_pdf_downloader import _extract_preprint_dois_from_article_html

        html = """
        <html><body>
          <section data-type="preprint-version">
            <h2>Preprint Version</h2>
            <div>Preprint version available on medRxiv
              (<a href="https://doi.org/10.64898/2025.12.22.25342797">
                https://doi.org/10.64898/2025.12.22.25342797
              </a>).
            </div>
          </section>
        </body></html>
        """

        self.assertEqual(
            _extract_preprint_dois_from_article_html(html),
            ["10.64898/2025.12.22.25342797"],
        )

    def test_article_preprint_extraction_ignores_reference_list_preprints(self):
        from utils.fast_pdf_downloader import _extract_preprint_dois_from_article_html

        html = """
        <html><body>
          <section id="references">
            <h2>References</h2>
            <p>
              Xiao, Y. et al. CellAgent: an LLM-driven multi-agent framework
              for automated single-cell data analysis. Preprint at bioRxiv
              https://doi.org/10.1101/2024.05.13.593861 (2024).
            </p>
            <a data-track="click_references"
               aria-label="Google Scholar reference 40"
               href="http://scholar.google.com/scholar_lookup?doi=10.1101/2024.05.13.593861">
               Google Scholar
            </a>
          </section>
        </body></html>
        """

        self.assertEqual(_extract_preprint_dois_from_article_html(html), [])

    def test_article_preprint_pdf_runs_before_verified_article_print_fallback(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class ArticlePreprintBeforePrintDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.order = []

            def _try_direct_pdf_url(self, url):
                return None

            def _get_publisher_method(self, publisher, doi, url):
                return None

            def _try_static_html_pdf(self, url, title=None):
                return None

            def _try_unpaywall(self, doi):
                return None

            def _try_semantic_scholar(self, doi, title):
                return None

            def _try_arxiv(self, arxiv_id, title):
                return None

            def _try_biorxiv_medrxiv(self, doi, title):
                return None

            def _try_find_preprint(self, doi, title):
                return None

            def _try_pmc(self, pmid, doi):
                return None

            def _try_europe_pmc(self, pmid, doi):
                return None

            def _try_doi_redirect(self, doi):
                return None

            def _try_core(self, doi, title):
                return None

            def _try_article_preprint_pdf(self, paper):
                self.order.append("article_preprint_pdf")
                return "https://www.medrxiv.org/content/10.64898/2025.12.22.25342797.full.pdf"

            def _try_verified_article_print_pdf(self, paper):
                self.order.append("verified_article_print_pdf")
                return "https://ascopubs.org/doi/10.1200/CCI-25-00386"

            def _download_pdf(self, url, title, method, paper_id=None):
                if method == "article_preprint_pdf":
                    return Path("/tmp/reviewpilot-test-pdfs/article-preprint.pdf")
                return None

        downloader = ArticlePreprintBeforePrintDownloader()
        success, method, result = downloader.download({
            "paper_id": "P0007",
            "title": "Large Language Model-Based Classification of Case Report Abstracts",
            "doi": "10.1200/CCI-25-00386",
            "journal": "JCO Clinical Cancer Informatics",
        })

        self.assertTrue(success)
        self.assertEqual(method, "article_preprint_pdf")
        self.assertEqual(result, "/tmp/reviewpilot-test-pdfs/article-preprint.pdf")
        self.assertEqual(downloader.order, ["article_preprint_pdf"])

    def test_pmc_direct_oa_runs_before_article_preprint_pdf(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class PmcBeforeArticlePreprintDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.order = []

            def _try_direct_pdf_url(self, url):
                return None

            def _get_publisher_method(self, publisher, doi, url):
                return None

            def _try_static_html_pdf(self, url, title=None):
                return None

            def _try_unpaywall(self, doi):
                return None

            def _try_semantic_scholar(self, doi, title):
                return None

            def _try_arxiv(self, arxiv_id, title):
                return None

            def _try_biorxiv_medrxiv(self, doi, title):
                return None

            def _try_find_preprint(self, doi, title):
                return None

            def _try_pmc(self, pmid, doi):
                self.order.append("pmc")
                return "https://pmc.ncbi.nlm.nih.gov/articles/PMC12868943/pdf/jmb-36-e2511050.pdf"

            def _try_article_preprint_pdf(self, paper):
                self.order.append("article_preprint_pdf")
                return "https://www.medrxiv.org/content/10.1101/example.full.pdf"

            def _download_pdf(self, url, title, method, paper_id=None):
                if method == "pmc":
                    return Path("/tmp/reviewpilot-test-pdfs/pmc-direct.pdf")
                return None

        downloader = PmcBeforeArticlePreprintDownloader()
        success, method, result = downloader.download({
            "paper_id": "P0003",
            "title": "Development of Large Language Model Specialized into Microbiome Datasets",
            "doi": "10.4014/jmb.2511.11050",
            "pmid": "42248877",
            "journal": "Journal of Microbiology and Biotechnology",
        })

        self.assertTrue(success)
        self.assertEqual(method, "pmc")
        self.assertEqual(result, "/tmp/reviewpilot-test-pdfs/pmc-direct.pdf")
        self.assertEqual(downloader.order, ["pmc"])

    def test_pubmed_id_is_used_as_pmid_when_pmid_field_is_missing(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class PubmedIdAsPmidDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.seen_pmids = []

            def _try_direct_pdf_url(self, url):
                return None

            def _get_publisher_method(self, publisher, doi, url):
                return None

            def _try_static_html_pdf(self, url, title=None):
                return None

            def _try_unpaywall(self, doi):
                return None

            def _try_semantic_scholar(self, doi, title):
                return None

            def _try_arxiv(self, arxiv_id, title):
                return None

            def _try_biorxiv_medrxiv(self, doi, title):
                return None

            def _try_find_preprint(self, doi, title):
                return None

            def _try_article_preprint_pdf(self, paper):
                return None

            def _try_doi_redirect(self, doi):
                return None

            def _try_core(self, doi, title):
                return None

            def _try_pmc(self, pmid, doi):
                self.seen_pmids.append(pmid)
                if pmid == "42317858":
                    return "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/article.pdf"
                return None

            def _download_pdf(self, url, title, method, paper_id=None):
                if method == "pmc":
                    return Path("/tmp/reviewpilot-test-pdfs/pubmed-id-pmc.pdf")
                return None

        downloader = PubmedIdAsPmidDownloader()
        success, method, result = downloader.download({
            "paper_id": "P0001",
            "source": "pubmed",
            "id": "42317858",
            "title": "A Multi-Model LLM Consensus Framework",
            "doi": "",
            "url": "https://pubmed.ncbi.nlm.nih.gov/42317858/",
        })

        self.assertTrue(success)
        self.assertEqual(method, "pmc")
        self.assertEqual(result, "/tmp/reviewpilot-test-pdfs/pubmed-id-pmc.pdf")
        self.assertEqual(downloader.seen_pmids, ["42317858"])

    def test_pubmed_source_with_pmid_tries_pmc_before_semantic_scholar_for_no_doi(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class PubmedPmcPriorityDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.order = []

            def _try_direct_pdf_url(self, url):
                return None

            def _get_publisher_method(self, publisher, doi, url):
                return None

            def _try_static_html_pdf(self, url, title=None):
                return None

            def _try_unpaywall(self, doi):
                return None

            def _try_pmc(self, pmid, doi):
                self.order.append("pmc")
                return "https://pmc.ncbi.nlm.nih.gov/articles/PMC13274367/pdf/"

            def _try_semantic_scholar(self, doi, title):
                self.order.append("semantic_scholar")
                return "https://pdfs.semanticscholar.org/wrong/fallback.pdf"

            def _download_pdf(self, url, title, method, paper_id=None):
                if method == "pmc":
                    return Path("/tmp/reviewpilot-test-pdfs/pubmed-pmc.pdf")
                return None

        downloader = PubmedPmcPriorityDownloader()

        success, method, result = downloader.download({
            "paper_id": "P0001",
            "source": "pubmed",
            "id": "42317858",
            "title": "A Multi-Model LLM Consensus Framework",
            "doi": "",
            "url": "https://pubmed.ncbi.nlm.nih.gov/42317858/",
        })

        self.assertTrue(success)
        self.assertEqual(method, "pmc")
        self.assertEqual(result, "/tmp/reviewpilot-test-pdfs/pubmed-pmc.pdf")
        self.assertEqual(downloader.order, ["pmc"])

    def test_resolve_pmcid_uses_pubmed_efetch_when_idconv_lacks_recent_pmcid(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(
            email="lingyaol@usf.edu",
            output_dir=Path("/tmp/reviewpilot-test-pdfs"),
            sleep_func=lambda _: None,
        )
        downloader.session = FakeSequenceSession([
            FakeResponse(
                "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/",
                headers={"content-type": "application/json"},
                json_data={"records": [{"pmid": 42317858, "status": "error"}]},
            ),
            FakeResponse(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                content=b"""
                <PubmedArticleSet>
                  <PubmedArticle>
                    <PubmedData>
                      <ArticleIdList>
                        <ArticleId IdType="pubmed">42317858</ArticleId>
                        <ArticleId IdType="pmc">PMC13274367</ArticleId>
                      </ArticleIdList>
                    </PubmedData>
                  </PubmedArticle>
                </PubmedArticleSet>
                """,
                headers={"content-type": "text/xml"},
            ),
        ])

        self.assertEqual(downloader._resolve_pmcid("42317858", ""), "PMC13274367")

    def test_rsna_doi_uses_direct_pdf_template(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class RsnaDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.downloads = []

            def _try_direct_pdf_url(self, url):
                return None

            def _download_pdf(self, url, title, method, paper_id=None):
                self.downloads.append((method, url))
                if method == "publisher_rsna":
                    return Path("/tmp/reviewpilot-test-pdfs/rsna.pdf")
                return None

        downloader = RsnaDownloader()
        success, method, result = downloader.download({
            "paper_id": "P0005",
            "title": "Zero-shot Thoracic Oncologic History Generation for Radiologists",
            "doi": "10.1148/radiol.251581",
            "journal": "Radiology",
        })

        self.assertTrue(success)
        self.assertEqual(method, "publisher_rsna")
        self.assertEqual(result, "/tmp/reviewpilot-test-pdfs/rsna.pdf")
        self.assertIn(
            ("publisher_rsna", "https://pubs.rsna.org/doi/pdf/10.1148/radiol.251581"),
            downloader.downloads,
        )

    def test_classifies_common_download_failure_modes(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        self.assertEqual(
            downloader._classify_response_failure(
                "semantic_scholar",
                "https://api.semanticscholar.org/graph/v1/paper/DOI:x",
                FakeResponse(
                    "https://api.semanticscholar.org/graph/v1/paper/DOI:x",
                    content=b'{"code": 429}',
                    headers={"content-type": "application/json"},
                    status_code=429,
                ),
            ),
            "metadata_api_429",
        )
        self.assertEqual(
            downloader._classify_response_failure(
                "pmc",
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/",
                FakeResponse(
                    "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/",
                    content=b"<html><base href='https://www.google.com/recaptcha/challengepage/'></html>",
                    headers={"content-type": "text/html"},
                    status_code=200,
                ),
            ),
            "pmc_recaptcha",
        )
        self.assertEqual(
            downloader._classify_response_failure(
                "publisher_wiley",
                "https://onlinelibrary.wiley.com/doi/pdf/10.1111/example",
                FakeResponse(
                    "https://onlinelibrary.wiley.com/doi/pdf/10.1111/example",
                    content=b"<html><title>Just a moment...</title>Cloudflare</html>",
                    headers={"content-type": "text/html"},
                    status_code=403,
                ),
            ),
            "pdf_endpoint_cloudflare",
        )

    def test_primary_failure_class_prefers_download_blocker_over_metadata_429(self):
        from utils.fast_pdf_downloader import _primary_failure_class

        self.assertEqual(
            _primary_failure_class(["pdf_endpoint_tdm_blocked", "metadata_api_429"]),
            "pdf_endpoint_tdm_blocked",
        )
        self.assertEqual(
            _primary_failure_class(["non_pdf_html", "metadata_api_429"]),
            "non_pdf_html",
        )

    def test_semantic_scholar_uses_api_key_retries_429_and_caches_pdf_url(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "semantic_cache.json"
            sleeps = []
            downloader = FastCascadePDFDownloader(
                output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                semantic_scholar_api_key="test-key",
                semantic_scholar_cache_path=cache_path,
                semantic_scholar_max_retries=1,
                semantic_scholar_backoff_seconds=0.25,
                sleep_func=sleeps.append,
            )
            downloader.session = FakeSequenceSession([
                FakeResponse(
                    "https://api.semanticscholar.org/graph/v1/paper/DOI:10.1111%2Fjerd.13046",
                    content=b'{"code": 429}',
                    headers={"content-type": "application/json"},
                    status_code=429,
                ),
                FakeResponse(
                    "https://api.semanticscholar.org/graph/v1/paper/DOI:10.1111%2Fjerd.13046",
                    headers={"content-type": "application/json"},
                    json_data={"openAccessPdf": {"url": "https://example.org/paper.pdf"}},
                ),
            ])

            result = downloader._try_semantic_scholar("10.1111/jerd.13046", "Dental LLM paper")

            self.assertEqual(result, "https://example.org/paper.pdf")
            self.assertEqual(sleeps, [0.25])
            self.assertEqual(
                [call[1]["headers"]["x-api-key"] for call in downloader.session.get_calls],
                ["test-key", "test-key"],
            )

            cached_downloader = FastCascadePDFDownloader(
                output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                semantic_scholar_cache_path=cache_path,
            )
            cached_downloader.session = FakeSequenceSession([])

            self.assertEqual(
                cached_downloader._try_semantic_scholar("10.1111/jerd.13046", "Dental LLM paper"),
                "https://example.org/paper.pdf",
            )

    def test_semantic_scholar_api_key_runs_before_static_html_candidates(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class PriorityDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    semantic_scholar_api_key="test-key",
                )
                self.calls = []

            def _try_semantic_scholar(self, doi, title):
                self.calls.append("semantic_scholar")
                return "https://pdfs.semanticscholar.org/example/full.pdf"

            def _try_static_html_pdf(self, url, title=None):
                self.calls.append("static_html")
                return "https://journal.example.com/article"

            def _download_pdf(self, url, title, method, paper_id=None):
                self.calls.append(f"download:{method}")
                return Path(f"/tmp/reviewpilot-test-pdfs/{method}.pdf")

        downloader = PriorityDownloader()

        success, method, result = downloader.download({
            "paper_id": "PTEST",
            "title": "Semantic Scholar preferred paper",
            "doi": "10.5555/example",
            "journal": "",
        })

        self.assertTrue(success)
        self.assertEqual(method, "semantic_scholar")
        self.assertEqual(result, "/tmp/reviewpilot-test-pdfs/semantic_scholar.pdf")
        self.assertEqual(downloader.calls, ["semantic_scholar", "download:semantic_scholar"])

    def test_semantic_scholar_runs_before_verified_article_print_fallback(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class SemanticBeforePrintDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=True,
                )
                self.browser_calls = 0
                self.semantic_calls = 0

            def _try_semantic_scholar(self, doi, title):
                self.semantic_calls += 1
                return "https://pdfs.semanticscholar.org/example/full.pdf"

            def _download_pdf(self, url, title, method, paper_id=None):
                if method == "semantic_scholar":
                    return Path("/tmp/reviewpilot-test-pdfs/semantic-direct.pdf")
                return super()._download_pdf(url, title, method, paper_id)

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                self.browser_calls += 1
                self._last_success_class = "article_printable"
                return Path("/tmp/reviewpilot-test-pdfs/article-print.pdf")

        title = (
            "Development of Large Language Model Specialized into Microbiome Datasets: "
            "an Application of Self-Evaluation and Scoring Comparison with Conventional "
            "Natural Language Processing Markers."
        )
        doi = "10.4014/jmb.2511.11050"
        html = f"""
        <!DOCTYPE html>
        <html>
          <head><title>{title}</title></head>
          <body>
            <h1>{title}</h1>
            <section>Abstract</section>
            <section>Introduction</section>
            <section>Methods</section>
            <section>Results</section>
            <section>References</section>
          </body>
        </html>
        """.encode("utf-8")
        downloader = SemanticBeforePrintDownloader()
        downloader.session = FakeSessionByUrl(
            get_responses={
                f"https://doi.org/{doi}": FakeResponse(
                    f"https://doi.org/{doi}",
                    content=html,
                    headers={"content-type": "text/html"},
                ),
            }
        )

        success, method, result = downloader.download({
            "paper_id": "P0003",
            "title": title,
            "doi": doi,
            "journal": "",
        })

        self.assertTrue(success)
        self.assertEqual(method, "semantic_scholar")
        self.assertEqual(result, "/tmp/reviewpilot-test-pdfs/semantic-direct.pdf")
        self.assertEqual(downloader.semantic_calls, 1)
        self.assertEqual(downloader.browser_calls, 0)

    def test_pdf_endpoint_cloudflare_sets_domain_cooldown_and_skips_next_pdf_endpoint(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class CooldownDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=False,
                    domain_cooldown_seconds=300,
                    time_func=lambda: 100.0,
                )
                self.download_calls = []

            def _download_pdf(self, url, title, method, paper_id=None):
                self.download_calls.append(url)
                self._last_failure_class = "pdf_endpoint_cloudflare"
                self._register_domain_failure(url, "pdf_endpoint_cloudflare")
                return None

        downloader = CooldownDownloader()
        first = downloader._download_with_domain_policy(
            "https://blocked.example.com/doi/pdf/10.1234/one",
            "first",
            "publisher_example",
            "P1",
        )
        second = downloader._download_with_domain_policy(
            "https://blocked.example.com/doi/pdf/10.1234/two",
            "second",
            "publisher_example",
            "P2",
        )

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(downloader.download_calls, ["https://blocked.example.com/doi/pdf/10.1234/one"])
        self.assertEqual(downloader._last_failure_class, "domain_cooldown_skip")

    def test_pdf_endpoint_cooldown_does_not_skip_verified_article_page(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class CooldownDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=False,
                    domain_cooldown_seconds=300,
                    time_func=lambda: 100.0,
                )
                self.download_calls = []

            def _download_pdf(self, url, title, method, paper_id=None):
                self.download_calls.append((method, url))
                self._last_failure_class = "pdf_endpoint_cloudflare"
                return None

        downloader = CooldownDownloader()
        downloader._download_with_domain_policy(
            "https://dl.acm.org/doi/pdf/10.1145/one",
            "pdf endpoint",
            "publisher_acm",
            "P1",
        )
        downloader._download_with_domain_policy(
            "https://dl.acm.org/doi/10.1145/two",
            "article page",
            "verified_article_print_pdf",
            "P2",
        )

        self.assertEqual(
            downloader.download_calls,
            [
                ("publisher_acm", "https://dl.acm.org/doi/pdf/10.1145/one"),
                ("verified_article_print_pdf", "https://dl.acm.org/doi/10.1145/two"),
            ],
        )

    def test_unpaywall_pdf_candidates_only_returns_real_pdf_urls(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        downloader.session = FakeSequenceSession([
            FakeResponse(
                "https://api.unpaywall.org/v2/10.1093%2Fjamia%2Focaf008",
                headers={"content-type": "application/json"},
                json_data={
                    "best_oa_location": {
                        "url": "https://doi.org/10.1093/jamia/ocaf008",
                        "url_for_pdf": None,
                    },
                    "oa_locations": [
                        {"url": "https://academic.oup.com/jamia/article/doi/10.1093/jamia/ocaf008"},
                        {"url_for_pdf": "https://academic.oup.com/jamia/article-pdf/doi/10.1093/jamia/ocaf008/61442713/ocaf008.pdf"},
                        {"url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC11339493/pdf/ocae074.pdf"},
                    ],
                },
            )
        ])

        self.assertEqual(
            downloader._try_unpaywall_pdf_candidates("10.1093/jamia/ocaf008"),
            [
                "https://academic.oup.com/jamia/article-pdf/doi/10.1093/jamia/ocaf008/61442713/ocaf008.pdf",
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC11339493/pdf/ocae074.pdf",
            ],
        )

    def test_unpaywall_prefers_europe_pmc_render_for_pmc_pdf_locations(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        downloader.session = FakeSequenceSession([
            FakeResponse(
                "https://api.unpaywall.org/v2/10.1093%2Fbioinformatics%2Fbtae353",
                headers={"content-type": "application/json"},
                json_data={
                    "best_oa_location": {
                        "url_for_pdf": "https://academic.oup.com/bioinformatics/advance-article-pdf/doi/10.1093/bioinformatics/btae353/58064527/btae353.pdf",
                    },
                    "oa_locations": [
                        {
                            "url_for_pdf": "https://pmc.ncbi.nlm.nih.gov/articles/PMC11164829/pdf/btae353.pdf",
                        }
                    ],
                },
            )
        ])

        self.assertEqual(
            downloader._try_unpaywall("10.1093/bioinformatics/btae353"),
            "https://europepmc.org/articles/PMC11164829?pdf=render",
        )

    def test_unpaywall_closed_article_sets_publisher_paywalled_failure(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        downloader.session = FakeSequenceSession([
            FakeResponse(
                "https://api.unpaywall.org/v2/10.1038%2Fs41551-026-01634-6",
                headers={"content-type": "application/json"},
                json_data={
                    "is_oa": False,
                    "best_oa_location": None,
                    "oa_locations": [],
                },
            )
        ])

        self.assertIsNone(downloader._try_unpaywall("10.1038/s41551-026-01634-6"))
        self.assertEqual(downloader._last_failure_class, "publisher_paywalled")
        self.assertIn("not open access", downloader._last_failure_detail)

    def test_open_access_rescue_prioritizes_europe_pmc_render_from_unpaywall_pmc_url(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class CandidateDownloader(FastCascadePDFDownloader):
            def _try_europe_pmc(self, pmid, doi):
                return None

        downloader = CandidateDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        downloader.session = FakeSequenceSession([
            FakeResponse(
                "https://api.unpaywall.org/v2/10.1093%2Fbioinformatics%2Fbtae353",
                headers={"content-type": "application/json"},
                json_data={
                    "best_oa_location": {
                        "url_for_pdf": "https://academic.oup.com/bioinformatics/advance-article-pdf/doi/10.1093/bioinformatics/btae353/58064527/btae353.pdf",
                    },
                    "oa_locations": [
                        {
                            "url_for_pdf": "https://pmc.ncbi.nlm.nih.gov/articles/PMC11164829/pdf/btae353.pdf",
                        }
                    ],
                },
            )
        ])

        candidates = downloader._open_access_rescue_candidates({
            "paper_id": "P0075",
            "title": "KRAGEN: a knowledge graph-enhanced RAG framework for biomedical problem solving",
            "doi": "10.1093/bioinformatics/btae353",
        })

        self.assertEqual(
            candidates[:2],
            [
                "https://europepmc.org/articles/PMC11164829?pdf=render",
                "https://academic.oup.com/bioinformatics/advance-article-pdf/doi/10.1093/bioinformatics/btae353/58064527/btae353.pdf",
            ],
        )

    def test_open_access_rescue_includes_preprint_pdf_for_preprint_doi(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class PreprintRescueDownloader(FastCascadePDFDownloader):
            def _try_europe_pmc(self, pmid, doi):
                return None

            def _try_unpaywall_pdf_candidates(self, doi):
                return []

            def _try_biorxiv_medrxiv(self, doi, title):
                return f"https://www.biorxiv.org/content/{doi}.full.pdf"

        downloader = PreprintRescueDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        self.assertEqual(
            downloader._open_access_rescue_candidates({
                "paper_id": "P0027",
                "title": "Interpreting Omics Data Analysis with Large Language Models",
                "doi": "10.64898/2026.04.30.721768",
            }),
            ["https://www.biorxiv.org/content/10.64898/2026.04.30.721768.full.pdf"],
        )

    def test_open_access_rescue_bypasses_publisher_pdf_endpoint_cooldown(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class RescueDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    time_func=lambda: 100.0,
                )
                self.download_calls = []

            def _try_unpaywall_pdf_candidates(self, doi):
                return [
                    "https://academic.oup.com/jamia/article-pdf/doi/10.1093/jamia/ocaf008/61442713/ocaf008.pdf"
                ]

            def _try_europe_pmc(self, pmid, doi):
                return None

            def _download_pdf(self, url, title, method, paper_id=None):
                self.download_calls.append((url, method, paper_id))
                return Path("/tmp/reviewpilot-test-pdfs/rescued.pdf")

        downloader = RescueDownloader()
        downloader._register_domain_failure(
            "https://academic.oup.com/jamia/doi/pdf/10.1093/jamia/ocaf008",
            "pdf_endpoint_cloudflare",
        )
        paper = {
            "paper_id": "P0072",
            "title": "Improving large language model applications in biomedicine with retrieval-augmented generation",
            "doi": "10.1093/jamia/ocaf008",
            "pdf_downloaded": False,
        }
        results = {
            "success": 0,
            "failed": 1,
            "by_method": {},
            "downloaded": [],
            "failed_papers": [{"title": paper["title"], "doi": paper["doi"], "failure_class": "domain_cooldown_skip"}],
        }

        downloader._retry_failed_open_access_pdfs([paper], results, progress_file=None)

        self.assertTrue(paper["pdf_downloaded"])
        self.assertEqual(paper["pdf_method"], "open_access_rescue")
        self.assertEqual(results["success"], 1)
        self.assertEqual(results["failed"], 0)
        self.assertEqual(results["failed_papers"], [])
        self.assertEqual(
            downloader.download_calls,
            [
                (
                    "https://academic.oup.com/jamia/article-pdf/doi/10.1093/jamia/ocaf008/61442713/ocaf008.pdf",
                    "open_access_rescue",
                    "P0072",
                )
            ],
        )

    def test_open_access_rescue_retries_transient_candidate_generation_failure(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class FlakyCandidateDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.candidate_calls = 0

            def _open_access_rescue_candidates(self, paper):
                self.candidate_calls += 1
                if self.candidate_calls == 1:
                    return []
                return ["https://europepmc.org/articles/PMC11164829?pdf=render"]

            def _download_pdf(self, url, title, method, paper_id=None):
                return Path("/tmp/reviewpilot-test-pdfs/rescued.pdf")

        paper = {
            "paper_id": "P0113",
            "title": "KRAGEN: a knowledge graph-enhanced RAG framework",
            "doi": "10.1093/bioinformatics/btae353",
            "pdf_downloaded": False,
        }
        results = {
            "success": 0,
            "failed": 1,
            "by_method": {},
            "downloaded": [],
            "failed_papers": [{"title": paper["title"], "doi": paper["doi"], "failure_class": "domain_cooldown_skip"}],
        }

        downloader = FlakyCandidateDownloader()
        downloader._retry_failed_open_access_pdfs([paper], results, progress_file=None)

        self.assertEqual(downloader.candidate_calls, 2)
        self.assertTrue(paper["pdf_downloaded"])
        self.assertEqual(results["success"], 1)
        self.assertEqual(results["failed"], 0)

    def test_open_access_rescue_retries_transient_candidate_download_failure(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class FlakyDownloadDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.download_calls = 0

            def _open_access_rescue_candidates(self, paper):
                return ["https://www.biorxiv.org/content/10.64898/2026.04.30.721768.full.pdf"]

            def _download_pdf(self, url, title, method, paper_id=None):
                self.download_calls += 1
                if self.download_calls == 1:
                    return None
                return Path("/tmp/reviewpilot-test-pdfs/rescued.pdf")

        paper = {
            "paper_id": "P0014",
            "title": "Interpreting Omics Data Analysis with Large Language Models",
            "doi": "10.64898/2026.04.30.721768",
            "pdf_downloaded": False,
        }
        results = {
            "success": 0,
            "failed": 1,
            "by_method": {},
            "downloaded": [],
            "failed_papers": [{"title": paper["title"], "doi": paper["doi"], "failure_class": "article_print_failed"}],
        }

        downloader = FlakyDownloadDownloader()
        downloader._retry_failed_open_access_pdfs([paper], results, progress_file=None)

        self.assertEqual(downloader.download_calls, 2)
        self.assertTrue(paper["pdf_downloaded"])
        self.assertEqual(results["success"], 1)
        self.assertEqual(results["failed"], 0)

    def test_doi_prefix_resolver_overrides_broad_journal_substrings(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        self.assertEqual(downloader._resolve_publisher("10.3389/fcimb.2026.1773593", "Frontiers in Cellular and Infection Microbiology")["selected_publisher"], "frontiers")
        self.assertEqual(downloader._resolve_publisher("10.1186/s12859-023-05411-z", "Bioinformatics")["selected_publisher"], "bmc")
        self.assertEqual(downloader._resolve_publisher("10.1093/gigascience/giag015", "GigaScience")["selected_publisher"], "oxford")
        self.assertEqual(downloader._resolve_publisher("10.64898/2026.05.13.724985", "medRxiv preprint")["selected_publisher"], "biorxiv")
        self.assertEqual(downloader._resolve_publisher("10.1016/j.jbi.2026.105049", "Journal of biomedical informatics")["selected_publisher"], "elsevier")
        self.assertEqual(downloader._resolve_publisher("10.3233/SHTI260319", "Studies in health technology and informatics")["selected_publisher"], "ios")
        self.assertEqual(downloader._resolve_publisher("10.3791/69390", "Journal of visualized experiments : JoVE")["selected_publisher"], "jove")
        self.assertEqual(downloader._resolve_publisher("10.1109/bibm62325.2024.10822725", "IEEE International Conference on Bioinformatics and Biomedicine")["selected_publisher"], "ieee")

    def test_journal_detection_does_not_match_substrings_inside_words(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        self.assertEqual(
            downloader._detect_publisher_from_journal("Studies in health technology and informatics"),
            "ios",
        )
        self.assertEqual(
            downloader._detect_publisher_from_journal("International journal of medical informatics"),
            "elsevier",
        )
        self.assertEqual(
            downloader._detect_publisher_from_journal("IEEE International Conference on Bioinformatics and Biomedicine"),
            "ieee",
        )

    def test_oxford_publisher_method_is_available_for_oup_doi(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        publisher_method = downloader._get_publisher_method(
            "oxford",
            "10.1093/gigascience/giag015",
            None,
        )

        self.assertIsNotNone(publisher_method)

    def test_mdpi_doi_template_uses_mdpi_res_pdf_endpoint(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        self.assertEqual(
            downloader._try_mdpi("10.3390/info16100894", None),
            "https://mdpi-res.com/d_attachment/information/information-16-00894/article_deploy/information-16-00894.pdf",
        )
        self.assertEqual(
            downloader._try_mdpi("10.3390/life16040681", None),
            "https://mdpi-res.com/d_attachment/life/life-16-00681/article_deploy/life-16-00681.pdf",
        )
        self.assertEqual(
            downloader._try_mdpi("10.3390/multimedia2020006", None),
            "https://mdpi-res.com/d_attachment/multimedia/multimedia-02-00006/article_deploy/multimedia-02-00006.pdf",
        )

    def test_techrxiv_doi_template_strips_version_for_pdf_endpoint(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        self.assertEqual(
            downloader._resolve_publisher(
                "10.36227/techrxiv.177223091.15657802/v1",
                "",
            )["selected_publisher"],
            "techrxiv",
        )
        self.assertEqual(
            downloader._try_techrxiv("10.36227/techrxiv.177223091.15657802/v1"),
            "https://www.techrxiv.org/doi/pdf/10.36227/techrxiv.177223091.15657802",
        )

    def test_extracts_oxford_article_pdf_before_supplemental_files(self):
        from utils.fast_pdf_downloader import _extract_oxford_article_pdf_url

        html = """
        <html><body>
          <a href="https://oup.silverchair-cdn.com/oup/backfile/Content_public/Journal/gigascience/15/example/giag015_supplemental_file.pdf">Supplement</a>
          <a href="/gigascience/article-pdf/doi/10.1093/gigascience/giag015/66865299/giag015.pdf">PDF</a>
          <a href="https://oup.silverchair-cdn.com/oup/backfile/Content_public/Journal/gigascience/15/example/giag015_reviewer_1_report_original_submission.pdf">Reviewer report</a>
        </body></html>
        """

        result = _extract_oxford_article_pdf_url(
            html,
            "https://academic.oup.com/gigascience/article/doi/10.1093/gigascience/giag015/8475380",
        )

        self.assertEqual(
            result,
            "https://academic.oup.com/gigascience/article-pdf/doi/10.1093/gigascience/giag015/66865299/giag015.pdf",
        )

    def test_extracts_pmc_article_pdf_before_supplemental_files(self):
        from utils.fast_pdf_downloader import _extract_pmc_article_pdf_url

        html = """
        <html><body>
          <a href="/articles/PMC12868943/pdf/jmb-36-e2511050.pdf">PDF</a>
          <a href="/articles/instance/12868943/bin/jmb-36-e2511050-supple.pdf">Supplement</a>
        </body></html>
        """

        result = _extract_pmc_article_pdf_url(
            html,
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC12868943/",
            "PMC12868943",
        )

        self.assertEqual(
            result,
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC12868943/pdf/jmb-36-e2511050.pdf",
        )

    def test_pmc_uses_named_article_pdf_from_article_html(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class PmcNamedPdfDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.fetch_urls = []

            def _resolve_pmcid(self, pmid, doi):
                return "PMC12868943"

            def _fetch_article_html(self, url):
                self.fetch_urls.append(url)
                return (
                    url,
                    """
                    <html><body>
                      <a href="/articles/PMC12868943/pdf/jmb-36-e2511050.pdf">PDF</a>
                      <a href="/articles/instance/12868943/bin/jmb-36-e2511050-supple.pdf">Supplement</a>
                    </body></html>
                    """,
                )

        downloader = PmcNamedPdfDownloader()

        self.assertEqual(
            downloader._try_pmc("42248877", "10.4014/jmb.2511.11050"),
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC12868943/pdf/jmb-36-e2511050.pdf",
        )
        self.assertEqual(
            downloader.fetch_urls,
            ["https://pmc.ncbi.nlm.nih.gov/articles/PMC12868943/"],
        )

    def test_pmc_marks_ncbi_oa_non_open_access_without_hitting_article_page(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class PmcNonOpenAccessDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.fetch_urls = []

            def _resolve_pmcid(self, pmid, doi):
                return "PMC13274367"

            def _fetch_article_html(self, url):
                self.fetch_urls.append(url)
                raise AssertionError("non-OA PMC records must not hit the recaptcha-prone article page")

        downloader = PmcNonOpenAccessDownloader()
        oa_url = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id=PMC13274367"
        downloader.session = FakeSessionByUrl(
            get_responses={
                oa_url: FakeResponse(
                    oa_url,
                    content=b"""<?xml version="1.0" encoding="UTF-8"?>
                    <OA><error code="idIsNotOpenAccess">identifier is not open access</error></OA>""",
                    headers={"content-type": "text/xml"},
                ),
            },
        )

        self.assertIsNone(downloader._try_pmc("42317858", None))
        self.assertEqual(downloader._last_failure_class, "pmc_not_open_access")
        self.assertIn("not open access", downloader._last_failure_detail.lower())
        self.assertEqual(downloader.fetch_urls, [])

    def test_pmc_prefers_ncbi_oa_pdf_link_when_available(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class PmcOaPdfDownloader(FastCascadePDFDownloader):
            def _resolve_pmcid(self, pmid, doi):
                return "PMC12868943"

        downloader = PmcOaPdfDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        oa_url = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id=PMC12868943"
        downloader.session = FakeSessionByUrl(
            get_responses={
                oa_url: FakeResponse(
                    oa_url,
                    content=b"""<?xml version="1.0" encoding="UTF-8"?>
                    <OA>
                      <records>
                        <record id="PMC12868943">
                          <link format="tgz" href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/example.tar.gz"/>
                          <link format="pdf" href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_pdf/64/c7/jmb-36-e2511050.PMC12868943.pdf"/>
                        </record>
                      </records>
                    </OA>""",
                    headers={"content-type": "text/xml"},
                ),
            },
        )

        self.assertEqual(
            downloader._try_pmc("42248877", "10.4014/jmb.2511.11050"),
            "https://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_pdf/64/c7/jmb-36-e2511050.PMC12868943.pdf",
        )

    def test_ios_press_article_page_posts_download_pdf_form(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        title = "THERA-IE: An AI-Enabled System for Therapeutic Indication Identification and Extraction from Biomedical Literature."
        article_url = "https://ebooks.iospress.nl/doi/10.3233/SHTI260319"
        pdf_bytes = make_text_pdf_bytes(f"{title}\nAbstract\nMethods\nResults\nReferences")
        html = b"""
        <html><body>
          <form method="post" action="/Download/Pdf">
            <input type="hidden" name="id" value="78613" />
          </form>
        </body></html>
        """

        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = FastCascadePDFDownloader(output_dir=Path(tmp_dir))
            fake_session = FakeSessionByUrl(
                get_responses={
                    article_url: FakeResponse(article_url, content=html, headers={"content-type": "text/html"}),
                },
            )
            fake_session.post_responses = {
                "https://ebooks.iospress.nl/Download/Pdf": FakeResponse(
                    "https://ebooks.iospress.nl/Download/Pdf",
                    content=pdf_bytes,
                    headers={"content-type": "application/pdf"},
                )
            }
            downloader.session = fake_session

            result = downloader._download_pdf(article_url, title, "publisher_ios", "P0005")

            self.assertIsNotNone(result)
            self.assertEqual(result.read_bytes(), pdf_bytes)
            self.assertEqual(
                fake_session.post_calls,
                [
                    (
                        "https://ebooks.iospress.nl/Download/Pdf",
                        {
                            "data": {"id": "78613"},
                            "timeout": downloader.download_timeout,
                            "allow_redirects": True,
                            "headers": {"Referer": article_url},
                        },
                    )
                ],
            )

    def test_preprint_title_lookup_rejects_mismatched_candidate_title(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        downloader.session = FakeSequenceSession([
            FakeResponse(
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                headers={"content-type": "application/json"},
                json_data={
                    "resultList": {
                        "result": [
                            {
                                "title": "Rewriting protein alphabets with language models",
                                "doi": "10.1101/2026.01.01.123456",
                                "bookOrReportDetails": {"publisher": "bioRxiv"},
                            }
                        ]
                    }
                },
            )
        ])

        self.assertIsNone(
            downloader._try_biorxiv_medrxiv(
                "10.1016/j.drudis.2026.104654",
                "Natural language querying of biological databases with large language models.",
            )
        )

    def test_europe_pmc_uses_render_pdf_url_for_pmcid(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        downloader.session = FakeSequenceSession([
            FakeResponse(
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                headers={"content-type": "application/json"},
                json_data={
                    "resultList": {
                        "result": [
                            {
                                "isOpenAccess": "Y",
                                "pmcid": "PMC12005634",
                            }
                        ]
                    }
                },
            )
        ])

        self.assertEqual(
            downloader._try_europe_pmc(None, "10.1093/jamia/ocaf008"),
            "https://europepmc.org/articles/PMC12005634?pdf=render",
        )

    def test_europe_pmc_uses_render_pdf_url_when_pmcid_exists_without_oa_flag(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        downloader.session = FakeSequenceSession([
            FakeResponse(
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                headers={"content-type": "application/json"},
                json_data={
                    "resultList": {
                        "result": [
                            {
                                "pmcid": "PMC11339493",
                            }
                        ]
                    }
                },
            )
        ])

        self.assertEqual(
            downloader._try_europe_pmc(None, "10.1093/jamia/ocae074"),
            "https://europepmc.org/articles/PMC11339493?pdf=render",
        )

    def test_preprint_doi_tries_biorxiv_before_semantic_scholar_and_pmc(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class TrackingDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.order = []

            def _try_biorxiv_medrxiv(self, doi, title):
                self.order.append("biorxiv")
                return "https://www.biorxiv.org/content/10.64898/example.full.pdf"

            def _try_semantic_scholar(self, doi, title):
                self.order.append("semantic_scholar")
                return None

            def _try_pmc(self, pmid, doi):
                self.order.append("pmc")
                return None

            def _download_pdf(self, url, title, method, paper_id=None):
                return Path("/tmp/reviewpilot-test-pdfs/preprint.pdf") if method == "publisher_biorxiv" else None

        downloader = TrackingDownloader()
        success, method, _ = downloader.download({
            "paper_id": "P0006",
            "title": "Evaluating open LLMs for agentic analysis orchestration",
            "doi": "10.64898/2026.05.13.724985",
            "journal": "medRxiv",
        })

        self.assertTrue(success)
        self.assertEqual(method, "publisher_biorxiv")
        self.assertEqual(downloader.order, ["biorxiv"])

    def test_abstract_project_page_pdf_runs_before_ieee_paywall(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class TrackingDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.order = []

            def _try_abstract_link_pdf(self, paper):
                self.order.append("abstract_static_html")
                return "https://project.example.com/paper.pdf"

            def _try_ieee(self, doi, url):
                self.order.append("publisher_ieee")
                self._last_failure_class = "publisher_paywalled"
                return None

            def _download_pdf(self, url, title, method, paper_id=None):
                return Path("/tmp/reviewpilot-test-pdfs/project.pdf") if method == "abstract_static_html" else None

        downloader = TrackingDownloader()
        success, method, result = downloader.download({
            "paper_id": "P0002",
            "title": "SIAgent: Spatial Interaction Agent Via LLM-Powered Eye-Hand Motion Intent Understanding in VR",
            "doi": "10.1109/TVCG.2026.3686395",
            "journal": "IEEE transactions on visualization and computer graphics",
            "abstract": "Project page: https://project.example.com/SIAgent.html",
        })

        self.assertTrue(success)
        self.assertEqual(method, "abstract_static_html")
        self.assertEqual(result, "/tmp/reviewpilot-test-pdfs/project.pdf")
        self.assertEqual(downloader.order, ["abstract_static_html"])

    def test_preprint_title_lookup_runs_before_pmc_endpoint(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class TrackingDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.order = []

            def _try_semantic_scholar(self, doi, title):
                self.order.append("semantic_scholar")
                return None

            def _try_arxiv(self, arxiv_id, title):
                self.order.append("arxiv")
                return None

            def _try_biorxiv_medrxiv(self, doi, title):
                self.order.append("biorxiv")
                return "https://www.biorxiv.org/content/10.1101/example.full.pdf"

            def _try_pmc(self, pmid, doi):
                self.order.append("pmc")
                return None

            def _download_pdf(self, url, title, method, paper_id=None):
                return Path("/tmp/reviewpilot-test-pdfs/preprint-title.pdf") if method == "biorxiv" else None

        downloader = TrackingDownloader()
        success, method, _ = downloader.download({
            "paper_id": "P0002",
            "title": "Enhanced semantic classification of microbiome sample origins using large language models",
            "doi": "10.1093/gigascience/giag015",
            "journal": "GigaScience",
        })

        self.assertTrue(success)
        self.assertEqual(method, "biorxiv")
        self.assertEqual(downloader.order, ["semantic_scholar", "arxiv", "biorxiv"])

    def test_semantic_scholar_without_key_does_not_retry_429_and_sets_cooldown(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        with tempfile.TemporaryDirectory() as tmpdir:
            sleeps = []
            downloader = FastCascadePDFDownloader(
                output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                semantic_scholar_api_key="",
                semantic_scholar_cache_path=Path(tmpdir) / "no-key-cache.json",
                sleep_func=sleeps.append,
                time_func=lambda: 50.0,
            )
            downloader.session = FakeSequenceSession([
                FakeResponse(
                    "https://api.semanticscholar.org/graph/v1/paper/DOI:10.1111%2Fjerd.13046",
                    content=b'{"code": 429}',
                    headers={"content-type": "application/json"},
                    status_code=429,
                ),
            ])

            self.assertIsNone(downloader._try_semantic_scholar("10.1111/jerd.13046", "Dental LLM paper"))
            self.assertEqual(sleeps, [])
            self.assertEqual(len(downloader.session.get_calls), 1)
            self.assertEqual(downloader._last_failure_class, "metadata_api_429")

            downloader.session = FakeSequenceSession([])
            self.assertIsNone(downloader._try_semantic_scholar("10.7759/cureus.35179", "Other paper"))
            self.assertEqual(len(downloader.session.get_calls), 0)
            self.assertEqual(downloader._last_failure_class, "domain_cooldown_skip")

    def test_semantic_scholar_429_cooldown_is_serialized_across_workers(self):
        from concurrent.futures import ThreadPoolExecutor

        from utils.fast_pdf_downloader import DomainConcurrencyPolicy, FastCascadePDFDownloader

        policy = DomainConcurrencyPolicy(default_domain_concurrency=1, time_func=lambda: 100.0)
        downloader = FastCascadePDFDownloader(
            output_dir=Path("/tmp/reviewpilot-test-pdfs"),
            domain_policy=policy,
            semantic_scholar_api_key="",
        )
        downloader.session = Slow429SemanticScholarSession()

        dois = ["10.1109/example-one", "10.1109/example-two"]
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda doi: downloader._try_semantic_scholar(doi, "Example"), dois))

        self.assertEqual(len(downloader.session.get_calls), 1)

    def test_fast_downloader_skips_core_without_api_key(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        old_value = os.environ.pop("CORE_API_KEY", None)
        try:
            downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
            downloader.session = FakeSequenceSession([])

            self.assertIsNone(downloader._try_core("10.1002/ase.70262", "A slow CORE lookup"))
            self.assertEqual(downloader.session.get_calls, [])
        finally:
            if old_value is not None:
                os.environ["CORE_API_KEY"] = old_value

    def test_benchmark_runner_can_create_optimized_downloader(self):
        module_path = Path(".benchmark_step3_download/benchmark_step3_download.py")
        spec = importlib.util.spec_from_file_location("benchmark_step3_download", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        downloader = module.create_downloader(
            downloader_name="optimized",
            email="research@example.com",
            output_dir=Path("/tmp/reviewpilot-test-pdfs"),
            web_search_model="gpt-5-mini",
        )

        self.assertEqual(downloader.__class__.__name__, "FastCascadePDFDownloader")

    def test_production_pdf_downloader_factory_defaults_to_fast_downloader(self):
        from utils.pdf_downloader import create_pdf_downloader

        downloader = create_pdf_downloader(
            email="research@example.com",
            output_dir=Path("/tmp/reviewpilot-test-pdfs"),
            optimized=True,
        )
        try:
            self.assertEqual(downloader.__class__.__name__, "FastCascadePDFDownloader")
            self.assertTrue(downloader.enable_browser_fallback)
        finally:
            close = getattr(downloader, "close", None)
            if close:
                close()

        legacy = create_pdf_downloader(
            email="research@example.com",
            output_dir=Path("/tmp/reviewpilot-test-pdfs"),
            optimized=False,
        )
        self.assertEqual(legacy.__class__.__name__, "CascadePDFDownloader")

    def test_domain_aware_batch_serializes_same_domain_and_overlaps_different_domains(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class DomainTrackingDownloader(FastCascadePDFDownloader):
            active_by_domain = {}
            max_total_active = 0
            violations = []
            lock = threading.Lock()

            def download(self, paper):
                domain = self._paper_primary_domain(paper)
                with self.lock:
                    self.active_by_domain[domain] = self.active_by_domain.get(domain, 0) + 1
                    if self.active_by_domain[domain] > 1:
                        self.violations.append(domain)
                    type(self).max_total_active = max(type(self).max_total_active, sum(self.active_by_domain.values()))
                try:
                    time.sleep(0.05)
                    return True, "test", f"/tmp/{paper['paper_id']}.pdf"
                finally:
                    with self.lock:
                        self.active_by_domain[domain] -= 1

        DomainTrackingDownloader.active_by_domain = {}
        DomainTrackingDownloader.max_total_active = 0
        DomainTrackingDownloader.violations = []

        downloader = DomainTrackingDownloader(
            output_dir=Path("/tmp/reviewpilot-test-pdfs"),
            batch_workers=3,
            domain_concurrency=1,
            browser_concurrency=1,
        )
        papers = [
            {"paper_id": "P1", "title": "One", "url": "https://same.example.org/a"},
            {"paper_id": "P2", "title": "Two", "url": "https://same.example.org/b"},
            {"paper_id": "P3", "title": "Three", "url": "https://other.example.org/c"},
        ]

        results = downloader.download_batch(papers)

        self.assertEqual(results["success"], 3)
        self.assertEqual(DomainTrackingDownloader.violations, [])
        self.assertGreater(DomainTrackingDownloader.max_total_active, 1)

    def test_domain_aware_batch_shares_cooldown_across_worker_downloaders(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class CooldownBatchDownloader(FastCascadePDFDownloader):
            download_attempts = 0
            lock = threading.Lock()

            def download(self, paper):
                path = self._download_with_domain_policy(
                    paper["url"],
                    paper["title"],
                    "publisher_blocked",
                    paper["paper_id"],
                )
                if path:
                    return True, "publisher_blocked", str(path)
                return False, "none", self._last_failure_class

            def _download_pdf(self, url, title, method, paper_id=None):
                with self.lock:
                    type(self).download_attempts += 1
                self._last_failure_class = "pdf_endpoint_cloudflare"
                return None

        CooldownBatchDownloader.download_attempts = 0
        downloader = CooldownBatchDownloader(
            output_dir=Path("/tmp/reviewpilot-test-pdfs"),
            batch_workers=2,
            domain_concurrency=1,
            domain_cooldown_seconds=300,
            time_func=lambda: 100.0,
        )
        papers = [
            {
                "paper_id": "P1",
                "title": "Blocked one",
                "url": "https://blocked.example.org/doi/pdf/10.1234/one",
            },
            {
                "paper_id": "P2",
                "title": "Blocked two",
                "url": "https://blocked.example.org/doi/pdf/10.1234/two",
            },
        ]

        results = downloader.download_batch(papers)

        self.assertEqual(results["failed"], 2)
        self.assertEqual(CooldownBatchDownloader.download_attempts, 1)

    def test_fast_batch_does_not_resume_progress_by_default(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class FreshRunDownloader(FastCascadePDFDownloader):
            calls = 0
            lock = threading.Lock()

            def download(self, paper):
                with self.lock:
                    type(self).calls += 1
                return True, "fresh", f"/tmp/{paper['paper_id']}.pdf"

        FreshRunDownloader.calls = 0
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_file = Path(tmpdir) / "download_progress.jsonl"
            progress_file.write_text(
                json.dumps({
                    "doi": "10.1234/existing",
                    "title": "Existing paper",
                    "pdf_downloaded": True,
                    "pdf_path": "/tmp/old.pdf",
                    "pdf_method": "old",
                }) + "\n",
                encoding="utf-8",
            )

            downloader = FreshRunDownloader(
                output_dir=Path(tmpdir),
                batch_workers=1,
            )
            papers = [{
                "paper_id": "P1",
                "title": "Existing paper",
                "doi": "10.1234/existing",
                "url": "https://example.org/article",
            }]

            results = downloader.download_batch(papers, progress_file=str(progress_file))

        self.assertEqual(FreshRunDownloader.calls, 1)
        self.assertEqual(results["success"], 1)
        self.assertEqual(results["downloaded"][0]["method"], "fresh")
        self.assertEqual(papers[0]["pdf_path"], "/tmp/P1.pdf")

    def test_failed_batch_result_records_failure_detail_and_classes(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class FailureTelemetryDownloader(FastCascadePDFDownloader):
            def download(self, paper):
                self.last_failure_class = "domain_cooldown_skip"
                self.last_failure_detail = "sciencedirect.com cooled down after pdf_endpoint_tdm_blocked"
                self.last_method_timings = [
                    {"method": "publisher_elsevier", "failure_class": "pdf_endpoint_tdm_blocked"},
                    {"method": "semantic_scholar", "failure_class": "metadata_api_429"},
                    {"method": "doi_redirect", "failure_class": None},
                ]
                return False, "none", "All download methods failed"

            def _retry_failed_open_access_pdfs(self, papers, results, progress_file):
                return None

            def _retry_batch_article_print_failures(self, papers, results, progress_file):
                return None

        downloader = FailureTelemetryDownloader(
            output_dir=Path("/tmp/reviewpilot-test-pdfs"),
            batch_workers=1,
        )
        papers = [{
            "paper_id": "P0012",
            "title": "Empowering open medium-sized generative language models",
            "doi": "10.1016/j.ijmedinf.2026.106463",
            "url": "https://pubmed.ncbi.nlm.nih.gov/42107249/",
        }]

        results = downloader.download_batch(papers)

        self.assertEqual(results["failed"], 1)
        self.assertEqual(
            results["failed_papers"][0]["failure_detail"],
            "sciencedirect.com cooled down after pdf_endpoint_tdm_blocked",
        )
        self.assertEqual(
            results["failed_papers"][0]["failure_classes"],
            ["pdf_endpoint_tdm_blocked", "metadata_api_429"],
        )
        self.assertEqual(papers[0]["pdf_failure_class"], "domain_cooldown_skip")
        self.assertEqual(
            papers[0]["pdf_failure_classes"],
            ["pdf_endpoint_tdm_blocked", "metadata_api_429"],
        )

    def test_fast_batch_does_not_resume_even_when_env_requests_resume(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class FreshRunDownloader(FastCascadePDFDownloader):
            calls = 0
            lock = threading.Lock()

            def download(self, paper):
                with self.lock:
                    type(self).calls += 1
                return True, "fresh", f"/tmp/{paper['paper_id']}.pdf"

        old_value = os.environ.get("REVIEWPILOT_ENABLE_DOWNLOAD_RESUME")
        os.environ["REVIEWPILOT_ENABLE_DOWNLOAD_RESUME"] = "1"
        FreshRunDownloader.calls = 0
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                progress_file = Path(tmpdir) / "download_progress.jsonl"
                progress_file.write_text(
                    json.dumps({
                        "doi": "10.1234/existing",
                        "title": "Existing paper",
                        "pdf_downloaded": True,
                        "pdf_path": "/tmp/old.pdf",
                        "pdf_method": "old",
                    }) + "\n",
                    encoding="utf-8",
                )

                downloader = FreshRunDownloader(output_dir=Path(tmpdir), batch_workers=1)
                papers = [{
                    "paper_id": "P1",
                    "title": "Existing paper",
                    "doi": "10.1234/existing",
                    "url": "https://example.org/article",
                }]

                results = downloader.download_batch(papers, progress_file=str(progress_file))
        finally:
            if old_value is None:
                os.environ.pop("REVIEWPILOT_ENABLE_DOWNLOAD_RESUME", None)
            else:
                os.environ["REVIEWPILOT_ENABLE_DOWNLOAD_RESUME"] = old_value

        self.assertEqual(FreshRunDownloader.calls, 1)
        self.assertEqual(results["success"], 1)
        self.assertEqual(results["downloaded"][0]["method"], "fresh")
        self.assertEqual(papers[0]["pdf_path"], "/tmp/P1.pdf")

    def test_legacy_batch_does_not_resume_even_when_env_requests_resume(self):
        from utils.pdf_downloader import CascadePDFDownloader

        class FreshLegacyDownloader(CascadePDFDownloader):
            calls = 0

            def download(self, paper):
                type(self).calls += 1
                return True, "fresh", f"/tmp/{paper['paper_id']}.pdf"

        old_value = os.environ.get("REVIEWPILOT_ENABLE_DOWNLOAD_RESUME")
        os.environ["REVIEWPILOT_ENABLE_DOWNLOAD_RESUME"] = "1"
        FreshLegacyDownloader.calls = 0
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                progress_file = Path(tmpdir) / "download_progress.jsonl"
                progress_file.write_text(
                    json.dumps({
                        "doi": "10.1234/existing",
                        "title": "Existing paper",
                        "pdf_downloaded": True,
                        "pdf_path": "/tmp/old.pdf",
                        "pdf_method": "old",
                    }) + "\n",
                    encoding="utf-8",
                )
                downloader = FreshLegacyDownloader(output_dir=Path(tmpdir))
                papers = [{
                    "paper_id": "P1",
                    "title": "Existing paper",
                    "doi": "10.1234/existing",
                    "url": "https://example.org/article",
                }]

                results = downloader.download_batch(papers, progress_file=str(progress_file))
        finally:
            if old_value is None:
                os.environ.pop("REVIEWPILOT_ENABLE_DOWNLOAD_RESUME", None)
            else:
                os.environ["REVIEWPILOT_ENABLE_DOWNLOAD_RESUME"] = old_value

        self.assertEqual(FreshLegacyDownloader.calls, 1)
        self.assertEqual(results["success"], 1)
        self.assertEqual(results["downloaded"][0]["method"], "fresh")
        self.assertEqual(papers[0]["pdf_path"], "/tmp/P1.pdf")

    def test_article_page_cloudflare_failure_does_not_cooldown_entire_domain(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class ArticleCooldownDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    domain_cooldown_seconds=300,
                    time_func=lambda: 100.0,
                )
                self.download_calls = []

            def _download_pdf(self, url, title, method, paper_id=None):
                self.download_calls.append(url)
                self._last_failure_class = "pdf_endpoint_cloudflare"
                return None

        downloader = ArticleCooldownDownloader()
        first = downloader._download_with_domain_policy(
            "https://ascopubs.org/doi/10.1200/CCI-25-00386",
            "article page",
            "publisher_asco",
            "P1",
        )
        second = downloader._download_with_domain_policy(
            "https://ascopubs.org/doi/10.1200/CCI-25-99999",
            "article page two",
            "publisher_asco",
            "P2",
        )

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(downloader.download_calls), 2)
        self.assertEqual(downloader._last_failure_class, "pdf_endpoint_cloudflare")

    def test_article_page_browser_failure_uses_isolated_retry(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class ArticlePrintRetryDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=True,
                )
                self.browser_calls = 0
                self.isolated_calls = 0

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                self.browser_calls += 1
                return None

            def _download_article_page_with_isolated_browser_retry(self, url, title, method, paper_id=None):
                self.isolated_calls += 1
                self._last_success_class = "article_printable_isolated"
                return Path("/tmp/reviewpilot-test-pdfs/isolated-article.pdf")

        downloader = ArticlePrintRetryDownloader()
        downloader.session = FakeSession(
            FakeResponse(
                "https://ascopubs.org/doi/10.1200/CCI-25-00386",
                content=b"<!DOCTYPE html><html><title>Just a moment...</title>Cloudflare</html>",
                headers={"content-type": "text/html; charset=UTF-8"},
                status_code=403,
            )
        )

        result = downloader._download_pdf(
            "https://ascopubs.org/doi/10.1200/CCI-25-00386",
            "Large Language Model-Based Classification of Case Report Abstracts",
            "verified_article_print_pdf",
            "P0007",
        )

        self.assertEqual(result, Path("/tmp/reviewpilot-test-pdfs/isolated-article.pdf"))
        self.assertEqual(downloader.browser_calls, 1)
        self.assertEqual(downloader.isolated_calls, 1)
        self.assertEqual(downloader._last_success_class, "article_printable_isolated")

    def test_verified_article_print_method_uses_browser_for_normal_html_article_page(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class ArticlePrintNormalHtmlDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=True,
                )
                self.browser_calls = 0

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                self.browser_calls += 1
                self._last_success_class = "article_printable"
                return Path("/tmp/reviewpilot-test-pdfs/article-print.pdf")

        downloader = ArticlePrintNormalHtmlDownloader()
        downloader.session = FakeSession(
            FakeResponse(
                "https://ascopubs.org/doi/10.1200/CCI-25-00386",
                content=b"<!DOCTYPE html><html><title>Case Report Abstracts</title><body>Abstract References</body></html>",
                headers={"content-type": "text/html; charset=UTF-8"},
                status_code=200,
            )
        )

        result = downloader._download_pdf(
            "https://ascopubs.org/doi/10.1200/CCI-25-00386",
            "Large Language Model-Based Classification of Case Report Abstracts",
            "verified_article_print_pdf",
            "P0007",
        )

        self.assertEqual(result, Path("/tmp/reviewpilot-test-pdfs/article-print.pdf"))
        self.assertEqual(downloader.browser_calls, 1)
        self.assertEqual(downloader._last_success_class, "article_printable")

    def test_doi_static_html_does_not_use_browser_print_for_verified_article_page(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class VerifiedArticlePrintDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=True,
                )
                self.browser_calls = 0

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                self.browser_calls += 1
                self._last_success_class = "article_printable"
                return Path("/tmp/reviewpilot-test-pdfs/verified-article-print.pdf")

        title = (
            "Development of Large Language Model Specialized into Microbiome Datasets: "
            "an Application of Self-Evaluation and Scoring Comparison with Conventional "
            "Natural Language Processing Markers."
        )
        html = f"""
        <!DOCTYPE html>
        <html>
          <head><title>{title}</title></head>
          <body>
            <h1>{title}</h1>
            <section>Abstract</section>
            <section>Introduction</section>
            <section>Methods</section>
            <section>Results</section>
            <section>References</section>
          </body>
        </html>
        """.encode("utf-8")
        url = "https://www.journal.example.com/journal/view.html?doi=10.4014/jmb.2511.11050"

        downloader = VerifiedArticlePrintDownloader()
        downloader.session = FakeSession(
            FakeResponse(
                url,
                content=html,
                headers={"content-type": "text/html; charset=UTF-8"},
                status_code=200,
            )
        )

        result = downloader._download_pdf(url, title, "doi_static_html", "PTEST")

        self.assertIsNone(result)
        self.assertEqual(downloader.browser_calls, 0)

    def test_pubmed_abstract_page_does_not_use_browser_print(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class PubmedAbstractDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=True,
                )
                self.browser_calls = 0

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                self.browser_calls += 1
                return Path("/tmp/reviewpilot-test-pdfs/pubmed-print.pdf")

        title = (
            "Development of Large Language Model Specialized into Microbiome Datasets: "
            "an Application of Self-Evaluation and Scoring Comparison with Conventional "
            "Natural Language Processing Markers."
        )
        url = "https://pubmed.ncbi.nlm.nih.gov/41605796/"
        html = f"""
        <!DOCTYPE html>
        <html>
          <head><title>{title}</title></head>
          <body>
            <h1>{title}</h1>
            <section>Abstract</section>
            <section>References</section>
          </body>
        </html>
        """.encode("utf-8")

        downloader = PubmedAbstractDownloader()
        downloader.session = FakeSession(
            FakeResponse(
                url,
                content=html,
                headers={"content-type": "text/html; charset=UTF-8"},
                status_code=200,
            )
        )

        result = downloader._download_pdf(url, title, "static_html", "PTEST")

        self.assertIsNone(result)
        self.assertEqual(downloader.browser_calls, 0)

    def test_pmc_recaptcha_does_not_use_browser_fallback(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class PmcRecaptchaDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=True,
                )
                self.browser_calls = 0

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                self.browser_calls += 1
                return Path("/tmp/reviewpilot-test-pdfs/pmc-browser.pdf")

        url = "https://pmc.ncbi.nlm.nih.gov/articles/PMC12868943/pdf/jmb-36-e2511050.pdf"
        downloader = PmcRecaptchaDownloader()
        downloader.session = FakeSession(
            FakeResponse(
                url,
                content=b"<html><base href='https://www.google.com/recaptcha/challengepage/'></html>",
                headers={"content-type": "text/html; charset=utf-8"},
                status_code=200,
            )
        )

        result = downloader._download_pdf(url, "PMC recaptcha paper", "pmc", "PTEST")

        self.assertIsNone(result)
        self.assertEqual(downloader.browser_calls, 0)
        self.assertEqual(downloader._last_failure_class, "pmc_recaptcha")

    def test_article_page_browser_and_isolated_failure_classifies_as_article_print_failed(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class ArticlePrintFailDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=True,
                )

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                return None

            def _download_article_page_with_isolated_browser_retry(self, url, title, method, paper_id=None):
                return None

        downloader = ArticlePrintFailDownloader()
        downloader.session = FakeSession(
            FakeResponse(
                "https://ascopubs.org/doi/10.1200/CCI-25-00386",
                content=b"<!DOCTYPE html><html><title>Just a moment...</title>Cloudflare</html>",
                headers={"content-type": "text/html; charset=UTF-8"},
                status_code=403,
            )
        )

        result = downloader._download_pdf(
            "https://ascopubs.org/doi/10.1200/CCI-25-00386",
            "Large Language Model-Based Classification of Case Report Abstracts",
            "verified_article_print_pdf",
            "P0007",
        )

        self.assertIsNone(result)
        self.assertEqual(downloader._last_failure_class, "article_print_failed")

    def test_batch_worker_defers_article_print_without_immediate_isolated_retry(self):
        from utils.fast_pdf_downloader import DomainConcurrencyPolicy, FastCascadePDFDownloader

        class BatchArticlePrintDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=True,
                    domain_policy=DomainConcurrencyPolicy(),
                )
                self.browser_calls = 0
                self.isolated_calls = 0

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                self.browser_calls += 1
                return None

            def _download_article_page_with_isolated_browser_retry(self, url, title, method, paper_id=None):
                self.isolated_calls += 1
                return Path("/tmp/reviewpilot-test-pdfs/isolated-article.pdf")

        downloader = BatchArticlePrintDownloader()
        downloader.session = FakeSession(
            FakeResponse(
                "https://ascopubs.org/doi/10.1200/CCI-25-00386",
                content=b"<!DOCTYPE html><html><title>Just a moment...</title>Cloudflare</html>",
                headers={"content-type": "text/html; charset=UTF-8"},
                status_code=403,
            )
        )

        result = downloader._download_pdf(
            "https://ascopubs.org/doi/10.1200/CCI-25-00386",
            "Large Language Model-Based Classification of Case Report Abstracts",
            "verified_article_print_pdf",
            "P0007",
        )

        self.assertIsNone(result)
        self.assertEqual(downloader.browser_calls, 1)
        self.assertEqual(downloader.isolated_calls, 0)
        self.assertEqual(downloader._last_failure_class, "article_print_failed")

    def test_batch_retries_failed_article_print_after_workers_finish(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class FakeBatchArticleBrowser:
            def __init__(self):
                self._last_success_class = "article_printable"
                self._last_failure_class = None
                self._last_failure_detail = None

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                BatchArticleRetryDownloader.retry_saw_active_downloads = (
                    BatchArticleRetryDownloader.active_downloads
                )
                return Path("/tmp/reviewpilot-test-pdfs/batch-isolated-article.pdf")

            def close(self):
                pass

        class BatchArticleRetryDownloader(FastCascadePDFDownloader):
            active_downloads = 0
            retry_saw_active_downloads = None
            lock = threading.Lock()

            def download(self, paper):
                with self.lock:
                    type(self).active_downloads += 1
                try:
                    time.sleep(0.03)
                    if paper.get("doi") == "10.1200/CCI-25-00386":
                        self.last_failure_class = "article_print_failed"
                        return False, "none", "All download methods failed"
                    return True, "direct_pdf", f"/tmp/{paper['paper_id']}.pdf"
                finally:
                    with self.lock:
                        type(self).active_downloads -= 1

            def _create_article_print_retry_downloader(self, domain):
                return FakeBatchArticleBrowser()

        BatchArticleRetryDownloader.active_downloads = 0
        BatchArticleRetryDownloader.retry_saw_active_downloads = None
        downloader = BatchArticleRetryDownloader(
            output_dir=Path("/tmp/reviewpilot-test-pdfs"),
            batch_workers=2,
        )
        papers = [
            {
                "paper_id": "P0007",
                "title": "Large Language Model-Based Classification of Case Report Abstracts",
                "doi": "10.1200/CCI-25-00386",
                "journal": "JCO Clinical Cancer Informatics",
            },
            {
                "paper_id": "P0001",
                "title": "Fast direct paper",
                "doi": "10.1038/example",
                "url": "https://example.org/direct.pdf",
            },
        ]

        results = downloader.download_batch(papers)

        self.assertEqual(results["success"], 2)
        self.assertEqual(results["failed"], 0)
        self.assertEqual(papers[0]["pdf_method"], "publisher_asco")
        self.assertEqual(BatchArticleRetryDownloader.retry_saw_active_downloads, 0)

    def test_open_access_retry_success_clears_previous_failure_metadata(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class RescueDownloader(FastCascadePDFDownloader):
            def _open_access_rescue_candidates(self, paper):
                return ["https://example.org/rescue.pdf"]

            def _download_pdf(self, url, title, method, paper_id=None):
                return Path("/tmp/reviewpilot-test-pdfs/rescue.pdf")

        downloader = RescueDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        paper = {
            "paper_id": "P0017",
            "title": "Assessing the Accuracy and Reliability of ChatGPT-4",
            "doi": "10.14423/SMJ.0000000000001977",
            "pdf_downloaded": False,
            "pdf_failure_class": "domain_cooldown_skip",
            "pdf_failure_detail": "api.semanticscholar.org cooled down",
            "pdf_failure_classes": ["domain_cooldown_skip"],
            "pdf_error": "All download methods failed",
        }
        results = {
            "success": 0,
            "failed": 1,
            "by_method": {},
            "downloaded": [],
            "failed_papers": [{"title": paper["title"], "doi": paper["doi"], "error": "failed"}],
        }

        downloader._retry_failed_open_access_pdfs([paper], results, None)

        self.assertTrue(paper["pdf_downloaded"])
        self.assertNotIn("pdf_failure_class", paper)
        self.assertNotIn("pdf_failure_detail", paper)
        self.assertNotIn("pdf_failure_classes", paper)
        self.assertNotIn("pdf_error", paper)
        self.assertEqual(results["failed_papers"], [])

    def test_batch_article_print_retry_reuses_dedicated_browser_per_domain(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class FakeDedicatedArticleBrowser:
            def __init__(self, domain):
                self.domain = domain
                self.calls = []
                self.closed = False
                self._last_success_class = "article_printable"
                self._last_failure_class = None
                self._last_failure_detail = None

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                self.calls.append((url, title, method, paper_id))
                return Path(f"/tmp/reviewpilot-test-pdfs/{paper_id}.pdf")

            def close(self):
                self.closed = True

        class DedicatedBatchRetryDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.created_browsers = []

            def _create_article_print_retry_downloader(self, domain):
                browser = FakeDedicatedArticleBrowser(domain)
                self.created_browsers.append(browser)
                return browser

            def _download_article_page_with_isolated_browser_retry(self, url, title, method, paper_id=None):
                raise AssertionError("batch article-print retry should use the dedicated browser queue")

        downloader = DedicatedBatchRetryDownloader()
        papers = [
            {
                "paper_id": "P0007",
                "title": "Large Language Model-Based Classification of Case Report Abstracts",
                "doi": "10.1200/CCI-25-00386",
                "journal": "JCO Clinical Cancer Informatics",
                "pdf_downloaded": False,
            },
            {
                "paper_id": "P0027",
                "title": "Second ASCO article",
                "doi": "10.1200/CCI-25-99999",
                "journal": "JCO Clinical Cancer Informatics",
                "pdf_downloaded": False,
            },
        ]
        results = {
            "success": 0,
            "failed": 2,
            "by_method": {},
            "downloaded": [],
            "failed_papers": [
                {"title": papers[0]["title"], "doi": papers[0]["doi"], "error": "failed"},
                {"title": papers[1]["title"], "doi": papers[1]["doi"], "error": "failed"},
            ],
        }

        downloader._retry_batch_article_print_failures(papers, results, None)

        self.assertEqual(len(downloader.created_browsers), 1)
        browser = downloader.created_browsers[0]
        self.assertEqual(browser.domain, "ascopubs.org")
        self.assertEqual(len(browser.calls), 2)
        self.assertTrue(browser.closed)
        self.assertEqual(results["success"], 2)
        self.assertEqual(results["failed"], 0)
        self.assertEqual(results["by_method"]["publisher_asco"], 2)

    def test_batch_article_print_retry_skips_publisher_paywalled_papers(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class PaywalledRetryDownloader(FastCascadePDFDownloader):
            def _create_article_print_retry_downloader(self, domain):
                raise AssertionError("paywalled publisher pages should not open browser retry")

            def _create_fresh_article_print_retry_downloader(self, domain):
                raise AssertionError("paywalled publisher pages should not open browser retry")

        downloader = PaywalledRetryDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
        paper = {
            "paper_id": "P0001",
            "title": "LlymX: Multimodal LLM-Augmented XR for Context-Aware Information Access",
            "doi": "10.1109/MCG.2026.3701906",
            "journal": "IEEE Computer Graphics and Applications",
            "pdf_downloaded": False,
            "pdf_failure_class": "publisher_paywalled",
            "pdf_failure_classes": ["publisher_paywalled", "metadata_api_429"],
        }
        results = {
            "success": 0,
            "failed": 1,
            "by_method": {},
            "downloaded": [],
            "failed_papers": [{"title": paper["title"], "doi": paper["doi"], "failure_class": "publisher_paywalled"}],
        }

        downloader._retry_batch_article_print_failures([paper], results, None)

        self.assertFalse(paper["pdf_downloaded"])
        self.assertEqual(results["success"], 0)
        self.assertEqual(results["failed"], 1)

    def test_batch_article_print_retry_rotates_profile_after_dedicated_failure(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class FakeFailingArticleBrowser:
            def __init__(self):
                self.closed = False
                self._last_success_class = None
                self._last_failure_class = None
                self._last_failure_detail = None

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                return None

            def close(self):
                self.closed = True

        class FakeFreshArticleBrowser:
            def __init__(self):
                self.closed = False
                self._last_success_class = "article_printable"
                self._last_failure_class = None
                self._last_failure_detail = None

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                return Path(f"/tmp/reviewpilot-test-pdfs/{paper_id}-fresh.pdf")

            def close(self):
                self.closed = True

        class RotatingBatchRetryDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.primary_browser = FakeFailingArticleBrowser()
                self.fresh_browser = FakeFreshArticleBrowser()

            def _create_article_print_retry_downloader(self, domain):
                return self.primary_browser

            def _create_fresh_article_print_retry_downloader(self, domain):
                return self.fresh_browser

        downloader = RotatingBatchRetryDownloader()
        paper = {
            "paper_id": "P0007",
            "title": "Large Language Model-Based Classification of Case Report Abstracts",
            "doi": "10.1200/CCI-25-00386",
            "journal": "JCO Clinical Cancer Informatics",
            "pdf_downloaded": False,
        }
        results = {
            "success": 0,
            "failed": 1,
            "by_method": {},
            "downloaded": [],
            "failed_papers": [{"title": paper["title"], "doi": paper["doi"], "error": "failed"}],
        }

        downloader._retry_batch_article_print_failures([paper], results, None)

        self.assertTrue(downloader.primary_browser.closed)
        self.assertTrue(downloader.fresh_browser.closed)
        self.assertTrue(paper["pdf_downloaded"])
        self.assertEqual(results["success"], 1)
        self.assertEqual(results["failed"], 0)
        self.assertEqual(paper["pdf_path"], "/tmp/reviewpilot-test-pdfs/P0007-fresh.pdf")

    def test_batch_article_print_retry_uses_per_paper_fresh_profile_after_domain_failures(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class FakeFailingArticleBrowser:
            def __init__(self):
                self.closed = False
                self._last_success_class = None
                self._last_failure_class = "article_print_failed"
                self._last_failure_detail = "failed"

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                return None

            def close(self):
                self.closed = True

        class FakeSuccessfulArticleBrowser:
            def __init__(self):
                self.closed = False
                self._last_success_class = "article_printable"
                self._last_failure_class = None
                self._last_failure_detail = None

            def _download_pdf_with_browser(self, url, title, method, paper_id=None):
                return Path(f"/tmp/reviewpilot-test-pdfs/{paper_id}-final.pdf")

            def close(self):
                self.closed = True

        class FinalRetryDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.primary_browser = FakeFailingArticleBrowser()
                self.fresh_browsers = [
                    FakeFailingArticleBrowser(),
                    FakeSuccessfulArticleBrowser(),
                ]

            def _create_article_print_retry_downloader(self, domain):
                return self.primary_browser

            def _create_fresh_article_print_retry_downloader(self, domain):
                return self.fresh_browsers.pop(0)

        downloader = FinalRetryDownloader()
        paper = {
            "paper_id": "P0010",
            "title": "The performance of ChatGPT and other large language models",
            "doi": "10.1002/ase.70262",
            "journal": "Anatomical sciences education",
            "pdf_downloaded": False,
        }
        results = {
            "success": 0,
            "failed": 1,
            "by_method": {},
            "downloaded": [],
            "failed_papers": [{"title": paper["title"], "doi": paper["doi"], "error": "failed"}],
        }

        downloader._retry_batch_article_print_failures([paper], results, None)

        self.assertTrue(paper["pdf_downloaded"])
        self.assertEqual(paper["pdf_path"], "/tmp/reviewpilot-test-pdfs/P0010-final.pdf")
        self.assertEqual(results["success"], 1)
        self.assertEqual(results["failed"], 0)

    def test_article_print_retry_target_prefers_verified_article_url(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        class VerifiedUrlDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(output_dir=Path("/tmp/reviewpilot-test-pdfs"))
                self.verified_calls = 0

            def _try_verified_article_print_pdf(self, paper):
                self.verified_calls += 1
                return "https://anatomypubs.onlinelibrary.wiley.com/doi/10.1002/ase.70262"

        downloader = VerifiedUrlDownloader()

        self.assertEqual(
            downloader._article_print_retry_target_for_paper({
                "paper_id": "P0010",
                "title": "The performance of ChatGPT and other large language models",
                "doi": "10.1002/ase.70262",
                "journal": "Anatomical sciences education",
            }),
            (
                "publisher_wiley",
                "https://anatomypubs.onlinelibrary.wiley.com/doi/10.1002/ase.70262",
            ),
        )
        self.assertEqual(downloader.verified_calls, 1)

    def test_batch_worker_defers_article_print_failure_before_slow_generic_fallbacks(self):
        from utils.fast_pdf_downloader import DomainConcurrencyPolicy, FastCascadePDFDownloader

        class ArticlePrintDeferredDownloader(FastCascadePDFDownloader):
            def __init__(self):
                super().__init__(
                    output_dir=Path("/tmp/reviewpilot-test-pdfs"),
                    enable_browser_fallback=True,
                    domain_policy=DomainConcurrencyPolicy(),
                )
                self.core_called = False

            def _download_pdf(self, url, title, method, paper_id=None):
                if method == "publisher_asco":
                    self._last_failure_class = "article_print_failed"
                    return None
                return None

            def _try_core(self, doi, title):
                self.core_called = True
                return None

        downloader = ArticlePrintDeferredDownloader()
        success, method, result = downloader.download({
            "paper_id": "P0007",
            "title": "Large Language Model-Based Classification of Case Report Abstracts",
            "doi": "10.1200/CCI-25-00386",
            "journal": "JCO Clinical Cancer Informatics",
        })

        self.assertFalse(success)
        self.assertEqual(method, "none")
        self.assertEqual(result, "Article print deferred for isolated batch retry")
        self.assertFalse(downloader.core_called)


if __name__ == "__main__":
    unittest.main()
