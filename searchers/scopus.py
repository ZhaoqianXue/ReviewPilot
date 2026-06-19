"""
Scopus search module using Elsevier Scopus API
Requires API key from Elsevier Developer Portal
"""

import requests
import time
from typing import List, Dict, Optional


class ScopusSearcher:
    BASE_URL = "https://api.elsevier.com/content/search/scopus"

    def __init__(self, api_key: str):
        """
        Initialize Scopus searcher.

        Args:
            api_key: Elsevier API key (get from https://dev.elsevier.com/)
        """
        self.api_key = api_key
        self.headers = {
            "X-ELS-APIKey": api_key,
            "Accept": "application/json"
        }

    def search(self, query: str, max_results: int = 100) -> List[Dict]:
        """
        Search Scopus and return article metadata.

        Args:
            query: Search term
            max_results: Maximum number of results to return

        Returns:
            List of article dictionaries
        """
        # Convert query to Scopus format
        scopus_query = self._format_query(query)

        articles = []
        count = min(25, max_results)  # API limit per request
        start = 0

        while len(articles) < max_results:
            params = {
                "query": scopus_query,
                "count": count,
                "start": start,
                "view": "STANDARD"  # COMPLETE requires premium access
            }

            try:
                response = requests.get(
                    self.BASE_URL,
                    headers=self.headers,
                    params=params
                )
                response.raise_for_status()
                data = response.json()

                results = data.get("search-results", {})
                entries = results.get("entry", [])

                if not entries or (len(entries) == 1 and "error" in entries[0]):
                    break

                for entry in entries:
                    article = self._parse_entry(entry)
                    if article:
                        articles.append(article)

                total_results = int(results.get("opensearch:totalResults", 0))
                if start + count >= total_results:
                    break

                start += count
                time.sleep(0.2)  # Rate limiting

            except requests.exceptions.HTTPError as e:
                print(f"Scopus API error: {e}")
                break

        return articles[:max_results]

    def _format_query(self, query: str) -> str:
        """
        Format query for Scopus API.
        Converts our boolean query format to Scopus-compatible format.
        """
        import re

        # Check if it's already a simple query
        if ' AND ' not in query.upper() and ' OR ' not in query.upper():
            return f"TITLE-ABS-KEY({query})"

        # For complex boolean queries, we need to:
        # 1. Replace single quotes with double quotes
        # 2. Wrap each group properly with TITLE-ABS-KEY

        # Split by AND to get groups
        and_parts = re.split(r'\bAND\b', query, flags=re.IGNORECASE)

        formatted_groups = []
        for part in and_parts:
            part = part.strip().strip('()')
            # Replace single quotes with nothing (Scopus prefers unquoted or double-quoted)
            part = part.replace("'", '"')
            # Split by OR
            or_terms = re.split(r'\bOR\b', part, flags=re.IGNORECASE)
            or_terms = [t.strip() for t in or_terms if t.strip()]

            if or_terms:
                # Wrap each term in TITLE-ABS-KEY and join with OR
                formatted_terms = [f'TITLE-ABS-KEY({term})' for term in or_terms]
                group = f"({' OR '.join(formatted_terms)})"
                formatted_groups.append(group)

        # Join groups with AND
        return " AND ".join(formatted_groups)

    def _parse_entry(self, entry: Dict) -> Optional[Dict]:
        """Parse a Scopus entry into standard format."""
        try:
            # Check for error entries
            if "error" in entry:
                return None

            # Extract authors
            authors = []
            author_list = entry.get("author", [])
            if isinstance(author_list, list):
                for author in author_list:
                    if isinstance(author, dict):
                        authors.append(author.get("authname", ""))
            elif isinstance(author_list, dict):
                authors.append(author_list.get("authname", ""))

            # Get DOI
            doi = entry.get("prism:doi", "")

            # Get Scopus ID
            scopus_id = entry.get("dc:identifier", "").replace("SCOPUS_ID:", "")

            return {
                "source": "scopus",
                "id": scopus_id,
                "title": entry.get("dc:title", ""),
                "abstract": entry.get("dc:description", ""),
                "authors": authors,
                "journal": entry.get("prism:publicationName", ""),
                "year": entry.get("prism:coverDate", "")[:4] if entry.get("prism:coverDate") else "",
                "doi": doi,
                "url": entry.get("prism:url", ""),
                "citations": entry.get("citedby-count", "0")
            }
        except Exception as e:
            print(f"Error parsing Scopus entry: {e}")
            return None


def search(query: str, max_results: int = 100, api_key: Optional[str] = None) -> List[Dict]:
    """
    Convenience function to search Scopus.

    Args:
        query: Search term
        max_results: Maximum results to return
        api_key: Elsevier API key

    Returns:
        List of article dictionaries
    """
    if not api_key:
        print("Warning: Scopus API key required. Get one from https://dev.elsevier.com/")
        return []

    searcher = ScopusSearcher(api_key=api_key)
    return searcher.search(query, max_results)


if __name__ == "__main__":
    import os
    api_key = os.getenv("SCOPUS_API_KEY")
    if api_key:
        results = search("machine learning healthcare", max_results=5, api_key=api_key)
        for r in results:
            print(f"- {r['title'][:80]}... ({r['year']})")
    else:
        print("Set SCOPUS_API_KEY environment variable to test")
