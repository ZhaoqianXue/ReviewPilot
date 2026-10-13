"""Manual retrieval handoff: PDFs the user obtains for papers the downloader could not get.

The user opens a paper's link, saves the PDF, and ReviewPilot notices it (download-folder watch),
or the user drops/chooses files (upload), or says which paper a file belongs to (manual assignment).
Files are matched with the downloader's own identity gate, only against papers not yet obtained,
and imported through the retrieval-retry transaction. Files are read locally and never sent anywhere;
the user's original file is copied, never moved or changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any, Callable, Iterable
from urllib.parse import quote, urlencode, urlparse

from .atomic_files import atomic_write_json, atomic_write_jsonl
from .paper_links import doi as _doi, paper_links as record_links, pmid as _pmid, publisher_url as _publisher_url
from .project_store import read_json
from .retrieval_retry import (
    USER_DOWNLOAD_METHOD, RetryPreparation, current_retry_snapshot, is_user_provided, prepare_retry_request,
)
from .safe_text import safe_display_text


SOURCES = ("download_watch", "upload", "manual_assignment")
SOURCE_LABELS = {"download_watch": "saved to your download folder", "upload": "added by you",
                 "manual_assignment": "assigned by you"}
BASIS_LABELS = {"doi_on_page": "DOI on the first pages", "title": "title on the first page",
                "user_choice": "your choice"}
SETTINGS_FILE = Path(".app_settings") / "retrieval.json"
# assess_without_full_text: papers left without a PDF are extracted from their abstract and allowed web pages.
# A minor, opt-in feature; off unless the user turns it on.
DEFAULT_SETTINGS = {"download_folder": "~/Downloads", "watch_enabled": True, "library_resolver": "",
                    "assess_without_full_text": False}
STATE_DIR = "manual_retrieval"
AWAIT_SECONDS = 30 * 60          # a link click waits this long for its download
CLICK_SLACK_SECONDS = 2.0        # clock skew between the click request and the file system
STABLE_SECONDS = 1.0             # a file's size must stay unchanged this long before it is read
MAX_PDF_BYTES = 200 * 1024 * 1024
PARTIAL_SUFFIXES = (".crdownload", ".part", ".partial", ".download", ".tmp")
_RECENT_LIMIT = 8
_SEEN_LIMIT = 500


class ManualRetrievalError(ValueError):
    """A request the user can correct; the message is shown in the canvas."""


# ---------------------------------------------------------------- settings (application level)

def load_settings(output_root: Path | str) -> dict[str, Any]:
    stored = read_json(Path(output_root) / SETTINGS_FILE, {}) or {}
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(stored, dict):
        if isinstance(stored.get("download_folder"), str) and stored["download_folder"].strip():
            settings["download_folder"] = stored["download_folder"].strip()
        if isinstance(stored.get("watch_enabled"), bool):
            settings["watch_enabled"] = stored["watch_enabled"]
        if isinstance(stored.get("assess_without_full_text"), bool):
            settings["assess_without_full_text"] = stored["assess_without_full_text"]
        if isinstance(stored.get("library_resolver"), str):
            try:
                settings["library_resolver"] = _validate_resolver(stored["library_resolver"])
            except ManualRetrievalError:
                settings["library_resolver"] = ""
    return settings


def save_settings(output_root: Path | str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload or set(payload) - set(DEFAULT_SETTINGS):
        raise ManualRetrievalError("Settings must name download_folder, watch_enabled, library_resolver or assess_without_full_text.")
    settings = load_settings(output_root)
    if "download_folder" in payload:
        folder = payload["download_folder"]
        if not isinstance(folder, str) or not folder.strip() or len(folder) > 1000:
            raise ManualRetrievalError("Enter the folder your browser saves downloads to.")
        if not Path(os.path.expanduser(folder.strip())).is_absolute():
            raise ManualRetrievalError("Use a full folder path, for example ~/Downloads.")
        settings["download_folder"] = folder.strip()
    if "watch_enabled" in payload:
        if not isinstance(payload["watch_enabled"], bool):
            raise ManualRetrievalError("Watching must be on or off.")
        settings["watch_enabled"] = payload["watch_enabled"]
    if "assess_without_full_text" in payload:
        if not isinstance(payload["assess_without_full_text"], bool):
            raise ManualRetrievalError("Assessing papers without full text must be on or off.")
        settings["assess_without_full_text"] = payload["assess_without_full_text"]
    if "library_resolver" in payload:
        if not isinstance(payload["library_resolver"], str):
            raise ManualRetrievalError("The library link must be a web address.")
        settings["library_resolver"] = _validate_resolver(payload["library_resolver"])
    atomic_write_json(Path(output_root) / SETTINGS_FILE, settings)
    return settings


def settings_projection(output_root: Path | str) -> dict[str, Any]:
    settings = load_settings(output_root)
    folder = download_folder(settings)
    return {
        "downloadFolder": settings["download_folder"],
        "watchEnabled": settings["watch_enabled"],
        "libraryResolver": settings["library_resolver"],
        "assessWithoutFullText": settings["assess_without_full_text"],
        "folderAvailable": folder.is_dir() and os.access(folder, os.R_OK),
    }


def download_folder(settings: dict[str, Any]) -> Path:
    return Path(os.path.expanduser(settings["download_folder"]))


def _validate_resolver(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if (len(text) > 1000 or re.search(r"\s", text) or parsed.scheme not in {"http", "https"}
            or not parsed.hostname):
        raise ManualRetrievalError("The library link must start with https:// (an OpenURL resolver or EZproxy prefix).")
    if parsed.username or parsed.password:
        raise ManualRetrievalError("Remove the user name and password from the library link; ReviewPilot never stores library credentials.")
    return text


# ---------------------------------------------------------------- failure reasons and links

REASONS = {
    "paywalled": "Paywalled (no open copy found)",
    "blocked": "Publisher site blocks automated download",
    "not_article": "Not an article record",
    "not_found": "No full text found",
}
_PAYWALLED = {"publisher_paywalled", "article_print_paywalled", "pmc_not_open_access", "pmc_not_in_oa_cloud",
              "metered_budget_exhausted", "subscribed_unavailable"}
_BLOCKED = {"pdf_endpoint_cloudflare", "pdf_endpoint_waf", "pdf_endpoint_tdm_blocked", "antibot_challenge",
            "pmc_recaptcha", "pmc_pow_challenge", "repository_bot_check", "access_denied", "access_blocked",
            "rate_limited", "metadata_api_429", "domain_cooldown_skip"}
_NON_ARTICLE_TYPES = {"erratum", "correction", "retraction", "editorial", "paratext", "dataset", "peer-review",
                      "supplementary-materials", "letter", "comment", "other"}
_NON_ARTICLE_TITLE = re.compile(r"^\s*(correction|erratum|corrigendum|retraction|retracted|editorial|"
                                r"expression of concern|withdrawn)\b", re.IGNORECASE)


def failure_reason(row: dict[str, Any], detail: dict[str, Any]) -> tuple[str, str]:
    """Plain-words reason for a paper not obtained, from the downloader's failure classes.

    The primary class decides; the secondary classes recorded along the way only fill in when the
    primary one says nothing about access (for example a title mismatch after a challenge page).
    """
    primary = next((value.strip().casefold() for value in (row.get("pdf_failure_class"), detail.get("failure_class"),
                    detail.get("pdf_failure_class")) if isinstance(value, str) and value.strip()), "")
    secondary = []
    for value in (row.get("pdf_failure_classes"), detail.get("failure_classes")):
        if isinstance(value, list):
            secondary.extend(item.strip().casefold() for item in value if isinstance(item, str) and item.strip())
    record_type = str(row.get("type") or row.get("work_type") or row.get("publication_type") or "").strip().casefold()
    if (record_type in _NON_ARTICLE_TYPES or _NON_ARTICLE_TITLE.match(str(row.get("title") or ""))
            or (primary == "no_downloadable_pdf" and not (row.get("doi") or row.get("url")))):
        code = "not_article"
    else:
        code = _access_code(primary)
        if code is None:
            codes = {_access_code(item) for item in secondary}
            code = ("paywalled" if "paywalled" in codes or row.get("retrieval_status") == "subscribed_unavailable"
                    else "blocked" if "blocked" in codes else "not_found")
    return code, REASONS[code]


def _access_code(failure_class: str) -> str | None:
    if failure_class in _PAYWALLED or "paywall" in failure_class:
        return "paywalled"
    if failure_class in _BLOCKED or "challenge" in failure_class or "cloudflare" in failure_class:
        return "blocked"
    return None


def paper_links(row: dict[str, Any], library_resolver: str = "") -> list[dict[str, str]]:
    """The record links shown everywhere else (reviewpilot_core.paper_links), plus the user's library link."""
    links = record_links(row)
    library = library_link(row, library_resolver)
    if library:
        links.append({"kind": "library", "label": "Open with my library", "url": library})
    return links


