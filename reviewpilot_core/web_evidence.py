"""Deterministic acceptance of web evidence for papers without full text.

A web value is kept only when code can confirm all of the following; the model's
own judgement of a source is never trusted:

- the source URL is of an allowed type (publisher landing page, trial registry,
  PMC / Europe PMC record, preprint server, or the paper's own supplementary material);
- the fetched page names this paper (its DOI or title) and is not another work that
  cites or reviews it;
- the quote is one continuous excerpt that occurs in the fetched page text.
"""
from __future__ import annotations

import io
import re
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from .evidence_support import _canonical, locate_excerpt, safe_url

PUBLISHER = "publisher_landing_page"
TRIAL_REGISTRY = "trial_registry"
PMC = "pmc_europe_pmc"
PREPRINT = "preprint"
SUPPLEMENTARY = "supplementary_material"
ALLOWED_SOURCE_TYPES = (PUBLISHER, TRIAL_REGISTRY, PMC, PREPRINT, SUPPLEMENTARY)

MIN_QUOTE_CHARS = 20  # letters and digits; shorter excerpts match too many unrelated pages
MAX_PAGE_BYTES = 8 * 1024 * 1024

TRIAL_REGISTRY_HOSTS = (
    "clinicaltrials.gov", "isrctn.com", "trialsearch.who.int", "euclinicaltrials.eu", "clinicaltrialsregister.eu",
    "anzctr.org.au", "chictr.org.cn", "ctri.nic.in", "drks.de", "jrct.niph.go.jp", "jrct.mhlw.go.jp", "umin.ac.jp",
    "pactr.samrc.ac.za", "irct.behdasht.gov.ir", "irct.ir", "ensaiosclinicos.gov.br", "onderzoekmetmensen.nl",
    "trialregister.nl", "slctr.lk", "cris.nih.go.kr", "thaiclinicaltrials.org", "rpcec.sld.cu", "lbctr.moph.gov.lb",
)
PMC_HOSTS = ("pmc.ncbi.nlm.nih.gov", "europepmc.org", "europepmc.ebi.ac.uk")
PREPRINT_HOSTS = (
    "arxiv.org", "biorxiv.org", "medrxiv.org", "chemrxiv.org", "ssrn.com", "researchsquare.com", "preprints.org",
    "psyarxiv.com", "techrxiv.org", "authorea.com", "eartharxiv.org", "socarxiv.org", "engrxiv.org",
)
PUBLISHER_HOSTS = (
    "nature.com", "springer.com", "springeropen.com", "biomedcentral.com", "sciencedirect.com", "cell.com",
    "thelancet.com", "wiley.com", "tandfonline.com", "sagepub.com", "acm.org", "ieee.org", "jamanetwork.com",
    "oup.com", "bmj.com", "nejm.org", "plos.org", "frontiersin.org", "mdpi.com", "cambridge.org", "science.org",
    "pnas.org", "elifesciences.org", "karger.com", "thieme-connect.com", "thieme-connect.de", "lww.com",
    "ahajournals.org", "acpjournals.org", "aacrjournals.org", "asm.org", "jci.org", "rsc.org", "acs.org",
    "iop.org", "aps.org", "aip.org", "emerald.com", "degruyter.com", "hindawi.com", "peerj.com", "jmir.org",
    "f1000research.com", "royalsocietypublishing.org", "annualreviews.org", "aaai.org", "aclanthology.org",
    "mlr.press", "neurips.cc", "jmlr.org", "usenix.org", "spiedigitallibrary.org", "ajtmh.org", "liebertpub.com",
    "healthaffairs.org", "physiology.org", "ascopubs.org", "atsjournals.org", "ersjournals.com",
    "diabetesjournals.org", "rupress.org", "embopress.org", "jneurosci.org", "ametsoc.org", "agu.org",
    "copernicus.org", "informs.org", "apa.org", "jstor.org", "worldscientific.com", "ios-press.com",
    "iospress.com", "scielo.org", "scielo.br", "eurosurveillance.org", "cdc.gov", "who.int",
)
SHADOW_LIBRARY_MARKERS = ("sci-hub", "scihub", "libgen", "library.lol", "z-lib", "zlibrary", "annas-archive",
                          "1lib.", "booksc.", "libstc.")
