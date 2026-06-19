"""
DBLP search module - Computer Science bibliography database
Covers top CS venues: NeurIPS, ICML, ICLR, CVPR, ACL, EMNLP, AAAI, IJCAI, etc.
Free to use, no key required
"""

import requests
import time
from typing import List, Dict, Optional


# Top CS conference/venue identifiers in DBLP
TOP_CS_VENUES = {
    # Machine Learning & AI
    "neurips": "conf/nips",
    "icml": "conf/icml",
    "iclr": "conf/iclr",
    "aaai": "conf/aaai",
    "ijcai": "conf/ijcai",
    # Computer Vision
    "cvpr": "conf/cvpr",
    "iccv": "conf/iccv",
    "eccv": "conf/eccv",
    # NLP
    "acl": "conf/acl",
    "emnlp": "conf/emnlp",
    "naacl": "conf/naacl",
    # Systems
    "osdi": "conf/osdi",
    "sosp": "conf/sosp",
    "nsdi": "conf/nsdi",
    # Databases
    "sigmod": "conf/sigmod",
    "vldb": "conf/vldb",
    # Theory
    "stoc": "conf/stoc",
    "focs": "conf/focs",
    # Security
    "ccs": "conf/ccs",
    "usenix_security": "conf/uss",
    "sp": "conf/sp",
    # HCI
    "chi": "conf/chi",
    "uist": "conf/uist",
    # Web & Data Mining
    "www": "conf/www",
    "kdd": "conf/kdd",
    "wsdm": "conf/wsdm",
    # Robotics
    "icra": "conf/icra",
    "iros": "conf/iros",
    "rss": "conf/rss",
}