def library_link(row: dict[str, Any], resolver: str) -> str:
    """Link through the user's library: an EZproxy prefix wraps the paper's URL, an OpenURL base gets its citation."""
    if not resolver:
        return ""
    doi, pmid = _doi(row), _pmid(row)
    if re.search(r"(^|[?&])q?url=$", resolver, re.IGNORECASE):
        # Without a DOI only the paper's own landing page is worth proxying; an index page (OpenAlex, PubMed) is not.
        target = f"https://doi.org/{doi}" if doi else _publisher_url(row)
        return resolver + quote(target, safe="") if target else ""
    params = {"url_ver": "Z39.88-2004", "rft_val_fmt": "info:ofi/fmt:kev:mtx:journal", "genre": "article"}
    if doi:
        params["rft_id"] = f"info:doi/{doi}"
    elif pmid:
        params["rft_id"] = f"info:pmid/{pmid}"
    for key, value in (("rft.atitle", row.get("title")), ("rft.jtitle", row.get("journal")), ("rft.date", row.get("year"))):
        if isinstance(value, (str, int)) and str(value).strip():
            params[key] = str(value).strip()[:300]
    if "rft_id" not in params and "rft.atitle" not in params:
        return ""
    return resolver + ("&" if "?" in resolver else "?") + urlencode(params)