SOCIAL_MEDIA_HOSTS = ("twitter.com", "x.com", "facebook.com", "linkedin.com", "reddit.com", "researchgate.net",
                      "academia.edu", "bsky.app", "mastodon.social", "youtube.com", "instagram.com", "tiktok.com",
                      "weibo.com", "zhihu.com", "threads.net")
NEWS_OR_BLOG_HOSTS = ("medium.com", "substack.com", "wordpress.com", "blogspot.com", "sciencedaily.com",
                      "eurekalert.org", "medicalxpress.com", "phys.org", "statnews.com", "nytimes.com",
                      "theguardian.com", "bbc.co.uk", "bbc.com", "reuters.com", "cnn.com", "forbes.com",
                      "theconversation.com", "healthline.com", "medscape.com", "news-medical.net", "sciencenews.org",
                      "newscientist.com", "scientificamerican.com", "wired.com", "techcrunch.com", "fiercebiotech.com")
SUPPLEMENT_PATH_MARKERS = ("suppl", "supplement", "moesm", "mediaobjects", "/bin/", "attachment", "appendix")


# Publisher article hosts by DOI prefix: where this paper's own landing page lives.
DOI_PREFIX_HOSTS = {
    "10.1016": ("sciencedirect.com", "cell.com", "thelancet.com"), "10.1007": ("link.springer.com",),
    "10.1186": ("biomedcentral.com", "link.springer.com"), "10.1038": ("nature.com",), "10.1109": ("ieeexplore.ieee.org",),
    "10.1145": ("dl.acm.org",), "10.1177": ("journals.sagepub.com",), "10.1002": ("onlinelibrary.wiley.com",),
    "10.1111": ("onlinelibrary.wiley.com",), "10.1080": ("tandfonline.com",), "10.1097": ("journals.lww.com",),
    "10.1093": ("academic.oup.com",), "10.1001": ("jamanetwork.com",), "10.1136": ("bmj.com",), "10.1056": ("nejm.org",),
    "10.1371": ("journals.plos.org",), "10.3389": ("frontiersin.org",), "10.3390": ("mdpi.com",), "10.2196": ("jmir.org",),
    "10.1017": ("cambridge.org",), "10.1126": ("science.org",), "10.1073": ("pnas.org",), "10.7554": ("elifesciences.org",),
    "10.1055": ("thieme-connect.com",), "10.1159": ("karger.com",), "10.1161": ("ahajournals.org",),
    "10.7326": ("acpjournals.org",), "10.1200": ("ascopubs.org",), "10.1021": ("pubs.acs.org",), "10.1039": ("pubs.rsc.org",),
    "10.1089": ("liebertpub.com",), "10.1101": ("biorxiv.org", "medrxiv.org"), "10.48550": ("arxiv.org",),
    "10.2139": ("ssrn.com",), "10.21203": ("researchsquare.com",), "10.1155": ("hindawi.com", "onlinelibrary.wiley.com"),
    "10.7717": ("peerj.com",), "10.12688": ("f1000research.com",), "10.1098": ("royalsocietypublishing.org",),
}
# Trial registry identifiers an abstract may cite, and the public page each resolves to.
REGISTRY_IDS = (
    (re.compile(r"\bNCT\d{8}\b"), "https://clinicaltrials.gov/study/{}"),
    (re.compile(r"\bISRCTN\d{8}\b"), "https://www.isrctn.com/{}"),
    (re.compile(r"\bACTRN\d{14}\b"), "https://www.anzctr.org.au/{}.aspx"),
    (re.compile(r"\bDRKS\d{8}\b"), "https://drks.de/search/en/trial/{}"),
    (re.compile(r"\bChiCTR[-\w]{6,20}\b"), "https://www.chictr.org.cn/searchprojEN.html?regno={}"),
    (re.compile(r"\bCTRI/\d{4}/\d{2,3}/\d{4,6}\b"), ""),
    (re.compile(r"\bjRCT\w{8,12}\b"), "https://jrct.niph.go.jp/en-latest-detail/{}"),
)


