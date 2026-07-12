"""
Search Condition Agent.
Intelligently generates search conditions based on user's research description.

The agent:
1. Asks user to describe their research topic in natural language
2. Generates Boolean search queries
3. Iteratively refines based on user feedback
4. Suggests appropriate platforms based on domain
5. Allows user to approve at each step
"""

from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, date
import json
import re

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
from reviewpilot_core.model_policy import DEFAULT_MAX_RESULTS_PER_PLATFORM, SEARCH_CONDITION_MODEL
from utils.llm import query_llm
from utils.human_interaction import (
    ask_text, ask_confirm, ask_multiselect, ask_date, ask_number,
    print_header, print_subheader, print_summary, print_box, print_text
)
from utils.jsonl_handler import save_json


# Platform configurations
PLATFORMS = {
    "pubmed": {
        "name": "PubMed",
        "description": "Biomedical and life science literature",
        "domains": ["healthcare", "medical", "clinical", "biomedical", "health"],
        "requires_key": False
    },
    "openalex": {
        "name": "OpenAlex",
        "description": "Open scholarly metadata (all fields)",
        "domains": ["*"],
        "requires_key": False
    },
    "arxiv": {
        "name": "arXiv",
        "description": "Preprints in CS, physics, math, biology",
        "domains": ["machine learning", "deep learning", "AI", "NLP", "LLM"],
        "requires_key": False
    },
    "dblp": {
        "name": "DBLP",
        "description": "Computer science conferences and journals",
        "domains": ["machine learning", "NLP", "AI", "LLM", "computer science"],
        "requires_key": False
    },
    "scopus": {
        "name": "Scopus",
        "description": "Elsevier's citation database",
        "domains": ["*"],
        "requires_key": True
    },
    "wos": {
        "name": "Web of Science",
        "description": "Clarivate citation database",
        "domains": ["*"],
        "requires_key": True
    },
    "google_scholar": {
        "name": "Google Scholar",
        "description": "Web scraping (rate limited)",
        "domains": ["*"],
        "requires_key": False
    }
}


