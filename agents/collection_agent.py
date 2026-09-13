"""
Collection Agent.
Wraps the existing AcademicSearcher to collect papers from multiple platforms.
Outputs results in JSONL format per platform.
Supports cascade PDF downloading.
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
import json

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
from utils.jsonl_handler import write_jsonl, save_json, JSONLWriter
from utils.human_interaction import print_header, print_summary, show_progress
from utils.pdf_downloader import download_papers_cascade


class CollectionAgent(BaseAgent):
    """
    Agent responsible for collecting papers from academic databases.

    Uses the existing AcademicSearcher from main.py to search multiple platforms
    and saves results in JSONL format.
    """

    def __init__(self, project_path: Path):
        """
        Initialize the collection agent.

        Args:
            project_path: Path to the project directory
        """
        super().__init__(project_path, "collection")

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Collect papers based on search conditions.

        Args:
            input_data: Search conditions from SearchConditionAgent

        Returns:
            Dictionary with collection results and statistics
        """
        print_header("Paper Collection")

        # Import here to avoid circular imports
        from main import AcademicSearcher

        # Initialize searcher
        searcher = AcademicSearcher()

        # Extract search parameters
        query = input_data.get("search_terms", "")
        platforms = input_data.get("platforms", ["pubmed", "arxiv", "openalex"])
        max_results = input_data.get("max_results_per_platform", 0)
        arxiv_categories = input_data.get("arxiv_categories")
        cs_venues = input_data.get("cs_venues")
        from reviewpilot_core.publication_dates import resolve_range
        date_range = resolve_range(input_data.get("date_range", {}))
        source_limits = input_data.get("source_limits") if isinstance(input_data.get("source_limits"), dict) else {}

        # Check for arXiv-specific query
        arxiv_query = input_data.get("arxiv_search_terms", query)

        self.log(f"Starting collection from {len(platforms)} platforms")
        self.log(f"Query: {query[:100]}...")

        # Create output directory
        output_dir = self.ensure_directory("collected")

        # Collect papers
        print(f"\n  Searching {len(platforms)} platforms...")
        print(f"  Query: {query[:60]}..." if len(query) > 60 else f"  Query: {query}")
        print()

        platform_errors = {}
        try:
            if source_limits:
                results = {}
                for platform in platforms:
                    platform_limit = self._source_limit(source_limits, platform, max_results)
                    platform_results = searcher.search(
                        query=query,
                        platforms=[platform],
                        max_results=platform_limit if platform_limit > 0 else None,
                        arxiv_query=arxiv_query if platform == "arxiv" else None,
                        arxiv_categories=arxiv_categories,
                        cs_venues=cs_venues,
                        use_proxy=False, date_range=date_range
                    )
                    platform_errors.update(getattr(searcher, "last_errors", {}) or {})
                    results.update(platform_results)
            else:
                results = searcher.search(
                    query=query,
                    platforms=platforms,
                    max_results=max_results if max_results > 0 else None,
                    arxiv_query=arxiv_query if "arxiv" in platforms else None,
                    arxiv_categories=arxiv_categories,
                    cs_venues=cs_venues,
                    use_proxy=False, date_range=date_range
                )
                platform_errors.update(getattr(searcher, "last_errors", {}) or {})
        except Exception as e:
            self.log(f"Error during search: {e}", "error")
            raise

        # Process and save results
        total_papers = 0
        platform_stats = {}

        for platform, papers in results.items():
            count = len(papers)
            platform_stats[platform] = count
            total_papers += count

            # Add timestamp to each paper
            for paper in papers:
                paper["collected_at"] = datetime.now().isoformat()

            # Save to JSONL
            output_file = output_dir / f"{platform}.jsonl"
            write_jsonl(str(output_file), papers)
            print(f"  {platform}: {count} papers -> {output_file.name}")

        # Save summary
        summary = {
            "collected_at": datetime.now().isoformat(),
            "query": query,
            "platforms": platforms,
            "max_results_per_platform": max_results,
            "date_range": date_range,
            "coverage": {"bounded_by_source_limits": True, "date_filter": {source: "source_native" if source in {"pubmed", "arxiv", "openalex"} else "local_only" for source in platforms}, "note": "Source metadata determines native date coverage; incomplete dates in returned records are conservatively reviewed."},
            "results": platform_stats,
            "platform_stats": platform_stats,
            "platform_errors": platform_errors,
            "total_papers": total_papers
        }
        save_json(str(output_dir / "summary.json"), summary)

        # Show summary
        print_summary({
            "Total papers collected": total_papers,
            **{f"  - {k}": v for k, v in platform_stats.items()},
            "Output folder": str(output_dir)
        }, title="\nCollection Complete")

        # Save state
        self.state = {
            "completed": True,
            "total_papers": total_papers,
            "platform_stats": platform_stats,
            "platform_errors": platform_errors,
            "output_dir": str(output_dir)
        }
        self.save_state()

        return {
            "collected_folder": str(output_dir),
            "total_papers": total_papers,
            "platform_stats": platform_stats,
            "platform_errors": platform_errors,
            "summary": summary
        }

    def _source_limit(self, source_limits: Dict[str, Any], platform: str, fallback: int) -> int:
        try:
            value = int(source_limits.get(platform, fallback))
        except (TypeError, ValueError):
            value = int(fallback or 0)
        return value if value > 0 else 0

    def collect_single_platform(self, platform: str, query: str, max_results: int = 0) -> List[Dict]:
        """
        Collect papers from a single platform.

        Args:
            platform: Platform name
            query: Search query
            max_results: Maximum results (0 for unlimited)

        Returns:
            List of paper dictionaries
        """
        from main import AcademicSearcher

        searcher = AcademicSearcher()
        results = searcher.search(
            query=query,
            platforms=[platform],
            max_results=max_results if max_results > 0 else None
        )

        return results.get(platform, [])

    def download_pdfs(self, papers: List[Dict], email: str = "research@example.com",
                      progress_callback=None) -> Dict[str, Any]:
        """
        Download PDFs for a list of papers using cascade approach.

        Cascade order:
        1. Direct link (if available)
        2. Unpaywall API (legal open access)
        3. Semantic Scholar API
        4. PubMed Central
        5. arXiv search by title

        Args:
            papers: List of paper metadata dicts
            email: Email for API access (required by some APIs)
            progress_callback: Optional callback(current, total, title)

        Returns:
            Dictionary with download statistics
        """
        print_header("PDF Download (Cascade)")

        # Create PDFs directory
        pdf_dir = self.ensure_directory("pdfs")

        self.log(f"Attempting to download {len(papers)} papers")
        print(f"\n  Downloading PDFs for {len(papers)} papers...")
        print(f"  Output: {pdf_dir}")
        print()

        # Use cascade downloader
        results = download_papers_cascade(
            papers=papers,
            output_dir=pdf_dir,
            email=email,
            progress_callback=progress_callback
        )

        # Save download report
        report = {
            "downloaded_at": datetime.now().isoformat(),
            "total_papers": results["total"],
            "successful": results["success"],
            "failed": results["failed"],
            "success_rate": f"{results['success']/results['total']*100:.1f}%" if results["total"] > 0 else "0%",
            "by_method": results["by_method"],
            "downloaded_files": results["downloaded"],
            "failed_papers": results["failed_papers"]
        }
        save_json(str(pdf_dir / "download_report.json"), report)

        # Show summary
        print_summary({
            "Total papers": results["total"],
            "Successfully downloaded": results["success"],
            "Failed": results["failed"],
            "Success rate": report["success_rate"],
            **{f"  via {k}": v for k, v in results["by_method"].items()},
            "Output folder": str(pdf_dir)
        }, title="\nDownload Complete")

        return {
            "pdf_folder": str(pdf_dir),
            "total": results["total"],
            "success": results["success"],
            "failed": results["failed"],
            "by_method": results["by_method"],
            "papers": papers  # Papers now have pdf_downloaded, pdf_path, pdf_method fields
        }

    def download_pdfs_for_included(self, email: str = "research@example.com") -> Optional[Dict[str, Any]]:
        """
        Download PDFs for papers that passed relevance screening.

        Looks for included_papers.jsonl in the filtered folder.

        Args:
            email: Email for API access

        Returns:
            Download statistics or None if no included papers found
        """
        from utils.jsonl_handler import read_jsonl

        # Find included papers
        filtered_dir = self.project_path / "filtered"
        included_file = filtered_dir / "included_papers.jsonl"

        if not included_file.exists():
            # Try legacy filename
            included_file = filtered_dir / "relevant_papers.jsonl"

        if not included_file.exists():
            self.log("No included papers file found", "warning")
            return None

        papers = read_jsonl(str(included_file))
        self.log(f"Found {len(papers)} included papers for download")

        def progress_cb(current, total, title):
            show_progress(current, total, f"Downloading: {title[:40]}...")

        return self.download_pdfs(papers, email=email, progress_callback=progress_cb)
