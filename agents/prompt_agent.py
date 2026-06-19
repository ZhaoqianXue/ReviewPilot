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

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
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

    def __init__(self, project_path: Path):
        super().__init__(project_path, "prompt")

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

        If primary_topic and domain are already provided (from chat.py),
        auto-generates the prompt without asking questions.

        Otherwise asks user for:
        1. Primary topic/method (e.g., "LLM-as-a-Judge")
        2. Domain/context (e.g., "healthcare, clinical, medical")
        3. Any synonyms or related terms
        """
        search_terms = search_conditions.get("search_terms", "")

        # Check if we already have primary_topic and domain (from chat.py)
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
            f"Decide whether the paper is about {primary_topic}{primary_syn_str} "
            f"AND it is applied in {domain}{domain_syn_str} contexts. "
            f"Return True only if BOTH criteria are clearly indicated."
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
        examples.append(f"- '{primary_topic} for {domain} applications' → True")
        examples.append(f"- '{primary_topic} for code generation' (no {domain}) → False")
        examples.append(f"- '{domain} NER using BERT' (no {primary_topic}) → False")

        examples_str = "\n".join(examples)

        # Instruction
        instruction = (
            f"Use ONLY the provided title/abstract. Return exactly one word: True or False.\n"
            f"- True if the paper (1) concerns {primary_topic}{primary_syn_str} "
            f"AND (2) is applied in {domain}{domain_syn_str} contexts.\n"
            f"- If either part is missing or unclear, return False.\n"
            f"- Papers about {primary_topic} without {domain} context → False.\n"
            f"- Papers about {domain} without the {primary_topic} aspect → False.\n"
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

        # Save prompt to prompts folder
        prompts_dir = self.ensure_directory("prompts")
        output_file = prompts_dir / "extraction_prompt.json"
        save_json(str(output_file), extraction_prompt)
        self.log(f"Extraction prompt saved to: {output_file}")

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
