"""Lead Agent orchestration layer for ReviewPilot."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agents.prompt_agent import PromptAgent
from agents.search_condition_agent import SearchConditionAgent
from reviewpilot_core.model_policy import LEAD_AGENT_DEV_MODEL
from reviewpilot_core.sub_agent_contracts import default_sub_agent_contracts
from reviewpilot_core.workflow_adapter import WorkflowActionAdapter
from utils.jsonl_handler import append_jsonl
from utils.llm import query_llm


@dataclass(frozen=True)
class LeadAgentResult:
    stage: str
    status: str
    reply: str
    search_conditions: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    task_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "reply": self.reply,
            "search_conditions": self.search_conditions,
            "artifacts": self.artifacts,
            "next_actions": self.next_actions,
            "task_id": self.task_id,
            "data": self.data,
        }


class LeadAgent:
    """Bounded orchestration agent for the web app."""

    def __init__(
        self,
        output_root: Path | str,
        search_condition_agent_cls=SearchConditionAgent,
        prompt_agent_cls=PromptAgent,
        workflow_actions: dict[str, Any] | None = None,
        workflow_adapter: WorkflowActionAdapter | None = None,
        llm_query=None,
    ):
        self.output_root = Path(output_root)
        self.search_condition_agent_cls = search_condition_agent_cls
        self.prompt_agent_cls = prompt_agent_cls
        if workflow_actions is not None:
            raise ValueError("workflow_actions function overrides are no longer supported; provide workflow_adapter with explicit contracts")
        if workflow_adapter is None:
            workflow_adapter = WorkflowActionAdapter(
                default_sub_agent_contracts(
                    search_condition_agent_cls=search_condition_agent_cls,
                    prompt_agent_cls=prompt_agent_cls,
                )
            )
        self.workflow_adapter = workflow_adapter
        self.llm_query = llm_query

    def handle_message(
        self,
        project_id: str | None,
        message: str | None = None,
        action: str | None = None,
        input_data: dict[str, Any] | None = None,
    ) -> LeadAgentResult:
        if action and message:
            raise ValueError("Provide either message or action, not both")
        if action:
            if not project_id:
                raise ValueError("project_id is required for actions")
            return self._handle_action(project_id, action, input_data=input_data)
        if message:
            if not project_id:
                raise ValueError("project_id is required for chat messages")
            reply = self.reply_to_project_message(project_id, message)
            project_path = self.output_root / project_id
            stage = self._current_stage(project_path)
            return LeadAgentResult(
                stage=stage,
                status="completed",
                reply=reply,
                artifacts=self._stage_artifacts(project_path, stage),
                next_actions=self._next_actions(stage),
            )
        raise ValueError("message or action is required")

    def save_search_setup(self, project_id: str, config: dict[str, Any]) -> LeadAgentResult:
        project_path = self.output_root / project_id
        result = self._call_workflow_action("save-search-setup", project_id, input_data={**config, "project_path": str(project_path)})
        search_conditions = result.get("search_conditions") if isinstance(result, dict) else None
        if not isinstance(search_conditions, dict):
            raise ValueError("SearchConditionAgent contract did not return search_conditions")
        artifact = project_path / "search_conditions.json"
        self._verify_search_setup_artifact(artifact, search_conditions)
        return LeadAgentResult(
            stage="search_conditions",
            status="completed",
            reply="Search Setup is ready. Review it in the canvas, then run collection when you are ready.",
            search_conditions=search_conditions,
            artifacts=[str(artifact)],
            next_actions=["edit_search", "run_collection"],
        )

    def _verify_search_setup_artifact(self, artifact: Path, search_conditions: dict[str, Any]) -> None:
        if not artifact.exists():
            raise ValueError(f"Search setup artifact was not written: {artifact}")
        required = ("project_name", "search_terms", "platforms")
        missing = [key for key in required if not search_conditions.get(key)]
        if missing:
            raise ValueError(f"Search setup missing required fields: {', '.join(missing)}")

    def reply_to_project_message(self, project_id: str, message: str) -> str:
        project_path = self.output_root / project_id
        config_path = project_path / "search_conditions.json"
        if not config_path.exists():
            raise ValueError(f"Project search setup not found: {project_id}")
        user_message = str(message or "").strip()
        if not user_message:
            raise ValueError("message is required")

        config = json.loads(config_path.read_text(encoding="utf-8"))
        model = str(config.get("model") or LEAD_AGENT_DEV_MODEL)
        llm_query = self.llm_query or query_llm
        response_text, usage = llm_query(
            text_prompt=self._project_chat_prompt(config, user_message),
            system_prompt=(
                "You are ReviewPilot's Lead Agent. Answer the user's project-specific chat message. "
                "Return only valid JSON with a single string field named reply."
            ),
            model=model,
            provider="openai",
        )
        reply = self._parse_project_chat_reply(response_text)
        now = datetime.now().isoformat()
        append_jsonl(str(project_path / "chat" / "messages.jsonl"), {"step": 1, "role": "u", "text": user_message, "created_at": now})
        append_jsonl(
            str(project_path / "chat" / "messages.jsonl"),
            {"step": 1, "role": "a", "text": reply, "created_at": now, "llm_usage": usage or {}, "model": model},
        )
        return reply

    def _project_chat_prompt(self, config: dict[str, Any], message: str) -> str:
        return f"""Project context:
