import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("REVIEWPILOT_PDF_OA_INDEX", "0")
# Keep the Semantic Scholar batch cache out of the project's .cache during tests.
os.environ.setdefault("REVIEWPILOT_S2_CACHE", os.path.join(tempfile.mkdtemp(), "s2.json"))
os.environ.setdefault("REVIEWPILOT_OPENALEX_CACHE", os.path.join(tempfile.mkdtemp(), "openalex.json"))

from tests.test_fast_pdf_downloader import FakeResponse, FakeSessionByUrl, make_text_pdf_bytes  # noqa: E402


class FakeIndexSession:
    """Answers OpenAlex / idconv GETs and the Semantic Scholar batch POST."""

    def __init__(self, get_map, post_data):
        self.get_map = get_map
        self.post_data = post_data
        self.get_calls = []
        self.post_calls = []

    def get(self, url, params=None, **kwargs):
        self.get_calls.append((url, params))
        for key, response in self.get_map.items():
            if key in url and (not params or key != "api.openalex.org" or True):
                return response
        return FakeResponse(url, status_code=404)

    def post(self, url, json=None, **kwargs):
        self.post_calls.append((url, json))
        return FakeResponse(url, json_data=self.post_data)


class OAMetadataIndexTests(unittest.TestCase):
    def test_prefetch_merges_openalex_s2_and_idconv_by_identifier(self):
        from utils.oa_sources import OAMetadataIndex

        session = FakeIndexSession(
            get_map={
                "api.openalex.org": FakeResponse("https://api.openalex.org/works", json_data={"results": [{
                    "id": "https://openalex.org/W1",
                    "doi": "https://doi.org/10.1000/xyz",
                    "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/123456"},
                    "open_access": {"is_oa": True},
                    "best_oa_location": {"pdf_url": "https://repo.example.edu/bitstreams/u-1/download",
                                         "landing_page_url": "https://repo.example.edu/handle/1/2", "is_oa": True,
                                         "source": {"type": "repository"}},
                    "locations": [{"landing_page_url": "https://arxiv.org/abs/2401.01234v2", "is_oa": True}],
                }]}),
                "idconv": FakeResponse("https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/",
                                       json_data={"records": [{"requested-id": "10.1000/xyz", "pmcid": "PMC999"}]}),
            },
            post_data=[{"externalIds": {"DOI": "10.1000/XYZ", "ArXiv": "2401.01234"},
                        "openAccessPdf": {"url": "https://example.org/s2.pdf"}}],
        )
        index = OAMetadataIndex(email="me@uni.edu", session=session, sleep_func=lambda s: None)
        index.prefetch([{"doi": "10.1000/XYZ", "title": "T"}])
        meta = index.lookup({"doi": "https://doi.org/10.1000/xyz"})

        self.assertEqual(meta["pmcid"], "PMC999")
        self.assertEqual(meta["pmid"], "123456")
        self.assertEqual(meta["arxiv_id"], "2401.01234v2")
        pdfs = [loc["pdf_url"] for loc in meta["locations"] if loc["pdf_url"]]
        self.assertIn("https://repo.example.edu/bitstreams/u-1/download", pdfs)
        self.assertIn("https://example.org/s2.pdf", pdfs)
        self.assertEqual(len(session.post_calls), 1)
        # A second lookup for the same paper reads the index without new calls.
        calls = len(session.get_calls)
        index.lookup({"doi": "10.1000/xyz"})
        self.assertEqual(len(session.get_calls), calls)


