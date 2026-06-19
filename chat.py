#!/usr/bin/env python3
"""
Data Scholar - AI-Assisted Paper Search

Step-by-step configuration:
1. Describe research -> AI generates search query
2. Pick platforms from list
3. Set date range
4. Set max results
5. Configure relevance/extraction
6. Run!

Usage: python3 chat.py
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from utils.llm import query_llm
from agents.coordinator import PipelineCoordinator

# Available platforms
PLATFORMS = {
    "1": ("pubmed", "PubMed - Biomedical literature"),
    "2": ("arxiv", "arXiv - CS, physics, math preprints"),
    "3": ("openalex", "OpenAlex - Open scholarly database"),
    "4": ("scopus", "Scopus - Elsevier (requires API key)"),
    "5": ("wos", "Web of Science (requires API key)"),
    "6": ("google_scholar", "Google Scholar (may be rate-limited)"),
    "7": ("dblp", "DBLP - CS Conferences & Journals")
}


def extract_json(response: str) -> dict:
    """Extract JSON from LLM response."""
    text = response.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return json.loads(text.strip())


def generate_search_query(description: str) -> dict:
    """Generate search query and metadata from research description."""
    prompt = f"""Generate academic paper search configuration for: "{description}"

Return ONLY valid JSON:
{{
  "project_name": "lowercase-slug-name",
  "search_terms": "Boolean query with AND/OR for academic databases",
  "primary_topic": "main technique or method being studied",
  "domain": "application area or field"
}}"""

    try:
        response, _ = query_llm(
            text_prompt=prompt,
            system_prompt="Return only valid JSON. No markdown code blocks.",
            model="gpt-5-mini"
        )
        return extract_json(response)
    except Exception as e:
        print(f"\n  Error: {e}")
        return None


def refine_query(current: dict, feedback: str, description: str) -> dict:
    """Refine search query based on feedback."""
    prompt = f"""Update this search query based on user feedback.

Original research: "{description}"
Current config: {json.dumps(current, indent=2)}
User feedback: "{feedback}"

