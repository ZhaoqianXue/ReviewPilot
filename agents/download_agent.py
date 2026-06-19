"""
Download Agent.
Wraps the PaperDownloader to download PDFs from filtered papers.
"""

from pathlib import Path
from typing import Dict, Any
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
from utils.human_interaction import print_header, print_summary, ask_confirm
from utils.jsonl_handler import save_json


class DownloadAgent(BaseAgent):
    """
    Agent responsible for downloading papers from filtered results.

    Uses the PaperDownloader from utils/downloader.py to handle actual downloads.
    """

    def __init__(self, project_path: Path):
        """
        Initialize the download agent.

        Args:
            project_path: Path to the project directory
        """
        super().__init__(project_path, "download")

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Download papers from filtered results.

        Args:
            input_data: Contains:
                - filtered_file: Path to filtered papers JSONL

        Returns:
            Dictionary with download results and statistics
        """
        print_header("Paper Download")

        # Import downloader
        from utils.downloader import PaperDownloader

        filtered_file = input_data.get("filtered_file")

        if not filtered_file:
            filtered_file = str(self.project_path / "filtered" / "filtered_papers.jsonl")

        if not Path(filtered_file).exists():
            self.log(f"Filtered file not found: {filtered_file}", "error")
            raise FileNotFoundError(f"Filtered file not found: {filtered_file}")

        # Create output directory
        output_dir = self.ensure_directory("papers")

        print(f"  Input file: {filtered_file}")
        print(f"  Output directory: {output_dir}")
        print()

        # Initialize downloader
        downloader = PaperDownloader(output_dir=str(output_dir))

        # Run download
        downloader.download_from_jsonl(filtered_file)

        # Get statistics
        stats = downloader.stats.copy()
        stats["downloaded_at"] = datetime.now().isoformat()
        stats["input_file"] = filtered_file
        stats["output_dir"] = str(output_dir)

        # Save statistics
        save_json(str(self.project_path / "download_stats.json"), stats)

        # Count actual files
        pdf_files = list(output_dir.glob("*.pdf"))
        stats["pdf_count"] = len(pdf_files)

        # Show summary
        print_summary({
            "Downloaded": stats["successful"],
            "Failed": stats["failed"],
            "Skipped": stats["skipped"],
            "Total PDFs": len(pdf_files),
            "Output folder": str(output_dir)
        }, title="\nDownload Complete")

        # Save state
        self.state = {
            "completed": True,
            "stats": stats,
            "output_dir": str(output_dir)
        }
        self.save_state()

        return {
            "download_folder": str(output_dir),
            "pdf_count": len(pdf_files),
            "stats": stats
        }
