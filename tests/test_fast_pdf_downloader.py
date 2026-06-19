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
        self.get_calls = []
        self.head_calls = []

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


class FakeSequenceSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.get_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("Unexpected GET call")
        return self.responses.pop(0)


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
            ("publisher_wiley", "https://onlinelibrary.wiley.com/doi/pdf/10.1111/jerd.13046"),
            downloader.download_calls,
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

    def test_doi_prefix_resolver_overrides_broad_journal_substrings(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        self.assertEqual(downloader._resolve_publisher("10.3389/fcimb.2026.1773593", "Frontiers in Cellular and Infection Microbiology")["selected_publisher"], "frontiers")
        self.assertEqual(downloader._resolve_publisher("10.1186/s12859-023-05411-z", "Bioinformatics")["selected_publisher"], "bmc")
        self.assertEqual(downloader._resolve_publisher("10.1093/gigascience/giag015", "GigaScience")["selected_publisher"], "oxford")
        self.assertEqual(downloader._resolve_publisher("10.64898/2026.05.13.724985", "medRxiv preprint")["selected_publisher"], "biorxiv")

    def test_oxford_publisher_method_is_available_for_oup_doi(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        downloader = FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

        publisher_method = downloader._get_publisher_method(
            "oxford",
            "10.1093/gigascience/giag015",
            None,
        )

        self.assertIsNotNone(publisher_method)

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
