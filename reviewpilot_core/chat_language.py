"""The language a chat reply is written in, decided from the user's message.

The language is named explicitly. A vague "reply in the user's language" made the model answer plain English
requests in Spanish; naming the language from the message's script avoids that.
"""

from __future__ import annotations

import re

_SCRIPTS = (
    (re.compile(r"[぀-ヿ]"), "Japanese"),
    (re.compile(r"[가-힯ᄀ-ᇿ]"), "Korean"),
    (re.compile(r"[一-鿿㐀-䶿]"), "Chinese"),
)


def reply_language(message: str) -> str:
    text = str(message or "")
    for pattern, language in _SCRIPTS:
        if pattern.search(text):
            return language
    return "English"


def reply_language_instruction(message: str) -> str:
    language = reply_language(message)
    return f"Write the reply in {language}, the language of the current user message; keep field names, option labels and quoted text as they are."
