"""Shared checks for user-visible text that must not expose local paths."""

from pathlib import Path, PureWindowsPath
import re


_ABSOLUTE_PATH_MARKER = re.compile(
    r"(?i:\bfile:(?=/{1,3}|[A-Za-z]:[\\/]))|(?<![:/])/{2,}(?=[^/])|(?<![\w./])/(?!/)|(?<![\w])[A-Za-z]:[\\/]|(?<![\\\w])\\\\(?=[^\\])"
)


def contains_absolute_path(value: str) -> bool:
    text = str(value)
    return Path(text.strip()).is_absolute() or PureWindowsPath(text.strip()).is_absolute() or _ABSOLUTE_PATH_MARKER.search(text) is not None


def safe_display_text(value: str, *, fallback: str = "Sensitive path details hidden.") -> str:
    text = str(value).strip()
    return fallback if contains_absolute_path(text) else text
