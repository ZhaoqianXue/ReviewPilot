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

try:
    import config
except ImportError:
    config = None


class OpenAlexSearchError(RuntimeError):
    """Raised when OpenAlex returns a platform-level API error."""


class OpenAlexSearcher:
    BASE_URL = "https://api.openalex.org/works"

    def __init__(self, email: Optional[str] = None, api_key: Optional[str] = None):
        """
        Initialize OpenAlex searcher.

        Args:
            email: Optional email for polite pool (faster rate limits)
            api_key: Optional OpenAlex API key for authenticated search
        """
        self.email = email
        self.api_key = api_key or os.getenv("OPENALEX_API_KEY") or (getattr(config, "OPENALEX_API_KEY", None) if config else None)
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

                try:
                    response = requests.get(
                        self.BASE_URL,
                        params=params,
                        timeout=60,
                        headers={"User-Agent": f"ReviewPilot/0.1 (mailto:{self.email})" if self.email else "ReviewPilot/0.1"},
                    )
                    response.raise_for_status()
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

                except requests.exceptions.Timeout:
                    print(f"  Timeout at {len(articles)} articles, retrying in 5s...")
                    time.sleep(5)
                    continue
                except requests.exceptions.HTTPError as e:
                    self.last_error = self._http_error_message(e)
                    print(f"  OpenAlex API error: {self.last_error}")
                    if not articles and self._is_platform_unavailable_error(self.last_error):
                        raise OpenAlexSearchError(self.last_error)
                    break
                except requests.exceptions.ConnectionError:
                    self.last_error = f"Connection error at {len(articles)} articles"
                    print(f"  {self.last_error}, retrying in 5s...")
                    time.sleep(5)
                    continue

            if len(articles) >= max_results:
                break

        if not articles and self.last_error:
            raise OpenAlexSearchError(self.last_error)
        return articles[:max_results]

    def _is_platform_unavailable_error(self, message: str) -> bool:
        normalized = str(message or "").lower()
        return (
            "rate-limit" in normalized
            or "rate limited" in normalized
            or "temporarily unavailable" in normalized
            or "please use a free api key" in normalized
        )

    def _http_error_message(self, exc: requests.exceptions.HTTPError) -> str:
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                payload = response.json()
                message = payload.get("message") or payload.get("error")
                if message:
                    return str(message)
            except ValueError:
                text = getattr(response, "text", "")
                if text:
                    return text[:300]
        return str(exc)

    def _build_query_param_sets(self, query: str) -> List[Dict[str, str]]:
        # Native search supports Boolean operators, parentheses and exact phrases.
        # Sending a cleaned bag of words silently changes the approved scope.
        from reviewpilot_core.query_syntax import parse, render, phrase
        return [{'search': render(parse(query), phrase)}]

    def _parse_boolean_groups(self, query: str) -> List[List[str]]:
        groups = []
        for part in re.split(r'\bAND\b', query or "", flags=re.IGNORECASE):
            terms = []
            for term in re.split(r'\bOR\b', part.strip().strip("()"), flags=re.IGNORECASE):
                cleaned = self._clean_query_text(term)
                if cleaned and cleaned.lower() not in {"and", "or"}:
                    terms.append(cleaned)
            if terms:
                groups.append(terms)
        return groups

    def _clean_query_text(self, text: str) -> str:
        text = (text or "").replace("*", " ")
        text = text.replace("'", " ").replace('"', " ")
        text = re.sub(r"[()]", " ", text)
        text = re.sub(r"\b(AND|OR)\b", " ", text, flags=re.IGNORECASE)
        return " ".join(text.split())

    def _convert_to_openalex_format(self, query: str) -> str:
        """
        Convert boolean query to OpenAlex filter format.
        OpenAlex uses title_and_abstract.search with | for OR.
        """
        import re

        # Clean up the query
        query = query.replace("'", "").replace('"', "")

        # Check if it's a complex boolean query
        if ' AND ' not in query.upper():
            return f"title_and_abstract.search:{query}"

        # Split by AND
        and_parts = re.split(r'\bAND\b', query, flags=re.IGNORECASE)

        filter_parts = []
        for part in and_parts:
            part = part.strip().strip('()')
            # Replace OR with |
            part = re.sub(r'\bOR\b', '|', part, flags=re.IGNORECASE)
            # Clean up extra spaces
            part = ' '.join(part.split())
            filter_parts.append(f"title_and_abstract.search:{part}")

        # Join with comma (AND in OpenAlex filter syntax)
        return ",".join(filter_parts)

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
