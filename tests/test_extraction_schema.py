import json
import tempfile
import unittest
from pathlib import Path

from reviewpilot_core.extraction_schema import (
    add_schema_field,
    finalize_schema,
    is_schema_finalized,
    load_schema_draft,
    modify_schema_field,
    remove_schema_field,
    save_schema_draft,
)


class ExtractionSchemaTests(unittest.TestCase):
    def test_legacy_schema_with_completed_extraction_is_finalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            extraction_dir = project_dir / "extraction"
            extraction_dir.mkdir(parents=True)
            (extraction_dir / "extraction_schema.json").write_text(
                json.dumps({"fields": [{"name": "methods", "description": "Methods"}]}),
                encoding="utf-8",
            )
            (extraction_dir / "extraction_results.jsonl").write_text(
                json.dumps({"paper_id": "P1", "methods": "Survey"}) + "\n",
                encoding="utf-8",
            )

            finalized = is_schema_finalized(project_dir)

        self.assertTrue(finalized)

    def test_explicit_draft_takes_precedence_over_legacy_extraction_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            save_schema_draft(project_dir, {"fields": [{"name": "methods", "description": "Methods"}]})
            (project_dir / "extraction" / "extraction_results.jsonl").write_text(
                json.dumps({"paper_id": "P1", "methods": "Survey"}) + "\n",
                encoding="utf-8",
            )

            finalized = is_schema_finalized(project_dir)

        self.assertFalse(finalized)

    def test_malformed_or_blank_legacy_results_do_not_finalize_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            extraction_dir = project_dir / "extraction"
            extraction_dir.mkdir(parents=True)
            (extraction_dir / "extraction_schema.json").write_text(
                json.dumps({"fields": [{"name": "methods", "description": "Methods"}]}),
                encoding="utf-8",
            )
            results = extraction_dir / "extraction_results.jsonl"
            results.write_text("  \nnot-json\n", encoding="utf-8")

            finalized = is_schema_finalized(project_dir)

        self.assertFalse(finalized)

    def test_remove_requires_existing_field_and_rename_rejects_duplicate_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            save_schema_draft(
                project_dir,
                {"fields": [{"name": "methods"}, {"name": "findings"}]},
            )

            with self.assertRaisesRegex(ValueError, "field not found"):
                remove_schema_field(project_dir, "missing")
            with self.assertRaisesRegex(ValueError, "already exists"):
                modify_schema_field(project_dir, "methods", {"new_name": "findings"})

            names = [field["name"] for field in load_schema_draft(project_dir)["fields"]]

        self.assertEqual(names, ["methods", "findings"])

    def test_add_remove_and_modify_schema_fields_update_draft_without_finalizing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            save_schema_draft(
                project_dir,
                {
                    "fields": [
                        {"name": "methods", "type": "Text", "description": "Methods", "required": False, "example": "RCT"},
                        {"name": "limitations", "type": "Long text", "description": "Limits", "required": False, "example": "Small sample"},
                    ]
                },
            )

            add_schema_field(
                project_dir,
                {"name": "sample_size", "description": "Number of participants or records", "example": "128"},
            )
            modify_schema_field(
                project_dir,
                "methods",
                {"new_name": "study_methods", "new_description": "Study design and methods", "new_required": True},
            )
            remove_schema_field(project_dir, "limitations")
            draft = load_schema_draft(project_dir)

        self.assertFalse(is_schema_finalized(project_dir))
        self.assertEqual([field["name"] for field in draft["fields"]], ["study_methods", "sample_size"])
        self.assertEqual(draft["fields"][0]["description"], "Study design and methods")
        self.assertTrue(draft["fields"][0]["required"])
        self.assertEqual(draft["fields"][1]["type"], "Text")

    def test_finalize_schema_writes_final_schema_prompt_and_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            (project_dir / "search_conditions.json").write_text(
                json.dumps({"project_name": "Demo", "primary_topic": "LLMs", "domain": "medicine"}),
                encoding="utf-8",
            )
            save_schema_draft(
                project_dir,
                {
                    "fields": [
                        {
                            "name": "key_findings",
                            "type": "Long text",
                            "description": "Main findings",
                            "required": True,
                            "example": "Improved triage accuracy",
                        }
                    ]
                },
            )

            result = finalize_schema(project_dir)
            final_schema = json.loads((project_dir / "extraction" / "extraction_schema.json").read_text(encoding="utf-8"))
            final_prompt = json.loads((project_dir / "extraction" / "extraction_prompt.json").read_text(encoding="utf-8"))
            finalized = is_schema_finalized(project_dir)

        self.assertEqual(result["status"], "schema_finalized")
        self.assertTrue(finalized)
        self.assertEqual(final_schema["fields"][0]["name"], "key_findings")
        self.assertIn("key_findings", final_prompt["extraction_prompt"])
        self.assertIn("Main findings", final_prompt["extraction_prompt"])


if __name__ == "__main__":
    unittest.main()
