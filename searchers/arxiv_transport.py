"""Daily Atom page cache and a filesystem-shared arXiv request gate.

All processes using the same state directory share one connection, pacing and
cooldown. Multiple hosts must mount that directory on a filesystem supporting
shared advisory locks, or use an external coordinator. No IP/header rotation.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import requests

from reviewpilot_core.atomic_files import atomic_write_json
from searchers.http_retry import TransientSourceError, retry_after_seconds

ARXIV_API_URL = "https://export.arxiv.org/api/query"
MIN_INTERVAL = 3.0
DEFAULT_COOLDOWN = 60.0


def _now():
    return time.time()


def _pause(seconds):
    time.sleep(seconds)


def utc_day(timestamp=None):
    return datetime.fromtimestamp(_now() if timestamp is None else timestamp, timezone.utc).date().isoformat()


def state_directory():
    directory = Path(os.environ.get("REVIEWPILOT_ARXIV_STATE_DIR") or Path(__file__).resolve().parents[1] / ".cache/arxiv")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@contextmanager
def file_lock(path, *, blocking=True):
    """Separate file descriptors serialize threads as well as separate processes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_object(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


class ArxivDeferredError(TransientSourceError):
    def __init__(self, retry_at, status=429, *, request_sent=False):
        self.retry_at = retry_at
        self.status_code = status
        self.request_sent = request_sent
        when = datetime.fromtimestamp(retry_at, timezone.utc).isoformat(timespec="seconds")
        super().__init__(f"arXiv returned HTTP {status}; collection is incomplete. Retry no earlier than {when}.")


def _valid_feed(text):
    if not isinstance(text, str):
        return False
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return False
    atom = "{http://www.w3.org/2005/Atom}"
    return root.tag == atom + "feed" and not any(
        "/api/errors" in (entry.findtext(atom + "id") or "") for entry in root.findall(atom + "entry"))


def _cached_response(path, url):
    item = read_object(path)
    if item.get("day") != utc_day() or not _valid_feed(item.get("text")):
        return None
    response = requests.Response()
    response.status_code = 200
    response.url = url
    response._content = item["text"].encode("utf-8")
    response.encoding = "utf-8"
    response.headers["Content-Type"] = "application/atom+xml"
    response.from_cache = True
    response.retrieved_at = item.get("retrieved_at")
    return response


def arxiv_get(url=ARXIV_API_URL, *, session=None, **kwargs):
    """Return a cached successful page or make one globally paced GET attempt.

    429 and server-requested cooldowns are persisted and raised immediately so
    the collection can continue with other sources and schedule a later retry.
    """
    directory = state_directory()
    key = hashlib.sha256(json.dumps([url, kwargs.get("params") or {}], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    cache_path = directory / "pages" / f"{key}.json"
    cached = _cached_response(cache_path, url)
    if cached is not None:
        return cached
    with file_lock(directory / "request.lock"):
        # Coalesce identical concurrent requests after the first one populated the cache.
        cached = _cached_response(cache_path, url)
        if cached is not None:
            return cached
        state_path = directory / "request.json"
        state = read_object(state_path)
        cooldown = state.get("retry_at", 0)
        if isinstance(cooldown, (int, float)) and cooldown > _now():
            raise ArxivDeferredError(cooldown, state.get("status", 429))
        wait = state.get("ready_at", 0) - _now()
        if wait > 0:
            _pause(wait)
        try:
            response = (session.get if session is not None else requests.get)(url, **kwargs)
        finally:
            # requests reads the body before returning; a slow response cannot
            # overlap another connection, including after a network exception.
            state["ready_at"] = _now() + MIN_INTERVAL
            atomic_write_json(state_path, state)
        server_wait = retry_after_seconds(response)
        if response.status_code == 429 or (response.status_code == 503 and server_wait is not None):
            state.update(status=response.status_code, retry_at=_now() + max(
                MIN_INTERVAL, server_wait if server_wait is not None else DEFAULT_COOLDOWN))
            atomic_write_json(state_path, state)
            raise ArxivDeferredError(state["retry_at"], response.status_code, request_sent=True)
        if response.status_code == 200 and _valid_feed(response.text):
            atomic_write_json(cache_path, {"day": utc_day(), "retrieved_at": _now(), "text": response.text})
        return response
