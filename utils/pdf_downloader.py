"""
Cascade PDF Downloader - Enhanced Version with Smart Publisher Detection.
Attempts multiple methods to download academic paper PDFs.
Includes journal-to-publisher mapping, LLM-based publisher detection,
and publisher-specific URL patterns.
"""

import requests
import json
import time
import re
import os
from pathlib import Path
from typing import Optional, Dict, Tuple, List
from urllib.parse import quote, urlparse

# Optional Selenium imports for Cloudflare bypass
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# Journal name to publisher mapping (comprehensive)
JOURNAL_TO_PUBLISHER = {
    # Preprint servers (highest priority - always open access)
    'arxiv': 'arxiv',
    'biorxiv': 'biorxiv',
    'medrxiv': 'medrxiv',
    'chemrxiv': 'chemrxiv',
    'preprint': 'preprint',

    # Nature Publishing Group / Springer Nature
    'nature': 'nature',
    'scientific reports': 'nature',
    'nature medicine': 'nature',
    'nature communications': 'nature',
    'nature genetics': 'nature',
    'nature methods': 'nature',
    'nature biotechnology': 'nature',
    'npj': 'nature',  # npj Digital Medicine, etc.
    'european journal of human genetics': 'nature',
    'ejhg': 'nature',
    'communications biology': 'nature',
    'communications medicine': 'nature',

    # Springer
    'springer': 'springer',
    'journal of': 'springer',  # Many Springer journals

    # Elsevier / Lancet / Cell Press
    'lancet': 'lancet',  # Lancet has its own PDF URL pattern
    'the lancet': 'lancet',
    'lancet digital health': 'lancet',
    'lancet oncology': 'lancet',
    'lancet neurology': 'lancet',
    'lancet psychiatry': 'lancet',
    'lancet infectious diseases': 'lancet',
    'cell': 'cell',
    'neuron': 'cell',
    'immunity': 'cell',
    'current biology': 'cell',
    'cell reports': 'cell',
    'cell systems': 'cell',
    'molecular cell': 'cell',
    'cell genomics': 'cell',
    'cell reports medicine': 'cell',
    'hgg advances': 'cell',
    'american journal of human genetics': 'cell',
    'ajhg': 'cell',
    'ebiomedicine': 'elsevier',
    'international journal of medical informatics': 'elsevier',
    'journal of biomedical informatics': 'elsevier',
    'artificial intelligence in medicine': 'elsevier',
    'computers in biology and medicine': 'elsevier',
    'sciencedirect': 'elsevier',

    # PLOS (open access)
    'plos': 'plos',
    'plos one': 'plos',
    'plos medicine': 'plos',
    'plos biology': 'plos',
    'plos genetics': 'plos',
    'plos computational biology': 'plos',
    'plos digital health': 'plos',

    # MDPI (open access)
    'mdpi': 'mdpi',
    'biology': 'mdpi',
    'diagnostics': 'mdpi',
    'genes': 'mdpi',
    'jcm': 'mdpi',
    'journal of clinical medicine': 'mdpi',
    'jpm': 'mdpi',
    'journal of personalized medicine': 'mdpi',
    'biomedicines': 'mdpi',
    'healthcare': 'mdpi',
    'life': 'mdpi',
    'sensors': 'mdpi',
    'applied sciences': 'mdpi',
    'algorithms': 'mdpi',
    'information': 'mdpi',

    # Frontiers (open access)
    'frontiers': 'frontiers',
    'frontiers in': 'frontiers',

    # Oxford Academic
    'oxford': 'oxford',
    'jamia': 'oxford',
    'bioinformatics': 'oxford',
    'nucleic acids research': 'oxford',
    'nar': 'oxford',
    'database': 'oxford',
    'briefings in bioinformatics': 'oxford',

    # BMC / BioMed Central (open access)
    'bmc': 'bmc',
    'biomedcentral': 'bmc',
    'genome medicine': 'bmc',
    'genome biology': 'bmc',
    'orphanet journal of rare diseases': 'bmc',

    # Wiley
    'wiley': 'wiley',
    'human mutation': 'wiley',
    'genetic testing and molecular biomarkers': 'wiley',

    # IEEE
    'ieee': 'ieee',
    'ieee access': 'ieee',  # Open access journal
    'ieee transactions': 'ieee',
    'ieee journal': 'ieee',

    # ACM
    'acm': 'acm',
    'proceedings of the': 'acm',  # Many ACM proceedings

    # AAAS / Science
    'science': 'science',
    'science translational medicine': 'science',
    'science advances': 'science',

    # JAMA Network
    'jama': 'jama',

    # BMJ
    'bmj': 'bmj',
    'british medical journal': 'bmj',

    # Taylor & Francis
    'taylor': 'taylor',
    'informa': 'taylor',

    # SAGE
    'sage': 'sage',

    # ACS
    'acs': 'acs',
    'journal of the american chemical society': 'acs',
    'jacs': 'acs',

    # PeerJ (open access)
    'peerj': 'peerj',

    # eLife (open access)
    'elife': 'elife',

    # IOS Press
    'ios press': 'ios',
    'studies in health technology': 'ios',

    # JoVE
    'jove': 'jove',
    'journal of visualized experiments': 'jove',

    # Conference proceedings
    'neurips': 'neurips',
    'icml': 'icml',
    'iclr': 'iclr',
    'aaai': 'aaai',
    'proceedings of machine learning': 'mlr',
    'machine learning research': 'mlr',
    'miccai': 'springer',
    'medical image computing': 'springer',
}


