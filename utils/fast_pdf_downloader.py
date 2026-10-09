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
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager, nullcontext
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, urljoin, urlparse

from lxml import html as lxml_html

from utils.oa_sources import (
    OAMetadataIndex,
    api_user_agent,
    arxiv_id_from_text,
    arxiv_title_matches,
    crossref_has_open_licence,
    crossref_published_dois,
    crossref_related_dois,
    europepmc_fulltext_urls,
    openalex_title_versions,
    surnames,
    doaj_fulltext_links,
    dspace7_pdf_urls,
    figshare_article_id,
    figshare_article_id_from_url,
    figshare_pdf_urls,
    handle_from_url,
    nva_pdf_urls,
    nva_publication_id,
    ieee_oa_pdf_urls,
    is_metadata_url,
    jmir_asset_pdf_urls,
    normalize_doi,
    normalize_pmcid,
    ojs_download_url,
    osf_id_from,
    osf_pdf_urls,
    paper_pmid,
    pmc_cloud_pdf_url,
    repository_pdf_urls,
    zenodo_pdf_urls,
)
from utils.pdf_downloader import CascadePDFDownloader


# Markers that identify a URL as a PDF endpoint. Only these legacy markers make a
# URL eligible for the curl_cffi transport; the repository markers below widen
# discovery (static HTML, OA locations) without widening that transport.
CURL_CFFI_PDF_URL_MARKERS = (
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

PDF_URL_MARKERS = CURL_CFFI_PDF_URL_MARKERS + (
    "/article/download/",  # OJS galley download
    "viewcontent.cgi",  # bepress Digital Commons
    "/bitstream/",  # DSpace 6
    "/bitstreams/",  # DSpace 7
    "/smash/get/",  # DiVA
    "/download_pub",  # Preprints.org
    "servlets/purl",  # OSTI
    "zenodo.org/api/records/",
    "osf.io/download/",
    "mfr.osf.io/export",
)

# Publishers whose own sites answer every programmatic request with a JavaScript
# challenge; their papers are fetched only through repositories and OA copies.
CHALLENGE_ONLY_PUBLISHERS = {"jmir"}
CHALLENGE_ONLY_HOSTS = (
    "jmir.org",
    "researchprotocols.org",
    "i-jmr.org",
    "jmir.pub",
    "jmirx.org",
    "iproc.org",
    # Preprint servers that challenge every client (no official programmatic route).
    "ssrn.com",
    "techrxiv.org",
    "preprints.org",
)

# Repository and API hosts that expect an identifying, non-browser User-Agent
# (Zenodo refuses browser UAs; Anubis-protected repositories exempt them).
HONEST_UA_HOSTS = (
    "zenodo.org",
    "osf.io",
    "hal.science",
    "archives-ouvertes.fr",
    "pmc-oa-opendata.s3.amazonaws.com",
    "api.crossref.org",
    "api.openalex.org",
    "export.arxiv.org",
    # Springer's Fastly "Client Challenge" targets browser-like clients; an identified
    # tool receives the open-access PDF (303 to idp.springer.com sets a cookie).
    "link.springer.com",
    "idp.springer.com",
    "springeropen.com",
    "figshare.com",
    "osti.gov",
    "api.nva.unit.no",
    "doaj.org",
    "hdl.handle.net",
    "content.openalex.org",
    "api.elsevier.com",
    "api.wiley.com",
)
# Hosts that served a PDF only to the honest User-Agent during this process.
_LEARNED_HONEST_HOSTS: set = set()
_LEARNED_HONEST_LOCK = threading.Lock()

# Wolters Kluwer journals (LWW, Medknow, ...) hosted on Ovid.
OVID_DOI_PREFIXES = ("10.1097/", "10.4103/", "10.1213/", "10.1227/", "10.1249/", "10.1212/")

# Metered or rate-limited keyed routes, shared by all workers in the process.
_KEYED_ROUTE_LOCK = threading.Lock()
_KEYED_ROUTE_STATE = {"openalex_content_used": 0, "openalex_content_disabled": False, "wiley_calls": []}

ANUBIS_MARKERS = (b"making sure you&#39;re not a bot", b"making sure you're not a bot", b"/.within.website/")

# MDPI DOI journal codes whose site slug differs from the code.
MDPI_JOURNAL_SLUGS = {
    "info": "information",
    "s": "sensors",
    "bs": "behavsci",
    "app": "applsci",
    "dj": "dentistry",
    "bdcc": "BDCC",
    "fi": "futureinternet",
    "en": "energies",
}
MDPI_PROCEEDINGS_CODES = {"engproc", "proceedings", "csmf", "asec", "ecsa", "iocag", "cmsf", "environsciproc"}

_ARXIV_API_LOCK = threading.Lock()
_ARXIV_API_LAST_CALL = [0.0]

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

# Challenge classes that cool down a whole domain wherever they occur (not only on
# PDF endpoints). A plain 401/403 is "access_denied" and never cools a domain.
DOMAIN_COOLDOWN_FAILURE_CLASSES = {
    "pmc_recaptcha",
    "pmc_pow_challenge",
    "antibot_challenge",
    "metadata_api_429",
    "rate_limited",
}

NON_BROWSER_PDF_ENDPOINT_FAILURE_CLASSES = {
    "pdf_endpoint_tdm_blocked",
    "pdf_endpoint_waf",
    "antibot_challenge",
    "pmc_pow_challenge",
    "repository_bot_check",
}

PRIMARY_FAILURE_CLASS_PRIORITY = {
    "pdf_title_mismatch": 100,
    "article_print_incomplete": 95,
    "article_print_paywalled": 94,
    "pmc_recaptcha": 90,
    "pmc_pow_challenge": 90,
    "pmc_not_open_access": 89,
    "publisher_paywalled": 88,
    "pdf_endpoint_tdm_blocked": 85,
    "pdf_endpoint_waf": 84,
    "pdf_endpoint_cloudflare": 83,
    "antibot_challenge": 82,
    "repository_bot_check": 81,
    "domain_cooldown_skip": 80,
    "access_denied": 78,
    "rate_limited": 77,
    "article_print_failed": 75,
    "wrong_publisher_detection": 70,
    "non_pdf_html": 60,
    "access_blocked": 55,
    "pmc_not_in_oa_cloud": 40,
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
    if path.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".svg", ".ico", ".css", ".js")):
        return True
    # Repository cover sheets, title pages and abstract-only parts; T&F figshare supplements (_smNNNN).
    if re.search(r"(_cover\.|_title\.pdf|abstrak|[-_]abstract\.pdf|_sm\d{3,}\b)", f"{path}?{query}"):
        return True
    candidate_text = f"{filename}?{query}"
    markers = (
        "supple",
        "supplement",
        "supplementary",
        "appendix",
        "multimedia",
        "thumb",
    )
    if any(marker in candidate_text for marker in markers):
        return True
    # Publisher supplementary-file names: Elsevier mmcN, Springer MOESM/ESM, JMIR _appN, PLOS .s001
    if re.search(r"(^|[-_.=])(mmc\d+|moesm\d+|esm\d*|app\d+|s\d{3})([._-]|$)", candidate_text):
        return True
    return bool(re.search(r"(^|[-_])(fig|figure|table|tbl|f|t)\d+([._-]|$)", candidate_text))


def _host_matches(url: str, hosts) -> bool:
    host = urlparse(url or "").netloc.lower()
    return any(host == h or host.endswith(f".{h}") for h in hosts)


# Topic-neutral title words; they count a quarter as much as a paper's distinctive words in the
# title gate. Words generic for one review's topic (e.g. "language model" in an LLM review) are
# learned per batch from the titles themselves (see batch_generic_title_tokens).
GENERIC_TITLE_TOKENS = {
    "based", "study", "studies", "review", "systematic", "scoping", "analysis", "approach", "evaluation",
    "evaluating", "research", "data", "use", "case", "applications", "application", "towards", "toward",
    "role", "potential", "impact", "new", "framework", "system", "systems", "tool", "tools", "performance",
    "method", "methods", "results", "effect", "effects", "development", "design", "assessment",
}


def batch_generic_title_tokens(titles: Iterable[str], min_share: float = 0.04, min_count: int = 5) -> set:
    """Title words frequent across this batch (the review's topic words), plus the neutral core.

    In a batch about one topic, words that occur in many titles do not tell its papers apart, so
    the title gate gives them a quarter weight. Small batches fall back to the neutral core.
    """
    titles = [title for title in titles if title]
    counts: Dict[str, int] = {}
    for title in titles:
        for token in set(_title_match_tokens(title)):
            counts[token] = counts.get(token, 0) + 1
    threshold = max(min_count, int(min_share * len(titles)))
    learned = {token for token, count in counts.items() if count >= threshold} if len(titles) >= 2 * min_count else set()
    return set(GENERIC_TITLE_TOKENS) | learned
# Routes that fetch by the paper's own identifier: a textless PDF from them is still the paper.
IDENTIFIER_TRUSTED_METHODS = {
    "pmc_cloud", "pmc", "arxiv", "arxiv_twin", "direct_pdf", "europepmc", "publisher_zenodo", "publisher_osf",
    "publisher_figshare", "publisher_acl", "publisher_arxiv",
}


# Routes that fetch the record's *own* repository deposit (its arXiv id, or a repository
# DOI). Identifiers derived from another source (a PMCID or an arXiv twin found via the
# DOI) are not on this list: a wrong DOI in the record would make them another paper.
RECORD_ID_METHODS = {
    "arxiv", "publisher_zenodo", "publisher_osf", "publisher_figshare", "publisher_acl", "publisher_arxiv",
}


