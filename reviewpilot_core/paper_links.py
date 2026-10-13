"""Links a reader can follow to a paper's record: DOI, publisher or arXiv page, PubMed, PMC."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlparse

# Index and resolver hosts already covered by their own link; a URL on one of them is not the publisher page.
_INDEX_HOSTS = ("pubmed.ncbi.nlm.nih.gov", "openalex.org", "api.openalex.org", "doi.org", "dx.doi.org",
                "www.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov", "europepmc.org", "www.semanticscholar.org")


def doi(row: dict[str, Any]) -> str:
    value = str(row.get("doi") or "").strip().lower()
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value)
    value = re.sub(r"^doi:\s*", "", value).strip()
    return value if value.startswith("10.") and "/" in value else ""


def pmid(row: dict[str, Any]) -> str:
    for value in (row.get("pmid"), row.get("id") if row.get("source") == "pubmed" else None):
        if isinstance(value, (str, int)) and re.fullmatch(r"\d{4,9}", str(value).strip()):
            return str(value).strip()
    match = re.match(r"^https?://pubmed\.ncbi\.nlm\.nih\.gov/(\d{4,9})/?$", str(row.get("url") or ""))
    return match.group(1) if match else ""


def pmcid(row: dict[str, Any]) -> str:
    match = re.fullmatch(r"(?:PMC)?(\d{3,10})", str(row.get("pmcid") or "").strip(), re.IGNORECASE)
    return f"PMC{match.group(1)}" if match else ""


def web_url(value: Any) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    return text if parsed.scheme in {"http", "https"} and parsed.hostname and not re.search(r"\s", text) else ""


def publisher_url(row: dict[str, Any]) -> str:
    """The paper's own landing page (publisher or arXiv), never an index or resolver page."""
    for key in ("landing_page_url", "url"):
        url = web_url(row.get(key))
        if url and (urlparse(url).hostname or "").lower() not in _INDEX_HOSTS:
            return url
    return ""


def paper_links(row: dict[str, Any]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    paper_doi = doi(row)
    if paper_doi:
        links.append({"kind": "doi", "label": "DOI", "url": f"https://doi.org/{quote(paper_doi, safe='/:;()._-<>[]')}"})
    url = publisher_url(row)
    if url:
        label = "arXiv page" if (urlparse(url).hostname or "").lower().endswith("arxiv.org") else "Publisher page"
        links.append({"kind": "publisher", "label": label, "url": url})
    paper_pmid = pmid(row)
    if paper_pmid:
        links.append({"kind": "pubmed", "label": "PubMed", "url": f"https://pubmed.ncbi.nlm.nih.gov/{paper_pmid}/"})
    paper_pmcid = pmcid(row)
    if paper_pmcid:
        links.append({"kind": "pmc", "label": "PMC", "url": f"https://pmc.ncbi.nlm.nih.gov/articles/{paper_pmcid}/"})
    if not links:
        # An OpenAlex-only record still has its index page to start from.
        url = web_url(row.get("url"))
        if url:
            links.append({"kind": "record", "label": "Record page", "url": url})
    return links
