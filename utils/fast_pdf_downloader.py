"""
Fast PDF downloader experiment for Step 3 benchmarking.

Keeps the existing CascadePDFDownloader as the compatibility base, but changes
the expensive fallback behavior for isolated speed tests:
- static HTML PDF discovery first via lxml
- direct/API methods before browser automation
- no Selenium cold launch
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
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
    "stamppdf",
    "pdfft",
    "type=printable",
    "blobtype=pdf",
)


def _looks_like_pdf_url(url: str) -> bool:
    url_lower = url.lower()
    return any(marker in url_lower for marker in PDF_URL_MARKERS)


def _dedupe_keep_order(urls: List[str]) -> List[str]:
    seen = set()
    deduped = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            deduped.append(url)
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
        if failure_class not in {"pdf_endpoint_cloudflare", "pmc_recaptcha", "metadata_api_429"}:
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
        self.browser_user_data_dir = Path(browser_user_data_dir or ".cache/playwright_fast_pdf_profile")
        self.enable_curl_cffi = enable_curl_cffi
        self.curl_cffi_impersonates = curl_cffi_impersonates or ("chrome124", "safari184")
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
            or os.getenv("SEMANTIC_SCHOLAR_CACHE")
            or ".cache/semantic_scholar_open_access.json"
        )
        self.semantic_scholar_cache_path = Path(cache_path)
        self.semantic_scholar_cache = self._load_semantic_scholar_cache()
        self.semantic_scholar_max_retries = semantic_scholar_max_retries if self.semantic_scholar_api_key else 0
        self.semantic_scholar_backoff_seconds = semantic_scholar_backoff_seconds
        self.sleep_func = sleep_func
        self.time_func = time_func
        self.domain_cooldown_seconds = domain_cooldown_seconds
        self.batch_workers = batch_workers or _env_int("REVIEWPILOT_FAST_PDF_WORKERS", 8)
        self.domain_concurrency = domain_concurrency or _env_int("REVIEWPILOT_FAST_PDF_DOMAIN_CONCURRENCY", 2)
        self.browser_concurrency = browser_concurrency or _env_int("REVIEWPILOT_FAST_PDF_BROWSER_CONCURRENCY", 1)
        self.domain_policy = domain_policy
        self.domain_failure_cooldowns: Dict[Tuple[str, str], float] = {}
        self._playwright = None
        self._browser = None
        self._browser_context = None

    def download(self, paper: Dict) -> Tuple[bool, str, Optional[str]]:
        """Download with HTTP-first ordering and method-level telemetry."""
        self.last_method_timings = []

        title = paper.get("title", "unknown")
        doi = paper.get("doi")
        arxiv_id = paper.get("arxiv_id")
        direct_url = paper.get("pdf_url") or paper.get("url")
        paper_id = paper.get("paper_id")
        journal = paper.get("journal", "")
        source = (paper.get("source") or "").lower()
        self.last_publisher_detection = self._resolve_publisher(doi, journal)
        detected_publisher = self.last_publisher_detection["selected_publisher"]

        methods: List[Tuple[str, Callable[[], Optional[str]]]] = []
        semantic_added_early = False

        # Layer 1: HTTP/direct/static discovery.
        methods.append(("direct_pdf", lambda: self._try_direct_pdf_url(direct_url)))
        if self._has_semantic_scholar_cache(doi, title):
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
        methods.append(("static_html", lambda: self._try_static_html_pdf(direct_url)))
        methods.append(("doi_static_html", lambda: self._try_static_html_pdf(f"https://doi.org/{doi}") if doi else None))
        methods.append(("publisher_url", lambda: self._try_publisher_pattern(direct_url, doi)))

        # Layer 2: API/direct sources. Keep slower aggregators after cheap/direct checks.
        methods.append(("unpaywall", lambda: self._try_unpaywall(doi)))
        if not semantic_added_early:
            methods.append(("semantic_scholar", lambda: self._try_semantic_scholar(doi, title)))
        methods.append(("arxiv", lambda: self._try_arxiv(arxiv_id, title)))
        methods.append(("biorxiv", lambda: self._try_biorxiv_medrxiv(doi, title)))
        methods.append(("preprint_lookup", lambda: self._try_find_preprint(doi, title)))
        methods.append(("pmc", lambda: self._try_pmc(paper.get("pmid"), doi)))
        methods.append(("europepmc", lambda: self._try_europe_pmc(paper.get("pmid"), doi)))
        methods.append(("doi_redirect", lambda: self._try_doi_redirect(doi)))
        methods.append(("core", lambda: self._try_core(doi, title)))

        # Keep LLM search opt-in only. The speed benchmark disables it by default.
        if self.use_web_search:
            methods.append(("web_search", lambda: self._try_llm_web_search(title, doi, journal)))

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
                self.last_failure_classes = [
                    row["failure_class"]
                    for row in self.last_method_timings
                    if row.get("failure_class")
                ]
                self.last_failure_class = self.last_failure_classes[-1] if self.last_failure_classes else None

            if self.domain_policy and self._last_failure_class == "article_print_failed":
                self.last_failure_class = "article_print_failed"
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

        already_downloaded = self._load_batch_progress(progress_file, results)
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
            return

        results["failed"] += 1
        results["failed_papers"].append({
            "title": paper.get("title"),
            "doi": paper.get("doi"),
            "error": row["result"],
            "failure_class": row.get("failure_class"),
        })
        paper["pdf_downloaded"] = False

    def _retry_batch_article_print_failures(
        self,
        papers: List[Dict],
        results: Dict,
        progress_file: Optional[str],
    ) -> None:
        retry_downloaders = {}
        try:
            for paper in papers:
                if paper.get("pdf_downloaded"):
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
                if not file_path:
                    continue

                paper["pdf_downloaded"] = True
                paper["pdf_path"] = str(file_path)
                paper["pdf_method"] = method
                results["success"] += 1
                results["failed"] = max(0, results["failed"] - 1)
                results["by_method"][method] = results["by_method"].get(method, 0) + 1
                results["downloaded"].append({
                    "title": paper.get("title"),
                    "method": method,
                    "path": str(file_path),
                })
                self._remove_failed_batch_result(results, paper)
                if progress_file:
                    self._append_batch_progress(progress_file, paper)
        finally:
            self._close_spawned_downloaders(list(retry_downloaders.values()))

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
        retry_downloader = self.__class__(
            email=self.email,
            output_dir=self.output_dir,
            request_timeout=self.request_timeout,
            download_timeout=self.download_timeout,
            verify_timeout=self.verify_timeout,
            browser_timeout=max(self.browser_timeout, 20),
            enable_browser_fallback=True,
            browser_user_data_dir=self._article_print_retry_profile_dir(domain),
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

    def _article_print_retry_target_for_paper(self, paper: Dict) -> Optional[Tuple[str, str]]:
        doi = paper.get("doi")
        publisher = self._resolve_publisher(doi, paper.get("journal", "")).get("selected_publisher")
        if not publisher:
            return None
        method = f"publisher_{publisher}"
        publisher_method = self._get_publisher_method(publisher, doi, paper.get("pdf_url") or paper.get("url"))
        if not publisher_method:
            return None
        try:
            url = publisher_method()
        except Exception:
            return None
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
            "asco": "ascopubs.org",
            "biorxiv": "biorxiv.org",
            "bmc": "link.springer.com",
            "cell": "cell.com",
            "cureus": "cureus.com",
            "frontiers": "frontiersin.org",
            "mdpi": "mdpi.com",
            "nature": "nature.com",
            "oxford": "academic.oup.com",
            "plos": "journals.plos.org",
            "science": "science.org",
            "springer": "link.springer.com",
            "wiley": "onlinelibrary.wiley.com",
        }.get(publisher or "")

    def _try_direct_pdf_url(self, url: Optional[str]) -> Optional[str]:
        if url and _looks_like_pdf_url(url):
            return url
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
            ("10.1111/", "wiley"),
            ("10.1002/", "wiley"),
            ("10.1101/", "biorxiv"),
            ("10.64898/", "biorxiv"),
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
            return lambda: f"https://onlinelibrary.wiley.com/doi/pdf/{doi}"
        if publisher == "oxford":
            return None
        return super()._get_publisher_method(publisher, doi, url)

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

    def _domain_for_url(self, url: str) -> str:
        parsed = urlparse(url or "")
        domain = parsed.netloc.lower()
        return domain[4:] if domain.startswith("www.") else domain

    def _cooldown_key(self, url: str, failure_class: str) -> Tuple[str, str]:
        return (self._domain_for_url(url), failure_class)

    def _register_domain_failure(self, url: str, failure_class: Optional[str]) -> None:
        if failure_class == "pdf_endpoint_cloudflare" and not self._is_pdf_endpoint_cooldown_candidate(url):
            return
        if self.domain_policy:
            self.domain_policy.register_failure(url, failure_class)
            return
        if failure_class not in {"pdf_endpoint_cloudflare", "pmc_recaptcha", "metadata_api_429"}:
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
        cooldown_failure = self._domain_cooldown_failure(
            url,
            ["pdf_endpoint_cloudflare", "pmc_recaptcha"],
        )
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

    def _try_static_html_pdf(self, url: Optional[str]) -> Optional[str]:
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

        for candidate in extract_static_pdf_urls(response.text[:200000], response.url):
            if self._verify_pdf_candidate(candidate):
                return candidate

        return None

    def _verify_pdf_candidate(self, url: str) -> bool:
        try:
            self._rate_limit()
            response = self.session.head(url, timeout=self.verify_timeout, allow_redirects=True)
            content_type = response.headers.get("content-type", "")
            return response.status_code == 200 and ("pdf" in content_type.lower() or _looks_like_pdf_url(response.url))
        except Exception:
            return _looks_like_pdf_url(url)

    def _download_pdf(self, url: str, title: str, method: str, paper_id: str = None) -> Optional[Path]:
        """Download without Selenium fallback; parse returned HTML once for a PDF link."""
        self._rate_limit()

        try:
            response = self.session.get(url, timeout=self.download_timeout, allow_redirects=True)
        except Exception:
            self._last_failure_class = "network_error"
            return None

        content_type = response.headers.get("content-type", "")
        first_bytes = response.content[:20]
        if response.status_code == 200 and (b"%PDF" in first_bytes or "pdf" in content_type.lower()):
            self._last_success_class = self._last_success_class or "http_pdf"
            return self._save_pdf_bytes(response.content, title, method, paper_id)

        is_html_response = "html" in content_type.lower() or b"<html" in response.content[:1000].lower()
        if response.status_code == 200 and is_html_response:
            for candidate in extract_static_pdf_urls(response.text[:200000], response.url):
                try:
                    retry = self.session.get(candidate, timeout=self.download_timeout, allow_redirects=True)
                except Exception:
                    continue
                retry_type = retry.headers.get("content-type", "")
                if retry.status_code == 200 and (b"%PDF" in retry.content[:20] or "pdf" in retry_type.lower()):
                    self._last_success_class = self._last_success_class or "html_pdf_link"
                    return self._save_pdf_bytes(retry.content, title, method, paper_id)
                self._last_failure_class = self._classify_response_failure(method, candidate, retry)
                self._last_failure_detail = f"HTTP {retry.status_code}"

        failure_class = self._classify_response_failure(method, url, response)
        if failure_class:
            self._last_failure_class = failure_class
            self._last_failure_detail = f"HTTP {response.status_code}"

        if failure_class == "pdf_endpoint_cloudflare":
            curl_path = self._download_pdf_with_curl_cffi(url, title, method, paper_id)
            if curl_path:
                return curl_path

        if is_html_response and self.enable_browser_fallback and self._is_browser_worthy_html(response):
            self.browser_fallback_attempts += 1
            browser_path = self._download_pdf_with_browser(url, title, method, paper_id)
            if browser_path:
                self.browser_fallback_successes += 1
                return browser_path
            if self._is_article_print_retry_candidate(url, method):
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

    def _classify_response_failure(self, method: str, url: str, response) -> Optional[str]:
        url_lower = (url or "").lower()
        content_lower = response.content[:8000].lower()

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
        if response.status_code in (401, 403, 429) or any(marker in content_lower for marker in cloudflare_markers):
            if _looks_like_pdf_url(url_lower) or "/doi/pdf" in url_lower or "/doi/epdf" in url_lower or method.startswith("publisher_"):
                return "pdf_endpoint_cloudflare"
            return "access_blocked"

        content_type = response.headers.get("content-type", "").lower()
        if response.status_code == 200 and ("html" in content_type or b"<html" in response.content[:1000].lower()):
            return "non_pdf_html"
        if response.status_code >= 400:
            return f"http_{response.status_code}"
        return None

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
                self._last_success_class = "curl_cffi_pdf"
                return self._save_pdf_bytes(content, title, method, paper_id)

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
                        self._last_success_class = "curl_cffi_pdf_link"
                        return self._save_pdf_bytes(retry_content, title, method, paper_id)

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
        if not self._is_article_print_retry_candidate(url, method):
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
                        self._last_success_class = "browser_download"
                        return file_path
                    except Exception:
                        pass

                response = page.goto(url, wait_until="domcontentloaded", timeout=self.browser_timeout * 1000)
                if response and "pdf" in (response.headers.get("content-type", "").lower()):
                    body = response.body()
                    if body.startswith(b"%PDF"):
                        file_path = self._save_pdf_bytes(body, title, method, paper_id)
                        return file_path

                page.wait_for_timeout(min(3000, self.browser_timeout * 1000))
                html = page.content()
                if method.startswith("publisher_") and self._is_verified_article_page(page.url, page.title(), html, title):
                    file_path = self._browser_output_path(title, method, paper_id)
                    page.pdf(path=str(file_path), format="Letter", print_background=True)
                    if file_path.exists() and file_path.read_bytes()[:4] == b"%PDF":
                        self._last_success_class = "article_printable"
                        return file_path

                for candidate in extract_static_pdf_urls(html[:200000], page.url):
                    try:
                        pdf_response = page.goto(candidate, wait_until="domcontentloaded", timeout=self.browser_timeout * 1000)
                        if pdf_response and "pdf" in (pdf_response.headers.get("content-type", "").lower()):
                            body = pdf_response.body()
                            if body.startswith(b"%PDF"):
                                self._last_success_class = "browser_pdf_link"
                                file_path = self._save_pdf_bytes(body, title, method, paper_id)
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

        expected_words = [
            word for word in re.findall(r"[a-z0-9]+", (expected_title or "").lower())
            if len(word) >= 4
        ]
        if not expected_words:
            return False

        page_text = f"{page_title_lower} {page_html_lower[:50000]}"
        overlap = sum(1 for word in expected_words[:16] if word in page_text)
        full_text_markers = (
            "abstract",
            "references",
            "introduction",
            "methods",
            "results",
            "discussion",
            "conclusion",
            "original reports",
        )
        has_full_text_marker = any(marker in page_html_lower for marker in full_text_markers)
        return overlap >= min(5, len(expected_words)) and has_full_text_marker

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
