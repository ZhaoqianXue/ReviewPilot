"""Start a project from the researcher's own PDFs and go straight to Information Extraction.

The researcher uploads PDFs to a staging area (`output/.imports/<id>/`). Each file is checked and
described when it arrives (title, authors, year and DOI from the PDF, completed from Crossref when
the PDF names a DOI) so the form can list what was recognised. Creating the project writes the
same artifacts the search, screening and download stages would have written, marks those three
stages complete, and records `paper_import.json`; the project then opens at Step 4.

Search, screening and retrieval actions are refused for such a project (`is_imported_project`):
there is no search to rerun, and rerunning them would replace the researcher's own set of papers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import html
from pathlib import Path
import re
import secrets
import shutil
from threading import Lock
from typing import Any, Callable

from .atomic_files import atomic_write_json, atomic_write_jsonl
from .project_store import read_json
from .retrieval_retry import USER_DOWNLOAD_METHOD

SOURCE = "user_import"
SOURCE_LABEL = "Your PDFs"
MARKER = "paper_import.json"
STAGING_DIR = ".imports"
MAX_PDF_BYTES = 200 * 1024 * 1024
MAX_FILES = 500
MAX_IMPORT_BYTES = 4 * 1024 * 1024 * 1024
TEXT_CHECK_PAGES = 5
STAGING_TTL = timedelta(days=1)
IMPORT_ID = re.compile(r"^[a-f0-9]{24}$")
FILE_ID = re.compile(r"^[a-f0-9]{16}$")
DOI_PATTERN = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.IGNORECASE)
REFUSED_ACTIONS = {
    "collect", "screen", "download-pdfs", "retry-failed-downloads",
    "edit-criteria", "save-criteria", "finalize-criteria",
    "generate-screening-guidance", "confirm-screening-guidance",
}
REFUSAL = ("This project started from your own PDFs, so it has no search, screening or download step to run. "
           "Continue from Information Extraction.")
EXPIRED = "This upload has expired. Start again."
USED = "These PDFs were already used to create a project. Start a new upload."
_LOCKS_GUARD = Lock()
_IMPORT_LOCKS: dict[str, Lock] = {}


class PaperImportError(ValueError):
    """A problem the researcher can fix; the message is shown as is. `code` names the expired/used states."""

    def __init__(self, message: str):
        super().__init__(message)
        self.code = {EXPIRED: "import_expired", USED: "import_used"}.get(message, "")


def is_imported_project(project: Path | str) -> bool:
    return (Path(project) / MARKER).is_file()


def projection(project: Path | str) -> dict[str, Any] | None:
    """What the workspace shows about an imported project, or None for a searched one."""
    marker = read_json(Path(project) / MARKER, None)
    if not isinstance(marker, dict):
        return None
    return {"papers": int(marker.get("papers") or 0), "importedAt": str(marker.get("imported_at") or ""),
            "sourceLabel": SOURCE_LABEL}


# --- staging ------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _staging_root(output_root: Path | str) -> Path:
    return Path(output_root) / STAGING_DIR


def _import_lock(output_root: Path | str, import_id: str) -> Lock:
    """One lock per upload: file changes and project creation never interleave (the app is one process)."""
    key = str((_staging_root(output_root) / import_id).resolve())
    with _LOCKS_GUARD:
        return _IMPORT_LOCKS.setdefault(key, Lock())


def _staging(output_root: Path | str, import_id: str) -> Path:
    if not isinstance(import_id, str) or not IMPORT_ID.match(import_id):
        raise PaperImportError(EXPIRED)
    path = _staging_root(output_root) / import_id
    if not (path / "manifest.json").is_file():
        raise PaperImportError(EXPIRED)
    return path


def _open_manifest(path: Path) -> dict[str, Any]:
    """The manifest of an upload that can still change; a used upload is refused."""
    manifest = _manifest(path)
    if manifest.get("project_id"):
        raise PaperImportError(USED)
    return manifest


def _manifest(path: Path) -> dict[str, Any]:
    return read_json(path / "manifest.json", {}) or {"files": []}


def begin_import(output_root: Path | str) -> dict[str, Any]:
    """Open a staging area for one upload; older abandoned uploads are removed."""
    root = _staging_root(output_root)
    root.mkdir(parents=True, exist_ok=True)
    cutoff = _now() - STAGING_TTL
    for old in root.iterdir():
        try:
            if old.is_dir() and not old.is_symlink() and datetime.fromtimestamp(old.stat().st_mtime, timezone.utc) < cutoff:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            continue
    import_id = secrets.token_hex(12)
    path = root / import_id
    path.mkdir()
    atomic_write_json(path / "manifest.json", {"created_at": _now().isoformat(), "files": []})
    return {"importId": import_id, "files": []}


def add_file(output_root: Path | str, import_id: str, name: str, content: bytes,
             lookup: Callable[[str], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Check one uploaded PDF, describe it, and keep it in the staging area.

    Reading the PDF and the Crossref lookup run outside the upload's lock; the manifest is
    re-read and the limits re-checked inside it, so concurrent uploads and removals never lose an entry.
    """
    path = _staging(output_root, import_id)
    _open_manifest(path)
    display = Path(str(name or "paper.pdf")).name[:200] or "paper.pdf"
    if len(content) > MAX_PDF_BYTES:
        raise PaperImportError(f"{display} is larger than 200 MB.")
    if not content.lstrip()[:5].startswith(b"%PDF"):
        raise PaperImportError(f"{display} is not a PDF.")
    digest = hashlib.sha256(content).hexdigest()
    metadata = describe_pdf(content, display)
    first_page = metadata.pop("firstPageText", "")
    if metadata.get("doi"):
        metadata = _merge_crossref(metadata, (lookup or crossref_lookup)(metadata["doi"]), first_page)
    with _import_lock(output_root, import_id):
        path = _staging(output_root, import_id)
        manifest = _open_manifest(path)
        for existing in manifest["files"]:
            if existing["sha256"] == digest:
                return {**_public(existing), "duplicateOf": existing["fileName"]}
        if len(manifest["files"]) >= MAX_FILES:
            raise PaperImportError(f"One project can start from at most {MAX_FILES} PDFs.")
        if sum(int(entry.get("size") or 0) for entry in manifest["files"]) + len(content) > MAX_IMPORT_BYTES:
            raise PaperImportError("One project can start from at most 4 GB of PDFs.")
        entry = {"id": digest[:16], "sha256": digest, "fileName": display, "size": len(content), **metadata}
        (path / f"{entry['id']}.pdf").write_bytes(content)
        manifest["files"].append(entry)
        atomic_write_json(path / "manifest.json", manifest)
        return _public(entry)


