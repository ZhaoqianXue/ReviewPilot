"""
Platform-specific academic paper searchers.
"""

from searchers.pubmed import PubMedSearcher
from searchers.openalex import OpenAlexSearcher
from searchers.arxiv_search import ArxivSearcher
from searchers.scopus import ScopusSearcher
from searchers.wos import WoSSearcher
from searchers.google_scholar import GoogleScholarSearcher
from searchers.dblp import DBLPSearcher

__all__ = [
    'PubMedSearcher',
    'OpenAlexSearcher',
    'ArxivSearcher',
    'ScopusSearcher',
    'WoSSearcher',
    'GoogleScholarSearcher',
    'DBLPSearcher'
]
