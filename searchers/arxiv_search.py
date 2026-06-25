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
    BASE_URL = "http://export.arxiv.org/api/query"

    def __init__(self):
        """Initialize arXiv searcher."""
        self._last_status_code = None

    def search(self, query: str, max_results: int = 100,
               categories: Optional[List[str]] = None,
               output_file: Optional[str] = None) -> List[Dict]:
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
        # Check if query is complex and needs to be split
        parsed_groups = self._parse_query(query)

        if parsed_groups and len(parsed_groups) >= 2:
            # Try arXiv's native boolean syntax first. Expanding broad OR groups into
            # every AND combination can create hundreds of API calls and trigger 429s.
            native_results = self._search_simple(
                query,
                max_results,
                categories,
                output_file=output_file,
            )
            if len(native_results) >= max_results:
                return native_results[:max_results]
            if self._last_status_code == 429:
                print("  arXiv native query rate limited; skipping split fallback")
                return native_results[:max_results]

            split_results = self._search_split(parsed_groups, max_results, categories, output_file)
            merged = {}
            for article in native_results + split_results:
                article_id = article.get("id") or article.get("url") or article.get("title")
                if article_id and article_id not in merged:
                    merged[article_id] = article

            return list(merged.values())[:max_results]

        # Simple query - use standard approach
        return self._search_simple(query, max_results, categories, output_file=output_file)

    def _search_simple(self, query: str, max_results: int,
                      categories: Optional[List[str]] = None,
                      is_boolean_query: bool = False,
                      output_file: Optional[str] = None) -> List[Dict]:
        """Standard search for simple queries. Saves incrementally if output_file is provided."""
        articles = []
        start = 0
        max_per_request = 100
        seen_ids = set()
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

        # Build search query
        if is_boolean_query:
            search_query = query
        else:
            search_query = self._format_query(query)

        if categories:
            cat_query = " OR ".join([f"cat:{cat}" for cat in categories])
            search_query = f"({search_query}) AND ({cat_query})"

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

                response.raise_for_status()

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

            except requests.exceptions.Timeout:
                print(f"  Timeout at {len(articles)} articles, retrying in 10s...")
                time.sleep(10)
                continue
            except requests.exceptions.HTTPError as e:
                response = getattr(e, "response", None)
                if response is not None:
                    self._last_status_code = response.status_code
                print(f"  arXiv API error: {e}")
                break
            except requests.exceptions.ConnectionError as e:
                print(f"  Connection error at {len(articles)} articles, retrying in 10s...")
                time.sleep(10)
                continue

        return articles[:max_results]

    def _parse_query(self, query: str) -> Optional[tuple]:
        """Parse query into groups for complex boolean queries."""
        if not query or ' AND ' not in query.upper():
            return None

        and_parts = re.split(r'\bAND\b', query, flags=re.IGNORECASE)
        groups = []

        for part in and_parts:
            part = part.strip().strip('()')
            part = part.replace("'", "").replace('"', "")
            or_terms = re.split(r'\bOR\b', part, flags=re.IGNORECASE)
            terms = [t.strip() for t in or_terms if t.strip()]
            if terms:
                groups.append(terms)

        return tuple(groups) if groups else None

    def _search_split(self, groups: tuple, max_results: int,
                     categories: Optional[List[str]] = None,
                     output_file: Optional[str] = None) -> List[Dict]:
        """
        Search by splitting large queries into smaller batches.
        This considers ALL combinations but sends them in manageable chunks.
        Uses proper arXiv query syntax with all: prefix on each term.
        Saves incrementally if output_file is provided.
        """
        all_articles = {}  # Deduplicate by ID
        max_split_queries = self._max_split_queries()
        attempted_queries = 0

        # Load existing articles if output file exists
        if output_file and os.path.exists(output_file):
            with open(output_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        article = json.loads(line.strip())
                        all_articles[article.get('id')] = article
                    except:
                        pass
            print(f"  Resuming: loaded {len(all_articles)} existing records")

        def format_term(term: str) -> str:
            """Format a term for arXiv query - wrap multi-word terms in quotes."""
            term = term.strip()
            if ' ' in term:
                return f'all:"{term}"'
            return f'all:{term}'

        def save_article(article):
            """Save a single article to the output file."""
            if output_file:
                with open(output_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(article, ensure_ascii=False) + '\n')

        if len(groups) == 2:
            # Two-group query: search each combination individually
            group1 = groups[0]
            group2 = groups[1]

            for term1 in group1:
                for term2 in group2:
                    if len(all_articles) >= max_results or attempted_queries >= max_split_queries:
                        break

                    search_query = f'{format_term(term1)} AND {format_term(term2)}'
                    attempted_queries += 1
                    batch_articles = self._search_simple(search_query, max_results, categories, is_boolean_query=True)

                    for article in batch_articles:
                        article_id = article.get("id")
                        if article_id and article_id not in all_articles:
                            all_articles[article_id] = article
                            save_article(article)

                if len(all_articles) >= max_results or attempted_queries >= max_split_queries:
                    break

        elif len(groups) >= 3:
            # Three-group query: search each combination individually
            # Limit terms per group to avoid rate limiting (too many combinations)
            group1 = groups[0][:4]
            group2 = groups[1][:4]
            group3 = groups[2][:4]

            for term1 in group1:
                for term2 in group2:
                    for term3 in group3:
                        if len(all_articles) >= max_results or attempted_queries >= max_split_queries:
                            break

                        search_query = f'{format_term(term1)} AND {format_term(term2)} AND {format_term(term3)}'
                        attempted_queries += 1
                        batch_articles = self._search_simple(search_query, max_results, categories, is_boolean_query=True)

                        for article in batch_articles:
                            article_id = article.get("id")
                            if article_id and article_id not in all_articles:
                                all_articles[article_id] = article
                                save_article(article)

                    if len(all_articles) >= max_results or attempted_queries >= max_split_queries:
                        break
                if len(all_articles) >= max_results or attempted_queries >= max_split_queries:
                    break

        if attempted_queries >= max_split_queries and len(all_articles) < max_results:
            print(f"  arXiv split fallback stopped after {attempted_queries} queries")
        print(f"  Total unique articles: {len(all_articles)}")
        return list(all_articles.values())[:max_results]

    def _max_split_queries(self) -> int:
        """Maximum arXiv combination fallback requests after native boolean search."""
        try:
            return max(0, int(os.getenv("REVIEWPILOT_ARXIV_MAX_SPLIT_QUERIES", "6")))
        except ValueError:
            return 6

    def _format_query(self, query: str) -> str:
        """
        Format boolean query for arXiv API.

        arXiv API format:
        - Use all:term for searching all fields
        - Use ti:term for title, abs:term for abstract
        - AND, OR, ANDNOT for boolean operators
        - Quotes for phrases: all:"machine learning"
        """
        # First, check if it looks like it's already formatted for arXiv
        if 'all:' in query or 'ti:' in query or 'abs:' in query:
            return query

        # Parse the boolean query
        # Simple approach: replace quoted phrases with placeholders
        phrases = {}
        placeholder_idx = 0

        def replace_phrase(match):
            nonlocal placeholder_idx
            key = f"__PHRASE_{placeholder_idx}__"
            phrases[key] = match.group(1)
            placeholder_idx += 1
            return key

        # Extract quoted phrases
        temp_query = re.sub(r'"([^"]+)"', replace_phrase, query)
        temp_query = re.sub(r"'([^']+)'", replace_phrase, temp_query)

        # Remove remaining parentheses (grouping)
        temp_query = temp_query.replace('(', ' ').replace(')', ' ')
        temp_query = ' '.join(temp_query.split())

        # Check if it has boolean operators
        has_and = ' AND ' in temp_query.upper()
        has_or = ' OR ' in temp_query.upper()

        if not has_and and not has_or:
            # Simple query - restore phrases and format
            for key, phrase in phrases.items():
                temp_query = temp_query.replace(key, phrase)
            if ' ' in temp_query:
                return f'all:"{temp_query}"'
            return f'all:{temp_query}'

        # Split by AND (case insensitive)
        and_parts = re.split(r'\s+AND\s+', temp_query, flags=re.IGNORECASE)

        formatted_groups = []
        for part in and_parts:
            part = part.strip()
            if not part:
                continue

            # Split by OR within this group
            or_terms = re.split(r'\s+OR\s+', part, flags=re.IGNORECASE)
            formatted_terms = []

            for term in or_terms:
                term = term.strip()
                if not term:
                    continue

                # Restore any phrase placeholders
                for key, phrase in phrases.items():
                    if key in term:
                        term = term.replace(key, phrase)

                # Format the term
                if ' ' in term:
                    formatted_terms.append(f'all:"{term}"')
                else:
                    formatted_terms.append(f'all:{term}')

            if formatted_terms:
                if len(formatted_terms) == 1:
                    formatted_groups.append(formatted_terms[0])
                else:
                    formatted_groups.append(f"({' OR '.join(formatted_terms)})")

        if not formatted_groups:
            # Fallback
            for key, phrase in phrases.items():
                temp_query = temp_query.replace(key, phrase)
            return f'all:"{temp_query}"'
        elif len(formatted_groups) == 1:
            return formatted_groups[0]
        else:
            return ' AND '.join(formatted_groups)

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
           output_file: Optional[str] = None) -> List[Dict]:
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
    return searcher.search(query, max_results, categories, output_file)


if __name__ == "__main__":
    # Test with a boolean query
    print("Testing arXiv search...")
    results = search('("large language model" OR LLM) AND ("rare disease")', max_results=5)
    print(f"Found {len(results)} results")
    for r in results:
        print(f"- {r['title'][:70]}... ({r['year']})")
