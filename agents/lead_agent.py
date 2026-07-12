"""Lead Agent orchestration layer for ReviewPilot."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from agents.prompt_agent import PromptAgent
from agents.search_condition_agent import SearchConditionAgent
from reviewpilot_core.extraction_schema import (
    add_schema_field,
    finalize_schema,
    is_schema_finalized,
    load_schema_draft,
    modify_schema_field,
    remove_schema_field,
    save_schema_draft,
    schema_paths,
)
from reviewpilot_core.model_policy import LEAD_AGENT_DEV_MODEL
from reviewpilot_core.sub_agent_contracts import default_sub_agent_contracts
from reviewpilot_core.workflow_adapter import WorkflowActionAdapter
from reviewpilot_core.workflow_state import load_workflow_state
from utils.jsonl_handler import append_jsonl
from utils.llm import query_llm


_ABSOLUTE_PATH_MARKER = re.compile(
    r"(?i:\bfile:(?=/{1,3}|[A-Za-z]:[\\/]))|(?<![:/])/{2,}(?=[^/])|(?<![\w./])/(?!/)|(?<![\w])[A-Za-z]:[\\/]|(?<![\\\w])\\\\(?=[^\\])"
)


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
        context_step: str | None = None,
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
            schema_result = self._handle_schema_chat_if_applicable(project_id, message, context_step=context_step)
            if schema_result is not None:
                return schema_result
            reply = self.reply_to_project_message(project_id, message, context_step=context_step)
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

    def reply_to_project_message(self, project_id: str, message: str, context_step: str | None = None) -> str:
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
        chat_step = {"search": 1, "screening": 2, "retrieval": 3, "extraction": 4, "categorize": 5}.get(context_step, 1)
        append_jsonl(str(project_path / "chat" / "messages.jsonl"), {"step": chat_step, "role": "u", "text": user_message, "created_at": now})
        append_jsonl(
            str(project_path / "chat" / "messages.jsonl"),
            {"step": chat_step, "role": "a", "text": reply, "created_at": now, "llm_usage": usage or {}, "model": model},
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
            if is_schema_finalized(project_path):
                raise ValueError("Extraction schema is finalized. Use Edit Schema before regenerating it.")
            result = self._call_workflow_action("generate-schema", project_id)
            artifacts.extend(self._verify_stage_artifacts(project_path, "prompt_extraction"))
            return self._action_result(project_path, "prompt_extraction", result, artifacts, action="generate-schema")

        if action == "finalize-schema":
            self._verify_stage_artifacts(project_path, "prompt_extraction")
            result = finalize_schema(project_path)
            artifacts.extend(self._verify_stage_artifacts(project_path, "prompt_extraction"))
            artifacts.append(str(schema_paths(project_path)["finalized"]))
            return self._schema_action_result(
                project_path,
                result,
                artifacts,
                reply="Extraction schema finalized. Run Information Extraction when ready.",
            )

        if action == "edit-schema":
            self._verify_stage_artifacts(project_path, "prompt_extraction")
            schema = load_schema_draft(project_path)
            result = {"status": "schema_unfinalized", "schema": save_schema_draft(project_path, schema)}
            artifacts.extend(self._verify_stage_artifacts(project_path, "prompt_extraction"))
            return self._schema_action_result(
                project_path,
                result,
                artifacts,
                reply="Schema reopened for editing. Refine it in chat or finalize it again.",
            )

        if action == "download-pdfs":
            self._require_completed_stage(project_path, action, "filtering")
            result = self._call_workflow_action("download-pdfs", project_id)
            artifacts.extend(self._verify_stage_artifacts(project_path, "download"))
            return self._action_result(project_path, "download", result, artifacts, action="download-pdfs")

        if action == "run-extraction":
            self._require_completed_stage(project_path, action, "download")
            if not is_schema_finalized(project_path):
                raise ValueError("Action 'run-extraction' requires finalized extraction schema")
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

    def _handle_schema_chat_if_applicable(
        self,
        project_id: str,
        message: str,
        context_step: str | None = None,
    ) -> LeadAgentResult | None:
        project_path = self.output_root / project_id
        current_stage = self._current_stage(project_path)
        schema_finalized = is_schema_finalized(project_path)
        schema = load_schema_draft(project_path)
        if not schema.get("fields"):
            return None
        if context_step != "extraction":
            if schema_finalized:
                return None
            if current_stage not in {"prompt_extraction", "download"} and not schema_paths(project_path)["draft"].exists():
                return None

        config = self._load_search_conditions(project_path)
        user_message = str(message or "").strip()
        command = self._schema_command(config, schema, user_message)
        action = str(command.get("action") or "").strip()
        if schema_finalized and action in {"add_field", "remove_field", "modify_field", "finalize_extraction"}:
            result = {"action": action, "status": "schema_locked", "schema": schema}
        else:
            result = self._apply_schema_command(project_path, command)
        reply = self._schema_reply(result)
        now = datetime.now().isoformat()
        append_jsonl(str(project_path / "chat" / "messages.jsonl"), {"step": 4, "role": "u", "text": user_message, "created_at": now})
        append_jsonl(
            str(project_path / "chat" / "messages.jsonl"),
            {
                "step": 4,
                "role": "a",
                "text": reply,
                "created_at": now,
                "model": str(config.get("model") or LEAD_AGENT_DEV_MODEL),
                "source": "schema_chat",
                "stage": "prompt_extraction",
                "action": result.get("action"),
            },
        )
        paths = schema_paths(project_path)
        schema_finalized = is_schema_finalized(project_path)
        return LeadAgentResult(
            stage="prompt_extraction",
            status="completed",
            reply=reply,
            artifacts=[str(path) for path in (paths["draft"], paths["current"]) if path.exists()],
            next_actions=["edit_schema", "run_extraction"] if schema_finalized else ["finalize_schema", "download_pdfs"],
            data=result,
        )

    def _schema_command(self, config: dict[str, Any], schema: dict[str, Any], message: str) -> dict[str, Any]:
        llm_query = self.llm_query or query_llm
        response_text, _usage = llm_query(
            text_prompt=self._schema_chat_prompt(config, schema, message),
            system_prompt=(
                "Return ONLY valid JSON for a schema command. "
                "Use action show_schema, show_prompt, add_field, remove_field, modify_field, answer_question, or finalize_extraction."
            ),
            model=str(config.get("model") or LEAD_AGENT_DEV_MODEL),
            provider="openai",
        )
        try:
            payload = json.loads(str(response_text or "").strip())
        except json.JSONDecodeError as exc:
            raise ValueError("Lead Agent schema chat did not return valid JSON") from exc
        if not isinstance(payload, dict) or not payload.get("action"):
            raise ValueError("Lead Agent schema chat response missing action")
        return payload

    def _schema_chat_prompt(self, config: dict[str, Any], schema: dict[str, Any], message: str) -> str:
        return f"""Current extraction schema:
{json.dumps(schema, ensure_ascii=False, indent=2)}