def _weighted_coverage(title_tokens: List[str], text_tokens: set, generic: Optional[set] = None) -> Tuple[float, int, int]:
    """(weighted coverage, distinctive hits, distinctive tokens) of title words in a text."""
    generic = GENERIC_TITLE_TOKENS if generic is None else generic
    weight = lambda token: 0.25 if token in generic else 1.0
    total = sum(weight(token) for token in title_tokens) or 1.0
    covered = sum(weight(token) for token in title_tokens if token in text_tokens)
    distinctive = [token for token in title_tokens if token not in generic]
    return covered / total, sum(token in text_tokens for token in distinctive), len(distinctive)


_ENGLISH_MARKERS = {"the", "and", "of", "in", "to", "for", "with", "is", "are", "this", "that", "was", "were", "on"}


def _text_is_english(text: str) -> bool:
    words = re.findall(r"[^\W\d_]+", (text or "").lower())
    if len(words) < 30:
        return True
    letters = [ch for ch in text if ch.isalpha()]
    if letters and sum(ord(ch) > 0x24F for ch in letters) / len(letters) > 0.3:
        return False
    return sum(word in _ENGLISH_MARKERS for word in words) / len(words) >= 0.04


_NON_ENGLISH_FUNCTION_WORDS = {
    "de", "da", "do", "das", "dos", "la", "el", "los", "las", "en", "para", "con", "por", "del", "une", "des",
    "der", "die", "und", "mit", "für", "och", "av", "för", "og", "til", "het", "een", "van", "il", "di", "della",
}


def _looks_non_english_title(text: str) -> bool:
    if any(ch.isalpha() and ord(ch) > 0x7F for ch in text or ""):
        return True
    words = re.findall(r"[a-z]+", (text or "").lower())
    return sum(word in _NON_ENGLISH_FUNCTION_WORDS for word in words) >= 2


def _title_overlap(a: str, b: str) -> float:
    tokens_a, tokens_b = set(_title_match_tokens(a)), set(_title_match_tokens(b))
    return len(tokens_a & tokens_b) / max(1, min(len(tokens_a), len(tokens_b)))


def _front_page_is_supplement(text: str) -> bool:
    """True when page 1 opens like a supplementary file rather than an article."""
    head = re.sub(r"\s+", " ", (text or "")[:200]).strip().lower()
    return bool(re.match(
        r"^(multimedia\s+appendix|supplementary\s+(material|information|appendix|file|data|table|figure)s?|"
        r"supplemental\s+(material|digital content)|online\s+(supplement|resource|appendix)|"
        r"appendix\s+[0-9a-z]\b|additional\s+file\s+\d|s\d+\s+(table|fig|figure|file|appendix|text))",
        head,
    ))


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


def _declared_citation_title(html_text: str) -> str:
    """citation_title / DC.title a landing page declares for its item (any language)."""
    match = re.search(
        r"<meta[^>]+name=[\"'](?:citation_title|dc\.title|DC\.title)[\"'][^>]+content=[\"']([^\"']{8,500})[\"']",
        html_text or "",
        flags=re.IGNORECASE,
    )
    return stdlib_html.unescape(match.group(1)).strip() if match else ""


def extract_static_pdf_urls(html_text: str, base_url: str) -> List[str]:
    """Extract likely PDF URLs from static publisher HTML."""
    if not html_text:
        return []

    candidates: List[str] = []
    # Declared by the page itself (citation metadata, typed links): kept even when the
    # URL has no PDF-looking marker (e.g. DSpace /bitstreams/<uuid>/download).
    declared: List[str] = []

    try:
        doc = lxml_html.fromstring(html_text)
    except Exception:
        doc = None

    if doc is not None:
        declared_xpaths = [
            "//meta[translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='citation_pdf_url']/@content",
            "//meta[translate(@property, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='citation_pdf_url']/@content",
            "//meta[translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='bepress_citation_pdf_url']/@content",
            "//link[translate(@type, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='application/pdf']/@href",
        ]
        for xpath in declared_xpaths:
            declared.extend(doc.xpath(xpath))
        candidates.extend(declared)
        candidates.extend(doc.xpath(
            "//meta[contains(translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'pdf')]/@content"
        ))

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
        # Quoted .pdf paths inside scripts / window.open(...) / onclick handlers.
        r'["\']((?:https?://|/)[^"\'\s<>]+\.pdf)["\']',
    ]
    for pattern in regex_patterns:
        candidates.extend(re.findall(pattern, html_text, flags=re.IGNORECASE))

    declared_absolute = {urljoin(base_url, str(value).strip()) for value in declared if str(value).strip()}
    absolute = []
    for candidate in candidates:
        candidate = str(candidate).strip()
        if not candidate or candidate.startswith(("mailto:", "javascript:", "#")):
            continue
        absolute_url = urljoin(base_url, candidate)
        parsed = urlparse(absolute_url)
        if parsed.scheme not in {"http", "https"}:
            continue
        ojs = ojs_download_url(absolute_url)
        if ojs:
            # OJS galley viewer link: the direct download is the PDF.
            absolute.append(ojs)
        if absolute_url in declared_absolute or _looks_like_pdf_url(absolute_url):
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