Project name: {config.get('project_name') or 'ReviewPilot project'}
Research description: {config.get('description') or config.get('research_description') or ''}
Search terms: {config.get('search_terms') or ''}
Platforms: {config.get('platforms') or []}
Date range: {config.get('date_range') or {}}

User message:
{message}

Return ONLY valid JSON:
{{"reply": "your concise project-specific response"}}"""

    def _parse_project_chat_reply(self, response_text: str) -> str:
        try:
            payload = json.loads(str(response_text or "").strip())
        except json.JSONDecodeError as exc:
            raise ValueError("Lead Agent LLM did not return valid chat JSON") from exc
        if not isinstance(payload, dict) or not str(payload.get("reply") or "").strip():
            raise ValueError("Lead Agent LLM response missing reply")
        return str(payload["reply"]).strip()

    def _handle_action(self, project_id: str, action: str, input_data: dict[str, Any] | None = None) -> LeadAgentResult:
        project_path = self.output_root / project_id
        if not project_path.exists():
            raise ValueError(f"Project not found: {project_id}")
        config = self._load_search_conditions(project_path)
        artifacts: list[str] = []

        if action == "collect":
            self._verify_stage_artifacts(project_path, "search_conditions")
            artifacts.append(str(self._ensure_relevance_prompt(project_path, config)))
            result = self._call_workflow_action("collect", project_id)
            artifacts.extend(self._verify_stage_artifacts(project_path, "collection"))
            return self._action_result(project_path, "collection", result, artifacts, action="collect")

        if action == "screen":
            self._require_completed_stage(project_path, action, "collection")
            artifacts.append(str(self._ensure_relevance_prompt(project_path, config)))
            result = self._call_workflow_action("screen", project_id)
            artifacts.extend(self._verify_stage_artifacts(project_path, "filtering"))
            return self._action_result(project_path, "filtering", result, artifacts, action="screen")

        if action == "generate-schema":
            self._require_completed_stage(project_path, action, "filtering")
            result = self._call_workflow_action("generate-schema", project_id)
            artifacts.extend(self._verify_stage_artifacts(project_path, "prompt_extraction"))
            return self._action_result(project_path, "prompt_extraction", result, artifacts, action="generate-schema")

        if action == "download-pdfs":
            self._require_completed_stage(project_path, action, "filtering")
            result = self._call_workflow_action("download-pdfs", project_id)
            artifacts.extend(self._verify_stage_artifacts(project_path, "download"))
            return self._action_result(project_path, "download", result, artifacts, action="download-pdfs")

        if action == "run-extraction":
            self._require_completed_stage(project_path, action, "download")
            artifacts.append(str(self._ensure_extraction_prompt(project_path, config)))
            result = self._call_workflow_action("run-extraction", project_id)
            artifacts.extend(self._verify_stage_artifacts(project_path, "extraction"))
            return self._action_result(project_path, "extraction", result, artifacts, action="run-extraction")

        if action == "suggest-categories":
            self._require_completed_stage(project_path, action, "extraction")
            result = self._call_workflow_action("suggest-categories", project_id, input_data=input_data)
            artifacts.append(str(project_path / "categorization" / "suggested_categories.json"))
            return self._action_result(project_path, "categorization", result, artifacts, action="suggest-categories")

        if action == "categorize":
            self._require_completed_stage(project_path, action, "extraction")
            result = self._call_workflow_action("categorize", project_id, input_data=input_data)
            artifacts.extend(self._verify_stage_artifacts(project_path, "categorization"))
            return self._action_result(project_path, "categorization", result, artifacts, action="categorize")

        raise ValueError(f"Unsupported action: {action}")

    def _call_workflow_action(self, action: str, project_id: str, input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.workflow_adapter.run(action, self.output_root, project_id, llm_query=self.llm_query, input_data=input_data)

    def _action_result(self, project_path: Path, stage: str, result: dict[str, Any], artifacts: list[str], action: str) -> LeadAgentResult:
        data = dict(result)
        contract = self.workflow_adapter.contract_for(action)
        data.setdefault("sub_agent", contract.agent_name)
        data.setdefault("contract_stage", contract.stage)
        data.setdefault("model", contract.model)
        reply = self._stage_reply(stage, result)
        append_jsonl(
            str(project_path / "chat" / "messages.jsonl"),
            {
                "step": self._stage_step(stage),
                "role": "a",
                "text": reply,
                "created_at": datetime.now().isoformat(),
                "model": LEAD_AGENT_DEV_MODEL,
                "source": "canvas_action",
                "stage": stage,
                "action": action,
            },
        )
        return LeadAgentResult(
            stage=stage,
            status="completed",
            reply=reply,
            artifacts=artifacts,
            next_actions=self._next_actions(stage),
            data=data,
        )

    def _stage_step(self, stage: str) -> int:
        return {
            "search_conditions": 1,
            "prompt_relevance": 1,
            "collection": 1,
            "filtering": 2,
            "download": 3,
            "prompt_extraction": 4,
            "extraction": 4,
            "categorization_suggestions": 5,
            "categorization": 5,
        }.get(stage, 1)

    def _stage_reply(self, stage: str, result: dict[str, Any]) -> str:
        llm_query = self.llm_query or query_llm
        response_text, _usage = llm_query(
            text_prompt=f"""A ReviewPilot canvas action completed.

