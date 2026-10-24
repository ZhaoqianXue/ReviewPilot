"""
arXiv search module using arXiv API
Free to use, no key required
Supports real-time saving and resume capability
"""

import requests
import xml.etree.ElementTree as ET
import time
import re
import json
import os
from typing import List, Dict, Optional

from searchers.http_retry import TransientSourceError, get_with_retry
from searchers.arxiv_transport import ARXIV_API_URL, arxiv_get, read_object, utc_day

ARXIV_EPOCH = "1991-01-01"


class ArxivSearcher:
    BASE_URL = ARXIV_API_URL
    # Network/5xx failures retain bounded retries. A 429 or server cooldown is
    # persisted by the shared transport and deferred to a later collection run.
    RETRY_DELAYS = (5, 20)
    REQUEST_TIMEOUT = (10, 30)

    def __init__(self):
        """Initialize arXiv searcher."""
        self._last_status_code = None
        self.last_query = ""
        self.partial_records = []
        self.progress = {}

    def search(self, query: str, max_results: int = 100,
               categories: Optional[List[str]] = None,
               output_file: Optional[str] = None, date_range: Optional[Dict] = None) -> List[Dict]:
        """
        Search arXiv and return article metadata.

        Args:
            query: Search term (supports boolean AND/OR)
            max_results: Maximum number of results to return
            categories: Optional list of arXiv categories (e.g., ['cs.LG', 'cs.AI'])
            output_file: Optional path to save results incrementally (JSONL format)

        Returns:
            List of article dictionaries
        """
        from reviewpilot_core.publication_dates import resolve_range
        self._date_range = resolve_range(date_range) if date_range is not None else None

        # One native Boolean query: what is sent is exactly what is recorded.
        return self._search_simple(query, max_results, categories, output_file=output_file)

    def _search_simple(self, query: str, max_results: int,
                      categories: Optional[List[str]] = None,
                      output_file: Optional[str] = None) -> List[Dict]:
        """Standard search for simple queries. Saves incrementally if output_file is provided."""
        articles = []
        start = 0
        max_per_request = 100
        seen_ids = set()
        self._last_status_code = None

        search_query = self._format_query(query)

        if categories:
            cat_query = " OR ".join([f"cat:{cat}" for cat in categories])
            search_query = f"({search_query}) AND ({cat_query})"

        bounds = getattr(self, '_date_range', None)
        if bounds:
            # arXiv answers HTTP 500 to years before 1000 (e.g. 00010101), so an open
            # start uses 1991-01-01: arXiv holds nothing submitted before August 1991.
            start_date = (bounds['start'] or ARXIV_EPOCH).replace('-', '')
            end_date = bounds['end'].replace('-', '')
            search_query = f"({search_query}) AND submittedDate:[{start_date}0000 TO {end_date}2359]"

        self.last_query = search_query
        print(f"  arXiv query: {search_query[:80]}...")

        from pathlib import Path
        from reviewpilot_core.atomic_files import atomic_write_json, atomic_write_jsonl
        checkpoint = Path(str(output_file) + ".checkpoint.json") if output_file else None
        identity = {"query": search_query, "limit": max_results, "page_size": max_per_request,
                    "sortBy": "relevance", "sortOrder": "descending"}
        saved = read_object(checkpoint) if checkpoint else {}
        if (saved.get("identity") == identity and isinstance(saved.get("records"), list)
                and all(isinstance(row, dict) and row.get("id") for row in saved["records"])
                and isinstance(saved.get("next_start"), int) and saved["next_start"] >= 0):
            articles = saved["records"][:max_results]
            start = saved.get("next_start", 0)
            seen_ids = {self._record_key(row) for row in articles}
        else:
            saved = {}
        self.partial_records = articles
        self.progress = {"records": len(articles), "limit": max_results, "next_start": start,
                         "complete": False, "snapshot_day": saved.get("day", utc_day())}
        retained_count = len(articles) if saved and saved.get("day") != utc_day() else 0
        revalidating = bool(saved and saved.get("day") != utc_day())

        def persist(complete=False, last_page=None):
            nonlocal revalidating
            # Across a new day, rebuild the original ranked prefix. Until it is
            # revalidated, retain the prior snapshot if another 429 interrupts us.
            # Successful refreshed pages are already durable in the daily cache.
            if revalidating and len(articles) < retained_count and not complete:
                return
            revalidating = False
            self.partial_records = articles[:max_results]
            self.progress.update(records=len(self.partial_records), next_start=start, complete=complete,
                                 snapshot_day=utc_day(), revalidating=False)
            if checkpoint:
                atomic_write_json(checkpoint, {"identity": identity, "records": self.partial_records,
                                  "next_start": start, "complete": complete, "day": utc_day(),
                                  "last_page": last_page or saved.get("last_page")})
                atomic_write_jsonl(output_file, self.partial_records)

        # Refresh all saved pages after the daily snapshot expires. Checking
        # only the boundary could miss changes nearer the top of the ranking.
        if revalidating:
            if checkpoint:
                atomic_write_json(str(checkpoint) + ".previous", saved)
            articles, start, seen_ids = [], 0, set()
            self.progress["revalidating"] = True
        elif saved.get("complete"):
            self.progress["complete"] = True
            return articles[:max_results]

        while len(articles) < max_results:
            params = {
                "search_query": search_query,
                "start": start,
                "max_results": min(max_per_request, max_results - len(articles)),
                "sortBy": "relevance",
                "sortOrder": "descending"
            }

            # 429, 5xx, timeouts and dropped connections are retried with backoff
            # (see searchers/http_retry.py); anything else fails the source at once.
            new_articles = self._request_page(params)

            if not new_articles:
                persist(complete=True)
                break

            # Filter duplicates and save incrementally
            new_unique = []
            for article in new_articles:
                if self._record_key(article) not in seen_ids:
                    seen_ids.add(self._record_key(article))
                    new_unique.append(article)

            articles.extend(new_unique)
            start += len(new_articles)
            saved["last_page"] = {"params": params, "ids": [self._record_key(row) for row in new_articles]}
            persist(complete=len(articles) >= max_results, last_page=saved["last_page"])

            if new_unique:
                print(f"  Progress: {len(articles)} articles (saved {len(new_unique)} new)")

        return articles[:max_results]

    @staticmethod
    def _record_key(row):
        return re.sub(r"v\d+$", "", str(row.get("id") or ""))

    def _request_page(self, params):
        response = get_with_retry(self.BASE_URL, params=params, label="arXiv", delays=self.RETRY_DELAYS,
                                  timeout=self.REQUEST_TIMEOUT, requester=arxiv_get)
        self._last_status_code = response.status_code
        detail = self._api_error(response.text)
        if response.status_code >= 400:
            raise RuntimeError(f"arXiv rejected the query (HTTP {response.status_code})" + (f": {detail}" if detail else "."))
        if detail:
            raise RuntimeError(f"arXiv rejected the query: {detail}")
        return self._parse_response(response.text)

    def _format_query(self, query: str) -> str:
        """Render the Boolean query for the arXiv API, matching each term in titles or abstracts."""
        from reviewpilot_core.query_syntax import parse, render, phrase

        def term(value: str) -> str:
            if re.match(r'^(all|ti|abs|au|cat):', value):
                return value
            quoted = phrase(value)
            return f'(ti:{quoted} OR abs:{quoted})'
        return render(parse(query), term, negative='ANDNOT')

    @staticmethod
    def _api_error(xml_text: str) -> str:
        """arXiv reports errors as a feed whose single entry id points at /api/errors."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return ""
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            if "/api/errors" in (entry.findtext("atom:id", "", ns) or ""):
                return (entry.findtext("atom:summary", "", ns) or "").strip() or "unspecified API error"
        return ""

    def _parse_response(self, xml_text: str) -> List[Dict]:
        """Parse arXiv Atom feed response."""
        articles = []

        # Define namespaces
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom"
        }

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            raise TransientSourceError("arXiv returned an unreadable Atom feed; collection is incomplete.") from e
        if root.tag != "{http://www.w3.org/2005/Atom}feed":
            raise TransientSourceError("arXiv returned an unexpected response instead of an Atom feed; collection is incomplete.")

        for entry in root.findall("atom:entry", ns):
            try:
                # Get arXiv ID
                id_elem = entry.find("atom:id", ns)
                arxiv_url = id_elem.text if id_elem is not None else ""
                arxiv_id = arxiv_url.split("/abs/")[-1] if arxiv_url else ""

                # Title
                title_elem = entry.find("atom:title", ns)
                title = title_elem.text.strip().replace("\n", " ") if title_elem is not None else ""

                # Abstract
                summary_elem = entry.find("atom:summary", ns)
                abstract = summary_elem.text.strip().replace("\n", " ") if summary_elem is not None else ""

                # Authors
                authors = []
                for author in entry.findall("atom:author", ns):
                    name_elem = author.find("atom:name", ns)
                    if name_elem is not None and name_elem.text:
                        authors.append(name_elem.text)

                # Published date
                published_elem = entry.find("atom:published", ns)
                published = published_elem.text if published_elem is not None else ""
                year = published[:4] if published else ""

                # Categories
                categories = []
                for category in entry.findall("atom:category", ns):
                    term = category.get("term", "")
                    if term:
                        categories.append(term)

                # PDF link
                pdf_url = ""
                for link in entry.findall("atom:link", ns):
                    if link.get("title") == "pdf":
                        pdf_url = link.get("href", "")
                        break

                # DOI (if available)
                doi_elem = entry.find("arxiv:doi", ns)
                doi = doi_elem.text if doi_elem is not None else ""

                articles.append({
                    "source": "arxiv",
                    "id": arxiv_id,
                    "title": title,
                    "abstract": abstract,
                    "authors": authors,
                    "journal": "arXiv",
                    "year": year,
                    "publication_date": published,
                    "doi": doi,
                    "url": arxiv_url,
                    "pdf_url": pdf_url,
                    "categories": categories
                })

            except Exception as e:
                print(f"  Error parsing arXiv entry: {e}")
                continue

        return articles


def search(query: str, max_results: int = 100,
           categories: Optional[List[str]] = None,
           output_file: Optional[str] = None, date_range: Optional[Dict] = None) -> List[Dict]:
    """
    Convenience function to search arXiv.

    Args:
        query: Search term
        max_results: Maximum results to return
        categories: Optional arXiv categories (e.g., ['cs.LG', 'cs.AI'])
        output_file: Optional path to save results incrementally (JSONL format)

    Returns:
        List of article dictionaries
    """
    searcher = ArxivSearcher()
    return searcher.search(query, max_results, categories, output_file, date_range=date_range)


if __name__ == "__main__":
    # Test with a boolean query
    print("Testing arXiv search...")
    results = search('("large language model" OR LLM) AND ("rare disease")', max_results=5)
    print(f"Found {len(results)} results")
    for r in results:
        print(f"- {r['title'][:70]}... ({r['year']})")
