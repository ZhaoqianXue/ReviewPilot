import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reviewpilot_core.extraction_preview import (
    project_preview_projection,
    run_project_preview,
    schema_revision,
    write_preview_cache,
)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class ExtractionPreviewProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "demo"
        self.schema = {
            "fields": [
                {"name": "methods", "type": "Text", "description": "Methods", "required": True},
                {"name": "key_findings", "type": "Long text", "description": "Findings", "required": False},
            ]
        }
        write_json(self.project / "extraction" / "extraction_schema_draft.json", self.schema)
        write_jsonl(
            self.project / "filtered" / "included_papers.jsonl",
            [
                {"id": "p1", "title": "Paper A", "source": "pubmed", "year": 2026},
                {"id": "p2", "title": "Paper B", "source": "arxiv", "year": 2025},
            ],
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_formal_result_is_projected_in_schema_order_without_internal_metadata(self):
        write_jsonl(
            self.project / "extraction" / "extraction_results.jsonl",
            [{
                "paper_id": "p1", "title": "Paper A", "source": "pubmed",
                "pdf_file": "secret.pdf", "row_number": 1,
                "extracted_at": "2026-07-14", "extraction_model": "model",
                "extraction_cost_usd": 1.5,
                "extracted_data": {"methods": "Survey"},
                "methods": "Survey", "unknown": "hidden", "extraction_status": "success",
            }],
        )

        preview = project_preview_projection(self.project, 0)

        self.assertEqual(preview["status"], "ready")
        self.assertEqual(preview["index"], 0)
        self.assertEqual(preview["total"], 2)
        self.assertFalse(preview["canPrevious"])
        self.assertTrue(preview["canNext"])
        self.assertEqual(preview["paper"], {"id": "p1", "title": "Paper A", "ref": "pubmed · 2026"})
        self.assertEqual([field["name"] for field in preview["fields"]], ["methods", "key_findings"])
        self.assertEqual([field["value"] for field in preview["fields"]], ["Survey", "—"])
        visible = json.dumps(preview)
        for forbidden in ("pdf_file", "row_number", "extracted_at", "extraction_model", "extraction_cost_usd", "extracted_data", "unknown", "secret.pdf"):
            self.assertNotIn(forbidden, visible)

    def test_completed_result_takes_precedence_over_same_revision_preview_cache(self):
        write_preview_cache(self.project, self.schema, "p1", {"paper_id": "p1", "methods": "Cached", "extraction_status": "success"})
        write_jsonl(self.project / "extraction" / "extraction_results.jsonl", [{"paper_id": "p1", "methods": "Formal", "extraction_status": "success"}])
        self.assertEqual(project_preview_projection(self.project, 0)["fields"][0]["value"], "Formal")

    def test_stale_cache_is_ignored(self):
        write_json(
            self.project / "extraction" / "schema_preview.json",
            {"version": 1, "schema_revision": "stale", "items": {"p1": {"methods": "Stale"}}},
        )
        preview = project_preview_projection(self.project, 0)
        self.assertEqual(preview["status"], "missing")
        self.assertEqual(preview["fields"], [])

    def test_error_is_safe_and_never_exposes_paths(self):
        write_jsonl(
            self.project / "extraction" / "extraction_results.jsonl",
            [{"paper_id": "p1", "extraction_status": "error", "error_message": f"failed at {self.project}/secret.pdf"}],
        )
        preview = project_preview_projection(self.project, 0)
        self.assertEqual(preview["status"], "error")
        self.assertNotIn(str(self.project), preview["error"])
        self.assertNotIn("secret.pdf", preview["error"])

    def test_index_must_be_in_range_and_not_bool(self):
        for index in (-1, 2, True):
            with self.subTest(index=index), self.assertRaises(ValueError):
                project_preview_projection(self.project, index)

    def test_schema_revision_is_canonical(self):
        reordered = {"fields": [dict(reversed(list(field.items()))) for field in self.schema["fields"]]}
        self.assertEqual(schema_revision(self.schema), schema_revision(reordered))

    def test_run_project_preview_writes_revision_scoped_cache_without_formal_artifacts(self):
        pdf = self.project / "pdfs" / "row1_paper.pdf"
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b"%PDF-1.4\n")
        papers = [{"id": "p1", "title": "Paper A", "source": "pubmed", "pdf_path": str(pdf), "pdf_downloaded": True}]
        write_jsonl(self.project / "filtered" / "included_papers.jsonl", papers)

        result = run_project_preview(
            self.project,
            0,
            llm_query=lambda **kwargs: (json.dumps({"methods": "Survey", "key_findings": ""}), {"total_tokens": 1}),
            pdf_reader=lambda path: "paper text",
        )

        self.assertEqual(result, {"status": "preview_ready", "paper_index": 0, "total": 1})
        self.assertEqual(project_preview_projection(self.project, 0)["fields"][0]["value"], "Survey")
        self.assertFalse((self.project / "extraction" / "extraction_results.jsonl").exists())
        self.assertFalse((self.project / "extraction" / "extraction_stats.json").exists())

    def test_schema_change_during_preview_prevents_cache_publication(self):
        with patch("reviewpilot_core.extraction_preview.ExtractionAgent.extract_one") as extract:
            def mutate(**kwargs):
                changed = {"fields": [{"name": "different", "type": "Text"}]}
                write_json(self.project / "extraction" / "extraction_schema_draft.json", changed)
                return {"paper_id": "p1", "methods": "stale", "extraction_status": "success"}
            extract.side_effect = mutate
            with self.assertRaisesRegex(ValueError, "schema changed"):
                run_project_preview(self.project, 0)
        self.assertFalse((self.project / "extraction" / "schema_preview.json").exists())


if __name__ == "__main__":
    unittest.main()