Project:
{config.get('project_name') or 'ReviewPilot project'}

Research description:
{config.get('description') or ''}

User message:
{message}

Return ONLY valid JSON:
{{"action": "add_field", "args": {{"name": "snake_case", "type": "Text", "description": "what to extract", "required": false, "example": "example value"}}}}

For remove_field use args.field_name. For modify_field use args.field_name plus new_name, new_type, new_description, new_required, or new_example. For conceptual questions use answer_question with args.response."""

    def _apply_schema_command(self, project_path: Path, command: dict[str, Any]) -> dict[str, Any]:
        action = str(command.get("action") or "").strip()
        args = command.get("args") if isinstance(command.get("args"), dict) else {}
        if action == "show_schema":
            return {"action": action, "status": "shown", "schema": load_schema_draft(project_path)}
        if action == "show_prompt":
            prompt = self._read_json_artifact(schema_paths(project_path)["architecture_prompt"], "prompt_extraction")
            return {"action": action, "status": "shown", "prompt": prompt, "schema": load_schema_draft(project_path)}
        if action == "add_field":
            schema = add_schema_field(project_path, args)
            return {"action": action, "status": "schema_updated", "schema": schema, "field": args.get("name")}
        if action == "remove_field":
            schema = remove_schema_field(project_path, str(args.get("field_name") or ""))
            return {"action": action, "status": "schema_updated", "schema": schema, "field": args.get("field_name")}
        if action == "modify_field":
            schema = modify_schema_field(project_path, str(args.get("field_name") or ""), args)
            return {"action": action, "status": "schema_updated", "schema": schema, "field": args.get("field_name")}
        if action == "answer_question":
            return {"action": action, "status": "answered", "response": str(args.get("response") or "").strip(), "schema": load_schema_draft(project_path)}
        if action == "finalize_extraction":
            result = finalize_schema(project_path)
            return {"action": action, **result}
        raise ValueError(f"Unsupported schema chat action: {action}")

    def _schema_reply(self, result: dict[str, Any]) -> str:
        action = result.get("action")
        if result.get("status") == "schema_locked":
            return "Extraction schema is finalized. Click Edit Schema before changing fields."
        if action == "show_schema":
            fields = result.get("schema", {}).get("fields") or []
            lines = [f"- {field['name']}: {field.get('description', '')}" for field in fields]
            return "Current extraction schema:\n" + "\n".join(lines)
        if action == "show_prompt":
            prompt = result.get("prompt") if isinstance(result.get("prompt"), dict) else {}
            system_prompt = str(prompt.get("system_prompt") or "").strip()
            user_prompt = str(prompt.get("user_prompt_template") or prompt.get("extraction_prompt") or "").strip()
            return f"Current extraction prompt:\n\nSystem:\n{system_prompt}\n\nUser template:\n{user_prompt}".strip()
        if action == "add_field":
            return f"Extraction schema updated. Added field: {result.get('field') or 'new field'}."
        if action == "remove_field":
            return f"Extraction schema updated. Removed field: {result.get('field') or 'requested field'}."
        if action == "modify_field":
            return f"Extraction schema updated. Modified field: {result.get('field') or 'requested field'}."
        if action == "answer_question":
            return result.get("response") or "Refine the schema in chat, then finalize it before running extraction."
        if action == "finalize_extraction":
            return "Extraction schema finalized. Run Information Extraction when ready."
        return "Extraction schema updated."

    def _schema_action_result(self, project_path: Path, result: dict[str, Any], artifacts: list[str], reply: str) -> LeadAgentResult:
        append_jsonl(
            str(project_path / "chat" / "messages.jsonl"),
            {
                "step": 4,
                "role": "a",
                "text": reply,
                "created_at": datetime.now().isoformat(),
                "model": LEAD_AGENT_DEV_MODEL,
                "source": "canvas_action",
                "stage": "prompt_extraction",
                "action": result.get("status"),
            },
        )
        return LeadAgentResult(
            stage="prompt_extraction",
            status="completed",
            reply=reply,
            artifacts=artifacts,
            next_actions=["run_extraction"] if is_schema_finalized(project_path) else ["finalize_schema"],
            data=result,
        )

    def _call_workflow_action(self, action: str, project_id: str, input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.workflow_adapter.run(action, self.output_root, project_id, llm_query=self.llm_query, input_data=input_data)

    def _action_result(self, project_path: Path, stage: str, result: dict[str, Any], artifacts: list[str], action: str) -> LeadAgentResult:
        data = dict(result)
        contract = self.workflow_adapter.contract_for(action)
        data.setdefault("sub_agent", contract.agent_name)
        data.setdefault("contract_stage", contract.stage)
        data.setdefault("model", contract.model)
        reply = self._stage_reply(stage, result, action=action)
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
            next_actions=self._next_actions(stage, action=action),
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

    def _stage_reply(self, stage: str, result: dict[str, Any], action: str | None = None) -> str:
        if action == "suggest-categories":
            field = self._sanitize_prompt_value(str(result.get("field") or "selected field"))
            category_count = result.get("categories")
            if isinstance(category_count, int) and not isinstance(category_count, bool):
                suggestion_text = f"{category_count} category {'suggestion' if category_count == 1 else 'suggestions'}"
            else:
                suggestion_text = "category suggestions"
            return (
                f"Generated {suggestion_text} for {field}. "
                "Review them, select Confirm Categories, then select Apply Categorization."
            )
        if stage == "prompt_extraction":
            field_count = result.get("field_count")
            count_text = ""
            if isinstance(field_count, int) and not isinstance(field_count, bool):
                count_text = f" with {field_count} {'field' if field_count == 1 else 'fields'}"
            return f"Draft extraction schema generated{count_text}. Review it, then select Finalize Schema before running Information Extraction."
        llm_query = self.llm_query or query_llm
        prompt_result = self._sanitize_prompt_value(result)
        response_text, _usage = llm_query(
            text_prompt=f"""A ReviewPilot canvas action completed.

