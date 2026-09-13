"""
Prompt Agent.
Generates prompts for:
1. Relevance checking during filtering
2. Information extraction from PDFs

Uses structured templates with Task, Input, and Instruction components.
"""

from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
import json
import re

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
from reviewpilot_core.extraction_schema import build_extraction_prompts, save_schema_draft
from reviewpilot_core.model_policy import PROMPT_MODEL
from utils.human_interaction import (
    ask_text, ask_confirm, print_header, print_subheader, print_box, print_text
)
from utils.jsonl_handler import save_json


class PromptAgent(BaseAgent):
    """
    Agent responsible for generating prompts for filtering and extraction.

    Creates structured prompts with Task, Input, and Instruction components
    based on user's research requirements.
    """

    def __init__(self, project_path: Path, model: str = PROMPT_MODEL, llm_query=None):
        super().__init__(project_path, "prompt")
        self.model = model
        self.llm_query = llm_query

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate prompts based on search conditions."""
        prompt_type = input_data.get("prompt_type", "relevance")

        if prompt_type == "relevance":
            return self.generate_relevance_prompt(input_data)
        elif prompt_type == "extraction":
            return self.generate_extraction_prompt(input_data)
        else:
            raise ValueError(f"Unknown prompt type: {prompt_type}")

    def generate_relevance_prompt(self, search_conditions: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate relevance checking prompt based on search conditions.

        If primary_topic and domain are already provided by the caller,
        auto-generates the prompt without asking questions.

        Otherwise asks user for:
        1. Primary topic/method (e.g., "LLM-as-a-Judge")
        2. Domain/context (e.g., "healthcare, clinical, medical")
        3. Any synonyms or related terms
        """
        search_terms = search_conditions.get("search_terms", "")

        # Check if primary_topic and domain were already supplied.
        primary_topic = search_conditions.get("primary_topic", "")
        domain = search_conditions.get("domain", "")

        if primary_topic and domain:
            # Auto-generate without interactive prompts
            print(f"\n  Auto-generating relevance prompt...")
            print(f"  Primary topic: {primary_topic}")
            print(f"  Domain: {domain}")

            relevance_prompt = self._build_relevance_prompt(
                primary_topic=primary_topic,
                primary_synonyms="",
                domain=domain,
                domain_synonyms="",
                positive_example="",
                negative_example=""
            )
        else:
            # Interactive mode - ask user for details
            print_header("Relevance Check Prompt Generation")
            print(f"  Based on your search: {search_terms[:80]}...")
            print()
            print("  To generate an effective relevance prompt, please describe:")
            print()

            # Primary topic/method
            primary_topic = ask_text(
                "What is the PRIMARY topic or method you're looking for?",
                default="",
                required=True
            )

            # Ask for synonyms of primary topic
            print(f"\n  What are synonyms or related terms for '{primary_topic}'?")
            print("  (e.g., for 'LLM-as-a-Judge': LLM judge, agent-as-a-judge, LLM evaluation)")
            primary_synonyms = ask_text(
                "Synonyms (comma-separated)",
                default="",
                required=False
            )

            # Domain/context
            domain = ask_text(
                "\nWhat DOMAIN or CONTEXT must the paper be in?",
                default="",
                required=True
            )

            # Ask for synonyms of domain
            print(f"\n  What are synonyms or related terms for '{domain}'?")
            domain_synonyms = ask_text(
                "Synonyms (comma-separated)",
                default="",
                required=False
            )

            # Ask for positive and negative examples
            print("\n  Provide example paper titles to help calibrate the prompt:")

            positive_example = ask_text(
                "Example of a RELEVANT paper title",
                default="",
                required=False
            )

            negative_example = ask_text(
                "Example of an IRRELEVANT paper title",
                default="",
                required=False
            )

            # Build the prompt
            relevance_prompt = self._build_relevance_prompt(
                primary_topic=primary_topic,
                primary_synonyms=primary_synonyms,
                domain=domain,
                domain_synonyms=domain_synonyms,
                positive_example=positive_example,
                negative_example=negative_example
            )

            # Show preview
            print_subheader("Generated Relevance Prompt")
            print("\n  TASK:")
            print_text(relevance_prompt["task"], indent=4)
            print("\n  INSTRUCTION:")
            print_text(relevance_prompt["instruction"], indent=4)

            # Ask for approval
            if not ask_confirm("\nApprove this prompt?", default=True):
                print("\n  You can edit the task description:")
                custom_task = ask_text("Custom task (or Enter to keep)", required=False)
                if custom_task:
                    relevance_prompt["task"] = custom_task
                    relevance_prompt = self._rebuild_prompt_from_task(relevance_prompt)

        # Add metadata
        relevance_prompt["prompt_type"] = "relevance_check"
        relevance_prompt["generated_at"] = datetime.now().isoformat()
        relevance_prompt["based_on_search_terms"] = search_terms
        relevance_prompt["primary_topic"] = primary_topic
        relevance_prompt["domain"] = domain

        # Save prompt to prompts folder
        prompts_dir = self.ensure_directory("prompts")
        output_file = prompts_dir / "relevance_prompt.json"
        save_json(str(output_file), relevance_prompt)
        self.log(f"Relevance prompt saved to: {output_file}")

        # Save state
        self.state["relevance_prompt"] = relevance_prompt
        self.save_state()

        return relevance_prompt

    def _build_relevance_prompt(
        self,
        primary_topic: str,
        primary_synonyms: str,
        domain: str,
        domain_synonyms: str,
        positive_example: str = "",
        negative_example: str = ""
    ) -> Dict[str, Any]:
        """
        Build a structured relevance checking prompt.

        Returns prompt with Task, Input, and Instruction components.
        """
        # Parse synonyms
        primary_syns = [s.strip() for s in primary_synonyms.split(",") if s.strip()]
        domain_syns = [s.strip() for s in domain_synonyms.split(",") if s.strip()]

        # Build synonym strings
        if primary_syns:
            primary_syn_str = f" (synonyms: {', '.join(primary_syns)})"
        else:
            primary_syn_str = ""

        if domain_syns:
            domain_syn_str = f" (synonyms: {', '.join(domain_syns)})"
        else:
            domain_syn_str = ""

        criteria = {
            "topic": {"label": primary_topic, "equivalent_terms": primary_syns},
            "context": {"label": domain, "equivalent_terms": domain_syns},
        }
        task = "Assess whether the supplied title-and-abstract record is eligible or plausibly eligible for the stated review scope."

        # Input format
        input_format = "Paper Title: {title}\nPaper Abstract (if available): {abstract}\n"

        examples = []
        if positive_example:
            examples.append({"record": positive_example, "decision": "True"})
        if negative_example:
            examples.append({"record": negative_example, "decision": "False"})

        # Instruction
        instruction = (
            "Use the supplied record evidence and review-scope data. Return True when the record clearly fits or remains plausibly eligible because title-and-abstract evidence is incomplete. "
            "Return False only when explicit record evidence establishes material incompatibility with the stated topic or context. "
            "Treat words describing the review activity as the reviewer's synthesis intent rather than a candidate publication-type requirement. "
            "Return exactly one token: True or False."
        )

        # System prompt
        system_prompt = "You screen scholarly records conservatively against a stated review scope using only the supplied record evidence."

        # User prompt template (combines all components)
        user_prompt_template = f"""TASK: {task}

INPUT:
{input_format}

REVIEW SCOPE DATA:
{json.dumps(criteria, ensure_ascii=False)}

REVIEWER-SUPPLIED EXAMPLES (data):
{json.dumps(examples, ensure_ascii=False)}

INSTRUCTION:
{instruction}

Your response (True/False):"""

        return {
            "system_prompt": system_prompt,
            "user_prompt_template": user_prompt_template,
            "task": task,
            "input_format": input_format,
            "instruction": instruction,
            "expected_output": "True or False",
            "criteria": criteria,
            "examples": examples,
        }

    def _rebuild_prompt_from_task(self, prompt: Dict[str, Any]) -> Dict[str, Any]:
        """Rebuild the user prompt template from updated task."""
        prompt["user_prompt_template"] = f"""TASK: {prompt["task"]}

INPUT:
{prompt["input_format"]}

INSTRUCTION:
{prompt["instruction"]}

Your response (True/False):"""
        return prompt

    def generate_extraction_prompt(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate information extraction prompt.

        If auto_approve is set, uses extraction_fields from config or defaults.
        """
        auto_approve = input_data.get("auto_approve", False)
        llm_query = input_data.get("llm_query") or self.llm_query

        if auto_approve:
            # Use fields from config or defaults
            extraction_fields = input_data.get(
                "extraction_fields",
                "datasets used, methods, key findings, evaluation metrics"
            )
            print(f"\n  Generating extraction prompt for: {extraction_fields}")
            structured = True
        else:
            # Interactive mode
            print_header("Information Extraction Prompt Generation")

            print("  What information would you like to extract from each paper?")
            print("  Examples: datasets, methods, key findings, evaluation metrics")
            print()

            extraction_fields = ask_text(
                "Fields to extract (describe or list)",
                default="datasets used, methods, key findings, evaluation metrics",
                required=True
            )

            # Ask about output format
            print("\n  Output format options:")
            print("  1. Structured JSON (separate fields)")
            print("  2. Markdown (formatted text)")
            format_choice = ask_text(
                "Preferred output format (1 or 2)",
                default="1",
                required=True
            )

            structured = format_choice == "1"

        # Generate the prompt
        extraction_prompt = self._build_extraction_prompt(extraction_fields, structured)

        if not auto_approve:
            # Show preview
            print_subheader("Generated Extraction Prompt")
            print()
            print_text(extraction_prompt["user_prompt_template"], indent=4)

            # Ask for approval
            if not ask_confirm("Approve this prompt?", default=True):
                custom_prompt = ask_text("Enter custom extraction prompt", required=False)
                if custom_prompt:
                    extraction_prompt["user_prompt_template"] = custom_prompt

        # Add metadata
        extraction_prompt["prompt_type"] = "extraction"
        extraction_prompt["generated_at"] = datetime.now().isoformat()
        extraction_prompt["extraction_fields"] = extraction_fields
        extraction_prompt["output_structured"] = structured
        schema, source, usage = self._generate_extraction_schema(input_data, extraction_prompt, llm_query)
        system_prompt, stage_prompt, user_prompt_template = build_extraction_prompts(input_data, schema)
        extraction_prompt["system_prompt"] = system_prompt
        extraction_prompt["user_prompt_template"] = user_prompt_template
        extraction_prompt["schema"] = schema
        extraction_prompt["source"] = source
        extraction_prompt["usage"] = usage

        # Save prompt to prompts folder
        prompts_dir = self.ensure_directory("prompts")
        output_file = prompts_dir / "extraction_prompt.json"
        save_json(str(output_file), extraction_prompt)
        self.log(f"Extraction prompt saved to: {output_file}")

        extraction_dir = self.ensure_directory("extraction")
        save_schema_draft(self.project_path, schema)
        save_json(
            str(extraction_dir / "extraction_prompt.json"),
            {
                "system_prompt": system_prompt,
                "extraction_prompt": stage_prompt,
                "schema": schema,
                "source": source,
                "usage": usage,
            },
        )

        # Save state
        self.state["extraction_prompt"] = extraction_prompt
        self.save_state()

        return extraction_prompt

    def _build_extraction_prompt(self, extraction_fields: str, structured: bool) -> Dict[str, Any]:
        """Build the information extraction prompt."""

        # Parse fields
        fields = [f.strip() for f in extraction_fields.split(",")]

        system_prompt = "You extract source-grounded evidence from scholarly papers into requested fields."

        if structured:
            # Build JSON schema from fields
            schema_fields = []
            for field in fields:
                field_key = field.lower().replace(" ", "_")
                schema_fields.append(f'  "{field_key}": "..."')

            schema = "{\n" + ",\n".join(schema_fields) + "\n}"

            user_prompt_template = f"""TASK: Extract the following information from the research paper.

FIELDS TO EXTRACT:
{extraction_fields}

PAPER CONTENT:
{{paper_text}}

INSTRUCTION:
Populate each requested field from the supplied paper evidence. Use an empty string when the evidence does not support a field. Return exactly one JSON object with the requested field names.

OUTPUT FORMAT:
{schema}

Your JSON response:"""

        else:
            user_prompt_template = f"""TASK: Extract and summarize the following information from the research paper.

FIELDS TO EXTRACT:
{extraction_fields}

PAPER CONTENT:
{{paper_text}}

INSTRUCTION:
Populate each requested field from the supplied paper evidence. Use an empty value when the evidence does not support a field, and use one heading per requested field.

Your response:"""

        return {
            "system_prompt": system_prompt,
            "user_prompt_template": user_prompt_template,
            "output_format": "json" if structured else "markdown",
            "fields": fields
        }

    def _generate_extraction_schema(self, input_data: Dict[str, Any], extraction_prompt: Dict[str, Any], llm_query) -> tuple[Dict[str, Any], str, Dict[str, Any]]:
        query = llm_query or self._default_llm_query
        response, usage = query(
            text_prompt=self._schema_generation_prompt(input_data, extraction_prompt),
            system_prompt="You design JSON extraction schemas for systematic literature reviews. Return only valid JSON.",
            model=self.model,
            provider="openai",
        )
        schema = self._normalize_schema(self._extract_json(str(response)))
        if not schema["fields"]:
            raise ValueError("PromptAgent LLM response did not include extraction schema fields")
        return schema, "llm", usage or {}

    def _schema_generation_prompt(self, input_data: Dict[str, Any], extraction_prompt: Dict[str, Any]) -> str:
        included = input_data.get("included_papers") or []
        sample_papers = []
        for paper in included[:3]:
            abstract = str(paper.get("abstract") or "")
            sample_papers.append({
                "title": paper.get("title") or "Untitled",
                "abstract_excerpt": abstract[:500],
                "abstract_truncated": len(abstract) > 500,
            })
        project_data = {
            "research_question": input_data.get("description") or input_data.get("search_terms") or input_data.get("project_name") or "",
            "primary_topic": input_data.get("primary_topic") or "",
            "domain": input_data.get("domain") or "",
            "requested_extraction_fields": extraction_prompt.get("extraction_fields") or extraction_prompt.get("fields") or [],
        }
        return f"""Design an extraction schema for a systematic review.

CURRENT PROJECT DATA (authoritative):
{json.dumps(project_data, ensure_ascii=False)}

INCLUDED PAPER SAMPLE DATA:
{json.dumps(sample_papers, ensure_ascii=False)}

ADVISORY CROSS-PROJECT MEMORY DATA:
{json.dumps(input_data.get("memory_context") or "", ensure_ascii=False)}

Return ONLY valid JSON:
{{
  "fields": [
    {{
      "name": "snake_case_name",
      "type": "Text",
      "description": "What to extract from the full text",
      "required": false,
      "example": "Example value"
    }}
  ]
}}

Generate 8-12 fields when the request is broad. Preserve user-requested concepts. Use the paper samples only to assess field feasibility. Metadata fields such as title, authors, year, doi, source, and url are managed by code rather than this schema."""

    def _build_extraction_stage_prompt(self, input_data: Dict[str, Any], schema: Dict[str, Any]) -> tuple[str, str]:
        system_prompt, extraction_prompt, _template = build_extraction_prompts(input_data, schema)
        return system_prompt, extraction_prompt

    def _normalize_schema(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(raw, dict) or set(raw) != {"fields"} or not isinstance(raw["fields"], list) or not 1 <= len(raw["fields"]) <= 20:
            raise ValueError("PromptAgent extraction schema must contain 1 to 20 fields")
        fields = []
        for item in raw["fields"]:
            if not isinstance(item, dict) or set(item) != {"name", "type", "description", "required", "example"}:
                raise ValueError("PromptAgent extraction schema field has an invalid shape")
            if not all(isinstance(item[key], str) for key in ("name", "type", "description", "example")) or type(item["required"]) is not bool:
                raise ValueError("PromptAgent extraction schema field has invalid value types")
            name = item["name"].strip()
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or not item["type"].strip() or not item["description"].strip():
                raise ValueError("PromptAgent extraction schema field has invalid content")
            fields.append({key: item[key].strip() if isinstance(item[key], str) else item[key] for key in ("name", "type", "description", "required", "example")})
        if len({field["name"] for field in fields}) != len(fields):
            raise ValueError("PromptAgent extraction schema field names must be unique")
        from reviewpilot_core.extraction_schema import validate_schema
        return validate_schema({"fields": fields})

    def _extract_json(self, text: str) -> Dict[str, Any]:
        stripped = text.strip()
        if "```json" in stripped:
            stripped = stripped.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in stripped:
            stripped = stripped.split("```", 1)[1].split("```", 1)[0]
        return json.loads(stripped)

    def _prompt_text(self, prompt: Dict[str, Any]) -> str:
        if not isinstance(prompt, dict):
            return str(prompt or "")
        return "\n".join(str(prompt.get(key) or "") for key in ("task", "instruction", "user_prompt_template") if prompt.get(key))

    def _default_llm_query(self, *args, **kwargs):
        from utils.llm import query_llm

        return query_llm(*args, **kwargs)