def remove_file(output_root: Path | str, import_id: str, file_id: str) -> dict[str, Any]:
    if not isinstance(file_id, str) or not FILE_ID.match(file_id):
        raise PaperImportError("That file is not part of this upload.")
    with _import_lock(output_root, import_id):
        path = _staging(output_root, import_id)
        manifest = _open_manifest(path)
        manifest["files"] = [entry for entry in manifest["files"] if entry["id"] != file_id]
        atomic_write_json(path / "manifest.json", manifest)
        (path / f"{file_id}.pdf").unlink(missing_ok=True)
        return {"importId": import_id, "files": [_public(entry) for entry in manifest["files"]]}


def _public(entry: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "fileName", "size", "title", "authors", "year", "journal", "doi", "hasText", "titleSource")
    return {key: entry.get(key) for key in keys}


# --- reading a PDF ------------------------------------------------------------


def describe_pdf(content: bytes, file_name: str) -> dict[str, Any]:
    """Title, DOI and whether the PDF has a text layer, read locally from the file."""
    import fitz

    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise PaperImportError(f"{file_name} could not be opened as a PDF.") from exc
    try:
        if document.needs_pass:
            raise PaperImportError(f"{file_name} is password-protected. Remove the password and upload it again.")
        if document.page_count == 0:
            raise PaperImportError(f"{file_name} has no pages.")
        first_page = document[0].get_text()
        text = first_page + "".join(document[index].get_text() for index in range(1, min(2, document.page_count)))
        meta = document.metadata or {}
        title, source = _metadata_title(meta.get("title") or ""), "pdf_metadata"
        if not title:
            title, source = _first_page_title(document[0]), "first_page"
        if not title:
            title, source = _title_from_file_name(file_name), "file_name"
        doi_match = DOI_PATTERN.search(" ".join([meta.get("subject") or "", meta.get("keywords") or "", text]))
        authors = [part.strip() for part in re.split(r"[;,]| and ", meta.get("author") or "") if part.strip()]
        # Bounded: only the first pages are read to decide whether the PDF carries extractable text.
        sample = text + "".join(document[index].get_text() for index in range(2, min(TEXT_CHECK_PAGES, document.page_count)))
        has_text = len(re.sub(r"\s+", "", sample)) >= 200
        return {"title": title, "titleSource": source, "doi": _clean_doi(doi_match.group(1)) if doi_match else "",
                "authors": authors[:50], "year": None, "journal": "", "abstract": "", "hasText": has_text,
                "firstPageText": first_page}
    finally:
        document.close()


