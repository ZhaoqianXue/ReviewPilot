"""The scholarly sources ReviewPilot searches, behind one interface.

Every source receives the same source-neutral Boolean query, matches it against
titles and abstracts, and reports the exact query string it executed so the
search can be reported and reproduced.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from searchers.arxiv_search import ArxivSearcher
from searchers.openalex import OpenAlexSearcher
from searchers.pubmed import PubMedSearcher

try:
    import config
except ImportError:  # config.py is optional and git-ignored
    config = None

SOURCE_ORDER = ("pubmed", "arxiv", "openalex")
SOURCE_LABELS = {"pubmed": "PubMed", "arxiv": "arXiv", "openalex": "OpenAlex"}


@dataclass(frozen=True)
class SourceResult:
    records: List[Dict[str, Any]]
    executed_query: str


def _setting(name: str) -> Optional[str]:
    value = getattr(config, name, None) if config else None
    return value or os.getenv(name) or None


def _email() -> str:
    return _setting("EMAIL") or os.getenv("RESEARCHER_EMAIL") or "researcher@example.com"


def _pubmed(query: str, max_results: int, date_range: Optional[Dict], output_file: Optional[str]) -> SourceResult:
    searcher = PubMedSearcher(email=_email(), api_key=_setting("PUBMED_API_KEY"))
    records = searcher.search(query, max_results=max_results, output_file=output_file, date_range=date_range)
    return SourceResult(records, searcher.last_query)


def _arxiv(query: str, max_results: int, date_range: Optional[Dict], output_file: Optional[str]) -> SourceResult:
    searcher = ArxivSearcher()
    records = searcher.search(query, max_results=max_results, output_file=output_file, date_range=date_range)
    return SourceResult(records, searcher.last_query)


def _openalex(query: str, max_results: int, date_range: Optional[Dict], output_file: Optional[str]) -> SourceResult:
    searcher = OpenAlexSearcher(email=_email(), api_key=_setting("OPENALEX_API_KEY"))
    records = searcher.search(query, max_results=max_results, output_file=output_file, date_range=date_range)
    return SourceResult(records, searcher.last_query)


_SEARCHES: Dict[str, Callable[..., SourceResult]] = {"pubmed": _pubmed, "arxiv": _arxiv, "openalex": _openalex}


def search_source(source: str, query: str, *, max_results: int, date_range: Optional[Dict] = None,
                  output_file: Optional[str] = None) -> SourceResult:
    """Search one supported source; raises on failure so the caller can record the error."""
    if source not in _SEARCHES:
        raise ValueError(f"Unsupported search source: {source}")
    if max_results <= 0:
        raise ValueError("Each source needs a positive result limit.")
    return _SEARCHES[source](query, max_results, date_range, output_file)