Stage: {stage}
Structured result:
{json.dumps(result, ensure_ascii=False, indent=2)}

Write one concise Lead Agent reply for the chat panel. Mention the stage outcome and the next canvas action when obvious. Do not invent counts beyond the structured result.
Use this exact stage-to-next-canvas-action policy:
- collection -> Paper Screening
- filtering -> Full-Text Retrieval
- download -> Information Extraction
- extraction -> Categorization & Analysis
- categorization -> no next required canvas action
If the completed stage is download and some papers remain unavailable or eligible for web-search fallback, explain that the next canvas action is Information Extraction and that ExtractionAgent will use web-search fallback for eligible unavailable papers. Do not describe web-search fallback as a separate workflow step or separate canvas action.

Return ONLY valid JSON:
{{"reply": "your concise reply"}}""",
            system_prompt=(
                "You are ReviewPilot's Lead Agent. Summarize completed workflow actions for the user. "
                "Return only valid JSON with a single string field named reply."
            ),
            model=LEAD_AGENT_DEV_MODEL,
            provider="openai",
        )
        return self._parse_project_chat_reply(response_text)

    def _load_search_conditions(self, project_path: Path) -> dict[str, Any]:
        path = project_path / "search_conditions.json"
        return self._read_json_artifact(path, "search_conditions")

    def _ensure_relevance_prompt(self, project_path: Path, config: dict[str, Any]) -> Path:
        prompt_path = project_path / "prompts" / "relevance_prompt.json"
        if prompt_path.exists():
            self._verify_stage_artifacts(project_path, "prompt_relevance")
            return prompt_path

        self._call_workflow_action("generate-relevance-prompt", project_path.name, input_data=config)
        self._verify_stage_artifacts(project_path, "prompt_relevance")
        return prompt_path

    def _ensure_extraction_prompt(self, project_path: Path, config: dict[str, Any]) -> Path:
        prompt_path = project_path / "prompts" / "extraction_prompt.json"
        if prompt_path.exists():
            self._verify_stage_artifacts(project_path, "prompt_extraction_prompt")
            return prompt_path

        self._call_workflow_action("generate-schema", project_path.name, input_data=config)
        self._verify_stage_artifacts(project_path, "prompt_extraction_prompt")
        return prompt_path

    def _require_completed_stage(self, project_path: Path, action: str, required_stage: str) -> None:
        try:
            self._verify_stage_artifacts(project_path, required_stage)
        except ValueError as exc:
            raise ValueError(f"Action '{action}' requires completed stage '{required_stage}'") from exc

    def _verify_stage_artifacts(self, project_path: Path, stage: str) -> list[str]:
        if stage == "search_conditions":
            path = project_path / "search_conditions.json"
            data = self._read_json_artifact(path, "search_conditions")
            missing = [key for key in ("project_name", "search_terms", "platforms") if not data.get(key)]
            if missing:
                raise ValueError(f"search_conditions artifact missing required fields: {', '.join(missing)}")
            return [str(path)]

        if stage == "prompt_relevance":
            path = project_path / "prompts" / "relevance_prompt.json"
            data = self._read_json_artifact(path, "prompt_relevance")
            if not (data.get("task") or data.get("instruction") or data.get("user_prompt_template")):
                raise ValueError("prompt_relevance artifact missing prompt content")
            return [str(path)]

        if stage == "prompt_extraction_prompt":
            path = project_path / "prompts" / "extraction_prompt.json"
            data = self._read_json_artifact(path, "prompt_extraction")
            if not (data.get("user_prompt_template") or data.get("extraction_prompt")):
                raise ValueError("prompt_extraction artifact missing prompt content")
            return [str(path)]

        if stage == "collection":
            path = project_path / "collected" / "summary.json"
            data = self._read_json_artifact(path, "collection")
            if "platform_stats" not in data:
                raise ValueError("collection artifact missing platform_stats")
            if "total_papers" not in data:
                raise ValueError("collection artifact missing total_papers")
            return [str(path)]

        if stage == "filtering":
            included = project_path / "filtered" / "included_papers.jsonl"
            stats = project_path / "filtered" / "screening_stats.json"
            self._require_file(included, "filtering included_papers")
            self._read_json_artifact(stats, "filtering screening_stats")
            return [str(included), str(stats)]

        if stage == "prompt_extraction":
            architecture_prompt = self._verify_stage_artifacts(project_path, "prompt_extraction_prompt")[0]
            schema = project_path / "extraction" / "extraction_schema.json"
            prompt = project_path / "extraction" / "extraction_prompt.json"
            schema_data = self._read_json_artifact(schema, "prompt_extraction schema")
            self._read_json_artifact(prompt, "prompt_extraction prompt")
            if not schema_data.get("fields"):
                raise ValueError("prompt_extraction artifact missing fields")
            return [architecture_prompt, str(schema), str(prompt)]

        if stage == "download":
            path = project_path / "pdfs" / "download_report.json"
            self._read_json_artifact(path, "download")
            return [str(path)]

        if stage == "extraction":
            path = project_path / "extraction" / "extraction_results.jsonl"
            self._require_file(path, "extraction results")
            return [str(path)]

        if stage == "categorization":
            results = project_path / "categorization" / "categorized_results.jsonl"
            mapping = project_path / "categorization" / "categorization_mapping.json"
            self._require_file(results, "categorization results")
            self._read_json_artifact(mapping, "categorization mapping")
            return [str(results), str(mapping)]

        raise ValueError(f"Unknown stage: {stage}")

    def _read_json_artifact(self, path: Path, label: str) -> dict[str, Any]:
        if not path.exists():
            raise ValueError(f"{label} artifact was not written: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} artifact is not valid JSON: {path}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{label} artifact must be a JSON object: {path}")
        return data

    def _require_file(self, path: Path, label: str) -> None:
        if not path.exists():
            raise ValueError(f"{label} artifact was not written: {path}")
        if path.is_file():
            return
        raise ValueError(f"{label} artifact is not a file: {path}")

    def _current_stage(self, project_path: Path) -> str:
        checks = [
            ("categorization", project_path / "categorization" / "categorization_mapping.json"),
            ("extraction", project_path / "extraction" / "extraction_results.jsonl"),
            ("download", project_path / "pdfs" / "download_report.json"),
            ("prompt_extraction", project_path / "prompts" / "extraction_prompt.json"),
            ("filtering", project_path / "filtered" / "screening_stats.json"),
            ("collection", project_path / "collected" / "summary.json"),
            ("prompt_relevance", project_path / "prompts" / "relevance_prompt.json"),
            ("search_conditions", project_path / "search_conditions.json"),
        ]
        for stage, path in checks:
            if path.exists():
                return stage
        return "search_conditions"

    def _stage_artifacts(self, project_path: Path, stage: str) -> list[str]:
        try:
            return self._verify_stage_artifacts(project_path, stage)
        except ValueError:
            return []

    def _next_actions(self, stage: str) -> list[str]:
        return {
            "search_conditions": ["edit_search", "run_collection"],
            "prompt_relevance": ["run_collection"],
            "collection": ["run_screening"],
            "filtering": ["download_pdfs", "generate_schema"],
            "prompt_extraction": ["download_pdfs"],
            "download": ["run_extraction"],
            "extraction": ["apply_categorization"],
            "categorization": [],
        }.get(stage, [])