class CascadePDFDownloader:
    """
    Downloads PDFs using an enhanced cascade of methods:
    1. Direct link (if available)
    2. Publisher-specific URL patterns
    3. Unpaywall API (legal open access)
    4. Semantic Scholar API
    5. CORE API (open access aggregator)
    6. PubMed Central
    7. Europe PMC
    8. bioRxiv/medRxiv
    9. arXiv search by title
    10. DOI redirect following
    11. LLM web search (with title verification to prevent hallucinations)
    """

    # Publisher PDF URL patterns - maps domain patterns to PDF URL transformers
    PUBLISHER_PATTERNS = {
        # Elsevier / ScienceDirect
        'sciencedirect.com': lambda url, doi: _elsevier_pdf(url, doi),
        # Springer / Nature
        'link.springer.com': lambda url, doi: f"https://link.springer.com/content/pdf/{doi}.pdf" if doi else None,
        'nature.com': lambda url, doi: _nature_pdf(url),
        # Wiley
        'onlinelibrary.wiley.com': lambda url, doi: f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}" if doi else None,
        # MDPI (usually open access)
        'mdpi.com': lambda url, doi: url.replace('/htm', '/pdf') if '/htm' in url else f"{url}/pdf",
        # Frontiers (open access)
        'frontiersin.org': lambda url, doi: f"{url}/pdf" if not url.endswith('/pdf') else url,
        # Taylor & Francis
        'tandfonline.com': lambda url, doi: f"https://www.tandfonline.com/doi/pdf/{doi}" if doi else None,
        # SAGE
        'journals.sagepub.com': lambda url, doi: f"https://journals.sagepub.com/doi/pdf/{doi}" if doi else None,
        # Oxford Academic
        'academic.oup.com': lambda url, doi: _oxford_pdf(url, doi),
        # IEEE
        'ieeexplore.ieee.org': lambda url, doi: _ieee_pdf(url),
        # ACM
        'dl.acm.org': lambda url, doi: _acm_pdf(url, doi),
        # PLOS (open access)
        'journals.plos.org': lambda url, doi: url.replace('article?', 'article/file?') + '&type=printable' if 'article?' in url else None,
        # BMC / BioMed Central (open access)
        'biomedcentral.com': lambda url, doi: f"{url.split('?')[0]}.pdf",
        # PeerJ (open access)
        'peerj.com': lambda url, doi: f"{url}.pdf" if not url.endswith('.pdf') else url,
        # eLife (open access)
        'elifesciences.org': lambda url, doi: _elife_pdf(url),
        # Royal Society
        'royalsocietypublishing.org': lambda url, doi: f"https://royalsocietypublishing.org/doi/pdf/{doi}" if doi else None,
        # Science (AAAS)
        'science.org': lambda url, doi: f"https://www.science.org/doi/pdf/{doi}" if doi else None,
        # Cell Press
        'cell.com': lambda url, doi: _cell_pdf(url),
        # JAMA Network
        'jamanetwork.com': lambda url, doi: f"{url.replace('fullarticle', 'articlepdf')}" if 'fullarticle' in url else None,
        # ACS Publications
        'pubs.acs.org': lambda url, doi: f"https://pubs.acs.org/doi/pdf/{doi}" if doi else None,
    }

    def __init__(self, email: str = "user@example.com", output_dir: Path = None):
        """
        Initialize the downloader.

        Args:
            email: Email for API access (required by Unpaywall, CORE)
            output_dir: Directory to save PDFs
        """
        self.email = email
        self.output_dir = output_dir or Path("pdfs")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.session = requests.Session()
        # Use browser-like headers to avoid blocks
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        })

        # Rate limiting
        self.last_request_time = 0
        self.min_request_interval = 0.5  # seconds (slightly faster)

        # CORE API key (optional, works without but rate limited)
        self.core_api_key = None

        # LLM function for publisher detection (optional)
        self.llm_query_func = None

        # Enable LLM web search as final fallback (optional)
        self.use_web_search = False
        self.web_search_model = os.getenv("REVIEWPILOT_LLM_MODEL", "gpt-5.4-nano")

    def set_llm_query_func(self, func):
        """Set the LLM query function for smart publisher detection."""
        self.llm_query_func = func

    def enable_web_search(self, model: str = None):
        """Enable LLM web search as final fallback for failed downloads."""
        self.use_web_search = True
        self.web_search_model = model or os.getenv("REVIEWPILOT_LLM_MODEL", "gpt-5.4-nano")

    def _detect_publisher_from_journal(self, journal: str) -> Optional[str]:
        """Detect publisher from journal name using mapping."""
        if not journal:
            return None

        journal_lower = re.sub(r"\s+", " ", journal.lower()).strip()

        exact_publisher = JOURNAL_TO_PUBLISHER.get(journal_lower)
        if exact_publisher:
            return exact_publisher

        strong_indicators = [
            (r"\bieee\b", "ieee"),
            (r"\bios press\b", "ios"),
            (r"\bstudies in health technology\b", "ios"),
            (r"\bjove\b", "jove"),
            (r"\bjournal of visualized experiments\b", "jove"),
        ]
        for pattern, publisher in strong_indicators:
            if re.search(pattern, journal_lower):
                return publisher

        unsafe_broad_patterns = {"journal of", "proceedings of the"}
        for pattern, publisher in sorted(
            JOURNAL_TO_PUBLISHER.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            if pattern in unsafe_broad_patterns:
                continue
            pattern_re = r"(?<![a-z0-9])" + re.escape(pattern) + r"(?![a-z0-9])"
            if re.search(pattern_re, journal_lower):
                return publisher

        return None

    def _detect_publisher_with_llm(self, paper: Dict) -> Optional[str]:
        """Use LLM to detect publisher for unknown journals."""
        if not self.llm_query_func:
            return None

        journal = paper.get("journal", "")
        title = paper.get("title", "")
        doi = paper.get("doi", "")

        if not journal and not doi:
            return None

        allowed_publishers = {
            "nature", "springer", "elsevier", "cell", "wiley", "oxford", "plos", "mdpi", "frontiers", "bmc",
            "ieee", "acm", "science", "jama", "bmj", "taylor", "sage", "acs", "peerj", "elife", "arxiv",
            "biorxiv", "medrxiv", "unknown",
        }
        prompt = f"""Classify the publisher or preprint server for the supplied paper metadata.

PAPER METADATA DATA:
{json.dumps({"journal": journal, "doi": doi, "title": title}, ensure_ascii=False)}

ALLOWED OUTPUT LABELS:
{json.dumps(sorted(allowed_publishers))}

Return exactly one allowed lowercase label."""

        try:
            response, _ = self.llm_query_func(
                text_prompt=prompt,
                system_prompt="You classify scholarly paper metadata into a supplied publisher vocabulary.",
                model=os.getenv("REVIEWPILOT_LLM_MODEL", "gpt-5.4-nano")
            )
            publisher = response.strip().lower()
            if publisher in allowed_publishers and publisher != "unknown":
                return publisher
        except:
            pass

        return None

    def _rate_limit(self, interval: float = None):
        """Enforce rate limiting between requests."""
        min_interval = interval or self.min_request_interval
        elapsed = time.time() - self.last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self.last_request_time = time.time()

    def _sanitize_filename(self, title: str) -> str:
        """Create a safe filename from paper title."""
        safe = re.sub(r'[^\w\s-]', '', title.lower())
        safe = re.sub(r'\s+', '_', safe)
        return safe[:100]

    def _extract_title_from_pdf(self, pdf_path: Path) -> str:
        """Extract title from the first page of a PDF for verification."""
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(str(pdf_path))
            if len(doc) == 0:
                return ""

            # Get text from first page
            first_page = doc[0]
            text = first_page.get_text()[:3000]
            doc.close()

            # Extract title using heuristics
            lines = [line.strip() for line in text.split('\n') if line.strip()]

            for line in lines[:15]:
                line_lower = line.lower()

                # Stop at abstract
                if line_lower.startswith('abstract') or line_lower == 'abstract':
                    break

                # Skip common header patterns
                skip_patterns = [
                    r'^\d+$', r'^page\s*\d+', r'^vol\.\s*\d+',
                    r'^\d{4}\s*(ieee|acm|springer)', r'^arxiv:',
                    r'^preprint', r'^accepted|submitted|published',
                    r'^author|correspondence|email|@',
                ]
                if any(re.match(p, line_lower) for p in skip_patterns):
                    continue

                # Good title candidate: 20-400 chars with letters
                if 20 <= len(line) <= 400 and re.search(r'[a-zA-Z]', line):
                    return line

            # Fallback: return first meaningful line
            for line in lines[:5]:
                if len(line) > 15 and re.search(r'[a-zA-Z]', line):
                    return line

        except Exception:
            pass

        return ""

    def _verify_title_match(self, expected_title: str, pdf_title: str) -> Tuple[bool, float]:
        """
        Verify if PDF title matches expected title to catch LLM hallucinations.

        Returns:
            Tuple of (is_match: bool, similarity_score: float)
        """
        if not expected_title or not pdf_title:
            return (False, 0.0)

        # Normalize both titles
        def normalize(s):
            s = s.lower().strip()
            s = re.sub(r'[^\w\s]', ' ', s)
            s = re.sub(r'\s+', ' ', s)
            return s

        exp_norm = normalize(expected_title)
        pdf_norm = normalize(pdf_title)

        # Method 1: Jaccard similarity
        exp_words = set(exp_norm.split())
        pdf_words = set(pdf_norm.split())
        intersection = exp_words & pdf_words
        union = exp_words | pdf_words
        jaccard = len(intersection) / len(union) if union else 0.0

        # Method 2: Word overlap ratio
        exp_words_filtered = {w for w in exp_words if len(w) > 2}
        pdf_words_filtered = {w for w in pdf_words if len(w) > 2}
        if exp_words_filtered:
            word_overlap = len(exp_words_filtered & pdf_words_filtered) / len(exp_words_filtered)
        else:
            word_overlap = 0.0

        # Method 3: Prefix match (first significant words)
        exp_sig_words = [w for w in exp_norm.split() if len(w) > 3][:6]
        pdf_sig_words = [w for w in pdf_norm.split() if len(w) > 3][:6]
        if exp_sig_words and pdf_sig_words:
            prefix_match = sum(1 for a, b in zip(exp_sig_words, pdf_sig_words) if a == b) / len(exp_sig_words)
        else:
            prefix_match = 0.0

        best_score = max(jaccard, word_overlap, prefix_match)

        # Require at least 0.4 similarity for web search results (stricter than extraction)
        # This catches obvious hallucinations while allowing for minor title variations
        is_match = best_score >= 0.4

        return (is_match, best_score)

    def download(self, paper: Dict) -> Tuple[bool, str, Optional[str]]:
        """
        Attempt to download PDF for a paper using smart cascade approach.

        Priority order:
        1. Preprints (arXiv, bioRxiv, medRxiv) - always work
        2. Journal-based publisher detection
        3. API-based methods (Unpaywall, Semantic Scholar, etc.)
        4. DOI redirect as fallback

        Args:
            paper: Paper metadata dict with keys like doi, title, url, pmid, arxiv_id, paper_id

        Returns:
            Tuple of (success, method_used, file_path or error_message)
        """
        title = paper.get("title", "unknown")
        doi = paper.get("doi")
        pmid = paper.get("pmid")
        arxiv_id = paper.get("arxiv_id")
        direct_url = paper.get("pdf_url") or paper.get("url")
        paper_id = paper.get("paper_id")
        journal = paper.get("journal", "")

        # Step 1: Detect publisher from journal name
        detected_publisher = self._detect_publisher_from_journal(journal)

        # Step 2: Build smart cascade based on detected publisher
        methods = []

        # Priority 1: Preprint servers (always try first if detected)
        if detected_publisher in ['arxiv', 'biorxiv', 'medrxiv', 'preprint']:
            methods.append(("arxiv", lambda: self._try_arxiv(arxiv_id, title)))
            methods.append(("biorxiv", lambda: self._try_biorxiv_medrxiv(doi, title)))

        # Priority 2: Direct PDF link
        methods.append(("direct", lambda: self._try_direct(direct_url)))

        # Priority 3: Publisher-specific method based on journal detection
        if detected_publisher:
            publisher_method = self._get_publisher_method(detected_publisher, doi, direct_url)
            if publisher_method:
                methods.append((f"publisher_{detected_publisher}", publisher_method))

        # Priority 4: URL-based publisher detection
        methods.append(("publisher_url", lambda: self._try_publisher_pattern(direct_url, doi)))

        # Priority 5: PMC direct (if URL contains PMC or pmc.ncbi.nlm.nih.gov)
        if direct_url and ('pmc' in direct_url.lower() or 'ncbi.nlm.nih.gov' in direct_url.lower()):
            methods.append(("pmc_direct", lambda: self._try_pmc_direct(direct_url)))

        # Priority 6: Open access APIs
        methods.append(("unpaywall", lambda: self._try_unpaywall(doi)))
        methods.append(("semantic_scholar", lambda: self._try_semantic_scholar(doi, title)))
        methods.append(("europepmc", lambda: self._try_europe_pmc(pmid, doi)))
        methods.append(("pmc", lambda: self._try_pmc(pmid, doi)))
        methods.append(("core", lambda: self._try_core(doi, title)))

        # Priority 6: Preprint servers (if not already tried)
        if detected_publisher not in ['arxiv', 'biorxiv', 'medrxiv', 'preprint']:
            methods.append(("arxiv", lambda: self._try_arxiv(arxiv_id, title)))
            methods.append(("biorxiv", lambda: self._try_biorxiv_medrxiv(doi, title)))
            # Also try to find preprint version of published paper
            methods.append(("preprint_lookup", lambda: self._try_find_preprint(doi, title)))

        # Priority 7: DOI redirect with HTML parsing
        methods.append(("doi_redirect", lambda: self._try_doi_redirect(doi)))

        # Priority 8: LLM-based publisher detection
        if self.llm_query_func and not detected_publisher:
            llm_publisher = self._detect_publisher_with_llm(paper)
            if llm_publisher:
                llm_method = self._get_publisher_method(llm_publisher, doi, direct_url)
                if llm_method:
                    methods.append((f"llm_{llm_publisher}", llm_method))

        # Execute cascade
        for method_name, method_func in methods:
            try:
                pdf_url = method_func()
                if pdf_url:
                    file_path = self._download_pdf(pdf_url, title, method_name, paper_id)
                    if file_path:
                        return True, method_name, str(file_path)
            except Exception as e:
                continue

        # Priority 9: LLM web search as final fallback
        if self.use_web_search:
            try:
                web_result = self._try_llm_web_search(title, doi, journal)
                if web_result:
                    file_path = self._download_pdf(web_result, title, "web_search", paper_id)
                    if file_path:
                        return True, "web_search", str(file_path)
            except Exception as e:
                pass

        return False, "none", "All download methods failed"

    def _get_publisher_method(self, publisher: str, doi: Optional[str], url: Optional[str]):
        """Get the download method for a specific publisher."""
        if not doi:
            return None

        publisher_methods = {
            'nature': lambda: f"https://www.nature.com/articles/{doi.split('/')[-1]}.pdf" if doi else None,
            'springer': lambda: f"https://link.springer.com/content/pdf/{doi}.pdf" if doi else None,
            'elsevier': lambda: self._try_elsevier(doi, url),
            'lancet': lambda: self._try_lancet(doi, url),  # Lancet has special PDF URL
            'cell': lambda: self._try_cell(doi, url),
            'wiley': lambda: f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}" if doi else None,
            'oxford': lambda: f"https://academic.oup.com/view-large/pdf/{doi}" if doi else None,
            'plos': lambda: self._try_plos(doi),
            'mdpi': lambda: self._try_mdpi(doi, url),
            'frontiers': lambda: self._try_frontiers(doi, url),
            'bmc': lambda: self._try_bmc(doi, url),
            'ieee': lambda: self._try_ieee(doi, url),
            'acm': lambda: f"https://dl.acm.org/doi/pdf/{doi}" if doi else None,
            'science': lambda: f"https://www.science.org/doi/pdf/{doi}" if doi else None,
            'jama': lambda: self._try_jama(doi, url),
            'taylor': lambda: f"https://www.tandfonline.com/doi/pdf/{doi}" if doi else None,
            'sage': lambda: f"https://journals.sagepub.com/doi/pdf/{doi}" if doi else None,
            'acs': lambda: f"https://pubs.acs.org/doi/pdf/{doi}" if doi else None,
            'peerj': lambda: f"https://peerj.com/articles/{doi.split('/')[-1]}.pdf" if doi else None,
            'elife': lambda: self._try_elife(doi),
        }

        return publisher_methods.get(publisher)

    def _try_elsevier(self, doi: Optional[str], url: Optional[str]) -> Optional[str]:
        """Try Elsevier/ScienceDirect PDF URL."""
        if url and 'sciencedirect.com' in url:
            pii_match = re.search(r'/pii/([A-Z0-9]+)', url, re.IGNORECASE)
            if pii_match:
                return f"https://www.sciencedirect.com/science/article/pii/{pii_match.group(1)}/pdfft"
        return None

    def _try_lancet(self, doi: Optional[str], url: Optional[str]) -> Optional[str]:
        """Try Lancet PDF URL using the showPdf action with PII from DOI.

        Lancet blocks direct PDF access (403) unless you have proper cookies/referer.
        We need to visit the article page first to establish a session.

        Handles two DOI formats:
        1. PII format: 10.1016/S2589-7500(25)00135-9
        2. Journal format: 10.1016/j.landig.2025.100953 (need to resolve to get PII)
        """
        if not doi and not url:
            return None

        pii = None
        article_url = None

        # Try to extract PII from DOI
        if doi:
            # Format 1: Direct PII in DOI (e.g., 10.1016/S2589-7500(25)00135-9)
            pii_match = re.search(r'10\.1016/(S\d{4}-?\d{4}\(?\d{2}\)?\d+-?\d+)', doi, re.IGNORECASE)
            if pii_match:
                pii = pii_match.group(1)
            else:
                # Format 2: Journal-style DOI (e.g., 10.1016/j.landig.2025.100953)
                # Need to follow DOI redirect to get article page and extract PII
                try:
                    self._rate_limit()
                    doi_url = f"https://doi.org/{doi}"
                    resp = self.session.get(doi_url, timeout=15, allow_redirects=True)
                    if resp.status_code == 200:
                        # Check for Elsevier linkinghub or Lancet URL
                        # Extract PII from URL (format: /pii/S2589750025001359 or /article/PIIS...)
                        pii_match = re.search(r'/pii/(S\d{10,})', resp.url, re.IGNORECASE)
                        if pii_match:
                            pii = pii_match.group(1)
                        else:
                            pii_match = re.search(r'/article/(PII)?(S\d{10,})', resp.url, re.IGNORECASE)
                            if pii_match:
                                pii = pii_match.group(2) if pii_match.group(2) else pii_match.group(1)

                        if not pii:
                            # Try to find PII in page content
                            pii_match = re.search(r'pii[=/](S\d{10,})', resp.text[:50000], re.IGNORECASE)
                            if pii_match:
                                pii = pii_match.group(1)
                            else:
                                # Look for showPdf link in page
                                pdf_match = re.search(r'href="([^"]*showPdf[^"]*pii=([^"&]+))"', resp.text)
                                if pdf_match:
                                    return f"https://www.thelancet.com{pdf_match.group(1)}" if pdf_match.group(1).startswith('/') else pdf_match.group(1)
                except:
                    pass

        # Try to extract PII from URL if still not found
        if not pii and url and 'thelancet.com' in url:
            pii_match = re.search(r'/article/(PII)?(S\d{10,})', url, re.IGNORECASE)
            if pii_match:
                pii = pii_match.group(1)

        if not pii:
            return None

        pii_encoded = quote(pii, safe='')
        pdf_url = f"https://www.thelancet.com/action/showPdf?pii={pii_encoded}"

        # If we have an article URL, use it; otherwise construct one
        if not article_url:
            article_url = f"https://www.thelancet.com/journals/landig/article/{pii}/fulltext"

        try:
            # Step 1: Visit article page to get session cookies
            self._rate_limit()
            self.session.headers.update({
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            })
            article_resp = self.session.get(article_url, timeout=15, allow_redirects=True)

            # Step 2: Now try to get PDF with proper Referer
            self._rate_limit()
            self.session.headers.update({
                'Referer': article_resp.url,
                'Accept': 'application/pdf,*/*',
            })

            # Try HEAD first
            head_resp = self.session.head(pdf_url, timeout=10, allow_redirects=True)
            if head_resp.status_code == 200:
                content_type = head_resp.headers.get('content-type', '')
                if 'pdf' in content_type.lower():
                    return head_resp.url
                # Return PDF URL anyway, _download_pdf will do the actual GET
                return pdf_url

        except Exception as e:
            pass

        # Fallback: return the PDF URL anyway, maybe direct download will work
        return pdf_url

    def _try_cell(self, doi: Optional[str], url: Optional[str]) -> Optional[str]:
        """Try Cell Press PDF URL."""
        if doi:
            # Cell Press uses a specific PDF URL pattern
            return f"https://www.cell.com/action/showPdf?pii={doi.split('/')[-1]}"
        return None

    def _try_plos(self, doi: Optional[str]) -> Optional[str]:
        """Try PLOS PDF URL."""
        if doi and '10.1371' in doi:
            return f"https://journals.plos.org/plosone/article/file?id={doi}&type=printable"
        return None

    def _try_mdpi(self, doi: Optional[str], url: Optional[str]) -> Optional[str]:
        """Try MDPI PDF URL.

        MDPI URLs have format: https://www.mdpi.com/ISSN/volume/issue/article/pdf
        DOI format: 10.3390/journalXXYYZZZZ needs to be resolved to get proper URL.
        """
        # If we have an MDPI URL, just append /pdf
        if url and 'mdpi.com' in url:
            base_url = url.replace('/htm', '').rstrip('/')
            if not base_url.endswith('/pdf'):
                return f"{base_url}/pdf"
            return base_url

        # For DOI, we need to follow redirect to get proper MDPI URL structure
        if doi and '10.3390' in doi:
            try:
                self._rate_limit()
                doi_url = f"https://doi.org/{doi}"
                resp = self.session.get(doi_url, timeout=15, allow_redirects=True)
                # Use the URL even if we get 403 (bot detection) - PDF endpoint might work
                if 'mdpi.com' in resp.url:
                    # Got the proper MDPI article URL, append /pdf
                    article_url = resp.url.rstrip('/')
                    if not article_url.endswith('/pdf'):
                        return f"{article_url}/pdf"
                    return article_url
            except:
                pass

            # Fallback: try direct construction (may not always work)
            return f"https://www.mdpi.com/{doi.replace('10.3390/', '')}/pdf"

        return None

    def _try_frontiers(self, doi: Optional[str], url: Optional[str]) -> Optional[str]:
        """Try Frontiers PDF URL."""
        if url and 'frontiersin.org' in url:
            return f"{url}/pdf" if not url.endswith('/pdf') else url
        return None

    def _try_bmc(self, doi: Optional[str], url: Optional[str]) -> Optional[str]:
        """Try BMC PDF URL."""
        if url and 'biomedcentral.com' in url:
            return f"{url.split('?')[0]}.pdf"
        return None

    def _try_ieee(self, doi: Optional[str], url: Optional[str]) -> Optional[str]:
        """Try IEEE PDF URL with enhanced support for IEEE Access (open access)."""
        arnumber = None

        # Extract article number from URL if available
        if url and 'ieeexplore.ieee.org' in url:
            match = re.search(r'/document/(\d+)', url)
            if match:
                arnumber = match.group(1)

        # If no URL or no arnumber, try to resolve DOI to get IEEE URL
        if not arnumber and doi:
            self._rate_limit()
            try:
                # Follow DOI redirect to IEEE
                doi_url = f"https://doi.org/{doi}"
                response = self.session.get(doi_url, timeout=15, allow_redirects=True)
                if response.status_code == 200 and 'ieeexplore.ieee.org' in response.url:
                    match = re.search(r'/document/(\d+)', response.url)
                    if match:
                        arnumber = match.group(1)
            except:
                pass

        if arnumber:
            # For IEEE Access (open access), try multiple PDF URL patterns
            pdf_urls = [
                # Direct PDF download (works for IEEE Access)
                f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={arnumber}&ref=",
                f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={arnumber}",
                # Alternative stamp format
                f"https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={arnumber}",
            ]

            for pdf_url in pdf_urls:
                try:
                    self._rate_limit()
                    response = self.session.head(pdf_url, timeout=10, allow_redirects=True)
                    if response.status_code == 200:
                        content_type = response.headers.get('content-type', '')
                        final_url = response.url
                        if 'pdf' in content_type.lower() or '.pdf' in final_url.lower():
                            return final_url
                        # IEEE sometimes returns the stamp page which then serves PDF
                        return pdf_url
                except:
                    continue

            # Return the most reliable format
            return f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={arnumber}"

        return None

    def _try_pmc_direct(self, url: Optional[str]) -> Optional[str]:
        """Try to get PDF directly from PMC page by parsing HTML."""
        if not url:
            return None

        # Extract PMC ID from URL
        pmc_match = re.search(r'PMC(\d+)', url, re.IGNORECASE)
        if not pmc_match:
            return None

        pmc_id = f"PMC{pmc_match.group(1)}"

        # Try direct PDF URLs first
        pdf_urls = [
            f"https://pmc.ncbi.nlm.nih.gov/articles/{pmc_id}/pdf/",
            f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/pdf/",
        ]

        for pdf_url in pdf_urls:
            try:
                self._rate_limit()
                response = self.session.head(pdf_url, timeout=10, allow_redirects=True)
                if response.status_code == 200:
                    return response.url
            except:
                continue

        # If direct URLs don't work, parse the PMC page to find PDF link
        try:
            self._rate_limit()
            response = self.session.get(url, timeout=15)
            if response.status_code == 200:
                html = response.text

                # Look for PDF link patterns in PMC pages
                pdf_patterns = [
                    r'href="(/pmc/articles/PMC\d+/pdf/[^"]*)"',
                    r'href="(https://pmc\.ncbi\.nlm\.nih\.gov/articles/PMC\d+/pdf/[^"]*)"',
                    r'href="([^"]*\.pdf)"[^>]*>.*?PDF',
                    r'<a[^>]*href="([^"]+)"[^>]*class="[^"]*pdf[^"]*"',
                ]

                for pattern in pdf_patterns:
                    match = re.search(pattern, html, re.IGNORECASE)
                    if match:
                        pdf_link = match.group(1)
                        # Make absolute URL if needed
                        if pdf_link.startswith('/'):
                            pdf_link = f"https://pmc.ncbi.nlm.nih.gov{pdf_link}"
                        return pdf_link
        except:
            pass

        # Return the standard PMC PDF URL as fallback
        return f"https://pmc.ncbi.nlm.nih.gov/articles/{pmc_id}/pdf/"

    def _try_jama(self, doi: Optional[str], url: Optional[str]) -> Optional[str]:
        """Try JAMA Network PDF URL."""
        if url and 'jamanetwork.com' in url and 'fullarticle' in url:
            return url.replace('fullarticle', 'articlepdf')
        return None

    def _try_elife(self, doi: Optional[str]) -> Optional[str]:
        """Try eLife PDF URL."""
        if doi and '10.7554' in doi:
            article_id = doi.split('.')[-1]
            return f"https://elifesciences.org/articles/{article_id}.pdf"
        return None

    def _try_direct(self, url: Optional[str]) -> Optional[str]:
        """Try direct URL if it looks like a PDF."""
        if not url:
            return None

        # Check if URL ends with .pdf or is from known PDF sources
        if url.endswith('.pdf') or '/pdf/' in url.lower():
            return url

        # Try to fetch and check content type
        try:
            self._rate_limit()
            response = self.session.head(url, timeout=10, allow_redirects=True)
            content_type = response.headers.get('content-type', '')
            if 'pdf' in content_type.lower():
                return response.url  # Return final URL after redirects
        except:
            pass

        return None

    def _try_publisher_pattern(self, url: Optional[str], doi: Optional[str]) -> Optional[str]:
        """Try publisher-specific URL patterns."""
        if not url:
            return None

        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower().replace('www.', '')

            # Check each publisher pattern
            for pattern_domain, transformer in self.PUBLISHER_PATTERNS.items():
                if pattern_domain in domain:
                    pdf_url = transformer(url, doi)
                    if pdf_url:
                        # Verify it's actually a PDF
                        self._rate_limit()
                        try:
                            response = self.session.head(pdf_url, timeout=10, allow_redirects=True)
                            if response.status_code == 200:
                                content_type = response.headers.get('content-type', '')
                                if 'pdf' in content_type.lower() or response.url.endswith('.pdf'):
                                    return response.url
                        except:
                            # Still try to download even if HEAD fails
                            return pdf_url
        except:
            pass

        return None

    def _try_unpaywall(self, doi: Optional[str]) -> Optional[str]:
        """Try Unpaywall API to find open access PDF."""
        if not doi:
            return None

        self._rate_limit()
        url = f"https://api.unpaywall.org/v2/{quote(doi, safe='')}?email={self.email}"

        try:
            response = self.session.get(url, timeout=15)
            if response.status_code == 200:
                data = response.json()

                # Try best_oa_location first
                best_oa = data.get("best_oa_location")
                if best_oa:
                    pdf_url = best_oa.get("url_for_pdf") or best_oa.get("url")
                    if pdf_url:
                        return pdf_url

                # Try other OA locations
                for loc in data.get("oa_locations", []):
                    pdf_url = loc.get("url_for_pdf") or loc.get("url")
                    if pdf_url:
                        return pdf_url
        except:
            pass

        return None

    def _try_semantic_scholar(self, doi: Optional[str], title: str) -> Optional[str]:
        """Try Semantic Scholar API for open access PDF."""
        self._rate_limit()

        # Try by DOI first
        if doi:
            url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{quote(doi, safe='')}?fields=openAccessPdf"
            try:
                response = self.session.get(url, timeout=15)
                if response.status_code == 200:
                    data = response.json()
                    oa_pdf = data.get("openAccessPdf")
                    if oa_pdf and oa_pdf.get("url"):
                        return oa_pdf["url"]
            except:
                pass

        # Try by title search
        if title:
            self._rate_limit()
            search_url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={quote(title)}&fields=openAccessPdf&limit=1"
            try:
                response = self.session.get(search_url, timeout=15)
                if response.status_code == 200:
                    data = response.json()
                    papers = data.get("data", [])
                    if papers:
                        oa_pdf = papers[0].get("openAccessPdf")
                        if oa_pdf and oa_pdf.get("url"):
                            return oa_pdf["url"]
            except:
                pass

        return None

    def _try_core(self, doi: Optional[str], title: str) -> Optional[str]:
        """Try CORE API for open access PDF."""
        self._rate_limit(1.0)  # CORE has stricter rate limits

        headers = {"Accept": "application/json"}
        if self.core_api_key:
            headers["Authorization"] = f"Bearer {self.core_api_key}"

        # Try by DOI first
        if doi:
            url = f"https://api.core.ac.uk/v3/search/works?q=doi:{quote(doi, safe='')}&limit=1"
            try:
                response = self.session.get(url, headers=headers, timeout=15)
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])
                    if results:
                        download_url = results[0].get("downloadUrl")
                        if download_url:
                            return download_url
            except:
                pass

        # Try by title
        if title:
            self._rate_limit(1.0)
            url = f"https://api.core.ac.uk/v3/search/works?q={quote(title)}&limit=1"
            try:
                response = self.session.get(url, headers=headers, timeout=15)
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])
                    if results:
                        download_url = results[0].get("downloadUrl")
                        if download_url:
                            return download_url
            except:
                pass

        return None

    def _try_pmc(self, pmid: Optional[str], doi: Optional[str]) -> Optional[str]:
        """Try PubMed Central for free full text."""
        pmc_id = None

        if pmid:
            self._rate_limit()
            url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?tool=academic_search&email={self.email}&ids={pmid}&format=json"
            try:
                response = self.session.get(url, timeout=15)
                if response.status_code == 200:
                    data = response.json()
                    records = data.get("records", [])
                    if records and records[0].get("pmcid"):
                        pmc_id = records[0]["pmcid"]
            except:
                pass

        if not pmc_id and doi:
            self._rate_limit()
            url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?tool=academic_search&email={self.email}&ids={quote(doi, safe='')}&format=json"
            try:
                response = self.session.get(url, timeout=15)
                if response.status_code == 200:
                    data = response.json()
                    records = data.get("records", [])
                    if records and records[0].get("pmcid"):
                        pmc_id = records[0]["pmcid"]
            except:
                pass

        if pmc_id:
            # Try multiple PMC PDF URL formats (new and old domains)
            pmc_urls = [
                f"https://pmc.ncbi.nlm.nih.gov/articles/{pmc_id}/pdf/",
                f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/pdf/",
            ]

            for pdf_url in pmc_urls:
                try:
                    self._rate_limit()
                    response = self.session.head(pdf_url, timeout=10, allow_redirects=True)
                    if response.status_code == 200:
                        content_type = response.headers.get('content-type', '')
                        if 'pdf' in content_type.lower() or response.url.endswith('.pdf'):
                            return response.url
                        # Sometimes PMC returns HTML that redirects to PDF
                        if response.status_code == 200:
                            return pdf_url
                except:
                    continue

            # Return the new format as default
            return f"https://pmc.ncbi.nlm.nih.gov/articles/{pmc_id}/pdf/"

        return None

    def _try_europe_pmc(self, pmid: Optional[str], doi: Optional[str]) -> Optional[str]:
        """Try Europe PMC for open access PDF."""
        self._rate_limit()

        # Try by PMID
        if pmid:
            url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:{pmid}&format=json"
            try:
                response = self.session.get(url, timeout=15)
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("resultList", {}).get("result", [])
                    if results:
                        result = results[0]
                        # Check if full text is available
                        if result.get("isOpenAccess") == "Y":
                            pmcid = result.get("pmcid")
                            if pmcid:
                                return f"https://europepmc.org/backend/ptpmcrender.fcgi?accid={pmcid}&blobtype=pdf"
            except:
                pass

        # Try by DOI
        if doi:
            self._rate_limit()
            url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:{quote(doi, safe='')}&format=json"
            try:
                response = self.session.get(url, timeout=15)
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("resultList", {}).get("result", [])
                    if results:
                        result = results[0]
                        if result.get("isOpenAccess") == "Y":
                            pmcid = result.get("pmcid")
                            if pmcid:
                                return f"https://europepmc.org/backend/ptpmcrender.fcgi?accid={pmcid}&blobtype=pdf"
            except:
                pass

        return None

    def _try_biorxiv_medrxiv(self, doi: Optional[str], title: str) -> Optional[str]:
        """Try bioRxiv and medRxiv for preprint PDFs.

        Handles both DOI prefixes:
        - 10.1101: Traditional bioRxiv/medRxiv DOI prefix
        - 10.64898: Newer medRxiv DOI prefix (e.g., 10.64898/2026.02.10.26346018)
        """
        # Check if DOI is from bioRxiv/medRxiv
        if doi:
            # Handle both DOI prefixes
            is_preprint_doi = '10.1101' in doi or '10.64898' in doi

            if is_preprint_doi:
                # For newer 10.64898 format, try direct URL construction
                # DOI like 10.64898/2026.02.10.26346018 -> medrxiv ID is the suffix
                if '10.64898' in doi:
                    # Extract the medRxiv ID (the part after 10.64898/)
                    medrxiv_id = doi.replace('10.64898/', '')
                    # Try direct PDF URL construction for medRxiv
                    pdf_url = f"https://www.medrxiv.org/content/10.1101/{medrxiv_id}.full.pdf"
                    try:
                        self._rate_limit()
                        response = self.session.head(pdf_url, timeout=10, allow_redirects=True)
                        if response.status_code == 200:
                            return pdf_url
                    except:
                        pass

                    # Also try with the original DOI format
                    pdf_url_alt = f"https://www.medrxiv.org/content/{doi}.full.pdf"
                    try:
                        self._rate_limit()
                        response = self.session.head(pdf_url_alt, timeout=10, allow_redirects=True)
                        if response.status_code == 200:
                            return pdf_url_alt
                    except:
                        pass

                    # Try to follow DOI redirect to find actual URL
                    try:
                        self._rate_limit()
                        doi_url = f"https://doi.org/{doi}"
                        response = self.session.get(doi_url, timeout=15, allow_redirects=True)
                        if response.status_code == 200 and 'medrxiv.org' in response.url:
                            # Got medRxiv URL, construct PDF link
                            article_url = response.url.rstrip('/')
                            if '.full' not in article_url:
                                return f"{article_url}.full.pdf"
                            else:
                                return article_url.replace('.full', '.full.pdf')
                    except:
                        pass

                # Standard 10.1101 handling
                self._rate_limit()
                # Try bioRxiv API
                url = f"https://api.biorxiv.org/details/biorxiv/{doi}"
                try:
                    response = self.session.get(url, timeout=15)
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("collection"):
                            paper = data["collection"][0]
                            biorxiv_doi = paper.get("doi")
                            if biorxiv_doi:
                                return f"https://www.biorxiv.org/content/{biorxiv_doi}.full.pdf"
                except:
                    pass

                # Try medRxiv API
                self._rate_limit()
                url = f"https://api.biorxiv.org/details/medrxiv/{doi}"
                try:
                    response = self.session.get(url, timeout=15)
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("collection"):
                            paper = data["collection"][0]
                            medrxiv_doi = paper.get("doi")
                            if medrxiv_doi:
                                return f"https://www.medrxiv.org/content/{medrxiv_doi}.full.pdf"
                except:
                    pass

        # Search by title on bioRxiv/medRxiv using Europe PMC (indexes preprints)
        if title:
            self._rate_limit()
            # Europe PMC indexes bioRxiv and medRxiv preprints
            search_title = quote(title[:150])
            url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=TITLE:\"{search_title}\" AND (SRC:PPR)&format=json&pageSize=5"
            try:
                response = self.session.get(url, timeout=15)
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("resultList", {}).get("result", [])
                    for result in results:
                        # Check if it's from bioRxiv or medRxiv
                        source = result.get("bookOrReportDetails", {}).get("publisher", "").lower()
                        preprint_doi = result.get("doi")
                        if preprint_doi and ('biorxiv' in source or 'medrxiv' in source or '10.1101' in str(preprint_doi)):
                            if 'medrxiv' in source:
                                return f"https://www.medrxiv.org/content/{preprint_doi}.full.pdf"
                            else:
                                return f"https://www.biorxiv.org/content/{preprint_doi}.full.pdf"
            except:
                pass

            # Also try direct bioRxiv search page scraping as fallback
            self._rate_limit()
            search_url = f"https://www.biorxiv.org/search/{quote(title[:80])}"
            try:
                response = self.session.get(search_url, timeout=15)
                if response.status_code == 200:
                    # Look for article links
                    matches = re.findall(r'href="(/content/10\.1101/[^"]+)"', response.text)
                    if matches:
                        # Get first match and construct PDF URL
                        article_path = matches[0]
                        # Remove version suffix if present for PDF
                        pdf_url = f"https://www.biorxiv.org{article_path}.full.pdf"
                        return pdf_url
            except:
                pass

        return None

    def _try_find_preprint(self, doi: Optional[str], title: str) -> Optional[str]:
        """
        Try to find preprint version of a published paper.
        Uses Europe PMC to find linked preprints and Semantic Scholar for preprint data.
        """
        # Method 1: Use Europe PMC to find preprint links for published DOI
        if doi:
            self._rate_limit()
            url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:{quote(doi, safe='')}&format=json"
            try:
                response = self.session.get(url, timeout=15)
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("resultList", {}).get("result", [])
                    if results:
                        result = results[0]
                        pmid = result.get("pmid")
                        # Check for preprint reference
                        if pmid:
                            # Try to get linked preprints via Europe PMC references
                            self._rate_limit()
                            ref_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/MED/{pmid}/references?format=json"
                            try:
                                ref_resp = self.session.get(ref_url, timeout=15)
                                if ref_resp.status_code == 200:
                                    ref_data = ref_resp.json()
                                    refs = ref_data.get("referenceList", {}).get("reference", [])
                                    for ref in refs:
                                        ref_doi = ref.get("doi", "")
                                        if ref_doi and "10.1101" in ref_doi:
                                            # Found a bioRxiv/medRxiv preprint reference
                                            return f"https://www.biorxiv.org/content/{ref_doi}.full.pdf"
                            except:
                                pass
            except:
                pass

        # Method 2: Use Semantic Scholar to find preprint versions
        if doi or title:
            self._rate_limit()
            if doi:
                ss_url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{quote(doi, safe='')}?fields=externalIds,openAccessPdf"
            else:
                ss_url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={quote(title)}&fields=externalIds,openAccessPdf&limit=1"

            try:
                response = self.session.get(ss_url, timeout=15)
                if response.status_code == 200:
                    data = response.json()
                    # Handle search results
                    if "data" in data and data["data"]:
                        data = data["data"][0]

                    external_ids = data.get("externalIds", {})

                    # Check for arXiv ID
                    arxiv_id = external_ids.get("ArXiv")
                    if arxiv_id:
                        return f"https://arxiv.org/pdf/{arxiv_id}.pdf"

                    # Check for bioRxiv/medRxiv via DOI
                    preprint_doi = external_ids.get("DOI", "")
                    if preprint_doi and "10.1101" in preprint_doi:
                        return f"https://www.biorxiv.org/content/{preprint_doi}.full.pdf"
            except:
                pass

        return None

    def _try_arxiv(self, arxiv_id: Optional[str], title: str) -> Optional[str]:
        """Try arXiv for preprint PDF."""
        if arxiv_id:
            arxiv_id = arxiv_id.replace("arXiv:", "").replace("arxiv:", "")
            return f"https://arxiv.org/pdf/{arxiv_id}.pdf"

        if title:
            self._rate_limit()
            search_query = quote(f'ti:"{title}"')
            url = f"http://export.arxiv.org/api/query?search_query={search_query}&max_results=1"

            try:
                response = self.session.get(url, timeout=15)
                if response.status_code == 200:
                    content = response.text
                    match = re.search(r'<id>http://arxiv.org/abs/([^<]+)</id>', content)
                    if match:
                        found_id = match.group(1)
                        return f"https://arxiv.org/pdf/{found_id}.pdf"
            except:
                pass

        return None

    def _try_doi_redirect(self, doi: Optional[str]) -> Optional[str]:
        """Follow DOI redirect and look for PDF link on the landing page."""
        if not doi:
            return None

        self._rate_limit()
        doi_url = f"https://doi.org/{doi}"

        try:
            response = self.session.get(doi_url, timeout=15, allow_redirects=True)
            if response.status_code == 200:
                final_url = response.url
                content_type = response.headers.get('content-type', '')

                # If we landed on a PDF, return it
                if 'pdf' in content_type.lower():
                    return final_url

                # Try to find PDF link in the HTML
                html = response.text[:100000]  # Limit to first 100KB

                # Look for common PDF link patterns (expanded for PMC, IEEE, etc.)
                pdf_patterns = [
                    # Direct PDF links
                    r'href="([^"]*\.pdf[^"]*)"',
                    r'href="([^"]*\/pdf\/[^"]*)"',
                    r'href="([^"]*\/pdf[^"]*)"',
                    # Data attributes
                    r'data-pdf-url="([^"]+)"',
                    r'data-pdf="([^"]+)"',
                    # JSON in page
                    r'"pdfUrl"\s*:\s*"([^"]+)"',
                    r'"pdf_url"\s*:\s*"([^"]+)"',
                    # CSS class-based links
                    r'<a[^>]*class="[^"]*pdf[^"]*"[^>]*href="([^"]+)"',
                    r'<a[^>]*class="[^"]*download[^"]*pdf[^"]*"[^>]*href="([^"]+)"',
                    # PMC specific patterns
                    r'href="(/pmc/articles/PMC\d+/pdf/[^"]*)"',
                    r'href="(https://pmc\.ncbi\.nlm\.nih\.gov/articles/PMC\d+/pdf/[^"]*)"',
                    # IEEE specific patterns
                    r'href="([^"]*stampPDF[^"]*)"',
                    r'href="([^"]*stamp\.jsp[^"]*arnumber=\d+[^"]*)"',
                    # Button/link text patterns
                    r'<a[^>]*href="([^"]+)"[^>]*>\s*(?:Download\s+)?PDF',
                    r'<a[^>]*href="([^"]+)"[^>]*>\s*Full\s+Text\s+PDF',
                    # Meta tags
                    r'<meta[^>]*name="citation_pdf_url"[^>]*content="([^"]+)"',
                ]

                for pattern in pdf_patterns:
                    matches = re.findall(pattern, html, re.IGNORECASE)
                    for match in matches:
                        pdf_url = match
                        # Make absolute URL if needed
                        if pdf_url.startswith('/'):
                            parsed = urlparse(final_url)
                            pdf_url = f"{parsed.scheme}://{parsed.netloc}{pdf_url}"
                        elif not pdf_url.startswith('http'):
                            continue

                        # Verify it's a PDF
                        try:
                            head_resp = self.session.head(pdf_url, timeout=5, allow_redirects=True)
                            if head_resp.status_code == 200:
                                ct = head_resp.headers.get('content-type', '')
                                if 'pdf' in ct.lower() or pdf_url.endswith('.pdf'):
                                    return head_resp.url
                        except:
                            pass
        except:
            pass

        return None

    def _try_llm_web_search(self, title: str, doi: Optional[str], journal: Optional[str]) -> Optional[str]:
        """Use LLM with web search to find PDF URL as last resort."""
        try:
            from utils.llm import find_pdf_url_with_search

            pdf_url = find_pdf_url_with_search(
                title=title,
                doi=doi,
                journal=journal,
                model=self.web_search_model
            )

            if pdf_url:
                # Verify it's accessible
                self._rate_limit()
                try:
                    response = self.session.head(pdf_url, timeout=10, allow_redirects=True)
                    if response.status_code == 200:
                        return response.url
                except:
                    # Still return the URL even if HEAD fails
                    return pdf_url

        except ImportError:
            pass
        except Exception as e:
            pass

        return None

    def _download_pdf(self, url: str, title: str, method: str, paper_id: str = None) -> Optional[Path]:
        """Download PDF from URL and save to file.

        For web_search method, performs title verification to catch LLM hallucinations.
        """
        self._rate_limit()

        try:
            # Special handling for Lancet - needs proper Referer and may need to visit article first
            if 'thelancet.com' in url and 'showPdf' in url:
                # Extract PII from URL to construct article page URL
                pii_match = re.search(r'pii=([^&]+)', url)
                if pii_match:
                    pii = pii_match.group(1)
                    article_url = f"https://www.thelancet.com/journals/landig/article/{pii}/fulltext"

                    # Visit article page first to get cookies
                    self._rate_limit()
                    self.session.headers.update({'Accept': 'text/html,*/*'})
                    article_resp = self.session.get(article_url, timeout=15, allow_redirects=True)

                    # Set proper headers for PDF request
                    self.session.headers.update({
                        'Referer': article_resp.url,
                        'Accept': 'application/pdf,*/*',
                    })

            response = self.session.get(url, timeout=60, allow_redirects=True)

            # Check for Cloudflare protection
            if self._is_cloudflare_blocked(response):
                # Try Selenium as fallback for Cloudflare-protected sites
                selenium_result = self._try_selenium_download(url, title, paper_id)
                if selenium_result:
                    return selenium_result
                return None

            content_type = response.headers.get('content-type', '')
            if response.status_code == 200:
                # Check content type or file signature
                first_bytes = response.content[:10]
                if b'%PDF' in first_bytes or 'pdf' in content_type.lower():
                    if paper_id:
                        filename = f"{paper_id}_{self._sanitize_filename(title)[:60]}.pdf"
                    else:
                        filename = self._sanitize_filename(title) + f"_{method}.pdf"
                    file_path = self.output_dir / filename

                    with open(file_path, 'wb') as f:
                        f.write(response.content)

                    # Title verification for web_search method to catch LLM hallucinations
                    if method == "web_search":
                        pdf_title = self._extract_title_from_pdf(file_path)
                        is_match, similarity = self._verify_title_match(title, pdf_title)

                        if not is_match:
                            # LLM likely hallucinated - delete the wrong PDF
                            try:
                                file_path.unlink()
                            except:
                                pass
                            return None

                    return file_path
                else:
                    # Got HTML instead of PDF - might be Cloudflare challenge
                    if b'Just a moment' in first_bytes or b'<!DOCTYPE' in first_bytes:
                        # Try Selenium as fallback
                        selenium_result = self._try_selenium_download(url, title, paper_id)
                        if selenium_result:
                            return selenium_result
        except Exception as e:
            pass

        return None

    def _is_bot_blocked(self, response) -> bool:
        """Check if response indicates bot protection (Cloudflare, Akamai, etc.)."""
        if response.status_code == 403:
            headers = response.headers
            content = response.content[:1000]
            content_lower = content.lower()

            # Cloudflare detection
            if 'cf-mitigated' in headers or 'cf-ray' in headers.get('server-timing', ''):
                return True
            if b'just a moment' in content_lower or b'cloudflare' in content_lower:
                return True

            # Akamai detection
            if 'akamai-grn' in headers or 'akamai' in headers.get('server', '').lower():
                return True
            if b'edgesuite.net' in content_lower or b'access denied' in content_lower:
                return True

            # Generic bot protection detection
            if b'robot' in content_lower or b'captcha' in content_lower or b'blocked' in content_lower:
                return True

        return False

    def _is_cloudflare_blocked(self, response) -> bool:
        """Alias for backwards compatibility."""
        return self._is_bot_blocked(response)

    def _try_selenium_download(self, url: str, title: str, paper_id: str = None) -> Optional[Path]:
        """
        Use Selenium to download PDF from Cloudflare-protected sites.
        This bypasses JavaScript challenges that block regular HTTP requests.
        """
        if not SELENIUM_AVAILABLE:
            return None

        driver = None
        try:
            # Setup Chrome with PDF download settings
            chrome_options = ChromeOptions()
            output_dir_str = str(self.output_dir.absolute())

            # Record existing PDFs BEFORE download to detect new files
            existing_pdfs = set()
            if os.path.exists(output_dir_str):
                existing_pdfs = {f for f in os.listdir(output_dir_str) if f.endswith('.pdf')}

            prefs = {
                "download.default_directory": output_dir_str,
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "plugins.always_open_pdf_externally": True,
                "profile.default_content_settings.popups": 0,
            }
            chrome_options.add_experimental_option("prefs", prefs)

            # Run headless
            chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

            driver = webdriver.Chrome(options=chrome_options)
            driver.get(url)

            # Wait for Cloudflare challenge to complete and PDF to download
            time.sleep(15)

            # Look for NEW downloaded PDF (not in existing set)
            for f in os.listdir(output_dir_str):
                if f.endswith('.pdf') and not f.endswith('.crdownload') and f not in existing_pdfs:
                    pdf_path = Path(output_dir_str) / f
                    # Verify it's a real PDF
                    with open(pdf_path, 'rb') as pf:
                        header = pf.read(10)
                        if b'%PDF' in header:
                            # Rename to standard format
                            if paper_id:
                                new_name = f"{paper_id}_{self._sanitize_filename(title)[:60]}.pdf"
                            else:
                                new_name = self._sanitize_filename(title) + "_selenium.pdf"
                            new_path = self.output_dir / new_name

                            # Only rename if different and target doesn't exist
                            if pdf_path.name != new_name:
                                if new_path.exists():
                                    new_path.unlink()  # Remove existing to avoid conflict
                                pdf_path.rename(new_path)
                                return new_path
                            return pdf_path

            return None

        except Exception as e:
            return None
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass

    def download_batch(self, papers: List[Dict], progress_callback=None,
                       progress_file: Optional[str] = None) -> Dict:
        """
        Download PDFs for a batch of papers with real-time saving.

        Args:
            papers: List of paper metadata dicts
            progress_callback: Optional callback(current, total, paper_title)
            progress_file: Optional path to save download progress (JSONL format).
                          Existing progress is not used to skip papers; every run is fresh.

        Returns:
            Dict with statistics and results
        """
        import json

        results = {
            "total": len(papers),
            "success": 0,
            "failed": 0,
            "by_method": {},
            "downloaded": [],
            "failed_papers": []
        }

        already_downloaded = set()

        for i, paper in enumerate(papers):
            # Check if already downloaded
            paper_id = paper.get('id') or paper.get('doi') or paper.get('title')
            if paper_id and paper_id in already_downloaded:
                paper["pdf_downloaded"] = True
                continue

            if progress_callback:
                progress_callback(i + 1, len(papers), paper.get("title", "")[:50])

            success, method, result = self.download(paper)

            if success:
                results["success"] += 1
                results["by_method"][method] = results["by_method"].get(method, 0) + 1
                results["downloaded"].append({
                    "title": paper.get("title"),
                    "method": method,
                    "path": result
                })
                paper["pdf_downloaded"] = True
                paper["pdf_path"] = result
                paper["pdf_method"] = method
            else:
                results["failed"] += 1
                results["failed_papers"].append({
                    "title": paper.get("title"),
                    "doi": paper.get("doi"),
                    "error": result
                })
                paper["pdf_downloaded"] = False

            # Save progress immediately (real-time saving)
            if progress_file:
                try:
                    with open(progress_file, 'a', encoding='utf-8') as f:
                        record = {
                            "id": paper.get("id"),
                            "doi": paper.get("doi"),
                            "title": paper.get("title"),
                            "pdf_downloaded": paper.get("pdf_downloaded", False),
                            "pdf_path": paper.get("pdf_path"),
                            "pdf_method": paper.get("pdf_method")
                        }
                        f.write(json.dumps(record, ensure_ascii=False) + '\n')
                except Exception as e:
                    print(f"  Error saving progress: {e}")

        return results


