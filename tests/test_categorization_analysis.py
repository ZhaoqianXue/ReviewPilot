import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reviewpilot_core.atomic_files import atomic_write_json, atomic_write_jsonl
from reviewpilot_core.categorization_analysis import CategorizationAnalysis, _assign_category, _recommended_category_field
from reviewpilot_core.project_store import read_jsonl


class CategorizationAnalysisTests(unittest.TestCase):
    def test_formal_categorization_outputs_use_shared_atomic_writers(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            analysis = CategorizationAnalysis(project_dir)
            rows = [{"title": "Paper A", "category": "Clinical"}]

            with (
                patch("reviewpilot_core.categorization_analysis.atomic_write_jsonl", wraps=atomic_write_jsonl) as jsonl_writer,
                patch("reviewpilot_core.categorization_analysis.atomic_write_json", wraps=atomic_write_json) as json_writer,
            ):
                analysis._write_outputs("key_findings", ["Clinical"], {}, rows, mode="single")
                analysis._write_suggestions("key_findings", "single", ["Clinical"], {}, ["Finding"])

            self.assertEqual(
                [call.args[0] for call in jsonl_writer.call_args_list],
                [project_dir / "categorization" / "categorized_results.jsonl"],
            )
            self.assertEqual(
                {call.args[0] for call in json_writer.call_args_list},
                {
                    project_dir / "categorization" / "categorization_mapping.json",
                    project_dir / "categorization" / "suggested_categories.json",
                },
            )

    def test_recommended_category_field_ignores_metadata_rows(self):
        field = _recommended_category_field(
            [
                {"row_number": 1, "extraction_status": "success", "key_findings": "clinical support"},
                {"row_number": 2, "extraction_status": "success", "key_findings": "patient communication"},
            ]
        )

        self.assertEqual(field, "key_findings")

    def test_run_skips_failed_extraction_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            extraction_dir = project_dir / "extraction"
            extraction_dir.mkdir(parents=True)
            (extraction_dir / "extraction_results.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"title": "Success", "extraction_status": "success", "key_findings": "clinical support"}),
                        json.dumps({"title": "Failed", "extraction_status": "error", "error_message": "bad pdf"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            def fake_llm_query(*, text_prompt, system_prompt, **kwargs):
                if "Create 3-8 meaningful categories" in text_prompt:
                    return (
                        json.dumps(
                            {
                                "field": "key_findings",
                                "categories": ["Clinical support"],
                                "category_descriptions": {"Clinical support": "Clinical support use cases"},
                            }
                        ),
                        {},
                    )
                return ("Clinical support", {})

            result = CategorizationAnalysis(project_dir, llm_query=fake_llm_query).run()
            rows = read_jsonl(project_dir / "categorization" / "categorized_results.jsonl")

        self.assertEqual(result["rows"], 1)
        self.assertEqual([row["title"] for row in rows], ["Success"])

    def test_run_writes_only_populated_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            extraction_dir = project_dir / "extraction"
            extraction_dir.mkdir(parents=True)
            (extraction_dir / "extraction_results.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"title": "Paper A", "extraction_status": "success", "key_findings": "clinical support"}),
                        json.dumps({"title": "Paper B", "extraction_status": "success", "key_findings": "clinical support"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            def fake_llm_query(*, text_prompt, system_prompt, **kwargs):
                if "Create 3-8 meaningful categories" in text_prompt:
                    return (
                        json.dumps(
                            {
                                "field": "key_findings",
                                "categories": ["Clinical support", "Unused category"],
                                "category_descriptions": {"Clinical support": "Clinical use", "Unused category": "No papers"},
                            }
                        ),
                        {},
                    )
                return ("Clinical support", {})

            result = CategorizationAnalysis(project_dir, llm_query=fake_llm_query).run()
            mapping = json.loads((project_dir / "categorization" / "categorization_mapping.json").read_text(encoding="utf-8"))

        self.assertEqual(result["categories"], 1)
        self.assertEqual(mapping["categories"], ["Clinical support"])
        self.assertEqual(mapping["category_descriptions"], {"Clinical support": "Clinical use"})

    def test_suggest_categories_writes_reviewable_draft_without_finalizing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            extraction_dir = project_dir / "extraction"
            extraction_dir.mkdir(parents=True)
            (extraction_dir / "extraction_results.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"title": "Paper A", "extraction_status": "success", "methods": "Interview study"}),
                        json.dumps({"title": "Paper B", "extraction_status": "success", "methods": "Benchmark evaluation"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            def fake_llm_query(*, text_prompt, system_prompt, **kwargs):
                self.assertIn('Analyze these values from the "methods" field', text_prompt)
                self.assertIn("at most 2 broad, reusable categories", text_prompt)
                self.assertIn("materially fewer categories than papers", text_prompt)
                return (
                    json.dumps(
                        {
                            "categories": ["Qualitative studies", "Benchmark studies"],
                            "category_descriptions": {
                                "Qualitative studies": "Interview or fieldwork studies",
                                "Benchmark studies": "Evaluation benchmark papers",
                            },
                        }
                    ),
                    {},
                )

            result = CategorizationAnalysis(project_dir, llm_query=fake_llm_query).suggest_categories(
                {"field": "methods", "mode": "multiple"}
            )
            suggestions = json.loads((project_dir / "categorization" / "suggested_categories.json").read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "categories_suggested")
        self.assertEqual(result["field"], "methods")
        self.assertEqual(result["mode"], "multiple")
        self.assertEqual(result["categories"], 2)
        self.assertFalse((project_dir / "categorization" / "categorization_mapping.json").exists())
        self.assertEqual(suggestions["categories"], ["Qualitative studies", "Benchmark studies"])

    def test_suggestion_prompt_prevents_one_category_per_paper_for_small_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            extraction_dir = project_dir / "extraction"
            extraction_dir.mkdir(parents=True)
            (extraction_dir / "extraction_results.jsonl").write_text(
                "".join(json.dumps({"title": f"Paper {index}", "methods": f"Method {index}"}) + "\n" for index in range(7)),
                encoding="utf-8",
            )

            def fake_llm_query(*, text_prompt, system_prompt, **kwargs):
                self.assertIn("at most 4 broad, reusable categories", text_prompt)
                self.assertIn("do not create paper-specific categories", text_prompt)
                return json.dumps({"categories": ["A", "B", "C", "D"]}), {}

            result = CategorizationAnalysis(project_dir, llm_query=fake_llm_query).suggest_categories(
                {"field": "methods", "mode": "multiple"}
            )

        self.assertEqual(result["categories"], 4)

    def test_run_applies_user_confirmed_categories_and_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "demo"
            extraction_dir = project_dir / "extraction"
            extraction_dir.mkdir(parents=True)
            (extraction_dir / "extraction_results.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"title": "Paper A", "extraction_status": "success", "methods": "Interview study"}),
                        json.dumps({"title": "Paper B", "extraction_status": "success", "methods": "Benchmark evaluation"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            def fake_llm_query(*, text_prompt, system_prompt, **kwargs):
                if "Paper A" in text_prompt:
                    return ("Qualitative studies", {})
                return ("Benchmark studies", {})

            result = CategorizationAnalysis(project_dir, llm_query=fake_llm_query).run(
                {
                    "field": "methods",
                    "mode": "single",
                    "categories": ["Qualitative studies", "Benchmark studies"],
                    "category_descriptions": {"Qualitative studies": "Interview studies"},
                }
            )
            mapping = json.loads((project_dir / "categorization" / "categorization_mapping.json").read_text(encoding="utf-8"))
            rows = read_jsonl(project_dir / "categorization" / "categorized_results.jsonl")

        self.assertEqual(result["status"], "categorization_done")
        self.assertEqual(result["field"], "methods")
        self.assertEqual(result["mode"], "single")
        self.assertEqual(mapping["field"], "methods")
        self.assertEqual(mapping["mode"], "single")
        self.assertEqual(mapping["categories"], ["Qualitative studies", "Benchmark studies"])
        self.assertEqual(rows[0]["methods_category"], "Qualitative studies")
        self.assertEqual(rows[1]["methods_category"], "Benchmark studies")

    def test_assign_category_does_not_force_json_mode_for_plain_category(self):
        seen = []

        def fake_llm_query(*, text_prompt, system_prompt, **kwargs):
            seen.append((text_prompt, system_prompt))
            return ("Clinical Decision Support", {})

        category = _assign_category(
            {"title": "Paper"},
            "key_findings",
            "Decision support in clinics",
            ["Clinical Decision Support", "Evaluation"],
            fake_llm_query,
        )

        self.assertEqual(category, "Clinical Decision Support")
        self.assertNotIn("json", (seen[0][0] + seen[0][1]).lower())

    def test_assign_category_accepts_json_object_response(self):
        def fake_llm_query(*, text_prompt, system_prompt, **kwargs):
            return (json.dumps({"category": "Study methods and evaluation design"}), {})

        category = _assign_category(
            {"title": "Paper"},
            "methods",
            "Mixed methods evaluation",
            ["Study methods and evaluation design", "User experience outcomes"],
            fake_llm_query,
        )

        self.assertEqual(category, "Study methods and evaluation design")

    def test_assign_category_normalizes_case_and_spacing_to_allowed_category(self):
        def fake_llm_query(*, text_prompt, system_prompt, **kwargs):
            return ("  user experience outcomes  ", {})

        category = _assign_category(
            {"title": "Paper"},
            "key_findings",
            "Usability outcomes",
            ["Study methods and evaluation design", "User Experience Outcomes"],
            fake_llm_query,
        )

        self.assertEqual(category, "User Experience Outcomes")


if __name__ == "__main__":
    unittest.main()
