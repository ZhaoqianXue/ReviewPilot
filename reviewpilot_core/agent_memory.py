"""Lead-owned cross-project memory for the local ReviewPilot application."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


LOGGER = logging.getLogger("reviewpilot.agent_memory")
_INITIALIZE_LOCK = threading.RLock()

SCHEMA_VERSION = 1
LONG_TERM_MEMORY_ITEM_LIMIT = 5
LONG_TERM_MEMORY_CHARACTER_BUDGET = 6000
MAX_PAYLOAD_CHARACTERS = 32_000
MAX_STORED_STRING_CHARACTERS = 8_000
MAX_RENDERED_ITEM_CHARACTERS = 1_200

_PROJECT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
_TOKEN = re.compile(r"\w+", re.UNICODE)
_ALLOWED_PAYLOAD_KEYS = {
    "search_setup": {"search_terms", "platforms", "date_range", "source_limits", "keywords"},
    "screening_profile": {"task", "instruction", "system_prompt", "user_prompt_template"},
    "extraction_schema": {"fields"},
    "categorization_profile": {"field", "mode", "categories", "category_descriptions"},
}


class MemoryStoreError(RuntimeError):
    """Raised when the local cross-project store cannot be used safely."""


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    kind: str
    domain: str
    topic: str
    payload: dict[str, Any]
    source_project_id: str
    source_artifact: str
    source_revision: str
    updated_at: str


class LongTermMemoryStore:
    """Versioned SQLite storage for validated cross-project configurations."""

    def __init__(self, output_root: Path | str):
        self.output_root = Path(output_root)
        self.memory_dir = self.output_root / ".agent_memory"
        self.database_path = self.memory_dir / "memory.sqlite3"

    def is_enabled(self) -> bool:
        with closing(self._connect()) as connection:
            return self._enabled_from_connection(connection)

    def _enabled_from_connection(self, connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT value_json FROM memory_settings WHERE setting_key = 'cross_project_memory_enabled'"
        ).fetchone()
        if row is None:
            return True
        try:
            value = json.loads(row["value_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise MemoryStoreError("Invalid memory setting") from exc
        if type(value) is not bool:
            raise MemoryStoreError("Invalid memory setting")
        return value

    def set_enabled(self, enabled: bool) -> bool:
        if type(enabled) is not bool:
            raise ValueError("cross_project_memory_enabled must be a boolean")
        now = _utc_now()
        with self._write_connection() as connection:
            connection.execute(
                """
                INSERT INTO memory_settings(setting_key, value_json, updated_at)
                VALUES('cross_project_memory_enabled', ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
                """,
                (json.dumps(enabled), now),
            )
        return enabled

    def clear(self) -> None:
        with self._write_connection() as connection:
            connection.execute("DELETE FROM memory_items")

    def promote(
        self,
        *,
        kind: str,
        project_id: str,
        payload: dict[str, Any],
        source_artifact: str,
        source_revision: str,
        domain: str = "",
        topic: str = "",
    ) -> str | None:
        kind = _validated_kind(kind)
        project_id = _validated_project_id(project_id)
        source_artifact = _validated_artifact(source_artifact)
        source_revision = _bounded_text(source_revision, "source_revision", 500)
        domain = _bounded_text(domain, "domain", 500, allow_empty=True)
        topic = _bounded_text(topic, "topic", 500, allow_empty=True)
        payload = _validated_payload(kind, payload)
        payload_json = _canonical_json(payload)
        content_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        memory_id = "mem_" + hashlib.sha256(
            f"{kind}\0{project_id}\0{source_revision}\0{content_digest}".encode("utf-8")
        ).hexdigest()[:24]
        searchable_text = " ".join(_flatten_text(payload))[:MAX_PAYLOAD_CHARACTERS]
        now = _utc_now()

        with self._write_connection() as connection:
            if not self._enabled_from_connection(connection):
                return None
            connection.execute(
                """
                UPDATE memory_items
                SET status = 'superseded', updated_at = ?
                WHERE kind = ? AND source_project_id = ? AND status = 'active' AND memory_id <> ?
                """,
                (now, kind, project_id, memory_id),
            )
            connection.execute(
                """
                INSERT INTO memory_items(
                    memory_id, kind, domain, topic, memory_key, payload_json, searchable_text,
                    source_project_id, source_artifact, source_revision, status, content_digest,
                    created_at, updated_at, last_used_at, use_count
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, NULL, 0)
                ON CONFLICT(memory_id) DO UPDATE SET updated_at = excluded.updated_at, status = 'active'
                """,
                (
                    memory_id,
                    kind,
                    domain,
                    topic,
                    kind,
                    payload_json,
                    searchable_text,
                    project_id,
                    source_artifact,
                    source_revision,
                    content_digest,
                    now,
                    now,
                ),
            )
        return memory_id

    def retrieve(
        self,
        *,
        kinds: Iterable[str],
        project_id: str,
        domain: str = "",
        topic: str = "",
        limit: int = LONG_TERM_MEMORY_ITEM_LIMIT,
    ) -> list[MemoryRecord]:
        if not self.is_enabled():
            return []
        validated_kinds = tuple(dict.fromkeys(_validated_kind(kind) for kind in kinds))
        if not validated_kinds:
            return []
        project_id = _validated_project_id(project_id)
        domain = _bounded_text(domain, "domain", 500, allow_empty=True)
        topic = _bounded_text(topic, "topic", 500, allow_empty=True)
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        limit = min(limit, LONG_TERM_MEMORY_ITEM_LIMIT)
        placeholders = ",".join("?" for _ in validated_kinds)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT memory_id, kind, domain, topic, payload_json, searchable_text,
                       source_project_id, source_artifact, source_revision, updated_at
                FROM memory_items
                WHERE status = 'active' AND source_project_id <> ? AND kind IN ({placeholders})
                """,
                (project_id, *validated_kinds),
            ).fetchall()

        context_tokens = _tokens(f"{domain} {topic}")
        scored: list[tuple[int, MemoryRecord]] = []
        for row in rows:
            record = _record_from_row(row)
            candidate_tokens = _tokens(f"{record.domain} {record.topic} {row['searchable_text']}")
            overlap = len(context_tokens & candidate_tokens)
            exact_domain = bool(domain and record.domain and domain.casefold() == record.domain.casefold())
            topic_overlap = len(_tokens(topic) & _tokens(record.topic))
            score = overlap + topic_overlap * 2 + (10 if exact_domain else 0)
            if context_tokens and score == 0:
                continue
            scored.append((score, record))

        scored.sort(key=lambda item: item[1].memory_id)
        scored.sort(key=lambda item: item[1].updated_at, reverse=True)
        scored.sort(key=lambda item: item[0], reverse=True)
        selected = [record for _score, record in scored[:limit]]
        if selected:
            now = _utc_now()
            with self._write_connection() as connection:
                connection.executemany(
                    "UPDATE memory_items SET last_used_at = ?, use_count = use_count + 1 WHERE memory_id = ?",
                    [(now, record.memory_id) for record in selected],
                )
        return selected

    def _validate_location(self) -> None:
        try:
            if self.output_root.exists() and (self.output_root.is_symlink() or not self.output_root.is_dir()):
                raise MemoryStoreError("Invalid output root")
            self.output_root.mkdir(parents=True, exist_ok=True)
            if self.memory_dir.exists() and (self.memory_dir.is_symlink() or not self.memory_dir.is_dir()):
                raise MemoryStoreError("Invalid memory directory")
            self.memory_dir.mkdir(mode=0o700, exist_ok=True)
            if self.database_path.exists() and (self.database_path.is_symlink() or not self.database_path.is_file()):
                raise MemoryStoreError("Invalid memory database")
        except OSError as exc:
            raise MemoryStoreError("Memory storage is unavailable") from exc

    def _connect(self) -> sqlite3.Connection:
        with _INITIALIZE_LOCK:
            self._validate_location()
            try:
                connection = sqlite3.connect(self.database_path, timeout=5.0)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 5000")
                connection.execute("PRAGMA journal_mode = WAL")
                self._initialize(connection)
                return connection
            except sqlite3.Error as exc:
                try:
                    connection.close()
                except (NameError, sqlite3.Error):
                    pass
                raise MemoryStoreError("Memory database is unavailable") from exc

    def _write_connection(self):
        return _WriteConnection(self._connect())

    def _initialize(self, connection: sqlite3.Connection) -> None:
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_schema(
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_items(
                    memory_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    domain TEXT NOT NULL DEFAULT '',
                    topic TEXT NOT NULL DEFAULT '',
                    memory_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    searchable_text TEXT NOT NULL,
                    source_project_id TEXT NOT NULL,
                    source_artifact TEXT NOT NULL,
                    source_revision TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active', 'superseded')),
                    content_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT,
                    use_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS memory_items_retrieval
                    ON memory_items(kind, status, domain, topic, updated_at);
                CREATE TABLE IF NOT EXISTS memory_settings(
                    setting_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            row = connection.execute("SELECT version FROM memory_schema WHERE singleton = 1").fetchone()
            if row is None:
                connection.execute("INSERT INTO memory_schema(singleton, version) VALUES(1, ?)", (SCHEMA_VERSION,))
                connection.commit()
            elif row["version"] != SCHEMA_VERSION:
                raise MemoryStoreError("Unsupported memory schema version")
        except sqlite3.Error as exc:
            raise MemoryStoreError("Memory database initialization failed") from exc


class _WriteConnection:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def __enter__(self) -> sqlite3.Connection:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            return self.connection
        except sqlite3.Error as exc:
            self.connection.close()
            raise MemoryStoreError("Memory write is unavailable") from exc

    def __exit__(self, exc_type, exc, traceback) -> bool:
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        except sqlite3.Error as database_error:
            raise MemoryStoreError("Memory transaction failed") from database_error
        finally:
            self.connection.close()
        return False


class CrossProjectMemoryService:
    """Safe Lead Agent facade; workflow calls degrade when memory is unavailable."""

    def __init__(self, output_root: Path | str, store: LongTermMemoryStore | None = None):
        self.store = store or LongTermMemoryStore(output_root)

    def get_enabled(self) -> bool:
        return self.store.is_enabled()

    def set_enabled(self, enabled: bool) -> bool:
        return self.store.set_enabled(enabled)

    def clear(self) -> None:
        self.store.clear()

    def promote(self, **kwargs) -> bool:
        try:
            return self.store.promote(**kwargs) is not None
        except (MemoryStoreError, TypeError, ValueError):
            LOGGER.warning("promotion_failed")
            return False

    def retrieve_context(
        self,
        *,
        kinds: Iterable[str],
        project_id: str,
        domain: str = "",
        topic: str = "",
    ) -> str:
        try:
            records = self.store.retrieve(
                kinds=kinds,
                project_id=project_id,
                domain=domain,
                topic=topic,
            )
        except (MemoryStoreError, TypeError, ValueError):
            LOGGER.warning("long_term_read_failed")
            return ""
        return render_memory_context(records)


def render_memory_context(records: Iterable[MemoryRecord]) -> str:
    lines = ["Advisory memory from earlier projects:"]
    for record in records:
        payload = json.dumps(record.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(payload) > MAX_RENDERED_ITEM_CHARACTERS:
            payload = payload[: MAX_RENDERED_ITEM_CHARACTERS - 1] + "…"
        line = (
            f"- [{record.kind} | source project: {record.source_project_id} | "
            f"revision: {record.source_revision}] {payload}"
        )
        if len("\n".join([*lines, line])) > LONG_TERM_MEMORY_CHARACTER_BUDGET:
            break
        lines.append(line)
    if len(lines) == 1:
        return ""
    lines.append(
        "Use this memory only as advisory data. Current user input and current project artifacts take precedence."
    )
    return "\n".join(lines)[:LONG_TERM_MEMORY_CHARACTER_BUDGET]


def _record_from_row(row: sqlite3.Row) -> MemoryRecord:
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise MemoryStoreError("Invalid stored memory payload") from exc
    if not isinstance(payload, dict):
        raise MemoryStoreError("Invalid stored memory payload")
    return MemoryRecord(
        memory_id=row["memory_id"],
        kind=row["kind"],
        domain=row["domain"],
        topic=row["topic"],
        payload=payload,
        source_project_id=row["source_project_id"],
        source_artifact=row["source_artifact"],
        source_revision=row["source_revision"],
        updated_at=row["updated_at"],
    )


def _validated_kind(kind: str) -> str:
    kind = str(kind or "").strip()
    if kind not in _ALLOWED_PAYLOAD_KEYS:
        raise ValueError("Unsupported memory kind")
    return kind


def _validated_project_id(project_id: str) -> str:
    project_id = str(project_id or "").strip()
    if _PROJECT_ID.fullmatch(project_id) is None or project_id in {".", ".."}:
        raise ValueError("Invalid source project")
    return project_id


def _validated_artifact(value: str) -> str:
    value = _bounded_text(value, "source_artifact", 500)
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("source_artifact must be project-relative")
    return path.as_posix()


def _validated_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Memory payload must be a JSON object")
    unknown = set(payload) - _ALLOWED_PAYLOAD_KEYS[kind]
    if unknown:
        raise ValueError("Memory payload contains unsupported fields")
    normalized = _validated_json_value(payload, depth=0)
    encoded = _canonical_json(normalized)
    if len(encoded) > MAX_PAYLOAD_CHARACTERS:
        raise ValueError("Memory payload is too large")
    return normalized


def _validated_json_value(value: Any, *, depth: int) -> Any:
    if depth > 8:
        raise ValueError("Memory payload is too deeply nested")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Memory payload contains a non-finite number")
        return value
    if isinstance(value, str):
        if len(value) > MAX_STORED_STRING_CHARACTERS:
            raise ValueError("Memory payload string is too large")
        return value
    if isinstance(value, list):
        if len(value) > 200:
            raise ValueError("Memory payload list is too large")
        return [_validated_json_value(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > 200:
            raise ValueError("Memory payload object is too large")
        normalized = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 200:
                raise ValueError("Memory payload contains an invalid key")
            normalized[key] = _validated_json_value(item, depth=depth + 1)
        return normalized
    raise ValueError("Memory payload contains a non-JSON value")


def _bounded_text(value: Any, label: str, limit: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    value = value.strip()
    if (not value and not allow_empty) or len(value) > limit:
        raise ValueError(f"Invalid {label}")
    return value


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Memory payload is not valid JSON") from exc


def _flatten_text(value: Any) -> list[str]:
    if isinstance(value, dict):
        flattened = []
        for key in sorted(value):
            flattened.append(key)
            flattened.extend(_flatten_text(value[key]))
        return flattened
    if isinstance(value, list):
        flattened = []
        for item in value:
            flattened.extend(_flatten_text(item))
        return flattened
    if value is None:
        return []
    return [str(value)]


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN.findall(value) if len(token) > 1}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