class ResolverTests(unittest.TestCase):
    def test_jmir_asset_urls_for_article_and_preprint_dois(self):
        from utils.oa_sources import jmir_asset_pdf_urls

        base = "https://s3.ca-central-1.amazonaws.com/assets.jmir.org/assets/preprints/preprint-"
        self.assertEqual(jmir_asset_pdf_urls("10.2196/96587"), [f"{base}96587-accepted.pdf", f"{base}96587-submitted.pdf"])
        self.assertEqual(jmir_asset_pdf_urls("10.2196/preprints.78186")[0], f"{base}78186-submitted.pdf")
        self.assertEqual(jmir_asset_pdf_urls("10.1016/j.x.2020.01.001"), [])

    def test_repository_landing_declares_pdf_without_marker(self):
        from utils.oa_sources import repository_pdf_urls

        html = '<html><head><meta name="citation_pdf_url" content="/bitstreams/9abc/download"></head></html>'
        self.assertEqual(
            repository_pdf_urls("https://vtechworks.lib.vt.edu/items/1", html),
            ["https://vtechworks.lib.vt.edu/bitstreams/9abc/download"],
        )
        self.assertIn(
            "https://hal.science/hal-05011844/document",
            repository_pdf_urls("https://hal.science/hal-05011844", ""),
        )
        self.assertIn(
            "https://su.diva-portal.org/smash/get/diva2:2071221/FULLTEXT01.pdf",
            repository_pdf_urls("https://su.diva-portal.org/smash/record.jsf?pid=diva2:2071221", ""),
        )

    def test_ojs_viewer_rewritten_to_download(self):
        from utils.oa_sources import ojs_download_url

        self.assertEqual(
            ojs_download_url("https://jmai.amegroups.org/article/view/8029/pdf"),
            "https://jmai.amegroups.org/article/download/8029/pdf",
        )
        self.assertIsNone(ojs_download_url("https://example.org/article/123"))

    def test_zenodo_skips_software_and_prefers_main_pdf(self):
        from utils.oa_sources import zenodo_pdf_urls

        record = "https://zenodo.org/api/records/7617123"
        session = FakeSessionByUrl(get_responses={record: FakeResponse(record, json_data={
            "metadata": {"resource_type": {"type": "publication"}},
            "files": [
                {"key": "supplement.pdf", "size": 9_000_000, "links": {"self": "https://zenodo.org/s.pdf"}},
                {"key": "paper.pdf", "size": 500_000, "links": {"self": "https://zenodo.org/p.pdf"}},
            ],
        })})
        self.assertEqual(zenodo_pdf_urls(session, "7617123", {}), ["https://zenodo.org/p.pdf"])


