"""Starting a project from the researcher's own PDFs and going straight to Information Extraction."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pymupdf
from starlette.testclient import TestClient

import web_app
from reviewpilot_core import paper_import
from reviewpilot_core.project_store import read_json, read_jsonl
from reviewpilot_core.state_projection import build_rp_data
from reviewpilot_core.task_runner import TaskRunner
from reviewpilot_core.workflow_state import load_workflow_state


def make_pdf(title: str, *lines: str, metadata_title: str = "", pages_of_text: int = 1) -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((54, 90), title, fontsize=18)
    y = 130
    for line in lines:
        page.insert_text((54, y), line, fontsize=10)
        y += 14
    for _ in range(pages_of_text):
        page.insert_text((54, y), "Methods. We recruited participants and measured outcomes over twelve weeks in a trial. " * 1, fontsize=9)
        y += 14
        page.insert_text((54, y), "Results. The intervention improved adherence compared with usual care in every subgroup.", fontsize=9)
        y += 14
        page.insert_text((54, y), "Discussion. These findings support wider use of structured counselling in primary care.", fontsize=9)
        y += 14
    if metadata_title:
        document.set_metadata({"title": metadata_title})
    data = document.tobytes()
    document.close()
    return data


def scanned_pdf() -> bytes:
    document = pymupdf.open()
    document.new_page()
    data = document.tobytes()
    document.close()
    return data


NO_LOOKUP = mock.patch.object(paper_import, "crossref_lookup", lambda doi: {})


class DescribePdfTests(unittest.TestCase):
    def test_title_comes_from_the_largest_text_on_the_first_page(self):
        meta = paper_import.describe_pdf(make_pdf("A Trial of Structured Counselling", "Journal of Examples"), "x.pdf")
        self.assertEqual(meta["title"], "A Trial of Structured Counselling")
        self.assertEqual(meta["titleSource"], "first_page")
        self.assertTrue(meta["hasText"])

    def test_usable_metadata_title_wins_and_tool_names_are_ignored(self):
        good = paper_import.describe_pdf(make_pdf("Heading", metadata_title="Counselling Outcomes in Primary Care"), "x.pdf")
        self.assertEqual((good["title"], good["titleSource"]), ("Counselling Outcomes in Primary Care", "pdf_metadata"))
        word = paper_import.describe_pdf(make_pdf("Real Title of the Study", metadata_title="Microsoft Word - draft3.docx"), "x.pdf")
        self.assertEqual(word["title"], "Real Title of the Study")

    def test_doi_is_found_on_the_first_pages(self):
        meta = paper_import.describe_pdf(make_pdf("Some Study Title Here", "https://doi.org/10.1234/abc.5678."), "x.pdf")
        self.assertEqual(meta["doi"], "10.1234/abc.5678")

    def test_scanned_pdf_falls_back_to_the_file_name_and_is_flagged(self):
        meta = paper_import.describe_pdf(scanned_pdf(), "smith_2024_counselling-trial.pdf")
        self.assertEqual(meta["title"], "smith 2024 counselling trial")
        self.assertFalse(meta["hasText"])

    def test_crossref_details_replace_local_guesses_when_the_title_is_on_the_page(self):
        merged = paper_import._merge_crossref({"title": "guess", "titleSource": "first_page", "year": None, "authors": [], "doi": "10.1/x"},
                                              {"title": "Published Title of Study", "year": 2025, "authors": ["A B"], "journal": "J", "abstract": ""},
                                              "Journal header\nPublished Title of\nStudy\nAuthors")
        self.assertEqual((merged["title"], merged["titleSource"], merged["year"]), ("Published Title of Study", "crossref", 2025))

    def test_a_doi_of_another_paper_is_dropped_instead_of_renaming_the_pdf(self):
        local = {"title": "Our Counselling Trial", "titleSource": "first_page", "doi": "10.1/cited", "authors": []}
        merged = paper_import._merge_crossref(local, {"title": "A Completely Different Cited Work", "year": 2001},
                                              "Our Counselling Trial\nReferences 10.1/cited")
        self.assertEqual((merged["title"], merged["doi"]), ("Our Counselling Trial", ""))

    def test_doi_cleanup_keeps_balanced_brackets(self):
        self.assertEqual(paper_import._clean_doi("10.1016/S0140-6736(20)30183-5."), "10.1016/S0140-6736(20)30183-5")
        self.assertEqual(paper_import._clean_doi("10.1000/abc(2024)"), "10.1000/abc(2024)")
        self.assertEqual(paper_import._clean_doi("10.1000/abc)."), "10.1000/abc")


class ImportFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_root, self.old_runner = web_app.OUTPUT_ROOT, web_app.task_runner
        web_app.OUTPUT_ROOT = self.root
        web_app.task_runner = TaskRunner()
        self.client = TestClient(web_app.create_app())
        NO_LOOKUP.start()

    def tearDown(self):
        NO_LOOKUP.stop()
        web_app.task_runner, web_app.OUTPUT_ROOT = self.old_runner, self.old_root
        self.tmp.cleanup()

    def upload(self, import_id, name, content):
        return self.client.post(f"/imports/{import_id}/files?name={name}", content=content,
                                headers={"Content-Type": "application/pdf"})

    def create(self, files):
        import_id = self.client.post("/imports").json()["importId"]
        for name, content in files:
            self.assertEqual(self.upload(import_id, name, content).status_code, 200)
        response = self.client.post(f"/imports/{import_id}/finish",
                                    json={"project_name": "My Papers", "description": "How is counselling evaluated?"})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def test_upload_checks_files_and_skips_duplicates(self):
        import_id = self.client.post("/imports").json()["importId"]
        content = make_pdf("A Trial of Structured Counselling")
        self.assertEqual(self.upload(import_id, "a.pdf", content).json()["file"]["title"], "A Trial of Structured Counselling")
        duplicate = self.upload(import_id, "copy.pdf", content).json()
        self.assertEqual(duplicate["file"]["duplicateOf"], "a.pdf")
        self.assertEqual(len(duplicate["files"]), 1)
        refused = self.upload(import_id, "notes.pdf", b"plain text")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("not a PDF", refused.json()["detail"])
        file_id = duplicate["files"][0]["id"]
        self.assertEqual(self.client.delete(f"/imports/{import_id}/files/{file_id}").json()["files"], [])

    def test_unknown_or_malformed_upload_ids_are_refused(self):
        self.assertEqual(self.upload("../../etc", "a.pdf", make_pdf("Title of a Study")).status_code, 404)
        expired = self.upload("0" * 24, "a.pdf", make_pdf("Title of a Study")).json()
        self.assertEqual((expired["code"], "expired" in expired["detail"]), ("import_expired", True))

    def test_finish_needs_files_name_and_question(self):
        import_id = self.client.post("/imports").json()["importId"]
        empty = self.client.post(f"/imports/{import_id}/finish", json={"project_name": "X", "description": "Q"})
        self.assertIn("at least one PDF", empty.json()["detail"])
        self.upload(import_id, "a.pdf", make_pdf("A Trial of Structured Counselling"))
        unnamed = self.client.post(f"/imports/{import_id}/finish", json={"project_name": "", "description": "Q"})
        self.assertEqual(unnamed.status_code, 400)
        self.assertEqual([p.name for p in self.root.iterdir() if not p.name.startswith(".")], [])

    def test_project_opens_at_extraction_with_the_papers_included(self):
        project_id = self.create([("a.pdf", make_pdf("A Trial of Structured Counselling")),
                                  ("b.pdf", make_pdf("Counselling by Telephone for Smokers"))])
        project = self.root / project_id
        stages = load_workflow_state(project)["stages"]
        self.assertEqual([stages[name]["status"] for name in ("collection", "screening", "retrieval", "extraction")],
                         ["completed", "completed", "completed", "ready"])
        included = read_jsonl(project / "filtered/included_papers.jsonl")
        self.assertEqual([row["title"] for row in included],
                         ["A Trial of Structured Counselling", "Counselling by Telephone for Smokers"])
        for row in included:
            self.assertTrue(Path(row["pdf_path"]).is_file())
            self.assertTrue(Path(row["pdf_path"]).resolve().is_relative_to((project / "pdfs").resolve()))
        self.assertEqual(read_json(project / "pdfs/download_report.json")["provided_by_user"], 2)
        self.assertEqual(list((self.root / ".imports").glob("*/*.pdf")), [])  # staged copies are gone; the manifest remains

        state = build_rp_data(self.root, project_id)
        self.assertEqual(state["paperImport"]["papers"], 2)
        self.assertEqual([step["status"] for step in state["steps"]], ["done", "done", "done", "active", "todo"])
        self.assertEqual(state["platforms"], [["Your PDFs", 2]])
        self.assertIsNone(state["prismaFlow"])
        self.assertEqual(state["fields"], [])

    def test_search_screening_and_download_cannot_run_on_an_imported_project(self):
        project_id = self.create([("a.pdf", make_pdf("A Trial of Structured Counselling"))])
        for action in ("collect", "screen", "download-pdfs", "finalize-criteria"):
            response = self.client.post(f"/projects/{project_id}/actions/{action}", json={})
            self.assertEqual(response.status_code, 400, action)
            self.assertIn("own PDFs", response.json()["detail"])
        setup = self.client.put(f"/projects/{project_id}/setup", json={"project_name": "X", "description": "Y"})
        self.assertEqual(setup.status_code, 400)
        chat = self.client.post(f"/projects/{project_id}/chat", json={"message": "add arXiv", "step": "search"})
        self.assertEqual(chat.status_code, 400)

    def test_schema_generation_is_allowed(self):
        project_id = self.create([("a.pdf", make_pdf("A Trial of Structured Counselling"))])
        with mock.patch.object(web_app.task_runner, "submit", return_value="task-1") as submit:
            response = self.client.post(f"/projects/{project_id}/actions/generate-schema", json={})
        self.assertEqual(response.status_code, 200, response.text)
        submit.assert_called_once()

    def test_finish_is_idempotent_and_closes_the_upload(self):
        import_id = self.client.post("/imports").json()["importId"]
        self.upload(import_id, "a.pdf", make_pdf("A Trial of Structured Counselling"))
        body = {"project_name": "My Papers", "description": "How is counselling evaluated?"}
        first = self.client.post(f"/imports/{import_id}/finish", json=body).json()
        second = self.client.post(f"/imports/{import_id}/finish", json=body).json()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual([p.name for p in self.root.iterdir() if not p.name.startswith(".")], [first["id"]])
        late = self.upload(import_id, "b.pdf", make_pdf("Counselling by Telephone for Smokers"))
        self.assertEqual(late.status_code, 400)
        self.assertIn("already used", late.json()["detail"])
        self.assertEqual(late.json()["code"], "import_used")

    def test_concurrent_uploads_removals_and_finishes_keep_one_consistent_project(self):
        from concurrent.futures import ThreadPoolExecutor
        import_id = paper_import.begin_import(self.root)["importId"]
        pdfs = [make_pdf(f"Counselling Study Number {n} of the Series") for n in range(12)]
        with ThreadPoolExecutor(8) as pool:
            added = list(pool.map(lambda item: paper_import.add_file(self.root, import_id, f"p{item[0]}.pdf", item[1]), enumerate(pdfs)))
        self.assertEqual(len(paper_import.staged_files(self.root, import_id)), 12)
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda entry: paper_import.remove_file(self.root, import_id, entry["id"]), added[:4]))
        staged = paper_import.staged_files(self.root, import_id)
        self.assertEqual(sorted(entry["id"] for entry in staged), sorted(entry["id"] for entry in added[4:]))
        body = {"project_name": "Race", "description": "Q?"}
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda _: web_app.create_imported_project(self.root, import_id, body), range(4)))
        self.assertEqual(len({result["id"] for result in results}), 1)
        project = self.root / results[0]["id"]
        self.assertEqual(len(read_jsonl(project / "filtered/included_papers.jsonl")), 8)
        self.assertEqual(len(list((project / "pdfs").glob("*.pdf"))), 8)

    def test_oversized_upload_is_refused_without_reading_it(self):
        import_id = self.client.post("/imports").json()["importId"]
        with mock.patch.object(paper_import, "MAX_PDF_BYTES", 1000):
            response = self.upload(import_id, "big.pdf", b"%PDF" + b"x" * 5000)
        self.assertEqual(response.status_code, 400)
        self.assertIn("larger than", response.json()["detail"])

    def test_imported_project_offers_no_search_or_criteria_for_reuse(self):
        from reviewpilot_core.configuration_reuse import configuration_options, preview_configuration
        imported = self.create([("a.pdf", make_pdf("A Trial of Structured Counselling"))])
        other = self.create([("b.pdf", make_pdf("Counselling by Telephone for Smokers"))])
        kinds = {item["kind"] for item in configuration_options(self.root, other) if item["source_project_id"] == imported}
        self.assertNotIn("search_setup", kinds)
        self.assertFalse({"search_setup", "screening_profile"} & {item["kind"] for item in configuration_options(self.root, imported)})
        with self.assertRaises(ValueError):
            preview_configuration(self.root, other, {"source_project_id": imported, "kind": "search_setup"})

    def test_imported_project_is_listed_and_the_staging_area_is_not(self):
        project_id = self.create([("a.pdf", make_pdf("A Trial of Structured Counselling"))])
        self.client.post("/imports")
        from reviewpilot_core.state_projection import list_projects
        self.assertEqual([item["id"] for item in list_projects(self.root)], [project_id])


if __name__ == "__main__":
    unittest.main()
