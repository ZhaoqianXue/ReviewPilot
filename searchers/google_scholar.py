"""
Google Scholar search module using scholarly library
Note: Google Scholar has no official API, this uses web scraping
May be rate-limited or blocked with heavy usage
"""

import time
from typing import List, Dict, Optional

try:
    from scholarly import scholarly, ProxyGenerator
    SCHOLARLY_AVAILABLE = True
except ImportError:
    SCHOLARLY_AVAILABLE = False


class GoogleScholarSearcher:
    def __init__(self, use_proxy: bool = False):
        """
        Initialize Google Scholar searcher.

        Args:
            use_proxy: Whether to use free proxies (slower but avoids blocks)
        """
        self.use_proxy = use_proxy

        if SCHOLARLY_AVAILABLE and use_proxy:
            try:
                pg = ProxyGenerator()
                pg.FreeProxies()
                scholarly.use_proxy(pg)
            except Exception as e:
                print(f"Warning: Could not set up proxy: {e}")

    def search(self, query: str, max_results: int = 100) -> List[Dict]:
        """
        Search Google Scholar and return article metadata.

        Args:
            query: Search term
            max_results: Maximum number of results to return

        Returns:
            List of article dictionaries
        """
        if not SCHOLARLY_AVAILABLE:
            print("Warning: scholarly library not installed. Run: pip install scholarly")
            return []

        articles = []

        try:
            search_query = scholarly.search_pubs(query)

            for i, result in enumerate(search_query):
                if i >= max_results:
                    break

                article = self._parse_result(result)
                if article:
                    articles.append(article)

                # Rate limiting to avoid blocks
                time.sleep(1.5)

        except Exception as e:
            print(f"Google Scholar error: {e}")

        return articles

    def _parse_result(self, result: Dict) -> Optional[Dict]:
        """Parse a scholarly result into standard format."""
        try:
            bib = result.get("bib", {})

            # Extract authors
            authors = bib.get("author", [])
            if isinstance(authors, str):
                authors = [a.strip() for a in authors.split(" and ")]

            return {
                "source": "google_scholar",
                "id": result.get("author_id", [""])[0] if result.get("author_id") else "",
                "title": bib.get("title", ""),
                "abstract": bib.get("abstract", ""),
                "authors": authors,
                "journal": bib.get("venue", "") or bib.get("journal", ""),
                "year": str(bib.get("pub_year", "")),
                "doi": "",  # Not directly available
                "url": result.get("pub_url", "") or result.get("eprint_url", ""),
                "citations": result.get("num_citations", 0)
            }
        except Exception as e:
            print(f"Error parsing Google Scholar result: {e}")
            return None


def search(query: str, max_results: int = 100, use_proxy: bool = False) -> List[Dict]:
    """
    Convenience function to search Google Scholar.

    Args:
        query: Search term
        max_results: Maximum results to return
        use_proxy: Whether to use proxies

    Returns:
        List of article dictionaries
    """
    searcher = GoogleScholarSearcher(use_proxy=use_proxy)
    return searcher.search(query, max_results)


if __name__ == "__main__":
    if SCHOLARLY_AVAILABLE:
        results = search("machine learning healthcare", max_results=5)
        for r in results:
            print(f"- {r['title'][:80]}... ({r['year']}) [Citations: {r['citations']}]")
    else:
        print("Install scholarly: pip install scholarly")
