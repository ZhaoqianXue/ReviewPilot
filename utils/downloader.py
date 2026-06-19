"""
Download papers from a CSV file based on available URLs and DOIs.
Supports arXiv PDFs, DOI resolution, and direct PDF links.
"""

import csv
import os
import time
import requests
from pathlib import Path
from typing import List, Dict, Optional
import argparse


class PaperDownloader:
    def __init__(self, output_dir: str = "downloaded_papers"):
        """
        Initialize paper downloader.

        Args:
            output_dir: Directory to save downloaded papers
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)

        # Track download statistics
        self.stats = {
            "attempted": 0,
            "successful": 0,
            "failed": 0,
            "skipped": 0
        }

    def download_from_csv(self, csv_file: str):
        """
        Download papers from a CSV file based on available URLs.

        Args:
            csv_file: Path to CSV file containing paper metadata
        """
        csv_path = Path(csv_file)

        if not csv_path.exists():
            print(f"Error: CSV file not found: {csv_file}")
            return

        print(f"Reading papers from: {csv_file}")
        papers = self._read_csv(csv_path)

        # Filter papers with valid download URLs
        papers_with_urls = [p for p in papers if self._get_download_url(p)]

        print(f"Found {len(papers)} papers in CSV")
        print(f"Papers with downloadable URLs: {len(papers_with_urls)}\n")

        for i, paper in enumerate(papers_with_urls, 1):
            print(f"[{i}/{len(papers_with_urls)}]", end=" ")
            # Find the original row number in the full papers list
            row_number = papers.index(paper) + 1  # +1 because CSV rows start at 1 (header is 0)
            self._download_paper(paper, row_number)
            time.sleep(1)  # Rate limiting

        self._print_stats()

    def _read_csv(self, file_path: Path) -> List[Dict]:
        """Read papers from CSV file."""
        papers = []
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                papers.append(row)
        return papers

    def _download_paper(self, paper: Dict, row_number: int):
        """Download a single paper."""
        self.stats["attempted"] += 1

        # Get paper metadata from CSV columns
        paper_id = paper.get("id", "unknown")
        title = paper.get("title", "untitled")[:80]  # Truncate long titles
        source = paper.get("source", "unknown")
        year = paper.get("year", "")

        # Sanitize filename
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()
        safe_title = safe_title.replace(' ', '_')

        # Determine download URL
        download_url = self._get_download_url(paper)

        if not download_url:
            print(f"⊘ Skipped: {safe_title} (no download URL)")
            self.stats["skipped"] += 1
            return

        # Create filename with row number, source, year, and ID
        filename = f"row{row_number}_{source}_{year}_{safe_title}_{paper_id}.pdf"
        # Clean up the filename to avoid issues
        filename = filename.replace("/", "_").replace("\\", "_")
        output_path = self.output_dir / filename

        # Skip if already downloaded
        if output_path.exists():
            print(f"✓ Already exists: {safe_title}")
            self.stats["skipped"] += 1
            return

        # Download the paper
        try:
            print(f"↓ Downloading: {safe_title}...", end=" ")
            response = requests.get(download_url, timeout=30, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; AcademicResearchBot/1.0)'
            })
            response.raise_for_status()

            # Check if we got a PDF
            content_type = response.headers.get('Content-Type', '')
            if 'pdf' not in content_type.lower() and len(response.content) < 1000:
                print(f"✗ Not a PDF or access denied")
                self.stats["failed"] += 1
                return

            # Save the file
            with open(output_path, 'wb') as f:
                f.write(response.content)

            print(f"✓ ({len(response.content) / 1024:.1f} KB)")
            self.stats["successful"] += 1

        except requests.exceptions.RequestException as e:
            print(f"✗ {str(e)[:50]}")
            self.stats["failed"] += 1

    def _get_download_url(self, paper: Dict) -> Optional[str]:
        """Determine the download URL for a paper."""
        source = paper.get("source", "")

        # arXiv: construct PDF URL from ID
        if source == "arxiv" or "arxiv" in paper.get("url", "").lower():
            arxiv_id = paper.get("id", "")
            if arxiv_id:
                # arXiv PDF URL format
                return f"https://arxiv.org/pdf/{arxiv_id}.pdf"

        # Try pdf_url column if it exists
        if paper.get("pdf_url"):
            return paper["pdf_url"]

        # Try direct URL from url column (for PubMed, OpenAlex, DBLP, etc.)
        url = paper.get("url", "")
        if url and url.strip():
            return url.strip()

        # Try DOI resolution as last resort
        doi = paper.get("doi", "")
        if doi:
            # Clean DOI
            doi = doi.strip()
            if doi.startswith("http"):
                return doi
            else:
                # Try publisher URL (may require institutional access)
                return f"https://doi.org/{doi}"

        return None

    def download_from_jsonl(self, jsonl_file: str):
        """
        Download papers from a JSONL file based on available URLs.

        Args:
            jsonl_file: Path to JSONL file containing paper metadata
        """
        import json

        jsonl_path = Path(jsonl_file)

        if not jsonl_path.exists():
            print(f"Error: JSONL file not found: {jsonl_file}")
            return

        print(f"Reading papers from: {jsonl_file}")

        # Read JSONL file
        papers = []
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        papers.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        # Filter papers with valid download URLs
        papers_with_urls = [p for p in papers if self._get_download_url(p)]

        print(f"Found {len(papers)} papers in JSONL")
        print(f"Papers with downloadable URLs: {len(papers_with_urls)}\n")

        for i, paper in enumerate(papers_with_urls, 1):
            print(f"[{i}/{len(papers_with_urls)}]", end=" ")
            # Find the original row number in the full papers list
            row_number = papers.index(paper) + 1
            self._download_paper(paper, row_number)
            time.sleep(1)  # Rate limiting

        self._print_stats()

    def _print_stats(self):
        """Print download statistics."""
        print("\n" + "=" * 60)
        print("Download Statistics:")
        print(f"  Attempted:  {self.stats['attempted']}")
        print(f"  Successful: {self.stats['successful']}")
        print(f"  Failed:     {self.stats['failed']}")
        print(f"  Skipped:    {self.stats['skipped']}")
        success_rate = (self.stats['successful'] / self.stats['attempted'] * 100) if self.stats['attempted'] > 0 else 0
        print(f"  Success Rate: {success_rate:.1f}%")
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Download papers from a CSV file based on available URLs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download all papers with valid URLs from a CSV file
  python download_papers.py llm_as_judge_healthcare_final.csv

  # Specify output directory
  python download_papers.py llm_as_judge_healthcare_final.csv --output my_papers

Note:
  - CSV file should have columns: id, title, source, year, doi, url, pdf_url
  - Only papers with valid download URLs will be downloaded
  - arXiv papers have the most reliable PDF access
  - PubMed, Scopus, OpenAlex papers may require institutional access
  - Downloads respect rate limits with 1-second delays between requests
  - Files are named: source_year_title_id.pdf
        """
    )

    parser.add_argument(
        "csv_file",
        help="Path to CSV file (e.g., llm_as_judge_healthcare_final.csv)"
    )
    parser.add_argument(
        "--output", "-o",
        default="downloaded_papers",
        help="Output directory for downloaded papers (default: downloaded_papers)"
    )

    args = parser.parse_args()

    downloader = PaperDownloader(output_dir=args.output)
    downloader.download_from_csv(args.csv_file)


if __name__ == "__main__":
    main()
