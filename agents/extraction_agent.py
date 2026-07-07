"""
Extraction Agent.
Extracts information from downloaded PDFs using LLM prompts.
Outputs results in JSONL format with real-time writing.
"""

from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
import json
import re

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
from reviewpilot_core.model_policy import EXTRACTION_MODEL
from utils.jsonl_handler import read_jsonl, append_jsonl, save_json
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

    def __init__(self, project_path: Path, model: str = EXTRACTION_MODEL, llm_query=None, pdf_reader=None, web_search_query=None):
        """
        Initialize the extraction agent.

        Args:
            project_path: Path to the project directory
            model: LLM model to use for extraction
        """
        super().__init__(project_path, "extraction")
        self.model = model
        self.llm_query = llm_query
        self.pdf_reader = pdf_reader
        self.web_search_query = web_search_query
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

        active_pdf_reader = input_data.get("pdf_reader") or self.pdf_reader
        active_llm_query = input_data.get("llm_query") or self.llm_query
        active_web_search_query = input_data.get("web_search_query") or self.web_search_query

        if not PDF_SUPPORT and active_pdf_reader is None:
            raise ImportError("pypdf is required for PDF extraction. Install with: pip install pypdf")

        # Get paths
        pdf_folder = Path(input_data.get("download_folder", self.project_path / "pdfs"))
        filtered_file = input_data.get("filtered_file", self.project_path / "filtered" / "included_papers.jsonl")
        extraction_prompt = input_data.get("extraction_prompt") or self._load_extraction_prompt()

        # Create output directory
        output_dir = self.ensure_directory("extraction")

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

        pdf_files = sorted(pdf_folder.glob("*.pdf"))
        print(f"  Found {len(pdf_files)} PDFs to process\n")
        needs_web_search_client = any(
            paper.get("web_search_fallback_pending") and not paper.get("pdf_downloaded")
            for paper in papers
        ) and active_web_search_query is None
        needs_pdf_llm_client = active_llm_query is None and any(
            not (paper.get("web_search_fallback_pending") and not paper.get("pdf_downloaded"))
            for paper in papers
        )
        if needs_web_search_client or needs_pdf_llm_client:
            self._init_client()

        # Get prompts
        system_prompt = extraction_prompt.get("system_prompt", "You are an expert academic paper analyst.")
        user_template = extraction_prompt.get("user_prompt_template") or f"{extraction_prompt.get('extraction_prompt', 'Summarize this paper.')}\n\nPAPER CONTENT:\n{{paper_text}}"

        # Process PDFs
        output_file = output_dir / "extraction_results.jsonl"
        output_file.write_text("", encoding="utf-8")
        processed = 0
        errors = 0
        pending_web_search_fallback = 0
        web_search_fallback = 0
        total_cost = 0.0

        for i, paper_meta in enumerate(papers, start=1):
            show_progress(i, len(papers), prefix="  Extracting")

            if paper_meta.get("web_search_fallback_pending") and not paper_meta.get("pdf_downloaded"):
                try:
                    result, cost = self._extract_with_web_search_fallback(
                        paper=paper_meta,
                        row_number=i,
                        extraction_prompt=extraction_prompt,
                        web_search_query=active_web_search_query,
                    )
                    append_jsonl(str(output_file), result)
                    processed += 1
                    web_search_fallback += 1
                    total_cost += cost
                except Exception as e:
                    self.log(f"Web-search fallback failed for row {i}: {e}", "error")
                    append_jsonl(str(output_file), self._web_search_error_record(paper_meta, i, str(e)))
                    errors += 1
                    pending_web_search_fallback += 1
                continue

            pdf_file = self._pdf_for_paper(i, paper_meta, pdf_folder, pdf_files)
            if pdf_file is None:
                append_jsonl(str(output_file), self._error_record(paper_meta, i, "PDF file not found"))
                errors += 1
                continue

            try:
                # Read PDF
                pdf_text = active_pdf_reader(pdf_file) if active_pdf_reader else self._read_pdf(pdf_file)

                if not pdf_text:
                    self.log(f"Could not extract text from {pdf_file.name}", "warning")
                    errors += 1
                    append_jsonl(str(output_file), self._error_record(paper_meta, i, "Could not extract text from PDF", pdf_file))
                    continue

                # Extract information
                extracted, cost = self._extract_with_llm(
                    pdf_text, system_prompt, user_template, active_llm_query
                )
                total_cost += cost
                extracted_data = self._parse_extracted_data(extracted)

                # Build result record
                result = {
                    "paper_id": paper_meta.get("id", "unknown") if paper_meta else "unknown",
                    "source": paper_meta.get("source", "unknown") if paper_meta else "unknown",
                    "title": paper_meta.get("title", pdf_file.stem) if paper_meta else pdf_file.stem,
                    "pdf_file": pdf_file.name,
                    "row_number": i,
                    "extracted_at": datetime.now().isoformat(),
                    "extraction_model": self.model,
                    "extraction_cost_usd": cost,
                    "extracted_data": extracted_data,
                    "extraction_source": "pdf",
                    "extraction_status": "success",
                    **extracted_data,
                }

                # Write immediately
                append_jsonl(str(output_file), result)
                processed += 1

            except Exception as e:
                self.log(f"Error processing {pdf_file.name}: {e}", "error")
                errors += 1

                # Write error record
                error_result = {
                    "paper_id": paper_meta.get("id", "unknown") if paper_meta else "unknown",
                    "title": paper_meta.get("title", pdf_file.stem) if paper_meta else pdf_file.stem,
                    "pdf_file": pdf_file.name,
                    "row_number": i,
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
            "web_search_fallback": web_search_fallback,
            "pending_web_search_fallback": pending_web_search_fallback,
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
            "status": "extraction_done",
            "output_file": str(output_file),
            "processed": processed,
            "errors": errors,
            "web_search_fallback": web_search_fallback,
            "pending_web_search_fallback": pending_web_search_fallback,
            "total_cost": total_cost,
            "stats": stats
        }

    def _load_extraction_prompt(self) -> Dict[str, Any]:
        for path in [self.project_path / "prompts" / "extraction_prompt.json", self.project_path / "extraction" / "extraction_prompt.json"]:
            if path.exists():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
        return {}

    def _pdf_for_paper(self, row_number: int, paper: Dict[str, Any], pdf_folder: Path, pdf_files: List[Path]) -> Path | None:
        if paper.get("pdf_path") and Path(str(paper["pdf_path"])).exists():
            return Path(str(paper["pdf_path"]))
        row_prefix = f"row{row_number}_"
        for pdf_file in pdf_files:
            if pdf_file.name.startswith(row_prefix):
                return pdf_file
        return None

    def _extract_with_web_search_fallback(
        self,
        *,
        paper: Dict[str, Any],
        row_number: int,
        extraction_prompt: Dict[str, Any],
        web_search_query=None,
    ) -> tuple[Dict[str, Any], float]:
        if web_search_query is not None:
            response, usage = web_search_query(
                paper=paper,
                extraction_prompt=extraction_prompt,
                model=self.model,
            )
        else:
            response, usage = self._query_web_search_extraction(paper, extraction_prompt)

        extracted_data = self._parse_extracted_data(response)
        source_urls = self._normalize_source_urls(extracted_data.get("source_urls") or extracted_data.get("sources") or [])
        if not source_urls:
            source_urls = self._metadata_source_urls(paper)
        if not source_urls:
            raise ValueError("web_search fallback response missing source_urls")
        extracted_data["source_urls"] = source_urls
        extracted_data["confidence"] = str(extracted_data.get("confidence") or "low")
        cost = self._usage_cost(usage)
        return {
            "paper_id": paper.get("id", "unknown"),
            "source": paper.get("source", "unknown"),
            "title": paper.get("title", f"Paper {row_number}"),
            "doi": paper.get("doi", ""),
            "row_number": row_number,
            "extracted_at": datetime.now().isoformat(),
            "extraction_model": self.model,
            "extraction_cost_usd": cost,
            "extracted_data": extracted_data,
            "extraction_source": "web_search_fallback",
            "extraction_status": "success",
            "pdf_failure_class": paper.get("pdf_failure_class", ""),
            "retrieval_status": paper.get("retrieval_status", ""),
            "web_search_fallback_pending": False,
            **extracted_data,
        }, cost

    def _metadata_source_urls(self, paper: Dict[str, Any]) -> list[str]:
        urls: list[str] = []
        for value in (paper.get("url"), paper.get("landing_page_url"), paper.get("pdf_url")):
            text = str(value or "").strip()
            if text and text not in urls:
                urls.append(text)
        doi = str(paper.get("doi") or "").strip()
        if doi:
            doi_url = doi if doi.startswith(("http://", "https://")) else f"https://doi.org/{doi}"
            if doi_url not in urls:
                urls.append(doi_url)
        return urls

    def _query_web_search_extraction(self, paper: Dict[str, Any], extraction_prompt: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        if self.client is None:
            self._init_client()
        response = self.client.responses.create(
            model=self.model,
            tools=[
                {
                    "type": "web_search",
                    "filters": {
                        "allowed_domains": [
                            "pubmed.ncbi.nlm.nih.gov",
                            "pmc.ncbi.nlm.nih.gov",
                            "arxiv.org",
                            "openalex.org",
                            "semanticscholar.org",
                            "acm.org",
                            "ieee.org",
                            "springer.com",
                            "nature.com",
                            "sciencedirect.com",
                            "wiley.com",
                        ],
                    },
                }
            ],
            tool_choice="required",
            include=["web_search_call.action.sources"],
            input=[
                {
                    "role": "system",
                    "content": (
                        "Extract only information supported by cited web sources. "
                        "Return only valid JSON. Include source_urls and confidence. "
                        "If a schema field is unsupported, return an empty value and low confidence."
                    ),
                },
                {
                    "role": "user",
                    "content": self._web_search_fallback_prompt(paper, extraction_prompt),
                },
            ],
        )
        output_text = str(getattr(response, "output_text", "") or "").strip()
        if not output_text:
            raise ValueError("Responses API web_search returned no output_text")
        payload = self._parse_extracted_data(output_text)
        if "source_urls" not in payload:
            source_urls = self._source_urls_from_response(response)
            if source_urls:
                payload["source_urls"] = source_urls
        usage = self._usage_dict(getattr(response, "usage", None))
        return json.dumps(payload, ensure_ascii=False), usage

    def _web_search_fallback_prompt(self, paper: Dict[str, Any], extraction_prompt: Dict[str, Any]) -> str:
        schema = extraction_prompt.get("schema") or {"fields": extraction_prompt.get("fields") or []}
        return f"""Use web search to extract schema fields for this subscribed/paywalled paper.

Paper metadata:
{json.dumps({key: paper.get(key) for key in ("title", "authors", "year", "doi", "url", "abstract", "source")}, ensure_ascii=False, indent=2)}

Extraction schema:
{json.dumps(schema, ensure_ascii=False, indent=2)}

Return ONLY valid JSON with:
- every supported schema field by its field name
- source_urls: non-empty list of URLs that support the extracted values
- confidence: "high", "medium", or "low"

Do not claim full-text extraction. Use empty strings for unsupported fields."""

    def _normalize_source_urls(self, value: Any) -> list[str]:
        if isinstance(value, str):
            candidates = [value]
        elif isinstance(value, list):
            candidates = value
        else:
            candidates = []
        urls = []
        for item in candidates:
            text = str(item.get("url") if isinstance(item, dict) else item).strip()
            if text and text not in urls:
                urls.append(text)
        return urls

    def _source_urls_from_response(self, response: Any) -> list[str]:
        urls: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"url", "uri"} and isinstance(item, str) and item.startswith(("http://", "https://")):
                        if item not in urls:
                            urls.append(item)
                    else:
                        visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)
            elif hasattr(value, "model_dump"):
                visit(value.model_dump())
            elif hasattr(value, "__dict__"):
                visit(vars(value))

        visit(getattr(response, "output", None))
        return urls

    def _usage_dict(self, usage: Any) -> Dict[str, Any]:
        if usage is None:
            return {}
        if isinstance(usage, dict):
            return usage
        return {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }

    def _usage_cost(self, usage: Any) -> float:
        data = self._usage_dict(usage)
        input_tokens = int(data.get("input_tokens") or 0)
        output_tokens = int(data.get("output_tokens") or 0)
        total_tokens = int(data.get("total_tokens") or input_tokens + output_tokens)
        if input_tokens or output_tokens:
            return (input_tokens / 1_000_000) * 0.25 + (output_tokens / 1_000_000) * 2.0
        return total_tokens / 1_000_000 * 2.25

    def _web_search_error_record(self, paper: Dict[str, Any], row_number: int, message: str) -> Dict[str, Any]:
        return {
            "paper_id": paper.get("id", "unknown"),
            "source": paper.get("source", "unknown"),
            "title": paper.get("title", f"Paper {row_number}"),
            "row_number": row_number,
            "extracted_at": datetime.now().isoformat(),
            "extraction_source": "web_search_fallback",
            "extraction_status": "error",
            "error_message": message,
            "pdf_failure_class": paper.get("pdf_failure_class", ""),
            "retrieval_status": paper.get("retrieval_status", ""),
            "source_urls": [],
            "confidence": "low",
            "web_search_fallback_pending": True,
        }

    def _error_record(self, paper: Dict[str, Any], row_number: int, message: str, pdf_file: Path | None = None) -> Dict[str, Any]:
        return {
            "paper_id": paper.get("id", "unknown"),
            "source": paper.get("source", "unknown"),
            "title": paper.get("title", f"Paper {row_number}"),
            "pdf_file": pdf_file.name if pdf_file else "",
            "row_number": row_number,
            "extracted_at": datetime.now().isoformat(),
            "extraction_source": "pdf",
            "extraction_status": "error",
            "error_message": message,
        }

    def _parse_extracted_data(self, extracted: Any) -> Dict[str, Any]:
        if isinstance(extracted, dict):
            return extracted
        text = str(extracted or "").strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0]
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"extracted_text": str(extracted or "")}
        return parsed if isinstance(parsed, dict) else {"extracted_text": str(extracted or "")}

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

    def _extract_with_llm(self, text: str, system_prompt: str, user_template: str, llm_query=None) -> tuple:
        """
        Extract information from text using LLM.

        Returns:
            Tuple of (extracted_text, cost_usd)
        """
        text = self._sanitize_text_for_utf8(text)
        system_prompt = self._sanitize_text_for_utf8(system_prompt)
        user_template = self._sanitize_text_for_utf8(user_template)

        # Truncate text if too long
        max_chars = 400000  # ~128k tokens
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... [truncated]"

        user_prompt = self._render_user_prompt(user_template, text)

        if llm_query is not None:
            response, usage = llm_query(
                text_prompt=user_prompt,
                system_prompt=system_prompt,
                model=self.model,
                provider="openai",
            )
            total_tokens = 0
            if isinstance(usage, dict):
                total_tokens = int(usage.get("total_tokens") or usage.get("input_tokens") or 0)
            return response, total_tokens / 1_000_000 * 2.25

        try:
            request = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.3,
            }
            if "json" in f"{system_prompt}\n{user_prompt}".lower():
                request["response_format"] = {"type": "json_object"}
            if self.model.startswith("gpt-5"):
                request["max_completion_tokens"] = 4096
            else:
                request["max_tokens"] = 4096
            response = self.client.chat.completions.create(**request)

            extracted = response.choices[0].message.content

            # Estimate cost (rough approximation)
            input_tokens = response.usage.prompt_tokens if response.usage else 0
            output_tokens = response.usage.completion_tokens if response.usage else 0

            # Cost per 1M tokens for the active extraction model.
            cost = (input_tokens / 1_000_000) * 0.25 + (output_tokens / 1_000_000) * 2.0

            return extracted, cost

        except Exception as e:
            self.log(f"LLM error: {e}", "error")
            raise

    def _render_user_prompt(self, user_template: str, paper_text: str) -> str:
        if "{paper_text}" in user_template:
            return user_template.replace("{paper_text}", paper_text)
        return f"{user_template}\n\nPAPER CONTENT:\n{paper_text}"

    def _sanitize_text_for_utf8(self, value: Any) -> str:
        return str(value or "").encode("utf-8", "replace").decode("utf-8")