Stage: {stage}
Structured result:
{json.dumps(prompt_result, ensure_ascii=False, indent=2)}

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
        reply = self._parse_project_chat_reply(response_text)
        if self._contains_absolute_path(reply):
            return self._safe_stage_reply(stage, result)
        return reply

    def _sanitize_prompt_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._sanitize_prompt_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._sanitize_prompt_value(item) for item in value]
        if isinstance(value, str):
            stripped = value.strip()
            if Path(stripped).is_absolute() or PureWindowsPath(stripped).is_absolute() or self._contains_absolute_path(value):
                return "project artifact"
            return value
        return value

    def _contains_absolute_path(self, value: str) -> bool:
        return _ABSOLUTE_PATH_MARKER.search(str(value)) is not None

    def _safe_stage_reply(self, stage: str, result: dict[str, Any]) -> str:
        def count(*keys: str) -> int | None:
            for key in keys:
                value = result.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    return value
            return None

        details: list[str] = []
        if stage == "collection":
            total = count("total", "total_papers")
            if total is not None:
                details.append(f"{total} {'paper' if total == 1 else 'papers'} collected")
        elif stage == "filtering":
            included = count("included", "included_count")
            excluded = count("excluded", "excluded_count")
            if included is not None:
                details.append(f"{included} included")
            if excluded is not None:
                details.append(f"{excluded} excluded")
        elif stage == "download":
            success = count("success")
            failed = count("failed")
            if success is not None:
                details.append(f"{success} available")
            if failed is not None:
                details.append(f"{failed} failed")
        elif stage == "extraction":
            processed = count("processed")
            errors = count("errors")
            failed = count("failed") if errors is None else None
            if processed is not None:
                details.append(f"{processed} processed")
            if errors is not None:
                details.append(f"{errors} {'error' if errors == 1 else 'errors'}")
            elif failed is not None:
                details.append(f"{failed} failed")
        elif stage == "categorization":
            categories = count("categories")
            rows = count("rows")
            if categories is not None:
                details.append(f"{categories} {'category' if categories == 1 else 'categories'}")
            if rows is not None:
                details.append(f"{rows} {'row' if rows == 1 else 'rows'} categorized")

        stage_name = {
            "collection": "Collection",
            "filtering": "Paper Screening",
            "download": "Full-Text Retrieval",
            "extraction": "Information Extraction",
            "categorization": "Categorization & Analysis",
        }.get(stage, stage.replace("_", " ").title())
        reply = f"{stage_name} completed"
        if details:
            reply += f": {', '.join(details)}"
        reply += "."
        outcome = result.get("outcome")
        if isinstance(outcome, str) and re.fullmatch(r"[A-Za-z0-9 _-]{1,80}", outcome):
            reply += f" Outcome: {outcome}."
        next_action = {
            "collection": "Paper Screening",
            "filtering": "Full-Text Retrieval",
            "download": "Information Extraction",
            "extraction": "Categorization & Analysis",
        }.get(stage)
        if next_action:
            reply += f" Next action: {next_action}."
        if stage == "download":
            download_stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}
            fallback_candidates = result.get("web_search_fallback_candidates") or download_stats.get("web_search_fallback_candidates")
            if count("failed") and fallback_candidates:
                reply += " ExtractionAgent will use web-search fallback for eligible unavailable papers."
        if stage == "categorization":
            reply += " No next canvas action is required."
        return reply

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
        ledger_stage = {
            "collection": "collection",
            "filtering": "screening",
            "download": "retrieval",
            "extraction": "extraction",
            "categorization": "categorization",
        }.get(required_stage)
        state = load_workflow_state(project_path)
        if ledger_stage is None or state["stages"][ledger_stage]["status"] != "completed":
            raise ValueError(f"Action '{action}' requires completed stage '{required_stage}'")
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

    def _next_actions(self, stage: str, action: str | None = None) -> list[str]:
        if action == "suggest-categories":
            return ["confirm_categories", "apply_categorization"]
        return {
            "search_conditions": ["edit_search", "run_collection"],
            "prompt_relevance": ["run_collection"],
            "collection": ["run_screening"],
            "filtering": ["download_pdfs", "generate_schema"],
            "prompt_extraction": ["finalize_schema"],
            "download": ["run_extraction"],
            "extraction": ["apply_categorization"],
            "categorization": [],
        }.get(stage, [])
