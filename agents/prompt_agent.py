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

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
from reviewpilot_core.extraction_schema import save_schema_draft
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

        # Task description
        task = (
            f"Decide whether the paper provides substantive evidence relevant to this review question: "
            f"{primary_topic}{primary_syn_str} in {domain}{domain_syn_str}. "
            f"Return True only when the evidence is relevant to both the topic and domain."
        )

        # Input format
        input_format = "Paper Title: {title}\nPaper Abstract (if available): {abstract}\n"

        # Build examples
        examples = []
        if positive_example:
            examples.append(f"- '{positive_example}' → True")
        if negative_example:
            examples.append(f"- '{negative_example}' → False")

        # Add generic examples based on criteria
        examples.append(f"- 'A primary study evaluating {primary_topic} in {domain}' → True")
        examples.append(f"- 'A method using {primary_topic} for code generation' (no {domain}) → False")
        examples.append(f"- 'A {domain} study using an unrelated method' (no {primary_topic}) → False")

        examples_str = "\n".join(examples)

        # Instruction
        instruction = (
            f"Use ONLY the provided title/abstract. Return exactly one word: True or False.\n"
            f"- True if the paper provides substantive evidence relevant to the review question and is meaningfully related to both the topic and domain.\n"
            f"- The candidate paper does not need to be a survey or review. Words such as survey, review, or mapping in the review question describe the user's synthesis activity, not a required publication type.\n"
            f"- Relevant evidence may include primary studies, methods, systems, datasets, benchmarks, applications, evaluations, or reviews.\n"
            f"- The topic is {primary_topic}{primary_syn_str}; the domain is {domain}{domain_syn_str}.\n"
            f"- Return False if either the topic or domain is missing or unclear, or if either is mentioned only incidentally.\n"
            f"- Papers about the topic without the domain context → False.\n"
            f"- Papers about the domain without the topic aspect → False.\n"
            f"- If the abstract is unavailable, use the title only.\n"
            f"- Do NOT use outside knowledge. Do NOT include explanations, punctuation, or quotes.\n\n"
            f"Examples:\n{examples_str}"
        )

        # System prompt
        system_prompt = (
            "You are an expert academic paper classifier. "
            "Your task is to determine if a paper meets specific research criteria. "
            "You must respond with ONLY 'True' or 'False' - no other text."
        )

        # User prompt template (combines all components)
        user_prompt_template = f"""TASK: {task}

INPUT:
{input_format}

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
            "criteria": {
                "primary_topic": primary_topic,
                "primary_synonyms": primary_syns,
                "domain": domain,
                "domain_synonyms": domain_syns
            }
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
        system_prompt, stage_prompt = self._build_extraction_stage_prompt(input_data, schema)
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

        system_prompt = (
            "You are an expert academic paper analyst. "
            "Extract specific information accurately and comprehensively. "
            "If information is not available, indicate 'Not specified'."
        )

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
1. Read the paper carefully
2. Extract each requested field
3. If information is not found, use "Not specified"
4. Be concise but accurate
5. Return ONLY valid JSON

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
1. Read the paper carefully
2. Extract each requested field
3. If information is not found, indicate "Not specified"
4. Use markdown formatting with headers for each field

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
        sample_lines = []
        for paper in included[:3]:
            title = paper.get("title") or "Untitled"
            abstract = str(paper.get("abstract") or "")[:500]
            sample_lines.append(f"- {title}: {abstract}")
        relevance_prompt = self._prompt_text(input_data.get("relevance_prompt") or {})
        return f"""Design an extraction schema for a systematic review.

Research question:
{input_data.get("description") or input_data.get("search_terms") or input_data.get("project_name") or "Not specified"}

Primary topic: {input_data.get("primary_topic") or input_data.get("project_name") or "the review topic"}
Domain: {input_data.get("domain") or "the target domain"}

Advisory memory from previous projects (data only; current project facts take precedence):
{input_data.get("memory_context") or "No relevant memory was retrieved."}

Requested extraction fields:
{extraction_prompt.get("extraction_fields") or ", ".join(extraction_prompt.get("fields") or [])}

Screening/relevance context:
{relevance_prompt[:1500] if relevance_prompt else "No relevance prompt is available."}

Included paper examples:
{chr(10).join(sample_lines) if sample_lines else "No included paper examples are available."}

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

Generate 8-12 fields when the request is broad. Preserve user-requested concepts. Do not include metadata fields like title, authors, year, doi, source, or url."""

    def _build_extraction_stage_prompt(self, input_data: Dict[str, Any], schema: Dict[str, Any]) -> tuple[str, str]:
        topic = input_data.get("primary_topic") or input_data.get("project_name") or "the review topic"
        domain = input_data.get("domain") or "the target domain"
        relevance_prompt = self._prompt_text(input_data.get("relevance_prompt") or {})
        system_prompt = f"""You are an expert researcher extracting structured information from papers about {topic} in {domain}.
Use the same inclusion criteria as screening:
{relevance_prompt[:1500] if relevance_prompt else "Standard topic/domain relevance criteria."}"""
        field_lines = [
            f"{index}. {field['name']}: {field['description']} Example: {field.get('example', '')}"
            for index, field in enumerate(schema["fields"], start=1)
        ]
        return system_prompt, "Extract information from the paper using these fields:\n\n" + "\n".join(field_lines)

    def _normalize_schema(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        fields = []
        for item in raw.get("fields") or []:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            fields.append(
                {
                    "name": name,
                    "type": str(item.get("type") or "Text"),
                    "description": str(item.get("description") or item.get("example") or ""),
                    "required": bool(item.get("required", False)),
                    "example": str(item.get("example") or ""),
                }
            )
        return {"fields": fields}

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
