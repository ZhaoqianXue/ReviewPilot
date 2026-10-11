"""
Extraction Agent.
Extracts information from downloaded PDFs using LLM prompts.
Outputs results in JSONL format with real-time writing.
"""

from reviewpilot_core import evidence_support, fulltext_eligibility, run_ledger, web_evidence
from reviewpilot_core.evidence_support import (
    ABSTRACT, FIELD_EVIDENCE_INSTRUCTION, FULL_TEXT, NOT_AVAILABLE, WEB, WEB_FIELD_EVIDENCE_INSTRUCTION, verify_field_evidence,
)
from reviewpilot_core.field_values import coerce_value, validate_value
from reviewpilot_core.no_full_text import lacks_full_text
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
import json
import re

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
from reviewpilot_core.extraction_schema import build_extraction_prompts
from reviewpilot_core.atomic_files import atomic_output_path
from reviewpilot_core.model_policy import EXTRACTION_MODEL, WEB_SEARCH_CALL_USD, accepts_custom_temperature, supports_web_search
from utils.jsonl_handler import read_jsonl, append_jsonl, save_json
from utils.human_interaction import print_header, print_summary, show_progress

try:
    import pypdf
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
    print("Warning: pypdf not installed. PDF extraction disabled.")

# Rules for papers without full text live in these prompts, not in the extraction Skill, so the
# Skill text that full-text extraction uses stays as validated.
ABSTRACT_ONLY_NOTE = (
    "The supplied evidence is the paper's title and abstract; the full text is not available. Apply the same schema, "
    "category definitions and coding rules as for full text. Populate a field only when the abstract states its value or "
    "the value necessarily follows from what the abstract describes, and quote the abstract sentence that establishes it. "
    "Leave every other field empty: silence in an abstract says nothing about what the full text reports, and a field is "
    "left empty rather than inferred from the study topic, the venue or similar studies."
)
WEB_EVIDENCE_RULES = (
    "The full text of this paper is not available and the abstract left the requested fields open. Use web search to find "
    "them only on pages about this paper itself: its publisher landing page, a trial registry entry for the study, its PMC "
    "or Europe PMC record, a preprint of the same work, or its own supplementary material. Before using a page, confirm that "
    "it presents this paper, by its DOI or title, as its own subject; works that cite or review the paper, news stories, blogs, "
    "social media and unauthorised copies describe it second-hand and are left out. Report each value at the strength the page "
    "states, and leave a field empty rather than inferring it from the study topic, the venue or similar studies."
)


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


