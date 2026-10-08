"""Inclusive date bounds with explicit uncertainty for incomplete metadata."""
from datetime import date
import calendar
import re


def resolve_range(value):
    value = value or {}
    start = str(value.get('start') or value.get('start_date') or '').strip()
    end = str(value.get('end') or value.get('end_date') or date.today().isoformat()).strip()
    # Legacy year-only configurations represent the entire calendar year.
    if re.fullmatch(r'\d{4}', start):
        start += '-01-01'
    if re.fullmatch(r'\d{4}', end):
        end += '-12-31'
    # A month on its own covers the whole month.
    if re.fullmatch(r'\d{4}-\d{2}', start) and 1 <= int(start[5:]) <= 12:
        start += '-01'
    if re.fullmatch(r'\d{4}-\d{2}', end) and 1 <= int(end[5:]) <= 12:
        end += f'-{calendar.monthrange(int(end[:4]), int(end[5:]))[1]:02d}'
    for text in (start, end):
        if not text:
            continue
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
            raise ValueError('Dates must use YYYY-MM-DD.')
        try:
            date.fromisoformat(text)
        except ValueError:
            raise ValueError(f'{text} is not a calendar date. Dates must use YYYY-MM-DD.') from None
    if start and start > end:
        raise ValueError('Start date must not be after end date.')
    return {'start': start, 'end': end}


def assess(paper, bounds):
    bounds = resolve_range(bounds)
    raw = next((str(paper[k]).strip() for k in ('publication_date', 'published', 'date', 'year') if paper.get(k)), '')
    match = re.match(r'^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?(?:$|T|\s)', raw)
    try:
        if not match:
            raise ValueError()
        year, month, day = (int(v) if v else None for v in match.groups())
        low_month = month if month is not None else 1
        high_month = month if month is not None else 12
        low = date(year, low_month, day if day is not None else 1).isoformat()
        high = date(year, high_month, day if day is not None else calendar.monthrange(year, high_month)[1]).isoformat()
    except (ValueError, TypeError):
        return {'excluded': False, 'needs_review': True, 'precision': 'unknown', 'reason': 'Publication date is missing or invalid; retained for date review.', 'bounds': bounds}
    excluded = high < bounds['start'] or low > bounds['end']
    uncertain = not excluded and (low < bounds['start'] or high > bounds['end'])
    return {'excluded': excluded, 'needs_review': uncertain, 'precision': 'day' if day else 'month' if month else 'year',
            'earliest': low, 'latest': high, 'bounds': bounds,
            'reason': 'Publication date is outside the inclusive review range.' if excluded else 'Incomplete publication date overlaps a review boundary; retained for date review.' if uncertain else 'Publication date falls within the review range.'}
