"""Atomic replacement helpers for formal workflow artifacts."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Iterator


@contextmanager
def atomic_output_path(target: Path | str) -> Iterator[Path]:
    """Yield a same-directory temporary path and replace target on success."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(fd)
    pending = Path(name)
    try:
        yield pending
        os.replace(pending, target)
    finally:
        pending.unlink(missing_ok=True)


def atomic_write_text(target: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    with atomic_output_path(target) as pending:
        pending.write_text(text, encoding=encoding)


def atomic_write_json(target: Path | str, data: Any, *, indent: int | None = 2) -> None:
    with atomic_output_path(target) as pending:
        pending.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")


def atomic_write_jsonl(target: Path | str, records: Iterable[dict[str, Any]]) -> None:
    with atomic_output_path(target) as pending:
        with pending.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
