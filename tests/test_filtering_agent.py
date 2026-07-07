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
                json.dumps({"id": "keep", "title": "Keep", "abstract": "Relevant", "year": 2024}) + "\n",
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
                            "user_prompt_template": "{title}\n{abstract}",
                        },
                        "auto_approve": True,
                    }
                )

        self.assertNotIn("json", (seen_prompts[0][0] + seen_prompts[0][1]).lower())

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


if __name__ == "__main__":
    unittest.main()
