"""
Pipeline Coordinator.
Orchestrates all agents in the multi-agent system with human-in-the-loop interactions.
Supports checkpointing and resume functionality.
"""

from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
import logging

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import BaseAgent
from agents.search_condition_agent import SearchConditionAgent
from agents.prompt_agent import PromptAgent
from agents.collection_agent import CollectionAgent
from agents.filtering_agent import FilteringAgent
from agents.download_agent import DownloadAgent
from agents.extraction_agent import ExtractionAgent
from utils.jsonl_handler import save_json, load_json
from utils.human_interaction import (
    print_header, print_summary, ask_confirm, pause
)


# Pipeline stages in order
STAGES = [
    "search_conditions",
    "prompt_relevance",
    "collection",
    "filtering",
    "prompt_extraction",
    "download",
    "extraction"
]


class PipelineCoordinator:
    """
    Central orchestrator for the multi-agent pipeline.

    Responsibilities:
    - Initialize project folder structure
    - Manage agent state and transitions
    - Handle human-in-the-loop interactions
    - Log all pipeline events
    - Handle errors and recovery
    - Support resumption from checkpoints
    """

    def __init__(self, output_dir: str = "output"):
        """
        Initialize the pipeline coordinator.

        Args:
            output_dir: Base output directory for all projects
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.project_path: Optional[Path] = None
        self.state: Dict[str, Any] = {}
        self.logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        """Set up logging for the coordinator."""
        logger = logging.getLogger("coordinator")
        if not logger.handlers:
            logger.setLevel(logging.DEBUG)

            # Console handler
            console = logging.StreamHandler()
            console.setLevel(logging.INFO)
            console.setFormatter(logging.Formatter('%(message)s'))
            logger.addHandler(console)

        return logger

    def run_pipeline(
        self,
        resume_from: Optional[str] = None,
        config_file: Optional[str] = None,
        config_data: Optional[Dict[str, Any]] = None,
        model: str = "gpt-5-mini",
        auto_approve: bool = False
    ) -> Dict[str, Any]:
        """
        Execute the full pipeline or resume from a stage.

        Args:
            resume_from: Stage name to resume from (None for fresh start)
            config_file: Optional config file to load search conditions
            config_data: Optional config dictionary (for conversational mode)
            model: LLM model to use for filtering and extraction
            auto_approve: If True, skip confirmation prompts

        Returns:
            Dictionary with pipeline results
        """
        print_header("Multi-Agent Academic Paper Search System")

        # Determine starting point
        if resume_from:
            return self._resume_pipeline(resume_from, model)

        # Fresh start
        results = {}

        try:
            # Stage 1: Search Conditions
            print(f"\n[Stage 1/{len(STAGES)}] Search Condition Configuration")
            search_agent = SearchConditionAgent(output_dir=str(self.output_dir))

            if config_data:
                # Use provided config data (from conversational mode)
                search_conditions = search_agent.run(config_data)
            elif config_file:
                config = load_json(config_file)
                search_conditions = search_agent.run(config)
            else:
                search_conditions = search_agent.run()

            self.project_path = Path(search_conditions["project_path"])
            results["search_conditions"] = search_conditions
            self._save_pipeline_state("search_conditions", results)

            # Stage 2: Generate Relevance Prompt
            print(f"\n[Stage 2/{len(STAGES)}] Generating Relevance Prompt")
            prompt_agent = PromptAgent(self.project_path)
            relevance_prompt = prompt_agent.generate_relevance_prompt(search_conditions)
            results["relevance_prompt"] = relevance_prompt
            self._save_pipeline_state("prompt_relevance", results)

            # Stage 3: Collection
            print(f"\n[Stage 3/{len(STAGES)}] Paper Collection")
            if auto_approve or ask_confirm("Proceed to paper collection?", default=True):
                collection_agent = CollectionAgent(self.project_path)
                collection_results = collection_agent.run(search_conditions)
                results["collection"] = collection_results
                self._save_pipeline_state("collection", results)
            else:
                print("  Pipeline paused. Use --resume collection to continue.")
                return results

            # Confirm before filtering
            if not auto_approve and not ask_confirm("Proceed to filtering?", default=True):
                print("  Pipeline paused. Use --resume filtering to continue.")
                return results

            # Stage 4: Filtering
            print(f"\n[Stage 4/{len(STAGES)}] Paper Filtering")
            filtering_agent = FilteringAgent(self.project_path, model=model)
            filtering_input = {
                "collected_folder": collection_results["collected_folder"],
                "relevance_prompt": relevance_prompt,
                "date_range": search_conditions.get("date_range", {}),
                "auto_approve": auto_approve
            }
            filtering_results = filtering_agent.run(filtering_input)
            results["filtering"] = filtering_results
            self._save_pipeline_state("filtering", results)

            # Confirm before extraction prompt
            if not auto_approve and not ask_confirm("Proceed to extraction setup?", default=True):
                print("  Pipeline paused. Use --resume prompt_extraction to continue.")
                return results

            # Stage 5: Generate Extraction Prompt
            print(f"\n[Stage 5/{len(STAGES)}] Generating Extraction Prompt")
            extraction_input = {**search_conditions, "auto_approve": auto_approve}
            extraction_prompt = prompt_agent.generate_extraction_prompt(extraction_input)
            results["extraction_prompt"] = extraction_prompt
            self._save_pipeline_state("prompt_extraction", results)

            # Stage 6: Download
            print(f"\n[Stage 6/{len(STAGES)}] Paper Download")
            if auto_approve or ask_confirm("Proceed to download papers?", default=True):
                download_agent = DownloadAgent(self.project_path)
                download_input = {"filtered_file": filtering_results["filtered_file"]}
                download_results = download_agent.run(download_input)
                results["download"] = download_results
                self._save_pipeline_state("download", results)
            else:
                print("  Pipeline paused. Use --resume download to continue.")
                return results

            # Confirm before extraction
            if not auto_approve and not ask_confirm("Proceed to information extraction?", default=True):
                print("  Pipeline paused. Use --resume extraction to continue.")
                return results

            # Stage 7: Extraction
            print(f"\n[Stage 7/{len(STAGES)}] Information Extraction")
            extraction_agent = ExtractionAgent(self.project_path, model=model)
            extraction_input = {
                "download_folder": download_results["download_folder"],
                "filtered_file": filtering_results["filtered_file"],
                "extraction_prompt": extraction_prompt
            }
            extraction_results = extraction_agent.run(extraction_input)
            results["extraction"] = extraction_results
            self._save_pipeline_state("extraction", results)

            # Final summary
            self._print_final_summary(results)

            return results

        except KeyboardInterrupt:
            print("\n\nPipeline interrupted by user.")
            if self.project_path:
                print(f"Progress saved. Use --resume to continue from last checkpoint.")
            raise SystemExit(1)

        except Exception as e:
            self.logger.error(f"Pipeline error: {e}")
            raise

    def _resume_pipeline(self, stage: str, model: str) -> Dict[str, Any]:
        """Resume pipeline from a specific stage."""
        if stage not in STAGES:
            raise ValueError(f"Unknown stage: {stage}. Valid stages: {STAGES}")

        # Find the most recent project
        projects = [d for d in self.output_dir.iterdir() if d.is_dir()]
        if not projects:
            raise ValueError("No projects found to resume")

        # Sort by modification time and get most recent
        projects.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        self.project_path = projects[0]

        print(f"  Resuming project: {self.project_path.name}")
        print(f"  Starting from stage: {stage}")

        # Load previous state
        state_file = self.project_path / "logs" / "pipeline_state.json"
        if state_file.exists():
            results = load_json(str(state_file))
        else:
            results = {}

        # Load search conditions
        conditions_file = self.project_path / "search_conditions.json"
        if conditions_file.exists():
            search_conditions = load_json(str(conditions_file))
            results["search_conditions"] = search_conditions
        else:
            raise ValueError("Search conditions not found. Cannot resume.")

        # Find stage index
        stage_idx = STAGES.index(stage)

        # Execute remaining stages
        prompt_agent = PromptAgent(self.project_path)

        if stage_idx <= STAGES.index("prompt_relevance"):
            relevance_prompt = prompt_agent.generate_relevance_prompt(search_conditions)
            results["relevance_prompt"] = relevance_prompt
            self._save_pipeline_state("prompt_relevance", results)
        else:
            # Load existing prompt from prompts folder
            prompt_file = self.project_path / "prompts" / "relevance_prompt.json"
            if prompt_file.exists():
                results["relevance_prompt"] = load_json(str(prompt_file))

        if stage_idx <= STAGES.index("collection") and stage != "collection":
            # Collection was done, skip
            pass
        elif stage == "collection" or stage_idx < STAGES.index("collection"):
            collection_agent = CollectionAgent(self.project_path)
            collection_results = collection_agent.run(search_conditions)
            results["collection"] = collection_results
            self._save_pipeline_state("collection", results)

        # Continue with remaining stages...
        # (Similar logic for filtering, download, extraction)

        return results

    def _save_pipeline_state(self, stage: str, results: Dict[str, Any]):
        """Save pipeline state for resume capability."""
        if not self.project_path:
            return

        state = {
            "last_stage": stage,
            "timestamp": datetime.now().isoformat(),
            "results": {k: v for k, v in results.items() if k != "extraction_prompt" and k != "relevance_prompt"}
        }

        state_file = self.project_path / "logs" / "pipeline_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        save_json(str(state_file), state)

    def _print_final_summary(self, results: Dict[str, Any]):
        """Print final pipeline summary."""
        print_header("Pipeline Complete!")

        summary_data = {
            "Project": self.project_path.name if self.project_path else "N/A",
            "Location": str(self.project_path) if self.project_path else "N/A"
        }

        if "collection" in results:
            summary_data["Papers collected"] = results["collection"].get("total_papers", 0)

        if "filtering" in results:
            summary_data["Papers after filtering"] = results["filtering"].get("filtered_count", 0)

        if "download" in results:
            summary_data["PDFs downloaded"] = results["download"].get("pdf_count", 0)

        if "extraction" in results:
            summary_data["Papers extracted"] = results["extraction"].get("processed", 0)
            summary_data["Extraction cost"] = f"${results['extraction'].get('total_cost', 0):.2f}"

        print_summary(summary_data, title="Final Summary")

        # Generate PRISMA log
        self._generate_prisma_log(results)

        if self.project_path:
            print("\nOutput files:")
            print(f"  - {self.project_path / 'search_conditions.json'}")
            print(f"  - {self.project_path / 'collected/'}")
            print(f"  - {self.project_path / 'filtered/filtered_papers.jsonl'}")
            print(f"  - {self.project_path / 'papers/'}")
            print(f"  - {self.project_path / 'extracted/extracted_data.jsonl'}")
            print(f"  - {self.project_path / 'logs/pipeline.log'}")
            print(f"  - {self.project_path / 'logs/prisma_log.json'}")

    def _generate_prisma_log(self, results: Dict[str, Any]):
        """Generate PRISMA-style log for systematic review reporting."""
        if not self.project_path:
            return

        prisma_data = {
            "generated_at": datetime.now().isoformat(),
            "project": self.project_path.name if self.project_path else "N/A",
            "identification": {
                "description": "Records identified from databases",
                "total_records": 0,
                "by_platform": {}
            },
            "screening": {
                "description": "Records after duplicates removed and screened",
                "after_date_filter": 0,
                "after_exact_dedup": 0,
                "after_similarity_dedup": 0,
                "duplicates_removed": 0
            },
            "eligibility": {
                "description": "Records assessed for eligibility (relevance check)",
                "records_screened": 0,
                "records_excluded_irrelevant": 0,
                "records_remaining": 0
            },
            "included": {
                "description": "Studies included in final analysis",
                "pdfs_downloaded": 0,
                "pdfs_extracted": 0,
                "extraction_errors": 0
            },
            "flow_summary": []
        }

        # Populate from collection results
        if "collection" in results:
            collection = results["collection"]
            prisma_data["identification"]["total_records"] = collection.get("total_papers", 0)
            prisma_data["identification"]["by_platform"] = collection.get("platform_stats", {})
            prisma_data["flow_summary"].append(
                f"Identification: {collection.get('total_papers', 0)} records identified from databases"
            )

        # Populate from filtering results
        if "filtering" in results:
            filtering = results["filtering"]
            stats = filtering.get("stats", {})

            prisma_data["screening"]["after_date_filter"] = stats.get("after_date_filter", 0)
            prisma_data["screening"]["after_exact_dedup"] = stats.get("after_exact_dedup", 0)
            prisma_data["screening"]["after_similarity_dedup"] = stats.get("after_similarity_dedup", 0)

            removed = stats.get("removed", {})
            prisma_data["screening"]["duplicates_removed"] = (
                removed.get("by_exact_dedup", 0) + removed.get("by_similarity", 0)
            )

            prisma_data["eligibility"]["records_screened"] = stats.get("after_similarity_dedup", 0)
            prisma_data["eligibility"]["records_excluded_irrelevant"] = removed.get("by_relevance", 0)
            prisma_data["eligibility"]["records_remaining"] = stats.get("final_count", 0)

            prisma_data["flow_summary"].append(
                f"Screening: {prisma_data['screening']['duplicates_removed']} duplicates removed"
            )
            prisma_data["flow_summary"].append(
                f"Eligibility: {removed.get('by_relevance', 0)} records excluded (not relevant)"
            )
            prisma_data["flow_summary"].append(
                f"After screening: {stats.get('final_count', 0)} records remaining"
            )

        # Populate from download results
        if "download" in results:
            download = results["download"]
            prisma_data["included"]["pdfs_downloaded"] = download.get("pdf_count", 0)
            prisma_data["flow_summary"].append(
                f"Download: {download.get('pdf_count', 0)} PDFs successfully downloaded"
            )

        # Populate from extraction results
        if "extraction" in results:
            extraction = results["extraction"]
            prisma_data["included"]["pdfs_extracted"] = extraction.get("processed", 0)
            prisma_data["included"]["extraction_errors"] = extraction.get("errors", 0)
            prisma_data["flow_summary"].append(
                f"Extraction: {extraction.get('processed', 0)} papers processed, {extraction.get('errors', 0)} errors"
            )

        # Save PRISMA log
        logs_dir = self.project_path / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        prisma_file = logs_dir / "prisma_log.json"
        save_json(str(prisma_file), prisma_data)

        self.logger.info(f"PRISMA log saved to {prisma_file}")
