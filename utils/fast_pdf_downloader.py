"""
Fast PDF downloader experiment for Step 3 benchmarking.

Keeps the existing CascadePDFDownloader as the compatibility base, but changes
the expensive fallback behavior for isolated speed tests:
- static HTML PDF discovery first via lxml
- direct/API methods before browser automation
- no Selenium cold launch
"""

from __future__ import annotations

import io
import html as stdlib_html
import json
import os
import re
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin, urlparse

from lxml import html as lxml_html

from utils.pdf_downloader import CascadePDFDownloader


PDF_URL_MARKERS = (
    ".pdf",
    "/pdf",
    "showpdf",
    "download_pdf",
    "downloadpdf",
    "stamppdf",
    "pdfft",
    "type=printable",
    "blobtype=pdf",
)

ARTICLE_PRINT_PAYWALL_MARKERS = (
    "get full access to this article",
    "view all available purchase options",
    "go to purchase options",
    "purchase options",
    "purchase details",
    "payment options",
    "view purchased documents",
    "get access",
    "access through your institution",
    "sign in to access",
    "subscribe to unlock",
    "rent this article",
)

PDF_ENDPOINT_COOLDOWN_FAILURE_CLASSES = {
    "pdf_endpoint_cloudflare",
    "pdf_endpoint_tdm_blocked",
    "pdf_endpoint_waf",
}

NON_BROWSER_PDF_ENDPOINT_FAILURE_CLASSES = {
    "pdf_endpoint_tdm_blocked",
    "pdf_endpoint_waf",
}

PRIMARY_FAILURE_CLASS_PRIORITY = {
    "pdf_title_mismatch": 100,
    "article_print_incomplete": 95,
    "article_print_paywalled": 94,
    "pmc_recaptcha": 90,
    "pmc_not_open_access": 89,
    "publisher_paywalled": 88,
    "pdf_endpoint_tdm_blocked": 85,
    "pdf_endpoint_waf": 84,
    "pdf_endpoint_cloudflare": 83,
    "domain_cooldown_skip": 80,
    "article_print_failed": 75,
    "wrong_publisher_detection": 70,
    "non_pdf_html": 60,
    "access_blocked": 55,
    "metadata_api_429": 30,
    "metadata_api_error": 25,
    "network_error": 20,
}

PDF_TITLE_STOPWORDS = {
    "about",
    "after",
    "among",
    "and",
    "are",
    "between",
    "for",
    "from",
    "into",
    "the",
    "using",
    "via",
    "with",
}


def _looks_like_pdf_url(url: str) -> bool:
    url_lower = url.lower()
    return any(marker in url_lower for marker in PDF_URL_MARKERS)


def _is_likely_non_article_pdf_url(url: str) -> bool:
    parsed = urlparse(url or "")
    path = parsed.path.lower()
    query = parsed.query.lower()
    filename = path.rsplit("/", 1)[-1]
    if path.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff")):
        return True
    candidate_text = f"{filename}?{query}"
    markers = (
        "supple",
        "supplement",
        "supplementary",
        "appendix",
        "thumb",
    )
    if any(marker in candidate_text for marker in markers):
        return True
    return bool(re.search(r"(^|[-_])(fig|figure|table|tbl|f|t)\d+([._-]|$)", candidate_text))


def _dedupe_keep_order(urls: List[str]) -> List[str]:
    seen = set()
    deduped = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def _extract_http_urls_from_text(text: str) -> List[str]:
    urls = []
    for match in re.finditer(r"https?://[^\s<>()\"']+", text or ""):
        urls.append(match.group(0).rstrip(".,;:)]}'\""))
    return _dedupe_keep_order(urls)


def shared_semantic_scholar_cache_path() -> Path:
    return Path(os.getenv("SEMANTIC_SCHOLAR_CACHE") or ".cache/semantic_scholar_open_access.json")


def _primary_failure_class(failure_classes: List[str]) -> Optional[str]:
    if not failure_classes:
        return None
    return max(
        failure_classes,
        key=lambda failure_class: PRIMARY_FAILURE_CLASS_PRIORITY.get(failure_class, 10),
    )


def _is_techrxiv_pdf_endpoint(url: str) -> bool:
    parsed = urlparse(url or "")
    domain = parsed.netloc.lower()
    domain = domain[4:] if domain.startswith("www.") else domain
    return domain == "techrxiv.org" and parsed.path.lower().startswith("/doi/pdf/")


def _techrxiv_pdf_endpoint_from_article_url(url: str) -> Optional[str]:
    parsed = urlparse(url or "")
    domain = parsed.netloc.lower()
    domain = domain[4:] if domain.startswith("www.") else domain
    if domain != "techrxiv.org":
        return None
    match = re.search(r"/doi/full/(10\.36227/.+)$", parsed.path, flags=re.IGNORECASE)
    if not match:
        return None
    doi_without_version = re.sub(r"/v\d+$", "", match.group(1), flags=re.IGNORECASE)
    return f"{parsed.scheme or 'https'}://www.techrxiv.org/doi/pdf/{doi_without_version}"


def _title_match_tokens(value: str) -> List[str]:
    plain = re.sub(r"<[^>]+>", " ", value or "")
    tokens = re.findall(r"[a-z0-9]+", plain.lower())
    deduped = []
    seen = set()
    for token in tokens:
        if len(token) < 3 or token in PDF_TITLE_STOPWORDS or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped


def extract_static_pdf_urls(html_text: str, base_url: str) -> List[str]:
    """Extract likely PDF URLs from static publisher HTML."""
    if not html_text:
        return []

    candidates: List[str] = []

    try:
        doc = lxml_html.fromstring(html_text)
    except Exception:
        doc = None

    if doc is not None:
        meta_xpaths = [
            "//meta[translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='citation_pdf_url']/@content",
            "//meta[translate(@property, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='citation_pdf_url']/@content",
            "//meta[contains(translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'pdf')]/@content",
        ]
        for xpath in meta_xpaths:
            candidates.extend(doc.xpath(xpath))

        for attr in ("href", "data-pdf-url", "data-pdf", "data-download-url"):
            for value in doc.xpath(f"//*[@{attr}]/@{attr}"):
                if _looks_like_pdf_url(value):
                    candidates.append(value)

        for link in doc.xpath("//a[@href]"):
            href = link.get("href") or ""
            text = " ".join(link.itertext()).strip().lower()
            class_name = (link.get("class") or "").lower()
            if _looks_like_pdf_url(href) or "pdf" in text or "pdf" in class_name:
                candidates.append(href)

    regex_patterns = [
        r'"pdfUrl"\s*:\s*"([^"]+)"',
        r'"pdf_url"\s*:\s*"([^"]+)"',
        r'data-pdf-url=["\']([^"\']+)["\']',
        r'href=["\']([^"\']*(?:\.pdf|/pdf|showPdf|stampPDF|pdfft)[^"\']*)["\']',
    ]
    for pattern in regex_patterns:
        candidates.extend(re.findall(pattern, html_text, flags=re.IGNORECASE))

    absolute = []
    for candidate in candidates:
        candidate = str(candidate).strip()
        if not candidate or candidate.startswith(("mailto:", "javascript:", "#")):
            continue
        absolute_url = urljoin(base_url, candidate)
        parsed = urlparse(absolute_url)
        if parsed.scheme in {"http", "https"} and _looks_like_pdf_url(absolute_url):
            absolute.append(absolute_url)

    return _dedupe_keep_order(absolute)


def _extract_oxford_article_pdf_url(html_text: str, base_url: str) -> Optional[str]:
    for candidate in extract_static_pdf_urls(html_text, base_url):
        parsed = urlparse(candidate)
        if not parsed.netloc.endswith("academic.oup.com"):
            continue
        if "/article-pdf/" not in parsed.path.lower():
            continue
        if _is_likely_non_article_pdf_url(candidate):
            continue
        return candidate
    return None


def _extract_pmc_article_pdf_url(html_text: str, base_url: str, pmcid: str) -> Optional[str]:
    pmcid_lower = (pmcid or "").lower()
    if not pmcid_lower:
        return None

    article_pdf_path = f"/articles/{pmcid_lower}/pdf/"
    for candidate in extract_static_pdf_urls(html_text, base_url):
        if _is_likely_non_article_pdf_url(candidate):
            continue
        parsed = urlparse(candidate)
        path_lower = parsed.path.lower()
        if article_pdf_path in path_lower and path_lower.endswith(".pdf"):
            return candidate
    return None


def _article_print_rejection_reason(text: str) -> Optional[str]:
    normalized = re.sub(r"\s+", " ", stdlib_html.unescape(text or "").lower())
    for marker in ARTICLE_PRINT_PAYWALL_MARKERS:
        if marker in normalized:
            return f"paywall/access marker: {marker}"
    return None


def _article_print_incomplete_reason(text: str) -> Optional[str]:
    normalized = re.sub(r"\s+", " ", stdlib_html.unescape(text or "").lower())
    if "document sections" in normalized and any(
        marker in normalized
        for marker in (
            "cite this",
            "fulltext views",
            "more like this",
            "citations keywords metrics",
        )
    ):
        return "publisher overview/abstract page marker"
    return None


def _strip_reference_sections_for_preprint_search(html_text: str) -> str:
    """Remove reference-list markup before looking for article-version preprints."""
    if not html_text:
        return ""

    try:
        doc = lxml_html.fromstring(html_text)
    except Exception:
        stripped = html_text
    else:
        reference_markers = re.compile(
            r"(^|[-_\s])("
            r"references?|bibliography|ref-list|reference-list|article-references|c-article-references"
            r")($|[-_\s])",
            re.IGNORECASE,
        )
        attr_names = ("id", "class", "role", "aria-label", "data-track", "data-track-action")
        for node in list(doc.xpath("//*")):
            attr_text = " ".join(node.get(name) or "" for name in attr_names)
            if reference_markers.search(attr_text) or "click_references" in attr_text.lower():
                try:
                    node.drop_tree()
                except Exception:
                    pass

        for meta in list(doc.xpath(
            "//meta[contains(translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'citation_reference') "
            "or contains(translate(@content, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'citation_reference')]"
        )):
            try:
                meta.drop_tree()
            except Exception:
                pass

        stripped = lxml_html.tostring(doc, encoding="unicode")

    stripped = re.sub(
        r"<meta\b[^>]*(?:citation_reference|click_references)[^>]*>",
        " ",
        stripped,
        flags=re.IGNORECASE,
    )
    return stripped


def _extract_preprint_dois_from_article_html(html_text: str) -> List[str]:
    """Extract bioRxiv/medRxiv DOI links declared on publisher article pages."""
    if not html_text:
        return []

    normalized = stdlib_html.unescape(_strip_reference_sections_for_preprint_search(html_text))
    dois: List[str] = []
    doi_pattern = re.compile(r"10\.(?:1101|64898)/[A-Za-z0-9][A-Za-z0-9._/-]*[A-Za-z0-9]", re.IGNORECASE)
    for match in doi_pattern.finditer(normalized):
        candidate = match.group(0).rstrip(".,;:)]}'\"")
        context = normalized[max(0, match.start() - 500):match.end() + 500].lower()
        if not any(marker in context for marker in ("preprint", "medrxiv", "biorxiv")):
            continue
        if any(marker in context for marker in ("google scholar", "article reference", "click_references")):
            continue
        dois.append(candidate)
    return _dedupe_keep_order(dois)


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, ""))
        return value if value > 0 else default
    except ValueError:
        return default


class DomainConcurrencyPolicy:
    """Shared batch-level domain limits and failure cooldowns."""

    def __init__(
        self,
        default_domain_concurrency: int = 2,
        browser_concurrency: int = 1,
        domain_cooldown_seconds: int = 300,
        time_func: Callable[[], float] = time.monotonic,
    ):
        self.default_domain_concurrency = max(1, default_domain_concurrency)
        self.browser_concurrency = max(1, browser_concurrency)
        self.domain_cooldown_seconds = domain_cooldown_seconds
        self.time_func = time_func
        self._lock = threading.RLock()
        self._domain_semaphores: Dict[str, threading.BoundedSemaphore] = {}
        self._browser_semaphore = threading.BoundedSemaphore(self.browser_concurrency)
        self._cooldowns: Dict[Tuple[str, str], float] = {}

    def domain_for_url(self, url: str) -> str:
        parsed = urlparse(url or "")
        domain = parsed.netloc.lower()
        return domain[4:] if domain.startswith("www.") else domain

    def _domain_semaphore(self, domain: str) -> threading.BoundedSemaphore:
        domain = domain or "unknown"
        with self._lock:
            if domain not in self._domain_semaphores:
                self._domain_semaphores[domain] = threading.BoundedSemaphore(self.default_domain_concurrency)
            return self._domain_semaphores[domain]

    @contextmanager
    def domain_slot(self, domain: str):
        semaphore = self._domain_semaphore(domain)
        semaphore.acquire()
        try:
            yield
        finally:
            semaphore.release()

    @contextmanager
    def browser_slot(self):
        self._browser_semaphore.acquire()
        try:
            yield
        finally:
            self._browser_semaphore.release()

    def register_failure(self, url: str, failure_class: Optional[str]) -> None:
        if failure_class not in PDF_ENDPOINT_COOLDOWN_FAILURE_CLASSES | {"pmc_recaptcha", "metadata_api_429"}:
            return
        domain = self.domain_for_url(url)
        if not domain:
            return
        with self._lock:
            self._cooldowns[(domain, failure_class)] = self.time_func() + self.domain_cooldown_seconds

    def cooldown_failure(self, url: str, failure_classes: Optional[List[str]] = None) -> Optional[str]:
        domain = self.domain_for_url(url)
        if not domain:
            return None
        now = self.time_func()
        with self._lock:
            for (cooldown_domain, failure_class), expires_at in list(self._cooldowns.items()):
                if expires_at <= now:
                    self._cooldowns.pop((cooldown_domain, failure_class), None)
                    continue
                if cooldown_domain == domain and (failure_classes is None or failure_class in failure_classes):
                    return failure_class
        return None


