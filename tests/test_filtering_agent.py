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

    def test_exact_title_deduplication_merges_the_same_paper_and_keeps_different_ones(self):
        papers = [
            {"id": "123", "source": "pubmed", "doi": "10.1000/ABC", "title": "Lassa fever transmission in Nigeria"},
            {"id": "W9", "source": "openalex", "doi": "https://doi.org/10.1000/abc", "title": "Lassa Fever Transmission in Nigeria."},
            {"id": "W10", "source": "openalex", "doi": "", "title": "Lassa fever transmission in Nigeria"},
            {"id": "456", "source": "pubmed", "doi": "", "title": "Lassa fever transmission in Nigeria"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            agent = FilteringAgent(Path(tmp))
            kept = agent._deduplicate_exact(papers)

        self.assertEqual([paper["id"] for paper in kept], ["123", "456"])
        self.assertEqual([(row["id"], row["removal"]["representative"]["id"]) for row in agent.removed_records], [("W9", "123"), ("W10", "123")])

    def test_near_duplicate_detection_keeps_records_whose_doi_or_pmid_differ(self):
        authors = ["Ada Okafor", "Ben Sesay", "Chi Ilori", "Dan Jalloh", "Eve Kamara", "Fay Conteh"]
        first = {"id": "W1", "source": "openalex", "title": "Seroepidemiology of Lassa virus in pregnant women in Sierra Leone", "authors": authors}
        second = {"id": "W2", "source": "openalex", "title": "Transplacental transfer of Lassa antibodies in pregnant women in Sierra Leone", "authors": authors}
        with tempfile.TemporaryDirectory() as tmp:
            agent = FilteringAgent(Path(tmp))
            without_ids, _ = agent._deduplicate_similarity([dict(first), dict(second)])
            different_dois, _ = agent._deduplicate_similarity([{**first, "doi": "10.1/a"}, {**second, "doi": "10.1/b"}])
            different_pmids, _ = agent._deduplicate_similarity([{**first, "source": "pubmed", "id": "111"}, {**second, "source": "pubmed", "id": "222"}])
            same_doi, _ = agent._deduplicate_similarity([{**first, "doi": "10.1/A"}, {**second, "doi": "doi: 10.1/a"}])

        self.assertEqual([paper["id"] for paper in without_ids], ["W1"])
        self.assertEqual([paper["id"] for paper in different_dois], ["W1", "W2"])
        self.assertEqual([paper["id"] for paper in different_pmids], ["111", "222"])
        self.assertEqual([paper["id"] for paper in same_doi], ["W1"])

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