# ---------------------------------------------------------------- per-project state

_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(project: Path) -> threading.RLock:
    key = Path(project).resolve()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _state_path(project: Path) -> Path:
    return Path(project) / STATE_DIR / "state.json"


def load_state(project: Path) -> dict[str, Any]:
    state = read_json(_state_path(project), {}) or {}
    if not isinstance(state, dict):
        state = {}
    awaiting = state.get("awaiting") if isinstance(state.get("awaiting"), dict) else {}
    return {
        "awaiting": {key: value for key, value in awaiting.items()
                     if isinstance(value, dict) and isinstance(value.get("clicked_at"), (int, float))},
        "pending": [item for item in state.get("pending") or [] if isinstance(item, dict) and item.get("id")],
        "imported": [item for item in state.get("imported") or [] if isinstance(item, dict)],
        "seen": [item for item in state.get("seen") or [] if isinstance(item, str)][-_SEEN_LIMIT:],
    }


def _save_state(project: Path, state: dict[str, Any]) -> None:
    state["seen"] = state["seen"][-_SEEN_LIMIT:]
    atomic_write_json(_state_path(project), state)


def _failure_rows(project: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Papers not obtained, keyed by retry id, with their included row and report detail."""
    try:
        snapshot = current_retry_snapshot(project)
    except (OSError, TypeError, ValueError):
        return {}, []
    report, included = _thaw(snapshot.report), _thaw(snapshot.included)
    failures = {}
    for item in snapshot.items:
        failures[item.retry_id] = {"label": item.label, "row": included[item.included_index],
                                   "detail": report["failed_papers"][item.report_index]}
    return failures, included


def _thaw(value: Any) -> Any:
    if hasattr(value, "items"):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def mark_awaiting(project: Path, retry_id: str, awaiting: bool = True, *, now: float | None = None) -> None:
    project = Path(project)
    failures, _ = _failure_rows(project)
    if awaiting and retry_id not in failures:
        raise ManualRetrievalError("This paper is no longer missing its full text.")
    with _lock(project):
        state = load_state(project)
        if awaiting:
            state["awaiting"][retry_id] = {"clicked_at": time.time() if now is None else now}
        else:
            state["awaiting"].pop(retry_id, None)
        _save_state(project, state)


def active_awaiting(project: Path, *, now: float | None = None) -> dict[str, float]:
    """Awaiting papers still missing their PDF and still inside their waiting window (prunes the rest)."""
    project = Path(project)
    now = time.time() if now is None else now
    failures, _ = _failure_rows(project)
    with _lock(project):
        state = load_state(project)
        active = {rid: value["clicked_at"] for rid, value in state["awaiting"].items()
                  if rid in failures and now - value["clicked_at"] < AWAIT_SECONDS}
        if len(active) != len(state["awaiting"]):
            state["awaiting"] = {rid: {"clicked_at": clicked} for rid, clicked in active.items()}
            _save_state(project, state)
    return active


def record_import(project: Path, retry_id: str, *, label: str, source: str, basis: str, file_name: str,
                  sha256: str) -> None:
    with _lock(project):
        state = load_state(project)
        state["awaiting"].pop(retry_id, None)
        state["imported"].append({"retry_id": retry_id, "label": label, "source": source, "basis": basis,
                                  "file_name": file_name, "sha256": sha256,
                                  "at": datetime.now().isoformat(timespec="seconds")})
        state["imported"] = state["imported"][-50:]
        resolved = [item for item in state["pending"] if item.get("sha256") == sha256]
        state["pending"] = [item for item in state["pending"] if item.get("sha256") != sha256]
        _save_state(project, state)
    for item in resolved:
        stored = _inbox_file(project, item)
        if stored is not None:
            stored.unlink(missing_ok=True)


def imported_hashes(project: Path) -> dict[str, str]:
    return {item.get("sha256"): item.get("label", "") for item in load_state(project)["imported"] if item.get("sha256")}


def add_pending(project: Path, entry: dict[str, Any], *, seen_key: str | None = None) -> dict[str, Any]:
    entry = {"id": secrets.token_hex(8), "detected_at": datetime.now().isoformat(timespec="seconds"), **entry}
    with _lock(project):
        state = load_state(project)
        state["pending"] = [item for item in state["pending"] if item.get("sha256") != entry.get("sha256")]
        state["pending"].append(entry)
        if seen_key:
            state["seen"].append(seen_key)
        _save_state(project, state)
    return entry


def pending_entry(project: Path, pending_id: str) -> dict[str, Any]:
    entry = next((item for item in load_state(project)["pending"] if item.get("id") == pending_id), None)
    if entry is None:
        raise ManualRetrievalError("That file is no longer waiting for a decision.")
    return entry


def remove_pending(project: Path, pending_id: str) -> None:
    with _lock(project):
        state = load_state(project)
        removed = [item for item in state["pending"] if item.get("id") == pending_id]
        state["pending"] = [item for item in state["pending"] if item.get("id") != pending_id]
        _save_state(project, state)
    for item in removed:
        stored = _inbox_file(project, item)
        if stored is not None:
            stored.unlink(missing_ok=True)


def mark_seen(project: Path, key: str) -> None:
    with _lock(project):
        state = load_state(project)
        state["seen"].append(key)
        _save_state(project, state)


def store_in_inbox(project: Path, content: bytes) -> str:
    """Keep an uploaded file that needs a decision inside the project (never outside it)."""
    inbox = Path(project) / STATE_DIR / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_hex(12)}.pdf"
    (inbox / name).write_bytes(content)
    return name


def _inbox_file(project: Path, entry: dict[str, Any]) -> Path | None:
    name = entry.get("stored")
    if not isinstance(name, str) or not re.fullmatch(r"[0-9a-f]{24}\.pdf", name):
        return None
    return Path(project) / STATE_DIR / "inbox" / name


def pending_file(project: Path, entry: dict[str, Any]) -> Path:
    """The file a pending decision refers to, checked to be the same bytes the user saw."""
    path = _inbox_file(project, entry)
    if path is None:
        path = Path(str(entry.get("path") or ""))
    try:
        content = path.read_bytes() if path.is_file() and not path.is_symlink() else b""
    except OSError:
        content = b""
    if not content or hashlib.sha256(content).hexdigest() != entry.get("sha256"):
        raise ManualRetrievalError(f"{entry.get('file_name') or 'The file'} was moved or changed. Drop it into this view instead.")
    return path


# ---------------------------------------------------------------- matching (the downloader's identity gate)

@dataclass(frozen=True)
class Candidate:
    retry_id: str
    title: str
    doi: str
    label: str


@dataclass
class MatchOutcome:
    status: str                      # matched | ambiguous | weak | no_match | no_text | supplement | invalid
    retry_id: str = ""
    basis: str = ""
    candidates: list[dict[str, str]] = field(default_factory=list)


def is_pdf(content: bytes) -> bool:
    return content[:1024].lstrip().startswith(b"%PDF-")


def candidates_for(project: Path, retry_ids: Iterable[str] | None = None) -> list[Candidate]:
    failures, _ = _failure_rows(project)
    wanted = set(failures) if retry_ids is None else set(retry_ids) & set(failures)
    return [Candidate(rid, str(info["row"].get("title") or ""), _doi(info["row"]), info["label"])
            for rid, info in failures.items() if rid in wanted]


def match_pdf(content: bytes, candidates: list[Candidate], all_titles: Iterable[str] = ()) -> MatchOutcome:
    """Match one file against papers not yet obtained with the downloader's identity gate.

    The gate accepts a file when pages 1-2 carry the paper's DOI, or the title's distinctive words
    appear at the top of page 1 (page 2 behind a cover sheet); it rejects supplementary files.
    Only a unique confident match is imported without asking.
    """
    if not is_pdf(content):
        return MatchOutcome("invalid")
    from utils.fast_pdf_downloader import (
        UserFileIdentityGate, batch_generic_title_tokens, front_page_is_supplement, title_match_tokens,
    )

    titles = [title for title in all_titles if title]
    gate = UserFileIdentityGate(batch_generic_title_tokens(titles) if titles else None)
    pages = gate._extract_pdf_pages(content)
    if not any(title_match_tokens(page) for page in pages):
        return MatchOutcome("no_text")
    head = "\n".join(pages[:2])
    compact_head = re.sub(r"\s+", "", head.lower())
    compact_fronts = [re.sub(r"[^a-z0-9]+", "", page[:1800].lower()) for page in pages[:2]]
    # The gate checks one paper at a time, so a related paper whose title words also appear on
    # page 1 can pass too. Rank what it accepts: the paper's DOI printed on pages 1-2, then its
    # full title verbatim at the top, then any other acceptance. Only a single strongest match is
    # imported without asking.
    matches = []
    for candidate in candidates:
        accepted, _detail = gate.for_paper(candidate.doi)._pdf_title_match_result(content, candidate.title)
        if not accepted:
            continue
        compact_title = re.sub(r"[^a-z0-9]+", "", candidate.title.lower())
        if len(candidate.doi) >= 10 and candidate.doi in compact_head:
            tier = 0
        elif len(compact_title) >= 24 and any(compact_title in front for front in compact_fronts):
            tier = 1
        else:
            tier = 2 if len(title_match_tokens(candidate.title)) >= 3 else 3
        matches.append((tier, candidate))
    matches.sort(key=lambda item: item[0])
    found = [{"retry_id": candidate.retry_id, "label": candidate.label, "basis": "doi_on_page" if tier == 0 else "title"}
             for tier, candidate in matches]
    if not matches:
        return MatchOutcome("supplement" if front_page_is_supplement(pages[0] if pages else "") else "no_match")
    best = [candidate for tier, candidate in matches if tier == matches[0][0]]
    if len(best) > 1:
        return MatchOutcome("ambiguous", candidates=found)
    if matches[0][0] == 3 or (matches[0][0] == 2 and len(matches) > 1):
        return MatchOutcome("weak" if len(matches) == 1 else "ambiguous", candidates=found)
    return MatchOutcome("matched", best[0].retry_id, found[0]["basis"], found)


PENDING_REASONS = {
    "ambiguous": "Matches more than one paper. Choose the right one.",
    "weak": "Possibly this paper, but the match is not certain. Confirm or choose another.",
    "no_match": "Did not match any paper still missing its full text.",
    "no_text": "Has no readable text (a scan?), so it cannot be matched. Choose its paper.",
    "supplement": "Looks like a supplementary file, not the article.",
}


# ---------------------------------------------------------------- import through the retry transaction

def prepare_import(project: Path, retry_id: str) -> RetryPreparation:
    """Freeze a one-paper retry selection for a file the user provided."""
    snapshot = current_retry_snapshot(project)
    if retry_id not in {item.retry_id for item in snapshot.items}:
        raise ManualRetrievalError("This paper is no longer missing its full text.")
    ids = [retry_id]
    return prepare_retry_request(project, {
        "failed_ids": ids, "report_revision": snapshot.report_revision,
        "retry_confirmation": {"expected_report_revision": snapshot.report_revision, "failed_ids": ids},
    })


def staged_import(content: bytes, *, source: str, evidence: dict[str, Any]) -> Callable[[Path, str], dict]:
    """A staging step that writes a copy of the user's file where the retry transaction expects a download."""
    if source not in SOURCES:
        raise ValueError("Unknown user PDF source")
    if not is_pdf(content):
        raise ManualRetrievalError("The file is not a PDF.")

    def run(root: Path | str, project_id: str) -> dict[str, Any]:
        staged = Path(root) / project_id
        rows_path = staged / "filtered" / "included_papers.jsonl"
        rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(rows) != 1:
            raise ValueError("A user PDF is imported for exactly one paper")
        row = rows[0]
        stem = re.sub(r"\s+", "_", re.sub(r"[^\w\s-]", "", str(row.get("title") or "").lower())).strip("_")[:80]
        destination = staged / "pdfs" / f"{stem or 'paper'}_{USER_DOWNLOAD_METHOD}.pdf"
        destination.write_bytes(content)
        provenance = {"pdf_method": USER_DOWNLOAD_METHOD, "pdf_source": source, "pdf_match_evidence": evidence}
        row.update(pdf_downloaded=True, pdf_path=str(destination), retrieval_status="downloaded", **provenance)
        detail = {key: row[key] for key in ("id", "title", "doi", "url") if key in row}
        detail.update(path=str(destination), method=USER_DOWNLOAD_METHOD, **provenance)
        atomic_write_jsonl(rows_path, rows)
        atomic_write_json(staged / "pdfs" / "download_report.json", {
            "success": 1, "failed": 0, "downloaded": [detail], "failed_papers": [], "pdf_count": 1, "attempted": 1,
        })
        return {"success": 1, "failed": 0, "stats": {"success": 1, "failed": 0}}

    return run


def match_evidence(basis: str, file_name: str) -> dict[str, str]:
    if basis not in BASIS_LABELS:
        raise ValueError("Unknown matching evidence")
    return {"basis": basis, "file_name": Path(file_name).name[:200],
            "matched_at": datetime.now().isoformat(timespec="seconds")}


# ---------------------------------------------------------------- download-folder watch

# (project_id, retry_id, file bytes, source, basis, file name)
Importer = Callable[[str, str, bytes, str, str, str], Any]


class DownloadWatcher:
    """Polls the download folder while any paper is awaiting its download.

    Reads only *.pdf files created after the earliest pending link click, once their size is
    stable, skipping partial downloads. A unique confident match is imported; anything else is
    left for the user to decide in the canvas.
    """

    def __init__(self, output_root: Callable[[], Path], importer: Importer, *, interval: float = 1.5,
                 busy: Callable[[str], bool] = lambda project_id: False):
        self._output_root = output_root
        self._importer = importer
        self._busy = busy
        self.interval = interval
        self._projects: set[str] = set()
        self._sizes: dict[tuple[str, str], tuple[int, int, float]] = {}
        self._guard = threading.Lock()
        self._thread: threading.Thread | None = None

    def watch(self, project_id: str) -> None:
        with self._guard:
            self._projects.add(project_id)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="reviewpilot-download-watch", daemon=True)
                self._thread.start()

    def watching(self) -> set[str]:
        with self._guard:
            return set(self._projects)

    def _loop(self) -> None:
        while True:
            time.sleep(self.interval)
            try:
                self.tick()
            except Exception as exc:  # the watcher must survive one unreadable file or project
                print(f"[download-watch] {type(exc).__name__}: {exc}")
            with self._guard:
                if not self._projects:
                    self._thread = None
                    return

    def tick(self, *, now: float | None = None) -> None:
        settings = load_settings(self._output_root())
        for project_id in sorted(self.watching()):
            project = Path(self._output_root()) / project_id
            awaiting = active_awaiting(project, now=now) if project.is_dir() else {}
            if not awaiting or not settings["watch_enabled"]:
                with self._guard:
                    if not awaiting:
                        self._projects.discard(project_id)
                continue
            if self._busy(project_id):
                continue
            self._scan(project_id, project, download_folder(settings), awaiting, now=now)

    def _scan(self, project_id: str, project: Path, folder: Path, awaiting: dict[str, float], *, now: float | None) -> None:
        now = time.time() if now is None else now
        earliest = min(awaiting.values()) - CLICK_SLACK_SECONDS
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        seen = set(load_state(project)["seen"])
        for entry in sorted(entries, key=lambda item: item.name):
            name = entry.name
            lowered = name.lower()
            if name.startswith(".") or not lowered.endswith(".pdf") or lowered.endswith(PARTIAL_SUFFIXES):
                continue
            try:
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            created = getattr(info, "st_birthtime", None) or info.st_ctime
            if created < earliest or info.st_size == 0 or info.st_size > MAX_PDF_BYTES:
                continue
            key = f"{entry.path}|{info.st_size}|{info.st_mtime_ns}"
            if key in seen:
                continue
            size_key = (project_id, entry.path)
            previous = self._sizes.get(size_key)
            if previous is None or previous[:2] != (info.st_size, info.st_mtime_ns):
                self._sizes[size_key] = (info.st_size, info.st_mtime_ns, now)
                continue
            if now - previous[2] < STABLE_SECONDS:
                continue
            self._sizes.pop(size_key, None)
            if self._process(project_id, project, Path(entry.path), key, set(awaiting)) == "retry":
                self._sizes[size_key] = (info.st_size, info.st_mtime_ns, now - STABLE_SECONDS)

    def _process(self, project_id: str, project: Path, path: Path, key: str, awaiting_ids: set[str]) -> str:
        try:
            content = path.read_bytes()
        except OSError:
            return "retry"
        if not is_pdf(content):
            mark_seen(project, key)
            return "skipped"
        digest = hashlib.sha256(content).hexdigest()
        if digest in imported_hashes(project):
            mark_seen(project, key)
            return "duplicate"
        candidates = candidates_for(project, awaiting_ids)
        if not candidates:
            return "skipped"
        _failures, included = _failure_rows(project)
        outcome = match_pdf(content, candidates, (row.get("title") for row in included))
        if outcome.status == "matched":
            try:
                self._importer(project_id, outcome.retry_id, content, "download_watch", outcome.basis, path.name)
            except Exception as exc:
                if exc.__class__.__name__ == "TaskConflictError":
                    return "retry"
                add_pending(project, {"source": "download_watch", "file_name": path.name, "path": str(path),
                                      "sha256": digest, "size": len(content), "reason": "import_failed",
                                      "candidates": outcome.candidates}, seen_key=key)
                return "pending"
            mark_seen(project, key)
            return "imported"
        add_pending(project, {"source": "download_watch", "file_name": path.name, "path": str(path),
                              "sha256": digest, "size": len(content), "reason": outcome.status,
                              "candidates": outcome.candidates}, seen_key=key)
        return "pending"