def _config_value(name: str) -> Optional[str]:
    try:
        import config
    except Exception:
        return None
    value = getattr(config, name, None)
    return str(value) if value else None


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
        if failure_class not in PDF_ENDPOINT_COOLDOWN_FAILURE_CLASSES | DOMAIN_COOLDOWN_FAILURE_CLASSES:
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
        oa_index: Optional[OAMetadataIndex] = None,
        max_candidates_per_method: int = 6,
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
        # Batch-shared open-access metadata (OpenAlex, Semantic Scholar, NCBI ID converter).
        # REVIEWPILOT_PDF_OA_INDEX=0 disables it (offline tests).
        self.oa_index_enabled = os.getenv("REVIEWPILOT_PDF_OA_INDEX", "1") != "0"
        self.oa_index = oa_index
        self.max_candidates_per_method = max(1, max_candidates_per_method)
        # Batch-learned topic words (set in download_batch); single downloads use the neutral core.
        self.generic_title_tokens: set = set(GENERIC_TITLE_TOKENS)
        self._current_paper_doi = ""
        self._tried_urls: set = set()
        self._paper_meta: Dict = {"locations": []}
        self._reset_paper_request_state()
        # Official keyed routes, used only when the user configured a key.
        self.elsevier_api_key = os.getenv("ELSEVIER_API_KEY") or _config_value("ELSEVIER_API_KEY") or _config_value("SCOPUS_API_KEY")
        self.wiley_tdm_token = os.getenv("WILEY_TDM_TOKEN") or _config_value("WILEY_TDM_TOKEN")
        self.openalex_api_key = os.getenv("OPENALEX_API_KEY") or _config_value("OPENALEX_API_KEY")
        self.openalex_content_max = _env_int("OPENALEX_CONTENT_MAX_PER_RUN", 100)

    def _reset_paper_request_state(self) -> None:
        """Per-paper request state: URL-specific headers/params, honest-UA URLs, alt titles."""
        self._url_headers: Dict[str, Dict[str, str]] = {}
        self._url_params: Dict[str, Dict[str, str]] = {}
        self._url_min_pages: Dict[str, int] = {}
        self._repository_urls: set = set()
        self._alt_titles: List[str] = []
        self._network_error_urls: List[str] = []
        self._active_download_url = ""

    def _new_browser_profile_dir(self) -> Path:
        run_id = f"run-{os.getpid()}-{int(self.time_func() * 1000)}-{id(self)}"
        return Path(".cache/playwright_fast_pdf_profile") / "runs" / run_id

    def download(self, paper: Dict) -> Tuple[bool, str, Optional[str]]:
        """Download with identifier-first ordering and method-level telemetry.

        Order: the record's own PDF link, identifier routes into official open
        copies (arXiv, PMC Cloud), the publisher, every indexed OA location
        (OpenAlex, Semantic Scholar, Unpaywall, then repository landing pages),
        landing-page discovery, preprint routes, and finally browser printing.
        A method may yield several candidates; each URL is fetched at most once.
        """
        self.last_method_timings = []
        self.last_failure_detail = None
        self.last_success_class = None

        title = paper.get("title", "unknown")
        doi = paper.get("doi")
        direct_url = paper.get("pdf_url") or paper.get("url")
        paper_id = paper.get("paper_id")
        journal = paper.get("journal", "")
        source = (paper.get("source") or "").lower()
        pmid = paper_pmid(paper) or None

        meta = self._oa_metadata(paper)
        if not doi and meta.get("doi"):
            doi = meta["doi"]
        pmid = pmid or meta.get("pmid") or None
        pmcid = normalize_pmcid(paper.get("pmcid")) or meta.get("pmcid") or ""
        own_arxiv_id = paper.get("arxiv_id") or (
            arxiv_id_from_text(f"{paper.get('id') or ''} {direct_url or ''}") if source == "arxiv" or "arxiv.org" in (direct_url or "").lower() else ""
        )
        twin_arxiv_id = meta.get("arxiv_id") or ""
        self._current_paper_doi = normalize_doi(doi)
        self._paper_authors = list(paper.get("authors") or [])[:5]
        self._reset_paper_request_state()
        self._tried_urls = set()
        self._definitive_failed_urls = set()
        self._cooldown_skipped_urls = []
        self._paper_meta = meta
        self._unpaywall_landings = []

        self.last_publisher_detection = self._resolve_publisher(doi, journal)
        detected_publisher = self.last_publisher_detection["selected_publisher"]
        challenge_only = detected_publisher in CHALLENGE_ONLY_PUBLISHERS

        methods: List[Tuple[str, Callable[[], object]]] = []

        # Layer 1: the record's own link and identifier routes into official open copies.
        if direct_url and not is_metadata_url(direct_url):
            methods.append(("direct_pdf", lambda: self._try_direct_pdf_url(direct_url)))
        if own_arxiv_id or source == "arxiv":
            methods.append(("arxiv", lambda: self._arxiv_version_candidates(own_arxiv_id or paper.get("id"), title)))
        if pmcid:
            methods.append(("pmc_cloud", lambda: self._try_pmc_cloud(pmcid)))
        # Author copies linked from the abstract (project pages) beat a publisher paywall.
        methods.append(("abstract_static_html", lambda: self._try_abstract_link_pdf(paper)))
        legacy_semantic_scholar = not self.oa_index_enabled
        if legacy_semantic_scholar and (self._has_semantic_scholar_cache(doi, title) or self.semantic_scholar_api_key):
            # Without the batch index, Semantic Scholar is queried per paper as before.
            methods.append(("semantic_scholar", lambda: self._try_semantic_scholar(doi, title)))
            legacy_semantic_scholar = False
        pmc_early = source == "pubmed" and bool(pmid) and not doi and not pmcid
        if pmc_early:
            # PubMed records without a DOI: PMC is the only identifier route.
            methods.append(("pmc", lambda: self._try_pmc(pmid, doi)))
        if detected_publisher:
            if detected_publisher == "biorxiv":
                publisher_method = lambda: self._try_biorxiv_medrxiv(doi, title)
            else:
                publisher_method = self._get_publisher_method(detected_publisher, doi, direct_url)
            if publisher_method:
                methods.append((f"publisher_{detected_publisher}", publisher_method))

        # Layer 2: every indexed open-access location, then repository landing pages.
        methods.append(("oa_locations", lambda: self._oa_location_candidates(meta, doi, pmcid)))
        methods.append(("unpaywall", lambda: self._try_unpaywall(doi)))
        if legacy_semantic_scholar:
            methods.append(("semantic_scholar", lambda: self._try_semantic_scholar(doi, title)))
        methods.append(("oa_landing_pages", lambda: self._oa_landing_page_candidates(meta, doi)))
        if twin_arxiv_id and twin_arxiv_id != own_arxiv_id:
            methods.append(("arxiv_twin", lambda: self._arxiv_version_candidates(twin_arxiv_id, title)))

        # Layer 3: landing-page discovery on the record and publisher pages.
        if doi and normalize_doi(doi).startswith(OVID_DOI_PREFIXES):
            methods.append(("ovid_open_access", lambda: self._try_ovid_open_access(doi)))
        if not challenge_only:
            if direct_url and not is_metadata_url(direct_url):
                methods.append(("static_html", lambda: self._try_static_html_pdf(direct_url, title)))
            methods.append(("doi_static_html", lambda: self._try_static_html_pdf(f"https://doi.org/{doi}", title) if doi else None))
            methods.append(("publisher_url", lambda: self._try_publisher_pattern(direct_url, doi)))

        # Layer 4: preprints and remaining repositories.
        if not own_arxiv_id and not twin_arxiv_id:
            methods.append(("arxiv", lambda: self._try_arxiv(None, title)))
        methods.append(("biorxiv", lambda: self._try_biorxiv_medrxiv(doi, title)))
        methods.append(("preprint_lookup", lambda: self._try_find_preprint(doi, title)))
        methods.append(("published_version", lambda: self._published_version_candidates(doi, title)))
        if not pmc_early:
            methods.append(("pmc", lambda: self._try_pmc(pmid, doi)))
        methods.append(("europepmc", lambda: self._try_europe_pmc(pmid, doi)))
        if not challenge_only:
            methods.append(("article_preprint_pdf", lambda: self._try_article_preprint_pdf(paper)))
            methods.append(("doi_redirect", lambda: self._try_doi_redirect(doi)))
        methods.append(("core", lambda: self._try_core(doi, title)))
        methods.append(("network_retry", lambda: list(self._network_error_urls)))
        if self.openalex_api_key and meta.get("content_pdf"):
            # Metered official route (OpenAlex's cached OA copy): after every free route.
            methods.append(("openalex_content", lambda: self._openalex_content_candidate(meta)))

        # Keep LLM search opt-in only. The speed benchmark disables it by default.
        if self.use_web_search:
            methods.append(("web_search", lambda: self._try_llm_web_search(title, doi, journal)))
        if not challenge_only:
            methods.append(("verified_article_print_pdf", lambda: self._try_verified_article_print_pdf(paper)))

        for method_name, method_func in methods:
            started = time.perf_counter()
            pdf_url = None
            success = False
            error = None
            candidates_tried = 0
            self._last_failure_class = None
            self._last_failure_detail = None
            self._last_success_class = None
            try:
                for candidate in self._iter_candidates(method_func()):
                    if candidate in self._tried_urls and method_name != "network_retry":
                        continue
                    self._tried_urls.add(candidate)
                    pdf_url = candidate
                    candidates_tried += 1
                    file_path = self._download_with_domain_policy(candidate, title, method_name, paper_id)
                    if file_path:
                        success = True
                        self.last_success_class = self._last_success_class
                        return True, method_name, str(file_path)
                    if candidates_tried >= self.max_candidates_per_method:
                        break
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                self._last_failure_class = self._last_failure_class or "method_exception"
                self._last_failure_detail = self._last_failure_detail or error
            finally:
                self.last_method_timings.append({
                    "method": method_name,
                    "seconds": round(time.perf_counter() - started, 3),
                    "candidate_url": pdf_url,
                    "candidates_tried": candidates_tried,
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
            # A deferred article print no longer ends the cascade: the remaining
            # sources still run, and the batch retries the print only if all fail.

        return False, "none", "All download methods failed"

    @staticmethod
    def _iter_candidates(result) -> Iterable[str]:
        if not result:
            return
        if isinstance(result, str):
            yield result
            return
        for candidate in result:
            if candidate:
                yield str(candidate)

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
        self._prefetch_oa_metadata([paper for _, paper in pending])
        self.generic_title_tokens = batch_generic_title_tokens(paper.get("title") for paper in papers)
        self._batch_definitive_failed_urls: Dict[int, set] = {}
        self._batch_cooldown_skipped_urls: Dict[int, List[str]] = {}

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
                    "definitive_failed_urls": set(getattr(downloader, "_definitive_failed_urls", set())),
                    "cooldown_skipped_urls": list(getattr(downloader, "_cooldown_skipped_urls", [])),
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
                    self._batch_definitive_failed_urls[index] = row.get("definitive_failed_urls") or set()
                    self._batch_cooldown_skipped_urls[index] = row.get("cooldown_skipped_urls") or []
                    self._apply_batch_result(row, papers[index], results)
                    if progress_callback:
                        progress_callback(completed, len(papers), papers[index].get("title", "")[:50])
                    if progress_file:
                        self._append_batch_progress(progress_file, papers[index])
            self._close_spawned_downloaders(spawned_downloaders)
            spawned_downloaders = []
            self._retry_failed_open_access_pdfs(papers, results, progress_file)
            self._retry_failed_via_other_versions(papers, results, progress_file)
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
                # Only papers whose cascade deferred an article print get the (slow,
                # browser-based) retry; the others have no printable landing page.
                recorded_classes = paper.get("pdf_failure_classes") or (
                    [paper["pdf_failure_class"]] if paper.get("pdf_failure_class") else []
                )
                if recorded_classes and "article_print_failed" not in recorded_classes:
                    continue
                retry_target = self._article_print_retry_target_for_paper(paper)
                if not retry_target:
                    continue
                method, url = retry_target
                if _host_matches(url, CHALLENGE_ONLY_HOSTS) or self._is_metadata_article_source_url(url):
                    continue
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
        """Sequentially rescue failed papers with trusted OA PDF URLs.

        The rescue bypasses domain cooldowns, so it retries what the cascade skipped
        under a cooldown or lost to a network error; URLs that already failed
        definitively in the cascade are not fetched again.
        """
        definitive = getattr(self, "_batch_definitive_failed_urls", {}) or {}
        skipped = getattr(self, "_batch_cooldown_skipped_urls", {}) or {}
        attempts = 1 if self.oa_index_enabled else 2
        for index, paper in enumerate(papers):
            if paper.get("pdf_downloaded"):
                continue

            title = paper.get("title", "unknown")
            paper_id = paper.get("paper_id")
            self._current_paper_doi = normalize_doi(paper.get("doi"))
            already_failed = definitive.get(index, set())
            for _attempt in range(attempts):
                rescued = False
                candidates = _dedupe_keep_order(self._open_access_rescue_candidates(paper) + list(skipped.get(index, [])))
                for candidate in candidates:
                    if candidate in already_failed:
                        continue
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

    def _retry_failed_via_other_versions(
        self,
        papers: List[Dict],
        results: Dict,
        progress_file: Optional[str],
    ) -> None:
        """Last pass for papers still failing: other versions of the same work.

        Batched lookups (Crossref preprint/version relations, Europe PMC full-text links,
        arXiv title search at 1 request / 3 s, OpenAlex title search within its daily
        budget) run once for all failed papers; each paper then tries at most six
        candidates, which still pass the record's title gate. Versions are labelled
        "other_version" so a reviewer can see that the file is not the publisher copy.
        """
        if not self.oa_index_enabled:
            return
        failed = [(index, paper) for index, paper in enumerate(papers) if not paper.get("pdf_downloaded")]
        if not failed:
            return
        headers = self._api_headers()
        dois = [normalize_doi(paper.get("doi")) for _, paper in failed if paper.get("doi")]
        titles = [paper.get("title") for _, paper in failed if paper.get("title")]
        lookups = {}
        for name, call in (
            ("related", lambda: crossref_related_dois(self.session, dois, headers)),
            ("europepmc", lambda: europepmc_fulltext_urls(self.session, dois, headers)),
            ("arxiv", lambda: arxiv_title_matches(self.session, titles, headers, sleep_func=self.sleep_func)),
            ("openalex", lambda: openalex_title_versions(
                self.session, titles, headers, email=self.email, api_key=self.openalex_api_key or "")),
        ):
            try:
                lookups[name] = call() or {}
            except Exception:
                lookups[name] = {}
        definitive = getattr(self, "_batch_definitive_failed_urls", {}) or {}
        for index, paper in failed:
            doi = normalize_doi(paper.get("doi"))
            title = paper.get("title") or ""
            self._reset_paper_request_state()
            self._current_paper_doi = doi
            self._paper_meta = self._oa_metadata(paper)
            candidates: List[str] = []
            for url in lookups["europepmc"].get(doi, []):
                candidates.extend(self._rewrite_oa_url(url))
            # A title match alone identifies a work only when the title is distinctive;
            # otherwise ("Large Language Models") an author surname must match too.
            record_surnames = surnames((paper.get("authors") or [])[:5])
            distinctive_title = sum(
                token not in self.generic_title_tokens for token in _title_match_tokens(title)
            ) >= 3

            def same_work(candidate_authors) -> bool:
                if record_surnames and surnames(candidate_authors) & record_surnames:
                    return True
                return distinctive_title and not record_surnames

            arxiv_match = lookups["arxiv"].get(title)
            if arxiv_match and same_work(arxiv_match.get("authors")):
                candidates.extend(self._arxiv_version_candidates(arxiv_match["id"]))
            for related in lookups["related"].get(doi, []):
                if related.startswith("10.48550/arxiv."):
                    candidates.extend(self._arxiv_version_candidates(arxiv_id_from_text(related)))
                    continue
                meta = self._oa_metadata({"doi": related})
                if meta.get("pmcid"):
                    cloud = self._try_pmc_cloud(meta["pmcid"])
                    if cloud:
                        candidates.append(cloud)
                candidates.extend(self._oa_location_candidates(meta, related))
            for work in lookups["openalex"].get(title, []):
                work_doi = normalize_doi(work.get("doi"))
                if doi and work_doi == doi:
                    continue
                work_authors = [((a or {}).get("author") or {}).get("display_name") for a in work.get("authorships") or []]
                if not same_work(work_authors):
                    continue
                if work_doi.startswith("10.48550/arxiv."):
                    candidates.extend(self._arxiv_version_candidates(arxiv_id_from_text(work_doi)))
                for location in [work.get("best_oa_location")] + list(work.get("locations") or []):
                    pdf_url = (location or {}).get("pdf_url")
                    if pdf_url and not is_metadata_url(pdf_url) and not _host_matches(pdf_url, CHALLENGE_ONLY_HOSTS):
                        candidates.extend(self._rewrite_oa_url(pdf_url))
            already_failed = definitive.get(index, set())
            tried = 0
            for candidate in _dedupe_keep_order([c for c in candidates if c]):
                if candidate in already_failed:
                    continue
                if tried >= 6:
                    break
                tried += 1
                self._strict_title_gate = True
                try:
                    file_path = self._download_with_domain_policy(candidate, title, "other_version", paper.get("paper_id"))
                finally:
                    self._strict_title_gate = False
                if file_path:
                    self._record_batch_retry_success(paper, results, "other_version", file_path)
                    if progress_file:
                        self._append_batch_progress(progress_file, paper)
                    break

    def _open_access_rescue_candidates(self, paper: Dict) -> List[str]:
        title = paper.get("title", "unknown")
        doi = paper.get("doi")
        candidates: List[str] = []

        if self.oa_index_enabled:
            # Built from the batch metadata already fetched: no further API calls.
            meta = self._oa_metadata(paper)
            direct_url = paper.get("pdf_url") or ""
            if _looks_like_pdf_url(direct_url):
                candidates.append(direct_url)
            pmcid = normalize_pmcid(paper.get("pmcid")) or meta.get("pmcid")
            if pmcid:
                cloud = self._try_pmc_cloud(pmcid)
                if cloud:
                    candidates.append(cloud)
            candidates.extend(self._oa_location_candidates(meta, doi))
            if (doi or "").lower().startswith(("10.1101/", "10.64898/")):
                preprint_pdf = self._try_biorxiv_medrxiv(doi, title)
                if preprint_pdf:
                    candidates.append(preprint_pdf)
            return _dedupe_keep_order(candidates)

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
            oa_index=self.oa_index,
            max_candidates_per_method=self.max_candidates_per_method,
        )
        retry_downloader.core_api_key = self.core_api_key
        retry_downloader.generic_title_tokens = self.generic_title_tokens
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
            oa_index=self.oa_index,
            max_candidates_per_method=self.max_candidates_per_method,
        )
        worker.core_api_key = self.core_api_key
        worker.generic_title_tokens = self.generic_title_tokens
        if self.llm_query_func:
            worker.set_llm_query_func(self.llm_query_func)
        if self.use_web_search:
            worker.enable_web_search(self.web_search_model)
        return worker

    def _paper_primary_domain(self, paper: Dict) -> str:
        direct_url = paper.get("pdf_url") or paper.get("url")
        direct_domain = self._domain_for_url(direct_url or "")
        aggregator_domains = {"pubmed.ncbi.nlm.nih.gov", "openalex.org", "doi.org", "dx.doi.org"}
        if direct_domain and direct_domain not in aggregator_domains:
            return direct_domain

        publisher = self._resolve_publisher(paper.get("doi"), paper.get("journal", "")).get("selected_publisher")
        publisher_domain = self._publisher_primary_domain(publisher)
        if publisher_domain:
            return publisher_domain
        # Unknown publishers are grouped by DOI prefix (one registrant), not all under
        # the aggregator host, so they do not serialize behind a single slot.
        doi = normalize_doi(paper.get("doi"))
        if doi:
            return f"doi-prefix:{doi.split('/', 1)[0]}"
        title_key = re.sub(r"[^a-z0-9]+", "", (paper.get("title") or "").lower())
        return f"no-doi:{sum(map(ord, title_key)) % 8}"

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
            "jmir": "jmir.org",
            "jove": "app.jove.com",
            "acm": "dl.acm.org",
            "taylor": "tandfonline.com",
            "sage": "journals.sagepub.com",
            "jama": "jamanetwork.com",
            "zenodo": "zenodo.org",
            "osf": "osf.io",
            "acl": "aclanthology.org",
            "peerj": "peerj.com",
            "elife": "elifesciences.org",
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

    # ------------------------------------------------------------------
    # Open-access metadata and official open-copy routes
    # ------------------------------------------------------------------
    def _api_headers(self) -> Dict[str, str]:
        return {"User-Agent": api_user_agent(self.email), "Accept": "application/json"}

    def _ensure_oa_index(self) -> Optional[OAMetadataIndex]:
        if not self.oa_index_enabled:
            return None
        if self.oa_index is None:
            self.oa_index = OAMetadataIndex(
                email=self.email,
                timeout=max(self.request_timeout, 15),
                sleep_func=self.sleep_func,
            )
        return self.oa_index

    def _prefetch_oa_metadata(self, papers: List[Dict]) -> None:
        index = self._ensure_oa_index()
        if index is None or not papers:
            return
        try:
            index.prefetch(papers)
        except Exception as exc:
            index.errors.append(f"prefetch: {type(exc).__name__}: {exc}")

    def _oa_metadata(self, paper: Dict) -> Dict:
        index = self._ensure_oa_index()
        if index is None:
            return {"locations": []}
        try:
            return index.lookup(paper) or {"locations": []}
        except Exception:
            return {"locations": []}

    def _try_pmc_cloud(self, pmcid: str) -> Optional[str]:
        """PDF from NCBI's PMC Cloud Service bucket (PMC OA subset and author manuscripts)."""
        url = pmc_cloud_pdf_url(self.session, pmcid, timeout=self.request_timeout, headers=self._api_headers())
        if not url:
            self._last_failure_class = "pmc_not_in_oa_cloud"
            self._last_failure_detail = f"{pmcid} has no PDF in the PMC Cloud Service bucket"
        return url

    def _rewrite_oa_url(self, url: str) -> List[str]:
        """Map an indexed OA URL to the copy that can be fetched programmatically."""
        lowered = (url or "").lower()
        if ("ncbi.nlm.nih.gov" in lowered or "europepmc.org" in lowered) and re.search(r"pmc\d+", lowered):
            # PMC and Europe PMC web PDFs sit behind browser challenges; the same
            # article is served from the PMC Cloud Service bucket.
            pmcid = normalize_pmcid(re.search(r"(pmc\d+)", lowered).group(1))
            cloud = self._try_pmc_cloud(pmcid)
            if cloud:
                return [cloud]
            # Not in the bucket (e.g. PMC "free" articles outside the OA subset): the
            # Europe PMC render is the only remaining copy; it is challenged only at times.
            return [f"https://europepmc.org/articles/{pmcid}?pdf=render"]
        if _is_likely_non_article_pdf_url(url):
            return []
        arxiv_id = arxiv_id_from_text(url) if "arxiv.org" in lowered else ""
        if arxiv_id:
            return [f"https://arxiv.org/pdf/{arxiv_id}"]
        figshare_id = figshare_article_id_from_url(url)
        if figshare_id:
            # figshare's web UI is WAF-gated; its API names the files.
            return figshare_pdf_urls(self.session, figshare_id, self._api_headers(), self.request_timeout)
        if re.search(r"/bitstreams/[0-9a-f-]{36}/download", lowered):
            # DSpace 7 UI download route -> documented REST content endpoint.
            return dspace7_pdf_urls(self.session, url, self._api_headers(), self.request_timeout) + [url]
        ojs = ojs_download_url(url)
        return [ojs, url] if ojs else [url]

    def _oa_location_candidates(self, meta: Dict, doi: Optional[str], pmcid: str = "") -> Iterable[str]:
        """Direct PDF URLs that OpenAlex and Semantic Scholar list for the paper.

        Locations flagged open come first; file-like locations OpenAlex does not flag
        as open (its OA flags lag for repositories) are tried last.
        """
        own_doi = normalize_doi(doi)
        locations = sorted(meta.get("locations") or [], key=lambda loc: bool(loc.get("low_priority")))
        for location in locations:
            url = location.get("pdf_url") or ""
            if not url or is_metadata_url(url):
                continue
            if own_doi and normalize_doi(url) == own_doi:
                continue
            if _host_matches(url, CHALLENGE_ONLY_HOSTS):
                continue
            for candidate in self._rewrite_oa_url(url):
                if location.get("host_type") == "repository" or location.get("low_priority"):
                    self._repository_urls.add(candidate)
                yield candidate

    def _oa_landing_page_candidates(self, meta: Dict, doi: Optional[str], max_fetches: int = 3) -> Iterable[str]:
        """Resolve repository landing pages (OpenAlex/Unpaywall) into the PDFs they declare.

        Platform APIs come before HTML: DOAJ, figshare, NVA (Norway) and DSpace 7 REST;
        other landings are fetched for citation_pdf_url / DiVA / HAL links.
        """
        own_doi = normalize_doi(doi)
        located = [
            (2 if loc.get("low_priority") else 0 if (loc.get("host_type") or "") == "repository" else 1, loc.get("landing_url"))
            for loc in meta.get("locations") or []
            if loc.get("landing_url")
        ]
        located += [(0 if host_type == "repository" else 1, url) for host_type, url in getattr(self, "_unpaywall_landings", [])]
        queue = _dedupe_keep_order([url for _, url in sorted(located, key=lambda item: item[0])])
        fetches = 0
        seen = set()
        while queue:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            if is_metadata_url(url) or _host_matches(url, CHALLENGE_ONLY_HOSTS):
                continue
            if own_doi and normalize_doi(url) == own_doi:
                continue
            lowered = url.lower()
            arxiv_id = arxiv_id_from_text(url) if "arxiv.org" in lowered else ""
            if arxiv_id:
                yield f"https://arxiv.org/pdf/{arxiv_id}"
                continue
            zenodo = re.search(r"zenodo\.org/(?:records?|api/records)/(\d+)", url)
            if zenodo:
                yield from zenodo_pdf_urls(self.session, zenodo.group(1), self._api_headers(), self.request_timeout)
                continue
            osf_id = osf_id_from("", url)
            if osf_id:
                yield from osf_pdf_urls(self.session, osf_id, self._api_headers(), self.request_timeout)
                continue
            figshare_id = figshare_article_id_from_url(url)
            if figshare_id:
                yield from figshare_pdf_urls(self.session, figshare_id, self._api_headers(), self.request_timeout)
                continue
            if "doaj.org/" in lowered:
                # DOAJ records are metadata; the DOAJ API names the journal's full text.
                for link, content_type in doaj_fulltext_links(self.session, url, self._api_headers(), self.request_timeout):
                    if content_type == "PDF" or _looks_like_pdf_url(link):
                        yield link
                    elif not is_metadata_url(link):
                        queue.append(link)
                continue
            nva_id = nva_publication_id(self.session, url, self._api_headers(), self.request_timeout) if (
                "nva.sikt.no" in lowered or "urn.nb.no" in lowered or re.search(r"hdl\.handle\.net/(11250|10852)/", lowered)
                or "brage.unit.no" in lowered or "duo.uio.no" in lowered
            ) else ""
            if nva_id:
                for presigned in nva_pdf_urls(self.session, nva_id, self._api_headers(), self.request_timeout):
                    self._repository_urls.add(presigned)
                    yield presigned
                continue
            if handle_from_url(url) or re.search(r"/(items|entities/[a-z]+)/[0-9a-f-]{36}", lowered):
                rest_urls = dspace7_pdf_urls(self.session, url, self._api_headers(), self.request_timeout)
                if rest_urls:
                    for rest_url in rest_urls:
                        self._repository_urls.add(rest_url)
                        yield rest_url
                    continue
            if fetches >= max_fetches:
                continue
            fetches += 1
            fetched = self._fetch_landing_html(url, prefer_honest=True)
            if not fetched:
                continue
            final_url, html_text, is_pdf = fetched
            if is_pdf:
                yield final_url
                continue
            declared_title = _declared_citation_title(html_text[:200000])
            if declared_title:
                self._alt_titles.append(declared_title)
            declared = repository_pdf_urls(final_url, html_text[:400000])
            discovered = [
                candidate
                for candidate in extract_static_pdf_urls(html_text[:400000], final_url)
                if not _is_likely_non_article_pdf_url(candidate)
            ][:3]
            for candidate in _dedupe_keep_order(declared + discovered):
                self._repository_urls.add(candidate)
                ojs = ojs_download_url(candidate)
                if ojs:
                    yield ojs
                yield candidate

    def _fetch_landing_html(self, url: str, prefer_honest: bool = False) -> Optional[Tuple[str, str, bool]]:
        """GET a repository landing page -> (final_url, html, is_pdf).

        Repositories get the identifying User-Agent first; a bot check, a bare 403 or a
        connection reset is retried once with the other User-Agent.
        """
        honest = self._api_headers()
        attempts = [honest, None] if (prefer_honest or _host_matches(url, HONEST_UA_HOSTS)) else [None, honest]
        for headers in attempts:
            self._rate_limit()
            try:
                kwargs = {"timeout": self.request_timeout, "allow_redirects": True}
                if headers:
                    kwargs["headers"] = headers
                response = self.session.get(url, **kwargs)
            except Exception:
                continue
            content = response.content or b""
            content_type = response.headers.get("content-type", "").lower()
            if response.status_code == 200 and (content[:5] == b"%PDF-" or "application/pdf" in content_type):
                return response.url, "", True
            if response.status_code == 200 and any(marker in content[:8000].lower() for marker in ANUBIS_MARKERS):
                continue
            if response.status_code == 200 and ("html" in content_type or b"<html" in content[:1000].lower()):
                return response.url, response.text, False
            if response.status_code in (401, 403, 405):
                continue
            return None
        return None

    def _should_retry_with_honest_ua(self, url: str, response) -> bool:
        repository = url in getattr(self, "_repository_urls", set())
        if response is None:
            return repository
        headers = {str(k).lower(): str(v).lower() for k, v in (getattr(response, "headers", None) or {}).items()}
        if headers.get("cf-mitigated") == "challenge":
            return False
        head = (response.content or b"")[:8000].lower()
        if any(marker in head for marker in ANUBIS_MARKERS):
            return True
        if b"<title>client challenge</title>" in head or b"/_fs-ch-" in head:
            return True
        if headers.get("x-amzn-waf-action") == "challenge" or b"gokuprops" in head:
            return True
        return repository and response.status_code in (401, 403, 405)

    def _try_ovid_open_access(self, doi: Optional[str]) -> Optional[str]:
        """Wolters Kluwer (Ovid) open-access PDF: /fulltext/ page -> /pdf/ on the same session."""
        if not doi:
            return None
        self._rate_limit()
        try:
            response = self.session.get(f"https://doi.org/{doi}", timeout=self.request_timeout, allow_redirects=True)
        except Exception:
            return None
        final_url = response.url or ""
        if "ovid.com" not in self._domain_for_url(final_url) or "/fulltext/" not in final_url:
            return None
        body = response.content or b""
        if b"FreeOpenAccessContent" not in body and b"openAccessLicense" not in body:
            self._last_failure_class = "publisher_paywalled"
            self._last_failure_detail = "Ovid article is not open access"
            return None
        pdf_url = final_url.replace("/fulltext/", "/pdf/", 1)
        self._url_headers[pdf_url] = {"Referer": final_url}
        return pdf_url

    def _article_is_openly_licensed(self, doi: Optional[str]) -> bool:
        meta = getattr(self, "_paper_meta", {}) or {}
        if any(str(lic).lower().startswith("cc") for lic in meta.get("licenses") or []):
            return True
        licensed = crossref_has_open_licence(self.session, doi or "", self._api_headers(), self.request_timeout)
        return bool(licensed)

    def _elsevier_api_candidate(self, doi: Optional[str]) -> Optional[str]:
        """Elsevier Article Retrieval API (user's key).

        Open-access, open-archive and complimentary articles come back in full; for
        articles the key is not entitled to, the API returns a one-page preview, which
        the two-page minimum rejects.
        """
        if not self.elsevier_api_key or not normalize_doi(doi).startswith("10.1016/"):
            return None
        url = f"https://api.elsevier.com/content/article/doi/{normalize_doi(doi)}"
        self._url_params[url] = {"httpAccept": "application/pdf"}
        self._url_headers[url] = {"X-ELS-APIKey": self.elsevier_api_key, "Accept": "application/pdf"}
        self._url_min_pages[url] = 2
        return url

    def _wiley_candidates(self, doi: Optional[str]) -> List[str]:
        """Wiley TDM API (user's token) first, then the public pdfdirect URL."""
        candidates = []
        if self.wiley_tdm_token and doi:
            now = time.monotonic()
            with _KEYED_ROUTE_LOCK:
                calls = [t for t in _KEYED_ROUTE_STATE["wiley_calls"] if now - t < 600]
                allowed = len(calls) < 60  # Wiley TDM limit: 60 requests per 10 minutes
                if allowed:
                    calls.append(now)
                _KEYED_ROUTE_STATE["wiley_calls"] = calls
            if allowed:
                url = f"https://api.wiley.com/onlinelibrary/tdm/v1/articles/{quote(normalize_doi(doi), safe='')}"
                self._url_headers[url] = {"Wiley-TDM-Client-Token": self.wiley_tdm_token}
                candidates.append(url)
        if doi:
            candidates.append(f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}")
        return candidates

    def _openalex_content_candidate(self, meta: Dict) -> Optional[str]:
        """OpenAlex's cached copy of an OA PDF (metered: user's key, capped per run)."""
        url = meta.get("content_pdf")
        if not url or not self.openalex_api_key:
            return None
        with _KEYED_ROUTE_LOCK:
            if _KEYED_ROUTE_STATE["openalex_content_disabled"]:
                return None
            if _KEYED_ROUTE_STATE["openalex_content_used"] >= self.openalex_content_max:
                self._last_failure_class = "metered_budget_exhausted"
                self._last_failure_detail = f"OpenAlex content cap {self.openalex_content_max} reached"
                return None
            _KEYED_ROUTE_STATE["openalex_content_used"] += 1
        # The key travels as a request parameter, never inside the recorded URL.
        self._url_params[url] = {"api_key": self.openalex_api_key}
        return url

    def _arxiv_version_candidates(self, arxiv_ref, title: str = "") -> List[str]:
        """arXiv PDF for an id, falling back to earlier versions (withdrawn latest versions 404)."""
        raw = str(arxiv_ref or "")
        arxiv_id = arxiv_id_from_text(raw) or re.sub(r"^(arxiv:|https?://arxiv\.org/abs/)", "", raw.strip(), flags=re.IGNORECASE)
        if not re.match(r"^([a-z\-]+/\d{7}|\d{4}\.\d{4,5})(v\d+)?$", arxiv_id, re.IGNORECASE):
            return []
        match = re.match(r"^(.*?)(?:v(\d+))?$", arxiv_id)
        base, version = match.group(1), match.group(2)
        candidates = [f"https://arxiv.org/pdf/{arxiv_id}"]
        if version and int(version) > 1:
            candidates += [f"https://arxiv.org/pdf/{base}v{v}" for v in range(int(version) - 1, 0, -1)]
        elif not version:
            candidates.append(f"https://arxiv.org/pdf/{base}v1")
        return candidates

    def _try_arxiv(self, arxiv_id: Optional[str], title: str) -> Optional[str]:
        """arXiv by id, or a strict title search under arXiv's 1-request-per-3-seconds rule.

        The title search runs only when the open-access index has no record of the
        paper; indexed papers already carry their arXiv id when one exists.
        """
        if arxiv_id:
            return super()._try_arxiv(arxiv_id, title)
        meta = getattr(self, "_paper_meta", {}) or {}
        if self.oa_index_enabled and (meta.get("openalex_id") or meta.get("s2_found")):
            return None
        tokens = _title_match_tokens(title)
        if len(tokens) < 4:
            return None
        clean_title = re.sub(r"[\"():]", " ", title or "")
        clean_title = re.sub(r"\s+", " ", clean_title).strip()[:200]
        with _ARXIV_API_LOCK:
            wait = 3.0 - (time.monotonic() - _ARXIV_API_LAST_CALL[0])
            if wait > 0:
                time.sleep(wait)
            _ARXIV_API_LAST_CALL[0] = time.monotonic()
            try:
                response = self.session.get(
                    "https://export.arxiv.org/api/query",
                    params={"search_query": f'ti:"{clean_title}"', "max_results": 5},
                    headers=self._api_headers(),
                    timeout=self.request_timeout,
                )
            except Exception as exc:
                self._last_failure_class = "metadata_api_error"
                self._last_failure_detail = f"arXiv API: {type(exc).__name__}"
                return None
        if response.status_code != 200:
            self._last_failure_class = "metadata_api_429" if response.status_code == 429 else "metadata_api_error"
            self._last_failure_detail = f"arXiv API HTTP {response.status_code}"
            return None
        expected = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
        for entry in re.findall(r"<entry>(.*?)</entry>", response.text, flags=re.DOTALL):
            id_match = re.search(r"<id>https?://arxiv\.org/abs/([^<]+)</id>", entry)
            title_match = re.search(r"<title>(.*?)</title>", entry, flags=re.DOTALL)
            if not id_match or not title_match:
                continue
            found = re.sub(r"[^a-z0-9]+", " ", stdlib_html.unescape(title_match.group(1)).lower()).strip()
            if SequenceMatcher(None, expected, found).ratio() < 0.9:
                continue
            # Generic titles collide across works: require a shared author surname then.
            record_surnames = surnames(getattr(self, "_paper_authors", []) or [])
            entry_surnames = surnames(re.findall(r"<name>(.*?)</name>", entry, flags=re.DOTALL))
            distinctive = sum(token not in self.generic_title_tokens for token in tokens) >= 3
            if (record_surnames and entry_surnames & record_surnames) or (distinctive and not record_surnames):
                return f"https://arxiv.org/pdf/{id_match.group(1)}"
        return None

    def _published_version_candidates(self, doi: Optional[str], title: str) -> Iterable[str]:
        """For a preprint DOI, the open copies of its version of record (Crossref is-preprint-of)."""
        doi_lower = normalize_doi(doi)
        preprint_prefixes = ("10.36227/", "10.20944/", "10.2139/", "10.31234/", "10.31219/", "10.31235/",
                             "10.21203/", "10.22541/", "10.26434/", "10.1101/", "10.64898/", "10.48550/")
        if not doi_lower.startswith(preprint_prefixes):
            return
        for published in crossref_published_dois(self.session, doi_lower, self._api_headers(), self.request_timeout):
            meta = self._oa_metadata({"doi": published})
            if meta.get("pmcid"):
                cloud = self._try_pmc_cloud(meta["pmcid"])
                if cloud:
                    yield cloud
            for candidate in self._oa_location_candidates(meta, published):
                yield candidate

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
            ("10.2196/", "jmir"),
            ("10.5281/zenodo.", "zenodo"),
            ("10.18653/", "acl"),
            ("10.1007/", "springer"),
            ("10.1080/", "taylor"),
            ("10.1177/", "sage"),
            ("10.1001/", "jama"),
            ("10.7717/", "peerj"),
            ("10.7554/", "elife"),
            ("10.1126/", "science"),
            ("10.48550/", "arxiv"),
        ]
        for prefix, publisher in doi_prefix_map:
            if doi_lower.startswith(prefix):
                return publisher
        if "/osf.io/" in doi_lower or doi_lower.startswith(("10.31234/", "10.31219/", "10.31235/", "10.35542/")):
            return "osf"
        if figshare_article_id(doi_lower):
            return "figshare"
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
            return lambda: self._wiley_candidates(doi)
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
        if publisher == "jmir" and doi:
            # jmir.org answers programmatic clients with an AWS WAF challenge; JMIR's
            # public asset bucket holds the accepted manuscript.
            return lambda: jmir_asset_pdf_urls(doi)
        if publisher == "zenodo" and doi:
            record = re.search(r"zenodo\.(\d+)", doi.lower())
            return (lambda: zenodo_pdf_urls(self.session, record.group(1), self._api_headers(), self.request_timeout)) if record else None
        if publisher == "osf" and doi:
            osf_id = osf_id_from(doi, url or "")
            return (lambda: osf_pdf_urls(self.session, osf_id, self._api_headers(), self.request_timeout)) if osf_id else None
        if publisher == "figshare" and doi:
            article_id = figshare_article_id(doi)
            return lambda: figshare_pdf_urls(self.session, article_id, self._api_headers(), self.request_timeout)
        if publisher == "acl" and doi:
            anthology_id = re.sub(r"^10\.18653/v1/", "", doi.strip(), flags=re.IGNORECASE)
            return lambda: f"https://aclanthology.org/{anthology_id}.pdf"
        if publisher == "arxiv" and doi:
            return lambda: self._arxiv_version_candidates(arxiv_id_from_text(doi))
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
        """IEEE: Computer Society CSDL first, then Xplore stampPDF only for openly licensed articles.

        Crossref supplies the arnumber and licence, so subscription articles are not
        requested from Xplore at all and are reported as paywalled.
        """
        csdl_pdf_url = self._try_ieee_computer_society_pdf(doi)
        if csdl_pdf_url or self._last_failure_class == "publisher_paywalled":
            return csdl_pdf_url

        arnumber = None
        if url and "ieeexplore.ieee.org" in url:
            match = re.search(r"/document/(\d+)", url)
            if match:
                arnumber = match.group(1)
        cache = self.__dict__.setdefault("_ieee_info_cache", {})
        if doi and doi not in cache:
            cache[doi] = ieee_oa_pdf_urls(self.session, doi, self._api_headers(), self.request_timeout)
        info = cache.get(doi) or {}
        arnumber = arnumber or info.get("arnumber") or self._ieee_arnumber_from_doi_redirect(doi)
        if not arnumber:
            self._last_failure_class = "metadata_api_error"
            self._last_failure_detail = "IEEE arnumber not found (Crossref, doi.org)"
            return None
        openly_available = info.get("cc_license") or (getattr(self, "_paper_meta", {}) or {}).get("is_oa")
        if info.get("crossref_found") and not openly_available:
            self._last_failure_class = "publisher_paywalled"
            self._last_failure_detail = "IEEE article has no open licence (Crossref/OpenAlex)"
            return None
        return f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={arnumber}"

    def _ieee_arnumber_from_doi_redirect(self, doi: Optional[str]) -> Optional[str]:
        if not doi:
            return None
        self._rate_limit()
        try:
            response = self.session.get(f"https://doi.org/{doi}", timeout=self.request_timeout, allow_redirects=True)
        except Exception:
            return None
        for text in (response.url or "", (response.text or "")[:20000]):
            if "ieeexplore.ieee.org" in text or "/document/" in text:
                match = re.search(r"/document/(\d+)", text)
                if match:
                    return match.group(1)
        return None

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
        api_url = self._elsevier_api_candidate(doi)
        if api_url:
            return api_url
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

    def _try_mdpi(self, doi: Optional[str], url: Optional[str]) -> List[str]:
        # The asset URL is derived from the DOI; if it 404s, fall through to the DOI-based route.
        return [candidate for candidate in (self._mdpi_res_pdf_url(doi), super()._try_mdpi(doi, url)) if candidate]

    def _mdpi_res_pdf_url(self, doi: Optional[str]) -> Optional[str]:
        doi_lower = (doi or "").lower().strip()
        proceedings = re.match(r"^10\.3390/([a-z]+)(20\d\d)(\d{3})(\d{3,})$", doi_lower)
        if proceedings and proceedings.group(1) in MDPI_PROCEEDINGS_CODES:
            slug, volume, article = proceedings.group(1), int(proceedings.group(3)), int(proceedings.group(4))
            stem = f"{slug}-{volume:02d}-{article:05d}"
            return f"https://mdpi-res.com/d_attachment/{slug}/{stem}/article_deploy/{stem}.pdf"
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
        journal_slug = MDPI_JOURNAL_SLUGS.get(journal_code, journal_code)
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

    def _try_unpaywall(self, doi: Optional[str]) -> List[str]:
        """Every Unpaywall OA location, with PMC/Europe PMC copies mapped to the PMC Cloud bucket."""
        candidates: List[str] = []
        for url in self._try_unpaywall_pdf_candidates(doi):
            if _host_matches(url, CHALLENGE_ONLY_HOSTS):
                continue
            candidates.extend(self._rewrite_oa_url(url))
        return _dedupe_keep_order(candidates) or None

    def _try_unpaywall_pdf_candidates(self, doi: Optional[str]) -> List[str]:
        """Return only Unpaywall locations that are direct PDF-looking URLs."""
        self._unpaywall_landings = []
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
            landing = location.get("url_for_landing_page") or location.get("url")
            if landing and not _looks_like_pdf_url(landing):
                self._unpaywall_landings.append((location.get("host_type") or "", landing))
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
        if failure_class not in PDF_ENDPOINT_COOLDOWN_FAILURE_CLASSES | DOMAIN_COOLDOWN_FAILURE_CLASSES:
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
        cooldown_classes = sorted(DOMAIN_COOLDOWN_FAILURE_CLASSES - {"metadata_api_429"})
        if self._is_pdf_endpoint_cooldown_candidate(url):
            cooldown_classes.extend(sorted(PDF_ENDPOINT_COOLDOWN_FAILURE_CLASSES))
        cooldown_failure = self._domain_cooldown_failure(url, cooldown_classes)
        if cooldown_failure:
            self._last_failure_class = "domain_cooldown_skip"
            self._last_failure_detail = f"{self._domain_for_url(url)} cooled down after {cooldown_failure}"
            getattr(self, "_cooldown_skipped_urls", []).append(url)
            return None

        file_path = self._download_pdf(url, title, method, paper_id)
        if not file_path:
            self._register_domain_failure(url, self._last_failure_class)
            # Transient outcomes (network errors, rate limits) stay eligible for the rescue pass.
            if self._last_failure_class not in (None, "network_error", "rate_limited", "metadata_api_429"):
                getattr(self, "_definitive_failed_urls", set()).add(url)
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

    def _try_pmc(self, pmid: Optional[str], doi: Optional[str]) -> List[str]:
        """PMC copy: the PMC Cloud Service bucket first, the PMC website last.

        NCBI retired the OA web service (oa.fcgi); the cloud bucket is its
        replacement. The website's PDF links sit behind a browser challenge, so
        they are only a fallback for articles outside the bucket.
        """
        pmcid = self._resolve_pmcid(pmid, doi)
        if not pmcid:
            return []
        cloud = self._try_pmc_cloud(pmcid)
        if cloud:
            return [cloud]
        candidates = []
        named_pdf = self._try_pmc_named_article_pdf(pmcid)
        if named_pdf:
            candidates.append(named_pdf)
        candidates.append(f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/")
        return candidates

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

        for candidate in extract_static_pdf_urls(response.text[:200000], response.url)[:6]:
            if _is_likely_non_article_pdf_url(candidate):
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
        # Reverse coverage stops a short title matching a longer, different preprint.
        reverse_coverage = len(hits) / len(candidate_tokens)
        return coverage >= 0.72 and reverse_coverage >= 0.6 and len(hits) >= min(5, len(expected_tokens))

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
            # medRxiv serves 10.64898 DOIs under their own prefix (rewriting to 10.1101 gives 403).
            return f"https://www.medrxiv.org/content/{preprint_doi}.full.pdf"
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
                    publisher_urls = list(self._iter_candidates(publisher_method()))
                except Exception:
                    publisher_urls = []
                candidates.extend(url for url in publisher_urls if not _looks_like_pdf_url(url))

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

        # Metadata records (PubMed, OpenAlex) are not article pages and must not be printed.
        if direct_url and not _looks_like_pdf_url(direct_url) and not self._is_metadata_article_source_url(direct_url):
            candidates.append(direct_url)

        publisher = self._resolve_publisher(doi, paper.get("journal", "")).get("selected_publisher")
        if publisher:
            publisher_method = self._get_publisher_method(publisher, doi, direct_url)
            if publisher_method:
                try:
                    publisher_urls = list(self._iter_candidates(publisher_method()))
                except Exception:
                    publisher_urls = []
                candidates.extend(url for url in publisher_urls if not _looks_like_pdf_url(url))

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

    def _extract_pdf_pages(self, content: bytes, max_pages: int = 4, max_chars: int = 30000) -> List[str]:
        """Text of the first pages, one string per page (NFKC-normalised)."""
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            if getattr(reader, "is_encrypted", False):
                try:
                    reader.decrypt("")
                except Exception:
                    return []
            pages: List[str] = []
            for page in reader.pages[:max_pages]:
                try:
                    pages.append(unicodedata.normalize("NFKC", page.extract_text() or ""))
                except Exception:
                    pages.append("")
                if sum(len(text) for text in pages) >= max_chars:
                    break
            return pages
        except Exception:
            return []

    def _pdf_title_match_result(self, content: bytes, title: str) -> Tuple[bool, Optional[str]]:
        pages = self._extract_pdf_pages(content)
        if getattr(self, "_strict_title_gate", False):
            return self._strict_title_gate_result(pages, title)
        matches, detail = self._title_gate(pages, title)
        if matches:
            return True, None
        # Titles declared by the record's own landing page (citation_title, DSpace dc.title,
        # NVA/DOAJ titles) cover translated or reworded record titles.
        for alternative in getattr(self, "_alt_titles", []) or []:
            # Only a translation or a rewording of the record title counts: a landing reached
            # through a wrong record DOI declares another paper's title (hci-00002).
            if alternative and alternative.strip().lower() != (title or "").strip().lower() and (
                _looks_non_english_title(alternative) or _title_overlap(alternative, title) >= 0.5
            ):
                alt_matches, _ = self._title_gate(pages, alternative)
                if alt_matches:
                    return True, None
        return False, detail

    def _strict_title_gate_result(self, pages: List[str], title: str) -> Tuple[bool, Optional[str]]:
        """Gate for files found as *other versions* of a paper (preprint, repository copy):
        the record's DOI on pages 1-2, or >=80% of its title words there, and never a supplement."""
        head = "\n".join(pages[:2])
        if _front_page_is_supplement(head):
            return False, "supplementary file, not the article"
        paper_doi = re.sub(r"\s+", "", getattr(self, "_current_paper_doi", "") or "")
        if len(paper_doi) >= 10 and paper_doi in re.sub(r"\s+", "", head.lower()):
            return True, None
        title_tokens = _title_match_tokens(title)
        if len(title_tokens) < 3:
            return False, "title too short to verify another version"
        head_tokens = set(_title_match_tokens(head[:6000]))
        coverage = sum(token in head_tokens for token in title_tokens) / len(title_tokens)
        if coverage >= 0.8:
            return True, None
        return False, f"other version: title coverage {coverage:.2f} on pages 1-2"

    def _title_gate(self, pages: List[str], title: str) -> Tuple[bool, Optional[str]]:
        title_tokens = _title_match_tokens(title)
        if len(title_tokens) < 2:
            return True, None

        pdf_text = "\n".join(pages)[:30000]
        if not pdf_text.strip() or not _title_match_tokens(pdf_text):
            # Nothing to verify against (scan or image-only PDF): accept only when the
            # file was fetched by the paper's own identifier.
            method = getattr(self, "_active_method", "") or ""
            if method in IDENTIFIER_TRUSTED_METHODS or method.startswith("publisher_"):
                return True, None
            return False, "no extractable text to verify the title"

        pdf_tokens = set(_title_match_tokens(pdf_text))

        hits = [token for token in title_tokens if token in pdf_tokens]
        coverage = len(hits) / len(title_tokens)
        required_hits = min(6, len(title_tokens))
        front_text = pdf_text[:1200]
        compact_title = re.sub(r"[^a-z0-9]+", "", (title or "").lower())
        if _front_page_is_supplement(pdf_text) and not _front_page_is_supplement(title):
            return False, "supplementary file, not the article"
        # Page 1 may be a repository cover sheet or a sidebar layout, so the title is
        # looked for at the top of page 1 and of page 2.
        # 1800 characters: publisher boilerplate (Procedia, journal mastheads) can precede the title.
        fronts = [page[:1800] for page in pages[:2] if page.strip()] or [front_text]
        if len(compact_title) >= 24 and any(
            compact_title in re.sub(r"[^a-z0-9]+", "", front.lower()) for front in fronts
        ):
            return True, None

        head_text = "\n".join(pages[:2])[:6000] if pages else pdf_text[:3000]
        paper_doi = re.sub(r"\s+", "", getattr(self, "_current_paper_doi", "") or "")
        doi_on_head = len(paper_doi) >= 10 and paper_doi in re.sub(r"\s+", "", head_text.lower())
        if doi_on_head:
            # The paper's own DOI printed on pages 1-2 identifies it when the title was reworded
            # at publication; the title must still be recognisable (a wrong DOI in the
            # record would otherwise pull in a different paper) ...
            head_tokens = set(_title_match_tokens(head_text))
            head_coverage = sum(token in head_tokens for token in title_tokens) / len(title_tokens)
            if len(hits) >= 2 and head_coverage >= 0.35:
                return True, None
            # ... unless the paper is not in English, where an English record title cannot match.
            if not _text_is_english(head_text):
                return True, None

        front_letters = [ch for ch in front_text if ch.isalpha()]
        non_latin = front_letters and sum(ord(ch) > 0x24F for ch in front_letters) / len(front_letters) > 0.3
        if len(title_tokens) >= 3 and not non_latin:
            # The title's distinctive words must appear at the top of page 1 (or of page 2,
            # behind a cover sheet, where a stricter bar applies); generic domain words
            # such as "large language model" count only a quarter.
            front_ok, best = False, (0.0, 0, 0)
            for position, front in enumerate(fronts):
                front_tokens = set(_title_match_tokens(front))
                if not front_tokens:
                    continue
                wcov, distinct_hits, distinct_total = _weighted_coverage(title_tokens, front_tokens, self.generic_title_tokens)
                best = max(best, (wcov, distinct_hits, distinct_total))
                needed_hits = min(5, distinct_total) if distinct_total else 0
                bar = 0.55 if position == 0 else 0.7
                weighted_ok = wcov >= bar or (distinct_total >= 2 and distinct_hits >= needed_hits)
                # The plain word rule must hold too, so the gate is never looser than before
                # topic words were down-weighted.
                plain_hits = sum(token in front_tokens for token in title_tokens)
                plain_ok = plain_hits / len(title_tokens) >= bar or plain_hits >= min(5, len(title_tokens))
                if weighted_ok and plain_ok:
                    front_ok = True
                    break
            # Exact repository records fetched by ID (OSF, Zenodo, PMC bucket, arXiv id, ...)
            # are the paper even when the deposited version was retitled; for them only the
            # whole-text coverage below applies.
            if not front_ok and getattr(self, "_active_method", "") not in RECORD_ID_METHODS:
                return False, (
                    f"front matter title token coverage {best[0]:.2f} weighted "
                    f"({best[1]}/{best[2]} distinctive words)"
                )
        wcov_all, distinct_all, distinct_total_all = _weighted_coverage(title_tokens, pdf_tokens, self.generic_title_tokens)
        if wcov_all >= 0.5 or (distinct_total_all and distinct_all >= min(5, distinct_total_all)):
            return True, None
        if getattr(self, "_active_method", "") in RECORD_ID_METHODS and (coverage >= 0.45 or len(hits) >= required_hits):
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
        self._active_method = method
        min_pages = getattr(self, "_url_min_pages", {}).get(getattr(self, "_active_download_url", ""), 0)
        if min_pages and len(self._extract_pdf_pages(content, max_pages=min_pages, max_chars=10**9)) < min_pages:
            # e.g. Elsevier's API returns a first-page preview for non-entitled articles.
            self._last_failure_class = "publisher_paywalled"
            self._last_failure_detail = f"only a {min_pages - 1}-page preview was returned"
            return None
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
        # A printed HTML article always carries text; a textless print is a blank or
        # canvas-only page, which the title gate would otherwise wave through.
        if not self._extract_pdf_text_sample(content, max_pages=2, max_chars=2000).strip():
            self._last_failure_class = "article_print_incomplete"
            self._last_failure_detail = "printed page has no extractable text"
            return False
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
        self._active_download_url = url
        self._active_method = method
        honest_headers = {**self._api_headers(), "Accept": "application/pdf,*/*"}

        request_kwargs = {
            "timeout": self.download_timeout,
            "allow_redirects": True,
        }
        headers = self._pdf_request_headers(url)
        if headers:
            request_kwargs["headers"] = headers
        if self._url_params.get(url):
            request_kwargs["params"] = self._url_params[url]
        sent_honest = bool(headers) and headers.get("User-Agent") == honest_headers["User-Agent"]
        try:
            response = self.session.get(url, **request_kwargs)
        except Exception as exc:
            response = None
            error = exc
        if not sent_honest and self._should_retry_with_honest_ua(url, response):
            # Bot checks that exempt identified clients (Anubis, Fastly client challenge,
            # AWS WAF), and repositories that refuse spoofed browser UAs: ask once as the
            # tool we are. Cloudflare managed challenges are never retried.
            try:
                retry = self.session.get(url, **{**request_kwargs, "headers": {**(headers or {}), **honest_headers}})
            except Exception as exc:
                retry, error = None, exc
            if retry is not None:
                if retry.status_code == 200 and (retry.content or b"")[:5] == b"%PDF-":
                    with _LEARNED_HONEST_LOCK:
                        _LEARNED_HONEST_HOSTS.add(self._domain_for_url(url))
                response = retry
        if response is None:
            self._last_failure_class = "network_error"
            self._last_failure_detail = f"{type(error).__name__}"
            getattr(self, "_network_error_urls", []).append(url)
            curl_path = self._download_pdf_with_curl_cffi(url, title, method, paper_id)
            if curl_path:
                return curl_path
            return None
        if "content.openalex.org" in url and response.status_code in (401, 402, 403, 429):
            with _KEYED_ROUTE_LOCK:
                _KEYED_ROUTE_STATE["openalex_content_disabled"] = True

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

            page_candidates = [
                candidate
                for candidate in extract_static_pdf_urls(response.text[:200000], response.url)
                if not _is_likely_non_article_pdf_url(candidate)
            ]
            for candidate in page_candidates[:4]:
                try:
                    candidate_headers = self._pdf_request_headers(candidate)
                    retry = self.session.get(
                        candidate,
                        timeout=self.download_timeout,
                        allow_redirects=True,
                        **({"headers": candidate_headers} if candidate_headers else {}),
                    )
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

        if failure_class in ("pdf_endpoint_cloudflare", "access_denied"):
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
            if b"preparing to download" in content_lower or b"pow_challenge" in content_lower:
                return "pmc_pow_challenge"

        headers = {str(k).lower(): str(v).lower() for k, v in (getattr(response, "headers", None) or {}).items()}
        # Specific challenge signatures only: the bare word "cloudflare" also appears in
        # ordinary pages that load cdnjs.cloudflare.com, so it is not a marker.
        cloudflare_markers = (
            b"just a moment",
            b"cf-chl",
            b"_cf_chl_opt",
            b"challenge-platform",
            b"checking your browser",
        )
        is_cloudflare_challenge = headers.get("cf-mitigated") == "challenge" or any(
            marker in content_lower for marker in cloudflare_markers
        )
        if is_pdf_endpoint and "sciencedirect.com" in url_lower and (
            b"tdm-reservation" in content_lower or b"tdmrep-policy" in content_lower
        ):
            return "pdf_endpoint_tdm_blocked"
        if (
            b"awswafcookiedomainlist" in content_lower
            or b"gokuprops" in content_lower
            or headers.get("x-amzn-waf-action") == "challenge"
        ):
            return "pdf_endpoint_waf" if is_pdf_endpoint else "antibot_challenge"
        if b"<title>client challenge</title>" in content_lower or b"/_fs-ch-" in content_lower:
            return "antibot_challenge"
        if any(marker in content_lower for marker in ANUBIS_MARKERS):
            return "repository_bot_check"
        if is_cloudflare_challenge:
            return "pdf_endpoint_cloudflare" if is_pdf_endpoint else "antibot_challenge"
        if response.status_code == 429:
            return "rate_limited"
        if response.status_code in (401, 403):
            # A bare 401/403 is a paywall or access rule, not a bot challenge.
            return "access_denied" if is_pdf_endpoint else "access_blocked"

        content_type = response.headers.get("content-type", "").lower()
        if response.status_code == 200 and ("html" in content_type or b"<html" in response.content[:1000].lower()):
            return "non_pdf_html"
        if response.status_code >= 400:
            return f"http_{response.status_code}"
        return None

    def _pdf_request_headers(self, url: str) -> Optional[Dict[str, str]]:
        url_headers = getattr(self, "_url_headers", {}).get(url)
        honest = (
            _host_matches(url, HONEST_UA_HOSTS)
            or self._domain_for_url(url) in _LEARNED_HONEST_HOSTS
            or url in getattr(self, "_repository_urls", set())
            or "/server/api/core/bitstreams/" in (url or "")
        )
        if honest:
            return {**self._api_headers(), "Accept": "application/pdf,*/*", **(url_headers or {})}
        if url_headers:
            session_headers = getattr(self.session, "headers", {}) or {}
            return {"User-Agent": session_headers.get("User-Agent", "Mozilla/5.0"), "Accept": "application/pdf,*/*", **url_headers}
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
            and (
                any(marker in url_lower for marker in CURL_CFFI_PDF_URL_MARKERS)
                or "/doi/pdf" in url_lower
                or "/doi/epdf" in url_lower
            )
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
            retry_downloader.generic_title_tokens = self.generic_title_tokens
            retry_downloader._current_paper_doi = getattr(self, "_current_paper_doi", "")
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
