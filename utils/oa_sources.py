"""Open-access metadata and official full-text routes for the PDF downloader.

Batch lookups (OpenAlex, Semantic Scholar, NCBI ID converter) run once per
download batch, so workers read shared results instead of calling rate-limited
APIs paper by paper. The resolvers below turn identifiers or repository landing
pages into direct PDF URLs through each service's documented API or public
storage: the PMC Cloud Service bucket (NCBI's AWS Open Data copy of PMC),
Zenodo, OSF, HAL, DiVA and repository citation metadata.

Every route here is a legitimate open-access channel. API calls identify the
tool with an honest User-Agent and contact address.
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, urljoin, urlparse

import requests

PMC_CLOUD_BASE = "https://pmc-oa-opendata.s3.amazonaws.com"
IDCONV_URL = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"

# Hosts that only list records; their URLs are never full-text candidates.
METADATA_HOSTS = (
    "openalex.org",
    "pubmed.ncbi.nlm.nih.gov",
    "semanticscholar.org",
    "worldcat.org",
    "openlibrary.org",
    "ci.nii.ac.jp",
    "europepmc.org/abstract",
)


def api_user_agent(email: str) -> str:
    contact = f"; mailto:{email}" if email and "@" in email and not email.endswith("example.com") else ""
    return f"ReviewPilot/1.0 (systematic-review PDF retrieval{contact})"


def normalize_doi(doi: Optional[str]) -> str:
    value = str(doi or "").strip().lower()
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value)
    value = re.sub(r"^doi:\s*", "", value)
    return value.strip()


def normalize_pmid(pmid) -> str:
    value = str(pmid or "").strip()
    match = re.search(r"(\d{4,9})/?$", value)
    return match.group(1) if match else ""


def normalize_pmcid(pmcid) -> str:
    value = str(pmcid or "").strip().upper()
    match = re.search(r"(PMC)?(\d{3,10})$", value)
    return f"PMC{match.group(2)}" if match else ""


def arxiv_id_from_text(value: str) -> str:
    text = str(value or "")
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([a-z\-]+/\d{7}|\d{4}\.\d{4,5})(v\d+)?", text, re.IGNORECASE)
    if match:
        return match.group(1) + (match.group(2) or "")
    match = re.search(r"10\.48550/arxiv\.(\d{4}\.\d{4,5})", text, re.IGNORECASE)
    return match.group(1) if match else ""


def openalex_work_id(paper: Dict) -> str:
    for value in (paper.get("openalex_id"), paper.get("id"), paper.get("url")):
        match = re.search(r"openalex\.org/(W\d+)", str(value or ""), re.IGNORECASE)
        if match:
            return match.group(1).upper()
        if re.fullmatch(r"W\d+", str(value or ""), re.IGNORECASE):
            return str(value).upper()
    return ""


def paper_pmid(paper: Dict) -> str:
    if paper.get("pmid"):
        return normalize_pmid(paper.get("pmid"))
    if (paper.get("source") or "").lower() == "pubmed":
        return normalize_pmid(paper.get("id") or paper.get("url"))
    match = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", str(paper.get("url") or ""))
    return match.group(1) if match else ""


FILE_URL_PATTERN = re.compile(
    r"(\.pdf($|[?#])|/bitstream/|/bitstreams/[^/]+/(download|content)|/server/api/core/bitstreams/|"
    r"/ws/files/|/ws/portalfiles/|/article/download/|/smash/get/|viewcontent\.cgi)",
    re.IGNORECASE,
)


def looks_like_file_url(url: str) -> bool:
    return bool(url) and bool(FILE_URL_PATTERN.search(url))


def is_metadata_url(url: str) -> bool:
    lowered = str(url or "").lower()
    return any(host in lowered for host in METADATA_HOSTS)


def _chunks(values: List[str], size: int) -> Iterable[List[str]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


class OAMetadataIndex:
    """Shared, thread-safe open-access metadata for one download batch.

    Records are keyed by identifier ("doi:…", "pmid:…", "openalex:W…") and
    merged on lookup. `prefetch` fills the index with a handful of batch calls;
    `lookup` falls back to single-paper calls for papers that were not
    prefetched (e.g. a single `download()` outside a batch).
    """

    def __init__(
        self,
        email: str = "",
        session: Optional[requests.Session] = None,
        timeout: int = 20,
        sleep_func: Callable[[float], None] = time.sleep,
        enabled_sources: Iterable[str] = ("openalex", "s2", "idconv"),
    ):
        self.email = email if email and "@" in email else ""
        self.session = session or requests.Session()
        self.timeout = timeout
        self.sleep_func = sleep_func
        self.enabled_sources = set(enabled_sources)
        self.headers = {"User-Agent": api_user_agent(self.email), "Accept": "application/json"}
        self.openalex_api_key = os.getenv("OPENALEX_API_KEY") or ""
        self._lock = threading.RLock()
        self._records: Dict[str, Dict] = {}
        self._attempted: set = set()
        self._exhausted_hosts: set = set()
        self.errors: List[str] = []

    # ----------------------------------------------------------------- keys
    def paper_keys(self, paper: Dict) -> List[str]:
        keys = []
        doi = normalize_doi(paper.get("doi"))
        if doi:
            keys.append(f"doi:{doi}")
        pmid = paper_pmid(paper)
        if pmid:
            keys.append(f"pmid:{pmid}")
        work = openalex_work_id(paper)
        if work:
            keys.append(f"openalex:{work}")
        return keys

    def _merge(self, key: str, update: Dict) -> None:
        with self._lock:
            record = self._records.setdefault(key, {"locations": []})
            for field, value in update.items():
                if field == "locations":
                    seen = {(loc.get("pdf_url"), loc.get("landing_url")) for loc in record["locations"]}
                    for loc in value:
                        marker = (loc.get("pdf_url"), loc.get("landing_url"))
                        if marker not in seen:
                            record["locations"].append(loc)
                            seen.add(marker)
                elif value not in (None, "", [], {}) and not record.get(field):
                    record[field] = value

    def lookup(self, paper: Dict) -> Dict:
        keys = self.paper_keys(paper)
        if keys and not all(key in self._attempted for key in keys):
            self.prefetch([paper])
        merged: Dict = {"locations": []}
        with self._lock:
            for key in keys:
                record = self._records.get(key) or {}
                for field, value in record.items():
                    if field == "locations":
                        merged["locations"].extend(value)
                    elif value not in (None, "", [], {}) and not merged.get(field):
                        merged[field] = value
        # Identifiers learned from one source unlock the others (OpenAlex/S2 give a PMID
        # for a DOI-only record; the ID converter then gives its PMCID).
        pmid_key = f"pmid:{merged.get('pmid')}"
        if merged.get("pmid") and not merged.get("pmcid") and pmid_key not in keys and "idconv" in self.enabled_sources:
            with self._lock:
                first_time = f"idconv-{pmid_key}" not in self._attempted
                self._attempted.add(f"idconv-{pmid_key}")
            if first_time:
                self._idconv([merged["pmid"]], "pmid")
            with self._lock:
                merged["pmcid"] = (self._records.get(pmid_key) or {}).get("pmcid")
        return merged

    # ------------------------------------------------------------- prefetch
    def prefetch(self, papers: List[Dict]) -> None:
        dois, pmids, works = [], [], []
        for paper in papers:
            for key in self.paper_keys(paper):
                if key in self._attempted:
                    continue
                kind, value = key.split(":", 1)
                {"doi": dois, "pmid": pmids, "openalex": works}[kind].append(value)
        dois, pmids, works = (sorted(set(v)) for v in (dois, pmids, works))
        with self._lock:
            self._attempted.update([f"doi:{d}" for d in dois] + [f"pmid:{p}" for p in pmids] + [f"openalex:{w}" for w in works])
        if "openalex" in self.enabled_sources:
            self._openalex(dois, pmids, works)
        if "s2" in self.enabled_sources:
            self._semantic_scholar(dois, pmids)
        if "idconv" in self.enabled_sources:
            self._idconv(pmids, "pmid")
            self._idconv(dois, "doi")

    def _quota_exhausted(self, response) -> bool:
        """A 429 whose Retry-After is minutes or hours away means the daily quota is spent."""
        try:
            return response.status_code == 429 and float(response.headers.get("retry-after") or 0) > 60
        except (TypeError, ValueError):
            return False

    def _get(self, url: str, params: Dict, attempts: int = 3) -> Optional[requests.Response]:
        host = urlparse(url).netloc
        if host in self._exhausted_hosts:
            return None
        for attempt in range(attempts):
            try:
                response = self.session.get(url, params=params, headers=self.headers, timeout=self.timeout)
            except Exception as exc:
                self.errors.append(f"{host}: {type(exc).__name__}")
                self.sleep_func(1 + attempt)
                continue
            if self._quota_exhausted(response):
                # Circuit breaker: stop calling this API for the rest of the run.
                self._exhausted_hosts.add(host)
                self.errors.append(f"{host}: quota exhausted (Retry-After {response.headers.get('retry-after')})")
                return None
            if response.status_code == 429 or response.status_code >= 500:
                self.sleep_func(self._retry_after(response, attempt))
                continue
            return response
        return None

    @staticmethod
    def _retry_after(response, attempt: int) -> float:
        try:
            return min(30.0, float(response.headers.get("retry-after")))
        except (TypeError, ValueError):
            return 2.0 * (attempt + 1)

    def _openalex(self, dois: List[str], pmids: List[str], works: List[str]) -> None:
        select = "id,doi,ids,type,title,open_access,best_oa_location,primary_location,locations,has_content,content_urls"
        cache = _OpenAlexCache.load()
        for field, values in (("doi", dois), ("pmid", pmids), ("openalex_id", works)):
            missing = []
            for value in values:
                cached = cache.get(f"{field}:{value}")
                if cached is None:
                    missing.append(value)
                elif cached:
                    self._ingest_openalex(cached)
            for chunk in _chunks(missing, 50):
                params = {"filter": f"{field}:{'|'.join(chunk)}", "select": select, "per-page": 50}
                if self.email:
                    params["mailto"] = self.email
                if self.openalex_api_key:
                    params["api_key"] = self.openalex_api_key
                response = self._get(OPENALEX_WORKS_URL, params)
                if response is None or response.status_code != 200:
                    self.errors.append(f"openalex {field}: {getattr(response, 'status_code', 'none')}")
                    continue
                try:
                    results = response.json().get("results", [])
                except Exception:
                    continue
                seen = set()
                for work in results:
                    self._ingest_openalex(work)
                    ids = work.get("ids") or {}
                    for key in (f"doi:{normalize_doi(work.get('doi') or ids.get('doi'))}",
                                f"pmid:{normalize_pmid(ids.get('pmid'))}",
                                f"openalex_id:{openalex_work_id({'id': work.get('id')})}"):
                        if not key.endswith(":"):
                            cache.put(key, work)
                            seen.add(key)
                for value in chunk:
                    if f"{field}:{value}" not in seen:
                        cache.put(f"{field}:{value}", {})  # not in OpenAlex
                cache.save()

    def _ingest_openalex(self, work: Dict) -> None:
        ids = work.get("ids") or {}
        doi = normalize_doi(work.get("doi") or ids.get("doi"))
        pmid = normalize_pmid(ids.get("pmid"))
        pmcid = normalize_pmcid(ids.get("pmcid"))
        wid = openalex_work_id({"id": work.get("id")})
        locations = []
        arxiv_id = ""
        for loc in [work.get("best_oa_location"), work.get("primary_location")] + list(work.get("locations") or []):
            if not loc:
                continue
            source = loc.get("source") or {}
            landing = loc.get("landing_page_url") or ""
            pdf_url = loc.get("pdf_url") or ""
            arxiv_id = arxiv_id or arxiv_id_from_text(f"{landing} {pdf_url}")
            is_oa = bool(loc.get("is_oa"))
            host_type = source.get("type") or ""
            low_priority = False
            if not is_oa:
                # Locations not flagged OA are often open repository files all the same
                # (OpenAlex OA flags lag); keep file-like and repository ones, tried last.
                if looks_like_file_url(landing) and not pdf_url:
                    pdf_url, low_priority = landing, True
                elif host_type == "repository" and landing:
                    low_priority = True
                elif not pdf_url:
                    continue
                else:
                    low_priority = True
            if not pdf_url and not landing:
                continue
            locations.append({
                "pdf_url": pdf_url,
                "landing_url": landing if (is_oa or host_type == "repository") else "",
                "source": "openalex",
                "host": source.get("display_name") or "",
                "host_type": host_type,
                "version": loc.get("version") or "",
                "license": loc.get("license") or "",
                "low_priority": low_priority,
            })
        content_pdf = ""
        if (work.get("has_content") or {}).get("pdf") and wid:
            content_pdf = (work.get("content_urls") or {}).get("pdf") or f"https://content.openalex.org/works/{wid}.pdf"
        update = {
            "doi": doi, "pmid": pmid, "pmcid": pmcid, "arxiv_id": arxiv_id, "openalex_id": wid,
            "work_type": work.get("type"), "is_oa": (work.get("open_access") or {}).get("is_oa"),
            "title": work.get("title") or "", "content_pdf": re.sub(r"[?&]api_key=[^&]*", "", content_pdf),
            "licenses": sorted({loc.get("license") for loc in locations if loc.get("license")}),
            "locations": locations,
        }
        for key in ([f"doi:{doi}"] if doi else []) + ([f"pmid:{pmid}"] if pmid else []) + ([f"openalex:{wid}"] if wid else []):
            self._merge(key, update)

    def _semantic_scholar(self, dois: List[str], pmids: List[str]) -> None:
        ids = [f"DOI:{d}" for d in dois] + [f"PMID:{p}" for p in pmids]
        cache = _S2Cache.load()
        missing = []
        for requested in ids:
            item = cache.get(requested)
            if item is None:
                missing.append(requested)
            elif item:
                self._ingest_s2(requested, item)
        for chunk in _chunks(missing, 200):
            if "api.semanticscholar.org" in self._exhausted_hosts:
                break
            data = None
            for attempt in range(8):
                try:
                    response = self.session.post(
                        S2_BATCH_URL,
                        params={"fields": "openAccessPdf,externalIds,title"},
                        json={"ids": chunk},
                        headers=self.headers,
                        timeout=max(self.timeout, 40),
                    )
                except Exception as exc:
                    self.errors.append(f"s2: {type(exc).__name__}")
                    self.sleep_func(2 + attempt)
                    continue
                if self._quota_exhausted(response):
                    self._exhausted_hosts.add("api.semanticscholar.org")
                    break
                if response.status_code == 429 or response.status_code >= 500:
                    # Unauthenticated batch calls share a pool; back off up to ~2 minutes in total.
                    self.sleep_func(max(min(30.0, 3.0 * 2 ** attempt), self._retry_after(response, attempt)))
                    continue
                if response.status_code == 200:
                    try:
                        data = response.json()
                    except Exception:
                        data = None
                break
            if not isinstance(data, list):
                self.errors.append("s2 batch: no data")
                continue
            for requested, item in zip(chunk, data):
                cache.put(requested, item or {})
                if item:
                    self._ingest_s2(requested, item)
            cache.save()
            self.sleep_func(1.0)

    def _ingest_s2(self, requested: str, item: Dict) -> None:
        external = item.get("externalIds") or {}
        pdf = (item.get("openAccessPdf") or {}).get("url") or ""
        update = {
            "s2_found": True,
            "doi": normalize_doi(external.get("DOI")),
            "pmid": normalize_pmid(external.get("PubMed")),
            "pmcid": normalize_pmcid(external.get("PubMedCentral")),
            "arxiv_id": str(external.get("ArXiv") or ""),
            "acl_id": str(external.get("ACL") or ""),
            "locations": [{"pdf_url": pdf, "landing_url": "", "source": "semantic_scholar",
                           "host": "", "host_type": "", "version": ""}] if pdf else [],
        }
        kind, value = requested.split(":", 1)
        self._merge(f"{kind.lower()}:{value.lower() if kind == 'DOI' else value}", update)

    def _idconv(self, values: List[str], idtype: str) -> None:
        values = [v for v in values if v]
        for chunk in _chunks(values, 150):
            params = {"ids": ",".join(chunk), "format": "json", "tool": "reviewpilot"}
            if self.email:
                params["email"] = self.email
            response = self._get(IDCONV_URL, params)
            if response is None or response.status_code != 200:
                self.errors.append(f"idconv {idtype}: {getattr(response, 'status_code', 'none')}")
                continue
            try:
                records = response.json().get("records", [])
            except Exception:
                continue
            for record in records:
                pmcid = normalize_pmcid(record.get("pmcid"))
                if not pmcid:
                    continue
                update = {"pmcid": pmcid, "pmid": normalize_pmid(record.get("pmid")),
                          "doi": normalize_doi(record.get("doi")),
                          "pmc_live": record.get("live") is not False}
                requested = str(record.get("requested-id") or "")
                key = f"doi:{normalize_doi(requested)}" if idtype == "doi" else f"pmid:{normalize_pmid(requested)}"
                self._merge(key, update)


class _DiskCache:
    """30-day JSON disk cache of metadata-API answers (including "not found"), so a
    rate-limited or quota-limited run keeps what earlier runs learned."""

    TTL_SECONDS = 30 * 24 * 3600
    ENV = ""
    DEFAULT_PATH = ""
    _lock = threading.Lock()
    _instance = None

    def __init__(self, path: str):
        self.path = path
        self.data: Dict[str, Dict] = {}
        try:
            import json
            with open(path, encoding="utf-8") as handle:
                self.data = json.load(handle)
        except Exception:
            self.data = {}

    @classmethod
    def load(cls):
        with cls._lock:
            path = os.getenv(cls.ENV) or cls.DEFAULT_PATH
            if cls._instance is None or cls._instance.path != path:
                cls._instance = cls(path)
            return cls._instance

    def get(self, key: str) -> Optional[Dict]:
        entry = self.data.get(key)
        if not entry or time.time() - entry.get("t", 0) > self.TTL_SECONDS:
            return None
        return entry.get("item") or {}

    def put(self, key: str, item: Dict) -> None:
        with self._lock:
            self.data[key] = {"t": time.time(), "item": item}

    def save(self) -> None:
        import json
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                tmp = f"{self.path}.tmp"
                with open(tmp, "w", encoding="utf-8") as handle:
                    json.dump(self.data, handle)
                os.replace(tmp, self.path)
            except Exception:
                pass


class _S2Cache(_DiskCache):
    ENV = "REVIEWPILOT_S2_CACHE"
    DEFAULT_PATH = ".cache/semantic_scholar_batch.json"
    _lock = threading.Lock()
    _instance = None


class _OpenAlexCache(_DiskCache):
    ENV = "REVIEWPILOT_OPENALEX_CACHE"
    DEFAULT_PATH = ".cache/openalex_works.json"
    _lock = threading.Lock()
    _instance = None


# --------------------------------------------------------------- resolvers
def pmc_cloud_pdf_url(session, pmcid: str, timeout: int = 15, headers: Optional[Dict] = None) -> Optional[str]:
    """Direct PDF URL in NCBI's PMC Cloud Service bucket, or None when not deposited there.

    The bucket holds the PMC open-access subset and author manuscripts; the
    metadata JSON names the article's PDF (absent for XML-only deposits).
    """
    pmcid = normalize_pmcid(pmcid)
    if not pmcid:
        return None
    try:
        listing = session.get(f"{PMC_CLOUD_BASE}/", params={"list-type": "2", "prefix": f"metadata/{pmcid}."},
                              timeout=timeout, headers=headers)
    except Exception:
        return None
    if listing.status_code != 200:
        return None
    versions = [int(v) for v in re.findall(rf"<Key>metadata/{pmcid}\.(\d+)\.json</Key>", listing.text)]
    if not versions:
        return None
    version = max(versions)
    try:
        meta = session.get(f"{PMC_CLOUD_BASE}/metadata/{pmcid}.{version}.json", timeout=timeout, headers=headers)
        pdf_url = (meta.json() or {}).get("pdf_url") if meta.status_code == 200 else None
    except Exception:
        pdf_url = None
    if pdf_url:
        return re.sub(r"\?md5=.*$", "", pdf_url.replace("s3://pmc-oa-opendata/", f"{PMC_CLOUD_BASE}/"))
    return None


def jmir_asset_pdf_urls(doi: str) -> List[str]:
    """JMIR publishes accepted manuscripts in its public asset bucket (the jmir.org site is WAF-gated)."""
    match = re.match(r"^10\.2196/(preprints\.)?(?:[a-z]+\.)?(\d+)$", normalize_doi(doi))
    if not match:
        return []
    number = match.group(2)
    base = "https://s3.ca-central-1.amazonaws.com/assets.jmir.org/assets/preprints/preprint-"
    order = ("submitted", "accepted") if match.group(1) else ("accepted", "submitted")
    return [f"{base}{number}-{stage}.pdf" for stage in order]


def zenodo_pdf_urls(session, record_id: str, headers: Dict, timeout: int = 15) -> List[str]:
    try:
        response = session.get(f"https://zenodo.org/api/records/{record_id}", headers=headers, timeout=timeout)
        data = response.json() if response.status_code == 200 else None
    except Exception:
        data = None
    if not data:
        return []
    resource = ((data.get("metadata") or {}).get("resource_type") or {}).get("type") or ""
    if resource in {"software", "dataset", "image", "video"}:
        return []
    files = [f for f in data.get("files") or [] if str(f.get("key", "")).lower().endswith(".pdf")]
    files = [f for f in files if not re.search(r"supple|appendix", str(f.get("key", "")), re.IGNORECASE)] or files
    files.sort(key=lambda f: -(f.get("size") or 0))
    return [(f.get("links") or {}).get("self") for f in files if (f.get("links") or {}).get("self")]


def figshare_article_id(doi: str) -> str:
    """Article id for DOIs minted by figshare and its institutional portals (KiltHub, …)."""
    match = re.search(r"(?:m9\.figshare\.|/r\d+/)(\d{5,})(?:\.v\d+)?$", normalize_doi(doi))
    return match.group(1) if match else ""


def figshare_pdf_urls(session, article_id: str, headers: Dict, timeout: int = 15) -> List[str]:
    if not article_id:
        return []
    try:
        response = session.get(f"https://api.figshare.com/v2/articles/{article_id}", headers=headers, timeout=timeout)
        data = response.json() if response.status_code == 200 else None
    except Exception:
        data = None
    files = [f for f in (data or {}).get("files") or [] if str(f.get("name", "")).lower().endswith(".pdf")]
    files = [f for f in files if not re.search(r"supple|appendix", str(f.get("name", "")), re.IGNORECASE)] or files
    files.sort(key=lambda f: -(f.get("size") or 0))
    return [f.get("download_url") for f in files if f.get("download_url")]


def osf_pdf_urls(session, osf_id: str, headers: Dict, timeout: int = 15) -> List[str]:
    """PDF of an OSF-hosted preprint (PsyArXiv, SocArXiv, OSF Preprints, …) via the OSF API."""
    osf_id = osf_id.strip("/")
    try:
        response = session.get(f"https://api.osf.io/v2/preprints/{osf_id}/", headers=headers, timeout=timeout)
        data = response.json() if response.status_code == 200 else None
    except Exception:
        data = None
    file_id = ((((data or {}).get("data") or {}).get("relationships") or {}).get("primary_file") or {}).get("data") or {}
    file_id = file_id.get("id") if isinstance(file_id, dict) else None
    if not file_id:
        return []
    download = f"https://osf.io/download/{file_id}/"
    render = ("https://mfr.osf.io/export?url="
              + quote(f"https://osf.io/download/{file_id}/?direct&mode=render", safe="") + "&format=pdf")
    return [download, render]


def osf_id_from(doi: str, url: str = "") -> str:
    match = re.search(r"osf\.io/([a-z0-9]{5,}(?:_v\d+)?)", f"{normalize_doi(doi)} {url}", re.IGNORECASE)
    return match.group(1) if match else ""


def repository_pdf_urls(url: str, html_text: str) -> List[str]:
    """PDF URLs a repository landing page declares (citation metadata, DiVA, HAL)."""
    from lxml import html as lxml_html

    candidates: List[str] = []
    host = urlparse(url).netloc.lower()
    if html_text:
        try:
            doc = lxml_html.fromstring(html_text)
        except Exception:
            doc = None
        if doc is not None:
            for name in ("citation_pdf_url", "bepress_citation_pdf_url", "eprints.document_url"):
                candidates.extend(doc.xpath(
                    "//meta[translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')=$n]/@content", n=name))
            candidates.extend(doc.xpath("//link[@type='application/pdf']/@href"))
    if host.endswith("hal.science") or "archives-ouvertes.fr" in host:
        match = re.search(r"/(hal-\d+)(v\d+)?", urlparse(url).path)
        if match:
            candidates.append(f"https://hal.science/{match.group(1)}{match.group(2) or ''}/document")
    match = re.search(r"diva2:(\d+)", f"{url} {html_text[:20000] if html_text else ''}")
    if match and "diva-portal.org" in host:
        candidates.append(f"https://{host}/smash/get/diva2:{match.group(1)}/FULLTEXT01.pdf")
    absolute = []
    for candidate in candidates:
        candidate = str(candidate or "").strip()
        if candidate:
            absolute.append(urljoin(url, candidate))
    seen, ordered = set(), []
    for candidate in absolute:
        if candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def ojs_download_url(url: str) -> Optional[str]:
    """OJS galley viewer -> direct download (…/article/view/X/Y -> …/article/download/X/Y)."""
    match = re.match(r"^(.*?/article)/view/(\d+)/([^/?#]+)$", url or "")
    if not match:
        return None
    return f"{match.group(1)}/download/{match.group(2)}/{match.group(3)}"


def ieee_oa_pdf_urls(session, doi: str, headers: Dict, timeout: int = 15) -> Dict:
    """Use Crossref to find the IEEE arnumber and whether the article carries a CC licence."""
    doi = normalize_doi(doi)
    try:
        response = session.get(f"https://api.crossref.org/works/{quote(doi, safe='/')}", headers=headers, timeout=timeout)
        message = (response.json() or {}).get("message") if response.status_code == 200 else None
    except Exception:
        message = None
    arnumber = ""
    if message:
        for link in message.get("link") or []:
            match = re.search(r"arnumber=(\d+)", str(link.get("URL") or ""))
            if match:
                arnumber = match.group(1)
                break
    licensed_cc = bool(message) and any(
        "creativecommons.org" in str(lic.get("URL") or "") for lic in message.get("license") or []
    )
    return {"arnumber": arnumber, "cc_license": licensed_cc, "crossref_found": bool(message)}


def crossref_published_dois(session, doi: str, headers: Dict, timeout: int = 15) -> List[str]:
    """Version-of-record DOIs for a preprint DOI (Crossref relation is-preprint-of)."""
    try:
        response = session.get(f"https://api.crossref.org/works/{quote(normalize_doi(doi), safe='/')}",
                               headers=headers, timeout=timeout)
        message = (response.json() or {}).get("message") if response.status_code == 200 else None
    except Exception:
        message = None
    relations = ((message or {}).get("relation") or {}).get("is-preprint-of") or []
    return [normalize_doi(rel.get("id")) for rel in relations if rel.get("id-type") == "doi" and rel.get("id")]


# ------------------------------------------------- repository platform resolvers
def resolve_handle(session, handle: str, headers: Dict, timeout: int = 15) -> str:
    """Target URL of a Handle (hdl.handle.net) via the Handle.Net proxy REST API."""
    try:
        response = session.get(f"https://hdl.handle.net/api/handles/{handle}", headers=headers, timeout=timeout)
        values = (response.json() or {}).get("values") if response.status_code == 200 else None
    except Exception:
        values = None
    for value in values or []:
        if value.get("type") == "URL":
            return str((value.get("data") or {}).get("value") or "")
    return ""


def handle_from_url(url: str) -> str:
    match = re.search(r"(?:hdl\.handle\.net/|/handle/)(\d+(?:\.\d+)*/[^/?#\s]+)", url or "")
    return match.group(1) if match else ""


_DSPACE7_HOSTS: Dict[str, bool] = {}


def dspace7_pdf_urls(session, url: str, headers: Dict, timeout: int = 15) -> List[str]:
    """PDF content URLs of a DSpace 7 item through its documented REST API.

    DSpace 7 user interfaces render client-side (no citation_pdf_url in the HTML) and
    their UI download routes are often guarded; /server/api is the machine interface.
    Only files in the item's ORIGINAL bundle are returned.
    """
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    path = parsed.path
    match = re.search(r"/bitstreams/([0-9a-f-]{36})/(?:download|content)", path)
    if match:
        return [f"https://{host}/server/api/core/bitstreams/{match.group(1)}/content"]
    if host in ("hdl.handle.net", "www.hdl.handle.net"):
        handle = handle_from_url(url)
        target = resolve_handle(session, handle, headers, timeout) if handle else ""
        if not target:
            return []
        parsed = urlparse(target)
        host, path = parsed.netloc.lower(), f"/handle/{handle}"
    if _DSPACE7_HOSTS.get(host) is False:
        return []
    api_headers = {**headers, "Accept": "application/json"}
    item_uuid = ""
    match = re.search(r"/(?:items|entities/[a-z]+)/([0-9a-f-]{36})", path)
    if match:
        item_uuid = match.group(1)
    else:
        handle = handle_from_url(f"https://{host}{path}")
        if not handle:
            return []
        try:
            response = session.get(f"https://{host}/server/api/pid/find", params={"id": f"hdl:{handle}"},
                                   headers=api_headers, timeout=timeout, allow_redirects=True)
            data = response.json() if response.status_code == 200 else None
        except Exception:
            data = None
        if not isinstance(data, dict) or not data.get("uuid"):
            _DSPACE7_HOSTS.setdefault(host, False)
            return []
        item_uuid = data["uuid"]
    _DSPACE7_HOSTS[host] = True
    try:
        response = session.get(f"https://{host}/server/api/core/items/{item_uuid}/bundles",
                               params={"embed": "bitstreams"}, headers=api_headers, timeout=timeout)
        bundles = ((response.json() or {}).get("_embedded") or {}).get("bundles") if response.status_code == 200 else None
    except Exception:
        bundles = None
    files = []
    for bundle in bundles or []:
        if str(bundle.get("name") or "").upper() != "ORIGINAL":
            continue
        for bitstream in (((bundle.get("_embedded") or {}).get("bitstreams") or {}).get("_embedded") or {}).get("bitstreams") or []:
            name = str(bitstream.get("name") or "")
            href = ((bitstream.get("_links") or {}).get("content") or {}).get("href")
            if not href or not name.lower().endswith(".pdf"):
                continue
            if re.search(r"supple|appendix|_cover\.|cover\.pdf|_title\.pdf|abstrak", name, re.IGNORECASE):
                continue
            files.append((bitstream.get("sizeBytes") or 0, href))
    return [href for _, href in sorted(files, key=lambda item: -item[0])]


def nva_publication_id(session, url: str, headers: Dict, timeout: int = 15) -> str:
    """NVA (Norway) registration id for Brage/DUO handles, URN:NBN links or NVA URLs."""
    match = re.search(r"nva\.sikt\.no/registration/([0-9a-z-]+)", url or "", re.IGNORECASE)
    if match:
        return match.group(1)
    handle = handle_from_url(url)
    target = ""
    if handle and handle.split("/")[0] in ("11250", "10852", "10037", "11250.1"):
        target = resolve_handle(session, handle, headers, timeout)
    elif re.search(r"urn\.nb\.no/|brage\.unit\.no|duo\.uio\.no", url or ""):
        try:
            response = session.get(url, headers=headers, timeout=timeout, allow_redirects=False)
            target = response.headers.get("location") or ""
        except Exception:
            target = ""
    match = re.search(r"nva\.sikt\.no/registration/([0-9a-z-]+)", target or "", re.IGNORECASE)
    return match.group(1) if match else ""


def nva_pdf_urls(session, publication_id: str, headers: Dict, timeout: int = 15) -> Iterable[str]:
    """Open files of an NVA publication; each yields a short-lived presigned URL."""
    api = "https://api.nva.unit.no/publication"
    try:
        response = session.get(f"{api}/{publication_id}", headers={**headers, "Accept": "application/json"}, timeout=timeout)
        data = response.json() if response.status_code == 200 else None
    except Exception:
        data = None
    artifacts = []
    for artifact in (data or {}).get("associatedArtifacts") or []:
        if artifact.get("type") not in ("OpenFile", "PublishedFile"):
            continue
        if str(artifact.get("mimeType") or "") != "application/pdf":
            continue
        if artifact.get("embargoDate") and str(artifact["embargoDate"])[:10] > time.strftime("%Y-%m-%d"):
            continue
        name = str(artifact.get("name") or "")
        rank = (0 if "fulltext" in name.lower() else 1 if "cover" not in name.lower() else 3,
                0 if artifact.get("publisherVersion") == "PublishedVersion" else 1, -(artifact.get("size") or 0))
        artifacts.append((rank, artifact.get("identifier")))
    for _, identifier in sorted(artifacts, key=lambda item: item[0]):
        try:
            link = session.get(f"{api}/{publication_id}/filelink/{identifier}",
                               headers={**headers, "Accept": "application/json"}, timeout=timeout)
            presigned = (link.json() or {}).get("id") if link.status_code == 200 else None
        except Exception:
            presigned = None
        if presigned:
            yield presigned


def doaj_fulltext_links(session, url: str, headers: Dict, timeout: int = 15) -> List[Tuple[str, str]]:
    """(url, content_type) full-text links of a DOAJ article record via the public DOAJ API."""
    match = re.search(r"doaj\.org/(?:article|api/articles)/([0-9a-f]{32})", url or "")
    if not match:
        return []
    try:
        response = session.get(f"https://doaj.org/api/articles/{match.group(1)}", headers=headers, timeout=timeout)
        bibjson = (response.json() or {}).get("bibjson") if response.status_code == 200 else None
    except Exception:
        bibjson = None
    links = []
    for link in (bibjson or {}).get("link") or []:
        if link.get("type") == "fulltext" and link.get("url"):
            links.append((link["url"], str(link.get("content_type") or "").upper()))
    return links


def figshare_article_id_from_url(url: str) -> str:
    match = re.search(r"figshare\.com/articles/(?:[^/]+/)*(\d{5,})(?:/\d+)?/?$", (url or "").split("?")[0])
    return match.group(1) if match else ""


def crossref_has_open_licence(session, doi: str, headers: Dict, timeout: int = 15) -> Optional[bool]:
    try:
        response = session.get(f"https://api.crossref.org/works/{quote(normalize_doi(doi), safe='/')}",
                               headers=headers, timeout=timeout)
        message = (response.json() or {}).get("message") if response.status_code == 200 else None
    except Exception:
        return None
    if not message:
        return None
    return any("creativecommons.org" in str(lic.get("URL") or "") for lic in message.get("license") or [])


# ------------------------------------------- batched "other version" lookups
def _norm_title(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def titles_match(a: str, b: str, threshold: float = 0.92) -> bool:
    from difflib import SequenceMatcher

    left, right = _norm_title(a), _norm_title(b)
    if len(left) < 20 or len(right) < 20:
        return False
    return SequenceMatcher(None, left, right).ratio() >= threshold


def crossref_related_dois(session, dois: List[str], headers: Dict, timeout: int = 30) -> Dict[str, List[str]]:
    """Preprint <-> version-of-record DOIs from Crossref relations (batched, 20 DOIs per call)."""
    related: Dict[str, List[str]] = {}
    kinds = ("has-preprint", "is-preprint-of", "has-version", "is-version-of", "is-same-as")
    for chunk in _chunks([normalize_doi(d) for d in dois if d], 20):
        try:
            response = session.get("https://api.crossref.org/works",
                                   params={"filter": ",".join(f"doi:{d}" for d in chunk), "select": "DOI,relation",
                                           "rows": len(chunk)}, headers=headers, timeout=timeout)
            items = (response.json() or {}).get("message", {}).get("items", []) if response.status_code == 200 else []
        except Exception:
            items = []
        for item in items:
            found = []
            for kind in kinds:
                for rel in (item.get("relation") or {}).get(kind) or []:
                    if rel.get("id-type") == "doi" and rel.get("id"):
                        found.append(normalize_doi(rel["id"]))
            if found:
                related[normalize_doi(item.get("DOI"))] = found
    return related


def europepmc_fulltext_urls(session, dois: List[str], headers: Dict, timeout: int = 30) -> Dict[str, List[str]]:
    """Open/free PDF links Europe PMC lists for each DOI (batched OR query, 20 DOIs per call)."""
    urls: Dict[str, List[str]] = {}
    for chunk in _chunks([normalize_doi(d) for d in dois if d], 20):
        query = " OR ".join(f'DOI:"{d}"' for d in chunk)
        try:
            response = session.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                                   params={"query": query, "resultType": "core", "format": "json", "pageSize": 100},
                                   headers=headers, timeout=timeout)
            results = (response.json() or {}).get("resultList", {}).get("result", []) if response.status_code == 200 else []
        except Exception:
            results = []
        for result in results:
            doi = normalize_doi(result.get("doi"))
            for link in (result.get("fullTextUrlList") or {}).get("fullTextUrl") or []:
                if link.get("availability") in ("Open access", "Free") and link.get("documentStyle") == "pdf":
                    urls.setdefault(doi, []).append(link.get("url"))
    return urls


def arxiv_title_matches(session, titles: List[str], headers: Dict, sleep_func=time.sleep,
                        timeout: int = 30, per_call: int = 8) -> Dict[str, Dict]:
    """{title: {"id", "authors"}} for arXiv entries with the same title (1 request / 3 s, batched)."""
    found: Dict[str, Dict] = {}
    usable = [t for t in titles if len(_norm_title(t)) >= 20]
    for index, chunk in enumerate(_chunks(usable, per_call)):
        if index:
            sleep_func(3.1)
        query = " OR ".join('ti:"%s"' % re.sub(r"[^\w\s-]", " ", t).strip()[:180] for t in chunk)
        try:
            response = session.get("https://export.arxiv.org/api/query",
                                   params={"search_query": query, "max_results": per_call * 3},
                                   headers=headers, timeout=timeout)
            text = response.text if response.status_code == 200 else ""
        except Exception:
            text = ""
        for entry in re.findall(r"<entry>(.*?)</entry>", text, flags=re.DOTALL):
            id_match = re.search(r"<id>https?://arxiv\.org/abs/([^<]+)</id>", entry)
            title_match = re.search(r"<title>(.*?)</title>", entry, flags=re.DOTALL)
            if not id_match or not title_match:
                continue
            authors = [re.sub(r"\s+", " ", name).strip() for name in re.findall(r"<name>(.*?)</name>", entry, flags=re.DOTALL)]
            for title in chunk:
                if title not in found and titles_match(title, re.sub(r"\s+", " ", title_match.group(1))):
                    found[title] = {"id": id_match.group(1), "authors": authors}
    return found


def openalex_title_versions(session, titles: List[str], headers: Dict, email: str = "", api_key: str = "",
                            timeout: int = 30, per_call: int = 10, min_remaining: int = 150) -> Dict[str, List[Dict]]:
    """Other OpenAlex works with the same title (preprint, repository or conference versions).

    Keyless OpenAlex use is metered per day, so the lookup stops while budget remains.
    """
    versions: Dict[str, List[Dict]] = {}
    usable = [t for t in titles if len(_norm_title(t)) >= 20]
    for chunk in _chunks(usable, per_call):
        params = {"filter": "title.search:" + "|".join(re.sub(r"[,|:]", " ", t)[:200] for t in chunk),
                  "select": "id,doi,title,type,best_oa_location,locations,authorships", "per-page": 50}
        if email:
            params["mailto"] = email
        if api_key:
            params["api_key"] = api_key
        try:
            response = session.get("https://api.openalex.org/works", params=params, headers=headers, timeout=timeout)
        except Exception:
            continue
        if response.status_code != 200:
            break  # includes 429 once the keyless daily budget is spent
        for work in (response.json() or {}).get("results", []):
            for title in chunk:
                if titles_match(title, work.get("title") or ""):
                    versions.setdefault(title, []).append(work)
        try:
            if int(response.headers.get("x-ratelimit-remaining", "1000")) < min_remaining:
                break
        except ValueError:
            pass
    return versions


def surnames(names: Iterable[str]) -> set:
    """Lower-case ASCII-folded surnames ("First Last" or "Last, First")."""
    import unicodedata

    result = set()
    for name in names or []:
        name = str(name or "")
        surname = name.split(",")[0] if "," in name else (name.split() or [""])[-1]
        folded = unicodedata.normalize("NFKD", surname).encode("ascii", "ignore").decode().lower()
        folded = re.sub(r"[^a-z]", "", folded)
        if len(folded) >= 2:
            result.add(folded)
    return result