class ExtractionAgent(BaseAgent):
    """
    Agent responsible for extracting information from downloaded PDFs.

    Uses LLM to process PDF content based on extraction prompts.
    """

    EXTRACTION_WORKERS = 8

    def __init__(self, project_path: Path, model: str = EXTRACTION_MODEL, llm_query=None, pdf_reader=None, web_search_query=None,
                 page_fetcher=None, skill_augment=None):
        """
        Initialize the extraction agent.

        Args:
            project_path: Path to the project directory
            model: LLM model to use for extraction
            web_search_query: replaces the provider web-search call for papers without full text
            page_fetcher: replaces fetching a cited web page for its deterministic checks
            skill_augment: adds the extraction Skill to the web-search system prompt
        """
        super().__init__(project_path, "extraction")
        self.model = model
        self.llm_query = llm_query
        self.pdf_reader = pdf_reader
        self.web_search_query = web_search_query
        self.page_fetcher = page_fetcher
        self.skill_augment = skill_augment
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
        # The Lead Agent decides whether the web step runs; without its decision the model's capability does.
        web_search = input_data.get("web_search")
        if web_search is None:
            web_search = active_web_search_query is not None or supports_web_search(self.model)

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
        needs_llm_client = active_llm_query is None and any(
            not lacks_full_text(paper) or str(paper.get("abstract") or "").strip() for paper in papers
        )
        if needs_llm_client:
            self._init_client()

        # Get prompts
        system_prompt = extraction_prompt.get("system_prompt")
        user_template = extraction_prompt.get("user_prompt_template")
        schema_fields = (extraction_prompt.get("schema") or {}).get("fields") if isinstance(extraction_prompt.get("schema"), dict) else None
        if schema_fields and (not isinstance(system_prompt, str) or not system_prompt.strip() or not isinstance(user_template, str) or not user_template.strip()):
            config_path = self.project_path / "search_conditions.json"
            try:
                config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
            except (OSError, json.JSONDecodeError):
                config = {}
            system_prompt, _stage_prompt, user_template = build_extraction_prompts(
                config, extraction_prompt["schema"], (extraction_prompt.get("coding_rules") or {}).get("text", ""))
            extraction_prompt = {**extraction_prompt, "system_prompt": system_prompt, "user_prompt_template": user_template}
        if not isinstance(system_prompt, str) or not system_prompt.strip() or not isinstance(user_template, str) or not user_template.strip():
            raise ValueError("Extraction requires an explicit extraction prompt")

        # Process PDFs
        output_file = output_dir / "extraction_results.jsonl"
        processed = 0
        errors = 0
        pending_web_search_fallback = 0
        web_search_fallback = 0
        tier_counts = {tier: 0 for tier in evidence_support.EVIDENCE_TIERS}
        web_search_failed = 0
        total_cost = 0.0

        from concurrent.futures import ThreadPoolExecutor
        import threading

        progress_lock = threading.Lock()
        done = [0]
        paper_prompt = {**extraction_prompt, "system_prompt": system_prompt, "user_prompt_template": user_template}
        # A retry of failed papers keeps every successful row from the current results as it is.
        kept = self._reusable_rows(output_file, papers) if input_data.get("retry_failed_only") else {}

        def extract(item):
            row_number, paper_meta = item
            if self._paper_key(paper_meta) in kept:
                with progress_lock:
                    done[0] += 1
                return kept[self._paper_key(paper_meta)]
            result = self.extract_one(
                paper=paper_meta,
                row_number=row_number,
                extraction_prompt=paper_prompt,
                pdf_folder=pdf_folder,
                pdf_files=pdf_files,
                llm_query=active_llm_query,
                pdf_reader=active_pdf_reader,
                web_search_query=active_web_search_query,
                web_search=web_search,
            )
            with progress_lock:
                done[0] += 1
                show_progress(done[0], len(papers), prefix="  Extracting")
            return result

        # Papers are extracted several at a time; results are written in the input order.
        with ThreadPoolExecutor(max_workers=max(1, min(self.EXTRACTION_WORKERS, len(papers)))) as pool:
            futures = [run_ledger.submit_in_context(pool, extract, item) for item in enumerate(papers, start=1)]
            results = [future.result() for future in futures]
        with atomic_output_path(output_file) as pending_output:
            for paper_meta, result in zip(papers, results):
                append_jsonl(str(pending_output), result)
                if result["extraction_status"] == "success":
                    processed += 1
                    total_cost += float(result.get("extraction_cost_usd") or 0)
                    for tier in (result.get("evidence_tiers") or {}).values():
                        tier_counts[tier] = tier_counts.get(tier, 0) + 1
                    if result.get("extraction_source") == "web_search_fallback":
                        web_search_fallback += 1
                        web_search_failed += (result.get("web_search") or {}).get("status") == "failed"
                else:
                    errors += 1
                if lacks_full_text(paper_meta) and (result["extraction_status"] != "success" or result.get("web_search_fallback_pending")):
                    pending_web_search_fallback += 1

        print()  # New line after progress

        # Save statistics
        stats = {
            "extracted_at": datetime.now().isoformat(),
            "total_pdfs": len(pdf_files),
            "processed": processed,
            "errors": errors,
            "web_search_fallback": web_search_fallback,
            "pending_web_search_fallback": pending_web_search_fallback,
            "no_full_text": {
                "papers": sum(1 for paper in papers if lacks_full_text(paper)),
                "web_search": "enabled" if web_search else "skipped",
                "web_search_skipped_reason": "" if web_search else f"{self.model} has no web-search tool; abstract extraction ran alone.",
                "web_search_failed": web_search_failed,
            },
            "field_evidence_tiers": tier_counts,
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
            "no_full_text": stats["no_full_text"],
            "field_evidence_tiers": tier_counts,
            "total_cost": total_cost,
            "stats": stats
        }

    @staticmethod
    def _paper_key(paper: Dict[str, Any]) -> tuple:
        return (str(paper.get("source") or ""), str(paper.get("id") or paper.get("paper_id") or ""), str(paper.get("title") or "").strip().lower())

    def _reusable_rows(self, output_file: Path, papers: List[Dict[str, Any]]) -> Dict[tuple, Dict[str, Any]]:
        """Successful rows that still match their paper's evidence: a row made without full text is redone once a PDF exists."""
        rows = {self._paper_key({**row, "id": row.get("paper_id")}): row for row in read_jsonl(str(output_file))} if Path(output_file).exists() else {}
        kept = {}
        for paper in papers:
            row = rows.get(self._paper_key(paper))
            if row and row.get("extraction_status") == "success" and (row.get("extraction_source") == "pdf" or lacks_full_text(paper)):
                kept[self._paper_key(paper)] = row
        return kept

    def _load_extraction_prompt(self) -> Dict[str, Any]:
        for path in [self.project_path / "prompts" / "extraction_prompt.json", self.project_path / "extraction" / "extraction_prompt.json"]:
            if path.exists():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
        return {}

    def extract_one(
        self,
        *,
        paper: Dict[str, Any],
        row_number: int,
        extraction_prompt: Dict[str, Any],
        pdf_folder: Path,
        pdf_files: List[Path],
        llm_query=None,
        pdf_reader=None,
        web_search_query=None,
        web_search: bool | None = None,
    ) -> Dict[str, Any]:
        """Extract one paper without writing formal extraction artifacts."""
        active_llm_query = llm_query or self.llm_query
        active_pdf_reader = pdf_reader or self.pdf_reader
        active_web_search_query = web_search_query or self.web_search_query
        if lacks_full_text(paper):
            if web_search is None:
                web_search = active_web_search_query is not None or supports_web_search(self.model)
            try:
                if active_llm_query is None and self.client is None and str(paper.get("abstract") or "").strip():
                    self._init_client()
                result, _cost = self._extract_without_full_text(
                    paper=paper,
                    row_number=row_number,
                    extraction_prompt=extraction_prompt,
                    llm_query=active_llm_query,
                    web_search_query=active_web_search_query,
                    web_search=web_search,
                    page_fetcher=self.page_fetcher,
                )
                return result
            except Exception as exc:
                self.log(f"Extraction without full text failed for row {row_number}: {exc}", "error")
                return self._no_full_text_error_record(paper, row_number, str(exc))

        pdf_file = self._pdf_for_paper(row_number, paper, Path(pdf_folder), pdf_files)
        if pdf_file is None:
            return self._error_record(paper, row_number, "PDF file not found")
        try:
            text = active_pdf_reader(pdf_file) if active_pdf_reader else self._read_pdf(pdf_file)
            if not text:
                self.log(f"Could not extract text from {pdf_file.name}", "warning")
                return self._error_record(paper, row_number, "Could not extract text from PDF", pdf_file)
            if active_llm_query is None and self.client is None:
                self._init_client()
            system_prompt = extraction_prompt.get("system_prompt")
            user_template = extraction_prompt.get("user_prompt_template")
            if not isinstance(system_prompt, str) or not isinstance(user_template, str):
                raise ValueError("Extraction requires a finalized prompt")
            if (extraction_prompt.get("schema") or {}).get("fields"):
                system_prompt += "\n" + FIELD_EVIDENCE_INSTRUCTION
            extracted, cost = self._extract_with_llm(text, system_prompt, user_template, active_llm_query)
            extracted_data = self._parse_extracted_data(extracted)
            schema_fields = (extraction_prompt.get("schema") or {}).get("fields", [])
            supplied_evidence = self._pop_field_evidence(extracted_data, schema_fields)
            field_evidence = verify_field_evidence(supplied_evidence, schema_fields, self._sanitize_text_for_utf8(text)[:400000], tier=FULL_TEXT)
            extracted_data = self._validate_schema_data(extracted_data, extraction_prompt, set())
            return {
                **extracted_data,
                "paper_id": paper.get("id", "unknown"),
                "source": paper.get("source", "unknown"),
                "title": paper.get("title", pdf_file.stem),
                "pdf_file": pdf_file.name,
                "row_number": row_number,
                "extracted_at": datetime.now().isoformat(),
                "extraction_model": self.model,
                "extraction_cost_usd": cost,
                "extracted_data": extracted_data,
                "field_evidence": field_evidence,
                "evidence_tiers": {name: FULL_TEXT for name in field_evidence},
                "extraction_source": "pdf",
                "extraction_status": "success",
            }
        except Exception as exc:
            self.log(f"Error processing {pdf_file.name}: {exc}", "error")
            return self._error_record(paper, row_number, str(exc), pdf_file)

    def _pdf_for_paper(self, row_number: int, paper: Dict[str, Any], pdf_folder: Path, pdf_files: List[Path]) -> Path | None:
        if paper.get("pdf_path") and Path(str(paper["pdf_path"])).exists():
            return Path(str(paper["pdf_path"]))
        if paper.get("pdf_identity_required"):
            return None
        row_prefix = f"row{row_number}_"
        for pdf_file in pdf_files:
            if pdf_file.name.startswith(row_prefix):
                return pdf_file
        return None

    def _extract_without_full_text(
        self,
        *,
        paper: Dict[str, Any],
        row_number: int,
        extraction_prompt: Dict[str, Any],
        llm_query=None,
        web_search_query=None,
        web_search: bool | None = None,
        page_fetcher=None,
    ) -> tuple[Dict[str, Any], float]:
        """Extract a paper that has no full text: abstract first, then web sources for what is still missing.

        Every field ends with one evidence tier. Abstract values need a quote found in the
        record's abstract; web values need a source URL of an allowed type, a page confirmed
        to be this paper, and a quote found on that page. Code applies these checks; any
        value that fails them is stored as not available rather than guessed.
        """
        fields = [field for field in (extraction_prompt.get("schema") or {}).get("fields") or [] if isinstance(field, dict)]
        if not fields:
            raise ValueError("Extraction without full text requires a finalized extraction schema")
        names = [field["name"] for field in fields]
        values: Dict[str, Any] = {}
        evidence: Dict[str, Dict[str, Any]] = {}
        auxiliary: Dict[str, Any] = {}
        cost = 0.0

        rejected: Dict[str, List[Dict[str, str]]] = {}

        # Step 1: the abstract already in the record, with the same schema and coding rules as full text.
        # Fields are checked one by one: a missing field is empty, an off-type one is not available.
        abstract = self._sanitize_text_for_utf8(paper.get("abstract")).strip()
        if abstract:
            record_text = f"TITLE: {self._sanitize_text_for_utf8(paper.get('title')).strip()}\n\nABSTRACT:\n{abstract}"
            system_prompt = f"{extraction_prompt.get('system_prompt')}\n{FIELD_EVIDENCE_INSTRUCTION}\n{ABSTRACT_ONLY_NOTE}"
            extracted, step_cost = self._extract_with_llm(record_text, system_prompt, extraction_prompt.get("user_prompt_template"), llm_query)
            cost += step_cost
            data = self._parse_extracted_data(extracted)
            supplied = self._pop_field_evidence(data, fields)
            verified = verify_field_evidence(supplied, fields, record_text, tier=ABSTRACT)
            for field in fields:
                name = field["name"]
                value, problem, unmatched = self._checked_value(field, data.get(name))
                if problem:
                    rejected.setdefault(name, []).append({"url": "", "source": "abstract", "reason": "value_type", "detail": problem})
                    continue
                if _has_value(value) and verified[name]["status"] == "located":
                    values[name] = value
                    evidence[name] = {**verified[name], "source": "record_abstract"}
                    if unmatched is not None:
                        auxiliary[fulltext_eligibility.RAW_KEY] = unmatched

        # Step 2: web sources, only for fields the abstract left open.
        missing = [field for field in fields if field["name"] not in values]
        if not missing:
            web = {"status": "not_needed"}
        elif web_search is False:
            web = {"status": "skipped", "reason": f"{self.model} has no web-search tool; fields the abstract leaves open are not available."}
        else:
            try:
                sub_prompt = {**extraction_prompt, "schema": {**(extraction_prompt.get("schema") or {}), "fields": missing}}
                if web_search_query is not None:
                    response, usage = web_search_query(paper=paper, extraction_prompt=sub_prompt, model=self.model)
                else:
                    response, usage = self._query_web_search_extraction(paper, sub_prompt)
                data = self._parse_extracted_data(response)
            except Exception as exc:
                self.log(f"Web search failed for row {row_number}: {exc}", "warning")
                web = {"status": "failed", "error": self._sanitize_text_for_utf8(exc)[:300]}
            else:
                cost += self._usage_cost(usage)
                supplied = self._pop_field_evidence(data, missing)
                fetcher = page_fetcher or (lambda url: web_evidence.fetch_page(url))
                pages: Dict[str, Any] = {}
                for field in missing:
                    name = field["name"]
                    value, problem, _unmatched = self._checked_value(field, data.get(name))
                    if problem:
                        rejected.setdefault(name, []).append({"url": "", "source": "web", "reason": "value_type", "detail": problem})
                        continue
                    if not _has_value(value):
                        continue
                    entries = supplied.get(name)
                    entries = [entry for entry in (entries if isinstance(entries, list) else [entries]) if isinstance(entry, dict)]
                    if not entries:
                        rejected.setdefault(name, []).append({"url": "", "reason": "missing_evidence", "detail": "No source URL and quote were given."})
                    for entry in entries[:5]:
                        try:
                            accepted = web_evidence.accept_web_evidence(entry, paper, fetcher, pages)
                        except web_evidence.RejectedSource as exc:
                            rejected.setdefault(name, []).append({"url": evidence_support.safe_url(entry.get("url") or entry.get("source_url")),
                                                                  "reason": exc.reason, "detail": exc.detail})
                            continue
                        values[name] = value
                        evidence[name] = {**accepted, "tier": WEB}
                        break
                web = {"status": "completed", "fields_searched": [field["name"] for field in missing]}

        # Step 3: everything else stays empty and says why.
        for name in names:
            if name not in values:
                values[name] = ""
                evidence[name] = {"tier": NOT_AVAILABLE, "quote": "", "page": None, "status": "not_available",
                                  "verification": "no_full_text"}
                if rejected.get(name):
                    evidence[name]["rejected_sources"] = rejected[name][:5]
        extracted_data = {**{name: values[name] for name in names}, **auxiliary}
        source_urls = list(dict.fromkeys(item["source_url"] for item in evidence.values() if item.get("tier") == WEB))
        return {
            **extracted_data,
            "paper_id": paper.get("id", "unknown"),
            "source": paper.get("source", "unknown"),
            "title": paper.get("title", f"Paper {row_number}"),
            "doi": paper.get("doi", ""),
            "row_number": row_number,
            "extracted_at": datetime.now().isoformat(),
            "extraction_model": self.model,
            "extraction_cost_usd": cost,
            "extracted_data": extracted_data,
            "field_evidence": evidence,
            "evidence_tiers": {name: evidence[name]["tier"] for name in names},
            "extraction_source": "web_search_fallback",
            "extraction_status": "success",
            "source_urls": source_urls,
            "web_search": web,
            "pdf_failure_class": paper.get("pdf_failure_class", ""),
            "retrieval_status": paper.get("retrieval_status", ""),
            # A failed web step stays retryable; the abstract values are kept meanwhile.
            "web_search_fallback_pending": web["status"] == "failed",
        }, cost

    def _checked_value(self, field: Dict[str, Any], value: Any) -> tuple[Any, str, Any]:
        """(value, problem, unmatched eligibility value) for one field of an abstract or web response.

        The same coercion as full-text extraction; a value still off its declared type or options
        comes back with a problem and costs only this field, never the whole paper.
        """
        unmatched = None
        if not _has_value(value):
            return "", "", None
        if field["name"] == fulltext_eligibility.FIELD and isinstance(field.get("enum"), list):
            value, unmatched = fulltext_eligibility.normalize_value(value, field["enum"])
            if unmatched is not None:
                return "", f"{field['name']} must use the declared options", None
        try:
            value = coerce_value(field, value)
            validate_value(field, value)
        except ValueError as exc:
            return "", str(exc), None
        return value, "", unmatched

    def _query_web_search_extraction(self, paper: Dict[str, Any], extraction_prompt: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        if self.client is None:
            self._init_client()
        system_prompt = (
            f"{extraction_prompt.get('system_prompt', '')}\n\n{WEB_EVIDENCE_RULES}\n"
            "Return one JSON object containing only the requested schema fields you found, plus _field_evidence.\n"
            f"{WEB_FIELD_EVIDENCE_INSTRUCTION}"
        )
        if self.skill_augment is not None:
            system_prompt = self.skill_augment(system_prompt)
        response = self.client.responses.create(
            model=self.model,
            tools=[{"type": "web_search", "filters": {"allowed_domains": self._web_search_domains(paper)}}],
            tool_choice="required",
            include=["web_search_call.action.sources"],
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._web_search_fallback_prompt(paper, extraction_prompt)},
            ],
        )
        output_text = str(getattr(response, "output_text", "") or "").strip()
        if not output_text:
            raise ValueError("Responses API web_search returned no output_text")
        usage = {**self._usage_dict(getattr(response, "usage", None)), "web_search_calls": self._web_search_call_count(response)}
        run_ledger.record_llm_call(self.model, usage, cost_usd=self._usage_cost(usage), kind="web_search")
        return output_text, usage

    def _web_search_domains(self, paper: Dict[str, Any]) -> list[str]:
        # The search tool accepts a short allow-list; code re-checks every page it cites.
        # This paper's own publisher and registries come first, so the cap never drops them.
        domains = web_evidence.doi_hosts(paper.get("doi")) + sorted(web_evidence.paper_hosts(paper))
        domains += [web_evidence._host(url) for url in web_evidence.candidate_pages(paper) if "doi.org" not in url]
        domains += ["pmc.ncbi.nlm.nih.gov", "europepmc.org", "clinicaltrials.gov", "isrctn.com", "trialsearch.who.int",
                    "medrxiv.org", "biorxiv.org", "arxiv.org", "researchsquare.com", "ssrn.com",
                    "sciencedirect.com", "link.springer.com", "onlinelibrary.wiley.com", "nature.com", "tandfonline.com",
                    "journals.sagepub.com", "ieeexplore.ieee.org", "journals.lww.com", "academic.oup.com", "jamanetwork.com"]
        return list(dict.fromkeys(domain for domain in domains if domain))[:20]

    def _web_search_fallback_prompt(self, paper: Dict[str, Any], extraction_prompt: Dict[str, Any]) -> str:
        schema = extraction_prompt.get("schema") or {"fields": extraction_prompt.get("fields") or []}
        doi = web_evidence.normalize_doi(paper.get("doi"))
        title = str(paper.get("title") or "").strip()
        queries = [query for query in (doi and f'"{doi}"', title and f'"{title}"') if query]
        registries = [identifier for identifier, _url in web_evidence.registry_ids(paper.get("abstract"))]
        return f"""PAPER RECORD DATA:
{json.dumps({key: paper.get(key) for key in ("title", "authors", "year", "doi", "journal", "url", "source")}, ensure_ascii=False, indent=2)}

PAGES THAT BELONG TO THIS PAPER (open these first):
{json.dumps(web_evidence.candidate_pages(paper), ensure_ascii=False, indent=2)}

SEARCH QUERIES THAT IDENTIFY THIS PAPER:
{json.dumps(queries + registries, ensure_ascii=False)}

REQUESTED SCHEMA FIELDS (the abstract did not state these):
{json.dumps(schema, ensure_ascii=False, indent=2)}

Open the pages above, then search with the queries above. Use a page only when it is this paper's own page: the article landing page reached through its DOI, its trial registry entry, its PMC or Europe PMC record, a preprint of the same work, or its supplementary material. Article landing pages and registry entries often state registration, funding, data or code availability, study setting, design, participants and declarations; read the whole page, including its declarations and article information sections.
Return ONLY valid JSON with each requested field you found by its field name and _field_evidence giving url and quote for each of them; return {{}} when no such page states any requested field."""

    @staticmethod
    def _pop_field_evidence(data: Dict[str, Any], fields: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Take the _field_evidence object out of a model answer. Evidence written as flat
        "<field>_field_evidence" keys is folded in, so that layout costs nothing instead of failing the paper."""
        supplied = data.pop("_field_evidence", {})
        supplied = dict(supplied) if isinstance(supplied, dict) else {}
        names = {field.get("name") for field in fields if isinstance(field, dict)}
        for name in names:
            key = f"{name}_field_evidence"
            if key in data and key not in names:
                flat = data.pop(key)
                supplied.setdefault(name, {"quote": flat} if isinstance(flat, str) else flat)
        return supplied

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

    def _web_search_call_count(self, response: Any) -> int:
        # Each search the tool ran is billed per call on top of tokens.
        return sum(1 for item in getattr(response, "output", None) or []
                   if (item.get("type") if isinstance(item, dict) else getattr(item, "type", "")) == "web_search_call")

    def _usage_cost(self, usage: Any) -> float:
        """Cost at the extraction model's listed price; a usage report that already carries its cost is taken as is."""
        from utils.llm import calculate_cost

        data = self._usage_dict(usage)
        if isinstance(data.get("cost_usd"), (int, float)):
            return float(data["cost_usd"])
        input_tokens = int(data.get("input_tokens") or 0)
        output_tokens = int(data.get("output_tokens") or 0)
        if not (input_tokens or output_tokens):
            # Only a total is known; price it all as input, the larger share for full-text extraction.
            input_tokens = int(data.get("total_tokens") or 0)
        # Each search the web-search tool ran is billed per call on top of its tokens.
        return calculate_cost(input_tokens, output_tokens, self.model) + int(data.get("web_search_calls") or 0) * WEB_SEARCH_CALL_USD

    def _no_full_text_error_record(self, paper: Dict[str, Any], row_number: int, message: str) -> Dict[str, Any]:
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
        except json.JSONDecodeError as exc:
            raise ValueError("Extraction model response must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Extraction model response must be a JSON object")
        return parsed

    def _validate_schema_data(
        self,
        data: Dict[str, Any],
        extraction_prompt: Dict[str, Any],
        auxiliary_fields: set[str],
    ) -> Dict[str, Any]:
        json.dumps(data, allow_nan=False)
        schema = extraction_prompt.get("schema") if isinstance(extraction_prompt.get("schema"), dict) else {}
        expected = [
            field["name"].strip()
            for field in schema.get("fields") or []
            if isinstance(field, dict) and isinstance(field.get("name"), str) and field["name"].strip()
        ]
        if not expected:
            return data
        from reviewpilot_core.extraction_schema import validate_schema
        validate_schema(schema)
        if fulltext_eligibility.FIELD in expected and fulltext_eligibility.FIELD not in data:
            # An omitted eligibility judgement keeps the paper, as an empty one does, and is counted as unclear.
            data[fulltext_eligibility.FIELD] = ""
            data[fulltext_eligibility.RAW_KEY] = fulltext_eligibility.MISSING
        missing = set(expected) - set(data)
        unexpected = set(data) - set(expected) - auxiliary_fields - {fulltext_eligibility.RAW_KEY}
        if missing or unexpected:
            details = []
            if missing:
                details.append(f"missing fields: {', '.join(sorted(missing))}")
            if unexpected:
                details.append(f"unexpected fields: {', '.join(sorted(unexpected))}")
            raise ValueError("Extraction model response violates schema: " + "; ".join(details))
        from reviewpilot_core.field_values import coerce_value, validate_value
        for field in schema.get('fields') or []:
            # A list given as one string or a number given as text must not fail the whole paper.
            data[field['name']] = coerce_value(field, data[field['name']])
            if field['name'] == fulltext_eligibility.FIELD and isinstance(field.get('enum'), list):
                # One stray dash or capital must not fail the whole paper; keep what could not be mapped.
                data[field['name']], unmatched = fulltext_eligibility.normalize_value(data[field['name']], field['enum'])
                if unmatched is not None:
                    data[fulltext_eligibility.RAW_KEY] = unmatched
            validate_value(field, data[field['name']])
        return data

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
            for page_number, page in enumerate(reader.pages, 1):
                page_text = page.extract_text()
                if page_text:
                    text += f"[PDF page {page_number}]\n" + page_text + "\n"
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
            return response, self._usage_cost(usage if isinstance(usage, dict) else {})

        try:
            request = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
            }
            if accepts_custom_temperature(self.model):
                request["temperature"] = 0.3
            if "json" in f"{system_prompt}\n{user_prompt}".lower():
                request["response_format"] = {"type": "json_object"}
            # Reasoning models (gpt-5/gpt-6) spend completion tokens on reasoning before any text,
            # so a fixed cap can leave the answer empty; they run uncapped like every other agent call.
            if not self.model.startswith(("gpt-5", "gpt-6")):
                request["max_tokens"] = 4096
            response = self.client.chat.completions.create(**request)

            choice = response.choices[0]
            extracted = choice.message.content
            if choice.finish_reason == "length":
                raise ValueError("Extraction model ran out of output tokens before finishing its answer")

            # Estimate cost (rough approximation)
            input_tokens = response.usage.prompt_tokens if response.usage else 0
            output_tokens = response.usage.completion_tokens if response.usage else 0

            cost = self._usage_cost({"input_tokens": input_tokens, "output_tokens": output_tokens})
            run_ledger.record_llm_call(self.model, {"input_tokens": input_tokens, "output_tokens": output_tokens}, cost_usd=cost)

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