# Publisher-specific PDF URL helpers

def _elsevier_pdf(url: str, doi: Optional[str]) -> Optional[str]:
    """Get Elsevier/ScienceDirect PDF URL."""
    # Try to extract PII from URL
    pii_match = re.search(r'/pii/([A-Z0-9]+)', url, re.IGNORECASE)
    if pii_match:
        pii = pii_match.group(1)
        return f"https://www.sciencedirect.com/science/article/pii/{pii}/pdfft"
    return None


def _nature_pdf(url: str) -> Optional[str]:
    """Get Nature PDF URL."""
    # Nature URLs like /articles/s41586-021-03819-2
    match = re.search(r'/articles/([^/?]+)', url)
    if match:
        article_id = match.group(1)
        return f"https://www.nature.com/articles/{article_id}.pdf"
    return None


def _oxford_pdf(url: str, doi: Optional[str]) -> Optional[str]:
    """Get Oxford Academic PDF URL."""
    if doi:
        return f"https://academic.oup.com/{doi.split('/')[-1]}/pdf"
    return None


def _ieee_pdf(url: str) -> Optional[str]:
    """Get IEEE PDF URL."""
    # Extract document number
    match = re.search(r'/document/(\d+)', url)
    if match:
        doc_num = match.group(1)
        return f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={doc_num}"
    return None