Return updated JSON with same structure."""

    try:
        response, _ = query_llm(
            text_prompt=prompt,
            system_prompt="Return only valid JSON.",
            model="gpt-5-mini"
        )
        return extract_json(response)
    except:
        return current


def step_research_description():
    """Step 1: Get research description."""
    print("\n" + "="*70)
    print("  Step 1: Describe Your Research")
    print("="*70)
    print("\nDescribe what you're researching in a sentence or two.")
    print("Example: 'Survey of using LLM for rare disease diagnosis'\n")

    while True:
        description = input("What are you researching? ").strip()
        if description.lower() in ['quit', 'exit', 'q']:
            return None
        if len(description) >= 10:
            return description
        print("  Please provide more details.\n")


def step_search_query(description: str) -> dict:
    """Step 2: Generate and refine search query."""
    print("\n" + "="*70)
    print("  Step 2: Search Query")
    print("="*70)

    print("\nGenerating search query...")
    config = generate_search_query(description)
    if not config:
        return None

    while True:
        print(f"\n  Project: {config.get('project_name')}")
        print(f"  Query:   {config.get('search_terms')}")
        print(f"  Topic:   {config.get('primary_topic')}")
        print(f"  Domain:  {config.get('domain')}")

        feedback = input("\nRefine query? (feedback or Enter to continue): ").strip()
        if not feedback or feedback.lower() in ['ok', 'yes', 'y', 'good']:
            return config

        print("\n  Updating...")
        config = refine_query(config, feedback, description)


def step_platforms() -> list:
    """Step 3: Select platforms."""
    print("\n" + "="*70)
    print("  Step 3: Select Platforms")
    print("="*70)
    print("\nAvailable platforms:")

    for key, (name, desc) in PLATFORMS.items():
        print(f"  {key}. {desc}")

    print("\nEnter numbers separated by commas (e.g., 1,2,3)")
    print("Default: 1,2,3 (PubMed, arXiv, OpenAlex)")

    while True:
        choice = input("\nPlatforms [1,2,3]: ").strip()
        if not choice:
            choice = "1,2,3"

        # Parse selection
        selected = []
        try:
            for num in choice.replace(" ", "").split(","):
                if num in PLATFORMS:
                    selected.append(PLATFORMS[num][0])
        except:
            pass

        if selected:
            print(f"\n  Selected: {', '.join(selected)}")
            return selected
        else:
            print("  Invalid selection. Enter numbers like: 1,2,3")


def step_date_range() -> dict:
    """Step 4: Set date range."""
    print("\n" + "="*70)
    print("  Step 4: Date Range")
    print("="*70)
    print("\nEnter date range for papers.")
    print("Format: YYYY-MM-DD or YYYY")
    print("Leave blank for no limit.\n")

    start = input("Start date [2020-01-01]: ").strip() or "2020-01-01"
    end = input("End date [present]: ").strip() or None

    print(f"\n  Range: {start} to {end or 'present'}")
    return {"start": start, "end": end}


def step_max_results() -> int:
    """Step 5: Set max results."""
    print("\n" + "="*70)
    print("  Step 5: Max Results")
    print("="*70)
    print("\nMax papers to retrieve per platform.")
    print("Higher = more comprehensive, but slower.\n")

    while True:
        choice = input("Max results per platform [100]: ").strip()
        if not choice:
            return 100
        try:
            num = int(choice)
            if num > 0:
                return num
        except:
            pass
        print("  Enter a positive number.")


def step_relevance(config: dict) -> dict:
    """Step 6: Configure relevance filtering."""
    print("\n" + "="*70)
    print("  Step 6: Relevance Filtering")
    print("="*70)
    print("\nPapers will be filtered to match BOTH:")
    print(f"  Topic:  {config.get('primary_topic')}")
    print(f"  Domain: {config.get('domain')}")

    feedback = input("\nAdjust? (Enter to continue, or type changes): ").strip()
    if feedback and feedback.lower() not in ['no', 'n', 'ok']:
        new_topic = input("  New topic (Enter to keep): ").strip()
        new_domain = input("  New domain (Enter to keep): ").strip()
        if new_topic:
            config['primary_topic'] = new_topic
        if new_domain:
            config['domain'] = new_domain
        print(f"\n  Updated: {config['primary_topic']} + {config['domain']}")

    return config


def generate_extraction_fields(description: str, topic: str, domain: str) -> list:
    """Generate suggested extraction fields based on research topic."""
    prompt = f"""For a research survey on: "{description}"
Topic: {topic}
Domain: {domain}

Suggest 5-7 specific fields to extract from each paper. Return ONLY a JSON array of field names.
Example: ["datasets used", "model architecture", "evaluation metrics", "key findings"]

