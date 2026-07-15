"""Deterministic, project-local Agent Skill loading for ReviewPilot."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SKILL_ASSIGNMENTS = {
    "save-search-setup": ("SearchConditionAgent", "systematic-review-search-strategy"),
    "generate-relevance-prompt": ("PromptAgent", "evidence-screening"),
    "screen": ("FilteringAgent", "evidence-screening"),
    "generate-schema": ("PromptAgent", "structured-evidence-extraction"),
    "run-extraction": ("ExtractionAgent", "structured-evidence-extraction"),
    "suggest-categories": ("LeadAgentCategorization", "evidence-synthesis-and-categorization"),
    "categorize": ("LeadAgentCategorization", "evidence-synthesis-and-categorization"),
}

SKILL_VERSIONS = {
    "systematic-review-search-strategy": "1.0.0",
    "evidence-screening": "1.0.0",
    "structured-evidence-extraction": "1.0.0",
    "evidence-synthesis-and-categorization": "1.0.0",
}

_START_MARKER = "<reviewpilot-agent-skill"


@dataclass(frozen=True)
class SkillActivation:
    action: str
    agent_name: str
    name: str
    description: str
    instructions: str
    version: str
    content_hash: str

    def augment(self, system_prompt: str) -> str:
        base = str(system_prompt or "").strip()
        marker = f'{_START_MARKER} name="{self.name}"'
        if marker in base:
            return base
        skill_block = (
            f'{marker} version="{self.version}" sha256="{self.content_hash}">\n'
            f"{self.instructions}\n"
            "</reviewpilot-agent-skill>"
        )
        return f"{base}\n\n{skill_block}" if base else skill_block

    def trace(self, project_path: Path | str, *, model: str | None = None, provider: str | None = None) -> None:
        trace_path = Path(project_path) / ".reviewpilot" / "skill_activations.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "activated_at": datetime.now(timezone.utc).isoformat(),
            "action": self.action,
            "agent": self.agent_name,
            "skill": self.name,
            "version": self.version,
            "content_hash": self.content_hash,
            "activation_reason": f"deterministic action mapping: {self.action}",
            "model": model,
            "provider": provider,
            "loaded_resources": [],
        }
        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


class SkillRegistry:
    def __init__(self, skills_root: Path | str | None = None):
        self.skills_root = Path(skills_root or Path(__file__).resolve().parents[1] / ".agents" / "skills")

    def activate(self, action: str, agent_name: str) -> SkillActivation:
        assignment = SKILL_ASSIGNMENTS.get(action)
        if assignment is None:
            raise ValueError(f"Action has no Agent Skill assignment: {action}")
        expected_agent, skill_name = assignment
        if agent_name != expected_agent:
            raise ValueError(f"Agent Skill assignment mismatch for {action}: expected {expected_agent}, got {agent_name}")
        return self._load(action, agent_name, skill_name)

    def _load(self, action: str, agent_name: str, skill_name: str) -> SkillActivation:
        skill_dir = self.skills_root / skill_name
        skill_file = skill_dir / "SKILL.md"
        if skill_dir.is_symlink() or not skill_dir.is_dir() or skill_file.is_symlink() or not skill_file.is_file():
            raise ValueError(f"Missing or unsafe Agent Skill package: {skill_name}")
        try:
            if skill_file.resolve().parent != skill_dir.resolve() or skill_dir.resolve().parent != self.skills_root.resolve():
                raise ValueError(f"Unsafe Agent Skill package path: {skill_name}")
            raw = skill_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"Cannot read Agent Skill package: {skill_name}") from exc
        name, description, instructions = _parse_skill(raw)
        if name != skill_name:
            raise ValueError(f"Agent Skill name mismatch: expected {skill_name}, got {name}")
        version = SKILL_VERSIONS.get(name)
        if version is None:
            raise ValueError(f"Missing Agent Skill version: {name}")
        return SkillActivation(
            action=action,
            agent_name=agent_name,
            name=name,
            description=description,
            instructions=instructions,
            version=version,
            content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        )


def bind_skill_llm_query(
    project_path: Path | str,
    action: str,
    agent_name: str,
    llm_query: Callable[..., Any] | None = None,
    *,
    model: str | None = None,
) -> Callable[..., Any]:
    activation = SkillRegistry().activate(action, agent_name)
    activation.trace(project_path, model=model)

    def skill_query(*args, **kwargs):
        active_query = llm_query
        if active_query is None:
            from utils.llm import query_llm

            active_query = query_llm
        call_kwargs = dict(kwargs)
        if "system_prompt" in call_kwargs:
            call_kwargs["system_prompt"] = activation.augment(call_kwargs["system_prompt"])
        elif len(args) >= 2:
            positional = list(args)
            positional[1] = activation.augment(positional[1])
            args = tuple(positional)
        else:
            raise ValueError("Skill-bound LLM call must provide a system prompt")
        return active_query(*args, **call_kwargs)

    return skill_query


def activate_prompt_skill(project_path: Path | str, action: str, agent_name: str, system_prompt: str, *, model: str | None = None) -> str:
    activation = SkillRegistry().activate(action, agent_name)
    activation.trace(project_path, model=model)
    return activation.augment(system_prompt)


def _parse_skill(raw: str) -> tuple[str, str, str]:
    if not raw.startswith("---\n"):
        raise ValueError("Agent Skill must start with YAML frontmatter")
    try:
        frontmatter, instructions = raw[4:].split("\n---\n", 1)
    except ValueError as exc:
        raise ValueError("Agent Skill frontmatter is not closed") from exc
    metadata: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise ValueError("Agent Skill frontmatter contains an invalid field")
        key, value = line.split(":", 1)
        key = key.strip()
        if key not in {"name", "description"} or key in metadata:
            raise ValueError(f"Agent Skill frontmatter field is invalid: {key}")
        metadata[key] = value.strip().strip('"').strip("'")
    if set(metadata) != {"name", "description"} or not all(metadata.values()):
        raise ValueError("Agent Skill frontmatter requires non-empty name and description")
    body = instructions.strip()
    if not body:
        raise ValueError("Agent Skill instructions are empty")
    return metadata["name"], metadata["description"], body