def doi_hosts(doi) -> list[str]:
    value = normalize_doi(doi)
    return list(DOI_PREFIX_HOSTS.get(value.split("/", 1)[0], ())) if value else []


def registry_ids(text) -> list[tuple[str, str]]:
    """Trial registry identifiers named in the text, with their registry page when it has a stable URL."""
    found = []
    for pattern, template in REGISTRY_IDS:
        for match in dict.fromkeys(pattern.findall(str(text or ""))):
            found.append((match, template.format(match) if template else ""))
    return found


def candidate_pages(paper: dict) -> list[str]:
    """Pages that belong to this paper by construction: its DOI link, registry entries, PMC record, landing page."""
    pages = []
    doi = normalize_doi(paper.get("doi"))
    if doi:
        pages.append(f"https://doi.org/{doi}")
    pages += [url for _id, url in registry_ids(f"{paper.get('abstract') or ''} {paper.get('title') or ''}") if url]
    pmcid = str(paper.get("pmcid") or "").strip().upper()
    if re.fullmatch(r"PMC\d+", pmcid):
        pages.append(f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/")
    for key in ("landing_page_url", "url"):
        url = safe_url(paper.get(key))
        if url and _host(url) in paper_hosts(paper):
            pages.append(url)
    return list(dict.fromkeys(pages))


class RejectedSource(ValueError):
    """A web source that fails a deterministic check; the reason is shown to the reviewer."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _on(host: str, domains) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def normalize_doi(value) -> str:
    text = unquote(str(value or "")).strip().lower()
    text = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", text)
    return text if text.startswith("10.") else ""


def paper_hosts(paper: dict) -> set[str]:
    """Hosts of the record's own landing pages, which count as the publisher's page for this paper."""
    hosts = set()
    for key in ("url", "landing_page_url", "publisher_url"):
        url = safe_url(paper.get(key))
        if url:
            hosts.add(_host(url))
    hosts.update(doi_hosts(paper.get("doi")))
    blocked = {"doi.org", "dx.doi.org", "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "openalex.org",
               "api.openalex.org", "semanticscholar.org", "api.semanticscholar.org", "scholar.google.com"}
    return {host for host in hosts if host and host not in blocked and not _denied(host, "")}


def _denied(host: str, path: str) -> str:
    if any(marker in host for marker in SHADOW_LIBRARY_MARKERS):
        return "shadow_library"
    if _on(host, SOCIAL_MEDIA_HOSTS):
        return "social_media"
    if _on(host, NEWS_OR_BLOG_HOSTS) or host.startswith(("blog.", "blogs.", "news.")) or re.search(r"/(news|blogs?)/", path):
        return "news_or_blog"
    return ""


def classify_source(url, paper: dict) -> str:
    """Return the allowed source type of a URL, or raise RejectedSource."""
    text = safe_url(url)
    if not text:
        raise RejectedSource("missing_url", "No http(s) source URL was given.")
    parts = urlsplit(text)
    host, path = _host(text), unquote(parts.path or "").lower()
    denied = _denied(host, path)
    if denied:
        raise RejectedSource(denied, f"{host} is a {denied.replace('_', ' ')} source.")
    doi = normalize_doi(paper.get("doi"))
    is_supplement = any(marker in path for marker in SUPPLEMENT_PATH_MARKERS)
    if _on(host, TRIAL_REGISTRY_HOSTS):
        return TRIAL_REGISTRY
    if _on(host, PMC_HOSTS) or host == "ncbi.nlm.nih.gov" and path.startswith("/pmc/"):
        return SUPPLEMENTARY if is_supplement else PMC
    if _on(host, PREPRINT_HOSTS) or host == "osf.io" and path.startswith("/preprints/"):
        return PREPRINT
    if host in {"doi.org", "dx.doi.org"}:
        if doi and normalize_doi(path.lstrip("/")) == doi:
            return PUBLISHER
        raise RejectedSource("not_allowed_source", "The DOI link does not resolve this paper's DOI.")
    if _on(host, PUBLISHER_HOSTS) or host in paper_hosts(paper):
        return SUPPLEMENTARY if is_supplement else PUBLISHER
    raise RejectedSource("not_allowed_source", f"{host} is outside the allowed source types.")


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.meta_dois: list[str] = []
        self.meta_titles: list[str] = []
        self._skip = 0
        self._capture: str | None = None
        self._captured: dict[str, list[str]] = {"title": [], "h1": []}

    def handle_starttag(self, tag, attrs):
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag in {"script", "style", "noscript", "template"}:
            self._skip += 1
        elif tag == "meta":
            name = (attributes.get("name") or attributes.get("property") or "").lower()
            content = attributes.get("content") or ""
            if name in {"citation_doi", "dc.identifier", "prism.doi", "bepress_citation_doi"} and normalize_doi(content):
                self.meta_dois.append(normalize_doi(content))
            elif name in {"citation_title", "dc.title", "og:title"} and content.strip():
                self.meta_titles.append(content.strip())
        elif tag in {"title", "h1"} and self._capture is None and not self._captured[tag]:
            self._capture = tag
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "section", "td"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "template"} and self._skip:
            self._skip -= 1
        if tag == self._capture:
            self._capture = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._capture:
            self._captured[self._capture].append(data)
        if self._capture != "title":
            self.parts.append(data)


def parse_page(url: str, body: bytes | str, content_type: str = "") -> dict:
    """Turn a fetched HTML, PDF or text response into page text plus declared identity."""
    if isinstance(body, bytes) and ("pdf" in content_type.lower() or body[:5] == b"%PDF-"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(body))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return {"url": url, "text": text, "declared_dois": [], "declared_titles": []}
    html = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    if "<" not in html:
        return {"url": url, "text": html, "declared_dois": [], "declared_titles": []}
    parser = _PageParser()
    parser.feed(html)
    titles = [*parser.meta_titles, " ".join(parser._captured["title"]).strip(), " ".join(parser._captured["h1"]).strip()]
    return {"url": url, "text": re.sub(r"[ \t]+", " ", "".join(parser.parts)),
            "declared_dois": list(dict.fromkeys(parser.meta_dois)), "declared_titles": [t for t in titles if t]}


def fetch_page(url: str) -> dict:
    """Fetch one public page as an ordinary client; challenged or failed pages are not retried."""
    import requests
    try:
        import config
        contact = str(getattr(config, "EMAIL", "") or "")
    except ImportError:
        contact = ""
    headers = {"User-Agent": "ReviewPilot evidence check" + (f" (mailto:{contact})" if contact else ""),
               "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,text/plain;q=0.8"}
    with requests.get(url, headers=headers, timeout=(10, 30), allow_redirects=True, stream=True) as response:
        if response.status_code >= 400:
            raise RejectedSource("page_unavailable", f"The page returned HTTP {response.status_code}.")
        body = b""
        for chunk in response.iter_content(65536):
            body += chunk
            if len(body) > MAX_PAGE_BYTES:
                raise RejectedSource("page_unavailable", "The page is too large to check.")
        return parse_page(response.url, body, response.headers.get("Content-Type", ""))


MIN_TITLE_CHARS = 12  # letters and digits; shorter titles are too generic to identify a paper


def _title_matches(title_key: str, candidate) -> bool:
    return len(title_key) >= MIN_TITLE_CHARS and title_key in _canonical(candidate)


def confirm_paper_page(page: dict, paper: dict, source_type: str) -> None:
    """Raise RejectedSource unless the page is about this paper rather than another work."""
    doi = normalize_doi(paper.get("doi"))
    title_key = _canonical(paper.get("title"))
    text_key = _canonical(page.get("text"))
    url_text = unquote(str(page.get("url") or "")).lower()
    doi_in_url = bool(doi) and doi in url_text
    declared_dois = {normalize_doi(value) for value in page.get("declared_dois") or []} - {""}
    named = (doi_in_url or bool(doi) and (doi in declared_dois or _canonical(doi) in text_key)
             or _title_matches(title_key, page.get("text")))
    if not named:
        raise RejectedSource("paper_not_on_page", "The page shows neither this paper's DOI nor its title.")
    if source_type == TRIAL_REGISTRY:
        return  # A registry entry names its publications; it never declares the paper as itself.
    title_declared = any(_title_matches(title_key, value) for value in page.get("declared_titles") or [])
    if declared_dois and doi not in declared_dois and not title_declared:
        raise RejectedSource("other_work", "The page is another work (for example one that cites or reviews this paper).")
    if source_type == SUPPLEMENTARY:
        return
    if not (doi_in_url or doi and doi in declared_dois or title_declared):
        raise RejectedSource("other_work", "The page does not present itself as this paper; it only mentions it.")


def check_quote(quote, page: dict) -> str:
    """Return the quote when it is one continuous excerpt of the page text, else raise RejectedSource."""
    text = str(quote or "").strip()[:4000]
    if not text:
        raise RejectedSource("missing_quote", "No quote from the page was given.")
    if re.search(r"\.\.\.|…", text):
        raise RejectedSource("quote_not_verbatim", "The quote joins separate passages.")
    if len(_canonical(text)) < MIN_QUOTE_CHARS:
        raise RejectedSource("quote_too_short", "The quote is too short to locate reliably.")
    if not locate_excerpt(text, [{"page": None, "text": page.get("text") or ""}]):
        raise RejectedSource("quote_not_on_page", "The quote does not occur in the page text.")
    return text


def accept_web_evidence(entry: dict, paper: dict, fetcher, cache: dict) -> dict:
    """Check one {url, quote} entry; return stored evidence or raise RejectedSource."""
    url = safe_url(entry.get("url") or entry.get("source_url"))
    source_type = classify_source(url, paper)
    if not str(entry.get("quote") or "").strip():
        raise RejectedSource("missing_quote", "No quote from the page was given.")
    if url not in cache:
        try:
            cache[url] = fetcher(url)
        except RejectedSource as exc:
            cache[url] = exc
        except Exception as exc:  # network failures leave the value unconfirmed, never accepted
            cache[url] = RejectedSource("page_unavailable", f"The page could not be fetched: {type(exc).__name__}.")
    page = cache[url]
    if isinstance(page, RejectedSource):
        raise page
    final_url = safe_url(page.get("url")) or url
    if _host(final_url) != _host(url):
        if _host(url) in {"doi.org", "dx.doi.org"}:
            # The DOI registry sends this paper's DOI to its publisher; only a denied host is refused.
            denied = _denied(_host(final_url), unquote(urlsplit(final_url).path or "").lower())
            if denied:
                raise RejectedSource(denied, f"The DOI link led to a {denied.replace('_', ' ')} source.")
        else:
            source_type = classify_source(final_url, paper)
    confirm_paper_page(page, paper, source_type)
    quote = check_quote(entry.get("quote"), page)
    evidence = {"quote": quote, "page": None, "status": "located", "verification": "verbatim_web_page_match",
                "source_url": url, "source_type": source_type, "needs_human_check": True}
    if final_url != url:
        evidence["resolved_url"] = final_url
    return evidence
