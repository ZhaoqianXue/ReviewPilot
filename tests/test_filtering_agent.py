import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.filtering_agent import FilteringAgent
from reviewpilot_core.project_store import read_json, read_jsonl


class FilteringAgentTests(unittest.TestCase):
    def test_relevance_prompt_can_remain_plain_boolean_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            collected_dir = project_dir / "collected"
            collected_dir.mkdir(parents=True)
            (collected_dir / "openalex.jsonl").write_text(
                json.dumps({"id": "keep", "title": "Keep {abstract}", "abstract": "Relevant", "year": 2024}) + "\n",
                encoding="utf-8",
            )
            (collected_dir / "summary.json").write_text(
                json.dumps({"platform_stats": {"openalex": 1}}),
                encoding="utf-8",
            )
            seen_prompts = []

            def fake_query_llm(text_prompt, system_prompt, model, provider):
                seen_prompts.append((text_prompt, system_prompt))
                return ("true", {"total_tokens": 1})

            with patch("utils.llm.query_llm", side_effect=fake_query_llm):
                FilteringAgent(project_dir).run(
                    {
                        "collected_folder": str(collected_dir),
                        "relevance_prompt": {
                            "system_prompt": "Return true or false.",
                            "user_prompt_template": '{title}\n{abstract}\nSCOPE DATA: {"topic": "robots"}',
                        },
                        "auto_approve": True,
                    }
                )

        self.assertIn("Keep {abstract}\nRelevant", seen_prompts[0][0])
        self.assertIn('{"topic": "robots"}', seen_prompts[0][0])

    def test_run_writes_stage_contract_artifacts_for_relevance_screening(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            collected_dir = project_dir / "collected"
            collected_dir.mkdir(parents=True)
            (collected_dir / "openalex.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"id": "keep", "title": "Keep this paper", "abstract": "A relevant abstract", "year": 2024}),
                        json.dumps({"id": "drop", "title": "Drop this paper", "abstract": "An unrelated abstract", "year": 2024}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (collected_dir / "summary.json").write_text(
                json.dumps({"platform_stats": {"openalex": 2}}),
                encoding="utf-8",
            )

            def fake_query_llm(text_prompt, system_prompt, model, provider):
                return ("false" if "Drop this paper" in text_prompt else "true", {"total_tokens": 1})

            with patch("utils.llm.query_llm", side_effect=fake_query_llm):
                result = FilteringAgent(project_dir).run(
                    {
                        "collected_folder": str(collected_dir),
                        "relevance_prompt": {
                            "system_prompt": "Return true or false.",
                            "user_prompt_template": "{title}\n{abstract}",
                        },
                        "auto_approve": True,
                    }
                )

            filtered_dir = project_dir / "filtered"
            included = read_jsonl(filtered_dir / "included_papers.jsonl")
            excluded = read_jsonl(filtered_dir / "excluded_papers.jsonl")
            screening_stats = read_json(filtered_dir / "screening_stats.json")

        self.assertEqual(result["filtered_count"], 1)
        self.assertEqual(result["included_count"], 1)
        self.assertEqual(result["excluded_count"], 1)
        self.assertEqual([paper["id"] for paper in included], ["keep"])
        self.assertEqual([paper["id"] for paper in excluded], ["drop"])
        self.assertEqual(screening_stats["total_screened"], 2)
        self.assertEqual(screening_stats["included_count"], 1)
        self.assertEqual(screening_stats["excluded_count"], 1)

    def test_malformed_relevance_response_retains_plausibly_eligible_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            collected_dir = project_dir / "collected"
            collected_dir.mkdir(parents=True)
            long_abstract = "Relevant evidence. " * 100
            (collected_dir / "openalex.jsonl").write_text(
                json.dumps({"id": "uncertain", "title": "Potentially eligible", "abstract": long_abstract, "year": 2024}) + "\n",
                encoding="utf-8",
            )
            (collected_dir / "summary.json").write_text(json.dumps({"platform_stats": {"openalex": 1}}), encoding="utf-8")
            seen = {}

            def fake_query_llm(text_prompt, system_prompt, model, provider):
                seen["prompt"] = text_prompt
                return ("The evidence is uncertain", {"total_tokens": 1})

            result = FilteringAgent(project_dir, llm_query=fake_query_llm).run(
                {
                    "collected_folder": str(collected_dir),
                    "relevance_prompt": {"system_prompt": "Screen conservatively.", "user_prompt_template": "{title}\n{abstract}"},
                    "auto_approve": True,
                }
            )
            rows = read_jsonl(project_dir / "filtered" / "included_papers.jsonl")

        self.assertEqual(result["included_count"], 1)
        self.assertIsNone(rows[0]["is_relevant"])
        self.assertIn("Abstract truncated by ReviewPilot", seen["prompt"])

    def test_collection_rerun_excludes_removed_source_artifacts_from_screening(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            collected_dir = project_dir / "collected"
            collected_dir.mkdir(parents=True)
            (collected_dir / "openalex.jsonl").write_text(
                json.dumps({"id": "current", "title": "Current source", "year": 2024}) + "\n",
                encoding="utf-8",
            )
            (collected_dir / "pubmed.jsonl").write_text(
                json.dumps({"id": "ghost", "title": "Removed source", "year": 2023}) + "\n",
                encoding="utf-8",
            )
            (collected_dir / "summary.json").write_text(
                json.dumps({"platform_stats": {"openalex": 1}}),
                encoding="utf-8",
            )

            result = FilteringAgent(project_dir).run(
                {
                    "collected_folder": str(collected_dir),
                    "relevance_prompt": {},
                    "auto_approve": True,
                }
            )
            included = read_jsonl(project_dir / "filtered" / "included_papers.jsonl")

        self.assertEqual(result["filtered_count"], 1)
        self.assertEqual([paper["id"] for paper in included], ["current"])

    def test_failed_source_with_zero_current_count_does_not_reuse_old_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            collected_dir = project_dir / "collected"
            collected_dir.mkdir(parents=True)
            (collected_dir / "pubmed.jsonl").write_text(
                json.dumps({"id": "ghost", "title": "Old failed-source row", "year": 2023}) + "\n",
                encoding="utf-8",
            )
            (collected_dir / "summary.json").write_text(
                json.dumps({"platform_stats": {"pubmed": 0}, "platform_errors": {"pubmed": "503"}}),
                encoding="utf-8",
            )

            result = FilteringAgent(project_dir).run(
                {"collected_folder": str(collected_dir), "relevance_prompt": {}, "auto_approve": True}
            )

        self.assertEqual(result["filtered_count"], 0)

    def test_current_source_row_count_mismatch_fails_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            collected_dir = project_dir / "collected"
            collected_dir.mkdir(parents=True)
            (collected_dir / "openalex.jsonl").write_text(
                json.dumps({"id": "old", "title": "Only one row", "year": 2023}) + "\n",
                encoding="utf-8",
            )
            (collected_dir / "summary.json").write_text(
                json.dumps({"platform_stats": {"openalex": 2}}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "does not match current collection summary"):
                FilteringAgent(project_dir).run(
                    {"collected_folder": str(collected_dir), "relevance_prompt": {}, "auto_approve": True}
                )


if __name__ == "__main__":
    unittest.main()
