"""
OpenAlex search module
Free API, no key required (but email recommended for polite pool)
Supports real-time saving and resume capability
"""

import requests
import time
import json
import os
import itertools
import re
from typing import List, Dict, Optional

from searchers.http_retry import get_with_retry, shorten_message

try:
    import config
except ImportError:
    config = None


class OpenAlexSearchError(RuntimeError):
    """Raised when OpenAlex returns a platform-level API error."""


class OpenAlexSearcher:
    BASE_URL = "https://api.openalex.org/works"
    # OpenAlex rate-limits anonymous search under load (HTTP 429/503). Three
    # attempts with 2 s and 5 s waits; Retry-After is honoured up to 60 s.
    RETRY_DELAYS = (2, 5)
    REQUEST_TIMEOUT = (10, 60)

    def __init__(self, email: Optional[str] = None, api_key: Optional[str] = None):
        """
        Initialize OpenAlex searcher.

        Args:
            email: Optional email for polite pool (faster rate limits)
            api_key: Optional OpenAlex API key for authenticated search
        """
        self.email = email
        self.api_key = api_key or os.getenv("OPENALEX_API_KEY") or (getattr(config, "OPENALEX_API_KEY", None) if config else None)
        self.last_query = ""
        self.last_error = ""

    def search(self, query: str, max_results: int = 100,
               output_file: Optional[str] = None, date_range: Optional[Dict] = None) -> List[Dict]:
        """
        Search OpenAlex and return article metadata.

        Args:
            query: Search term (supports boolean AND/OR syntax)
            max_results: Maximum number of results to return
            output_file: Optional path to save results incrementally (JSONL format)

        Returns:
            List of article dictionaries
        """
        from reviewpilot_core.publication_dates import resolve_range
        self._date_range = resolve_range(date_range) if date_range is not None else None

        # Load existing articles if output file exists (for resume)
        seen_ids = set()
        existing_articles = []
        if output_file and os.path.exists(output_file):
            with open(output_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        article = json.loads(line.strip())
                        seen_ids.add(article.get('id'))
                        existing_articles.append(article)
                    except:
                        pass
            print(f"  Resuming: loaded {len(existing_articles)} existing records")

        # If we already have enough articles, return early
        if len(existing_articles) >= max_results:
            return existing_articles[:max_results]

        query_param_sets = self._build_query_param_sets(query)

        if self._date_range:
            for params in query_param_sets:
                filters = [params.get('filter', ''), f"to_publication_date:{self._date_range['end']}"]
                if self._date_range['start']:
                    filters.append(f"from_publication_date:{self._date_range['start']}")
                params['filter'] = ','.join(part for part in filters if part)
        self.last_query = query_param_sets[0]['filter']
        articles = list(existing_articles)

        for base_params in query_param_sets:
            cursor = "*"
            while len(articles) < max_results:
                params = {
                    **base_params,
                    "per_page": min(200, max_results - len(articles)),
                    "cursor": cursor
                }

                if self.email:
                    params["mailto"] = self.email
                if self.api_key:
                    params["api_key"] = self.api_key

                # 429, 5xx, timeouts and dropped connections are retried with backoff
                # (see searchers/http_retry.py). Any other HTTP error fails the source,
                # even mid-pagination, so a truncated result is never reported as complete.
                response = get_with_retry(
                    self.BASE_URL,
                    params=params,
                    label="OpenAlex",
                    delays=self.RETRY_DELAYS,
                    timeout=self.REQUEST_TIMEOUT,
                    headers={"User-Agent": f"ReviewPilot/0.1 (mailto:{self.email})" if self.email else "ReviewPilot/0.1"},
                )
                try:
                    response.raise_for_status()
                except requests.exceptions.HTTPError as e:
                    self.last_error = self._http_error_message(e)
                    print(f"  OpenAlex API error: {self.last_error}")
                    raise OpenAlexSearchError(self.last_error) from e
                data = response.json()

                results = data.get("results", [])
                if not results:
                    break

                new_count = 0
                for result in results:
                    article = self._parse_result(result)
                    if article:
                        article_id = article.get('id')
                        if article_id and article_id not in seen_ids:
                            seen_ids.add(article_id)
                            articles.append(article)
                            new_count += 1

                            # Save immediately if output file is provided
                            if output_file:
                                with open(output_file, 'a', encoding='utf-8') as f:
                                    f.write(json.dumps(article, ensure_ascii=False) + '\n')

                if new_count > 0:
                    print(f"  Progress: {len(articles)} articles (saved {new_count} new)")

                # Get next cursor
                meta = data.get("meta", {})
                cursor = meta.get("next_cursor")
                if not cursor:
                    break

                time.sleep(0.1)  # Rate limiting

            if len(articles) >= max_results:
                break

        return articles[:max_results]

    def _http_error_message(self, exc: requests.exceptions.HTTPError) -> str:
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                payload = response.json()
                message = payload.get("message") or payload.get("error")
                if message:
                    return shorten_message(str(message))
            except ValueError:
                text = getattr(response, "text", "")
                if text:
                    return shorten_message(text)
        return str(exc)

    def _build_query_param_sets(self, query: str) -> List[Dict[str, str]]:
        """Boolean expression matched against titles and abstracts only.

        The title_and_abstract.search filter accepts AND/OR/NOT, parentheses and
        quoted phrases. Commas separate filters, so they are removed from terms.
        """
        from reviewpilot_core.query_syntax import parse, render, phrase
        expression = render(parse(query), lambda value: phrase(value.replace(',', ' ')))
        return [{'filter': f'title_and_abstract.search:{expression}'}]

    def _parse_result(self, result: Dict) -> Optional[Dict]:
        """Parse an OpenAlex result into standard format."""
        try:
            # Extract authors
            authors = []
            for authorship in result.get("authorships", []):
                author = authorship.get("author", {})
                name = author.get("display_name", "")
                if name:
                    authors.append(name)

            # Extract DOI
            doi = result.get("doi", "")
            if doi and doi.startswith("https://doi.org/"):
                doi = doi.replace("https://doi.org/", "")

            # Extract venue/journal
            primary_location = result.get("primary_location", {}) or {}
            source = primary_location.get("source", {}) or {}
            journal = source.get("display_name", "")

            # Extract year
            year = str(result.get("publication_year", ""))

            # Get OpenAlex ID
            openalex_id = result.get("id", "").replace("https://openalex.org/", "")

            return {
                "source": "openalex",
                "id": openalex_id,
                "title": result.get("title", "") or "",
                "abstract": self._reconstruct_abstract(result.get("abstract_inverted_index")),
                "authors": authors,
                "journal": journal,
                "year": year,
                "publication_date": result.get("publication_date", ""),
                "doi": doi,
                "url": result.get("id", ""),
                "citations": result.get("cited_by_count", 0),
                "open_access": result.get("open_access", {}).get("is_oa", False)
            }
        except Exception as e:
            print(f"Error parsing OpenAlex result: {e}")
            return None

    def _reconstruct_abstract(self, inverted_index: Optional[Dict]) -> str:
        """Reconstruct abstract from inverted index format."""
        if not inverted_index:
            return ""

        try:
            # Create position -> word mapping
            position_word = {}
            for word, positions in inverted_index.items():
                for pos in positions:
                    position_word[pos] = word

            # Reconstruct in order
            if position_word:
                max_pos = max(position_word.keys())
                words = [position_word.get(i, "") for i in range(max_pos + 1)]
                return " ".join(words)
        except Exception:
            pass

        return ""


def search(query: str, max_results: int = 100, email: Optional[str] = None,
           output_file: Optional[str] = None, api_key: Optional[str] = None, date_range: Optional[Dict] = None) -> List[Dict]:
    """
    Convenience function to search OpenAlex.

    Args:
        query: Search term
        max_results: Maximum results to return
        email: Optional email for polite pool
        output_file: Optional path to save results incrementally (JSONL format)
        api_key: Optional OpenAlex API key

    Returns:
        List of article dictionaries
    """
    searcher = OpenAlexSearcher(email=email, api_key=api_key)
    return searcher.search(query, max_results, output_file=output_file, date_range=date_range)


if __name__ == "__main__":
    results = search("machine learning healthcare", max_results=5)
    for r in results:
        print(f"- {r['title'][:80]}... ({r['year']}) [OA: {r['open_access']}]")
