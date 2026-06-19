"""
Extraction Agent.
Extracts information from downloaded PDFs using LLM prompts.
Outputs results in JSONL format with real-time writing.
"""

from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
import re

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
from utils.jsonl_handler import read_jsonl, append_jsonl, save_json, JSONLWriter
from utils.human_interaction import print_header, print_summary, show_progress

try:
    import pypdf
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
    print("Warning: pypdf not installed. PDF extraction disabled.")


class ExtractionAgent(BaseAgent):
    """
    Agent responsible for extracting information from downloaded PDFs.

    Uses LLM to process PDF content based on extraction prompts.
    """

    def __init__(self, project_path: Path, model: str = "gpt-5-mini"):
        """
        Initialize the extraction agent.

        Args:
            project_path: Path to the project directory
            model: LLM model to use for extraction
        """
        super().__init__(project_path, "extraction")
        self.model = model
        self.client = None

    def _init_client(self):
        """Initialize OpenAI client."""
        if self.client is None:
            from openai import OpenAI

            # Load API key from secrets
            secrets_file = self.project_path.parent / "secrets.txt"
            if not secrets_file.exists():
                secrets_file = Path("secrets.txt")

            api_key = None
            if secrets_file.exists():
                with open(secrets_file) as f:
                    for line in f:
                        parts = line.strip().split(',')
                        if len(parts) >= 2 and parts[0].strip() == 'openai_key':
                            api_key = parts[1].strip()
                            break

            if not api_key:
                raise ValueError("OpenAI API key not found in secrets.txt")

            self.client = OpenAI(api_key=api_key)

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract information from downloaded PDFs.

        Args:
            input_data: Contains:
                - download_folder: Path to downloaded PDFs
                - filtered_file: Path to filtered papers JSONL (for metadata)
                - extraction_prompt: Prompt configuration

        Returns:
            Dictionary with extraction results and statistics
        """
        print_header("Information Extraction")

        if not PDF_SUPPORT:
            raise ImportError("pypdf is required for PDF extraction. Install with: pip install pypdf")

        self._init_client()

        # Get paths
        pdf_folder = Path(input_data.get("download_folder", self.project_path / "papers"))
        filtered_file = input_data.get("filtered_file", self.project_path / "filtered" / "filtered_papers.jsonl")
        extraction_prompt = input_data.get("extraction_prompt", {})

        # Create output directory
        output_dir = self.ensure_directory("extracted")

        print(f"  PDF folder: {pdf_folder}")
        print(f"  Metadata file: {filtered_file}")
        print(f"  Model: {self.model}")
        print()

        # Load paper metadata
        papers = []
        if Path(filtered_file).exists():
            papers = read_jsonl(str(filtered_file))
            self.log(f"Loaded {len(papers)} paper records")
        else:
            self.log("No metadata file found, extracting from PDFs only", "warning")

        # Find all PDFs
        pdf_files = sorted(pdf_folder.glob("*.pdf"))
        if not pdf_files:
            self.log("No PDF files found", "error")
            return {"error": "No PDF files found"}

        print(f"  Found {len(pdf_files)} PDFs to process\n")

        # Get prompts
        system_prompt = extraction_prompt.get("system_prompt", "You are an expert academic paper analyst.")
        user_template = extraction_prompt.get("user_prompt_template", "Summarize this paper: {paper_text}")

        # Process PDFs
        output_file = output_dir / "extracted_data.jsonl"
        processed = 0
        errors = 0
        total_cost = 0.0

        for i, pdf_file in enumerate(pdf_files):
            show_progress(i + 1, len(pdf_files), prefix="  Extracting")

            # Extract row number from filename
            row_number = self._get_row_number(pdf_file.name)

            # Find matching paper metadata
            paper_meta = self._find_paper_metadata(papers, row_number)

            try:
                # Read PDF
                pdf_text = self._read_pdf(pdf_file)

                if not pdf_text:
                    self.log(f"Could not extract text from {pdf_file.name}", "warning")
                    errors += 1
                    continue

                # Extract information
                extracted, cost = self._extract_with_llm(
                    pdf_text, system_prompt, user_template
                )
                total_cost += cost

                # Build result record
                result = {
                    "paper_id": paper_meta.get("id", "unknown") if paper_meta else "unknown",
                    "source": paper_meta.get("source", "unknown") if paper_meta else "unknown",
                    "title": paper_meta.get("title", pdf_file.stem) if paper_meta else pdf_file.stem,
                    "pdf_file": pdf_file.name,
                    "row_number": row_number,
                    "extracted_at": datetime.now().isoformat(),
                    "extraction_model": self.model,
                    "extraction_cost_usd": cost,
                    "extracted_data": extracted,
                    "extraction_status": "success"
                }

                # Write immediately
                append_jsonl(str(output_file), result)
                processed += 1

            except Exception as e:
                self.log(f"Error processing {pdf_file.name}: {e}", "error")
                errors += 1

                # Write error record
                error_result = {
                    "pdf_file": pdf_file.name,
                    "row_number": row_number,
                    "extracted_at": datetime.now().isoformat(),
                    "extraction_status": "error",
                    "error_message": str(e)
                }
                append_jsonl(str(output_file), error_result)

        print()  # New line after progress

        # Save statistics
        stats = {
            "extracted_at": datetime.now().isoformat(),
            "total_pdfs": len(pdf_files),
            "processed": processed,
            "errors": errors,
            "total_cost_usd": total_cost,
            "model": self.model,
            "output_file": str(output_file)
        }
        save_json(str(output_dir / "extraction_stats.json"), stats)

        # Show summary
        print_summary({
            "Processed": processed,
            "Errors": errors,
            "Total cost": f"${total_cost:.2f}",
            "Output file": str(output_file)
        }, title="\nExtraction Complete")

        # Save state
        self.state = {
            "completed": True,
            "stats": stats
        }
        self.save_state()

        return {
            "output_file": str(output_file),
            "processed": processed,
            "errors": errors,
            "total_cost": total_cost,
            "stats": stats
        }

    def _get_row_number(self, filename: str) -> Optional[int]:
        """Extract row number from filename like 'row2_pubmed_2025_...'"""
        match = re.match(r'row(\d+)_', filename)
        if match:
            return int(match.group(1))
        return None

    def _find_paper_metadata(self, papers: List[Dict], row_number: Optional[int]) -> Optional[Dict]:
        """Find paper metadata by row number."""
        if row_number is None or not papers:
            return None

        # Row number is 1-indexed
        if 1 <= row_number <= len(papers):
            return papers[row_number - 1]

        return None

    def _read_pdf(self, pdf_path: Path) -> str:
        """Extract text from PDF file."""
        try:
            reader = pypdf.PdfReader(str(pdf_path))
            text = ""
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
            return text.strip()
        except Exception as e:
            self.log(f"Error reading PDF {pdf_path.name}: {e}", "warning")
            return ""

    def _extract_with_llm(self, text: str, system_prompt: str, user_template: str) -> tuple:
        """
        Extract information from text using LLM.

        Returns:
            Tuple of (extracted_text, cost_usd)
        """
        # Truncate text if too long
        max_chars = 400000  # ~128k tokens
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... [truncated]"

        # Format user prompt
        user_prompt = user_template.format(paper_text=text)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=4096
            )

            extracted = response.choices[0].message.content

            # Estimate cost (rough approximation)
            input_tokens = response.usage.prompt_tokens if response.usage else 0
            output_tokens = response.usage.completion_tokens if response.usage else 0

            # Cost per 1M tokens (gpt-5-mini pricing)
            cost = (input_tokens / 1_000_000) * 0.25 + (output_tokens / 1_000_000) * 2.0

            return extracted, cost

        except Exception as e:
            self.log(f"LLM error: {e}", "error")
            raise
