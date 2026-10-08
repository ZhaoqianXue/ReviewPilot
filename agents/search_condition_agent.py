"""
Search Condition Agent.

Turns a research request into a concept strategy and keeps it as the single
source of truth for the project's search:

- derive: an LLM proposes concept blocks from the user's request;
- provided: caller-supplied concept blocks (or a legacy AND-of-OR query) are validated;
- refine: an LLM revises the current concept blocks from a chat message.

The Boolean query is always rebuilt from the concept blocks
(reviewpilot_core.search_concepts). Method rules for designing concepts live in
the systematic-review-search-strategy Agent Skill; the prompts here only define
the data supplied and the JSON contract returned.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent
from reviewpilot_core.model_policy import DEFAULT_MAX_RESULTS_PER_PLATFORM, SEARCH_CONDITION_MODEL
from reviewpilot_core.publication_dates import resolve_range
from reviewpilot_core.search_concepts import (
    BLOCK_KEYS,
    MAX_BLOCKS,
    MAX_TERMS_PER_BLOCK,
    ROLES,
    blocks_from_query,
    derived_fields,
    validate_concept_blocks,
)
from utils.jsonl_handler import save_json
from utils.llm import query_llm

SUPPORTED_SOURCES = ("pubmed", "arxiv", "openalex")
SETTINGS_KEYS = {"project_name", "platforms", "max_results", "date_start", "date_end"}

_CONCEPT_SCHEMA = """{
  "label": "concise canonical concept label",
  "role": "%s",
  "eligibility_group": "concise_snake_case_group",
  "required_for_eligibility": true | false,
  "query_terms": ["plain source-neutral term or phrase"]
}""" % " | ".join(ROLES)

_FORMAT_RULES = f"""Output constraints:
- Return 1 to {MAX_BLOCKS} concept blocks with only the keys {", ".join(BLOCK_KEYS)}.
- A required concept has 1 to {MAX_TERMS_PER_BLOCK} unique query terms; a concept that is not required has 0 to {MAX_TERMS_PER_BLOCK}.
- Write query terms as plain text without Boolean operators, field tags, wildcards, or quotation marks.
- Concepts sharing one eligibility_group share one role and one required_for_eligibility value."""

_SETTINGS_CONTRACT = """"search_settings" holds only operational settings the CURRENT message explicitly requests: project_name (string), platforms (array drawn from pubmed, arxiv, openalex), max_results (positive integer per source), date_start and date_end (ISO dates). Return {} when none are requested."""


class SearchConditionAgent(BaseAgent):
    """Builds, validates, and refines a project's concept-based search strategy."""

    def __init__(self, output_dir: str = "output", llm_query=None):
        self.output_dir = Path(output_dir)
        self.agent_name = "search_condition"
        self.llm_query = llm_query or query_llm
        self.state = {}
        self.logger = None

    # ------------------------------------------------------------------ save
    def run(self, input_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Save a project's search setup and return the persisted search conditions."""
        config = dict(input_data or {})
        project_name = str(config.get("project_name") or "").strip()
        if not project_name:
            raise ValueError("project_name is required")
        project_path = Path(config.get("project_path") or self.output_dir / project_name)
        project_path.mkdir(parents=True, exist_ok=True)
        super().__init__(project_path, self.agent_name)
        description = str(config.get("description") or config.get("research_description") or "").strip()

        if config.get("derive_search_terms") or not (config.get("concept_blocks") or str(config.get("search_terms") or "").strip()):
            if not description:
                raise ValueError("description is required to derive a search strategy")
            conditions = self._derive(config, project_path, description)
        else:
            blocks = (validate_concept_blocks(config["concept_blocks"]) if config.get("concept_blocks")
                      else blocks_from_query(str(config["search_terms"])))
            conditions = self._assemble(config, project_path, description, blocks)
            conditions["generated_by"] = "user"

        save_json(str(project_path / "search_conditions.json"), conditions)
        self.log(f"Search conditions saved: {project_path / 'search_conditions.json'}")
        self.state = {"completed": True, "conditions": conditions}
        self.save_state()
        return conditions

    def _derive(self, config: Dict[str, Any], project_path: Path, description: str) -> Dict[str, Any]:
        model = str(config.get("model") or SEARCH_CONDITION_MODEL)
        interpret_settings = config.get("interpret_chat_settings") is True
        prompt = self._derive_prompt(config, description, interpret_settings)
        payload, usage = self._query_json(prompt, model, lambda data: self._check_derive_payload(data, interpret_settings))
        if interpret_settings:
            settings = payload.get("search_settings", {})
            config = self.apply_chat_settings(config, settings)
            if "project_name" not in settings:
                # A chat-created project is named from the request unless the user named it.
                config["project_name"] = payload["title"].strip()
        blocks = validate_concept_blocks(payload["concept_blocks"])
        conditions = self._assemble(config, project_path, description, blocks)
        conditions.update(
            research_description=payload["research_description"].strip(),
            lead_agent_reply=payload["reply"].strip(),
            llm_usage=usage or {},
            model=model,
            generated_by="llm",
        )
        return conditions

    def _assemble(self, config: Dict[str, Any], project_path: Path, description: str,
                  blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
        platforms = self._platforms(config.get("platforms"))
        limits = self._source_limits(config, platforms)
        configured_range = config.get("date_range") if isinstance(config.get("date_range"), dict) else {}
        date_range = resolve_range({"start": configured_range.get("start") or "", "end": configured_range.get("end") or ""})
        carried = {key: value for key, value in config.items()
                   if key not in {"memory_context", "interpret_chat_settings", "search_settings"}}
        return {
            **carried,
            "project_name": str(config["project_name"]).strip(),
            "project_path": str(project_path),
            "description": description,
            "research_description": str(config.get("research_description") or description),
            **derived_fields(blocks),
            "platforms": platforms,
            "date_range": date_range,
            "source_limits": limits,
            "max_results": max(limits.values()),
            "max_results_per_platform": max(limits.values()),
            "derive_search_terms": False,
        }

    # ---------------------------------------------------------------- refine
    def refine(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Revise the saved strategy from one chat message.

        Returns {"reply", "changed", "config"}; ``config`` is the complete setup to
        save when ``changed`` is true. Nothing is written here: the caller saves
        it through the normal setup path so revision checks and impacts apply.
        """
        current = dict(input_data.get("current") or {})
        message = str(input_data.get("message") or "").strip()
        if not message:
            raise ValueError("message is required")
        project_path = Path(current.get("project_path") or self.output_dir)
        super().__init__(project_path, self.agent_name)
        blocks = (validate_concept_blocks(current["concept_blocks"]) if current.get("concept_blocks")
                  else blocks_from_query(str(current.get("search_terms") or "")))
        model = str(current.get("model") or SEARCH_CONDITION_MODEL)
        prompt = self._refine_prompt(current, blocks, message, input_data.get("history") or [])
        payload, _usage = self._query_json(prompt, model, self._check_refine_payload)
        baseline = self.apply_chat_settings({**current, "concept_blocks": blocks}, {})
        updated = self.apply_chat_settings(baseline, payload["search_settings"])
        if payload["concept_blocks"] is not None:
            updated["concept_blocks"] = validate_concept_blocks(payload["concept_blocks"])
        changed = any(updated.get(key) != baseline.get(key)
                      for key in ("project_name", "platforms", "source_limits", "date_range", "concept_blocks"))
        setup = {key: updated.get(key) for key in ("project_name", "description", "concept_blocks", "platforms", "source_limits", "date_range", "model")}
        setup.update(derive_search_terms=False, max_results=max((updated.get("source_limits") or {"_": DEFAULT_MAX_RESULTS_PER_PLATFORM}).values()))
        return {"reply": payload["reply"].strip(), "changed": changed, "config": setup}

    # --------------------------------------------------------------- prompts
    @staticmethod
    def system_prompt() -> str:
        return ("You design and revise concept strategies for systematic-review searches. "
                "Follow the attached search-strategy skill and return only valid JSON.")

    def _derive_prompt(self, config: Dict[str, Any], description: str, interpret_settings: bool) -> str:
        settings = ""
        if interpret_settings:
            defaults = {key: config.get(key) for key in ("project_name", "platforms", "source_limits", "date_range")}
            settings = (f'\nAlso return a top-level "search_settings" object. {_SETTINGS_CONTRACT} '
                        f"Current defaults, for reference only: {json.dumps(defaults, ensure_ascii=False)}\n")
        return f"""Design the concept strategy for this research request.

USER RESEARCH REQUEST DATA:
{json.dumps(description, ensure_ascii=False)}

OPTIONAL APPROVED VOCABULARY DATA:
{json.dumps(config.get("memory_context") or "", ensure_ascii=False)}

Return ONLY a JSON object with this shape:
{{
  "reply": "brief message to the user describing the concepts",
  "title": "concise project title of 3 to 8 words in the user's language",
  "research_description": "the user's research question in clear prose",
  "concept_blocks": [{_CONCEPT_SCHEMA}]{', "search_settings": {}' if interpret_settings else ''}
}}

{_FORMAT_RULES}
{settings}"""

    def _refine_prompt(self, current: Dict[str, Any], blocks: List[Dict[str, Any]], message: str, history: list) -> str:
        settings = {key: current.get(key) for key in ("project_name", "platforms", "source_limits", "date_range")}
        return f"""Revise the saved concept strategy only as the CURRENT USER MESSAGE requests.

RESEARCH DESCRIPTION DATA:
{json.dumps(current.get("description") or "", ensure_ascii=False)}

SAVED CONCEPT BLOCKS DATA (authoritative):
{json.dumps(blocks, ensure_ascii=False)}

SAVED SETTINGS DATA (authoritative):
{json.dumps(settings, ensure_ascii=False)}

CONVERSATION HISTORY DATA (context only; earlier requests are already handled):
{json.dumps(history[-12:], ensure_ascii=False)}

CURRENT USER MESSAGE DATA:
{json.dumps(message, ensure_ascii=False)}

Return ONLY a JSON object with this shape:
{{
  "reply": "brief message stating exactly what changed, or answering the question",
  "concept_blocks": null or the complete updated list of [{_CONCEPT_SCHEMA}],
  "search_settings": {{}}
}}

Use null for concept_blocks when the message does not change concepts or terms; otherwise return the complete list, preserving every block the message does not ask to change. {_SETTINGS_CONTRACT}
The reply describes only changes carried by concept_blocks or search_settings.

{_FORMAT_RULES}"""

    # ------------------------------------------------------------- LLM calls
    def _query_json(self, prompt: str, model: str, check) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Call the LLM; on a rejected response retry once with the rejection reason."""
        request = prompt
        for attempt in range(2):
            response_text, usage = self.llm_query(text_prompt=request, system_prompt=self.system_prompt(),
                                                  model=model, provider="openai")
            try:
                payload = json.loads(str(response_text or "").strip())
                if not isinstance(payload, dict):
                    raise ValueError("SearchConditionAgent LLM response must be a JSON object")
                check(payload)
                return payload, usage or {}
            except json.JSONDecodeError as exc:
                error = ValueError("SearchConditionAgent LLM did not return valid JSON")
                error.__cause__ = exc
            except ValueError as exc:
                error = exc
            if attempt:
                raise error
            self.log(f"Search strategy response rejected ({error}); retrying once", level="warning")
            request = f"{prompt}\nYour previous response was rejected: {error}. Return a corrected JSON object that follows the shape exactly."
        raise AssertionError("unreachable")

    @classmethod
    def _check_derive_payload(cls, payload: Dict[str, Any], interpret_settings: bool) -> None:
        required = {"reply", "title", "research_description", "concept_blocks"}
        allowed = required | ({"search_settings"} if interpret_settings else set())
        cls._check_keys(payload, required, allowed)
        for key in ("reply", "title", "research_description"):
            cls._require_text(payload[key], key)
        if len(payload["title"].strip()) > 120:
            raise ValueError("SearchConditionAgent LLM response title must be at most 120 characters")
        validate_concept_blocks(payload["concept_blocks"])
        if interpret_settings and not isinstance(payload.get("search_settings", {}), dict):
            raise ValueError("search_settings must be an object")

    @classmethod
    def _check_refine_payload(cls, payload: Dict[str, Any]) -> None:
        cls._check_keys(payload, {"reply", "concept_blocks", "search_settings"}, {"reply", "concept_blocks", "search_settings"})
        cls._require_text(payload["reply"], "reply")
        if payload["concept_blocks"] is not None:
            validate_concept_blocks(payload["concept_blocks"])
        if not isinstance(payload["search_settings"], dict):
            raise ValueError("search_settings must be an object")

    @staticmethod
    def _check_keys(payload: Dict[str, Any], required: set, allowed: set) -> None:
        missing = sorted(required - set(payload))
        if missing:
            raise ValueError(f"SearchConditionAgent LLM response missing required fields: {', '.join(missing)}")
        unexpected = sorted(set(payload) - allowed)
        if unexpected:
            raise ValueError(f"SearchConditionAgent LLM response has unexpected fields: {', '.join(unexpected)}")

    @staticmethod
    def _require_text(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"SearchConditionAgent LLM response {field} must be non-empty text")
        return value.strip()

    # -------------------------------------------------------------- settings
    @staticmethod
    def apply_chat_settings(config: Dict[str, Any], settings: Any) -> Dict[str, Any]:
        """Apply operational settings requested in chat; unspecified settings keep their values."""
        if not isinstance(settings, dict):
            raise ValueError("Invalid chat search settings")
        settings = dict(settings)
        # Models often echo defaults in config shape (source_limits, date_range,
        # per-source max_results); map those onto the settings schema.
        if "source_limits" in settings and "max_results" not in settings:
            settings["max_results"] = settings.pop("source_limits")
        if isinstance(settings.get("date_range"), dict):
            date_range = settings.pop("date_range")
            for key in ("start", "end"):
                if key in date_range:
                    settings.setdefault("date_" + key, date_range[key])
        if set(settings) - SETTINGS_KEYS:
            raise ValueError("Invalid chat search settings")
        result = dict(config)
        if "project_name" in settings:
            name = settings["project_name"]
            if not isinstance(name, str) or not name.strip() or len(name) > 120:
                raise ValueError("Invalid chat project name")
            result["project_name"] = name.strip()
        platforms = settings.get("platforms", result.get("platforms") or list(SUPPORTED_SOURCES))
        if not isinstance(platforms, list) or not platforms or any(not isinstance(p, str) or p not in SUPPORTED_SOURCES for p in platforms):
            raise ValueError("Invalid chat search sources")
        result["platforms"] = list(dict.fromkeys(platforms))
        limit = settings.get("max_results")
        limits = limit if isinstance(limit, dict) else {p: limit for p in result["platforms"]} if limit is not None else {}
        if any(type(value) is not int or value <= 0 for value in limits.values()):
            raise ValueError("Invalid chat result limit")
        old_limits = result.get("source_limits") or {}
        fallback = SearchConditionAgent._positive_int(result.get("max_results"), DEFAULT_MAX_RESULTS_PER_PLATFORM)
        result["source_limits"] = {p: limits.get(p) or SearchConditionAgent._positive_int(old_limits.get(p), fallback) for p in result["platforms"]}
        bounds = dict(result.get("date_range") or {})
        for key in ("start", "end"):
            if "date_" + key in settings:
                value = settings["date_" + key]
                if not isinstance(value, str):
                    raise ValueError("Invalid chat date range")
                bounds[key] = value
        result["date_range"] = resolve_range(bounds)
        return result

    @staticmethod
    def _platforms(value: Any) -> List[str]:
        items = value.split(",") if isinstance(value, str) else value if isinstance(value, list) else []
        platforms = list(dict.fromkeys(str(item).strip().lower() for item in items if str(item).strip()))
        unsupported = [item for item in platforms if item not in SUPPORTED_SOURCES]
        if unsupported:
            raise ValueError(f"Unsupported search source: {', '.join(unsupported)}")
        return platforms or list(SUPPORTED_SOURCES)

    def _source_limits(self, config: Dict[str, Any], platforms: List[str]) -> Dict[str, int]:
        configured = config.get("source_limits") if isinstance(config.get("source_limits"), dict) else {}
        default = self._positive_int(config.get("max_results") or config.get("max_results_per_platform"), DEFAULT_MAX_RESULTS_PER_PLATFORM)
        return {platform: self._positive_int(configured.get(platform), default) for platform in platforms}

    @staticmethod
    def _positive_int(value: Any, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default