Focus on fields specific to this research area."""

    try:
        response, _ = query_llm(
            text_prompt=prompt,
            system_prompt="Return only a JSON array of strings. No explanation.",
            model="gpt-5-mini"
        )
        # Parse JSON array
        text = response.strip()
        if text.startswith('['):
            import json
            return json.loads(text)
    except:
        pass

    # Fallback
    return ["methods", "datasets", "key findings", "evaluation metrics"]


def step_extraction(description: str, topic: str, domain: str) -> str:
    """Step 7: Configure extraction fields."""
    print("\n" + "="*70)
    print("  Step 7: Information Extraction")
    print("="*70)

    print("\nGenerating suggested fields based on your research...")
    suggested_fields = generate_extraction_fields(description, topic, domain)

    print("\nSuggested fields to extract:")
    for i, field in enumerate(suggested_fields, 1):
        print(f"  {i}. {field}")

    print("\nOptions:")
    print("  - Enter to use these fields")
    print("  - Type additional fields to add")
    print("  - Type 'replace' to specify your own fields")

    response = input("\nCustomize fields: ").strip()

    if not response or response.lower() in ['ok', 'yes', 'y', 'good']:
        print("\n  Using suggested fields.")
        return ", ".join(suggested_fields)

    if response.lower() == 'replace':
        custom = input("  Enter all fields to extract: ").strip()
        if custom:
            fields = [f.strip() for f in custom.split(',') if f.strip()]
        else:
            fields = suggested_fields
    else:
        # Add to suggested
        new_fields = [f.strip() for f in response.split(',') if f.strip()]
        fields = suggested_fields + new_fields

    print("\n  Will extract:")
    for field in fields:
        print(f"    - {field}")

    return ", ".join(fields)


def show_summary(config: dict):
    """Show final configuration summary."""
    print("\n" + "="*70)
    print("  Configuration Summary")
    print("="*70)
    print(f"\n  Project:     {config['project_name']}")
    print(f"  Query:       {config['search_terms'][:50]}...")
    print(f"  Topic:       {config['primary_topic']}")
    print(f"  Domain:      {config['domain']}")
    print(f"  Platforms:   {', '.join(config['platforms'])}")
    print(f"  Date range:  {config['date_range']['start']} to {config['date_range']['end'] or 'present'}")
    print(f"  Max results: {config['max_results']} per platform")
    print(f"  Extract:     {config['extraction_fields'][:40]}...")
    print("="*70)


def main():
    """Main entry point."""
    print("\n" + "="*70)
    print("  Data Scholar - AI-Assisted Paper Search")
    print("="*70)

    # Step 1: Research description
    description = step_research_description()
    if not description:
        return 0

    # Step 2: Search query
    config = step_search_query(description)
    if not config:
        print("Failed to generate query. Please try again.")
        return 1

    # Step 3: Platforms
    config['platforms'] = step_platforms()

    # Step 4: Date range
    config['date_range'] = step_date_range()

    # Step 5: Max results
    config['max_results'] = step_max_results()

    # Step 6: Relevance
    config = step_relevance(config)

    # Step 7: Extraction
    config['extraction_fields'] = step_extraction(
        description,
        config['primary_topic'],
        config['domain']
    )

    # Show summary and confirm
    show_summary(config)
    confirm = input("\nStart search? [Y/n]: ").strip().lower()
    if confirm in ['n', 'no', 'q', 'quit']:
        print("Cancelled.")
        return 0

    # Save config
    config_file = Path("output") / f"{config['project_name']}_config.json"
    config_file.parent.mkdir(exist_ok=True)
    with open(config_file, 'w') as f:
        json.dump(config, indent=2, fp=f)
    print(f"\nSaved: {config_file}")

    # Run pipeline
    print("\nStarting pipeline...\n")

    try:
        coordinator = PipelineCoordinator(output_dir="output")

        pipeline_config = {
            "project_name": config["project_name"],
            "search_terms": config["search_terms"],
            "platforms": config["platforms"],
            "date_range": config["date_range"],
            "max_results": config["max_results"],
            "primary_topic": config["primary_topic"],
            "domain": config["domain"],
            "extraction_fields": config["extraction_fields"]
        }

        results = coordinator.run_pipeline(
            config_data=pipeline_config,
            model="gpt-5-mini",
            auto_approve=True
        )

        print("\n" + "="*70)
        print("  Complete!")
        print("="*70)
        print(f"\nResults: output/{config['project_name']}/")
        print("\nFolders:")
        print("  collected/  - Raw papers from all platforms")
        print("  filtered/   - Relevant papers after filtering")
        print("  papers/     - Downloaded PDFs")
        print("  extracted/  - Extracted information")
        return 0

    except KeyboardInterrupt:
        print(f"\n\nInterrupted! Resume with:")
        print(f"python3 cli.py run --resume collection --project {config['project_name']}")
        return 1

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