class FastCascadePDFDownloader(CascadePDFDownloader):
    """Optimized Step 3 downloader for isolated benchmark experiments."""

    def __init__(
        self,
        email: str = "user@example.com",
        output_dir: Path = None,
        request_timeout: int = 8,
        download_timeout: int = 20,
        verify_timeout: int = 3,
        browser_timeout: int = 12,
        enable_browser_fallback: bool = False,
        browser_user_data_dir: Optional[Path] = None,
        enable_curl_cffi: bool = True,
        curl_cffi_impersonates: Optional[Tuple[str, ...]] = None,
        semantic_scholar_api_key: Optional[str] = None,
        semantic_scholar_cache_path: Optional[Path] = None,
        semantic_scholar_max_retries: int = 1,
        semantic_scholar_backoff_seconds: float = 1.0,
        sleep_func: Callable[[float], None] = time.sleep,
        time_func: Callable[[], float] = time.monotonic,
        domain_cooldown_seconds: int = 300,
        batch_workers: Optional[int] = None,
        domain_concurrency: Optional[int] = None,
        browser_concurrency: Optional[int] = None,
        domain_policy: Optional[DomainConcurrencyPolicy] = None,
        semantic_cache_lock: Optional[threading.RLock] = None,
    ):
        super().__init__(email=email, output_dir=output_dir)
        self.request_timeout = request_timeout
        self.download_timeout = download_timeout
        self.verify_timeout = verify_timeout
        self.browser_timeout = browser_timeout
        self.enable_browser_fallback = enable_browser_fallback
        self.sleep_func = sleep_func
        self.time_func = time_func
        self.browser_user_data_dir = Path(browser_user_data_dir) if browser_user_data_dir else self._new_browser_profile_dir()
        self.enable_curl_cffi = enable_curl_cffi
        self.curl_cffi_impersonates = curl_cffi_impersonates or ("safari17_0", "chrome124", "safari184")
        self.min_request_interval = 0.2
        self.session.headers["Accept-Encoding"] = "gzip, deflate"
        self.last_method_timings: List[Dict] = []
        self.last_failure_class = None
        self.last_failure_classes: List[str] = []
        self.last_success_class = None
        self.last_publisher_detection: Dict = {}
        self._last_failure_class = None
        self._last_failure_detail = None
        self._last_success_class = None
        self.browser_fallback_attempts = 0
        self.browser_fallback_successes = 0
        if semantic_scholar_api_key is None:
            self.semantic_scholar_api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY") or os.getenv("S2_API_KEY")
        else:
            self.semantic_scholar_api_key = semantic_scholar_api_key or None
        self.semantic_cache_lock = semantic_cache_lock
        cache_path = (
            semantic_scholar_cache_path
            or shared_semantic_scholar_cache_path()
        )
        self.semantic_scholar_cache_path = Path(cache_path)
        self.semantic_scholar_cache = self._load_semantic_scholar_cache()
        self.semantic_scholar_max_retries = semantic_scholar_max_retries if self.semantic_scholar_api_key else 0
        self.semantic_scholar_backoff_seconds = semantic_scholar_backoff_seconds
        self.domain_cooldown_seconds = domain_cooldown_seconds
        self.batch_workers = batch_workers or _env_int("REVIEWPILOT_FAST_PDF_WORKERS", 8)
        self.domain_concurrency = domain_concurrency or _env_int("REVIEWPILOT_FAST_PDF_DOMAIN_CONCURRENCY", 2)
        self.browser_concurrency = browser_concurrency or _env_int("REVIEWPILOT_FAST_PDF_BROWSER_CONCURRENCY", 1)
        self.domain_policy = domain_policy
        self.domain_failure_cooldowns: Dict[Tuple[str, str], float] = {}
        self._playwright = None
        self._browser = None
        self._browser_context = None

    def _new_browser_profile_dir(self) -> Path:
        run_id = f"run-{os.getpid()}-{int(self.time_func() * 1000)}-{id(self)}"
        return Path(".cache/playwright_fast_pdf_profile") / "runs" / run_id

    def download(self, paper: Dict) -> Tuple[bool, str, Optional[str]]:
        """Download with HTTP-first ordering and method-level telemetry."""
        self.last_method_timings = []
        self.last_failure_detail = None

        title = paper.get("title", "unknown")
        doi = paper.get("doi")
        arxiv_id = paper.get("arxiv_id")
        direct_url = paper.get("pdf_url") or paper.get("url")
        paper_id = paper.get("paper_id")
        journal = paper.get("journal", "")
        source = (paper.get("source") or "").lower()
        pmid = paper.get("pmid")
        if not pmid and source == "pubmed":
            pubmed_id = str(paper.get("id") or "").strip()
            if pubmed_id.isdigit():
                pmid = pubmed_id
        self.last_publisher_detection = self._resolve_publisher(doi, journal)
        detected_publisher = self.last_publisher_detection["selected_publisher"]

        methods: List[Tuple[str, Callable[[], Optional[str]]]] = []
        semantic_added_early = False
        pmc_added_early = False

        # Layer 1: HTTP/direct/static discovery.
        methods.append(("direct_pdf", lambda: self._try_direct_pdf_url(direct_url)))
        methods.append(("abstract_static_html", lambda: self._try_abstract_link_pdf(paper)))
        if self._has_semantic_scholar_cache(doi, title) or self.semantic_scholar_api_key:
            methods.append(("semantic_scholar", lambda: self._try_semantic_scholar(doi, title)))
            semantic_added_early = True
        if source == "arxiv" or (direct_url and "arxiv.org" in direct_url.lower()):
            methods.append(("arxiv", lambda: self._try_arxiv(arxiv_id or paper.get("id"), title)))
        if detected_publisher:
            if detected_publisher == "biorxiv":
                publisher_method = lambda: self._try_biorxiv_medrxiv(doi, title)
            else:
                publisher_method = self._get_publisher_method(detected_publisher, doi, direct_url)
            if publisher_method:
                methods.append((f"publisher_{detected_publisher}", publisher_method))
        methods.append(("static_html", lambda: self._try_static_html_pdf(direct_url, title)))
        methods.append(("doi_static_html", lambda: self._try_static_html_pdf(f"https://doi.org/{doi}", title) if doi else None))
        methods.append(("publisher_url", lambda: self._try_publisher_pattern(direct_url, doi)))
        if source == "pubmed" and pmid and not doi:
            methods.append(("pmc", lambda: self._try_pmc(pmid, doi)))
            methods.append(("europepmc", lambda: self._try_europe_pmc(pmid, doi)))
            pmc_added_early = True

        # Layer 2: API/direct sources. Keep slower aggregators after cheap/direct checks.
        methods.append(("unpaywall", lambda: self._try_unpaywall(doi)))
        if not semantic_added_early:
            methods.append(("semantic_scholar", lambda: self._try_semantic_scholar(doi, title)))
        methods.append(("arxiv", lambda: self._try_arxiv(arxiv_id, title)))
        methods.append(("biorxiv", lambda: self._try_biorxiv_medrxiv(doi, title)))
        methods.append(("preprint_lookup", lambda: self._try_find_preprint(doi, title)))
        if not pmc_added_early:
            methods.append(("pmc", lambda: self._try_pmc(pmid, doi)))
            methods.append(("europepmc", lambda: self._try_europe_pmc(pmid, doi)))
        methods.append(("article_preprint_pdf", lambda: self._try_article_preprint_pdf(paper)))
        methods.append(("doi_redirect", lambda: self._try_doi_redirect(doi)))
        methods.append(("core", lambda: self._try_core(doi, title)))

        # Keep LLM search opt-in only. The speed benchmark disables it by default.
        if self.use_web_search:
            methods.append(("web_search", lambda: self._try_llm_web_search(title, doi, journal)))
        methods.append(("verified_article_print_pdf", lambda: self._try_verified_article_print_pdf(paper)))

        for method_name, method_func in methods:
            started = time.perf_counter()
            pdf_url = None
            success = False
            error = None
            self._last_failure_class = None
            self._last_failure_detail = None
            self._last_success_class = None
            try:
                pdf_url = method_func()
                if pdf_url:
                    file_path = self._download_with_domain_policy(pdf_url, title, method_name, paper_id)
                    if file_path:
                        success = True
                        self.last_success_class = self._last_success_class
                        return True, method_name, str(file_path)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                self._last_failure_class = self._last_failure_class or "method_exception"
                self._last_failure_detail = self._last_failure_detail or error
            finally:
                self.last_method_timings.append({
                    "method": method_name,
                    "seconds": round(time.perf_counter() - started, 3),
                    "candidate_url": pdf_url,
                    "success": success,
                    "success_class": self._last_success_class if success else None,
                    "failure_class": None if success else self._last_failure_class,
                    "failure_detail": None if success else self._last_failure_detail,
                    "error": error,
                })
                failure_rows = [
                    row
                    for row in self.last_method_timings
                    if row.get("failure_class")
                ]
                self.last_failure_classes = [
                    row["failure_class"]
                    for row in failure_rows
                ]
                self.last_failure_class = _primary_failure_class(self.last_failure_classes)
                primary_detail = next(
                    (
                        row.get("failure_detail")
                        for row in failure_rows
                        if row.get("failure_class") == self.last_failure_class and row.get("failure_detail")
                    ),
                    None,
                )
                if primary_detail:
                    self.last_failure_detail = primary_detail
                elif self._last_failure_detail:
                    self.last_failure_detail = self._last_failure_detail

            if self.domain_policy and self._last_failure_class == "article_print_failed":
                self.last_failure_class = "article_print_failed"
                self.last_failure_detail = self._last_failure_detail
                return False, "none", "Article print deferred for isolated batch retry"

        return False, "none", "All download methods failed"

    def download_batch(self, papers: List[Dict], progress_callback=None, progress_file: Optional[str] = None) -> Dict:
        """Download a batch with per-domain concurrency and single-writer progress."""
        results = {
            "total": len(papers),
            "success": 0,
            "failed": 0,
            "by_method": {},
            "downloaded": [],
            "failed_papers": [],
        }

        already_downloaded = set()
        pending: List[Tuple[int, Dict]] = []
        for index, paper in enumerate(papers):
            paper_key = paper.get("id") or paper.get("doi") or paper.get("title")
            if paper_key and paper_key in already_downloaded:
                paper["pdf_downloaded"] = True
                continue
            pending.append((index, paper))

        if not pending:
            return results

        policy = self.domain_policy or DomainConcurrencyPolicy(
            default_domain_concurrency=self.domain_concurrency,
            browser_concurrency=self.browser_concurrency,
            domain_cooldown_seconds=self.domain_cooldown_seconds,
            time_func=self.time_func,
        )
        semantic_cache_lock = self.semantic_cache_lock or threading.RLock()
        worker_count = min(max(1, self.batch_workers), len(pending))

        thread_local = threading.local()
        worker_lock = threading.Lock()
        worker_counter = {"next": 0}
        spawned_downloaders = []

        def get_worker_downloader():
            downloader = getattr(thread_local, "downloader", None)
            if downloader:
                return downloader
            with worker_lock:
                worker_id = worker_counter["next"]
                worker_counter["next"] += 1
            downloader = self._spawn_worker_downloader(worker_id, policy, semantic_cache_lock)
            with worker_lock:
                spawned_downloaders.append(downloader)
            thread_local.downloader = downloader
            return downloader

        def run_one(index: int, paper: Dict) -> Dict:
            downloader = get_worker_downloader()
            domain = downloader._paper_primary_domain(paper)
            started = time.perf_counter()
            with policy.domain_slot(domain):
                paper_copy = dict(paper)
                success, method, result = downloader.download(paper_copy)
                return {
                    "index": index,
                    "paper": paper_copy,
                    "success": success,
                    "method": method,
                    "result": result,
                    "domain": domain,
                    "duration_seconds": round(time.perf_counter() - started, 3),
                    "failure_class": getattr(downloader, "last_failure_class", None),
                    "failure_detail": getattr(downloader, "last_failure_detail", None),
                    "failure_classes": _dedupe_keep_order([
                        timing.get("failure_class")
                        for timing in getattr(downloader, "last_method_timings", [])
                        if timing.get("failure_class")
                    ]),
                    "success_class": getattr(downloader, "last_success_class", None),
                }

        try:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(run_one, index, paper): (index, paper)
                    for index, paper in pending
                }
                completed = len(already_downloaded)
                for future in as_completed(futures):
                    index, original_paper = futures[future]
                    try:
                        row = future.result()
                    except Exception as exc:
                        row = {
                            "index": index,
                            "paper": dict(original_paper),
                            "success": False,
                            "method": "none",
                            "result": f"{type(exc).__name__}: {exc}",
                            "domain": None,
                            "duration_seconds": None,
                            "failure_class": "method_exception",
                            "failure_detail": f"{type(exc).__name__}: {exc}",
                            "failure_classes": ["method_exception"],
                            "success_class": None,
                        }

                    completed += 1
                    self._apply_batch_result(row, papers[index], results)
                    if progress_callback:
                        progress_callback(completed, len(papers), papers[index].get("title", "")[:50])
                    if progress_file:
                        self._append_batch_progress(progress_file, papers[index])
            self._close_spawned_downloaders(spawned_downloaders)
            spawned_downloaders = []
            self._retry_failed_open_access_pdfs(papers, results, progress_file)
            self._retry_batch_article_print_failures(papers, results, progress_file)
        finally:
            self._close_spawned_downloaders(spawned_downloaders)

        return results

    def _close_spawned_downloaders(self, downloaders: List["FastCascadePDFDownloader"]) -> None:
        for downloader in downloaders:
            close = getattr(downloader, "close", None)
            if close:
                close()

    def _load_batch_progress(self, progress_file: Optional[str], results: Dict) -> set:
        already_downloaded = set()
        if not progress_file or not os.path.exists(progress_file):
            return already_downloaded

        try:
            with open(progress_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        record = json.loads(line.strip())
                    except Exception:
                        continue
                    paper_id = record.get("id") or record.get("doi") or record.get("title")
                    if paper_id and record.get("pdf_downloaded"):
                        already_downloaded.add(paper_id)
                        results["success"] += 1
                        results["downloaded"].append({
                            "title": record.get("title"),
                            "method": record.get("pdf_method"),
                            "path": record.get("pdf_path"),
                        })
        except Exception as exc:
            print(f"  Error loading progress file: {exc}")
        return already_downloaded

    def _apply_batch_result(self, row: Dict, paper: Dict, results: Dict) -> None:
        if row["success"]:
            results["success"] += 1
            results["by_method"][row["method"]] = results["by_method"].get(row["method"], 0) + 1
            results["downloaded"].append({
                "title": paper.get("title"),
                "method": row["method"],
                "path": row["result"],
            })
            paper["pdf_downloaded"] = True
            paper["pdf_path"] = row["result"]
            paper["pdf_method"] = row["method"]
            paper.pop("pdf_failure_class", None)
            paper.pop("pdf_failure_detail", None)
            paper.pop("pdf_failure_classes", None)
            paper.pop("pdf_error", None)
            return

        results["failed"] += 1
        results["failed_papers"].append({
            "title": paper.get("title"),
            "doi": paper.get("doi"),
            "error": row["result"],
            "failure_class": row.get("failure_class"),
            "failure_detail": row.get("failure_detail"),
            "failure_classes": row.get("failure_classes") or (
                [row.get("failure_class")] if row.get("failure_class") else []
            ),
        })
        paper["pdf_downloaded"] = False
        paper["pdf_failure_class"] = row.get("failure_class")
        paper["pdf_failure_detail"] = row.get("failure_detail")
        paper["pdf_failure_classes"] = row.get("failure_classes") or (
            [row.get("failure_class")] if row.get("failure_class") else []
        )
        paper["pdf_error"] = row["result"]

    def _retry_batch_article_print_failures(
        self,
        papers: List[Dict],
        results: Dict,
        progress_file: Optional[str],
    ) -> None:
        retry_downloaders = {}
        rotated_domains = set()
        try:
            for paper in papers:
                if paper.get("pdf_downloaded"):
                    continue
                if (
                    paper.get("pdf_failure_class") == "publisher_paywalled"
                    or "publisher_paywalled" in (paper.get("pdf_failure_classes") or [])
                ):
                    continue
                retry_target = self._article_print_retry_target_for_paper(paper)
                if not retry_target:
                    continue
                method, url = retry_target
                domain = self._domain_for_url(url) or "unknown"
                retry_downloader = retry_downloaders.get(domain)
                if retry_downloader is None:
                    retry_downloader = self._create_article_print_retry_downloader(domain)
                    retry_downloaders[domain] = retry_downloader

                file_path = self._download_article_page_with_dedicated_browser_retry(
                    retry_downloader,
                    url,
                    paper.get("title", "unknown"),
                    method,
                    paper.get("paper_id"),
                )
                if not file_path and domain not in rotated_domains:
                    rotated_domains.add(domain)
                    self._close_spawned_downloaders([retry_downloader])
                    retry_downloader = self._create_fresh_article_print_retry_downloader(domain)
                    retry_downloaders[domain] = retry_downloader
                    file_path = self._download_article_page_with_dedicated_browser_retry(
                        retry_downloader,
                        url,
                        paper.get("title", "unknown"),
                        method,
                        paper.get("paper_id"),
                    )
                if not file_path:
                    final_retry_downloader = self._create_fresh_article_print_retry_downloader(domain)
                    try:
                        file_path = self._download_article_page_with_dedicated_browser_retry(
                            final_retry_downloader,
                            url,
                            paper.get("title", "unknown"),
                            method,
                            paper.get("paper_id"),
                        )
                    finally:
                        self._close_spawned_downloaders([final_retry_downloader])
                if not file_path:
                    continue

                self._record_batch_retry_success(paper, results, method, file_path)
                if progress_file:
                    self._append_batch_progress(progress_file, paper)
        finally:
            self._close_spawned_downloaders(list(retry_downloaders.values()))

    def _retry_failed_open_access_pdfs(
        self,
        papers: List[Dict],
        results: Dict,
        progress_file: Optional[str],
    ) -> None:
        """Sequentially rescue failed papers with trusted OA PDF URLs."""
        for paper in papers:
            if paper.get("pdf_downloaded"):
                continue

            title = paper.get("title", "unknown")
            paper_id = paper.get("paper_id")
            for _attempt in range(2):
                rescued = False
                for candidate in self._open_access_rescue_candidates(paper):
                    file_path = self._download_pdf(candidate, title, "open_access_rescue", paper_id)
                    if not file_path:
                        continue

                    self._record_batch_retry_success(paper, results, "open_access_rescue", file_path)
                    if progress_file:
                        self._append_batch_progress(progress_file, paper)
                    rescued = True
                    break
                if rescued:
                    break

    def _open_access_rescue_candidates(self, paper: Dict) -> List[str]:
        title = paper.get("title", "unknown")
        doi = paper.get("doi")
        candidates: List[str] = []

        direct_url = paper.get("pdf_url") or ""
        if _looks_like_pdf_url(direct_url):
            candidates.append(direct_url)

        europe_pmc_url = self._try_europe_pmc(paper.get("pmid"), doi)
        if europe_pmc_url:
            candidates.append(europe_pmc_url)

        doi_lower = (doi or "").lower()
        if doi_lower.startswith(("10.1101/", "10.64898/")):
            preprint_pdf = self._try_biorxiv_medrxiv(doi, title)
            if preprint_pdf:
                candidates.append(preprint_pdf)

        unpaywall_candidates = self._try_unpaywall_pdf_candidates(doi)
        candidates.extend(
            candidate
            for candidate in (self._europe_pmc_render_url_from_url(url) for url in unpaywall_candidates)
            if candidate
        )
        candidates.extend(unpaywall_candidates)

        if self._has_semantic_scholar_cache(doi, title) or self.semantic_scholar_api_key:
            semantic_url = self._try_semantic_scholar(doi, title)
            if semantic_url and _looks_like_pdf_url(semantic_url):
                candidates.append(semantic_url)

        return _dedupe_keep_order(candidates)

    def _europe_pmc_render_url_from_url(self, url: str) -> Optional[str]:
        match = re.search(r"/articles/(PMC\d+)(?:/|$)", url or "", flags=re.IGNORECASE)
        if not match:
            return None
        return f"https://europepmc.org/articles/{match.group(1).upper()}?pdf=render"

    def _download_article_page_with_dedicated_browser_retry(
        self,
        retry_downloader,
        url: str,
        title: str,
        method: str,
        paper_id: str = None,
    ) -> Optional[Path]:
        if not self._is_article_print_retry_candidate(url, method):
            return None

        file_path = retry_downloader._download_pdf_with_browser(url, title, method, paper_id)
        if file_path:
            success_class = getattr(retry_downloader, "_last_success_class", None)
            self._last_success_class = (
                "article_printable_dedicated"
                if success_class == "article_printable"
                else success_class
            )
            return file_path

        self._last_failure_class = getattr(retry_downloader, "_last_failure_class", None) or "article_print_failed"
        self._last_failure_detail = getattr(retry_downloader, "_last_failure_detail", None)
        return None

    def _create_article_print_retry_downloader(self, domain: str):
        return self._build_article_print_retry_downloader(
            self._article_print_retry_profile_dir(domain)
        )

    def _create_fresh_article_print_retry_downloader(self, domain: str):
        return self._build_article_print_retry_downloader(
            self._fresh_article_print_retry_profile_dir(domain)
        )

    def _build_article_print_retry_downloader(self, profile_dir: Path):
        retry_downloader = self.__class__(
            email=self.email,
            output_dir=self.output_dir,
            request_timeout=self.request_timeout,
            download_timeout=self.download_timeout,
            verify_timeout=self.verify_timeout,
            browser_timeout=max(self.browser_timeout, 20),
            enable_browser_fallback=True,
            browser_user_data_dir=profile_dir,
            enable_curl_cffi=self.enable_curl_cffi,
            curl_cffi_impersonates=self.curl_cffi_impersonates,
            semantic_scholar_api_key=self.semantic_scholar_api_key,
            semantic_scholar_cache_path=self.semantic_scholar_cache_path,
            semantic_scholar_max_retries=self.semantic_scholar_max_retries,
            semantic_scholar_backoff_seconds=self.semantic_scholar_backoff_seconds,
            sleep_func=self.sleep_func,
            time_func=self.time_func,
            domain_cooldown_seconds=self.domain_cooldown_seconds,
            batch_workers=1,
            domain_concurrency=1,
            browser_concurrency=1,
            domain_policy=None,
            semantic_cache_lock=self.semantic_cache_lock,
        )
        retry_downloader.core_api_key = self.core_api_key
        if self.llm_query_func:
            retry_downloader.set_llm_query_func(self.llm_query_func)
        if self.use_web_search:
            retry_downloader.enable_web_search(self.web_search_model)
        return retry_downloader

    def _article_print_retry_profile_dir(self, domain: str) -> Path:
        safe_domain = re.sub(r"[^A-Za-z0-9._-]+", "_", domain or "unknown").strip("._-") or "unknown"
        return self.browser_user_data_dir / "article-print" / safe_domain[:80]

    def _fresh_article_print_retry_profile_dir(self, domain: str) -> Path:
        safe_domain = re.sub(r"[^A-Za-z0-9._-]+", "_", domain or "unknown").strip("._-") or "unknown"
        suffix = f"{safe_domain[:60]}-{os.getpid()}-{int(self.time_func() * 1000)}"
        return self.browser_user_data_dir / "article-print-fresh" / suffix

    def _article_print_url_for_publisher(
        self,
        publisher: Optional[str],
        doi: Optional[str],
        direct_url: Optional[str],
    ) -> Optional[str]:
        if direct_url and not _looks_like_pdf_url(direct_url) and not self._is_metadata_article_source_url(direct_url):
            return direct_url
        if not doi:
            return None

        doi_lower = doi.lower()
        if publisher == "asco":
            return f"https://ascopubs.org/doi/{doi}"
        if publisher == "wiley":
            return f"https://onlinelibrary.wiley.com/doi/{doi}"
        if publisher == "nature":
            return f"https://www.nature.com/articles/{doi.split('/')[-1]}"
        if publisher == "acs":
            return f"https://pubs.acs.org/doi/{doi}"
        if publisher == "jove":
            article_id = doi.split("/", 1)[-1]
            return f"https://app.jove.com/t/{article_id}"
        if doi_lower.startswith("10.36227/"):
            return f"https://www.techrxiv.org/doi/full/{doi}"
        if publisher == "rsna":
            return f"https://pubs.rsna.org/doi/{doi}"
        return f"https://doi.org/{doi}"

    def _article_print_retry_target_for_paper(self, paper: Dict) -> Optional[Tuple[str, str]]:
        doi = paper.get("doi")
        publisher = self._resolve_publisher(doi, paper.get("journal", "")).get("selected_publisher")
        if not publisher and not doi:
            return None
        method = f"publisher_{publisher}"
        try:
            verified_url = self._try_verified_article_print_pdf(paper)
        except Exception:
            verified_url = None
        if verified_url and self._is_article_print_retry_candidate(verified_url, method):
            return method, verified_url

        url = self._article_print_url_for_publisher(
            publisher,
            doi,
            paper.get("pdf_url") or paper.get("url"),
        )
        if url and self._is_article_print_retry_candidate(url, method):
            return method, url
        return None

    def _remove_failed_batch_result(self, results: Dict, paper: Dict) -> None:
        doi = paper.get("doi")
        title = paper.get("title")
        for index, failed in enumerate(list(results["failed_papers"])):
            if (doi and failed.get("doi") == doi) or (title and failed.get("title") == title):
                results["failed_papers"].pop(index)
                return

    def _record_batch_retry_success(self, paper: Dict, results: Dict, method: str, file_path: Path) -> None:
        paper["pdf_downloaded"] = True
        paper["pdf_path"] = str(file_path)
        paper["pdf_method"] = method
        paper.pop("pdf_failure_class", None)
        paper.pop("pdf_failure_detail", None)
        paper.pop("pdf_failure_classes", None)
        paper.pop("pdf_error", None)
        results["success"] += 1
        results["failed"] = max(0, results["failed"] - 1)
        results["by_method"][method] = results["by_method"].get(method, 0) + 1
        results["downloaded"].append({
            "title": paper.get("title"),
            "method": method,
            "path": str(file_path),
        })
        self._remove_failed_batch_result(results, paper)

    def _append_batch_progress(self, progress_file: str, paper: Dict) -> None:
        try:
            with open(progress_file, "a", encoding="utf-8") as f:
                record = {
                    "id": paper.get("id"),
                    "doi": paper.get("doi"),
                    "title": paper.get("title"),
                    "pdf_downloaded": paper.get("pdf_downloaded", False),
                    "pdf_path": paper.get("pdf_path"),
                    "pdf_method": paper.get("pdf_method"),
                    "pdf_failure_class": paper.get("pdf_failure_class"),
                    "pdf_failure_detail": paper.get("pdf_failure_detail"),
                    "pdf_failure_classes": paper.get("pdf_failure_classes"),
                    "pdf_error": paper.get("pdf_error"),
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            print(f"  Error saving progress: {exc}")

    def _spawn_worker_downloader(
        self,
        worker_id: int,
        domain_policy: DomainConcurrencyPolicy,
        semantic_cache_lock: threading.RLock,
    ):
        worker = self.__class__(
            email=self.email,
            output_dir=self.output_dir,
            request_timeout=self.request_timeout,
            download_timeout=self.download_timeout,
            verify_timeout=self.verify_timeout,
            browser_timeout=self.browser_timeout,
            enable_browser_fallback=self.enable_browser_fallback,
            browser_user_data_dir=self.browser_user_data_dir / f"worker-{worker_id}",
            enable_curl_cffi=self.enable_curl_cffi,
            curl_cffi_impersonates=self.curl_cffi_impersonates,
            semantic_scholar_api_key=self.semantic_scholar_api_key,
            semantic_scholar_cache_path=self.semantic_scholar_cache_path,
            semantic_scholar_max_retries=self.semantic_scholar_max_retries,
            semantic_scholar_backoff_seconds=self.semantic_scholar_backoff_seconds,
            sleep_func=self.sleep_func,
            time_func=self.time_func,
            domain_cooldown_seconds=self.domain_cooldown_seconds,
            batch_workers=1,
            domain_concurrency=self.domain_concurrency,
            browser_concurrency=self.browser_concurrency,
            domain_policy=domain_policy,
            semantic_cache_lock=semantic_cache_lock,
        )
        worker.core_api_key = self.core_api_key
        if self.llm_query_func:
            worker.set_llm_query_func(self.llm_query_func)
        if self.use_web_search:
            worker.enable_web_search(self.web_search_model)
        return worker

    def _paper_primary_domain(self, paper: Dict) -> str:
        direct_url = paper.get("pdf_url") or paper.get("url")
        direct_domain = self._domain_for_url(direct_url or "")
        aggregator_domains = {"pubmed.ncbi.nlm.nih.gov", "openalex.org", "doi.org"}
        if direct_domain and direct_domain not in aggregator_domains:
            return direct_domain

        publisher = self._resolve_publisher(paper.get("doi"), paper.get("journal", "")).get("selected_publisher")
        publisher_domain = self._publisher_primary_domain(publisher)
        return publisher_domain or direct_domain or "unknown"

    def _publisher_primary_domain(self, publisher: Optional[str]) -> Optional[str]:
        return {
            "acs": "pubs.acs.org",
            "asco": "ascopubs.org",
            "biorxiv": "biorxiv.org",
            "bmc": "link.springer.com",
            "cell": "cell.com",
            "cureus": "cureus.com",
            "elsevier": "sciencedirect.com",
            "frontiers": "frontiersin.org",
            "ieee": "ieeexplore.ieee.org",
            "ios": "ebooks.iospress.nl",
            "jove": "app.jove.com",
            "mdpi": "mdpi.com",
            "nature": "nature.com",
            "oxford": "academic.oup.com",
            "plos": "journals.plos.org",
            "rsna": "pubs.rsna.org",
            "science": "science.org",
            "springer": "link.springer.com",
            "techrxiv": "techrxiv.org",
            "wiley": "onlinelibrary.wiley.com",
        }.get(publisher or "")

    def _try_direct_pdf_url(self, url: Optional[str]) -> Optional[str]:
        if url and _looks_like_pdf_url(url):
            return url
        return None

    def _try_abstract_link_pdf(self, paper: Dict) -> Optional[str]:
        title = paper.get("title", "")
        metadata_domains = {"doi.org", "pubmed.ncbi.nlm.nih.gov", "openalex.org", "semanticscholar.org"}
        for url in _extract_http_urls_from_text(paper.get("abstract", ""))[:5]:
            domain = self._domain_for_url(url)
            if domain in metadata_domains or self._is_metadata_article_source_url(url):
                continue
            if _looks_like_pdf_url(url):
                return url
            pdf_url = self._try_static_html_pdf(url, title)
            if pdf_url:
                return pdf_url
        return None

    def _resolve_publisher(self, doi: Optional[str], journal: str) -> Dict:
        doi_publisher = self._detect_publisher_from_doi(doi)
        journal_publisher = self._detect_publisher_from_journal(journal)
        selected_publisher = doi_publisher or journal_publisher
        return {
            "doi_publisher": doi_publisher,
            "journal_publisher": journal_publisher,
            "selected_publisher": selected_publisher,
            "failure_class": "wrong_publisher_detection"
            if doi_publisher and journal_publisher and doi_publisher != journal_publisher
            else None,
        }

    def _detect_publisher_from_doi(self, doi: Optional[str]) -> Optional[str]:
        if not doi:
            return None

        doi_lower = doi.lower().strip()
        doi_prefix_map = [
            ("10.7759/", "cureus"),
            ("10.1200/", "asco"),
            ("10.1016/", "elsevier"),
            ("10.3233/", "ios"),
            ("10.3791/", "jove"),
            ("10.1109/", "ieee"),
            ("10.1145/", "acm"),
            ("10.1021/", "acs"),
            ("10.1111/", "wiley"),
            ("10.1002/", "wiley"),
            ("10.1101/", "biorxiv"),
            ("10.64898/", "biorxiv"),
            ("10.36227/", "techrxiv"),
            ("10.1148/", "rsna"),
            ("10.3389/", "frontiers"),
            ("10.1186/", "bmc"),
            ("10.1093/", "oxford"),
            ("10.1038/", "nature"),
            ("10.1371/", "plos"),
            ("10.3390/", "mdpi"),
        ]
        for prefix, publisher in doi_prefix_map:
            if doi_lower.startswith(prefix):
                return publisher
        if doi_lower.startswith("10.7759/"):
            return "cureus"
        if doi_lower.startswith("10.1200/"):
            return "asco"
        if doi_lower.startswith(("10.1111/", "10.1002/")):
            return "wiley"
        return None

    def _get_publisher_method(self, publisher: str, doi: Optional[str], url: Optional[str]):
        if publisher == "cureus" and doi:
            return lambda: self._try_cureus(doi)
        if publisher == "asco" and doi:
            return lambda: f"https://ascopubs.org/doi/{doi}"
        if publisher == "wiley" and doi:
            return lambda: f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}"
        if publisher == "oxford" and doi:
            return lambda: self._try_oxford(doi)
        if publisher == "ios" and doi:
            return lambda: self._try_ios_press(doi)
        if publisher == "jove" and doi:
            return lambda: self._try_jove(doi)
        if publisher == "techrxiv" and doi:
            return lambda: self._try_techrxiv(doi)
        if publisher == "rsna" and doi:
            return lambda: f"https://pubs.rsna.org/doi/pdf/{doi}"
        return super()._get_publisher_method(publisher, doi, url)

    def _try_ios_press(self, doi: Optional[str]) -> Optional[str]:
        if not doi or not doi.lower().startswith("10.3233/"):
            return None
        return f"https://ebooks.iospress.nl/doi/{doi}"

    def _try_jove(self, doi: Optional[str]) -> Optional[str]:
        if not doi or not doi.lower().startswith("10.3791/"):
            return None
        article_id = doi.split("/", 1)[-1]
        return f"https://app.jove.com/pdf/{article_id}"

    def _try_techrxiv(self, doi: Optional[str]) -> Optional[str]:
        doi_clean = (doi or "").strip()
        if not doi_clean.lower().startswith("10.36227/"):
            return None
        doi_without_version = re.sub(r"/v\d+$", "", doi_clean, flags=re.IGNORECASE)
        return f"https://www.techrxiv.org/doi/pdf/{doi_without_version}"

    def _try_ieee(self, doi: Optional[str], url: Optional[str]) -> Optional[str]:
        csdl_pdf_url = self._try_ieee_computer_society_pdf(doi)
        if csdl_pdf_url or self._last_failure_class == "publisher_paywalled":
            return csdl_pdf_url

        arnumber = None

        if url and "ieeexplore.ieee.org" in url:
            match = re.search(r"/document/(\d+)", url)
            if match:
                arnumber = match.group(1)

        if not arnumber and doi:
            self._rate_limit()
            try:
                response = self.session.get(
                    f"https://doi.org/{doi}",
                    timeout=self.request_timeout,
                    allow_redirects=True,
                )
            except Exception:
                response = None

            if response and "ieeexplore.ieee.org" in (response.url or ""):
                match = re.search(r"/document/(\d+)", response.url)
                if match:
                    arnumber = match.group(1)
            if response and not arnumber:
                match = re.search(r"/document/(\d+)", response.text[:20000])
                if match:
                    arnumber = match.group(1)

        if not arnumber:
            return None
        return f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={arnumber}"

    def _try_ieee_computer_society_pdf(self, doi: Optional[str]) -> Optional[str]:
        doi_clean = (doi or "").strip()
        if not doi_clean.lower().startswith("10.1109/"):
            return None

        query = """
        query ($doi: String!) {
            article: articleByDoi(doi: $doi) {
            id
            fno
            pubType
            idPrefix
            issueNum
            year
            hasPdf
            isOpenAccess
            showBuyMe
          }
        }
        """
        graphql_url = "https://www.computer.org/csdl/api/v1/graphql"

        self._rate_limit()
        try:
            response = self.session.post(
                graphql_url,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Origin": "https://www.computer.org",
                    "Referer": "https://www.computer.org/csdl/",
                },
                json={"query": query, "variables": {"doi": doi_clean}},
                timeout=self.request_timeout,
            )
        except Exception:
            return None

        if response.status_code != 200:
            return None

        try:
            data = response.json()
        except Exception:
            return None

        article = data.get("data", {}).get("article") if isinstance(data, dict) else None
        if not isinstance(article, dict):
            return None
        if (
            article.get("hasPdf") is True
            and article.get("isOpenAccess") is False
            and article.get("showBuyMe") is True
        ):
            self._last_failure_class = "publisher_paywalled"
            self._last_failure_detail = "IEEE Computer Society article requires purchase or subscription"
            return None
        return self._ieee_computer_society_pdf_url_from_article(article)

    def _ieee_computer_society_pdf_url_from_article(self, article: Dict) -> Optional[str]:
        article_id = str(article.get("id") or "").strip()
        fno = str(article.get("fno") or "").strip()
        pub_type = str(article.get("pubType") or "").strip().lower()
        id_prefix = str(article.get("idPrefix") or "").strip()
        issue_num = str(article.get("issueNum") or "").strip()
        year = str(article.get("year") or "").strip()
        if not all([article_id, fno, pub_type, id_prefix, issue_num, year]):
            return None

        if pub_type == "mags":
            collection = "mags"
        elif pub_type in {"trans", "letters", "digest"}:
            collection = "trans"
        else:
            return None

        path_parts = [
            collection,
            id_prefix,
            year,
            issue_num,
            fno,
            article_id,
        ]
        escaped_parts = [quote(part, safe="") for part in path_parts]
        return (
            "https://www.computer.org/csdl/api/v1/periodical/"
            f"{'/'.join(escaped_parts)}/download-article/pdf"
        )

    def _try_elsevier(self, doi: Optional[str], url: Optional[str]) -> Optional[str]:
        if url and "sciencedirect.com" in url:
            return super()._try_elsevier(doi, url)
        if not doi:
            return None

        self._rate_limit()
        try:
            response = self.session.get(
                f"https://doi.org/{doi}",
                timeout=self.request_timeout,
                allow_redirects=True,
            )
        except Exception:
            return None

        pii_match = re.search(r"/pii/([A-Z0-9]+)", response.url, re.IGNORECASE)
        if not pii_match:
            pii_match = re.search(r"pii[=/]([A-Z0-9]+)", response.text[:20000], re.IGNORECASE)
        if not pii_match:
            return None

        pii = pii_match.group(1)
        return f"https://www.sciencedirect.com/science/article/pii/{pii}/pdfft"

    def _try_mdpi(self, doi: Optional[str], url: Optional[str]) -> Optional[str]:
        mdpi_res_url = self._mdpi_res_pdf_url(doi)
        if mdpi_res_url:
            return mdpi_res_url
        return super()._try_mdpi(doi, url)

    def _mdpi_res_pdf_url(self, doi: Optional[str]) -> Optional[str]:
        doi_lower = (doi or "").lower().strip()
        match = re.match(r"10\.3390/([a-z]+)(\d+)$", doi_lower)
        if not match:
            return None
        journal_code, numeric_suffix = match.groups()
        if len(numeric_suffix) >= 8:
            volume_text = numeric_suffix[:2]
            article_text = numeric_suffix[4:]
        elif len(numeric_suffix) == 7:
            volume_text = numeric_suffix[:1]
            article_text = numeric_suffix[3:]
        else:
            return None
        journal_slug = {
            "info": "information",
        }.get(journal_code, journal_code)
        volume = int(volume_text)
        article_number = int(article_text)
        stem = f"{journal_slug}-{volume:02d}-{article_number:05d}"
        return f"https://mdpi-res.com/d_attachment/{journal_slug}/{stem}/article_deploy/{stem}.pdf"

    def _try_oxford(self, doi: Optional[str]) -> Optional[str]:
        if not doi:
            return None

        landing_url = f"https://doi.org/{doi}"
        headers = {
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        }
        if self.enable_curl_cffi:
            try:
                from curl_cffi import requests as curl_requests
            except Exception:
                curl_requests = None
            if curl_requests:
                for impersonate in self.curl_cffi_impersonates:
                    try:
                        response = curl_requests.get(
                            landing_url,
                            headers=headers,
                            timeout=self.request_timeout,
                            allow_redirects=True,
                            impersonate=impersonate,
                        )
                    except Exception:
                        continue
                    if response.status_code == 200 and "html" in response.headers.get("content-type", "").lower():
                        pdf_url = _extract_oxford_article_pdf_url(response.text[:800000], response.url)
                        if pdf_url:
                            return pdf_url

        try:
            self._rate_limit()
            response = self.session.get(landing_url, timeout=self.request_timeout, allow_redirects=True)
        except Exception:
            return None
        if response.status_code == 200 and "html" in response.headers.get("content-type", "").lower():
            return _extract_oxford_article_pdf_url(response.text[:800000], response.url)
        return None

    def _semantic_scholar_cache_key(self, doi: Optional[str], title: str) -> Optional[str]:
        if doi:
            return f"doi:{doi.lower().strip()}"
        if title:
            normalized = re.sub(r"\s+", " ", title.lower()).strip()
            return f"title:{normalized[:200]}"
        return None

    def _has_semantic_scholar_cache(self, doi: Optional[str], title: str) -> bool:
        cache_key = self._semantic_scholar_cache_key(doi, title)
        return bool(cache_key and self.semantic_scholar_cache.get(cache_key))

    def _try_unpaywall(self, doi: Optional[str]) -> Optional[str]:
        candidates = self._try_unpaywall_pdf_candidates(doi)
        europe_pmc_candidates = [
            candidate
            for candidate in (self._europe_pmc_render_url_from_url(url) for url in candidates)
            if candidate
        ]
        ordered = _dedupe_keep_order(europe_pmc_candidates + candidates)
        return ordered[0] if ordered else None

    def _try_unpaywall_pdf_candidates(self, doi: Optional[str]) -> List[str]:
        """Return only Unpaywall locations that are direct PDF-looking URLs."""
        if not doi:
            return []

        self._rate_limit()
        url = f"https://api.unpaywall.org/v2/{quote(doi, safe='')}?email={self.email}"

        try:
            response = self.session.get(url, timeout=15)
        except Exception:
            return []
        if response.status_code != 200:
            return []

        try:
            data = response.json()
        except Exception:
            return []

        if data.get("is_oa") is False:
            self._last_failure_class = "publisher_paywalled"
            self._last_failure_detail = "Unpaywall reports DOI is not open access"
            return []

        candidates: List[str] = []

        def add_location(location: Optional[Dict]) -> None:
            if not location:
                return
            pdf_url = location.get("url_for_pdf")
            if pdf_url:
                candidates.append(pdf_url)
                return
            landing_url = location.get("url")
            if landing_url and _looks_like_pdf_url(landing_url):
                candidates.append(landing_url)

        add_location(data.get("best_oa_location"))
        for location in data.get("oa_locations", []):
            add_location(location)

        return _dedupe_keep_order(candidates)

    def _domain_for_url(self, url: str) -> str:
        parsed = urlparse(url or "")
        domain = parsed.netloc.lower()
        return domain[4:] if domain.startswith("www.") else domain

    def _cooldown_key(self, url: str, failure_class: str) -> Tuple[str, str]:
        return (self._domain_for_url(url), failure_class)

    def _register_domain_failure(self, url: str, failure_class: Optional[str]) -> None:
        if failure_class in PDF_ENDPOINT_COOLDOWN_FAILURE_CLASSES and not self._is_pdf_endpoint_cooldown_candidate(url):
            return
        if self.domain_policy:
            self.domain_policy.register_failure(url, failure_class)
            return
        if failure_class not in PDF_ENDPOINT_COOLDOWN_FAILURE_CLASSES | {"pmc_recaptcha", "metadata_api_429"}:
            return
        key = self._cooldown_key(url, failure_class)
        self.domain_failure_cooldowns[key] = self.time_func() + self.domain_cooldown_seconds

    def _is_pdf_endpoint_cooldown_candidate(self, url: str) -> bool:
        url_lower = (url or "").lower()
        return _looks_like_pdf_url(url_lower) or "/doi/pdf" in url_lower or "/doi/epdf" in url_lower

    def _domain_cooldown_failure(self, url: str, failure_classes: Optional[List[str]] = None) -> Optional[str]:
        if self.domain_policy:
            return self.domain_policy.cooldown_failure(url, failure_classes)
        domain = self._domain_for_url(url)
        if not domain:
            return None
        now = self.time_func()
        for (cooldown_domain, failure_class), expires_at in list(self.domain_failure_cooldowns.items()):
            if expires_at <= now:
                self.domain_failure_cooldowns.pop((cooldown_domain, failure_class), None)
                continue
            if cooldown_domain == domain and (failure_classes is None or failure_class in failure_classes):
                return failure_class
        return None

    def _download_with_domain_policy(self, url: str, title: str, method: str, paper_id: str = None) -> Optional[Path]:
        cooldown_classes = ["pmc_recaptcha"]
        if self._is_pdf_endpoint_cooldown_candidate(url):
            cooldown_classes.extend(sorted(PDF_ENDPOINT_COOLDOWN_FAILURE_CLASSES))
        cooldown_failure = self._domain_cooldown_failure(url, cooldown_classes)
        if cooldown_failure:
            self._last_failure_class = "domain_cooldown_skip"
            self._last_failure_detail = f"{self._domain_for_url(url)} cooled down after {cooldown_failure}"
            return None

        file_path = self._download_pdf(url, title, method, paper_id)
        if not file_path:
            self._register_domain_failure(url, self._last_failure_class)
        return file_path

    def _load_semantic_scholar_cache(self) -> Dict[str, str]:
        lock_context = self.semantic_cache_lock or nullcontext()
        try:
            with lock_context:
                if self.semantic_scholar_cache_path.exists():
                    return json.loads(self.semantic_scholar_cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_semantic_scholar_cache(self) -> None:
        lock_context = self.semantic_cache_lock or nullcontext()
        try:
            with lock_context:
                disk_cache = {}
                if self.semantic_scholar_cache_path.exists():
                    try:
                        disk_cache = json.loads(self.semantic_scholar_cache_path.read_text(encoding="utf-8"))
                    except Exception:
                        disk_cache = {}
                disk_cache.update(self.semantic_scholar_cache)
                self.semantic_scholar_cache = disk_cache
                self.semantic_scholar_cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.semantic_scholar_cache_path.write_text(
                    json.dumps(self.semantic_scholar_cache, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception:
            pass

    def _cache_semantic_scholar_pdf(self, doi: Optional[str], title: str, pdf_url: str) -> None:
        key = self._semantic_scholar_cache_key(doi, title)
        if not key:
            return
        self.semantic_scholar_cache[key] = pdf_url
        self._save_semantic_scholar_cache()

    def _try_semantic_scholar(self, doi: Optional[str], title: str) -> Optional[str]:
        cache_key = self._semantic_scholar_cache_key(doi, title)
        if cache_key and self.semantic_scholar_cache.get(cache_key):
            self._last_success_class = "semantic_scholar_cache"
            return self.semantic_scholar_cache[cache_key]

        urls = []
        if doi:
            urls.append(
                f"https://api.semanticscholar.org/graph/v1/paper/DOI:{quote(doi, safe='')}?fields=openAccessPdf"
            )
        if title:
            urls.append(
                f"https://api.semanticscholar.org/graph/v1/paper/search?query={quote(title)}&fields=openAccessPdf&limit=1"
            )

        headers = {"Accept": "application/json"}
        if self.semantic_scholar_api_key:
            headers["x-api-key"] = self.semantic_scholar_api_key

        for url in urls:
            data = self._semantic_scholar_get_json(url, headers)
            if not data:
                if self._last_failure_class in {"metadata_api_429", "domain_cooldown_skip"}:
                    return None
                continue

            candidates = []
            if isinstance(data, dict) and data.get("openAccessPdf"):
                candidates.append(data.get("openAccessPdf"))
            for paper in data.get("data", []) if isinstance(data, dict) else []:
                if paper.get("openAccessPdf"):
                    candidates.append(paper.get("openAccessPdf"))

            for candidate in candidates:
                pdf_url = candidate.get("url") if isinstance(candidate, dict) else None
                if pdf_url:
                    self._cache_semantic_scholar_pdf(doi, title, pdf_url)
                    return pdf_url

        return None

    def _semantic_scholar_get_json(self, url: str, headers: Dict[str, str]) -> Optional[Dict]:
        api_slot = self.domain_policy.domain_slot(self._domain_for_url(url)) if self.domain_policy else nullcontext()
        with api_slot:
            cooldown_failure = self._domain_cooldown_failure(url, ["metadata_api_429"])
            if cooldown_failure:
                self._last_failure_class = "domain_cooldown_skip"
                self._last_failure_detail = f"{self._domain_for_url(url)} cooled down after {cooldown_failure}"
                return None

            for attempt in range(self.semantic_scholar_max_retries + 1):
                self._rate_limit()
                try:
                    response = self.session.get(url, headers=headers, timeout=self.request_timeout)
                except Exception as exc:
                    self._last_failure_class = "metadata_api_error"
                    self._last_failure_detail = f"{type(exc).__name__}: {exc}"
                    return None

                if response.status_code == 200:
                    try:
                        return response.json()
                    except Exception as exc:
                        self._last_failure_class = "metadata_api_error"
                        self._last_failure_detail = f"invalid_json: {exc}"
                        return None

                failure_class = self._classify_response_failure("semantic_scholar", url, response)
                self._last_failure_class = failure_class or "metadata_api_error"
                self._last_failure_detail = f"HTTP {response.status_code}"
                self._register_domain_failure(url, self._last_failure_class)
                if response.status_code == 429 and attempt < self.semantic_scholar_max_retries:
                    retry_after = response.headers.get("retry-after")
                    try:
                        delay = float(retry_after) if retry_after else self.semantic_scholar_backoff_seconds * (attempt + 1)
                    except ValueError:
                        delay = self.semantic_scholar_backoff_seconds * (attempt + 1)
                    self.sleep_func(delay)
                    continue
                return None

        return None

    def _resolve_pmcid(self, pmid: Optional[str], doi: Optional[str]) -> Optional[str]:
        id_candidates = []
        if pmid:
            id_candidates.append(pmid)
        if doi:
            id_candidates.append(doi)

        for identifier in id_candidates:
            self._rate_limit()
            url = (
                "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
                f"?tool=academic_search&email={self.email}&ids={quote(identifier, safe='')}&format=json"
            )
            try:
                response = self.session.get(url, timeout=self.request_timeout)
            except Exception:
                continue
            if response.status_code != 200:
                continue
            try:
                data = response.json()
            except Exception:
                continue
            records = data.get("records", [])
            if records and records[0].get("pmcid"):
                return records[0]["pmcid"]
        if pmid:
            return self._resolve_pmcid_from_pubmed_efetch(pmid)
        return None

    def _resolve_pmcid_from_pubmed_efetch(self, pmid: str) -> Optional[str]:
        pmid = str(pmid or "").strip()
        if not pmid.isdigit():
            return None

        params = {
            "db": "pubmed",
            "id": pmid,
            "retmode": "xml",
            "tool": "academic_search",
            "email": self.email,
        }
        api_key = os.getenv("PUBMED_API_KEY") or os.getenv("NCBI_API_KEY")
        if api_key:
            params["api_key"] = api_key

        self._rate_limit()
        try:
            response = self.session.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                params=params,
                timeout=self.request_timeout,
            )
        except Exception:
            return None
        if response.status_code != 200:
            return None

        try:
            root = ET.fromstring(response.content)
        except Exception:
            return None

        for article_id in root.findall(".//ArticleId"):
            id_type = (article_id.attrib.get("IdType") or "").lower()
            value = (article_id.text or "").strip()
            if id_type in {"pmc", "pmcid"} and value.upper().startswith("PMC"):
                return value
        return None

    def _try_pmc(self, pmid: Optional[str], doi: Optional[str]) -> Optional[str]:
        pmcid = self._resolve_pmcid(pmid, doi)
        if not pmcid:
            return None

        oa_pdf = self._try_ncbi_oa_pdf(pmcid)
        if oa_pdf:
            return oa_pdf
        if self._last_failure_class == "pmc_not_open_access":
            return None

        named_pdf = self._try_pmc_named_article_pdf(pmcid)
        if named_pdf:
            return named_pdf

        return f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/"

    def _try_ncbi_oa_pdf(self, pmcid: str) -> Optional[str]:
        self._rate_limit()
        url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id={quote(pmcid, safe='')}"
        try:
            response = self.session.get(url, timeout=self.request_timeout)
        except Exception:
            return None
        if response.status_code != 200:
            return None

        try:
            root = ET.fromstring(response.content)
        except Exception:
            return None

        for element in root.iter():
            tag = str(element.tag).rsplit("}", 1)[-1].lower()
            if tag != "error":
                continue
            code = (element.attrib.get("code") or "").strip()
            message = (element.text or "").strip()
            if code == "idIsNotOpenAccess":
                self._last_failure_class = "pmc_not_open_access"
                self._last_failure_detail = message or f"{pmcid} is not open access in NCBI OA"
                return None

        for element in root.iter():
            tag = str(element.tag).rsplit("}", 1)[-1].lower()
            if tag != "link":
                continue
            link_format = (element.attrib.get("format") or "").lower()
            href = (element.attrib.get("href") or "").strip()
            if not href:
                continue
            if link_format == "pdf" or href.lower().endswith(".pdf"):
                return self._normalize_ncbi_oa_pdf_url(href)

        return None

    def _normalize_ncbi_oa_pdf_url(self, url: str) -> str:
        if url.startswith("ftp://ftp.ncbi.nlm.nih.gov/"):
            return "https://ftp.ncbi.nlm.nih.gov/" + url[len("ftp://ftp.ncbi.nlm.nih.gov/") :]
        return url

    def _try_europe_pmc(self, pmid: Optional[str], doi: Optional[str]) -> Optional[str]:
        """Try Europe PMC's stable PDF render endpoint for PMC-backed OA articles."""
        id_queries = []
        if pmid:
            id_queries.append(f"EXT_ID:{pmid}")
        if doi:
            id_queries.append(f"DOI:{quote(doi, safe='')}")

        for query in id_queries:
            self._rate_limit()
            url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={query}&format=json"
            try:
                response = self.session.get(url, timeout=self.request_timeout)
            except Exception:
                continue
            if response.status_code != 200:
                continue
            try:
                data = response.json()
            except Exception:
                continue
            for result in data.get("resultList", {}).get("result", []):
                pmcid = result.get("pmcid")
                if pmcid:
                    return f"https://europepmc.org/articles/{pmcid}?pdf=render"

        return None

    def _try_core(self, doi: Optional[str], title: str) -> Optional[str]:
        """Try CORE only when an API key is configured; otherwise it is tail-heavy."""
        if not self.core_api_key:
            return None

        headers = {
            "Authorization": f"Bearer {self.core_api_key}",
            "Accept": "application/json",
        }
        queries = []
        if doi:
            queries.append(f"doi:{doi}")
        if title:
            queries.append(title)

        for query in queries:
            self._rate_limit()
            url = f"https://api.core.ac.uk/v3/search/works?q={quote(query)}&limit=1"
            try:
                response = self.session.get(url, headers=headers, timeout=self.request_timeout)
            except Exception:
                continue
            if response.status_code != 200:
                continue
            try:
                data = response.json()
            except Exception:
                continue
            for result in data.get("results", []):
                download_url = result.get("downloadUrl")
                if download_url:
                    return download_url
        return None

    def _try_pmc_named_article_pdf(self, pmcid: str) -> Optional[str]:
        article_url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"

        fetched = self._fetch_article_html(article_url)
        if fetched:
            final_url, html_text = fetched
            pdf_url = _extract_pmc_article_pdf_url(html_text[:500000], final_url, pmcid)
            if pdf_url:
                return pdf_url

        for final_url, html_text in self._iter_curl_cffi_article_html(article_url):
            pdf_url = _extract_pmc_article_pdf_url(html_text[:500000], final_url, pmcid)
            if pdf_url:
                return pdf_url

        return None

    def _try_cureus(self, doi: Optional[str]) -> Optional[str]:
        if not doi:
            return None

        self._rate_limit()
        try:
            response = self.session.get(
                f"https://doi.org/{doi}",
                timeout=self.request_timeout,
                allow_redirects=True,
            )
        except Exception:
            return None

        content_type = response.headers.get("content-type", "")
        is_html_response = "html" in content_type.lower() or b"<html" in response.content[:1000].lower()
        if response.status_code != 200 or not is_html_response:
            return None

        for candidate in extract_static_pdf_urls(response.text[:300000], response.url):
            if "cureus.com" in candidate and self._verify_pdf_candidate(candidate):
                return candidate

        if "cureus.com/articles/" in response.url and not response.url.rstrip("/").endswith(".pdf"):
            candidate = f"{response.url.rstrip('/')}.pdf"
            if self._verify_pdf_candidate(candidate):
                return candidate

        return None

    def _try_static_html_pdf(self, url: Optional[str], title: Optional[str] = None) -> Optional[str]:
        if not url:
            return None

        if _looks_like_pdf_url(url):
            return url

        self._rate_limit()
        try:
            response = self.session.get(url, timeout=self.request_timeout, allow_redirects=True)
        except Exception:
            return None

        content_type = response.headers.get("content-type", "")
        if response.status_code != 200:
            return None
        if "pdf" in content_type.lower():
            return response.url
        if "html" not in content_type.lower() and b"<html" not in response.content[:1000].lower():
            return None

        verified_article_page = self._is_verified_article_page(
            response.url,
            "",
            response.text[:200000],
            title or "",
        )
        for candidate in extract_static_pdf_urls(response.text[:200000], response.url):
            if verified_article_page and _is_likely_non_article_pdf_url(candidate):
                continue
            if self._verify_pdf_candidate(candidate):
                return candidate

        return None

    def _preprint_candidate_title_matches(self, expected_title: str, candidate_title: str) -> bool:
        expected_compact = re.sub(r"[^a-z0-9]+", "", (expected_title or "").lower())
        candidate_compact = re.sub(r"[^a-z0-9]+", "", (candidate_title or "").lower())
        if len(expected_compact) >= 32 and (
            expected_compact in candidate_compact or candidate_compact in expected_compact
        ):
            return True

        expected_tokens = _title_match_tokens(expected_title)
        if len(expected_tokens) < 4:
            return False
        candidate_tokens = set(_title_match_tokens(candidate_title))
        if not candidate_tokens:
            return False

        hits = [token for token in expected_tokens if token in candidate_tokens]
        coverage = len(hits) / len(expected_tokens)
        return coverage >= 0.72 and len(hits) >= min(5, len(expected_tokens))

    def _preprint_pdf_url_from_result(self, result: Dict) -> Optional[str]:
        preprint_doi = result.get("doi")
        if not preprint_doi:
            return None

        preprint_doi = str(preprint_doi).strip()
        publisher = (result.get("bookOrReportDetails", {}) or {}).get("publisher", "").lower()
        source_text = f"{publisher} {result.get('source', '')}".lower()
        if preprint_doi.lower().startswith("10.1101/"):
            server = "medrxiv" if "medrxiv" in source_text else "biorxiv"
            return f"https://www.{server}.org/content/{preprint_doi}.full.pdf"
        if preprint_doi.lower().startswith("10.64898/"):
            medrxiv_id = preprint_doi.split("/", 1)[-1]
            return f"https://www.medrxiv.org/content/10.1101/{medrxiv_id}.full.pdf"
        return None

    def _try_biorxiv_medrxiv(self, doi: Optional[str], title: str) -> Optional[str]:
        doi_lower = (doi or "").lower()
        if doi_lower.startswith(("10.1101/", "10.64898/")):
            return super()._try_biorxiv_medrxiv(doi, title)
        if not title:
            return None

        normalized_title = re.sub(r"\s+", " ", title).strip()[:220]
        query = f'TITLE:"{normalized_title}" AND (SRC:PPR)'
        url = (
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
            f"?query={quote(query, safe='')}&format=json&pageSize=5"
        )
        self._rate_limit()
        try:
            response = self.session.get(url, timeout=self.request_timeout)
        except Exception:
            return None
        if response.status_code != 200:
            return None
        try:
            data = response.json()
        except Exception:
            return None

        for result in data.get("resultList", {}).get("result", []):
            candidate_title = result.get("title") or ""
            if not self._preprint_candidate_title_matches(title, candidate_title):
                continue
            pdf_url = self._preprint_pdf_url_from_result(result)
            if pdf_url:
                return pdf_url
        return None

    def _try_article_preprint_pdf(self, paper: Dict) -> Optional[str]:
        title = paper.get("title", "unknown")
        doi = paper.get("doi")
        direct_url = paper.get("pdf_url") or paper.get("url")
        candidates: List[str] = []

        if direct_url and not _looks_like_pdf_url(direct_url) and not self._is_metadata_article_source_url(direct_url):
            candidates.append(direct_url)

        publisher = self._resolve_publisher(doi, paper.get("journal", "")).get("selected_publisher")
        if publisher:
            publisher_method = self._get_publisher_method(publisher, doi, direct_url)
            if publisher_method:
                try:
                    publisher_url = publisher_method()
                except Exception:
                    publisher_url = None
                if publisher_url and not _looks_like_pdf_url(publisher_url):
                    candidates.append(publisher_url)

        if doi:
            candidates.append(f"https://doi.org/{doi}")

        for candidate in _dedupe_keep_order(candidates):
            if self._is_metadata_article_source_url(candidate):
                continue
            fetched = self._fetch_article_html(candidate)
            if not fetched:
                continue
            final_url, html_text = fetched
            if self._is_metadata_article_source_url(final_url):
                continue
            for preprint_doi in _extract_preprint_dois_from_article_html(html_text[:400000]):
                pdf_url = self._try_biorxiv_medrxiv(preprint_doi, title)
                if pdf_url:
                    return pdf_url

        return None

    def _fetch_article_html(self, url: str) -> Optional[Tuple[str, str]]:
        self._rate_limit()
        try:
            response = self.session.get(url, timeout=self.request_timeout, allow_redirects=True)
        except Exception:
            response = None

        if response is not None:
            content_type = response.headers.get("content-type", "").lower()
            content = response.content or b""
            if response.status_code == 200 and ("html" in content_type or b"<html" in content[:1000].lower()):
                return response.url, response.text

        for fetched in self._iter_curl_cffi_article_html(url):
            return fetched

        return None

    def _iter_curl_cffi_article_html(self, url: str):
        if not self.enable_curl_cffi:
            return
        try:
            from curl_cffi import requests as curl_requests
        except Exception:
            return
        headers = {
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        }
        for impersonate in self.curl_cffi_impersonates:
            try:
                curl_response = curl_requests.get(
                    url,
                    headers=headers,
                    timeout=self.request_timeout,
                    allow_redirects=True,
                    impersonate=impersonate,
                )
            except Exception:
                continue

            content_type = curl_response.headers.get("content-type", "").lower()
            content = curl_response.content or b""
            if curl_response.status_code == 200 and (
                "html" in content_type or b"<html" in content[:1000].lower()
            ):
                yield curl_response.url, curl_response.text

    def _is_metadata_article_source_url(self, url: str) -> bool:
        parsed = urlparse(url or "")
        domain = parsed.netloc.lower()
        domain = domain[4:] if domain.startswith("www.") else domain
        metadata_domains = (
            "pubmed.ncbi.nlm.nih.gov",
            "semanticscholar.org",
            "openalex.org",
        )
        return any(domain == metadata_domain or domain.endswith(f".{metadata_domain}") for metadata_domain in metadata_domains)

    def _try_verified_article_print_pdf(self, paper: Dict) -> Optional[str]:
        title = paper.get("title", "unknown")
        doi = paper.get("doi")
        direct_url = paper.get("pdf_url") or paper.get("url")
        candidates: List[str] = []

        if direct_url and not _looks_like_pdf_url(direct_url):
            candidates.append(direct_url)

        publisher = self._resolve_publisher(doi, paper.get("journal", "")).get("selected_publisher")
        if publisher:
            publisher_method = self._get_publisher_method(publisher, doi, direct_url)
            if publisher_method:
                try:
                    publisher_url = publisher_method()
                except Exception:
                    publisher_url = None
                if publisher_url and not _looks_like_pdf_url(publisher_url):
                    candidates.append(publisher_url)

        if doi:
            candidates.append(f"https://doi.org/{doi}")

        for candidate in _dedupe_keep_order(candidates):
            self._rate_limit()
            try:
                response = self.session.get(candidate, timeout=self.request_timeout, allow_redirects=True)
            except Exception:
                continue
            content_type = response.headers.get("content-type", "").lower()
            if response.status_code == 200 and (
                "html" in content_type or b"<html" in response.content[:1000].lower()
            ):
                if self._is_verified_article_page(response.url, "", response.text[:200000], title):
                    return response.url
            if response.status_code in (401, 403, 429) and not _looks_like_pdf_url(candidate):
                return response.url or candidate

        return None

    def _verify_pdf_candidate(self, url: str) -> bool:
        try:
            self._rate_limit()
            response = self.session.head(url, timeout=self.verify_timeout, allow_redirects=True)
            content_type = response.headers.get("content-type", "")
            return response.status_code == 200 and ("pdf" in content_type.lower() or _looks_like_pdf_url(response.url))
        except Exception:
            return _looks_like_pdf_url(url)

    def _extract_pdf_text_sample(self, content: bytes, max_pages: int = 4, max_chars: int = 30000) -> str:
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            if getattr(reader, "is_encrypted", False):
                try:
                    reader.decrypt("")
                except Exception:
                    return ""
            chunks = []
            for page in reader.pages[:max_pages]:
                try:
                    chunks.append(page.extract_text() or "")
                except Exception:
                    continue
                if sum(len(chunk) for chunk in chunks) >= max_chars:
                    break
            return "\n".join(chunks)[:max_chars]
        except Exception:
            return ""

    def _pdf_title_match_result(self, content: bytes, title: str) -> Tuple[bool, Optional[str]]:
        title_tokens = _title_match_tokens(title)
        if len(title_tokens) < 2:
            return True, None

        pdf_text = self._extract_pdf_text_sample(content)
        if not pdf_text.strip():
            return True, None

        pdf_tokens = set(_title_match_tokens(pdf_text))
        if not pdf_tokens:
            return True, None

        hits = [token for token in title_tokens if token in pdf_tokens]
        coverage = len(hits) / len(title_tokens)
        required_hits = min(6, len(title_tokens))
        front_text = pdf_text[:1200]
        compact_title = re.sub(r"[^a-z0-9]+", "", (title or "").lower())
        compact_front = re.sub(r"[^a-z0-9]+", "", front_text.lower())
        if len(compact_title) >= 24 and compact_title in compact_front:
            return True, None
        front_tokens = set(_title_match_tokens(front_text))
        if len(title_tokens) >= 3 and front_tokens:
            front_hits = [token for token in title_tokens if token in front_tokens]
            front_coverage = len(front_hits) / len(title_tokens)
            front_required_hits = min(5, len(title_tokens))
            if front_coverage < 0.55 and len(front_hits) < front_required_hits:
                detail = (
                    f"front matter title token coverage {front_coverage:.2f} "
                    f"({len(front_hits)}/{len(title_tokens)})"
                )
                return False, detail
        if coverage >= 0.45 or len(hits) >= required_hits:
            return True, None

        detail = f"title token coverage {coverage:.2f} ({len(hits)}/{len(title_tokens)})"
        return False, detail

    def _save_pdf_bytes_if_title_matches(
        self,
        content: bytes,
        title: str,
        method: str,
        paper_id: str = None,
    ) -> Optional[Path]:
        matches, detail = self._pdf_title_match_result(content, title)
        if not matches:
            self._last_failure_class = "pdf_title_mismatch"
            self._last_failure_detail = detail
            return None
        return self._save_pdf_bytes(content, title, method, paper_id)

    def _saved_pdf_file_title_matches(self, file_path: Path, title: str) -> bool:
        try:
            content = file_path.read_bytes()
        except Exception:
            return True
        matches, detail = self._pdf_title_match_result(content, title)
        if not matches:
            self._last_failure_class = "pdf_title_mismatch"
            self._last_failure_detail = detail
            return False
        return True

    def build_quality_audit(self, papers: List[Dict]) -> Dict:
        records = []
        for paper in papers:
            if not paper.get("pdf_downloaded"):
                continue

            file_path = Path(paper.get("pdf_path") or "")
            record = {
                "paper_id": paper.get("paper_id"),
                "title": paper.get("title"),
                "doi": paper.get("doi"),
                "method": paper.get("pdf_method"),
                "pdf_path": str(file_path),
                "exists": file_path.exists(),
            }
            if not file_path.exists():
                record.update({
                    "status": "missing_file",
                    "suspect": True,
                    "detail": "PDF path does not exist",
                })
                records.append(record)
                continue

            try:
                content = file_path.read_bytes()
            except Exception as exc:
                record.update({
                    "status": "read_error",
                    "suspect": True,
                    "detail": f"{type(exc).__name__}: {exc}",
                })
                records.append(record)
                continue

            matches, detail = self._pdf_title_match_result(content, paper.get("title", ""))
            record.update({
                "status": "ok" if matches else "suspect_title_mismatch",
                "suspect": not matches,
                "detail": detail,
                "file_size": file_path.stat().st_size,
            })
            records.append(record)

        return {
            "summary": {
                "total_downloaded": len(records),
                "suspect_count": sum(1 for record in records if record["suspect"]),
                "missing_count": sum(1 for record in records if record["status"] == "missing_file"),
            },
            "records": records,
        }

    def _article_print_pdf_is_acceptable(self, content: bytes, title: str) -> bool:
        matches, detail = self._pdf_title_match_result(content, title)
        if not matches:
            self._last_failure_class = "pdf_title_mismatch"
            self._last_failure_detail = detail
            return False

        text = self._extract_pdf_text_sample(content, max_pages=4, max_chars=40000)
        incomplete_reason = _article_print_incomplete_reason(text)
        if incomplete_reason:
            self._last_failure_class = "article_print_incomplete"
            self._last_failure_detail = incomplete_reason
            return False

        reason = _article_print_rejection_reason(text)
        if reason:
            self._last_failure_class = "article_print_paywalled"
            self._last_failure_detail = reason
            return False
        return True

    def _download_ios_press_pdf_from_html(
        self,
        base_url: str,
        html_text: str,
        title: str,
        method: str,
        paper_id: str = None,
    ) -> Optional[Path]:
        if "ebooks.iospress.nl" not in (urlparse(base_url).netloc or "").lower():
            return None

        try:
            doc = lxml_html.fromstring(html_text)
        except Exception:
            return None

        for form in doc.xpath("//form[@action]"):
            action = form.get("action") or ""
            if "/download/pdf" not in action.lower():
                continue
            data = {}
            for field in form.xpath(".//input[@name]"):
                name = field.get("name")
                if not name:
                    continue
                data[name] = field.get("value") or ""
            if not data.get("id"):
                continue

            post_url = urljoin(base_url, action)
            try:
                response = self.session.post(
                    post_url,
                    data=data,
                    timeout=self.download_timeout,
                    allow_redirects=True,
                    headers={"Referer": base_url},
                )
            except Exception:
                continue

            content_type = response.headers.get("content-type", "")
            if response.status_code == 200 and (
                response.content.startswith(b"%PDF") or "pdf" in content_type.lower()
            ):
                file_path = self._save_pdf_bytes_if_title_matches(
                    response.content,
                    title,
                    method,
                    paper_id,
                )
                if file_path:
                    self._last_success_class = "ios_press_form_pdf"
                    return file_path

        return None

    def _download_pdf(self, url: str, title: str, method: str, paper_id: str = None) -> Optional[Path]:
        """Download without Selenium fallback; parse returned HTML once for a PDF link."""
        self._rate_limit()

        try:
            request_kwargs = {
                "timeout": self.download_timeout,
                "allow_redirects": True,
            }
            headers = self._pdf_request_headers(url)
            if headers:
                request_kwargs["headers"] = headers
            response = self.session.get(url, **request_kwargs)
        except Exception:
            self._last_failure_class = "network_error"
            curl_path = self._download_pdf_with_curl_cffi(url, title, method, paper_id)
            if curl_path:
                return curl_path
            return None

        content_type = response.headers.get("content-type", "")
        first_bytes = response.content[:20]
        if response.status_code == 200 and (b"%PDF" in first_bytes or "pdf" in content_type.lower()):
            file_path = self._save_pdf_bytes_if_title_matches(response.content, title, method, paper_id)
            if file_path:
                self._last_success_class = self._last_success_class or "http_pdf"
                return file_path
            return None

        is_html_response = "html" in content_type.lower() or b"<html" in response.content[:1000].lower()
        if response.status_code == 200 and is_html_response:
            ios_press_path = self._download_ios_press_pdf_from_html(
                response.url,
                response.text[:300000],
                title,
                method,
                paper_id,
            )
            if ios_press_path:
                return ios_press_path

            for candidate in extract_static_pdf_urls(response.text[:200000], response.url):
                try:
                    retry = self.session.get(candidate, timeout=self.download_timeout, allow_redirects=True)
                except Exception:
                    continue
                retry_type = retry.headers.get("content-type", "")
                if retry.status_code == 200 and (b"%PDF" in retry.content[:20] or "pdf" in retry_type.lower()):
                    file_path = self._save_pdf_bytes_if_title_matches(retry.content, title, method, paper_id)
                    if file_path:
                        self._last_success_class = self._last_success_class or "html_pdf_link"
                        return file_path
                    continue
                self._last_failure_class = self._classify_response_failure(method, candidate, retry)
                self._last_failure_detail = f"HTTP {retry.status_code}"

        failure_class = self._classify_response_failure(method, url, response)
        if failure_class and not self._last_failure_class:
            self._last_failure_class = failure_class
            self._last_failure_detail = f"HTTP {response.status_code}"

        if failure_class == "pdf_endpoint_cloudflare":
            curl_path = self._download_pdf_with_curl_cffi(url, title, method, paper_id)
            if curl_path:
                return curl_path

        should_try_browser = self._should_try_browser_fallback(method, url, response)
        if is_html_response and self.enable_browser_fallback and should_try_browser:
            self.browser_fallback_attempts += 1
            if method == "verified_article_print_pdf":
                warm_path = self._warm_techrxiv_pdf_challenge(url, title, paper_id)
                if warm_path:
                    self.browser_fallback_successes += 1
                    return warm_path
            browser_path = self._download_pdf_with_browser(url, title, method, paper_id)
            if browser_path:
                self.browser_fallback_successes += 1
                return browser_path
            if self._is_article_print_retry_candidate(url, method) or method == "verified_article_print_pdf":
                if self.domain_policy:
                    self._last_failure_class = "article_print_failed"
                    self._last_failure_detail = "article print deferred for batch retry"
                    return None
                self.browser_fallback_attempts += 1
                isolated_path = self._download_article_page_with_isolated_browser_retry(
                    url,
                    title,
                    method,
                    paper_id,
                )
                if isolated_path:
                    self.browser_fallback_successes += 1
                    return isolated_path
                self._last_failure_class = "article_print_failed"
                self._last_failure_detail = "isolated browser article print retry failed"
            return browser_path

        return None

    def _warm_techrxiv_pdf_challenge(self, article_url: str, title: str, paper_id: str = None) -> Optional[Path]:
        pdf_url = _techrxiv_pdf_endpoint_from_article_url(article_url)
        if not pdf_url:
            return None

        previous_timeout = self.browser_timeout
        previous_failure_class = self._last_failure_class
        previous_failure_detail = self._last_failure_detail
        previous_success_class = self._last_success_class
        try:
            self.browser_timeout = min(self.browser_timeout, 4)
            warm_path = self._download_pdf_with_browser(pdf_url, title, "publisher_techrxiv", paper_id)
            if warm_path:
                return warm_path
        finally:
            self.browser_timeout = previous_timeout
            self._last_failure_class = previous_failure_class
            self._last_failure_detail = previous_failure_detail
            self._last_success_class = previous_success_class

        return None

    def _should_try_browser_fallback(self, method: str, url: str, response) -> bool:
        if method == "verified_article_print_pdf":
            return True
        failure_class = self._classify_response_failure(method, url, response)
        if failure_class == "pmc_recaptcha":
            return False
        if failure_class in NON_BROWSER_PDF_ENDPOINT_FAILURE_CLASSES:
            return False
        if failure_class == "pdf_endpoint_cloudflare" and _is_techrxiv_pdf_endpoint(url):
            return False
        if not self._is_browser_worthy_html(response):
            return False
        if self._is_pdf_endpoint_cooldown_candidate(url):
            return True
        return method in {"pmc", "europepmc"}

    def _save_pdf_bytes(self, content: bytes, title: str, method: str, paper_id: str = None) -> Path:
        if paper_id:
            filename = f"{paper_id}_{self._sanitize_filename(title)[:60]}.pdf"
        else:
            filename = self._sanitize_filename(title) + f"_{method}.pdf"
        file_path = self.output_dir / filename
        with open(file_path, "wb") as f:
            f.write(content)
        return file_path

    def _is_browser_worthy_html(self, response) -> bool:
        content_lower = response.content[:5000].lower()
        if response.status_code in (401, 403, 429):
            return True
        markers = [
            b"checking your browser",
            b"recaptcha",
            b"cloudflare",
            b"captcha",
            b"just a moment",
            b"access denied",
            b"enable javascript",
        ]
        return any(marker in content_lower for marker in markers)

    def _is_verified_article_html_response(self, response, title: str) -> bool:
        if response.status_code != 200:
            return False
        content_type = response.headers.get("content-type", "").lower()
        if "html" not in content_type and b"<html" not in response.content[:1000].lower():
            return False
        return self._is_verified_article_page(response.url, "", response.text[:200000], title)

    def _classify_response_failure(self, method: str, url: str, response) -> Optional[str]:
        url_lower = (url or "").lower()
        content_lower = response.content[:8000].lower()
        is_pdf_endpoint = (
            _looks_like_pdf_url(url_lower)
            or "/doi/pdf" in url_lower
            or "/doi/epdf" in url_lower
            or method.startswith("publisher_")
        )

        if response.status_code == 429 and (
            method == "semantic_scholar" or "api.semanticscholar.org" in url_lower
        ):
            return "metadata_api_429"

        if "pmc.ncbi.nlm.nih.gov" in url_lower or "ncbi.nlm.nih.gov/pmc" in url_lower:
            if b"recaptcha" in content_lower or b"challengepage" in content_lower or b"checking your browser" in content_lower:
                return "pmc_recaptcha"

        cloudflare_markers = (
            b"just a moment",
            b"cloudflare",
            b"cf-chl",
            b"checking your browser",
        )
        if is_pdf_endpoint and "sciencedirect.com" in url_lower and (
            b"tdm-reservation" in content_lower or b"tdmrep-policy" in content_lower
        ):
            return "pdf_endpoint_tdm_blocked"
        if is_pdf_endpoint and (
            b"awswafcookiedomainlist" in content_lower or b"gokuprops" in content_lower
        ):
            return "pdf_endpoint_waf"
        if response.status_code in (401, 403, 429) or any(marker in content_lower for marker in cloudflare_markers):
            if is_pdf_endpoint:
                return "pdf_endpoint_cloudflare"
            return "access_blocked"

        content_type = response.headers.get("content-type", "").lower()
        if response.status_code == 200 and ("html" in content_type or b"<html" in response.content[:1000].lower()):
            return "non_pdf_html"
        if response.status_code >= 400:
            return f"http_{response.status_code}"
        return None

    def _pdf_request_headers(self, url: str) -> Optional[Dict[str, str]]:
        referer = self._ieee_computer_society_referer_for_pdf_url(url)
        if not referer:
            return None
        session_headers = getattr(self.session, "headers", {}) or {}
        return {
            "Accept": "application/pdf,*/*",
            "Referer": referer,
            "User-Agent": session_headers.get("User-Agent", "Mozilla/5.0"),
        }

    def _ieee_computer_society_referer_for_pdf_url(self, url: str) -> Optional[str]:
        parsed = urlparse(url or "")
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        if domain != "computer.org":
            return None

        match = re.match(
            r"^/csdl/api/v1/periodical/(mags|trans)/([^/]+)/([^/]+)/([^/]+)/([^/]+)/([^/]+)/download-article/pdf$",
            parsed.path,
        )
        if not match:
            return None

        collection, id_prefix, year, issue_num, fno, article_id = match.groups()
        route_type = "magazine" if collection == "mags" else "journal"
        return (
            "https://www.computer.org/csdl/"
            f"{route_type}/{id_prefix}/{year}/{issue_num}/{fno}/{article_id}"
        )

    def _is_curl_cffi_candidate(self, url: str) -> bool:
        url_lower = (url or "").lower()
        return (
            url_lower.startswith(("http://", "https://"))
            and (_looks_like_pdf_url(url_lower) or "/doi/pdf" in url_lower or "/doi/epdf" in url_lower)
        )

    def _download_pdf_with_curl_cffi(self, url: str, title: str, method: str, paper_id: str = None) -> Optional[Path]:
        if not self.enable_curl_cffi or not self._is_curl_cffi_candidate(url):
            return None

        try:
            from curl_cffi import requests as curl_requests
        except Exception:
            return None

        headers = {
            "Accept": "application/pdf,text/html,application/xhtml+xml,*/*;q=0.8",
        }
        for impersonate in self.curl_cffi_impersonates:
            try:
                response = curl_requests.get(
                    url,
                    headers=headers,
                    timeout=self.download_timeout,
                    allow_redirects=True,
                    impersonate=impersonate,
                )
            except Exception:
                continue

            content_type = response.headers.get("content-type", "")
            content = response.content or b""
            if response.status_code == 200 and (content.startswith(b"%PDF") or "pdf" in content_type.lower()):
                file_path = self._save_pdf_bytes_if_title_matches(content, title, method, paper_id)
                if file_path:
                    self._last_success_class = "curl_cffi_pdf"
                    return file_path
                return None

            is_html_response = "html" in content_type.lower() or b"<html" in content[:1000].lower()
            if response.status_code == 200 and is_html_response:
                for candidate in extract_static_pdf_urls(response.text[:200000], response.url):
                    try:
                        retry = curl_requests.get(
                            candidate,
                            headers=headers,
                            timeout=self.download_timeout,
                            allow_redirects=True,
                            impersonate=impersonate,
                        )
                    except Exception:
                        continue
                    retry_type = retry.headers.get("content-type", "")
                    retry_content = retry.content or b""
                    if retry.status_code == 200 and (
                        retry_content.startswith(b"%PDF") or "pdf" in retry_type.lower()
                    ):
                        file_path = self._save_pdf_bytes_if_title_matches(retry_content, title, method, paper_id)
                        if file_path:
                            self._last_success_class = "curl_cffi_pdf_link"
                            return file_path
                        continue

        return None

    def _ensure_browser_context(self):
        if self._browser_context:
            return self._browser_context

        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        user_agent = self.session.headers.get("User-Agent")
        try:
            self.browser_user_data_dir.mkdir(parents=True, exist_ok=True)
            self._browser_context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.browser_user_data_dir),
                headless=True,
                timeout=15000,
                accept_downloads=True,
                user_agent=user_agent,
                args=["--disable-gpu", "--no-sandbox"],
            )
            self._browser = None
        except Exception:
            self._browser = self._playwright.chromium.launch(
                headless=True,
                timeout=15000,
                args=["--disable-gpu", "--no-sandbox"],
            )
            self._browser_context = self._browser.new_context(
                accept_downloads=True,
                user_agent=user_agent,
            )
        return self._browser_context

    def _download_pdf_with_browser(self, url: str, title: str, method: str, paper_id: str = None) -> Optional[Path]:
        slot = self.domain_policy.browser_slot() if self.domain_policy else nullcontext()
        with slot:
            return self._download_pdf_with_browser_unlocked(url, title, method, paper_id)

    def _is_article_print_retry_candidate(self, url: str, method: str) -> bool:
        return method.startswith("publisher_") and not self._is_pdf_endpoint_cooldown_candidate(url)

    def _download_article_page_with_isolated_browser_retry(
        self,
        url: str,
        title: str,
        method: str,
        paper_id: str = None,
    ) -> Optional[Path]:
        if method != "verified_article_print_pdf" and not self._is_article_print_retry_candidate(url, method):
            return None

        parent_dir = self.browser_user_data_dir.parent
        parent_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="article-print-", dir=str(parent_dir)) as profile_dir:
            retry_downloader = FastCascadePDFDownloader(
                email=self.email,
                output_dir=self.output_dir,
                request_timeout=self.request_timeout,
                download_timeout=self.download_timeout,
                verify_timeout=self.verify_timeout,
                browser_timeout=max(self.browser_timeout, 20),
                enable_browser_fallback=True,
                browser_user_data_dir=Path(profile_dir),
                enable_curl_cffi=self.enable_curl_cffi,
                curl_cffi_impersonates=self.curl_cffi_impersonates,
                semantic_scholar_api_key=self.semantic_scholar_api_key,
                semantic_scholar_cache_path=self.semantic_scholar_cache_path,
                semantic_scholar_max_retries=self.semantic_scholar_max_retries,
                semantic_scholar_backoff_seconds=self.semantic_scholar_backoff_seconds,
                sleep_func=self.sleep_func,
                time_func=self.time_func,
                domain_cooldown_seconds=self.domain_cooldown_seconds,
                batch_workers=1,
                domain_concurrency=self.domain_concurrency,
                browser_concurrency=self.browser_concurrency,
                domain_policy=self.domain_policy,
                semantic_cache_lock=self.semantic_cache_lock,
            )
            try:
                file_path = retry_downloader._download_pdf_with_browser(url, title, method, paper_id)
                if file_path:
                    self._last_success_class = (
                        "article_printable_isolated"
                        if retry_downloader._last_success_class == "article_printable"
                        else retry_downloader._last_success_class
                    )
                    return file_path
                self._last_failure_class = retry_downloader._last_failure_class or "article_print_failed"
                self._last_failure_detail = retry_downloader._last_failure_detail
                return None
            finally:
                retry_downloader.close()

    def _download_pdf_with_browser_unlocked(self, url: str, title: str, method: str, paper_id: str = None) -> Optional[Path]:
        try:
            context = self._ensure_browser_context()
            page = context.new_page()
            page.set_default_timeout(self.browser_timeout * 1000)

            try:
                if _looks_like_pdf_url(url):
                    try:
                        with page.expect_download(timeout=self.browser_timeout * 1000) as download_info:
                            try:
                                page.goto(url, wait_until="domcontentloaded", timeout=self.browser_timeout * 1000)
                            except Exception as exc:
                                if "Download is starting" not in str(exc):
                                    raise
                        download = download_info.value
                        file_path = self._browser_output_path(title, method, paper_id)
                        download.save_as(str(file_path))
                        if self._saved_pdf_file_title_matches(file_path, title):
                            self._last_success_class = "browser_download"
                            return file_path
                        try:
                            file_path.unlink()
                        except Exception:
                            pass
                    except Exception:
                        pass

                response = page.goto(url, wait_until="domcontentloaded", timeout=self.browser_timeout * 1000)
                if response and "pdf" in (response.headers.get("content-type", "").lower()):
                    body = response.body()
                    if body.startswith(b"%PDF"):
                        file_path = self._save_pdf_bytes_if_title_matches(body, title, method, paper_id)
                        if file_path:
                            return file_path

                page.wait_for_timeout(min(3000, self.browser_timeout * 1000))
                html = page.content()
                if self._is_verified_article_page(page.url, page.title(), html, title):
                    file_path = self._browser_output_path(title, method, paper_id)
                    page.pdf(path=str(file_path), format="Letter", print_background=True)
                    content = file_path.read_bytes() if file_path.exists() else b""
                    if content[:4] == b"%PDF" and self._article_print_pdf_is_acceptable(content, title):
                        self._last_success_class = "article_printable"
                        return file_path
                    try:
                        file_path.unlink()
                    except Exception:
                        pass

                for candidate in extract_static_pdf_urls(html[:200000], page.url):
                    try:
                        pdf_response = page.goto(candidate, wait_until="domcontentloaded", timeout=self.browser_timeout * 1000)
                        if pdf_response and "pdf" in (pdf_response.headers.get("content-type", "").lower()):
                            body = pdf_response.body()
                            if body.startswith(b"%PDF"):
                                file_path = self._save_pdf_bytes_if_title_matches(body, title, method, paper_id)
                                if file_path:
                                    self._last_success_class = "browser_pdf_link"
                                    return file_path
                    except Exception:
                        continue
            finally:
                page.close()
        except Exception:
            return None

        return None

    def _is_verified_article_page(
        self,
        url: str,
        page_title: str,
        page_html: str,
        expected_title: str,
    ) -> bool:
        url_lower = (url or "").lower()
        if _looks_like_pdf_url(url_lower) or "/doi/pdf/" in url_lower or "/doi/epdf/" in url_lower:
            return False
        if self._is_metadata_article_source_url(url_lower):
            return False

        page_title_lower = (page_title or "").lower()
        page_html_lower = (page_html or "").lower()
        challenge_text = f"{page_title_lower} {page_html_lower[:2000]}"
        challenge_markers = (
            "just a moment",
            "checking your browser",
            "cloudflare",
            "captcha",
        )
        if any(marker in challenge_text for marker in challenge_markers):
            return False
        if _article_print_rejection_reason(page_html_lower[:80000]):
            return False

        expected_words = [
            word for word in re.findall(r"[a-z0-9]+", (expected_title or "").lower())
            if len(word) >= 4
        ]
        if not expected_words:
            return False

        page_text = f"{page_title_lower} {page_html_lower[:50000]}"
        overlap = sum(1 for word in expected_words[:16] if word in page_text)
        core_section_markers = (
            "introduction",
            "background",
            "materials and methods",
            "methods",
            "results",
            "discussion",
            "conclusion",
            "original reports",
        )
        core_section_hits = sum(1 for marker in core_section_markers if marker in page_html_lower)
        has_references = "references" in page_html_lower or "bibliography" in page_html_lower
        return overlap >= min(5, len(expected_words)) and core_section_hits >= 2 and has_references

    def _browser_output_path(self, title: str, method: str, paper_id: str = None) -> Path:
        if paper_id:
            filename = f"{paper_id}_{self._sanitize_filename(title)[:60]}.pdf"
        else:
            filename = self._sanitize_filename(title) + f"_{method}_browser.pdf"
        return self.output_dir / filename

    def _try_selenium_download(self, url: str, title: str, paper_id: str = None) -> Optional[Path]:
        """Disable Selenium cold starts in the optimized benchmark path."""
        return None

    def close(self) -> None:
        """Close optional browser fallback resources."""
        for resource in (self._browser_context, self._browser):
            if resource:
                try:
                    resource.close()
                except Exception:
                    pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._browser_context = None
        self._browser = None
        self._playwright = None
