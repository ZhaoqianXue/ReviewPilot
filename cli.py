#!/usr/bin/env python3
"""
Multi-Agent Academic Paper Search and Extraction System CLI.

Usage:
    python cli.py run                     # Run full interactive pipeline
    python cli.py run --resume filtering  # Resume from specific stage
    python cli.py run --config search.json # Load config file
    python cli.py status --project myproj # Check project status
"""

import argparse
import sys
from pathlib import Path


def cmd_run(args):
    """Run the full pipeline or resume from a stage."""
    from agents.coordinator import PipelineCoordinator

    coordinator = PipelineCoordinator(output_dir=args.output)

    try:
        results = coordinator.run_pipeline(
            resume_from=args.resume,
            config_file=args.config,
            model=args.model
        )
        return 0
    except KeyboardInterrupt:
        print("\nPipeline interrupted.")
        return 1
    except Exception as e:
        print(f"\nError: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


def cmd_status(args):
    """Show project status."""
    from utils.jsonl_handler import load_json

    output_dir = Path(args.output)
    if args.project:
        project_path = output_dir / args.project
    else:
        # Find most recent project
        projects = [d for d in output_dir.iterdir() if d.is_dir()]
        if not projects:
            print("No projects found.")
            return 1
        projects.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        project_path = projects[0]

    if not project_path.exists():
        print(f"Project not found: {project_path}")
        return 1

    print(f"\nProject: {project_path.name}")
    print(f"Location: {project_path}")
    print("-" * 50)

    # Check each component
    components = [
        ("Search conditions", "search_conditions.json"),
        ("Relevance prompt", "prompts/relevance_prompt.json"),
        ("Extraction prompt", "prompts/extraction_prompt.json"),
        ("Collected papers", "collected/summary.json"),
        ("Filtered papers", "filtered/filtered_papers.jsonl"),
        ("Download stats", "download_stats.json"),
        ("Extracted data", "extracted/extracted_data.jsonl"),
        ("Pipeline state", "logs/pipeline_state.json")
    ]

    for name, path in components:
        full_path = project_path / path
        if full_path.exists():
            print(f"  [+] {name}: {path}")

            # Show counts for JSONL files
            if path.endswith(".jsonl"):
                count = sum(1 for _ in open(full_path))
                print(f"      Records: {count}")

            # Show summary for JSON files
            elif path == "collected/summary.json":
                summary = load_json(str(full_path))
                print(f"      Total: {summary.get('total_papers', 'N/A')}")
        else:
            print(f"  [ ] {name}: not found")

    # Check PDF count
    pdf_dir = project_path / "papers"
    if pdf_dir.exists():
        pdf_count = len(list(pdf_dir.glob("*.pdf")))
        print(f"  [+] PDFs downloaded: {pdf_count}")

    return 0


def cmd_search(args):
    """Run search only with config file."""
    from agents.search_condition_agent import SearchConditionAgent
    from agents.collection_agent import CollectionAgent
    from utils.jsonl_handler import load_json

    # Load config
    config = load_json(args.config)

    # Run search condition agent
    search_agent = SearchConditionAgent(output_dir=args.output)
    conditions = search_agent.run(config)

    # Run collection agent
    project_path = Path(conditions["project_path"])
    collection_agent = CollectionAgent(project_path)
    results = collection_agent.run(conditions)

    print(f"\nCollection complete: {results['total_papers']} papers")
    return 0


def cmd_filter(args):
    """Run filtering only."""
    from agents.filtering_agent import FilteringAgent
    from utils.jsonl_handler import load_json

    project_path = Path(args.output) / args.project

    if not project_path.exists():
        print(f"Project not found: {project_path}")
        return 1

    # Load relevance prompt if exists
    prompt_file = project_path / "prompts" / "relevance_prompt.json"
    relevance_prompt = load_json(str(prompt_file)) if prompt_file.exists() else {}

    # Load search conditions for date range
    conditions_file = project_path / "search_conditions.json"
    conditions = load_json(str(conditions_file)) if conditions_file.exists() else {}

    filtering_agent = FilteringAgent(project_path, model=args.model)
    results = filtering_agent.run({
        "collected_folder": project_path / "collected",
        "relevance_prompt": relevance_prompt,
        "date_range": conditions.get("date_range", {})
    })

    print(f"\nFiltering complete: {results['filtered_count']} papers")
    return 0


def cmd_download(args):
    """Run download only."""
    from agents.download_agent import DownloadAgent

    project_path = Path(args.output) / args.project

    if not project_path.exists():
        print(f"Project not found: {project_path}")
        return 1

    download_agent = DownloadAgent(project_path)
    results = download_agent.run({
        "filtered_file": project_path / "filtered" / "filtered_papers.jsonl"
    })

    print(f"\nDownload complete: {results['pdf_count']} PDFs")
    return 0


def cmd_extract(args):
    """Run extraction only."""
    from agents.extraction_agent import ExtractionAgent
    from agents.prompt_agent import PromptAgent
    from utils.jsonl_handler import load_json

    project_path = Path(args.output) / args.project

    if not project_path.exists():
        print(f"Project not found: {project_path}")
        return 1

    # Check for extraction prompt
    prompt_file = project_path / "prompts" / "extraction_prompt.json"
    if prompt_file.exists():
        extraction_prompt = load_json(str(prompt_file))
    else:
        # Generate new extraction prompt
        conditions_file = project_path / "search_conditions.json"
        conditions = load_json(str(conditions_file)) if conditions_file.exists() else {}
        prompt_agent = PromptAgent(project_path)
        extraction_prompt = prompt_agent.generate_extraction_prompt(conditions)

    extraction_agent = ExtractionAgent(project_path, model=args.model)
    results = extraction_agent.run({
        "download_folder": project_path / "papers",
        "filtered_file": project_path / "filtered" / "filtered_papers.jsonl",
        "extraction_prompt": extraction_prompt
    })

    print(f"\nExtraction complete: {results['processed']} papers, ${results['total_cost']:.2f}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Agent Academic Paper Search and Extraction System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run full interactive pipeline
  python cli.py run

  # Resume from filtering stage
  python cli.py run --resume filtering

  # Load search conditions from config file
  python cli.py run --config my_search.json

  # Check project status
  python cli.py status --project my-project

  # Run individual stages
  python cli.py filter --project my-project
  python cli.py download --project my-project
  python cli.py extract --project my-project

Available stages for --resume:
  search_conditions, prompt_relevance, collection, filtering,
  prompt_extraction, download, extraction
        """
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed error messages"
    )
    parser.add_argument(
        "-o", "--output",
        default="output",
        help="Output directory for projects (default: output)"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run full pipeline")
    run_parser.add_argument(
        "--resume", "-r",
        help="Resume from stage (e.g., filtering, download)"
    )
    run_parser.add_argument(
        "--config", "-c",
        help="Load search conditions from JSON config file"
    )
    run_parser.add_argument(
        "--model", "-m",
        default="gpt-5-mini",
        help="LLM model for filtering and extraction (default: gpt-5-mini)"
    )
    run_parser.set_defaults(func=cmd_run)

    # Status command
    status_parser = subparsers.add_parser("status", help="Show project status")
    status_parser.add_argument(
        "--project", "-p",
        help="Project name (default: most recent)"
    )
    status_parser.set_defaults(func=cmd_status)

    # Search command
    search_parser = subparsers.add_parser("search", help="Run search with config")
    search_parser.add_argument(
        "--config", "-c",
        required=True,
        help="Search config JSON file"
    )
    search_parser.set_defaults(func=cmd_search)

    # Filter command
    filter_parser = subparsers.add_parser("filter", help="Run filtering only")
    filter_parser.add_argument(
        "--project", "-p",
        required=True,
        help="Project name"
    )
    filter_parser.add_argument(
        "--model", "-m",
        default="gpt-5-mini",
        help="LLM model for relevance checking"
    )
    filter_parser.set_defaults(func=cmd_filter)

    # Download command
    download_parser = subparsers.add_parser("download", help="Run download only")
    download_parser.add_argument(
        "--project", "-p",
        required=True,
        help="Project name"
    )
    download_parser.set_defaults(func=cmd_download)

    # Extract command
    extract_parser = subparsers.add_parser("extract", help="Run extraction only")
    extract_parser.add_argument(
        "--project", "-p",
        required=True,
        help="Project name"
    )
    extract_parser.add_argument(
        "--model", "-m",
        default="gpt-5-mini",
        help="LLM model for extraction"
    )
    extract_parser.set_defaults(func=cmd_extract)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
