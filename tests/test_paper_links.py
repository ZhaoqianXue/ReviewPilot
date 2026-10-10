import tempfile
import unittest
from pathlib import Path

from reviewpilot_core.paper_links import paper_links
from reviewpilot_core.record_review import key
from reviewpilot_core.state_projection import _retrieved


class PaperLinksTests(unittest.TestCase):
    def test_pubmed_record_gets_doi_pubmed_and_pmc_links(self):
        links = paper_links({"source": "pubmed", "id": "42820023", "doi": "https://doi.org/10.1/ABC",
                             "pmcid": "12345", "url": "https://pubmed.ncbi.nlm.nih.gov/42820023/"})
        self.assertEqual([link["kind"] for link in links], ["doi", "pubmed", "pmc"])
        self.assertEqual(links[0]["url"], "https://doi.org/10.1/abc")
        self.assertEqual(links[2]["url"], "https://pmc.ncbi.nlm.nih.gov/articles/PMC12345/")

    def test_arxiv_page_is_kept_and_index_only_record_falls_back_to_its_page(self):
        self.assertEqual(paper_links({"source": "arxiv", "url": "http://arxiv.org/abs/2403.19876v1"}),
                         [{"kind": "publisher", "label": "arXiv page", "url": "http://arxiv.org/abs/2403.19876v1"}])
        self.assertEqual(paper_links({"source": "openalex", "url": "https://openalex.org/W1"})[0]["kind"], "record")
        self.assertEqual(paper_links({"url": "javascript:alert(1)"}), [])


class RetrievedListTests(unittest.TestCase):
    def test_every_retrieved_paper_is_listed_with_links_and_its_pdf_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "pdfs").mkdir()
            included = []
            for index in range(12):
                pdf = project / "pdfs" / f"paper{index}.pdf"
                pdf.write_bytes(b"%PDF-1.4")
                included.append({"id": str(index), "source": "arxiv", "title": f"Paper {index}", "doi": f"10.1/{index}",
                                 "pdf_downloaded": True, "pdf_path": str(pdf)})
            included.append({"id": "missing", "source": "arxiv", "title": "Not obtained", "pdf_downloaded": False})
            rows = _retrieved(project, included, {"downloaded": []})

        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[11]["pdfKey"], key(included[11]))
        self.assertEqual(rows[0]["links"][0]["url"], "https://doi.org/10.1/0")


if __name__ == "__main__":
    unittest.main()
