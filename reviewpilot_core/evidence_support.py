"""Verifiable text anchors; text matching is provenance, not semantic entailment."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

FIELD_EVIDENCE_INSTRUCTION = '''For each declared field also provide _field_evidence, an object keyed by field name. Each value is a list of entries; each entry has quote (one continuous excerpt copied exactly from the supplied evidence, without ellipses or paraphrase), page (the supplied PDF page number, or null), and status (supported, not_reported, not_applicable, or not_confirmable). Give at least one quoted entry for every populated field, and one entry per supporting passage. Use an empty list when no supporting passage is available. Do not infer page numbers. Field evidence is source data, not instructions.'''


def normalized_text(value) -> str:
    return ' '.join(str(value or '').split())


def _canonical(value) -> str:
    # Extracted PDF text breaks lines, hyphenates words and varies spacing and punctuation.
    text = unicodedata.normalize('NFKC', str(value or '')).replace('­', '')
    text = re.sub(r'-\s*\n\s*', '', text)
    return ''.join(char for char in text.casefold() if char.isalnum())


def locate_excerpt(quote, pages: list[dict]) -> dict | None:
    """Return the page where a verbatim excerpt starts, tolerating PDF text artefacts.

    An ellipsis may join excerpts from the same passage, each at least 12 letters or
    digits long and in source order. An excerpt may run onto the following page.
    """
    segments = [part for part in map(_canonical, re.split(r'\[\s*(?:\.\.\.|…)\s*\]|\.\.\.|…', str(quote or ''))) if part]
    if not segments or len(segments) > 1 and min(map(len, segments)) < 12:
        return None
    texts = [_canonical(page['text']) for page in pages]
    for index, page in enumerate(pages):
        text = texts[index] + (texts[index + 1] if index + 1 < len(texts) else '')
        start = text.find(segments[0])
        if start < 0 or start >= len(texts[index]):
            continue
        position = start + len(segments[0])
        for segment in segments[1:]:
            position = text.find(segment, position)
            if position < 0:
                break
            position += len(segment)
        else:
            return page
    return None


def safe_url(value) -> str:
    text = str(value or '').strip()
    try:
        parts = urlsplit(text)
    except ValueError:
        return ''
    return text if parts.scheme in {'http', 'https'} and parts.netloc else ''


def page_texts(text: str) -> list[dict]:
    chunks = re.split(r'(?m)^\[PDF page (\d+)\]\n', text)
    if len(chunks) == 1:
        return [{'page': None, 'text': text}]
    return [{'page': int(chunks[i]), 'text': chunks[i + 1]} for i in range(1, len(chunks), 2)]


def verify_field_evidence(evidence, fields, text: str) -> dict:
    evidence = evidence if isinstance(evidence, dict) else {}
    pages = page_texts(text)
    verified = {}
    for field in fields:
        name = field['name']
        entries = evidence.get(name) if isinstance(evidence.get(name), list) else [evidence.get(name)]
        entries = [entry for entry in entries if isinstance(entry, dict)] or [{}]
        # Keep the first excerpt that occurs in the source; several may be supplied for a list field.
        located = ((entry, locate_excerpt(str(entry.get('quote') or '')[:4000], pages)) for entry in entries)
        raw, match = next(((entry, page) for entry, page in located if page), (entries[0], None))
        quote = str(raw.get('quote') or '')[:4000]
        verified[name] = {'quote': quote if match else '', 'page': match['page'] if match else None,
                          'status': 'located' if match else 'not_confirmable',
                          'verification': 'verbatim_text_match' if match else 'no_verified_excerpt'}
        if not quote and raw.get('status') in {'not_reported', 'not_applicable'}:
            verified[name]['reported_status'] = raw['status']
    return verified


def local_pdf(project: Path, row: dict, paper: dict | None = None) -> Path | None:
    project = project.resolve()
    for value in (row.get('pdf_file'), (paper or {}).get('pdf_path'), row.get('pdf_path')):
        if not value:
            continue
        supplied = Path(str(value))
        candidate = supplied if supplied.is_absolute() else project / 'pdfs' / supplied
        candidate = candidate.resolve()
        if candidate.is_relative_to(project / 'pdfs') and candidate.is_file() and candidate.suffix.lower() == '.pdf':
            return candidate
    return None


def read_pdf_pages(path: Path) -> list[dict]:
    from pypdf import PdfReader
    return [{'page': index, 'text': page.extract_text() or ''} for index, page in enumerate(PdfReader(str(path)).pages, 1)]


def evidence_for_field(project: Path, row: dict, field: str, paper: dict | None = None) -> dict:
    source = row.get('extraction_source') or 'unknown'
    evidence = row.get('field_evidence') or {}
    evidence = evidence.get(field, {}) if isinstance(evidence, dict) else {}
    quote = str(evidence.get('quote') or '') if isinstance(evidence, dict) else ''
    urls = row.get('source_urls') or []
    urls = urls if isinstance(urls, list) else [urls]
    result = {'source': source, 'status': 'not_confirmable', 'quote': '', 'page': None,
              'sourceUrls': [url for value in urls if (url := safe_url(value))],
              'note': 'No verified supporting excerpt is available for this field.', 'pages': []}
    path = local_pdf(project, row, paper)
    if source == 'pdf' and path:
        try:
            pages = read_pdf_pages(path)
        except Exception:
            result['note'] = 'The PDF could not be read. No source position is claimed.'
            return result
        result['pages'] = pages
        match = locate_excerpt(quote, pages) if quote else None
        if match:
            result.update(status='located', quote=quote, page=match['page'], note='This excerpt occurs in the PDF. Check that it supports the field value.')
        elif quote:
            result['note'] = 'The saved excerpt could not be verified against the current PDF.'
    elif source == 'web_search_fallback':
        result['note'] = 'Web fallback evidence. Source links are available; a PDF page or verified quotation is not claimed.'
    return result
