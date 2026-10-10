"""Feature B: extraction for papers without full text (design/FULL_TEXT_FALLBACK_DESIGN.md).

Every LLM, web-search and page-fetch call here is a fake; nothing reaches a provider or the network.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.extraction_agent import ExtractionAgent
from agents.lead_agent import LeadAgent
from reviewpilot_core import no_full_text, web_evidence
from reviewpilot_core.evidence_support import evidence_for_field
from reviewpilot_core.extraction_schema import finalize_schema, save_schema_draft
from reviewpilot_core.fulltext_eligibility import assessed, prisma_counts
from reviewpilot_core.model_policy import supports_web_search
from reviewpilot_core.project_store import read_json, read_jsonl
from reviewpilot_core.skill_runtime import SkillRegistry
from tests.test_lead_agent import confirm_rules, ledger_through

DOI = "10.1000/rural.2024.17"
TITLE = "Remote triage with a language model in rural clinics"
ABSTRACT = "We enrolled 240 adults across six rural clinics. Triage accuracy improved after deployment."
PAPER = {"id": "p1", "title": TITLE, "doi": DOI, "abstract": ABSTRACT, "source": "openalex",
         "url": "https://journal.example.org/article/rural-triage", "pdf_downloaded": False,
         "web_search_fallback_pending": True, "web_search_fallback_eligible": True,
         "retrieval_status": "subscribed_unavailable", "pdf_failure_class": "publisher_paywalled"}
SCHEMA = {"fields": [
    {"name": "sample_size", "type": "integer", "description": "Number of participants"},
    {"name": "trial_registration", "type": "Text", "description": "Trial registry identifier"},
    {"name": "funding", "type": "Text", "description": "Funding source"},
]}
PROMPT = {"system_prompt": "Extract structured evidence.", "user_prompt_template": "PAPER EVIDENCE DATA:\n{paper_text}", "schema": SCHEMA}

REGISTRY_URL = "https://clinicaltrials.gov/study/NCT05550001"
REGISTRY_PAGE = {"url": REGISTRY_URL, "declared_titles": ["Language Model Triage in Rural Clinics (NCT05550001)"],
                 "text": f"ClinicalTrials.gov ID NCT05550001. Publications: {TITLE}. doi:{DOI}. Sponsor: Rural Health Foundation."}
LANDING_URL = "https://journal.example.org/article/rural-triage"
LANDING_PAGE = {"url": LANDING_URL, "declared_dois": [DOI], "declared_titles": [TITLE],
                "text": f"{TITLE}. Funding: This study was funded by the Rural Health Foundation under grant RH-2203."}


def abstract_llm(calls=None, values=None, evidence=None):
    def query(*, text_prompt, system_prompt, model, provider):
        if calls is not None:
            calls.append({"text_prompt": text_prompt, "system_prompt": system_prompt})
        return json.dumps({"sample_size": 240, "trial_registration": "", "funding": "", **(values or {}),
                           "_field_evidence": evidence if evidence is not None else {
                               "sample_size": [{"quote": "We enrolled 240 adults across six rural clinics."}]}}), {"total_tokens": 100}
    return query


def web_query(payload, calls=None):
    def query(*, paper, extraction_prompt, model):
        if calls is not None:
            calls.append([field["name"] for field in extraction_prompt["schema"]["fields"]])
        return json.dumps(payload), {"input_tokens": 1000, "output_tokens": 100}
    return query


def pages(*known):
    by_url = {page["url"]: page for page in known}

    def fetch(url):
        if url not in by_url:
            raise AssertionError(f"unexpected fetch {url}")
        return by_url[url]
    return fetch


def extract(paper=PAPER, *, llm=None, web=None, fetcher=None, web_search=None, prompt=PROMPT):
    agent = ExtractionAgent(Path(tempfile.gettempdir()) / "rp-no-full-text", llm_query=llm or abstract_llm(),
                            web_search_query=web, page_fetcher=fetcher or pages())
    return agent.extract_one(paper=dict(paper), row_number=1, extraction_prompt=prompt, pdf_folder=Path("/nonexistent"),
                             pdf_files=[], web_search=web_search)


class PipelineTests(unittest.TestCase):
    def test_abstract_first_then_web_only_for_open_fields_then_not_available(self):
        llm_calls, web_calls = [], []
        row = extract(
            llm=abstract_llm(llm_calls),
            web=web_query({"trial_registration": "NCT05550001", "funding": "Rural Health Foundation",
                           "_field_evidence": {"trial_registration": [{"url": REGISTRY_URL, "quote": "ClinicalTrials.gov ID NCT05550001."}],
                                               "funding": [{"url": "https://twitter.com/ruralhealth/status/1", "quote": "Funded by the Rural Health Foundation today"}]}},
                          web_calls),
            fetcher=pages(REGISTRY_PAGE))

        self.assertEqual(row["extraction_status"], "success")
        self.assertIn(ABSTRACT, llm_calls[0]["text_prompt"])
        self.assertIn("_field_evidence", llm_calls[0]["system_prompt"])
        self.assertEqual(web_calls, [["trial_registration", "funding"]])  # the abstract already answered sample_size
        self.assertEqual(row["evidence_tiers"], {"sample_size": "abstract", "trial_registration": "web", "funding": "not_available"})
        self.assertEqual(row["sample_size"], 240)
        self.assertEqual(row["field_evidence"]["sample_size"]["quote"], "We enrolled 240 adults across six rural clinics.")
        web_value = row["field_evidence"]["trial_registration"]
        self.assertEqual(row["trial_registration"], "NCT05550001")
        self.assertEqual((web_value["source_url"], web_value["quote"]), (REGISTRY_URL, "ClinicalTrials.gov ID NCT05550001."))
        self.assertEqual(web_value["source_type"], "trial_registry")
        self.assertTrue(web_value["needs_human_check"])
        self.assertEqual(row["funding"], "")
        self.assertEqual(row["field_evidence"]["funding"]["rejected_sources"][0]["reason"], "social_media")
        self.assertEqual(row["source_urls"], [REGISTRY_URL])
        self.assertEqual(row["extracted_data"]["trial_registration"], "NCT05550001")
        self.assertGreater(row["extraction_cost_usd"], 0)

    def test_abstract_value_without_a_quote_found_in_the_abstract_goes_to_the_web_step(self):
        web_calls = []
        row = extract(llm=abstract_llm(evidence={"sample_size": [{"quote": "Two hundred and forty adults joined."}]}),
                      web=web_query({}, web_calls))
        self.assertEqual(web_calls, [["sample_size", "trial_registration", "funding"]])
        self.assertEqual(row["sample_size"], "")
        self.assertEqual(row["evidence_tiers"]["sample_size"], "not_available")

    def test_paper_without_abstract_goes_straight_to_web(self):
        def no_llm(**kwargs):
            raise AssertionError("no abstract means no abstract step")
        web_calls = []
        row = extract({**PAPER, "abstract": ""}, llm=no_llm, web=web_query({}, web_calls))
        self.assertEqual(web_calls, [["sample_size", "trial_registration", "funding"]])
        self.assertEqual(set(row["evidence_tiers"].values()), {"not_available"})

    def test_full_text_rows_carry_the_full_text_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "row1_paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            agent = ExtractionAgent(Path(tmp), llm_query=abstract_llm(), pdf_reader=lambda path: "[PDF page 3]\nWe enrolled 240 adults across six rural clinics.")
            row = agent.extract_one(paper={**PAPER, "pdf_downloaded": True, "pdf_path": str(pdf), "web_search_fallback_pending": False},
                                    row_number=1, extraction_prompt=PROMPT, pdf_folder=Path(tmp), pdf_files=[pdf])
        self.assertEqual(row["extraction_source"], "pdf")
        self.assertEqual(set(row["evidence_tiers"].values()), {"full_text"})
        self.assertEqual(row["field_evidence"]["sample_size"]["tier"], "full_text")
        self.assertEqual(row["field_evidence"]["sample_size"]["page"], 3)

    def test_web_step_failure_keeps_abstract_values_and_the_paper_stays_retryable(self):
        def failing(**kwargs):
            raise RuntimeError("search tool timed out")
        row = extract(web=failing)
        self.assertEqual(row["extraction_status"], "success")
        self.assertEqual(row["evidence_tiers"], {"sample_size": "abstract", "trial_registration": "not_available", "funding": "not_available"})
        self.assertEqual(row["web_search"]["status"], "failed")
        self.assertTrue(row["web_search_fallback_pending"])


class AcceptanceTests(unittest.TestCase):
    """The Acceptance checks of Feature B, one test per check."""

    def _web_value(self, evidence, *known, value="Rural Health Foundation", paper=PAPER):
        row = extract(paper, llm=abstract_llm(), web=web_query({"funding": value, "_field_evidence": {"funding": evidence}}),
                      fetcher=pages(*known))
        return row

    def assert_not_stored(self, row, reason):
        self.assertEqual(row["funding"], "")
        self.assertEqual(row["evidence_tiers"]["funding"], "not_available")
        self.assertEqual(row["source_urls"], [])
        self.assertNotIn("source_url", row["field_evidence"]["funding"])
        self.assertIn(reason, [item["reason"] for item in row["field_evidence"]["funding"]["rejected_sources"]])

    def test_value_without_url_and_quote_is_never_stored(self):
        quote = "This study was funded by the Rural Health Foundation under grant RH-2203."
        self.assert_not_stored(self._web_value([]), "missing_evidence")
        self.assert_not_stored(self._web_value([{"quote": quote}]), "missing_url")
        self.assert_not_stored(self._web_value([{"url": LANDING_URL}]), "missing_quote")
        # No metadata link stands in for a missing source.
        row = extract(web=web_query({"funding": "Rural Health Foundation", "source_urls": [LANDING_URL]}))
        self.assert_not_stored(row, "missing_evidence")

    def test_value_from_page_not_confirmed_to_be_the_paper_is_never_stored(self):
        quote = "This study was funded by the Rural Health Foundation under grant RH-2203."
        unrelated = {"url": "https://journal.example.org/article/other", "declared_dois": [], "declared_titles": ["Another study"],
                     "text": "Another study. " + quote}
        self.assert_not_stored(self._web_value([{"url": unrelated["url"], "quote": quote}], unrelated), "paper_not_on_page")
        paraphrase = self._web_value([{"url": LANDING_URL, "quote": "The Rural Health Foundation paid for this work."}], LANDING_PAGE)
        self.assert_not_stored(paraphrase, "quote_not_on_page")
        joined = self._web_value([{"url": LANDING_URL, "quote": "This study was funded ... grant RH-2203."}], LANDING_PAGE)
        self.assert_not_stored(joined, "quote_not_verbatim")
        def unreachable(url):
            raise ConnectionError("challenge page")
        row = extract(web=web_query({"funding": "x", "_field_evidence": {"funding": [{"url": LANDING_URL, "quote": quote}]}}), fetcher=unreachable)
        self.assert_not_stored(row, "page_unavailable")
        # A confirmed page with a verbatim quote is stored, with both kept.
        accepted = self._web_value([{"url": LANDING_URL, "quote": quote}], LANDING_PAGE)
        self.assertEqual(accepted["evidence_tiers"]["funding"], "web")
        self.assertEqual(accepted["field_evidence"]["funding"]["source_url"], LANDING_URL)
        self.assertEqual(accepted["field_evidence"]["funding"]["quote"], quote)

    def test_value_found_in_a_citing_paper_or_news_article_is_rejected(self):
        quote = "This study was funded by the Rural Health Foundation under grant RH-2203."
        citing = {"url": "https://www.nature.com/articles/s41591-025-0001", "declared_dois": ["10.1038/s41591-025-0001"],
                  "declared_titles": ["Language models in primary care: a review"],
                  "text": f"Language models in primary care: a review. {quote} References: 12. {TITLE}. doi:{DOI}"}
        self.assert_not_stored(self._web_value([{"url": citing["url"], "quote": quote}], citing), "other_work")
        citing_without_meta = {"url": "https://www.sciencedirect.com/science/article/pii/S0001", "declared_titles": ["Commentary on rural AI"],
                               "text": f"Commentary on rural AI. {quote} We discuss {TITLE}."}
        self.assert_not_stored(self._web_value([{"url": citing_without_meta["url"], "quote": quote}], citing_without_meta), "other_work")
        news_on_journal_site = {"url": "https://www.nature.com/articles/d41586-025-0002", "declared_dois": ["10.1038/d41586-025-0002"],
                                "declared_titles": ["AI triage arrives in rural clinics"], "text": f"{quote} The paper, {TITLE}, ..."}
        self.assert_not_stored(self._web_value([{"url": news_on_journal_site["url"], "quote": quote}], news_on_journal_site), "other_work")
        for url, reason in (("https://www.sciencedaily.com/releases/2025/01/250101.htm", "news_or_blog"),
                            ("https://www.nature.com/news/ai-triage-rural-1.2", "news_or_blog"),
                            ("https://medium.com/@someone/rural-triage", "news_or_blog"),
                            ("https://x.com/journal/status/1", "social_media"),
                            ("https://www.researchgate.net/publication/1_Remote_triage", "social_media"),
                            ("https://sci-hub.se/10.1000/rural.2024.17", "shadow_library"),
                            ("https://pubmed.ncbi.nlm.nih.gov/12345/", "not_allowed_source"),
                            ("https://example.com/copy.pdf", "not_allowed_source")):
            with self.subTest(url=url):
                self.assert_not_stored(self._web_value([{"url": url, "quote": quote}]), reason)

    def test_abstract_and_web_values_are_never_shown_as_full_text_and_exports_carry_the_tier(self):
        quote = "This study was funded by the Rural Health Foundation under grant RH-2203."
        row = self._web_value([{"url": LANDING_URL, "quote": quote}], LANDING_PAGE)
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "pdfs").mkdir()
            shown = {field: evidence_for_field(project, row, field) for field in ("sample_size", "funding", "trial_registration")}
        self.assertEqual({field: item["tier"] for field, item in shown.items()},
                         {"sample_size": "abstract", "funding": "web", "trial_registration": "not_available"})
        for item in shown.values():
            self.assertIsNone(item["page"])
            self.assertEqual(item["pages"], [])
            self.assertNotIn("PDF", item["note"])
            self.assertNotEqual(item["tier"], "full_text")
        self.assertTrue(shown["funding"]["needsHumanCheck"])
        self.assertEqual(shown["funding"]["sourceUrls"], [LANDING_URL])
        self.assertEqual(shown["sample_size"]["sourceUrls"], [])
        self.assertFalse(assessed(row))  # never counted as a full-text assessment

    def test_model_without_web_search_completes_from_the_abstract_with_a_visible_notice(self):
        self.assertFalse(supports_web_search("Qwen/Qwen2.5-7B-Instruct-Turbo"))
        self.assertFalse(supports_web_search("deepseek-ai/DeepSeek-V4.1-Flash"))
        self.assertTrue(supports_web_search("gpt-6-luna"))
        with tempfile.TemporaryDirectory() as tmp:
            root, project = _extraction_project(self, tmp, model="Qwen/Qwen2.5-7B-Instruct-Turbo")
            plan = no_full_text.plan(project)
            from reviewpilot_core.state_projection import build_rp_data
            before = build_rp_data(root, "demo")["noFullText"]

            def no_web(*args, **kwargs):
                raise AssertionError("the web step is skipped for a model without web search")

            with patch.object(ExtractionAgent, "_query_web_search_extraction", no_web), patch.object(web_evidence, "fetch_page", no_web):
                result = LeadAgent(root, llm_query=_skill_llm()).handle_message(project_id="demo", action="run-extraction")
            rows = read_jsonl(project / "extraction" / "extraction_results.jsonl")
            stats = read_json(project / "extraction" / "extraction_stats.json")

        self.assertFalse(plan["web_search"])
        self.assertIn("web step is skipped", before["notice"])
        self.assertEqual(result.status, "completed")
        self.assertIn("web step was skipped", result.reply)
        self.assertEqual(stats["no_full_text"]["web_search"], "skipped")
        closed = rows[1]
        self.assertEqual(closed["extraction_status"], "success")
        self.assertEqual(closed["web_search"]["status"], "skipped")
        self.assertEqual(closed["evidence_tiers"], {"sample_size": "abstract", "trial_registration": "not_available", "funding": "not_available"})


class SourceTypeTests(unittest.TestCase):
    QUOTE = "This study was funded by the Rural Health Foundation under grant RH-2203."

    def test_every_allowed_source_type_is_accepted_when_the_page_is_this_paper(self):
        cases = {
            "publisher_landing_page": ("https://journal.example.org/article/rural-triage", {"declared_dois": [DOI]}),
            "publisher_landing_page ": ("https://doi.org/" + DOI, {"url": "https://publisher.example/abs/1", "declared_dois": [DOI]}),
            "pmc_europe_pmc": ("https://pmc.ncbi.nlm.nih.gov/articles/PMC11/", {"declared_titles": [TITLE]}),
            "pmc_europe_pmc ": ("https://europepmc.org/article/MED/1", {"declared_dois": [DOI]}),
            "preprint": ("https://www.medrxiv.org/content/10.1101/2024.01.01.1v1", {"declared_dois": ["10.1101/2024.01.01.1"], "declared_titles": [TITLE + " | medRxiv"]}),
            "supplementary_material": (f"https://journal.example.org/doi/suppl/{DOI}/suppl_file/appendix.pdf", {}),
            "trial_registry": (REGISTRY_URL, {}),
        }
        for expected, (url, meta) in cases.items():
            with self.subTest(source=expected):
                page = {"url": url, "text": f"{TITLE}. doi:{DOI}. {self.QUOTE}", **meta}
                fetched_url = page["url"]
                evidence = web_evidence.accept_web_evidence({"url": url, "quote": self.QUOTE}, PAPER, lambda u: page, {})
                self.assertEqual(evidence["source_type"], expected.strip())
                self.assertEqual(evidence["source_url"], url)
                if fetched_url != url:
                    self.assertEqual(evidence["resolved_url"], fetched_url)

    def test_redirect_to_a_source_outside_the_allowed_types_is_rejected(self):
        page = {"url": "https://mirror.example.net/copy", "text": f"{TITLE}. {self.QUOTE}", "declared_titles": [TITLE]}
        with self.assertRaises(web_evidence.RejectedSource) as ctx:
            web_evidence.accept_web_evidence({"url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC11/", "quote": self.QUOTE}, PAPER, lambda url: page, {})
        self.assertEqual(ctx.exception.reason, "not_allowed_source")

    def test_doi_link_for_another_doi_is_rejected(self):
        with self.assertRaises(web_evidence.RejectedSource):
            web_evidence.classify_source("https://doi.org/10.1000/other", PAPER)

    def test_parse_page_reads_declared_identity_and_skips_scripts(self):
        html = (f'<html><head><title>{TITLE} | Journal</title><meta name="citation_doi" content="doi:{DOI}">'
                f'<script>var quote = "{self.QUOTE}";</script></head><body><h1>{TITLE}</h1><p>Funding statement.</p></body></html>')
        page = web_evidence.parse_page("https://journal.example.org/a", html.encode(), "text/html")
        self.assertEqual(page["declared_dois"], [DOI])
        self.assertIn(f"{TITLE} | Journal", page["declared_titles"])
        self.assertIn("Funding statement.", page["text"])
        self.assertNotIn("Rural Health Foundation", page["text"])


class LeadAgentTriggerTests(unittest.TestCase):
    def test_estimate_is_shown_before_running_and_counts_only_papers_still_without_full_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, project = _extraction_project(self, tmp, model="gpt-6-luna", extra_user_pdf=True)
            from reviewpilot_core.state_projection import build_rp_data
            shown = build_rp_data(root, "demo")["noFullText"]
            plan = no_full_text.plan(project)
        # Three papers: one retrieved, one supplied by the user after retrieval (Feature A), one still without full text.
        self.assertEqual(plan["papers"], 1)
        self.assertTrue(plan["web_search"])
        per_paper = no_full_text.per_paper_estimate("gpt-6-luna", True)
        self.assertEqual(plan["per_paper_usd"], per_paper)
        self.assertEqual(shown["papers"], 1)
        self.assertEqual(shown["perPaperUsd"], per_paper)
        self.assertIn(f"about 1 × ${per_paper:.3f}", shown["notice"])
        self.assertIn("abstracts, then allowed web sources", shown["notice"])

    def test_lead_agent_triggers_web_step_and_reports_tiers_after_feature_a(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, project = _extraction_project(self, tmp, model="gpt-6-luna", extra_user_pdf=True)
            web_papers = []

            def fake_web(agent, paper, extraction_prompt):
                web_papers.append(paper["id"])
                self.assertIn("structured-evidence-extraction", agent.skill_augment("system"))
                return json.dumps({"funding": "Rural Health Foundation", "_field_evidence": {"funding": [
                    {"url": LANDING_URL, "quote": "This study was funded by the Rural Health Foundation under grant RH-2203."}]}}), {}

            with patch.object(ExtractionAgent, "_query_web_search_extraction", fake_web), patch.object(web_evidence, "fetch_page", pages(LANDING_PAGE)):
                result = LeadAgent(root, llm_query=_skill_llm()).handle_message(project_id="demo", action="run-extraction")
            rows = {row["paper_id"]: row for row in read_jsonl(project / "extraction" / "extraction_results.jsonl")}
            counts = prisma_counts(project)

        self.assertEqual(web_papers, ["p1"])  # the user-supplied PDF is extracted as full text
        self.assertEqual(rows["user"]["extraction_source"], "pdf")
        self.assertEqual(rows["p1"]["evidence_tiers"], {"sample_size": "abstract", "trial_registration": "not_available", "funding": "web"})
        self.assertIn("marked for human check", result.reply)
        self.assertEqual(result.data["no_full_text_plan"]["papers"], 1)
        self.assertEqual(counts["screening"]["reports_not_retrieved"], 1)
        self.assertEqual(counts["field_evidence_tiers"], {"full_text": 6, "abstract": 1, "web": 1, "not_available": 1})


class ExportAndDisplayTests(unittest.TestCase):
    def test_exports_and_review_workbench_carry_the_tier_and_prisma_still_reports_not_retrieved(self):
        from starlette.testclient import TestClient
        import web_app
        from reviewpilot_core import record_review
        from reviewpilot_core.atomic_files import atomic_write_json, atomic_write_jsonl
        from tests.test_record_review import make_project

        quote = "This study was funded by the Rural Health Foundation under grant RH-2203."
        web_row = extract({**PAPER, "id": "p2"}, web=web_query({"funding": "Rural Health Foundation", "_field_evidence": {
            "funding": [{"url": LANDING_URL, "quote": quote}]}}), fetcher=pages(LANDING_PAGE))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = make_project(root)
            atomic_write_json(project / "extraction/extraction_schema.json", SCHEMA)
            atomic_write_jsonl(project / "filtered/included_papers.jsonl",
                               [{"id": "p1", "title": "Clinical study of AI", "pdf_downloaded": True}, {**PAPER, "id": "p2"}])
            pdf_row = {"paper_id": "p1", "title": "Clinical study of AI", "extraction_source": "pdf", "extraction_status": "success",
                       "pdf_file": "row1_paper.pdf", "sample_size": 42, "trial_registration": "", "funding": "",
                       "extracted_data": {"sample_size": 42, "trial_registration": "", "funding": ""},
                       "field_evidence": {name: {"tier": "full_text", "quote": "", "page": None} for name in ("sample_size", "trial_registration", "funding")},
                       "evidence_tiers": {"sample_size": "full_text", "trial_registration": "full_text", "funding": "full_text"}}
            atomic_write_jsonl(project / "extraction/extraction_results.jsonl", [pdf_row, web_row])
            with patch.object(web_app, "OUTPUT_ROOT", root):
                client = TestClient(web_app.create_app())
                exported = client.get("/projects/study/exports/extraction-results")
                prisma = client.get("/projects/study/exports/prisma").json()
                key = record_review.key(web_row)
                detail = client.get("/projects/study/review/field", params={"key": key, "field": "funding"}).json()
            workbench = record_review.extraction_rows(project)

        self.assertEqual(exported.status_code, 200, exported.text)
        rows = [json.loads(line) for line in exported.text.splitlines() if line.strip()]
        self.assertEqual(rows[1]["evidence_tiers"], {"sample_size": "abstract", "trial_registration": "not_available", "funding": "web"})
        self.assertEqual(rows[1]["field_evidence"]["funding"]["source_url"], LANDING_URL)
        self.assertEqual(rows[1]["field_evidence"]["funding"]["quote"], quote)
        self.assertEqual(prisma["screening"]["reports_not_retrieved"], 1)
        self.assertEqual(prisma["screening"]["reports_assessed"], 1)
        self.assertEqual(prisma["field_evidence_tiers"], {"full_text": 3, "abstract": 1, "web": 1, "not_available": 1})
        self.assertEqual(detail["evidence"]["tier"], "web")
        self.assertTrue(detail["evidence"]["needsHumanCheck"])
        self.assertIsNone(detail["evidence"]["page"])
        self.assertEqual([field["tier"] for field in workbench[1]["fields"]], ["abstract", "not_available", "web"])
        self.assertEqual([field["tier"] for field in workbench[0]["fields"]], ["full_text"] * 3)

    def test_interface_labels_tiers_distinctly_and_shows_the_estimate_before_running(self):
        root = Path(__file__).resolve().parents[1]
        review_js = (root / "frontend" / "review.js").read_text(encoding="utf-8")
        app_js = (root / "frontend" / "app.js").read_text(encoding="utf-8")
        for label in ("Full text", "Abstract", "Web · needs human check", "Not available without full text"):
            self.assertIn(label, review_js)
        self.assertIn("tierBadge(f.tier)", review_js)
        self.assertIn("tierBadge(ev.tier)", review_js)
        canvas = app_js[app_js.index("function extractionCanvas(v)"):app_js.index("function fieldsTable(v)")]
        self.assertLess(canvas.index("noFullTextNotice(v.noFullText)"), canvas.index('data-action="run-extraction"'))
        notice = canvas[canvas.index("function noFullTextNotice(n)"):]
        self.assertNotIn("<button", notice)  # information, not an opt-in switch
        self.assertNotIn("<input", notice)


class TargetingAndCostTests(unittest.TestCase):
    def test_web_search_starts_from_this_papers_own_pages(self):
        paper = {**PAPER, "doi": "10.1097/SCS.0000000000013379", "url": "https://pubmed.ncbi.nlm.nih.gov/1/",
                 "abstract": ABSTRACT + " The trial was registered (NCT05550001)."}
        agent = ExtractionAgent(Path(tempfile.gettempdir()) / "rp-no-full-text")
        domains = agent._web_search_domains(paper)
        prompt = agent._web_search_fallback_prompt(paper, PROMPT)
        self.assertEqual(domains[0], "journals.lww.com")  # the DOI prefix names the publisher; PubMed is not a source
        self.assertIn("clinicaltrials.gov", domains)
        self.assertLessEqual(len(domains), 20)
        self.assertIn("https://doi.org/10.1097/scs.0000000000013379", prompt)
        self.assertIn("https://clinicaltrials.gov/study/NCT05550001", prompt)
        self.assertIn('"\\"10.1097/scs.0000000000013379\\""', prompt)
        self.assertEqual(web_evidence.classify_source("https://journals.lww.com/jcraniofacialsurgery/abstract/1", paper), "publisher_landing_page")

    def test_costs_use_the_model_price_table_and_count_web_search_calls(self):
        priced = ExtractionAgent(Path(tempfile.gettempdir()) / "rp-no-full-text", model="gpt-6-luna")
        self.assertAlmostEqual(priced._usage_cost({"input_tokens": 1_000_000, "output_tokens": 1_000_000}), 0.60)
        self.assertAlmostEqual(priced._usage_cost({"input_tokens": 0, "output_tokens": 0, "web_search_calls": 3}), 0.03)

        class Usage:
            input_tokens, output_tokens, total_tokens = 10_000, 1_000, 11_000

        class Response:
            output_text = "{}"
            usage = Usage()
            output = [{"type": "web_search_call"}, {"type": "web_search_call"}, {"type": "message"}]

        class Client:
            class responses:
                @staticmethod
                def create(**kwargs):
                    return Response()

        agent = ExtractionAgent(Path(tempfile.gettempdir()) / "rp-no-full-text", model="gpt-6-luna", llm_query=lambda **kw: (
            json.dumps({"sample_size": "", "trial_registration": "", "funding": "", "_field_evidence": {}}), {"input_tokens": 3000, "output_tokens": 2000}))
        agent.client = Client()
        row = agent.extract_one(paper=dict(PAPER), row_number=1, extraction_prompt=PROMPT, pdf_folder=Path("/nonexistent"), pdf_files=[])
        expected = (3000 * 0.10 + 2000 * 0.50 + 10_000 * 0.10 + 1_000 * 0.50) / 1_000_000 + 2 * 0.01
        self.assertAlmostEqual(row["extraction_cost_usd"], expected)


class PrismaClassTests(unittest.TestCase):
    """Papers without a PDF in PRISMA and the analysis set (agreed with the full-text eligibility owner)."""

    def make_project(self, tmp):
        from reviewpilot_core import fulltext_eligibility as fte
        from reviewpilot_core.atomic_files import atomic_write_json, atomic_write_jsonl
        from reviewpilot_core.workflow_state import initialize_workflow_state
        project = Path(tmp) / "study"
        atomic_write_json(project / "collected/summary.json", {"total_papers": 7, "platform_stats": {"pubmed": 7}})
        atomic_write_json(project / "filtered/screening_stats.json", {"initial_count": 7, "removed": {}})
        atomic_write_jsonl(project / "filtered/included_papers.jsonl", [{"id": f"p{i}", "title": f"Paper {i}"} for i in range(1, 8)])
        atomic_write_jsonl(project / "filtered/excluded_papers.jsonl", [])
        atomic_write_json(project / "extraction/extraction_schema.json", {"fields": [{"name": fte.FIELD, "type": "Select"}, {"name": "n", "type": "integer"}]})
        exclude = fte.EXCLUDE_PREFIX + "Animal-only study"

        def web(i, eligibility, tiers, **extra):
            return {"paper_id": f"p{i}", "title": f"Paper {i}", "extraction_source": "web_search_fallback", "extraction_status": "success",
                    fte.FIELD: eligibility, "n": 5, "extracted_data": {fte.FIELD: eligibility, "n": 5}, "evidence_tiers": tiers, **extra}
        atomic_write_jsonl(project / "extraction/extraction_results.jsonl", [
            {"paper_id": "p1", "title": "Paper 1", "extraction_source": "pdf", "extraction_status": "success", "pdf_file": "row1.pdf",
             fte.FIELD: "Include", "n": 5, "extracted_data": {fte.FIELD: "Include", "n": 5}, "evidence_tiers": {fte.FIELD: "full_text", "n": "full_text"}},
            web(2, "Include", {fte.FIELD: "abstract", "n": "abstract"}),                     # 1: eligibility from the abstract
            web(3, exclude, {fte.FIELD: "web", "n": "not_available"}),                         # 1: excluded on a web source
            web(4, "", {fte.FIELD: "not_available", "n": "abstract"}),                        # 2: eligibility undetermined
            web(5, "", {fte.FIELD: "not_available", "n": "not_available"}),                   # 3: no evidence
            {"paper_id": "p6", "title": "Paper 6", "extraction_source": "web_search_fallback", "extraction_status": "success",
             fte.FIELD: "", "n": 7, "extracted_data": {fte.FIELD: "", "n": 7}, "source_urls": ["https://example.org"]},  # 3: legacy row, no tiers
            {"paper_id": "p7", "title": "Paper 7", "extraction_source": "web_search_fallback", "extraction_status": "error"},  # 4: not extracted
        ])
        initialize_workflow_state(project)
        return project

    def test_not_retrieved_splits_into_four_disjoint_classes_that_add_up(self):
        from reviewpilot_core import fulltext_eligibility as fte
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(tmp)
            counts = fte.prisma_counts(project)
            rows = read_jsonl(project / "extraction/extraction_results.jsonl")
        s = counts["screening"]
        parts = (s["not_retrieved_assessed_from_web_sources"], s["not_retrieved_eligibility_undetermined"],
                 s["not_retrieved_no_evidence"], s["not_retrieved_not_extracted"])
        self.assertEqual(s["reports_not_retrieved"], 6)
        self.assertEqual(parts, (2, 1, 2, 1))
        self.assertEqual(sum(parts), s["reports_not_retrieved"])  # invariant
        self.assert_paper_level_invariants(s)
        self.assertEqual(s["not_retrieved_excluded_from_web_sources"], 1)
        self.assertEqual(counts["included"]["studies_included_from_web_sources"], 1)
        self.assertEqual(counts["included"]["studies_included"], 1)
        self.assertEqual(s["reports_assessed"], 1)
        self.assertEqual([fte.no_full_text_class(row) for row in rows],
                         [None, fte.ASSESSED_FROM_WEB, fte.ASSESSED_FROM_WEB, fte.ELIGIBILITY_UNDETERMINED, fte.NO_EVIDENCE, fte.NO_EVIDENCE, None])

    def test_analysis_set_has_one_rule_shared_by_summary_categorization_and_prisma(self):
        from reviewpilot_core import fulltext_eligibility as fte
        from reviewpilot_core.categorization_analysis import _successful_rows
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(tmp)
            rows = read_jsonl(project / "extraction/extraction_results.jsonl")
            counts = fte.prisma_counts(project)
        summary = fte.analysis_summary(rows)
        self.assertEqual([row["paper_id"] for row in rows if fte.in_analysis(row)], ["p1", "p2"])
        self.assertEqual([row["paper_id"] for row in _successful_rows(rows)], ["p1", "p2"])
        self.assertEqual((summary["extracted"], summary["included"], summary["excluded"], summary["eligibility_undetermined"], summary["no_evidence"]),
                         (6, 2, 1, 1, 2))
        self.assertEqual(summary["extracted"], summary["included"] + summary["excluded"] + summary["eligibility_undetermined"] + summary["no_evidence"])
        self.assertEqual(summary["included"], counts["included"]["studies_included"] + counts["included"]["studies_included_from_web_sources"])

    def assert_paper_level_invariants(self, s):
        parts = (s["not_retrieved_assessed_from_web_sources"], s["not_retrieved_eligibility_undetermined"],
                 s["not_retrieved_no_evidence"], s["not_retrieved_not_extracted"])
        self.assertEqual(s["reports_sought"], s["reports_assessed"] + s["reports_not_extracted"] + s["reports_not_retrieved"])
        self.assertEqual(sum(parts), s["reports_not_retrieved"])
        self.assertTrue(all(part >= 0 for part in parts))
        return parts

    def test_pdf_downloaded_but_extracted_from_the_abstract_has_no_usable_full_text(self):
        from reviewpilot_core import fulltext_eligibility as fte
        from reviewpilot_core.atomic_files import atomic_write_jsonl
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(tmp)
            rows = read_jsonl(project / "extraction/extraction_results.jsonl")
            # p7's PDF could not be used; its row came from the abstract instead.
            rows[-1] = {"paper_id": "p7", "title": "Paper 7", "pdf_file": "row7.pdf", "extraction_source": "web_search_fallback",
                        "extraction_status": "success", fte.FIELD: "Include", "extracted_data": {fte.FIELD: "Include"},
                        "evidence_tiers": {fte.FIELD: "abstract"}}
            atomic_write_jsonl(project / "extraction/extraction_results.jsonl", rows)
            counts = fte.prisma_counts(project)
        s = counts["screening"]
        self.assertEqual((s["reports_assessed"], s["reports_not_extracted"], s["reports_not_retrieved"]), (1, 0, 6))
        self.assertEqual(self.assert_paper_level_invariants(s), (3, 1, 2, 0))
        self.assertEqual(fte.no_full_text_class(rows[-1]), fte.ASSESSED_FROM_WEB)
        self.assertTrue(fte.in_analysis(rows[-1]))
        self.assertEqual(counts["included"]["studies_included_from_web_sources"], 2)

    def test_pdf_downloaded_but_extraction_failed_counts_once_under_not_extracted(self):
        from reviewpilot_core import fulltext_eligibility as fte
        from reviewpilot_core.atomic_files import atomic_write_jsonl
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(tmp)
            rows = read_jsonl(project / "extraction/extraction_results.jsonl")
            rows[-1] = {"paper_id": "p7", "title": "Paper 7", "pdf_file": "row7.pdf", "extraction_source": "pdf",
                        "extraction_status": "error", "error_message": "Could not extract text from PDF"}
            atomic_write_jsonl(project / "extraction/extraction_results.jsonl", rows)
            counts = fte.prisma_counts(project)
        s = counts["screening"]
        self.assertEqual((s["reports_assessed"], s["reports_not_extracted"], s["reports_not_retrieved"]), (1, 1, 5))
        self.assertEqual(self.assert_paper_level_invariants(s), (2, 1, 2, 0))
        self.assertFalse(fte.in_analysis(rows[-1]))

    def test_studies_categorized_counts_only_papers_still_in_the_analysis_set(self):
        from reviewpilot_core import fulltext_eligibility as fte
        from reviewpilot_core.atomic_files import atomic_write_jsonl
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(tmp)
            rows = {row["paper_id"]: row for row in read_jsonl(project / "extraction/extraction_results.jsonl")}
            # Categorized under the old rule, which kept every successful row: p6 is a legacy web row without tiers.
            atomic_write_jsonl(project / "categorization/categorized_results.jsonl",
                               [{**rows[key], "category": "Clinical"} for key in ("p1", "p2", "p4", "p5", "p6")])
            counts = fte.prisma_counts(project)
        self.assertEqual(counts["included"]["studies_categorized"], 2)  # p1 (full text) and p2 (abstract)

    def test_schema_without_eligibility_field_counts_papers_with_evidence_as_assessed(self):
        from reviewpilot_core import fulltext_eligibility as fte
        row = {"extraction_source": "web_search_fallback", "extraction_status": "success", "evidence_tiers": {"n": "abstract"}}
        self.assertEqual(fte.no_full_text_class(row), fte.ASSESSED_FROM_WEB)
        self.assertTrue(fte.in_analysis(row))
        self.assertEqual(fte.no_full_text_class({**row, "evidence_tiers": {"n": "not_available"}}), fte.NO_EVIDENCE)


class PromptPlacementTests(unittest.TestCase):
    def test_no_full_text_rules_stay_out_of_the_skill_used_by_full_text_extraction(self):
        from agents.extraction_agent import ABSTRACT_ONLY_NOTE, WEB_EVIDENCE_RULES
        activation = SkillRegistry().activate("run-extraction", "ExtractionAgent")
        self.assertEqual(activation.version, "3.0.0")
        self.assertNotIn("Extract without full text", activation.instructions)
        for text in ("same schema, category definitions and coding rules", "quote the abstract sentence", "silence in an abstract"):
            self.assertIn(text, ABSTRACT_ONLY_NOTE)
        for text in ("trial registry entry", "supplementary material", "DOI or title", "cite or review"):
            self.assertIn(text, WEB_EVIDENCE_RULES)

    def test_abstract_and_web_prompts_carry_their_rules(self):
        from agents.extraction_agent import ABSTRACT_ONLY_NOTE, WEB_EVIDENCE_RULES
        calls = []
        extract(llm=abstract_llm(calls), web=web_query({}))
        self.assertIn(ABSTRACT_ONLY_NOTE, calls[0]["system_prompt"])
        sent = []

        class Client:
            class responses:
                @staticmethod
                def create(**kwargs):
                    sent.append(kwargs)
                    return type("R", (), {"output_text": "{}", "usage": None, "output": []})()

        agent = ExtractionAgent(Path(tempfile.gettempdir()) / "rp-no-full-text")
        agent.client = Client()
        agent._query_web_search_extraction(PAPER, PROMPT)
        self.assertIn(WEB_EVIDENCE_RULES, sent[0]["input"][0]["content"])

    def test_one_bad_abstract_field_costs_only_that_field(self):
        # The model leaves out one field and gives another an off-type value; the paper still succeeds.
        llm = abstract_llm(values={"trial_registration": ["NCT1", {"bad": 1}]}, evidence={
            "sample_size": [{"quote": "We enrolled 240 adults across six rural clinics."}]})
        def query(**kwargs):
            text, usage = llm(**kwargs)
            data = json.loads(text); data.pop("funding")
            return json.dumps(data), usage
        row = extract(llm=query, web_search=False)
        self.assertEqual(row["extraction_status"], "success")
        self.assertEqual(row["sample_size"], 240)
        self.assertEqual(row["evidence_tiers"], {"sample_size": "abstract", "trial_registration": "not_available", "funding": "not_available"})
        self.assertEqual(row["field_evidence"]["trial_registration"]["rejected_sources"][0]["reason"], "value_type")


def _skill_llm():
    def query(*, text_prompt, system_prompt, model, provider):
        if "ABSTRACT:\n" + ABSTRACT in text_prompt:
            return abstract_llm()(text_prompt=text_prompt, system_prompt=system_prompt, model=model, provider=provider)
        if "PAPER EVIDENCE DATA" in text_prompt:
            return json.dumps({"sample_size": 120, "trial_registration": "NCT01", "funding": "Agency",
                               "_field_evidence": {"sample_size": [{"quote": "We enrolled 120 adults.", "page": 1}]}}), {"total_tokens": 10}
        return json.dumps({"reply": "Done."}), {}
    return query


def _extraction_project(case, tmp, *, model, extra_user_pdf=False):
    root = Path(tmp)
    project = root / "demo"
    ledger_through(project, "retrieval")
    (project / "search_conditions.json").write_text(json.dumps({"project_name": "Demo", "description": "Rural triage",
                                                                "platforms": ["openalex"], "search_terms": "triage", "model": model}), encoding="utf-8")
    (project / "pdfs").mkdir(parents=True)
    retrieved = project / "pdfs" / "row1_retrieved.pdf"
    retrieved.write_bytes(b"%PDF-1.4\n")
    papers = [{"id": "open", "title": "Open triage study", "pdf_downloaded": True, "pdf_path": str(retrieved), "retrieval_status": "downloaded"},
              dict(PAPER)]
    if extra_user_pdf:
        supplied = project / "pdfs" / "row3_user_download.pdf"
        supplied.write_bytes(b"%PDF-1.4\n")
        # Feature A: the user's PDF went through the retrieval-retry transaction, which clears the fallback flags.
        papers.append({"id": "user", "title": "User supplied triage study", "pdf_downloaded": True, "pdf_path": str(supplied),
                       "pdf_method": "user_download", "retrieval_status": "downloaded"})
    (project / "filtered").mkdir(parents=True)
    (project / "filtered" / "included_papers.jsonl").write_text("".join(json.dumps(p) + "\n" for p in papers), encoding="utf-8")
    (project / "filtered" / "screening_stats.json").write_text(json.dumps({"included_count": len(papers)}), encoding="utf-8")
    (project / "pdfs" / "download_report.json").write_text(json.dumps({
        "success": len(papers) - 1, "failed": 1, "downloaded": [{"title": p["title"], "path": p["pdf_path"]} for p in papers if p.get("pdf_path")],
        "failed_papers": [{"id": "p1", "title": TITLE, "failure_class": "publisher_paywalled"}]}), encoding="utf-8")
    save_schema_draft(project, SCHEMA)
    finalize_schema(project)
    confirm_rules(project, "sample_size")  # extraction runs only with confirmed coding rules
    patcher = patch.object(ExtractionAgent, "_read_pdf", lambda self, path: "[PDF page 1]\nWe enrolled 120 adults.")
    patcher.start()
    case.addCleanup(patcher.stop)
    return root, project


if __name__ == "__main__":
    unittest.main()