class SearchConditionAgent(BaseAgent):
    """
    Agent that intelligently generates search conditions with iterative refinement.
    """

    def __init__(self, output_dir: str = "output", llm_query=None):
        self.output_dir = Path(output_dir)
        self.agent_name = "search_condition"
        self.llm_query = llm_query or query_llm
        self.state = {}
        self.logger = None

    def run(self, input_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Interactively collect and generate search conditions.
        If input_data has all required fields, skip interactive prompts.
        """
        defaults = input_data or {}

        # Check if an external caller supplied a complete config.
        required_fields = ['project_name', 'search_terms', 'platforms']
        if all(defaults.get(f) for f in required_fields) and (defaults.get('primary_topic') or defaults.get('derive_search_terms')):
            return self._use_provided_config(defaults)

        # Interactive mode
        print_header("Research Topic Configuration")

        # 1. Project name
        project_name = ask_text(
            "Project name (for organizing output)",
            default=defaults.get("project_name"),
            required=True
        )

        safe_name = self._sanitize_name(project_name)
        project_path = self.output_dir / safe_name

        if project_path.exists():
            if not ask_confirm(f"Project '{safe_name}' exists. Continue?", default=False):
                return self.run(input_data)
        else:
            project_path.mkdir(parents=True, exist_ok=True)

        super().__init__(project_path, self.agent_name)

        # 2. Research description (natural language)
        print("\n  Describe your research topic in plain language.")
        print("  Example: 'papers about using LLMs as judges for evaluating")
        print("           medical text generation quality'")
        print()

        research_description = ask_text(
            "Describe your research topic",
            required=True
        )

        # 3. Extract concepts and generate query with iterative refinement
        concepts = self._extract_concepts(research_description)

        print("\n  Detected concepts:")
        if concepts["primary_topics"]:
            print(f"    Topics: {', '.join(concepts['primary_topics'])}")
        if concepts["domains"]:
            print(f"    Domains: {', '.join(concepts['domains'])}")
        if concepts["methods"]:
            print(f"    Methods: {', '.join(concepts['methods'])}")

        # Generate initial query
        search_terms = self._generate_search_query(concepts)

        # Interactive refinement loop for search query
        search_terms = self._refine_search_query(search_terms, concepts, research_description)

        # 4. Platforms with refinement
        suggested_platforms = self._suggest_platforms(concepts)
        platforms = self._refine_platforms(suggested_platforms)

        # 5. Date range with refinement
        suggested_dates = self._suggest_date_range(concepts)
        start_date, end_date = self._refine_date_range(suggested_dates)

        # 6. Max results
        print("\n  How many results per platform?")
        print("  (Use 0 for unlimited, or limit to manage processing time)")
        max_results = ask_number("Max results per platform", default=DEFAULT_MAX_RESULTS_PER_PLATFORM, min_val=0)

        # 7. arXiv-specific query (if arxiv selected)
        arxiv_query = None
        if "arxiv" in platforms:
            arxiv_query = self._generate_arxiv_query(concepts)
            print_subheader("arXiv-Specific Query")
            print_text(arxiv_query)
            if ask_confirm("Modify arXiv query?", default=False):
                arxiv_query = ask_text("arXiv query", default=arxiv_query)

        # Build and save search conditions
        search_conditions = {
            "project_name": project_name,
            "project_path": str(project_path),
            "created_at": datetime.now().isoformat(),
            "research_description": research_description,
            "extracted_concepts": concepts,
            "search_terms": search_terms,
            "arxiv_search_terms": arxiv_query,
            "platforms": platforms,
            "date_range": {
                "start_date": start_date,
                "end_date": end_date
            },
            "max_results_per_platform": max_results
        }

        output_file = project_path / "search_conditions.json"
        save_json(str(output_file), search_conditions)

        print_summary({
            "Project": project_name,
            "Query": search_terms[:80] + "..." if len(search_terms) > 80 else search_terms,
            "Platforms": ", ".join(platforms),
            "Date range": f"{start_date} to {end_date}",
            "Max results": max_results if max_results > 0 else "unlimited"
        }, title="Search Configuration Summary")

        self.state = {"completed": True, "conditions": search_conditions}
        self.save_state()

        return search_conditions

    def _use_provided_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Use pre-configured settings without interactive prompts.
        Called when an external workflow provides complete configuration.
        """
        project_name = config['project_name']
        project_path = Path(config.get("project_path") or (self.output_dir / self._sanitize_name(project_name)))
        project_path.mkdir(parents=True, exist_ok=True)

        # Initialize base agent
        super().__init__(project_path, self.agent_name)

        description = str(config.get('description') or config.get('research_description') or config.get('search_terms') or project_name)
        derive_search_terms = bool(config.get('derive_search_terms')) or not str(config.get('search_terms') or '').strip()
        if derive_search_terms:
            return self._use_llm_derived_config(config, project_name, project_path, description)

        concepts = config.get('extracted_concepts') if isinstance(config.get('extracted_concepts'), dict) else self._extract_concepts(description)
        search_terms = str(config.get('search_terms') or '')
        platforms = config.get('platforms') or self._suggest_platforms(concepts)

        # Build date range
        date_range = config.get('date_range', {})
        if isinstance(date_range, dict):
            start_date = date_range.get('start')
            end_date = date_range.get('end')
        else:
            start_date = config.get('date_range_start')
            end_date = config.get('date_range_end')
        if derive_search_terms and not start_date and not end_date:
            start_year, _end_year = self._suggest_date_range(concepts)
            start_date = f"{start_year}-01-01"
            end_date = ""

        max_results = config.get('max_results', config.get('max_results_per_platform', DEFAULT_MAX_RESULTS_PER_PLATFORM)) or DEFAULT_MAX_RESULTS_PER_PLATFORM
        source_limits = config.get('source_limits') if isinstance(config.get('source_limits'), dict) else {}
        if not source_limits:
            source_limits = {platform: max_results for platform in platforms}

        # Build search conditions
        search_conditions = {
            **config,
            "project_name": project_name,
            "project_path": str(project_path),
            "description": description,
            "research_description": description,
            "extracted_concepts": concepts,
            "search_terms": search_terms,
            "search_queries": [{"name": "main", "query": search_terms}],
            "platforms": platforms,
            "date_range": {"start": start_date, "end": end_date},
            "max_results": max_results,
            "max_results_per_platform": max_results,
            "source_limits": source_limits,
            "primary_topic": config.get('primary_topic') or (concepts.get("primary_topics") or [""])[0],
            "domain": config.get('domain') or ", ".join(concepts.get("domains") or []),
            "extraction_fields": config.get('extraction_fields', 'datasets used, methods, key findings, evaluation metrics'),
            "arxiv_query": self._generate_arxiv_query(concepts) if "arxiv" in platforms else None
        }

        # Save conditions
        conditions_file = project_path / "search_conditions.json"
        save_json(str(conditions_file), search_conditions)
        self.log(f"Search conditions saved: {conditions_file}")

        # Show summary
        print_summary({
            "Project": project_name,
            "Query": search_conditions['search_terms'][:60] + "...",
            "Platforms": ", ".join(search_conditions['platforms']),
            "Max results": search_conditions.get('max_results', DEFAULT_MAX_RESULTS_PER_PLATFORM) or "unlimited"
        }, title="Using Provided Configuration")

        self.state = {"completed": True, "conditions": search_conditions}
        self.save_state()

        return search_conditions

    def _use_llm_derived_config(
        self,
        config: Dict[str, Any],
        project_name: str,
        project_path: Path,
        description: str,
    ) -> Dict[str, Any]:
        """
        Generate chat-derived search setup through a real LLM call.
        No deterministic search-query fallback is allowed in this path.
        """
        model = str(config.get("model") or SEARCH_CONDITION_MODEL)
        response_text, usage = self.llm_query(
            text_prompt=self._llm_search_setup_prompt(config, project_name, description),
            system_prompt=(
                "You are ReviewPilot's SearchConditionAgent. Generate rigorous systematic-review "
                "search setup JSON from the user's natural-language chat request. Return only valid JSON."
            ),
            model=model,
            provider="openai",
        )
        llm_payload = self._parse_llm_search_setup(response_text)
        search_conditions = self._normalize_llm_search_setup(
            config=config,
            project_name=project_name,
            project_path=project_path,
            description=description,
            model=model,
            llm_payload=llm_payload,
            usage=usage or {},
        )

        conditions_file = project_path / "search_conditions.json"
        save_json(str(conditions_file), search_conditions)
        self.log(f"Search conditions saved: {conditions_file}")

        self.state = {"completed": True, "conditions": search_conditions}
        self.save_state()
        return search_conditions

    def _llm_search_setup_prompt(self, config: Dict[str, Any], project_name: str, description: str) -> str:
        example_max_results = config.get('max_results') or config.get('max_results_per_platform') or DEFAULT_MAX_RESULTS_PER_PLATFORM
        example_platforms = config.get('platforms') or ["pubmed", "arxiv", "openalex"]
        example_source_limits = config.get('source_limits') or {
            "pubmed": DEFAULT_MAX_RESULTS_PER_PLATFORM,
            "arxiv": DEFAULT_MAX_RESULTS_PER_PLATFORM,
            "openalex": DEFAULT_MAX_RESULTS_PER_PLATFORM,
        }
        return f"""Generate Search Setup for ReviewPilot from this user chat request.

Project name:
{project_name}

User research request:
{description}

Current canvas constraints supplied by the user interface:
- Candidate platforms: {config.get('platforms') or []}
- Source limits: {config.get('source_limits') or {}}
- Max results: {config.get('max_results') or config.get('max_results_per_platform') or ''}
- Date range: {config.get('date_range') or {}}

Return ONLY valid JSON with this exact top-level shape:
{{
  "reply": "brief assistant message to the user",
  "project_name": "short human-readable review title",
  "research_description": "the user's research question in clear prose",
  "search_terms": "Boolean query string",
  "search_queries": [{{"name": "main", "query": "Boolean query string"}}],
  "platforms": {json.dumps(example_platforms)},
  "date_range": {{"start": "YYYY-MM-DD or blank", "end": "YYYY-MM-DD or blank"}},
  "max_results": {example_max_results},
  "source_limits": {json.dumps(example_source_limits)},
  "primary_topic": "main concept",
  "domain": "research domain",
  "extracted_concepts": {{
    "primary_topics": ["..."],
    "domains": ["..."],
    "methods": ["..."]
  }},
  "keywords": ["keyword or phrase"]
}}

Rules:
- Do not include explanatory text outside JSON.
- Return the candidate platforms exactly as supplied, in the same order; do not add, drop, or reorder them.
- Preserve the user's intended domain and scope.
- Preserve the current canvas source limits and max results exactly unless the user has explicitly changed them in the canvas.
- Build a real Boolean query suitable for academic database search."""

    def _parse_llm_search_setup(self, response_text: str) -> Dict[str, Any]:
        try:
            payload = json.loads(str(response_text or "").strip())
        except json.JSONDecodeError as exc:
            raise ValueError("SearchConditionAgent LLM did not return valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("SearchConditionAgent LLM response must be a JSON object")
        return payload

    def _normalize_llm_search_setup(
        self,
        *,
        config: Dict[str, Any],
        project_name: str,
        project_path: Path,
        description: str,
        model: str,
        llm_payload: Dict[str, Any],
        usage: Dict[str, Any],
    ) -> Dict[str, Any]:
        required = [
            "reply",
            "project_name",
            "research_description",
            "search_terms",
            "search_queries",
            "platforms",
            "date_range",
            "max_results",
            "source_limits",
            "primary_topic",
            "domain",
            "extracted_concepts",
        ]
        missing = [key for key in required if not llm_payload.get(key)]
        if missing:
            raise ValueError(f"SearchConditionAgent LLM response missing required fields: {', '.join(missing)}")

        platforms = llm_payload["platforms"]
        if not isinstance(platforms, list) or not all(str(item).strip() for item in platforms):
            raise ValueError("SearchConditionAgent LLM response must include a non-empty platforms list")
        source_limits = llm_payload["source_limits"]
        if not isinstance(source_limits, dict):
            raise ValueError("SearchConditionAgent LLM response source_limits must be an object")
        date_range = llm_payload["date_range"]
        if not isinstance(date_range, dict):
            raise ValueError("SearchConditionAgent LLM response date_range must be an object")
        concepts = llm_payload["extracted_concepts"]
        if not isinstance(concepts, dict):
            raise ValueError("SearchConditionAgent LLM response extracted_concepts must be an object")
        search_queries = llm_payload["search_queries"]
        if not isinstance(search_queries, list) or not search_queries:
            raise ValueError("SearchConditionAgent LLM response search_queries must be a non-empty list")

        configured_platforms = config.get("platforms")
        platform_source = configured_platforms if isinstance(configured_platforms, list) and configured_platforms else platforms
        selected_platforms = [str(platform).strip().lower() for platform in platform_source]
        preserved_source_limits = self._preserved_source_limits(config, llm_payload, selected_platforms)
        preserved_max_results = max(preserved_source_limits.values()) if preserved_source_limits else DEFAULT_MAX_RESULTS_PER_PLATFORM

        return {
            **config,
            "project_name": str(llm_payload["project_name"]),
            "project_path": str(project_path),
            "description": description,
            "research_description": str(llm_payload["research_description"]),
            "extracted_concepts": concepts,
            "search_terms": str(llm_payload["search_terms"]),
            "search_queries": search_queries,
            "platforms": selected_platforms,
            "date_range": {
                "start": str(date_range.get("start") or ""),
                "end": str(date_range.get("end") or ""),
            },
            "max_results": preserved_max_results,
            "max_results_per_platform": preserved_max_results,
            "source_limits": preserved_source_limits,
            "primary_topic": str(llm_payload["primary_topic"]),
            "domain": str(llm_payload["domain"]),
            "keywords": llm_payload.get("keywords") or [],
            "lead_agent_reply": str(llm_payload["reply"]),
            "llm_usage": usage,
            "model": model,
            "generated_by": "llm",
            "arxiv_query": llm_payload.get("arxiv_query"),
        }

    def _preserved_source_limits(self, config: Dict[str, Any], llm_payload: Dict[str, Any], platforms: List[str]) -> Dict[str, int]:
        configured_limits = config.get("source_limits") if isinstance(config.get("source_limits"), dict) else {}
        llm_limits = llm_payload.get("source_limits") if isinstance(llm_payload.get("source_limits"), dict) else {}
        default_limit = self._positive_int(
            config.get("max_results") or config.get("max_results_per_platform"),
            DEFAULT_MAX_RESULTS_PER_PLATFORM,
        )
        limits = {}
        for platform in platforms:
            if platform in configured_limits:
                limits[platform] = self._positive_int(configured_limits.get(platform), default_limit)
            else:
                limits[platform] = self._positive_int(llm_limits.get(platform), default_limit)
        return limits

    @staticmethod
    def _positive_int(value: Any, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    def _refine_search_query(self, initial_query: str, concepts: Dict, description: str) -> str:
        """
        Iteratively refine the search query based on user feedback.
        """
        query = initial_query

        while True:
            print_subheader("Generated Search Query")
            print()
            print_text(query)
            print()

            if ask_confirm("Is this search query correct?", default=True):
                return query

            # Get feedback
            print("\n  What's wrong with the query? I'll help fix it.")
            print("  Examples:")
            print("    - 'missing LLM-as-a-judge'")
            print("    - 'should include neural network'")
            print("    - 'remove biomedical'")
            print("    - 'let me type my own query'")
            print()

            feedback = ask_text("Your feedback", required=True)

            # Check if user wants to type their own
            if "own" in feedback.lower() or "type" in feedback.lower() or "manual" in feedback.lower():
                query = ask_text("Enter your search query", default=query, required=True)
                continue

            # Process feedback and update query
            query = self._apply_feedback(query, feedback, concepts, description)

    def _apply_feedback(self, query: str, feedback: str, concepts: Dict, description: str) -> str:
        """
        Apply user feedback to improve the query.
        """
        feedback_lower = feedback.lower()

        # Check for "missing X" or "add X" or "should include X"
        missing_patterns = [
            r"missing\s+(.+)",
            r"add\s+(.+)",
            r"should\s+(?:have|include)\s+(.+)",
            r"need\s+(.+)",
            r"include\s+(.+)"
        ]

        for pattern in missing_patterns:
            match = re.search(pattern, feedback_lower)
            if match:
                missing_term = match.group(1).strip()
                # Add the missing term to the query
                return self._add_term_to_query(query, missing_term)

        # Check for "remove X"
        remove_patterns = [
            r"remove\s+(.+)",
            r"delete\s+(.+)",
            r"don't\s+(?:need|want)\s+(.+)"
        ]

        for pattern in remove_patterns:
            match = re.search(pattern, feedback_lower)
            if match:
                remove_term = match.group(1).strip()
                return self._remove_term_from_query(query, remove_term)

        # If feedback contains specific terms, try to incorporate them
        # Re-extract concepts including the feedback
        combined_description = f"{description}. Also: {feedback}"
        new_concepts = self._extract_concepts(combined_description)

        # Merge concepts
        for key in concepts:
            if isinstance(concepts[key], list):
                combined = list(set(concepts[key] + new_concepts.get(key, [])))
                concepts[key] = combined

        # Regenerate query with merged concepts
        return self._generate_search_query(concepts)

    def _add_term_to_query(self, query: str, term: str) -> str:
        """Add a term to the query."""
        # Generate appropriate Boolean expression for the term
        term_lower = term.lower().strip("'\"")

        if "llm" in term_lower and "judge" in term_lower:
            new_part = "('LLM-as-a-Judge' OR 'LLM as judge' OR 'agent-as-a-judge' OR 'GPT as judge' OR 'LLM evaluation' OR 'LLM-based assessment')"
        elif "llm" in term_lower:
            new_part = "('LLM' OR 'large language model' OR 'GPT' OR 'language model')"
        else:
            new_part = f"('{term}')"

        if query:
            return f"{new_part} AND {query}"
        else:
            return new_part

    def _remove_term_from_query(self, query: str, term: str) -> str:
        """Remove a term from the query."""
        # Simple removal - remove the term and clean up
        term_lower = term.lower().strip("'\"")

        # Try to remove the term
        query_modified = re.sub(rf"'{term_lower}'(\s+OR\s+)?", "", query, flags=re.IGNORECASE)
        query_modified = re.sub(rf"(\s+OR\s+)?'{term_lower}'", "", query_modified, flags=re.IGNORECASE)

        # Clean up empty parentheses and dangling operators
        query_modified = re.sub(r"\(\s*\)", "", query_modified)
        query_modified = re.sub(r"\s+AND\s+AND\s+", " AND ", query_modified)
        query_modified = re.sub(r"^\s*AND\s+", "", query_modified)
        query_modified = re.sub(r"\s+AND\s*$", "", query_modified)

        return query_modified.strip()

    def _refine_platforms(self, suggested: List[str]) -> List[str]:
        """Refine platform selection with user."""
        print_subheader("Suggested Platforms")
        for p in suggested:
            info = PLATFORMS.get(p, {})
            print(f"  - {info.get('name', p)}: {info.get('description', '')}")

        if ask_confirm("\nAre these platforms correct?", default=True):
            return suggested

        # Let user select
        platform_options = [f"{PLATFORMS[p]['name']} - {PLATFORMS[p]['description']}"
                         for p in PLATFORMS.keys()]
        default_indices = [list(PLATFORMS.keys()).index(p) for p in suggested if p in PLATFORMS]
        selected = ask_multiselect("Select platforms", platform_options, defaults=default_indices)
        return [list(PLATFORMS.keys())[i] for i in selected]

    def _refine_date_range(self, suggested: Tuple[int, int]) -> Tuple[str, str]:
        """Refine date range with user."""
        start_year, end_year = suggested
        current_date = datetime.now().strftime("%Y-%m-%d")

        print_subheader("Date Range")
        print(f"  Suggested: {start_year} to {end_year}")
        print("  (LLM/AI topics are recent; broader topics may need wider range)")

        if ask_confirm("Is this date range correct?", default=True):
            return f"{start_year}-01-01", current_date

        start_date = ask_date("Start date (YYYY-MM-DD)", default=f"{start_year}-01-01")
        end_date = ask_date("End date (YYYY-MM-DD)", default="today")

        return start_date, end_date

    def _extract_concepts(self, description: str) -> Dict[str, Any]:
        """
        Extract key concepts from natural language description.
        """
        desc_lower = description.lower()

        concepts = {
            "primary_topics": [],
            "domains": [],
            "methods": [],
            "keywords": []
        }

        # Detect LLM-as-a-Judge patterns (improved)
        llm_judge_patterns = [
            r"llm[s]?\s+(?:as|used\s+as|are\s+used\s+as|for)\s+(?:a\s+)?(?:judge|judges|evaluator|evaluators|assessment)",
            r"(?:use|using)\s+llm[s]?\s+(?:as|for)\s+(?:a\s+)?(?:judge|judges|evaluator)",
            r"llm[s]?\s+(?:judge|judges|evaluation|eval)",
            r"llm[-\s]as[-\s](?:a[-\s])?judge",
            r"agent[-\s]as[-\s](?:a[-\s])?judge",
            r"(?:gpt|claude|gemini|llama)[-\s]as[-\s](?:a[-\s])?judge",
            r"(?:gpt|claude|gemini|llama)\s+(?:as|for)\s+(?:a\s+)?(?:judge|evaluator)",
            r"large\s+language\s+model[s]?\s+(?:as|for)\s+(?:a\s+)?(?:judge|evaluator)",
        ]

        for pattern in llm_judge_patterns:
            if re.search(pattern, desc_lower):
                if "LLM-as-a-Judge" not in concepts["primary_topics"]:
                    concepts["primary_topics"].append("LLM-as-a-Judge")
                break

        # Detect other LLM topics
        if not concepts["primary_topics"]:
            other_llm_patterns = [
                (r"llm[s]?\b|large\s+language\s+model", "LLM"),
                (r"\bgpt\b|\bclaude\b|\bgemini\b|\bllama\b", "LLM"),
                (r"language\s+model", "language model"),
            ]
            for pattern, topic in other_llm_patterns:
                if re.search(pattern, desc_lower):
                    if topic not in concepts["primary_topics"]:
                        concepts["primary_topics"].append(topic)

        # Other topics
        topic_patterns = [
            (r"machine\s+learning", "machine learning"),
            (r"deep\s+learning", "deep learning"),
            (r"neural\s+network", "neural networks"),
            (r"transformer[s]?", "transformers"),
            (r"natural\s+language\s+processing|nlp", "NLP"),
            (r"text\s+(?:generation|mining|classification)", "NLP"),
        ]

        for pattern, topic in topic_patterns:
            if re.search(pattern, desc_lower):
                if topic not in concepts["primary_topics"]:
                    concepts["primary_topics"].append(topic)

        # Detect domains
        domain_patterns = [
            (r"biomedicine|biomedical|bio-medical", "biomedical"),
            (r"healthcare|health\s*care", "healthcare"),
            (r"clinical|clinic", "clinical"),
            (r"medical|medicine", "medical"),
            (r"health(?!\s*care)", "health"),
            (r"ehr|electronic\s+health\s+record|electronic\s+medical\s+record", "EHR"),
            (r"radiology|imaging", "radiology"),
            (r"pathology", "pathology"),
            (r"drug|pharma", "pharmaceutical"),
            (r"finance|financial|banking", "finance"),
            (r"legal|law", "legal"),
            (r"education", "education"),
        ]

        for pattern, domain in domain_patterns:
            if re.search(pattern, desc_lower):
                if domain not in concepts["domains"]:
                    concepts["domains"].append(domain)

        # Detect methods
        method_patterns = [
            (r"evaluat|assess|judg|scor|rat", "evaluation"),
            (r"generat|creat|produc", "generation"),
            (r"classif|categor", "classification"),
            (r"extract|retriev", "extraction"),
            (r"summar", "summarization"),
            (r"question\s+answering|qa|q&a", "QA"),
        ]

        for pattern, method in method_patterns:
            if re.search(pattern, desc_lower):
                if method not in concepts["methods"]:
                    concepts["methods"].append(method)

        return concepts

    def _generate_search_query(self, concepts: Dict[str, Any]) -> str:
        """
        Generate Boolean search query from extracted concepts.
        """
        parts = []

        # Primary topics
        if concepts["primary_topics"]:
            topic_parts = []
            for topic in concepts["primary_topics"]:
                if topic == "LLM-as-a-Judge":
                    topic_parts.append(
                        "'LLM-as-a-Judge' OR 'LLM as judge' OR 'agent-as-a-judge' OR "
                        "'GPT as judge' OR 'LLM evaluation' OR 'LLM-based assessment' OR "
                        "'LLM evaluator' OR 'GPT evaluator'"
                    )
                elif topic == "LLM":
                    topic_parts.append("'LLM' OR 'large language model' OR 'GPT' OR 'language model'")
                else:
                    topic_parts.append(f"'{topic}'")

            if topic_parts:
                parts.append(f"({' OR '.join(topic_parts)})")

        # Domains
        if concepts["domains"]:
            domains = set(concepts["domains"])
            # Expand healthcare-related terms
            if any(d in domains for d in ["healthcare", "medical", "clinical", "health"]):
                domains.update(["healthcare", "clinical", "medical", "health", "biomedical"])
            domain_str = " OR ".join([f"'{d}'" for d in sorted(domains)])
            parts.append(f"({domain_str})")

        # Combine with AND
        if parts:
            return " AND ".join(parts)
        elif concepts["keywords"]:
            return " AND ".join([f"'{k}'" for k in concepts["keywords"][:3]])
        else:
            return ""

    def _generate_arxiv_query(self, concepts: Dict[str, Any]) -> str:
        """Generate arXiv-optimized query (simpler syntax)."""
        terms = []

        if concepts["primary_topics"]:
            for topic in concepts["primary_topics"][:2]:
                simple = topic.lower().replace("-", " ").replace("'", "")
                terms.append(simple)

        if concepts["domains"]:
            terms.extend(concepts["domains"][:2])

        if terms:
            return " AND ".join(terms)
        return ""

    def _suggest_platforms(self, concepts: Dict[str, Any]) -> List[str]:
        """Suggest platforms based on detected domains."""
        suggested = set(["openalex"])  # Always include

        domains = concepts.get("domains", [])
        topics = concepts.get("primary_topics", [])

        # Healthcare → PubMed
        if any(d in ["healthcare", "clinical", "medical", "biomedical", "health", "EHR"]
               for d in domains):
            suggested.add("pubmed")

        # AI/ML/LLM → arXiv, DBLP
        if any(t in ["LLM", "LLM-as-a-Judge", "machine learning", "deep learning", "NLP", "transformers", "language model"]
               for t in topics):
            suggested.add("arxiv")
            suggested.add("dblp")

        if len(suggested) <= 1:
            suggested.update(["pubmed", "arxiv", "dblp"])

        return list(suggested)

    def _suggest_date_range(self, concepts: Dict[str, Any]) -> Tuple[int, int]:
        """Suggest date range based on topic recency."""
        topics = concepts.get("primary_topics", [])
        current_year = datetime.now().year

        # LLM topics are very recent (2022+)
        if any("LLM" in str(t) or "GPT" in str(t) or "judge" in str(t).lower() for t in topics):
            return (2022, current_year)

        # Deep learning (2018+)
        if any(t in ["deep learning", "neural networks", "transformers"] for t in topics):
            return (2018, current_year)

        # General ML (2015+)
        if "machine learning" in topics:
            return (2015, current_year)

        return (current_year - 5, current_year)

    def _sanitize_name(self, name: str) -> str:
        """Sanitize project name for folder."""
        safe = name.lower()
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in safe)
        while "--" in safe:
            safe = safe.replace("--", "-")
        return safe.strip("-") or "unnamed-project"
