"""
Download Agent.
Runs the fast cascade PDF downloader from utils/fast_pdf_downloader.py.
"""

from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
from utils.human_interaction import print_header, print_summary
from utils.jsonl_handler import read_jsonl, save_json, write_jsonl


class DownloadAgent(BaseAgent):
    """
    Agent responsible for downloading papers from filtered results.

    Uses FastCascadePDFDownloader from utils/fast_pdf_downloader.py.
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

        from utils.fast_pdf_downloader import FastCascadePDFDownloader
        import config

        filtered_file = input_data.get("filtered_file")

        if not filtered_file:
            filtered_file = str(self.project_path / "filtered" / "filtered_papers.jsonl")

        if not Path(filtered_file).exists():
            self.log(f"Filtered file not found: {filtered_file}", "error")
            raise FileNotFoundError(f"Filtered file not found: {filtered_file}")

        # Create output directory
        output_dir = Path(input_data.get("download_folder") or self.project_path / "pdfs")
        output_dir.mkdir(parents=True, exist_ok=True)

        print(f"  Input file: {filtered_file}")
        print(f"  Output directory: {output_dir}")
        print()

        papers = read_jsonl(str(filtered_file))

        downloader = FastCascadePDFDownloader(
            email=str(input_data.get("email") or getattr(config, "EMAIL", "") or "research@example.com"),
            output_dir=output_dir,
            enable_browser_fallback=bool(input_data.get("enable_browser_fallback", True)),
        )

        try:
            download_result = downloader.download_batch(
                papers,
                progress_callback=input_data.get("progress_callback"),
                progress_file=input_data.get("progress_file"),
            )
        finally:
            close = getattr(downloader, "close", None)
            if close:
                close()

        stats = dict(download_result or {})
        stats["downloaded_at"] = datetime.now().isoformat()
        stats["input_file"] = filtered_file
        stats["output_dir"] = str(output_dir)
        stats["download_engine"] = "FastCascadePDFDownloader"

        report, papers = self._build_download_report(papers, output_dir, stats)
        write_jsonl(str(filtered_file), papers)
        save_json(str(output_dir / "download_report.json"), report)
        save_json(str(self.project_path / "download_stats.json"), report)

        # Show summary
        print_summary({
            "Downloaded": report["success"],
            "Failed": report["failed"],
            "Skipped": report.get("skipped", 0),
            "Total PDFs": report["pdf_count"],
            "Output folder": str(output_dir)
        }, title="\nDownload Complete")

        # Save state
        self.state = {
            "completed": True,
            "stats": report,
            "output_dir": str(output_dir)
        }
        self.save_state()

        return {
            "status": "download_done",
            "download_folder": str(output_dir),
            "pdf_count": report["pdf_count"],
            "success": report["success"],
            "failed": report["failed"],
            "stats": report
        }

    def _build_download_report(self, papers: List[Dict[str, Any]], output_dir: Path, stats: Dict[str, Any]) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        pdf_files = sorted(output_dir.glob("*.pdf"))
        failed_by_key = {self._paper_key(failed): failed for failed in stats.get("failed_papers") or []}
        downloaded = []
        failed_papers = []
        subscribed_papers = []
        unavailable_papers = []
        fallback_candidates = []

        for index, paper in enumerate(papers, start=1):
            row_pdf = self._pdf_for_paper(index, paper, pdf_files)
            if row_pdf:
                paper["pdf_downloaded"] = True
                paper["pdf_path"] = str(row_pdf)
                paper["retrieval_status"] = "downloaded"
                paper.pop("web_search_fallback_pending", None)
                paper.pop("pdf_failure_class", None)
                downloaded.append({"title": paper.get("title", ""), "path": str(row_pdf)})
                continue

            failure = failed_by_key.get(self._paper_key(paper), {})
            invalid_pdf = self._matching_pdf_for_paper(index, paper, pdf_files)
            failure_class = str(
                paper.get("pdf_failure_class")
                or failure.get("failure_class")
                or ("invalid_pdf_content" if invalid_pdf else "")
                or self._default_failure_class(paper)
            )
            paper["pdf_downloaded"] = False
            paper["pdf_failure_class"] = failure_class
            paper["retrieval_status"] = "subscribed_unavailable" if self._is_subscription_failure(failure_class) else "unavailable"
            paper["web_search_fallback_pending"] = True
            paper["web_search_fallback_eligible"] = True
            failed_entry = {
                "id": paper.get("id", ""),
                "title": paper.get("title", ""),
                "doi": paper.get("doi", ""),
                "url": paper.get("url", ""),
                "failure_class": failure_class,
                "retrieval_status": paper["retrieval_status"],
                "web_search_fallback_pending": True,
                "web_search_fallback_eligible": True,
            }
            failed_papers.append(failed_entry)
            fallback_candidates.append(failed_entry)
            if paper["retrieval_status"] == "subscribed_unavailable":
                subscribed_papers.append(failed_entry)
            else:
                unavailable_papers.append(failed_entry)

        success = len(downloaded)
        failed = len(failed_papers)
        report = {
            "downloaded_at": datetime.now().isoformat(),
            "success": success,
            "failed": failed,
            "skipped": int(stats.get("skipped") or 0),
            "attempted": int(stats.get("attempted") or success + failed),
            "pdf_count": len([pdf_file for pdf_file in pdf_files if self._is_valid_pdf(pdf_file)]),
            "downloaded": downloaded,
            "failed_papers": failed_papers,
            "subscribed_papers": subscribed_papers,
            "unavailable_papers": unavailable_papers,
            "web_search_fallback_candidates": fallback_candidates,
            "output_dir": str(output_dir),
            "download_engine": str(stats.get("download_engine") or "FastCascadePDFDownloader"),
        }
        return report, papers

    def _pdf_for_paper(self, row_number: int, paper: Dict[str, Any], pdf_files: List[Path]) -> Path | None:
        if paper.get("pdf_downloaded") and paper.get("pdf_path") and Path(str(paper["pdf_path"])).exists():
            pdf_path = Path(str(paper["pdf_path"]))
            return pdf_path if self._is_valid_pdf(pdf_path) else None
        matched = self._matching_pdf_for_paper(row_number, paper, pdf_files)
        return matched if matched and self._is_valid_pdf(matched) else None

    def _matching_pdf_for_paper(self, row_number: int, paper: Dict[str, Any], pdf_files: List[Path]) -> Path | None:
        row_prefix = f"row{row_number}_"
        for pdf_file in pdf_files:
            if pdf_file.name.startswith(row_prefix):
                return pdf_file
        return None

    def _is_valid_pdf(self, pdf_file: Path) -> bool:
        try:
            return pdf_file.read_bytes()[:1024].lstrip().startswith(b"%PDF-")
        except OSError:
            return False

    def _paper_key(self, paper: Dict[str, Any]) -> str:
        return str(paper.get("id") or paper.get("doi") or paper.get("title") or "").strip().lower()

    def _default_failure_class(self, paper: Dict[str, Any]) -> str:
        if not (paper.get("url") or paper.get("pdf_url") or paper.get("doi")):
            return "no_downloadable_pdf"
        return "download_failed"

    def _is_subscription_failure(self, failure_class: str) -> bool:
        normalized = failure_class.lower()
        return any(marker in normalized for marker in ("paywall", "subscrib", "subscription", "closed"))