# ---------------------------------------------------------------- canvas projection

def projection(project: Path, output_root: Path | str, *, enabled: bool) -> dict[str, Any]:
    project = Path(project)
    settings = load_settings(output_root)
    state = load_state(project)
    report = read_json(project / "pdfs" / "download_report.json", {}) or {}
    downloaded = report.get("downloaded") if isinstance(report.get("downloaded"), list) else []
    counts = {"automatic": sum(1 for row in downloaded if not is_user_provided(row)),
              "user": sum(1 for row in downloaded if is_user_provided(row))}
    failures = _failure_rows(project)[0] if enabled else {}
    items = []
    for retry_id, info in failures.items():
        code, reason = failure_reason(info["row"], info["detail"])
        clicked = state["awaiting"].get(retry_id, {}).get("clicked_at")
        awaiting = clicked is not None and time.time() - clicked < AWAIT_SECONDS and settings["watch_enabled"]
        items.append({"retryId": retry_id, "label": info["label"], "reasonCode": code, "reason": reason,
                      "links": paper_links(info["row"], settings["library_resolver"]),
                      "awaitingSince": datetime.fromtimestamp(clicked).strftime("%H:%M") if awaiting else ""})
    labels = {rid: info["label"] for rid, info in failures.items()}
    pending = []
    for entry in state["pending"]:
        options = [{"retryId": rid, "label": label} for rid, label in labels.items()]
        if not options:
            continue
        suggested = [{"retryId": item["retry_id"], "label": labels[item["retry_id"]],
                      "basis": BASIS_LABELS.get(item.get("basis"), "")}
                     for item in entry.get("candidates") or [] if item.get("retry_id") in labels]
        pending.append({"id": entry["id"], "fileName": safe_display_text(str(entry.get("file_name") or "PDF"), fallback="PDF"),
                        "source": entry.get("source"), "sourceLabel": SOURCE_LABELS.get(entry.get("source"), ""),
                        "reason": PENDING_REASONS.get(entry.get("reason"), "ReviewPilot could not add this file. Choose its paper."),
                        "suggested": suggested, "options": options})
    recent = [{"label": safe_display_text(str(item.get("label") or ""), fallback="Paper"),
               "source": SOURCE_LABELS.get(item.get("source"), ""), "basis": BASIS_LABELS.get(item.get("basis"), ""),
               "fileName": safe_display_text(str(item.get("file_name") or ""), fallback="")}
              for item in reversed(state["imported"][-_RECENT_LIMIT:])]
    return {"enabled": bool(enabled and items), "items": items, "pending": pending, "recent": recent,
            "counts": counts, "settings": settings_projection(output_root),
            "awaiting": any(item["awaitingSince"] for item in items)}