def _acm_pdf(url: str, doi: Optional[str]) -> Optional[str]:
    """Get ACM Digital Library PDF URL."""
    if doi:
        return f"https://dl.acm.org/doi/pdf/{doi}"
    return None


def _elife_pdf(url: str) -> Optional[str]:
    """Get eLife PDF URL."""
    # eLife URLs like /articles/12345
    match = re.search(r'/articles/(\d+)', url)
    if match:
        article_id = match.group(1)
        return f"https://elifesciences.org/articles/{article_id}.pdf"
    return None


def _cell_pdf(url: str) -> Optional[str]:
    """Get Cell Press PDF URL."""
    # Try to transform URL
    if '/fulltext/' in url:
        return url.replace('/fulltext/', '/pdfExtended/')
    return None


def create_pdf_downloader(email: str = None, output_dir: Path = None,
                          optimized: Optional[bool] = None,
                          semantic_scholar_cache_path: Optional[Path] = None):
    """Create the default Step 3 PDF downloader.

    The optimized downloader is the default production path. Set
    REVIEWPILOT_LEGACY_PDF_DOWNLOADER=1 or pass optimized=False to force the
    original Selenium-capable cascade.
    """
    use_optimized = optimized
    if use_optimized is None:
        use_optimized = os.getenv("REVIEWPILOT_LEGACY_PDF_DOWNLOADER") != "1"
    email = (
        email
        or os.getenv("UNPAYWALL_EMAIL")
        or os.getenv("REVIEWPILOT_API_EMAIL")
        or os.getenv("REVIEWPILOT_EMAIL")
        or ""
    )

    if use_optimized:
        try:
            from utils.fast_pdf_downloader import FastCascadePDFDownloader

            return FastCascadePDFDownloader(
                email=email,
                output_dir=output_dir,
                enable_browser_fallback=True,
                semantic_scholar_cache_path=semantic_scholar_cache_path,
            )
        except Exception as exc:
            if os.getenv("REVIEWPILOT_STRICT_FAST_PDF_DOWNLOADER") == "1":
                raise
            print(f"  Fast PDF downloader unavailable ({type(exc).__name__}: {exc}); using legacy cascade")

    return CascadePDFDownloader(email=email, output_dir=output_dir)


def download_papers_cascade(papers: List[Dict], output_dir: Path, email: str = "research@example.com",
                            progress_callback=None, llm_query_func=None,
                            progress_file: Optional[str] = None,
                            semantic_scholar_cache_path: Optional[Path] = None) -> Dict:
    """
    Convenience function to download PDFs for a list of papers.

    Args:
        papers: List of paper metadata dicts
        output_dir: Directory to save PDFs
        email: Email for API access
        progress_callback: Optional progress callback
        llm_query_func: Optional LLM query function for smart publisher detection
        progress_file: Optional path to save download progress (enables resume)

    Returns:
        Download statistics
    """
    downloader = create_pdf_downloader(
        email=email,
        output_dir=output_dir,
        semantic_scholar_cache_path=semantic_scholar_cache_path,
    )
    if llm_query_func:
        downloader.set_llm_query_func(llm_query_func)
    if os.getenv("REVIEWPILOT_ENABLE_PDF_WEB_SEARCH") == "1":
        downloader.enable_web_search()
    try:
        return downloader.download_batch(papers, progress_callback, progress_file=progress_file)
    finally:
        close = getattr(downloader, "close", None)
        if close:
            close()
