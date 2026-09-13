"""
Filtering Agent.
Handles paper deduplication and LLM-based relevance checking.
Outputs filtered papers in JSONL format with a log file.
"""

from reviewpilot_core.screening_evidence import evidence_prompt, parse_screening_response
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import re

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
from reviewpilot_core.model_policy import FILTERING_MODEL
from utils.jsonl_handler import (
    read_jsonl, write_jsonl, append_jsonl, save_json, load_json, JSONLWriter
)
from utils.human_interaction import (
    print_header, print_summary, show_progress, ask_confirm
)

try:
    import pandas as pd
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    print("Warning: sklearn not installed. Similarity-based deduplication disabled.")


class FilteringAgent(BaseAgent):
    """
    Agent responsible for filtering and deduplicating collected papers.

    Performs:
    1. Exact title deduplication
    2. Similarity-based deduplication (TF-IDF cosine similarity)
    3. Year filtering
    4. LLM-based relevance checking
    """

    def __init__(self, project_path: Path, model: str = FILTERING_MODEL, llm_query=None):
        """
        Initialize the filtering agent.

        Args:
            project_path: Path to the project directory
            model: LLM model to use for relevance checking
        """
        super().__init__(project_path, "filtering")
        self.model = model
        self.llm_query = llm_query

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Filter collected papers.

        Args:
            input_data: Contains:
                - collected_folder: Path to collected papers
                - relevance_prompt: Prompt configuration for relevance checking
                - date_range: Optional date filtering

        Returns:
            Dictionary with filtering results and statistics
        """
        print_header("Paper Filtering")

        collected_folder = Path(input_data.get("collected_folder", self.project_path / "collected"))
        relevance_prompt = input_data.get("relevance_prompt", {})
        if relevance_prompt.get("criteria_finalized"):
            relevance_prompt = evidence_prompt(relevance_prompt)
        date_range = input_data.get("date_range") or {}
        auto_approve = input_data.get("auto_approve", False)

        # Create output directory
        output_dir = self.ensure_directory("filtered")

        # Step 1: Load all collected papers
        print("  Loading collected papers...")
        papers = self._load_all_papers(collected_folder)
        initial_count = len(papers)
        self.log(f"Loaded {initial_count} papers from collection")

        self.removed_records = []
        for index, paper in enumerate(papers):
            paper['collection_record_id'] = f'record-{index + 1}'

        # Step 2: Publication date filtering
        if date_range is not None:
            print(f"  Filtering by date range...")
            papers = self._filter_by_date(papers, date_range)
            print(f"    {initial_count} -> {len(papers)} papers (after date filter)")

        after_date_count = len(papers)

        # Step 3: Exact title deduplication
        print("  Removing exact duplicates...")
        papers = self._deduplicate_exact(papers)
        after_exact_count = len(papers)
        print(f"    {after_date_count} -> {after_exact_count} papers (after exact dedup)")

        # Step 4: Similarity-based deduplication
        if SKLEARN_AVAILABLE and len(papers) > 1:
            print("  Finding near-duplicates (TF-IDF similarity > 0.7)...")
            papers, similarity_removed = self._deduplicate_similarity(papers)
            after_similarity_count = len(papers)
            print(f"    {after_exact_count} -> {after_similarity_count} papers (after similarity dedup)")
        else:
            similarity_removed = []
            after_similarity_count = after_exact_count

        # Step 5: LLM-based relevance checking
        run_relevance = auto_approve or ask_confirm("Run LLM relevance check?", default=True)
        if relevance_prompt and run_relevance:
            print(f"\n  Checking relevance with {self.model}...")
            papers, irrelevant = self._check_relevance(
                papers, relevance_prompt, output_dir
            )
            after_relevance_count = len(papers)
            print(f"    {after_similarity_count} -> {after_relevance_count} papers (after relevance check)")
        else:
            irrelevant = []
            after_relevance_count = after_similarity_count
            print("  Skipping relevance check.")

        # Save stage artifacts
        output_file = output_dir / "filtered_papers.jsonl"
        included_file = output_dir / "included_papers.jsonl"
        excluded_file = output_dir / "excluded_papers.jsonl"
        write_jsonl(str(output_file), papers)
        write_jsonl(str(included_file), papers)
        write_jsonl(str(excluded_file), irrelevant)
        write_jsonl(str(output_dir / 'removed_records.jsonl'), self.removed_records)
        self.log(f"Saved {len(papers)} filtered papers to {output_file}")

        # Save statistics
        stats = {
            "filtered_at": datetime.now().isoformat(),
            "initial_count": initial_count,
            "after_date_filter": after_date_count,
            "after_exact_dedup": after_exact_count,
            "after_similarity_dedup": after_similarity_count,
            "after_relevance_check": after_relevance_count,
            "removed": {
                "by_date": initial_count - after_date_count,
                "by_exact_dedup": after_date_count - after_exact_count,
                "by_similarity": after_exact_count - after_similarity_count,
                "by_relevance": after_similarity_count - after_relevance_count
            },
            "final_count": len(papers),
            "total_screened": after_similarity_count,
            "included_count": len(papers),
            "excluded_count": len(irrelevant)
        }
        screening_stats = {
            **stats,
            "total_screened": after_similarity_count,
            "included_count": len(papers),
            "excluded_count": len(irrelevant)
        }
        save_json(str(output_dir / "filtering_stats.json"), stats)
        save_json(str(output_dir / "screening_stats.json"), screening_stats)

        # Show summary
        print_summary({
            "Initial papers": initial_count,
            "After date filter": after_date_count,
            "After exact dedup": after_exact_count,
            "After similarity dedup": after_similarity_count,
            "After relevance check": after_relevance_count,
            "Final papers": len(papers),
            "Output file": str(output_file)
        }, title="\nFiltering Complete")

        # Save state
        self.state = {
            "completed": True,
            "stats": stats,
            "output_file": str(output_file)
        }
        self.save_state()

        return {
            "filtered_file": str(output_file),
            "included_file": str(included_file),
            "excluded_file": str(excluded_file),
            "filtered_count": len(papers),
            "included_count": len(papers),
            "excluded_count": len(irrelevant),
            "stats": stats
        }

    def _load_all_papers(self, collected_folder: Path) -> List[Dict]:
        """Load papers only from sources in the current collection summary."""
        papers = []
        summary = load_json(str(collected_folder / "summary.json")) or {}
        platform_stats = summary.get("platform_stats")
        if not isinstance(platform_stats, dict):
            return papers
        platform_errors = summary.get("platform_errors") or {}
        if not isinstance(platform_errors, dict):
            raise ValueError("Collection summary platform_errors must be an object")

        for platform, expected_count in platform_stats.items():
            if not isinstance(platform, str) or not platform or Path(platform).name != platform:
                raise ValueError("Collection summary contains an invalid source name")
            if type(expected_count) is not int or expected_count < 0:
                raise ValueError(f"Collection summary has an invalid count for {platform}")
            if expected_count == 0 or platform in platform_errors:
                continue
            jsonl_file = collected_folder / f"{platform}.jsonl"
            if not jsonl_file.is_file():
                raise ValueError(f"Current collection artifact is missing for {platform}")
            file_papers = read_jsonl(str(jsonl_file))
            if len(file_papers) != expected_count:
                raise ValueError(f"{platform} artifact does not match current collection summary")
            papers.extend(file_papers)
            self.log(f"Loaded {len(file_papers)} papers from {jsonl_file.name}")

        return papers

    def _record_removal(self, paper, kind, reason, representative=None):
        if not hasattr(self, 'removed_records'):
            self.removed_records = []
        identity = lambda row: {k: row.get(k) for k in ('collection_record_id', 'id', 'doi', 'source', 'title')}
        self.removed_records.append({**paper, 'removal': {'kind': kind, 'reason': reason,
            'representative': identity(representative) if representative else None,
            'duplicate_group': representative.get('collection_record_id') if representative else None}})

    def _filter_by_date(self, papers: List[Dict], date_range: Dict) -> List[Dict]:
        """Filter papers by publication date."""
        from reviewpilot_core.publication_dates import assess
        filtered = []
        for paper in papers:
            assessment = assess(paper, date_range)
            paper['date_assessment'] = assessment
            if not assessment['excluded']:
                filtered.append(paper)
            else:
                self._record_removal(paper, 'date', assessment['reason'])
        return filtered

    def _deduplicate_exact(self, papers: List[Dict]) -> List[Dict]:
        """Remove exact title duplicates."""
        seen_titles = {}
        unique_papers = []

        for paper in papers:
            title = paper.get("title", "")
            normalized = self._normalize_title(title)

            if not normalized or normalized not in seen_titles:
                seen_titles[normalized] = paper
                unique_papers.append(paper)
            else:
                self._record_removal(paper, 'exact_duplicate', 'Identical normalized title.', seen_titles[normalized])

        return unique_papers

    def _normalize_title(self, title: str) -> str:
        """Normalize title for comparison."""
        if not title:
            return ""
        # Lowercase, remove punctuation, remove extra spaces
        normalized = title.lower()
        normalized = re.sub(r'[^\w\s]', '', normalized)
        normalized = ' '.join(normalized.split())
        return normalized

    def _deduplicate_similarity(self, papers: List[Dict], threshold: float = 0.7) -> Tuple[List[Dict], List[int]]:
        """Remove near-duplicate papers using TF-IDF similarity."""
        if not SKLEARN_AVAILABLE or len(papers) < 2:
            return papers, []

        # Create signatures (title + authors)
        signatures = []
        for paper in papers:
            title = paper.get("title", "")
            authors = paper.get("authors", [])
            if isinstance(authors, list):
                authors = " ".join(authors)
            signatures.append(f"{title} {authors}")

        # Vectorize
        try:
            vectorizer = TfidfVectorizer(stop_words='english', min_df=1)
            tfidf_matrix = vectorizer.fit_transform(signatures)
            similarity_matrix = cosine_similarity(tfidf_matrix)
        except Exception as e:
            self.log(f"Error computing similarity: {e}", "warning")
            return papers, []

        # Find duplicates (keep first occurrence)
        to_remove = set()
        for i in range(len(papers)):
            if i in to_remove:
                continue
            for j in range(i + 1, len(papers)):
                if j in to_remove:
                    continue
                if similarity_matrix[i, j] >= threshold:
                    to_remove.add(j)
                    self._record_removal(papers[j], 'similar_duplicate', f'Title/author similarity {similarity_matrix[i, j]:.3f} >= {threshold}.', papers[i])

        # Filter papers
        unique_papers = [p for i, p in enumerate(papers) if i not in to_remove]

        return unique_papers, list(to_remove)

    def _check_relevance(self, papers: List[Dict], prompt_config: Dict, output_dir: Path) -> Tuple[List[Dict], List[Dict]]:
        """Check paper relevance using LLM."""
        from utils.llm import query_llm

        system_prompt = prompt_config.get("system_prompt", "")
        user_template = prompt_config.get("user_prompt_template", "")

        relevant_papers = []
        irrelevant_papers = []

        # Open log file for real-time writing
        log_file = output_dir / "filtering_log.jsonl"

        total = len(papers)
        for i, paper in enumerate(papers):
            show_progress(i + 1, total, prefix="  Checking relevance")

            title = paper.get("title", "")
            raw_abstract = str(paper.get("abstract") or "")
            abstract = raw_abstract if prompt_config.get("review_evidence") else raw_abstract[:1000]
            if len(raw_abstract) > len(abstract):
                abstract += "\n[Abstract truncated by ReviewPilot after 1000 characters]"

            # Substitute only the two declared record placeholders. The generated
            # template also contains JSON scope data whose braces are literal data.
            record_values = {
                "title": title,
                "abstract": abstract if abstract else "No abstract available",
            }
            user_prompt = re.sub(
                r"\{(title|abstract)\}",
                lambda match: record_values[match.group(1)],
                user_template,
            )

            try:
                active_llm_query = self.llm_query or query_llm
                response, usage = active_llm_query(
                    text_prompt=user_prompt,
                    system_prompt=system_prompt,
                    model=self.model,
                    provider="openai"
                )

                if prompt_config.get("review_evidence"):
                    is_relevant, rationale = parse_screening_response(response, paper, prompt_config)
                    paper["screening_evidence"] = rationale
                else:
                    decision = response.strip().casefold()
                    if decision not in {"true", "false"}:
                        raise ValueError("Relevance model response must be exactly True or False")
                    is_relevant = decision == "true"

                # Add relevance info to paper
                paper["is_relevant"] = is_relevant
                paper["relevance_response"] = response.strip()

                if is_relevant:
                    relevant_papers.append(paper)
                else:
                    irrelevant_papers.append(paper)

                # Log the result
                log_entry = {
                    "paper_id": paper.get("id", ""),
                    "title": title[:100],
                    "timestamp": datetime.now().isoformat(),
                    "action": "kept" if is_relevant else "removed_irrelevant",
                    "response": response.strip()
                }
                append_jsonl(str(log_file), log_entry)

            except Exception as e:
                self.log(f"Error checking relevance for {title[:50]}: {e}", "warning")
                # Keep paper if check fails
                paper["is_relevant"] = None
                paper["relevance_error"] = str(e)
                relevant_papers.append(paper)

        print()  # New line after progress bar
        return relevant_papers, irrelevant_papers