def _metadata_title(value: str) -> str:
    title = re.sub(r"\s+", " ", value).strip()
    if len(title) < 12 or len(title) > 400:
        return ""
    lowered = title.lower()
    # Word processors and typesetting tools often leave their own name or the file name as the title.
    if lowered.startswith(("microsoft word", "untitled", "doi:", "http")) or re.search(r"\.(pdf|docx?|tex|dvi)$", lowered):
        return ""
    if re.fullmatch(r"[\w\-. ]+\d{3,}[\w\-. ]*", title) and " " not in title.strip():
        return ""
    return title


def _first_page_title(page) -> str:
    """The largest text on the upper half of the first page, which is the title on almost every article."""
    spans = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if text and span["bbox"][1] < page.rect.height * 0.6:
                    spans.append((round(span.get("size", 0), 1), span["bbox"][1], text))
    candidates = [span for span in spans if re.search(r"[A-Za-z]", span[2])]
    if not candidates:
        return ""
    largest = max(size for size, _, _ in candidates)
    lines = [text for size, _, text in sorted(candidates, key=lambda item: item[1]) if abs(size - largest) < 0.6]
    title = re.sub(r"\s+", " ", " ".join(lines)).strip()
    return title if 12 <= len(title) <= 400 else ""


def _title_from_file_name(file_name: str) -> str:
    stem = Path(file_name).stem
    return re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", stem)).strip() or "Untitled paper"


def _clean_doi(value: str) -> str:
    """Drop citation punctuation after a DOI; a closing bracket stays when the DOI opened it."""
    value = value.strip()
    while value:
        last = value[-1]
        if last in ".,;>":
            value = value[:-1]
        elif last in ")]}" and value.count({")": "(", "]": "[", "}": "{"}[last]) < value.count(last):
            value = value[:-1]
        else:
            break
    return value


def crossref_lookup(doi: str, timeout: float = 8.0) -> dict[str, Any]:
    """Bibliographic details for a DOI from Crossref; an empty dict when it cannot be reached."""
    import requests
    from urllib.parse import quote

    try:
        response = requests.get(f"https://api.crossref.org/works/{quote(doi, safe='/')}",
                                headers={"User-Agent": "ReviewPilot (literature review tool)"}, timeout=timeout)
        if response.status_code != 200:
            return {}
        message = response.json().get("message") or {}
    except Exception:
        return {}
    title = " ".join(message.get("title") or []).strip()
    authors = [" ".join(part for part in (person.get("given"), person.get("family")) if part).strip()
               for person in message.get("author") or []]
    parts = ((message.get("issued") or {}).get("date-parts") or [[None]])[0]
    abstract = re.sub(r"<[^>]+>", " ", message.get("abstract") or "")
    return {"title": html.unescape(re.sub(r"\s+", " ", title)),
            "authors": [name for name in authors if name][:50],
            "year": parts[0] if parts and isinstance(parts[0], int) else None,
            "journal": " ".join(message.get("container-title") or []).strip(),
            "abstract": html.unescape(re.sub(r"\s+", " ", abstract)).strip()}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]{3,}", re.sub(r"-\s*\n\s*", "", text.lower()))


