"""Utility modules for the multi-agent academic paper search system."""

from .jsonl_handler import read_jsonl, write_jsonl, append_jsonl
from .human_interaction import (
    ask_text, ask_choice, ask_confirm, ask_multiselect,
    print_header, print_summary, print_box, print_text, print_subheader
)
from .llm import query_llm
from .downloader import PaperDownloader

__all__ = [
    # JSONL utilities
    'read_jsonl', 'write_jsonl', 'append_jsonl',
    # Human interaction
    'ask_text', 'ask_choice', 'ask_confirm', 'ask_multiselect',
    'print_header', 'print_subheader', 'print_summary', 'print_box', 'print_text',
    # LLM
    'query_llm',
    # Downloader
    'PaperDownloader'
]
