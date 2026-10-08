"""Concept blocks: the single source of truth for a project's search strategy.

A concept block is one atomic, user-facing concept with its equivalent query
terms. Blocks that share an ``eligibility_group`` are alternatives (OR); distinct
required groups must all match (AND). The Boolean query is always rebuilt from
the blocks, so editing the blocks and editing the query can never disagree.
"""

from __future__ import annotations

import re
from typing import Any

from .query_syntax import parse

MAX_BLOCKS = 12
MAX_TERMS_PER_BLOCK = 12
MAX_LABEL_LENGTH = 120
MAX_TERM_LENGTH = 200

# Roles offered to the model and the canvas.
ROLES = (
    "phenomenon",
    "intervention_or_exposure",
    "population",
    "condition",
    "context",
    "outcome",
    "study_design",
    "analytical_dimension",
    "other",
)
# Older saved strategies may still use population_or_context; they stay valid.
ACCEPTED_ROLES = ROLES + ("population_or_context",)
PRIMARY_ROLES = {"phenomenon", "intervention_or_exposure"}
CONTEXT_ROLES = {"population", "condition", "context", "population_or_context"}
METHOD_ROLES = {"study_design", "analytical_dimension"}

BLOCK_KEYS = ("label", "role", "eligibility_group", "required_for_eligibility", "query_terms")
_GROUP_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
# Quotes, wildcards, field tags and Boolean operators belong to source adapters, not terms.
# An apostrophe inside a word (Alzheimer's, Crohn's) is ordinary text.
_TERM_SYNTAX = re.compile(r'"|\*|\[[^\]]*\]|\b(?:AND|OR|NOT)\b|(?<![\w])\'|\'(?![\w])')
_LABEL_SYNTAX = re.compile(r'"|\*|\[[^\]]*\]|\b(?:AND|OR|NOT)\b')


def validate_concept_blocks(value: Any) -> list[dict[str, Any]]:
    """Return normalized blocks or raise ValueError with a user-readable reason."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_BLOCKS:
        raise ValueError(f"A search strategy needs 1 to {MAX_BLOCKS} concepts.")
    blocks: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != set(BLOCK_KEYS):
            raise ValueError(f"Each concept needs exactly these fields: {', '.join(BLOCK_KEYS)}.")
        label = raw["label"].strip() if isinstance(raw["label"], str) else ""
        if not label or len(label) > MAX_LABEL_LENGTH or _LABEL_SYNTAX.search(label):
            raise ValueError("Each concept needs a plain-text label without search syntax.")
        role = raw["role"].strip() if isinstance(raw["role"], str) else ""
        if role not in ACCEPTED_ROLES:
            raise ValueError(f"Concept '{label}' has an unsupported role.")
        group = raw["eligibility_group"].strip() if isinstance(raw["eligibility_group"], str) else ""
        if not _GROUP_PATTERN.fullmatch(group):
            raise ValueError(f"Concept '{label}' needs a snake_case eligibility group.")
        required = raw["required_for_eligibility"]
        if type(required) is not bool:
            raise ValueError(f"Concept '{label}' must state whether it is required.")
        terms = raw["query_terms"]
        if not isinstance(terms, list) or len(terms) > MAX_TERMS_PER_BLOCK or (required and not terms):
            raise ValueError(f"Concept '{label}' needs 1 to {MAX_TERMS_PER_BLOCK} query terms.")
        normalized_terms: list[str] = []
        for term in terms:
            text = " ".join(term.split()) if isinstance(term, str) else ""
            if not text or len(text) > MAX_TERM_LENGTH or _TERM_SYNTAX.search(text):
                raise ValueError(f"Query terms for '{label}' must be plain text without quotes, wildcards, field tags, or AND/OR/NOT.")
            normalized_terms.append(text)
        if len({term.casefold() for term in normalized_terms}) != len(normalized_terms):
            raise ValueError(f"Query terms for '{label}' must be unique.")
        blocks.append({"label": label, "role": role, "eligibility_group": group,
                       "required_for_eligibility": required, "query_terms": normalized_terms})
    if len({block["label"].casefold() for block in blocks}) != len(blocks):
        raise ValueError("Concept labels must be unique.")
    group_roles: dict[str, str] = {}
    group_required: dict[str, bool] = {}
    for block in blocks:
        if group_roles.setdefault(block["eligibility_group"], block["role"]) != block["role"]:
            raise ValueError("Concepts in one eligibility group must share a role.")
        if group_required.setdefault(block["eligibility_group"], block["required_for_eligibility"]) != block["required_for_eligibility"]:
            raise ValueError("Concepts in one eligibility group must all be required or all be optional.")
    if not any(block["required_for_eligibility"] for block in blocks):
        raise ValueError("At least one concept must be required for eligibility.")
    return blocks


def format_query_term(term: str) -> str:
    return term if re.fullmatch(r"[\w.+:/-]+", term) else f'"{term}"'


def build_boolean_query(blocks: list[dict[str, Any]]) -> str:
    """OR the terms of every required concept in a group; AND the groups in first-seen order."""
    grouped: dict[str, list[str]] = {}
    for block in blocks:
        if block["required_for_eligibility"]:
            terms = grouped.setdefault(block["eligibility_group"], [])
            terms.extend(term for term in (format_query_term(t) for t in block["query_terms"]) if term not in terms)
    return " AND ".join(f"({' OR '.join(terms)})" for terms in grouped.values())


def derived_fields(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Fields every other stage reads; always recomputed from the blocks."""
    query = build_boolean_query(blocks)
    required = [block for block in blocks if block["required_for_eligibility"]]
    primary = [block["label"] for block in blocks if block["role"] in PRIMARY_ROLES]
    context = [block["label"] for block in blocks if block["role"] in CONTEXT_ROLES]
    primary_topic = primary[0] if primary else required[0]["label"]
    return {
        "concept_blocks": blocks,
        "search_terms": query,
        "search_queries": [{"name": "main", "query": query}],
        "keywords": [block["label"] for block in required],
        "primary_topic": primary_topic,
        "domain": ", ".join(context) or primary_topic,
        "extracted_concepts": {
            "primary_topics": primary or [primary_topic],
            "domains": context,
            "methods": [block["label"] for block in blocks if block["role"] in METHOD_ROLES],
            "outcomes": [block["label"] for block in blocks if block["role"] == "outcome"],
        },
    }


def blocks_from_query(query: str) -> list[dict[str, Any]]:
    """Convert an AND-of-OR query from an older project into concept blocks.

    Each AND group becomes one required concept labelled by its first term.
    Queries with other shapes (NOT, nested mixes) cannot be represented and raise ValueError.
    """
    text = str(query or "").strip()
    if not text:
        raise ValueError("Search query must not be empty.")

    def alternatives(node):
        if node[0] == "term":
            return [node[1].strip('"\'')]
        if node[0] == "OR":
            return alternatives(node[1]) + alternatives(node[2])
        raise ValueError("Only queries that AND together groups of OR'd terms can be edited as concepts.")

    def conjuncts(node):
        return conjuncts(node[1]) + conjuncts(node[2]) if node[0] == "AND" else [alternatives(node)]

    groups = conjuncts(parse(text))
    blocks = []
    for index, terms in enumerate(groups, start=1):
        cleaned = list(dict.fromkeys(" ".join(term.split()) for term in terms if term.strip()))
        blocks.append({"label": cleaned[0][:MAX_LABEL_LENGTH], "role": "other", "eligibility_group": f"group_{index}",
                       "required_for_eligibility": True, "query_terms": cleaned[:MAX_TERMS_PER_BLOCK]})
    return validate_concept_blocks(blocks)
