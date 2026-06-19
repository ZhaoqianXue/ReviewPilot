"""
Web of Science search module using WoS Starter API
Requires API key from Clarivate
"""

import requests
import time
from typing import List, Dict, Optional


class WoSSearcher:
    BASE_URL = "https://api.clarivate.com/apis/wos-starter/v1"

    def __init__(self, api_key: str):
        """
        Initialize Web of Science searcher.

        Args:
            api_key: Clarivate API key (get from https://developer.clarivate.com/)
        """
        self.api_key = api_key
        self.headers = {
            "X-ApiKey": api_key,
            "Accept": "application/json"
        }

    def search(self, query: str, max_results: int = 100) -> List[Dict]:
        """
        Search Web of Science and return article metadata.

        Args:
            query: Search term
            max_results: Maximum number of results to return

        Returns:
            List of article dictionaries
        """
        articles = []
        limit = min(50, max_results)  # API limit per request
        offset = 1

        while len(articles) < max_results:
            params = {
                "q": query,
                "limit": limit,
                "page": offset
            }

            try:
                response = requests.get(
                    f"{self.BASE_URL}/documents",
                    headers=self.headers,
                    params=params
                )
                response.raise_for_status()
                data = response.json()

                hits = data.get("hits", [])
                if not hits:
                    break

                for hit in hits:
                    article = self._parse_record(hit)
                    if article:
                        articles.append(article)

                if len(hits) < limit:
                    break

                offset += 1
                time.sleep(0.5)  # Rate limiting

            except requests.exceptions.HTTPError as e:
                print(f"WoS API error: {e}")
                break

        return articles[:max_results]

    def _parse_record(self, record: Dict) -> Optional[Dict]:
        """Parse a WoS record into standard format."""
        try:
            # Extract authors
            authors = []
            names = record.get("names", {}).get("authors", [])
            for author in names:
                if isinstance(author, dict):
                    authors.append(author.get("wosStandard", author.get("displayName", "")))
                else:
                    authors.append(str(author))

            # Extract identifiers
            identifiers = record.get("identifiers", {})
            doi = identifiers.get("doi", "")
            uid = record.get("uid", "")

            # Extract source info
            source = record.get("source", {})

            return {
                "source": "wos",
                "id": uid,
                "title": record.get("title", ""),
                "abstract": record.get("abstract", ""),
                "authors": authors,
                "journal": source.get("sourceTitle", ""),
                "year": source.get("publishYear", ""),
                "doi": doi,
                "url": f"https://www.webofscience.com/wos/woscc/full-record/{uid}" if uid else ""
            }
        except Exception as e:
            print(f"Error parsing WoS record: {e}")
            return None


def search(query: str, max_results: int = 100, api_key: Optional[str] = None) -> List[Dict]:
    """
    Convenience function to search Web of Science.

    Args:
        query: Search term
        max_results: Maximum results to return
        api_key: Clarivate API key

    Returns:
        List of article dictionaries
    """
    if not api_key:
        print("Warning: WoS API key required. Get one from https://developer.clarivate.com/")
        return []

    searcher = WoSSearcher(api_key=api_key)
    return searcher.search(query, max_results)


if __name__ == "__main__":
    import os
    api_key = os.getenv("WOS_API_KEY")
    if api_key:
        results = search("machine learning healthcare", max_results=5, api_key=api_key)
        for r in results:
            print(f"- {r['title'][:80]}... ({r['year']})")
    else:
        print("Set WOS_API_KEY environment variable to test")