class DBLPSearcher:
    # Use dblp.uni-trier.de as primary (more reliable than dblp.org)
    BASE_URL = "https://dblp.uni-trier.de/search/publ/api"
    VENUE_URL = "https://dblp.uni-trier.de/search/venue/api"

    def __init__(self):
        """Initialize CS Conferences searcher."""
        pass

    def search(self, query: str, max_results: int = 100,
               venues: Optional[List[str]] = None) -> List[Dict]:
        """
        Search DBLP for CS conference papers.

        Args:
            query: Search term
            max_results: Maximum number of results to return
            venues: Optional list of venue keys from TOP_CS_VENUES

        Returns:
            List of article dictionaries
        """
        # Simplify query for DBLP - it doesn't support complex boolean syntax
        simplified_query = self._simplify_query(query)

        # If specific venues requested, search with venue filter
        if venues:
            venue_query = self._build_venue_query(simplified_query, venues)
        else:
            venue_query = simplified_query

        articles = []
        hits_per_page = 100
        first = 0

        while len(articles) < max_results:
            params = {
                "q": venue_query,
                "format": "json",
                "h": hits_per_page,
                "f": first
            }

            try:
                # Add headers to avoid being blocked
                headers = {
                    'User-Agent': 'DataScholar/1.0 (academic research tool; contact@example.com)'
                }
                response = requests.get(self.BASE_URL, params=params, headers=headers, timeout=30)

                # Handle 500 errors - DBLP API may be temporarily unavailable
                if response.status_code == 500:
                    print("  Note: DBLP API may be temporarily unavailable")
                    break

                response.raise_for_status()
                data = response.json()

                result = data.get("result", {})
                hits = result.get("hits", {})
                hit_list = hits.get("hit", [])

                if not hit_list:
                    break

                for hit in hit_list:
                    article = self._parse_hit(hit)
                    if article:
                        articles.append(article)

                total = int(hits.get("@total", 0))
                if first + hits_per_page >= total:
                    break

                first += hits_per_page
                time.sleep(0.5)  # Rate limiting

            except requests.exceptions.HTTPError as e:
                print(f"DBLP API error: {e}")
                break
            except requests.exceptions.Timeout:
                print(f"DBLP API timeout - try a simpler query")
                break
            except Exception as e:
                print(f"Error searching DBLP: {e}")
                break

        return articles[:max_results]

    def _simplify_query(self, query: str) -> str:
        """
        Simplify complex boolean queries for DBLP.
        DBLP works best with simple 1-2 word searches.
        """
        import re

        # Remove quotes and parentheses
        simplified = query.replace("'", "").replace('"', "")
        simplified = simplified.replace('(', '').replace(')', '')

        # Split by AND first
        and_parts = re.split(r'\bAND\b', simplified, flags=re.IGNORECASE)

        # Get the first meaningful term from each AND group
        key_terms = []
        for part in and_parts:
            # Split by OR and get first term
            or_terms = re.split(r'\bOR\b', part, flags=re.IGNORECASE)
            for term in or_terms:
                term = term.strip()
                # Skip common short words
                if term and len(term) > 2 and term.lower() not in ['the', 'and', 'for', 'llm', 'gpt']:
                    key_terms.append(term)
                    break

        # Use just 1-2 key terms for DBLP (it's very sensitive)
        if len(key_terms) >= 2:
            # Combine first two terms
            return f"{key_terms[0]} {key_terms[1]}"
        elif key_terms:
            return key_terms[0]
        else:
            # Fallback: extract first meaningful phrase
            words = simplified.split()
            meaningful = [w for w in words if len(w) > 3 and w.lower() not in ['and', 'the', 'for']]
            return meaningful[0] if meaningful else "machine learning"

    def _build_venue_query(self, query: str, venues: List[str]) -> str:
        """Build DBLP query with venue filters."""
        venue_filters = []
        for venue in venues:
            if venue.lower() in TOP_CS_VENUES:
                venue_filters.append(f"venue:{TOP_CS_VENUES[venue.lower()]}:")

        if venue_filters:
            venue_str = " | ".join(venue_filters)
            return f"{query} ({venue_str})"
        return query

    def _parse_hit(self, hit: Dict) -> Optional[Dict]:
        """Parse a DBLP hit into standard format."""
        try:
            info = hit.get("info", {})

            # Authors
            authors_data = info.get("authors", {}).get("author", [])
            if isinstance(authors_data, dict):
                authors_data = [authors_data]
            authors = []
            for author in authors_data:
                if isinstance(author, dict):
                    authors.append(author.get("text", ""))
                else:
                    authors.append(str(author))

            # Venue
            venue = info.get("venue", "")
            if isinstance(venue, list):
                venue = venue[0] if venue else ""

            return {
                "source": "dblp",
                "id": info.get("key", ""),
                "title": info.get("title", ""),
                "abstract": "",  # DBLP doesn't provide abstracts
                "authors": authors,
                "journal": venue,
                "year": info.get("year", ""),
                "doi": info.get("doi", ""),
                "url": info.get("url", ""),
                "venue_type": info.get("type", "")  # Conference Paper, Journal Article, etc.
            }
        except Exception as e:
            print(f"Error parsing DBLP hit: {e}")
            return None

    def get_available_venues(self) -> Dict[str, str]:
        """Return dictionary of available venue shortcuts."""
        return TOP_CS_VENUES.copy()


def search(query: str, max_results: int = 100,
           venues: Optional[List[str]] = None) -> List[Dict]:
    """
    Convenience function to search CS conferences via DBLP.

    Args:
        query: Search term
        max_results: Maximum results to return
        venues: Optional list of venue shortcuts (e.g., ['neurips', 'icml', 'iclr'])

    Returns:
        List of article dictionaries
    """
    searcher = CSConferencesSearcher()
    return searcher.search(query, max_results, venues)


def list_venues() -> Dict[str, str]:
    """List available venue shortcuts."""
    return TOP_CS_VENUES.copy()


if __name__ == "__main__":
    print("Available venues:", list(TOP_CS_VENUES.keys()))
    print("\nSearching NeurIPS, ICML, ICLR...")
    results = search("transformer attention", max_results=5, venues=["neurips", "icml", "iclr"])
    for r in results:
        print(f"- {r['title'][:70]}... ({r['year']}) [{r['journal']}]")
