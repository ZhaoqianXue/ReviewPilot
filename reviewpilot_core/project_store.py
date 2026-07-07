"""Read-only accessors for ReviewPilot project output folders."""

from __future__ import annotations

import json
from pathlib import Path
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
    if not root.exists():
        return []
    return sorted(
        [path for path in root.iterdir() if path.is_dir() and (path / "search_conditions.json").exists()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