class DownloaderRuleTests(unittest.TestCase):
    def _downloader(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        return FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

    def test_plain_403_is_access_denied_and_does_not_cool_down_domain(self):
        downloader = self._downloader()
        url = "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1111/x"
        response = FakeResponse(url, content=b"<html>Forbidden</html>", headers={"content-type": "text/html"}, status_code=403)
        self.assertEqual(downloader._classify_response_failure("unpaywall", url, response), "access_denied")
        downloader._register_domain_failure(url, "access_denied")
        self.assertIsNone(downloader._domain_cooldown_failure("https://onlinelibrary.wiley.com/doi/pdfdirect/10.1111/y"))

    def test_challenge_pages_are_classified_by_their_signatures(self):
        downloader = self._downloader()
        cases = [
            (FakeResponse("u", content=b"<title>Just a moment...</title>", status_code=403, headers={"cf-mitigated": "challenge"}),
             "https://www.medrxiv.org/content/10.1101/2024.01.01.24300001v1.full.pdf", "pdf_endpoint_cloudflare"),
            (FakeResponse("u", content=b"<title>Just a moment...</title>", status_code=403),
             "https://europepmc.org/articles/PMC1", "antibot_challenge"),
            (FakeResponse("u", content=b"<script>window.gokuProps={}</script>", status_code=202),
             "https://formative.jmir.org/2025/1/e1", "antibot_challenge"),
            (FakeResponse("u", content=b"<html><title>Client Challenge</title></html>", headers={"content-type": "text/html"}),
             "https://link.springer.com/article/10.1007/x", "antibot_challenge"),
            (FakeResponse("u", content=b"<title>Preparing to download ...</title>", headers={"content-type": "text/html"}),
             "https://pmc.ncbi.nlm.nih.gov/articles/PMC1/pdf/a.pdf", "pmc_pow_challenge"),
            # An ordinary page that merely loads a Cloudflare CDN script is not a challenge.
            (FakeResponse("u", content=b'<html><script src="https://cdnjs.cloudflare.com/x.js"></script></html>',
                          headers={"content-type": "text/html"}),
             "https://example.org/article/1", "non_pdf_html"),
        ]
        for response, url, expected in cases:
            self.assertEqual(downloader._classify_response_failure("x", url, response), expected, url)

    def test_title_gate_rejects_supplement_and_accepts_doi_anchored_pdf(self):
        downloader = self._downloader()
        title = "A New Research Model for Artificial Intelligence-Based Well-Being Chatbot Engagement"
        supplement = make_text_pdf_bytes("Multimedia Appendix 1. Checklist for Reporting Results of Internet E-Surveys")
        ok, detail = downloader._pdf_title_match_result(supplement, title)
        self.assertFalse(ok)
        self.assertIn("supplementary", detail)

        downloader._current_paper_doi = "10.2196/53133"
        renamed = make_text_pdf_bytes("JMIR Preprints\nA chatbot engagement model for well-being apps\ndoi: 10.2196/53133")
        ok, _ = downloader._pdf_title_match_result(renamed, title)
        self.assertTrue(ok)

    def test_citation_pdf_url_is_kept_without_pdf_marker(self):
        from utils.fast_pdf_downloader import extract_static_pdf_urls

        html = ('<html><head><meta name="citation_pdf_url" content="https://jyx.jyu.fi/bitstreams/04e4/download">'
                '</head><body><a href="/article/view/12/34">PDF</a></body></html>')
        urls = extract_static_pdf_urls(html, "https://journal.example.org/index.php/j/article/view/12")
        self.assertEqual(urls[0], "https://jyx.jyu.fi/bitstreams/04e4/download")
        self.assertIn("https://journal.example.org/article/download/12/34", urls)

    def test_supplementary_file_names_are_not_article_candidates(self):
        from utils.fast_pdf_downloader import _is_likely_non_article_pdf_url

        for url in (
            "https://ars.els-cdn.com/content/image/1-s2.0-S1-mmc1.pdf",
            "https://static-content.springer.com/esm/art/MOESM1_ESM.pdf",
            "https://jmir.org/api/download?alt_name=mhealth_v13i1e53133_app1.pdf",
            "https://journals.plos.org/x/file?id=10.1371/journal.pone.0001.s001",
        ):
            self.assertTrue(_is_likely_non_article_pdf_url(url), url)
        self.assertFalse(_is_likely_non_article_pdf_url("https://mdpi-res.com/d_attachment/applsci/applsci-14-08868/article_deploy/applsci-14-08868.pdf"))

    def test_arxiv_versions_fall_back_to_earlier_versions(self):
        downloader = self._downloader()
        self.assertEqual(
            downloader._arxiv_version_candidates("http://arxiv.org/abs/2309.09749v3"),
            ["https://arxiv.org/pdf/2309.09749v3", "https://arxiv.org/pdf/2309.09749v2", "https://arxiv.org/pdf/2309.09749v1"],
        )
        self.assertEqual(downloader._arxiv_version_candidates("2307.04251")[-1], "https://arxiv.org/pdf/2307.04251v1")

    def test_unknown_publishers_do_not_share_one_concurrency_slot(self):
        downloader = self._downloader()
        a = downloader._paper_primary_domain({"doi": "10.55041/ijsrem1", "url": "https://pubmed.ncbi.nlm.nih.gov/1/"})
        b = downloader._paper_primary_domain({"doi": "10.17605/osf.io/x", "url": "https://pubmed.ncbi.nlm.nih.gov/2/"})
        self.assertNotEqual(a, b)
        self.assertEqual(a, "doi-prefix:10.55041")


if __name__ == "__main__":
    unittest.main()


class RoundTwoRuleTests(unittest.TestCase):
    def _downloader(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        return FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

    def test_dspace7_handle_resolves_through_rest_original_bundle(self):
        from utils import oa_sources

        oa_sources._DSPACE7_HOSTS.clear()
        find = "https://helda.helsinki.fi/server/api/pid/find"
        bundles = "https://helda.helsinki.fi/server/api/core/items/0a1b2c3d-0000-4000-8000-000000000001/bundles"
        session = FakeSessionByUrl(get_responses={
            find: FakeResponse(find, json_data={"uuid": "0a1b2c3d-0000-4000-8000-000000000001"}),
            bundles: FakeResponse(bundles, json_data={"_embedded": {"bundles": [
                {"name": "THUMBNAIL", "_embedded": {"bitstreams": {"_embedded": {"bitstreams": [
                    {"name": "paper.pdf.jpg", "_links": {"content": {"href": "https://h/thumb"}}}]}}}},
                {"name": "ORIGINAL", "_embedded": {"bitstreams": {"_embedded": {"bitstreams": [
                    {"name": "supplement.pdf", "sizeBytes": 9, "_links": {"content": {"href": "https://h/supp"}}},
                    {"name": "article.pdf", "sizeBytes": 5, "_links": {"content": {"href": "https://h/article"}}}]}}}},
            ]}}),
        })
        urls = oa_sources.dspace7_pdf_urls(session, "https://helda.helsinki.fi/handle/10138/123456", {})
        self.assertEqual(urls, ["https://h/article"])
        self.assertEqual(session.get_calls[0][1]["params"], {"id": "hdl:10138/123456"})

    def test_dspace7_ui_download_route_maps_to_rest_content(self):
        from utils.oa_sources import dspace7_pdf_urls

        self.assertEqual(
            dspace7_pdf_urls(None, "https://jyx.jyu.fi/bitstreams/04e44c08-9132-4bcc-9ac9-4e92ff51fe6b/download", {}),
            ["https://jyx.jyu.fi/server/api/core/bitstreams/04e44c08-9132-4bcc-9ac9-4e92ff51fe6b/content"],
        )

    def test_nva_publication_yields_presigned_open_file(self):
        from utils.oa_sources import nva_pdf_urls

        pub = "https://api.nva.unit.no/publication/abc"
        link = "https://api.nva.unit.no/publication/abc/filelink/f2"
        session = FakeSessionByUrl(get_responses={
            pub: FakeResponse(pub, json_data={"associatedArtifacts": [
                {"type": "OpenFile", "mimeType": "application/pdf", "identifier": "f1", "name": "COVER.pdf"},
                {"type": "OpenFile", "mimeType": "application/pdf", "identifier": "f2", "name": "FULLTEXT01.pdf"},
                {"type": "PendingOpenFile", "mimeType": "application/pdf", "identifier": "f3"},
            ]}),
            link: FakeResponse(link, json_data={"id": "https://s3.example/presigned"}),
        })
        self.assertEqual(next(iter(nva_pdf_urls(session, "abc", {}))), "https://s3.example/presigned")

    def test_honest_retry_rules(self):
        downloader = self._downloader()
        fastly = FakeResponse("u", content=b"<html><title>Client Challenge</title></html>", headers={"content-type": "text/html"})
        cloudflare = FakeResponse("u", content=b"Just a moment", status_code=403, headers={"cf-mitigated": "challenge"})
        plain_403 = FakeResponse("u", content=b"Forbidden", status_code=403)
        self.assertTrue(downloader._should_retry_with_honest_ua("https://link.springer.com/content/pdf/x.pdf", fastly))
        self.assertFalse(downloader._should_retry_with_honest_ua("https://repo.example/x.pdf", cloudflare))
        self.assertFalse(downloader._should_retry_with_honest_ua("https://publisher.example/x.pdf", plain_403))
        downloader._repository_urls.add("https://repo.example/x.pdf")
        self.assertTrue(downloader._should_retry_with_honest_ua("https://repo.example/x.pdf", plain_403))

    def test_elsevier_api_preview_is_rejected_and_key_stays_out_of_url(self):
        downloader = self._downloader()
        downloader.elsevier_api_key = "secret-key"
        downloader._paper_meta = {"licenses": ["cc-by"]}
        url = downloader._elsevier_api_candidate("10.1016/j.example.2026.1")
        self.assertNotIn("secret-key", url)
        self.assertEqual(downloader._url_headers[url]["X-ELS-APIKey"], "secret-key")
        downloader._active_download_url = url
        one_page = make_text_pdf_bytes("Journal Pre-proof Example title")
        self.assertIsNone(downloader._save_pdf_bytes_if_title_matches(one_page, "Example title", "publisher_elsevier"))
        self.assertEqual(downloader._last_failure_class, "publisher_paywalled")

    def test_openalex_content_key_is_a_request_parameter(self):
        from utils import fast_pdf_downloader as module

        downloader = self._downloader()
        downloader.openalex_api_key = "oa-key"
        module._KEYED_ROUTE_STATE.update(openalex_content_used=0, openalex_content_disabled=False)
        url = downloader._openalex_content_candidate({"content_pdf": "https://content.openalex.org/works/W1.pdf"})
        self.assertEqual(url, "https://content.openalex.org/works/W1.pdf")
        self.assertEqual(downloader._url_params[url], {"api_key": "oa-key"})

    def test_title_gate_accepts_non_english_pdf_with_record_doi(self):
        downloader = self._downloader()
        downloader._current_paper_doi = "10.21638/spbu09.2024.204"
        russian = make_text_pdf_bytes(
            "DOI: 10.21638/spbu09.2024.204\n" + "Bolshie yazykovye modeli v obuchenii " * 3
            + "\n" + " ".join(["slovo"] * 60)
        )
        ok, _ = downloader._pdf_title_match_result(russian, "Large language models in foreign language teaching practice")
        self.assertTrue(ok)
        # An English paper under a wrong record DOI is still rejected.
        downloader._current_paper_doi = "10.1093/bib/bbad453"
        english = make_text_pdf_bytes(
            "Explainable artificial intelligence for omics data: a systematic mapping study\ndoi 10.1093/bib/bbad453\n"
            + "This is the study of the omics and the methods that are used in the field " * 5
        )
        ok, _ = downloader._pdf_title_match_result(english, "XAI in Genomics with LLM Generated Explanations in Oncology")
        self.assertFalse(ok)


class VersionLookupTests(unittest.TestCase):
    def test_titles_match_is_strict(self):
        from utils.oa_sources import titles_match

        self.assertTrue(titles_match("Large language models encode clinical knowledge",
                                     "Large Language Models Encode Clinical Knowledge."))
        self.assertFalse(titles_match("Large language models encode clinical knowledge",
                                      "Publisher Correction: Large language models encode clinical knowledge"))
        self.assertFalse(titles_match("Survey", "Survey"))

    def test_arxiv_batch_maps_each_title_to_its_entry(self):
        from utils.oa_sources import arxiv_title_matches

        feed = ("<feed><entry><id>http://arxiv.org/abs/2212.13138v1</id>"
                "<title>Large Language Models Encode Clinical\n  Knowledge</title></entry>"
                "<entry><id>http://arxiv.org/abs/2506.07044v5</id><title>Lingshu: A Generalist Foundation Model for "
                "Unified Multimodal Medical Understanding and Reasoning</title></entry></feed>")

        class Session:
            calls = []

            def get(self, url, params=None, **kwargs):
                self.calls.append(params)
                return FakeResponse(url, content=feed.encode())

        session = Session()
        found = arxiv_title_matches(session, [
            "Large language models encode clinical knowledge",
            "Lingshu: A Generalist Foundation Model for Unified Multimodal Medical Understanding and Reasoning",
            "A title that arXiv does not have at all in this feed",
        ], {}, sleep_func=lambda s: None)
        self.assertEqual(found["Large language models encode clinical knowledge"]["id"], "2212.13138v1")
        self.assertEqual(len(found), 2)
        self.assertEqual(len(session.calls), 1)


class WeightedTitleGateTests(unittest.TestCase):
    def _downloader(self):
        from utils.fast_pdf_downloader import FastCascadePDFDownloader

        return FastCascadePDFDownloader(output_dir=Path("/tmp/reviewpilot-test-pdfs"))

    def test_same_topic_paper_is_rejected_on_distinctive_words(self):
        from utils.fast_pdf_downloader import batch_generic_title_tokens

        downloader = self._downloader()
        # In the app the batch's own titles teach the gate its topic words.
        downloader.generic_title_tokens = batch_generic_title_tokens(
            [f"Large language model (LLM) study {n}: GPT applications" for n in range(40)])
        downloader._active_method = "doi_static_html"
        other = make_text_pdf_bytes(
            "Fine-tuning a LLM using Reinforcement Learning from Human Feedback for a Therapy Chatbot\n"
            + "large language model GPT-3 PaLM-2 comparison " * 3
        )
        ok, detail = downloader._pdf_title_match_result(
            other, "Large language model (LLM) comparison between GPT-3 and PaLM-2 to produce Indonesian cultural content")
        self.assertFalse(ok)
        self.assertIn("distinctive", detail)

    def test_textless_pdf_needs_an_identifier_route(self):
        downloader = self._downloader()
        blank = make_text_pdf_bytes(" ")
        downloader._active_method = "doi_static_html"
        self.assertFalse(downloader._pdf_title_match_result(blank, "A mixed-initiative conversational agent for homes")[0])
        downloader._active_method = "pmc_cloud"
        self.assertTrue(downloader._pdf_title_match_result(blank, "A mixed-initiative conversational agent for homes")[0])


class SurnameTests(unittest.TestCase):
    def test_surnames_fold_accents_and_orders(self):
        from utils.oa_sources import surnames

        self.assertEqual(surnames(["Sören Auer", "Giglou, Hamed Babaei", "Eric Schwitzgebel"]),
                         {"auer", "giglou", "schwitzgebel"})


class QuotaCircuitBreakerTests(unittest.TestCase):
    def test_exhausted_daily_quota_stops_further_calls(self):
        from utils.oa_sources import OAMetadataIndex

        class Session:
            calls = 0

            def get(self, url, **kwargs):
                Session.calls += 1
                return FakeResponse(url, status_code=429, headers={"retry-after": "20070"})

            def post(self, url, **kwargs):
                return FakeResponse(url, json_data=[])

        index = OAMetadataIndex(email="me@uni.edu", session=Session(), sleep_func=lambda s: None,
                                enabled_sources=("openalex",))
        index.prefetch([{"doi": f"10.1/{n}"} for n in range(120)])
        self.assertEqual(Session.calls, 1)
        self.assertIn("api.openalex.org", index._exhausted_hosts)
