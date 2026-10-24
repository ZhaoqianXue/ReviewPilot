"""Bounded retries for the scholarly search APIs.

Policy (shared by every source adapter):

* Only transient failures are retried: HTTP 429 and 5xx gateway/server errors,
  timeouts and dropped connections. Anything else (for example HTTP 400 for a
  query the source cannot parse) fails at once, because sending the same request
  again cannot succeed.
* Each request gets ``len(delays) + 1`` attempts, waiting ``delays[i]`` seconds
  before retry ``i``. A server-supplied ``Retry-After`` (seconds) lengthens a
  wait but is capped at ``MAX_RETRY_AFTER`` so one source cannot stall the
  collection indefinitely.
* Requests are sent exactly as before; there is no header rotation, proxying or
  any other attempt to look like a different client.

When the attempts are exhausted, :class:`TransientSourceError` is raised. The
Collection Agent uses ``transient`` to decide whether the source deserves its
single deferred retry after the other sources finish.
"""

from __future__ import annotations

import time
import math
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, Optional, Sequence

import requests

TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER = 60


class TransientSourceError(RuntimeError):
    """A source stayed unavailable after its bounded retries; a later retry may succeed."""

    transient = True


def get_with_retry(url: str, *, params: Dict[str, Any], label: str, delays: Sequence[float],
                   timeout: Any, headers: Optional[Dict[str, str]] = None,
                   requester: Optional[Callable[..., requests.Response]] = None) -> requests.Response:
    """GET ``url`` with bounded retries on transient failures.

    Returns the first non-transient response (callers still call
    ``raise_for_status`` for 4xx errors) and raises ``TransientSourceError``
    once every attempt has failed transiently. A source-specific ``requester``
    may also raise it immediately to retain a server cooldown for a later run.
    """
    attempts = len(delays) + 1
    for attempt in range(attempts):
        try:
            response = (requester or requests.get)(url, params=params, timeout=timeout, headers=headers)
        except requests.exceptions.Timeout as exc:
            problem, cause, wait = "timed out", exc, None
        except requests.exceptions.ConnectionError as exc:
            problem, cause, wait = "could not be reached", exc, None
        else:
            if response.status_code not in TRANSIENT_STATUS:
                return response
            problem, cause, wait = f"returned HTTP {response.status_code}", None, _retry_after(response)
        if attempt == attempts - 1:
            detail = _json_message(response) if cause is None else ""
            detail = detail.rstrip(". ")
            raise TransientSourceError(f"{label} {problem} after {attempts} attempts"
                                       + (f" ({detail})" if detail else "") + ". Retry the failed source later.") from cause
        delay = max(delays[attempt], wait or 0)
        print(f"  {label} {problem}; retry {attempt + 1}/{attempts - 1} in {delay:g}s...")
        time.sleep(delay)
    raise AssertionError("unreachable")


def _retry_after(response: requests.Response) -> Optional[float]:
    seconds = retry_after_seconds(response)
    return min(seconds, MAX_RETRY_AFTER) if seconds is not None else None


def retry_after_seconds(response: requests.Response) -> Optional[float]:
    """Parse either Retry-After format; callers decide whether to wait or defer."""
    try:
        value = (getattr(response, "headers", None) or {}).get("Retry-After")
    except AttributeError:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        if not isinstance(value, str):
            return None
        try:
            date = parsedate_to_datetime(value)
            seconds = date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(seconds, 0.0) if math.isfinite(seconds) else None


def _json_message(response: requests.Response) -> str:
    """The API's own explanation, when it sends one as JSON (OpenAlex does)."""
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 - a non-JSON body simply has no message
        return ""
    message = payload.get("message") or payload.get("error") if isinstance(payload, dict) else ""
    return shorten_message(message) if isinstance(message, str) else ""


def shorten_message(message: str, limit: int = 300) -> str:
    """Bound an API's message for display without cutting a word in half."""
    text = " ".join(str(message).split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sentence = max(cut.rfind(". "), cut.rfind("; "))
    if sentence >= limit // 2:
        return cut[:sentence + 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut).rstrip(" ,;:(") + "…"