def _title_on_page(title: str, page_text: str) -> bool:
    """Whether a Crossref title is printed on the PDF's first page, so the DOI belongs to this paper."""
    wanted, present = _words(title), set(_words(page_text))
    return bool(wanted) and sum(word in present for word in wanted) / len(wanted) >= 0.7


def _merge_crossref(local: dict[str, Any], found: dict[str, Any], first_page: str = "") -> dict[str, Any]:
    """Use Crossref details only when the DOI demonstrably belongs to this PDF.

    A DOI found on the first pages can be a cited work's. When the Crossref title is not on the first
    page, the local details are kept and the DOI is dropped rather than giving the PDF another paper's identity.
    """
    if not found or not found.get("title"):
        return local
    if not _title_on_page(found["title"], first_page):
        return {**local, "doi": ""}
    merged = {**local}
    merged.update(title=found["title"], titleSource="crossref")
    for key in ("authors", "year", "journal", "abstract"):
        if found.get(key):
            merged[key] = found[key]
    return merged


# --- creating the project -----------------------------------------------------


def _slug(text: str, limit: int = 60) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:limit] or "paper"


def finish_import(output_root: Path | str, import_id: str, config: dict[str, Any],
                  reserve: Callable[[], str]) -> dict[str, Any]:
    """Turn the staged PDFs into a project whose search, screening and retrieval are complete.

    Runs under the upload's lock, so no file is added or removed meanwhile. `reserve` creates and
    returns a new, empty project directory name. A repeated finish of the same upload returns the
    project the first one created. `config` is the normalized setup.
    """
    with _import_lock(output_root, import_id):
        staging = _staging(output_root, import_id)
        manifest = _manifest(staging)
        done = manifest.get("project_id")
        if done:
            if is_imported_project(Path(output_root) / done):
                return {"id": done, "title": manifest.get("project_title") or done, "papers": len(manifest["files"])}
            raise PaperImportError(USED)
        if not manifest["files"]:
            raise PaperImportError("Add at least one PDF.")
        project_id = reserve()
        try:
            result = _build_project(Path(output_root) / project_id, staging, manifest["files"], config)
        except Exception:
            shutil.rmtree(Path(output_root) / project_id, ignore_errors=True)
            raise
        atomic_write_json(staging / "manifest.json", {**manifest, "project_id": project_id,
                                                      "project_title": config["project_name"]})
        for entry in manifest["files"]:
            (staging / f"{entry['id']}.pdf").unlink(missing_ok=True)
        return {**result, "id": project_id}


