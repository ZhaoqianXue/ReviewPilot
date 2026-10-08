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
from searchers.http_retry import TransientSourceError
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


class SourceSearchError(RuntimeError):
    """A source search failed; carries the query the adapter attempted to send.

    ``transient`` is True for failures a later attempt may fix (rate limits,
    server errors, timeouts) and False for ones it cannot (a rejected query).
    """

    def __init__(self, message: str, executed_query: str = "", transient: bool = False):
        super().__init__(message)
        self.executed_query = executed_query
        self.transient = transient


def _setting(name: str) -> Optional[str]:
    value = getattr(config, name, None) if config else None
    return value or os.getenv(name) or None


def _email() -> str:
    return _setting("EMAIL") or os.getenv("RESEARCHER_EMAIL") or "researcher@example.com"


_SEARCHERS: Dict[str, Callable[[], Any]] = {
    "pubmed": lambda: PubMedSearcher(email=_email(), api_key=_setting("PUBMED_API_KEY")),
    "arxiv": ArxivSearcher,
    "openalex": lambda: OpenAlexSearcher(email=_email(), api_key=_setting("OPENALEX_API_KEY")),
}


def search_source(source: str, query: str, *, max_results: int, date_range: Optional[Dict] = None,
                  output_file: Optional[str] = None) -> SourceResult:
    """Search one supported source.

    Raises ``SourceSearchError`` on failure, carrying the query the adapter had
    built (adapters set ``last_query`` before sending), so a failed search still
    reports exactly what was attempted.
    """
    if source not in _SEARCHERS:
        raise ValueError(f"Unsupported search source: {source}")
    if max_results <= 0:
        raise ValueError("Each source needs a positive result limit.")
    searcher = _SEARCHERS[source]()
    try:
        records = searcher.search(query, max_results=max_results, output_file=output_file, date_range=date_range)
    except Exception as exc:
        raise SourceSearchError(str(exc) or exc.__class__.__name__, getattr(searcher, "last_query", "") or "",
                                transient=isinstance(exc, TransientSourceError)) from exc
    return SourceResult(records, searcher.last_query)
