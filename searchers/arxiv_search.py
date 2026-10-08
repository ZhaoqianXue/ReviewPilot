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


class ArxivSearcher:
    BASE_URL = "https://export.arxiv.org/api/query"

    def __init__(self):
        """Initialize arXiv searcher."""
        self._last_status_code = None
        self.last_query = ""

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
        network_failures = 0
        self._last_status_code = None

        # If output file exists, load existing IDs to avoid duplicates
        if output_file and os.path.exists(output_file):
            with open(output_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        article = json.loads(line.strip())
                        seen_ids.add(article.get('id'))
                        articles.append(article)
                    except:
                        pass
            print(f"  Resuming: loaded {len(articles)} existing records")
            start = len(articles)

        search_query = self._format_query(query)

        if categories:
            cat_query = " OR ".join([f"cat:{cat}" for cat in categories])
            search_query = f"({search_query}) AND ({cat_query})"

        bounds = getattr(self, '_date_range', None)
        if bounds:
            start_date = (bounds['start'] or '0001-01-01').replace('-', '')
            end_date = bounds['end'].replace('-', '')
            search_query = f"({search_query}) AND submittedDate:[{start_date}0000 TO {end_date}2359]"

        self.last_query = search_query
        print(f"  arXiv query: {search_query[:80]}...")

        while len(articles) < max_results:
            params = {
                "search_query": search_query,
                "start": start,
                "max_results": min(max_per_request, max_results - len(articles)),
                "sortBy": "relevance",
                "sortOrder": "descending"
            }

            try:
                response = requests.get(self.BASE_URL, params=params, timeout=30)
                self._last_status_code = response.status_code

                # Handle rate limiting (429) with retry
                if response.status_code == 429:
                    print(f"  Rate limited, waiting 5s before retry...")
                    time.sleep(5)
                    response = requests.get(self.BASE_URL, params=params, timeout=30)
                    self._last_status_code = response.status_code
                    if response.status_code == 429:
                        print(f"  Still rate limited, waiting 10s...")
                        time.sleep(10)
                        response = requests.get(self.BASE_URL, params=params, timeout=30)
                        self._last_status_code = response.status_code

                # arXiv returns transient 5xx errors under load; retry before failing the source
                for delay in (3, 8):
                    if response.status_code < 500:
                        break
                    print(f"  arXiv HTTP {response.status_code}, retrying in {delay}s...")
                    time.sleep(delay)
                    response = requests.get(self.BASE_URL, params=params, timeout=30)
                    self._last_status_code = response.status_code

                response.raise_for_status()
                network_failures = 0

                new_articles = self._parse_response(response.text)

                if not new_articles:
                    break

                # Filter duplicates and save incrementally
                new_unique = []
                for article in new_articles:
                    if article.get('id') not in seen_ids:
                        seen_ids.add(article.get('id'))
                        new_unique.append(article)

                        # Save immediately if output file is provided
                        if output_file:
                            with open(output_file, 'a', encoding='utf-8') as f:
                                f.write(json.dumps(article, ensure_ascii=False) + '\n')

                articles.extend(new_unique)
                start += len(new_articles)

                if new_unique:
                    print(f"  Progress: {len(articles)} articles (saved {len(new_unique)} new)")

                # Rate limiting (arXiv recommends longer delays for bulk queries)
                time.sleep(3)

            except requests.exceptions.Timeout as exc:
                network_failures += 1
                if network_failures >= 3:
                    raise RuntimeError("arXiv search timed out after 3 attempts. Retry collection later.") from exc
                print(f"  Timeout at {len(articles)} articles, retrying in 10s...")
                time.sleep(10)
                continue
            except requests.exceptions.HTTPError as e:
                response = getattr(e, "response", None)
                if response is not None:
                    self._last_status_code = response.status_code
                raise RuntimeError(f"arXiv search failed (HTTP {self._last_status_code}). Retry collection later.") from e
            except requests.exceptions.ConnectionError as e:
                network_failures += 1
                if network_failures >= 3:
                    raise RuntimeError("arXiv search connection failed after 3 attempts. Retry collection later.") from e
                print(f"  Connection error at {len(articles)} articles, retrying in 10s...")
                time.sleep(10)
                continue

        return articles[:max_results]

    def _format_query(self, query: str) -> str:
        """Render the Boolean query for the arXiv API, matching each term in titles or abstracts."""
        from reviewpilot_core.query_syntax import parse, render, phrase

        def term(value: str) -> str:
            if re.match(r'^(all|ti|abs|au|cat):', value):
                return value
            quoted = phrase(value)
            return f'(ti:{quoted} OR abs:{quoted})'
        return render(parse(query), term, negative='ANDNOT')

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
            print(f"  XML parse error: {e}")
            return []

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
