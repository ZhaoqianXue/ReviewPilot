"""Lead Agent orchestration layer for ReviewPilot."""

from __future__ import annotations

import json
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agents.prompt_agent import PromptAgent
from agents.search_condition_agent import SearchConditionAgent
from reviewpilot_core.agent_memory import CrossProjectMemoryService
from reviewpilot_core.screening_criteria import criteria_state, save_criteria, require_finalized_criteria, validate_criteria
from reviewpilot_core.project_store import read_json
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
from reviewpilot_core.safe_text import contains_absolute_path
from reviewpilot_core.sub_agent_contracts import default_sub_agent_contracts
from reviewpilot_core.workflow_adapter import WorkflowActionAdapter
from reviewpilot_core.workflow_state import load_workflow_state, structured_action_outcome
from utils.jsonl_handler import append_jsonl
from utils.llm import query_llm


MEMORY_LOGGER = logging.getLogger("reviewpilot.agent_memory")


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
        memory_service: CrossProjectMemoryService | None = None,
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
        self.memory_service = memory_service or CrossProjectMemoryService(self.output_root)

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
            if context_step == "screening":
                return self._screening_chat(project_id, message)
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
        self._promote_project_memory(project_path, "search_setup", source=search_conditions)
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
        history = self._session_history(project_path)
        memory_context = self._retrieve_memory_context(
            project_id,
            self._chat_memory_kind(context_step),
            config,
        )
        response_text, usage = llm_query(
            text_prompt=self._project_chat_prompt(
                config,
                user_message,
                history=history,
                memory_context=memory_context,
                project_memory=self._local_project_memory(project_path),
            ),
            system_prompt=(
                "You answer project-specific systematic-review questions using the supplied current project facts and conversation context."
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

    def _project_chat_prompt(
        self,
        config: dict[str, Any],
        message: str,
        *,
        history: list[dict[str, str]] | None = None,
        memory_context: str = "",
        project_memory: dict[str, Any] | None = None,
    ) -> str:
        project_data = {
            "project_name": config.get("project_name") or "ReviewPilot project",
            "research_description": config.get("description") or config.get("research_description") or "",
            "search_terms": config.get("search_terms") or "",
            "platforms": config.get("platforms") or [],
            "date_range": config.get("date_range") or {},
        }
        return f"""CURRENT PROJECT DATA (authoritative):
{json.dumps(project_data, ensure_ascii=False)}

SAVED LOCAL PROJECT STATE (authoritative; stale artifacts are marked):
{json.dumps(project_memory or {}, ensure_ascii=False)}

CONVERSATION HISTORY DATA:
{json.dumps(history or [], ensure_ascii=False)}

ADVISORY CROSS-PROJECT MEMORY DATA:
{json.dumps(memory_context or "", ensure_ascii=False)}

CURRENT USER MESSAGE DATA:
{json.dumps(message, ensure_ascii=False)}

Use current project data as authority. Use history for conversational continuity and advisory memory only when it is consistent with the current project.

Return ONLY valid JSON:
{{"reply": "your concise project-specific response"}}"""

    def _parse_project_chat_reply(self, response_text: str) -> str:
        try:
            payload = json.loads(str(response_text or "").strip())
        except json.JSONDecodeError as exc:
            raise ValueError("Lead Agent LLM did not return valid chat JSON") from exc
        if not isinstance(payload, dict) or set(payload) != {"reply"} or not isinstance(payload["reply"], str) or not payload["reply"].strip():
            raise ValueError("Lead Agent LLM response missing reply")
        return str(payload["reply"]).strip()

    def _handle_action(self, project_id: str, action: str, input_data: dict[str, Any] | None = None) -> LeadAgentResult:
        project_path = self.output_root / project_id
        if not project_path.exists():
            raise ValueError(f"Project not found: {project_id}")
        config = self._load_search_conditions(project_path)
        artifacts: list[str] = []

        if action in {"save-criteria", "finalize-criteria"}:
            self._require_completed_stage(project_path, action, "collection")
            saved = save_criteria(project_path, input_data or {}, finalized=action == "finalize-criteria")
            if action == "finalize-criteria":
                self._promote_project_memory(project_path, "screening_profile")
            return LeadAgentResult(stage="prompt_relevance", status="completed",
                reply="Screening criteria finalized. Run screening when ready." if action == "finalize-criteria" else "Screening criteria saved locally as a draft. Review and finalize before screening.",
                data=saved, artifacts=[str(project_path / "prompts/relevance_prompt.json")])

        if action == "collect":
            self._verify_stage_artifacts(project_path, "search_conditions")
            artifacts.append(str(self._ensure_relevance_prompt(project_path, config)))
            result = self._call_workflow_action("collect", project_id)
            artifacts.extend(self._verify_stage_artifacts(project_path, "collection"))
            return self._action_result(project_path, "collection", result, artifacts, action="collect")

        if action == "screen":
            self._require_completed_stage(project_path, action, "collection")
            require_finalized_criteria(project_path)
            artifacts.append(str(self._ensure_relevance_prompt(project_path, config)))
            result = self._call_workflow_action("screen", project_id)
            artifacts.extend(self._verify_stage_artifacts(project_path, "filtering"))
            self._promote_project_memory(project_path, "screening_profile")
            return self._action_result(project_path, "filtering", result, artifacts, action="screen")

        if action == "generate-schema":
            self._require_completed_stage(project_path, action, "filtering")
            if is_schema_finalized(project_path):
                raise ValueError("Extraction schema is finalized. Use Edit Schema before regenerating it.")
            result = self._call_workflow_action("generate-schema", project_id)
            artifacts.extend(self._verify_stage_artifacts(project_path, "prompt_extraction"))
            return self._action_result(project_path, "prompt_extraction", result, artifacts, action="generate-schema")

        if action == "regenerate-schema":
            self._require_completed_stage(project_path, action, "filtering")
            if is_schema_finalized(project_path):
                save_schema_draft(project_path, load_schema_draft(project_path))
            result = self._call_workflow_action("generate-schema", project_id)
            artifacts.extend(self._verify_stage_artifacts(project_path, "prompt_extraction"))
            return self._action_result(project_path, "prompt_extraction", result, artifacts, action="generate-schema")

        if action == "finalize-schema":
            self._verify_stage_artifacts(project_path, "prompt_extraction")
            result = finalize_schema(project_path)
            artifacts.extend(self._verify_stage_artifacts(project_path, "prompt_extraction"))
            artifacts.append(str(schema_paths(project_path)["finalized"]))
            self._promote_project_memory(project_path, "extraction_schema")
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

        if action == "finalize-and-run-extraction":
            self._require_completed_stage(project_path, action, "download")
            self._verify_stage_artifacts(project_path, "prompt_extraction")
            finalize_schema(project_path)
            self._verify_stage_artifacts(project_path, "prompt_extraction")
            self._promote_project_memory(project_path, "extraction_schema")
            artifacts.append(str(self._ensure_extraction_prompt(project_path, config)))
            artifacts.append(str(schema_paths(project_path)["finalized"]))
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
            self._promote_project_memory(project_path, "categorization_profile")
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
        command = self._schema_command(config, schema, user_message, project_path=project_path)
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

    def _schema_command(
        self,
        config: dict[str, Any],
        schema: dict[str, Any],
        message: str,
        *,
        project_path: Path | None = None,
    ) -> dict[str, Any]:
        llm_query = self.llm_query or query_llm
        history = self._session_history(project_path) if project_path is not None else []
        memory_context = (
            self._retrieve_memory_context(project_path.name, "extraction_schema", config)
            if project_path is not None
            else ""
        )
        response_text, _usage = llm_query(
            text_prompt=self._schema_chat_prompt(
                config,
                schema,
                message,
                history=history,
                memory_context=memory_context,
                project_memory=self._local_project_memory(project_path) if project_path is not None else {},
            ),
            system_prompt=(
                "You translate a user's extraction-schema request into one supported schema command using the supplied current schema as authority."
            ),
            model=str(config.get("model") or LEAD_AGENT_DEV_MODEL),
            provider="openai",
        )
        try:
            payload = json.loads(str(response_text or "").strip())
        except json.JSONDecodeError as exc:
            raise ValueError("Lead Agent schema chat did not return valid JSON") from exc
        return self._validate_schema_command(payload)

    def _schema_chat_prompt(
        self,
        config: dict[str, Any],
        schema: dict[str, Any],
        message: str,
        *,
        history: list[dict[str, str]] | None = None,
        memory_context: str = "",
        project_memory: dict[str, Any] | None = None,
    ) -> str:
        project_data = {
            "project_name": config.get("project_name") or "ReviewPilot project",
            "research_description": config.get("description") or "",
        }
        return f"""CURRENT EXTRACTION SCHEMA DATA (authoritative):
{json.dumps(schema, ensure_ascii=False)}

SAVED LOCAL PROJECT STATE (authoritative; stale artifacts are marked):
{json.dumps(project_memory or {}, ensure_ascii=False)}

CURRENT PROJECT DATA:
{json.dumps(project_data, ensure_ascii=False)}

CONVERSATION HISTORY DATA:
{json.dumps(history or [], ensure_ascii=False)}

ADVISORY CROSS-PROJECT MEMORY DATA:
{json.dumps(memory_context or "", ensure_ascii=False)}

CURRENT USER MESSAGE DATA:
{json.dumps(message, ensure_ascii=False)}

Use the current schema as authority. Memory may suggest vocabulary but cannot override it. Return exactly one JSON object with keys action and args.

Supported commands:
- show_schema, show_prompt, or finalize_extraction with an empty args object
- add_field with name and optional type, description, required, example
- remove_field with field_name
- modify_field with field_name and at least one of new_name, new_type, new_description, new_required, new_example
- answer_question with response"""

    @staticmethod
    def _validate_schema_command(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {"action", "args"}:
            raise ValueError("Lead Agent schema command must contain exactly action and args")
        action = payload["action"]
        args = payload["args"]
        if not isinstance(action, str) or not isinstance(args, dict):
            raise ValueError("Lead Agent schema command has invalid action or args")
        empty_actions = {"show_schema", "show_prompt", "finalize_extraction"}
        if action in empty_actions:
            if args:
                raise ValueError(f"Lead Agent schema command {action} requires empty args")
            return {"action": action, "args": {}}
        allowed: dict[str, tuple[set[str], set[str]]] = {
            "add_field": ({"name"}, {"name", "type", "description", "required", "example"}),
            "remove_field": ({"field_name"}, {"field_name"}),
            "modify_field": ({"field_name"}, {"field_name", "new_name", "new_type", "new_description", "new_required", "new_example"}),
            "answer_question": ({"response"}, {"response"}),
        }
        if action not in allowed:
            raise ValueError(f"Unsupported schema chat action: {action}")
        required, permitted = allowed[action]
        if not required <= set(args) or not set(args) <= permitted:
            raise ValueError(f"Lead Agent schema command {action} has invalid args")
        if action == "modify_field" and set(args) == {"field_name"}:
            raise ValueError("Lead Agent modify_field command requires an update")
        for key, value in args.items():
            if key in {"required", "new_required"}:
                if type(value) is not bool:
                    raise ValueError(f"Lead Agent schema command {key} must be boolean")
            elif not isinstance(value, str) or not value.strip():
                raise ValueError(f"Lead Agent schema command {key} must be non-empty text")
        return {"action": action, "args": args}

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
        memory_kind = {
            "save-search-setup": "search_setup",
            "screen": "screening_profile",
            "generate-schema": "extraction_schema",
            "suggest-categories": "categorization_profile",
        }.get(action)
        enriched_input = dict(input_data or {})
        if memory_kind:
            project_path = self.output_root / project_id
            config = enriched_input
            if (project_path / "search_conditions.json").exists():
                config = self._load_search_conditions(project_path)
            memory_context = self._retrieve_memory_context(project_id, memory_kind, config)
            if memory_context:
                enriched_input["memory_context"] = memory_context
        forwarded_input = enriched_input if enriched_input or input_data is not None else None
        return self.workflow_adapter.run(
            action,
            self.output_root,
            project_id,
            llm_query=self.llm_query,
            input_data=forwarded_input,
        )

    def _action_result(self, project_path: Path, stage: str, result: dict[str, Any], artifacts: list[str], action: str) -> LeadAgentResult:
        data = dict(result)
        outcome, counts = structured_action_outcome(action, data)
        data["outcome"] = outcome
        data.update(counts)
        contract = self.workflow_adapter.contract_for(action)
        data.setdefault("sub_agent", contract.agent_name)
        data.setdefault("contract_stage", contract.stage)
        data.setdefault("model", contract.model)
        reply = self._stage_reply(stage, data, action=action)
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
            status=outcome,
            reply=reply,
            artifacts=artifacts,
            next_actions=self._next_actions(stage, action=action) if outcome != "failed" else [],
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
        if result.get("outcome") in {"partial", "failed"}:
            return self._safe_stage_reply(stage, result)
        if action == "suggest-categories":
            field = self._sanitize_prompt_value(str(result.get("field") or "selected field"))
            category_count = result.get("categories")
            if isinstance(category_count, int) and not isinstance(category_count, bool):
                suggestion_text = f"{category_count} category {'suggestion' if category_count == 1 else 'suggestions'}"
                review_pronoun = "it" if category_count == 1 else "them"
            else:
                suggestion_text = "category suggestions"
                review_pronoun = "them"
            return (
                f"Generated {suggestion_text} for {field}. "
                f"Review {review_pronoun}, select Confirm Categories, then select Apply Categorization."
            )
        if stage == "prompt_extraction":
            field_count = result.get("field_count")
            count_text = ""
            if isinstance(field_count, int) and not isinstance(field_count, bool):
                count_text = f" with {field_count} {'field' if field_count == 1 else 'fields'}"
            return f"Draft extraction schema generated{count_text}. Review it, then select Finalize Schema before running Information Extraction."
        return self._safe_stage_reply(stage, result)

    def _sanitize_prompt_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._sanitize_prompt_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._sanitize_prompt_value(item) for item in value]
        if isinstance(value, str):
            if contains_absolute_path(value):
                return "project artifact"
            return value
        return value

    def _contains_absolute_path(self, value: str) -> bool:
        return contains_absolute_path(value)

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
        outcome = str(result.get("outcome") or "completed")
        outcome_label = {"completed": "completed", "partial": "partially completed", "failed": "failed"}.get(outcome, "completed")
        reply = f"{stage_name} {outcome_label}"
        if details:
            reply += f": {', '.join(details)}"
        reply += "."
        next_action = {
            "collection": "Paper Screening",
            "filtering": "Full-Text Retrieval",
            "download": "Information Extraction",
            "extraction": "Categorization & Analysis",
        }.get(stage)
        if next_action and outcome in {"completed", "partial"}:
            reply += f" Next action: {next_action}."
        if outcome == "partial":
            reply += " Failed items remain retryable in the recovery step."
        elif outcome == "failed":
            reply += " This stage is blocked until its failed items are recovered."
        if stage == "download":
            download_stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}
            fallback_candidates = result.get("web_search_fallback_candidates") or download_stats.get("web_search_fallback_candidates")
            if count("failed") and fallback_candidates:
                reply += " ExtractionAgent will use web-search fallback for eligible unavailable papers."
        if stage == "categorization":
            reply += " No next canvas action is required."
        return reply

    def _local_project_memory(self, project_path: Path) -> dict[str, Any]:
        """Read existing local artifacts afresh; no secondary memory copy to drift."""
        workflow = load_workflow_state(project_path)
        return {
            "screening_criteria": criteria_state(project_path),
            "extraction_schema": load_schema_draft(project_path),
            "schema_finalized": is_schema_finalized(project_path),
            "categorization": {key: value for key, value in read_json(project_path / "categorization/categorization_mapping.json", {}).items()
                               if key in {"field", "mode", "categories", "category_descriptions"}},
            "workflow": workflow["stages"],
        }

    def _screening_chat(self, project_id: str, message: str) -> LeadAgentResult:
        project = self.output_root / project_id
        self._require_completed_stage(project, "refine-criteria", "collection")
        config = self._load_search_conditions(project)
        current = criteria_state(project)
        response, usage = (self.llm_query or query_llm)(
            text_prompt=f"""Refine screening criteria or answer the user's question.
CURRENT LOCAL PROJECT DATA:
{json.dumps(self._local_project_memory(project), ensure_ascii=False)}
REVIEW SCOPE DATA:
{json.dumps({key: config.get(key) for key in ('description', 'primary_topic', 'domain')}, ensure_ascii=False)}
CONVERSATION HISTORY DATA:
{json.dumps(self._session_history(project), ensure_ascii=False)}
CURRENT USER MESSAGE DATA:
{json.dumps(message, ensure_ascii=False)}
Return only JSON with exactly these keys: {{"reply": "concise response", "criteria": null}}.
For an explicit request to change eligibility rules, replace null with {{"inclusion": ["complete updated rule list"], "exclusion": ["complete updated rule list"]}}.
Preserve unaffected rules. Use null for questions and requests to recall information. Changes are saved as a draft for human review; finalization is a canvas action.
""",
            system_prompt="You help the reviewer define observable inclusion and exclusion rules. Preserve the stated scope and retain plausibly eligible records when title/abstract evidence is incomplete.",
            model=str(config.get("model") or LEAD_AGENT_DEV_MODEL), provider="openai")
        command = json.loads(str(response).strip())
        if not isinstance(command, dict) or set(command) != {"reply", "criteria"} or not isinstance(command["reply"], str) or not command["reply"].strip():
            raise ValueError("Invalid screening chat response")
        reply = command["reply"].strip()
        if command["criteria"] is not None:
            if not isinstance(command["criteria"], dict) or set(command["criteria"]) != {"inclusion", "exclusion"}:
                raise ValueError("Invalid screening criteria response")
            criteria = validate_criteria(command["criteria"])
            save_criteria(project, {**criteria, "revision": current["revision"]})
            reply += "\n\nCriteria saved locally as a draft. Review them in Step 2 and click Finalize Criteria."
        now = datetime.now().isoformat()
        for role, text in (("u", message), ("a", reply)):
            append_jsonl(str(project / "chat/messages.jsonl"), {"step": 2, "role": role, "text": text, "created_at": now})
        return LeadAgentResult(stage="prompt_relevance", status="completed", reply=reply)

    def _session_history(self, project_path: Path) -> list[dict[str, str]]:
        path = project_path / "chat" / "messages.jsonl"
        if not path.exists():
            return []
        if path.is_symlink() or not path.is_file():
            MEMORY_LOGGER.warning("session_read_failed")
            return []
        history: list[dict[str, str]] = []
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    role = {"u": "user", "user": "user", "a": "assistant", "assistant": "assistant"}.get(
                        str(row.get("role") or "").strip().lower()
                    )
                    text = row.get("text")
                    if role and isinstance(text, str) and text.strip():
                        history.append({"role": role, "text": text.strip()})
        except (OSError, UnicodeError):
            MEMORY_LOGGER.warning("session_read_failed")
            return []
        return history

    def _chat_memory_kind(self, context_step: str | None) -> str | None:
        return {
            "search": "search_setup",
            "screening": "screening_profile",
            "extraction": "extraction_schema",
            "categorize": "categorization_profile",
        }.get(context_step or "search")

    def _retrieve_memory_context(self, project_id: str, kind: str | None, config: dict[str, Any]) -> str:
        if not kind:
            return ""
        return self.memory_service.retrieve_context(
            kinds=[kind],
            project_id=project_id,
            domain=str(config.get("domain") or config.get("description") or ""),
            topic=str(config.get("primary_topic") or config.get("project_name") or config.get("search_terms") or ""),
        )

    def _promote_project_memory(
        self,
        project_path: Path,
        kind: str,
        *,
        source: dict[str, Any] | None = None,
    ) -> None:
        config = source if kind == "search_setup" and isinstance(source, dict) else self._load_search_conditions(project_path)
        if kind == "search_setup":
            allowed = ("search_terms", "platforms", "date_range", "source_limits", "keywords")
            payload = {key: config[key] for key in allowed if key in config}
            source_artifact = "search_conditions.json"
        elif kind == "screening_profile":
            prompt = self._read_json_artifact(project_path / "prompts" / "relevance_prompt.json", "prompt_relevance")
            allowed = ("task", "instruction", "system_prompt", "user_prompt_template")
            payload = {key: prompt[key] for key in allowed if key in prompt}
            source_artifact = "prompts/relevance_prompt.json"
        elif kind == "extraction_schema":
            schema = load_schema_draft(project_path)
            payload = {"fields": schema.get("fields") or []}
            source_artifact = "extraction/extraction_schema.json"
        elif kind == "categorization_profile":
            mapping = self._read_json_artifact(
                project_path / "categorization" / "categorization_mapping.json",
                "categorization mapping",
            )
            allowed = ("field", "mode", "categories", "category_descriptions")
            payload = {key: mapping[key] for key in allowed if key in mapping}
            source_artifact = "categorization/categorization_mapping.json"
        else:
            return
        if not payload:
            return
        revision = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.memory_service.promote(
            kind=kind,
            project_id=project_path.name,
            payload=payload,
            source_artifact=source_artifact,
            source_revision=revision,
            domain=str(config.get("domain") or config.get("description") or ""),
            topic=str(config.get("primary_topic") or config.get("project_name") or config.get("search_terms") or ""),
        )

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
        if ledger_stage is None or state["stages"][ledger_stage]["status"] not in {"completed", "partial"}:
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
