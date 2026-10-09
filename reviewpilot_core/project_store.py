"""Read-only accessors for ReviewPilot project output folders."""

from __future__ import annotations

import json
from pathlib import Path
from stat import S_ISDIR
from typing import Any


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def read_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []

    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
                if limit is not None and len(rows) >= limit:
                    break
    except OSError:
        return []
    return rows


def count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def project_dir(output_root: Path, project_id: str) -> Path:
    return Path(output_root) / project_id


def iter_project_dirs(output_root: Path) -> list[Path]:
    root = Path(output_root)
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    projects = []
    for path in entries:
        try:
            metadata = path.stat()
            if not S_ISDIR(metadata.st_mode) or not (path / "search_conditions.json").exists():
                continue
        except OSError:
            # One inaccessible or concurrently removed entry must not hide the
            # remaining projects. Cache metadata so sorting cannot race removal.
            continue
        projects.append((path, metadata.st_mtime))
    return [path for path, _ in sorted(projects, key=lambda item: item[1], reverse=True)]
