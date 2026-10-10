"""Which papers go through abstract and web extraction, and what that is estimated to cost.

A paper qualifies when retrieval, including any PDF the user supplied afterwards, left it
without full text. The estimate is information shown before extraction runs; it is not
an opt-in switch.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .model_policy import EXTRACTION_MODEL, WEB_SEARCH_CALL_USD, project_model, supports_web_search, token_cost
from .project_store import read_json, read_jsonl

# Tokens per paper measured on a 10-paper gpt-6-luna trial (2026-10-08), rounded up: the
# extraction prompt with schema, coding rules and abstract, then a web-search turn whose
# retrieved page content is billed as input.
ABSTRACT_STEP_TOKENS = (3_000, 2_000)
WEB_STEP_TOKENS = (15_000, 1_000)
WEB_SEARCH_CALLS_PER_PAPER = 2


def lacks_full_text(paper: dict[str, Any]) -> bool:
    return bool(paper.get("web_search_fallback_pending")) and not paper.get("pdf_downloaded")


def per_paper_estimate(model: str, web_search: bool) -> float:
    cost = token_cost(model, *ABSTRACT_STEP_TOKENS)
    if web_search:
        cost += token_cost(model, *WEB_STEP_TOKENS) + WEB_SEARCH_CALLS_PER_PAPER * WEB_SEARCH_CALL_USD
    return round(cost, 4)


def plan(project: Path | str, model: str | None = None) -> dict[str, Any]:
    """Papers still without full text, the per-paper estimate, and whether web search will run."""
    project = Path(project)
    model = model or project_model(read_json(project / "search_conditions.json", {}) or {}, EXTRACTION_MODEL)
    papers = [paper for paper in read_jsonl(project / "filtered" / "included_papers.jsonl") if lacks_full_text(paper)]
    web_search = supports_web_search(model)
    per_paper = per_paper_estimate(model, web_search)
    count = len(papers)
    notice = ""
    if count:
        noun = "paper has" if count == 1 else "papers have"
        steps = "their abstracts, then allowed web sources for fields the abstract leaves open" if web_search else "their abstracts"
        notice = (f"{count} {noun} no full text. Information Extraction will extract them from {steps}: "
                  f"about {count} × ${per_paper:.3f} ≈ ${count * per_paper:.2f}.")
        if not web_search:
            notice += f" The web step is skipped because {model} has no web-search tool here; abstract extraction runs as usual."
    return {"papers": count, "model": model, "web_search": web_search, "per_paper_usd": per_paper,
            "total_usd": round(count * per_paper, 4), "notice": notice}
