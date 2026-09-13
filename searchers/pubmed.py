"""
PubMed search module using NCBI Entrez API
Free to use, requires email for identification
Supports real-time saving and resume capability
"""

import requests
import xml.etree.ElementTree as ET
import time
import json
import os
from typing import List, Dict, Optional


class PubMedSearcher:
    BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def __init__(self, email: str, api_key: Optional[str] = None):
        """
        Initialize PubMed searcher.

        Args:
            email: Required by NCBI for identification
            api_key: Optional API key for higher rate limits (10 req/sec vs 3 req/sec)
        """
        self.email = email
        self.api_key = api_key

    def search(self, query: str, max_results: int = 100, search_scope: str = "title_abstract",
               output_file: Optional[str] = None, date_range: Optional[Dict] = None) -> List[Dict]:
        """
        Search PubMed and return article metadata.

        Args:
            query: Search term
            max_results: Maximum number of results to return
            search_scope: "title_abstract" to restrict search to title/abstract only
            output_file: Optional path to save results incrementally (JSONL format)

        Returns:
            List of article dictionaries
        """
        from reviewpilot_core.publication_dates import resolve_range
        self._date_range = resolve_range(date_range) if date_range is not None else None

        # Load existing articles if output file exists (for resume)
        existing_ids = set()
        existing_articles = []
        if output_file and os.path.exists(output_file):
            with open(output_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        article = json.loads(line.strip())
                        existing_ids.add(article.get('id'))
                        existing_articles.append(article)
                    except:
                        pass
            print(f"  Resuming: loaded {len(existing_articles)} existing records")

        # Convert query to PubMed format with field restrictions
        if search_scope == "title_abstract":
            query = self._add_field_restrictions(query)

        # Step 1: Search for PMIDs
        pmids = self._search_pmids(query, max_results)

        if not pmids:
            return existing_articles

        # Filter out already fetched PMIDs
        new_pmids = [p for p in pmids if p not in existing_ids]
        if len(new_pmids) < len(pmids):
            print(f"  Skipping {len(pmids) - len(new_pmids)} already fetched articles")

        if not new_pmids:
            return existing_articles

        # Step 2: Fetch article details with real-time saving
        new_articles = self._fetch_details(new_pmids, output_file)

        return existing_articles + new_articles

    def _add_field_restrictions(self, query: str) -> str:
        """
        Add [Title/Abstract] field restrictions to query terms.
        Converts: ('LLM' OR 'GPT') AND ('judge')
        To: (LLM[Title/Abstract] OR GPT[Title/Abstract]) AND (judge[Title/Abstract])
        """
        from reviewpilot_core.query_syntax import parse, render, phrase
        import re
        return render(parse(query), lambda value: value if re.search(r'\[[^\[\]]+\]$', value) else phrase(value) + '[Title/Abstract]')

    def _search_pmids(self, query: str, max_results: int) -> List[str]:
        """Search for PMIDs matching the query."""
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": max_results,
            "retmode": "json",
            "email": self.email,
        }
        bounds = getattr(self, '_date_range', None)
        if bounds:
            params.update(datetype='pdat', mindate=(bounds['start'] or '0001-01-01').replace('-', '/'), maxdate=bounds['end'].replace('-', '/'))
        if self.api_key:
            params["api_key"] = self.api_key

        # Retry logic for server errors
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.get(f"{self.BASE_URL}/esearch.fcgi", params=params, timeout=60)
                response.raise_for_status()
                data = response.json()
                return data.get("esearchresult", {}).get("idlist", [])
            except requests.exceptions.HTTPError as e:
                if response.status_code >= 500 and attempt < max_retries - 1:
                    print(f"  PubMed server error (attempt {attempt + 1}/{max_retries}), retrying in 5s...")
                    time.sleep(5)
                    continue
                raise
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    print(f"  PubMed timeout (attempt {attempt + 1}/{max_retries}), retrying...")
                    time.sleep(3)
                    continue
                raise

        return []

    def _fetch_details(self, pmids: List[str], output_file: Optional[str] = None) -> List[Dict]:
        """Fetch article details for given PMIDs with real-time saving."""
        all_articles = []

        # Process in batches of 200
        batch_size = 200
        for i in range(0, len(pmids), batch_size):
            batch = pmids[i:i + batch_size]

            params = {
                "db": "pubmed",
                "id": ",".join(batch),
                "retmode": "xml",
                "email": self.email,
            }
            if self.api_key:
                params["api_key"] = self.api_key

            try:
                response = requests.get(f"{self.BASE_URL}/efetch.fcgi", params=params, timeout=60)
                response.raise_for_status()

                articles = self._parse_xml(response.text)

                # Save each article in real-time
                if output_file:
                    with open(output_file, 'a', encoding='utf-8') as f:
                        for article in articles:
                            f.write(json.dumps(article, ensure_ascii=False) + '\n')

                all_articles.extend(articles)
                print(f"  Progress: {len(all_articles)}/{len(pmids)} articles (batch {i//batch_size + 1})")

            except requests.exceptions.Timeout:
                print(f"  Timeout fetching batch {i//batch_size + 1}, retrying...")
                time.sleep(5)
                try:
                    response = requests.get(f"{self.BASE_URL}/efetch.fcgi", params=params, timeout=120)
                    response.raise_for_status()
                    articles = self._parse_xml(response.text)
                    if output_file:
                        with open(output_file, 'a', encoding='utf-8') as f:
                            for article in articles:
                                f.write(json.dumps(article, ensure_ascii=False) + '\n')
                    all_articles.extend(articles)
                except Exception as e:
                    print(f"  Retry failed: {e}")

            except Exception as e:
                print(f"  Error fetching batch {i//batch_size + 1}: {e}")

            # Rate limiting
            time.sleep(0.34 if self.api_key else 1)

        return all_articles

    def _parse_xml(self, xml_text: str) -> List[Dict]:
        """Parse PubMed XML response."""
        articles = []
        root = ET.fromstring(xml_text)

        for article in root.findall(".//PubmedArticle"):
            try:
                medline = article.find(".//MedlineCitation")
                pmid = medline.find(".//PMID").text if medline.find(".//PMID") is not None else ""

                article_elem = medline.find(".//Article")

                # Title
                title_elem = article_elem.find(".//ArticleTitle")
                title = "".join(title_elem.itertext()) if title_elem is not None else ""

                # Abstract
                abstract_elem = article_elem.find(".//Abstract/AbstractText")
                abstract = "".join(abstract_elem.itertext()) if abstract_elem is not None else ""

                # Authors
                authors = []
                for author in article_elem.findall(".//Author"):
                    lastname = author.find("LastName")
                    forename = author.find("ForeName")
                    if lastname is not None:
                        name = lastname.text or ""
                        if forename is not None and forename.text:
                            name = f"{forename.text} {name}"
                        authors.append(name)

                # Journal
                journal_elem = article_elem.find(".//Journal/Title")
                journal = journal_elem.text if journal_elem is not None else ""

                # Year
                year_elem = article_elem.find(".//Journal/JournalIssue/PubDate/Year")
                if year_elem is None:
                    year_elem = article_elem.find(".//Journal/JournalIssue/PubDate/MedlineDate")
                year = year_elem.text[:4] if year_elem is not None and year_elem.text else ""

                publication_date = year
                month = article_elem.findtext('.//Journal/JournalIssue/PubDate/Month') or ''
                day = article_elem.findtext('.//Journal/JournalIssue/PubDate/Day') or ''
                import calendar
                months = {v.lower(): i for i, v in enumerate(calendar.month_abbr) if v}
                month_number = int(month) if month.isdigit() else months.get(month[:3].lower())
                if year and month_number:
                    publication_date += f'-{month_number:02d}'
                    if day.isdigit():
                        publication_date += f'-{int(day):02d}'

                # DOI
                doi = ""
                for id_elem in article.findall(".//ArticleId"):
                    if id_elem.get("IdType") == "doi":
                        doi = id_elem.text or ""
                        break

                articles.append({
                    "source": "pubmed",
                    "id": pmid,
                    "title": title,
                    "abstract": abstract,
                    "authors": authors,
                    "journal": journal,
                    "year": year,
                    "publication_date": publication_date,
                    "doi": doi,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                })
            except Exception as e:
                print(f"Error parsing article: {e}")
                continue

        return articles


def search(query: str, max_results: int = 100, email: str = "your@email.com",
           api_key: Optional[str] = None, output_file: Optional[str] = None, date_range: Optional[Dict] = None) -> List[Dict]:
    """
    Convenience function to search PubMed.

    Args:
        query: Search term
        max_results: Maximum results to return
        email: Your email (required by NCBI)
        api_key: Optional NCBI API key
        output_file: Optional path to save results incrementally (JSONL format)

    Returns:
        List of article dictionaries
    """
    searcher = PubMedSearcher(email=email, api_key=api_key)
    return searcher.search(query, max_results, output_file=output_file, date_range=date_range)


if __name__ == "__main__":
    # Test search
    results = search("machine learning healthcare", max_results=5)
    for r in results:
        print(f"- {r['title'][:80]}... ({r['year']})")