def _build_project(project: Path, staging: Path, files: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    from .setup_revision import normalize_setup, setup_revision
    from .workflow_state import complete_action, initialize_workflow_state, start_action

    imported_at = datetime.now().isoformat()
    (project / "pdfs").mkdir(parents=True, exist_ok=True)

    records, used = [], set()
    for index, entry in enumerate(files, 1):
        name = f"U{index:04d}_{_slug(entry['title'])}.pdf"
        while name in used:
            name = f"U{index:04d}_{entry['id'][:6]}_{_slug(entry['title'], 40)}.pdf"
        used.add(name)
        target = project / "pdfs" / name
        shutil.copyfile(staging / f"{entry['id']}.pdf", target)
        doi = entry.get("doi") or ""
        records.append({
            "source": SOURCE,
            "id": f"upload-{entry['id']}",
            "title": entry["title"],
            "abstract": entry.get("abstract") or "",
            "authors": entry.get("authors") or [],
            "journal": entry.get("journal") or "",
            "year": entry.get("year"),
            "doi": doi,
            "url": f"https://doi.org/{doi}" if doi else "",
            "collected_at": imported_at,
            "collection_record_id": f"record-{index}",
            "import_file_name": entry["fileName"],
            "import_has_text": bool(entry.get("hasText")),
            "import_title_source": entry.get("titleSource") or "",
            "is_relevant": True,
            "relevance_reason": "Provided by the researcher",
            "pdf_identity_required": False,
            "pdf_downloaded": True,
            "pdf_path": str(target.resolve()),
            "pdf_method": USER_DOWNLOAD_METHOD,
            "retrieval_status": "downloaded",
        })
    total = len(records)

    search_conditions = {**normalize_setup(config), "project_path": str(project), "paper_import": True}
    search_conditions["setup_revision"] = setup_revision(search_conditions)
    atomic_write_json(project / "session.json", {"title": config["project_name"]})
    atomic_write_json(project / MARKER, {"source": SOURCE, "papers": total, "imported_at": imported_at,
                                         "files": [{"file_name": entry["fileName"], "sha256": entry["sha256"],
                                                    "title_source": entry.get("titleSource")} for entry in files]})
    collected = [{key: value for key, value in record.items()
                  if key not in {"is_relevant", "relevance_reason", "pdf_identity_required", "pdf_downloaded",
                                 "pdf_path", "pdf_method", "retrieval_status"}} for record in records]
    atomic_write_jsonl(project / "collected" / f"{SOURCE}.jsonl", collected)
    atomic_write_json(project / "collected" / "summary.json", {
        "collected_at": imported_at, "query": "", "platforms": [SOURCE], "max_results_per_platform": total,
        "date_range": search_conditions.get("date_range") or {}, "results": {SOURCE: total},
        "platform_stats": {SOURCE: total}, "platform_errors": {}, "total_papers": total,
        "import": {"source": "researcher-provided PDFs", "records": total},
    })
    atomic_write_jsonl(project / "filtered" / "included_papers.jsonl", records)
    atomic_write_jsonl(project / "filtered" / "excluded_papers.jsonl", [])
    atomic_write_json(project / "filtered" / "screening_stats.json",
                      {"initial_count": total, "included": total, "excluded": 0, "total": total, "screening_skipped": True})
    downloaded = [{"title": record["title"], "path": record["pdf_path"], "method": USER_DOWNLOAD_METHOD} for record in records]
    report = {"downloaded_at": imported_at, "success": total, "failed": 0, "skipped": 0, "attempted": total,
              "pdf_count": total, "downloaded": downloaded, "failed_papers": [], "subscribed_papers": [],
              "unavailable_papers": [], "web_search_fallback_candidates": [], "obtained_automatically": 0,
              "provided_by_user": total, "output_dir": str((project / "pdfs").resolve()), "download_engine": SOURCE}
    atomic_write_json(project / "pdfs" / "download_report.json", report)
    no_text = sum(1 for entry in files if not entry.get("hasText"))
    note = (f" {no_text} of them {'has' if no_text == 1 else 'have'} little extractable text (possibly scanned pages), so extraction "
            f"may find little in {'it' if no_text == 1 else 'them'}." if no_text else "")
    atomic_write_jsonl(project / "chat" / "messages.jsonl", [
        {"step": 1, "role": "u", "text": config["description"], "source": "initial_setup"},
        {"step": 4, "role": "a", "source": "paper_import", "created_at": imported_at,
         "text": (f"Started from {total} PDF{'s' if total != 1 else ''} you provided. Search, screening and full-text retrieval "
                  f"were skipped. Generate the extraction schema to decide what to collect from each paper.{note}")},
    ])
    initialize_workflow_state(project)
    start_action(project, "collect")
    complete_action(project, "collect", {"total": total, "platform_stats": {SOURCE: total}, "platform_errors": {}})
    start_action(project, "screen")
    complete_action(project, "screen", {"included": total, "excluded": 0})
    start_action(project, "download-pdfs")
    complete_action(project, "download-pdfs", report)
    # Written last: a project folder is listed and opened only once search_conditions.json exists.
    atomic_write_json(project / "search_conditions.json", search_conditions)
    return {"title": config["project_name"], "papers": total}


def staged_files(output_root: Path | str, import_id: str) -> list[dict[str, Any]]:
    return [_public(entry) for entry in _manifest(_staging(output_root, import_id))["files"]]

