import json
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from reviewpilot_core import fulltext_eligibility as fte
from reviewpilot_core.state_projection import build_rp_data
from reviewpilot_core.workflow_state import load_workflow_state, save_workflow_state
from tests.test_fulltext_eligibility import make_project

ROOT = Path(__file__).resolve().parents[1]

RENDER = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('./frontend/app.js', 'utf8');
const slice = (from, to) => source.slice(source.indexOf(from), source.indexOf(to));
const context = vm.createContext({ buttonStyle: '', platformLabel: (key) => ({ pubmed: 'PubMed', arxiv: 'arXiv', openalex: 'OpenAlex' }[key] || key) });
vm.runInContext(slice('  function prismaFlowPanel(', '  function uncategorizedNote('), context);
const flow = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify({ panel: context.prismaFlowPanel(flow), svg: flow ? context.prismaFlowSvg(flow) : '' }));
"""

# The PRISMA flow counts export of the 2026-10-09 LLM4HCI run.
LLM4HCI = {
    "stale_stages": [],
    "identification": {"records_by_source": {"pubmed": 30, "arxiv": 30, "openalex": 30}, "records_identified": 90,
                       "removed_outside_date_range": 0, "removed_duplicates": 9},
    "screening": {"records_screened": 81, "records_excluded": 32, "human_overrides": 0, "reports_sought": 49,
                  "reports_not_retrieved": 8, "not_retrieved_assessed_from_web_sources": 7,
                  "not_retrieved_excluded_from_web_sources": 0, "reports_not_extracted": 0, "reports_assessed": 41,
                  "full_text_eligibility_field": True, "reports_excluded": 3,
                  "reports_excluded_by_reason": {"No human interaction or user aspect is studied.": 1,
                                                 "Review, survey, editorial, commentary or position paper without original empirical results.": 1,
                                                 "not met: The study reports original empirical work: a system evaluation, user study, experiment or deployment.": 1},
                  "reports_unmatched_value": 0, "full_text_corrections": 0},
    "included": {"studies_included": 38, "studies_included_from_web_sources": 7, "studies_categorized": 45},
}


def render(flow):
    result = subprocess.run(["node", "-e", RENDER, json.dumps(flow)], cwd=ROOT, text=True, capture_output=True, timeout=10)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


def text_of(svg):
    """The diagram's words in reading order; wrapped lines join back into their phrases."""
    texts = [" ".join(node.itertext()) for node in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}text")]
    return " ".join(texts).replace("\xa0", " ")


class PrismaFlowRenderTests(unittest.TestCase):
    def test_not_retrieved_stands_alone_when_no_paper_was_assessed_without_full_text(self):
        flow = json.loads(json.dumps(LLM4HCI))
        flow["screening"].update({"reports_not_retrieved": 8, "not_retrieved_assessed_from_web_sources": 0,
                                  "not_retrieved_excluded_from_web_sources": 0, "not_retrieved_eligibility_undetermined": 0,
                                  "not_retrieved_no_evidence": 0, "not_retrieved_not_extracted": 8, "no_full_text_assessed": False})
        flow["included"].update({"studies_included_from_web_sources": 0, "studies_categorized": 38})
        out = render(flow)
        words = text_of(out["svg"])
        self.assertIn("Reports not retrieved (n = 8)", words)
        for absent in ("abstract", "web", "Not extracted", "Eligibility undetermined", "No usable evidence", "Assessed from full text"):
            self.assertNotIn(absent, words)
            self.assertNotIn(absent, out["panel"].split("<svg")[0] + out["panel"].split("</svg>")[-1])
        self.assertIn("Studies included in review (n = 38)", words)

    def test_llm4hci_counts_appear_in_prisma_2020_order(self):
        out = render(LLM4HCI)
        words = text_of(out["svg"])  # parsing also proves the downloadable SVG is well-formed XML
        expected = ["Records identified (n = 90)", "PubMed (n = 30)", "arXiv (n = 30)", "OpenAlex (n = 30)",
                    "Records removed before screening", "Duplicate records (n = 9)", "Outside the date range (n = 0)",
                    "Records screened (n = 81)", "Records excluded (n = 32)",
                    "Reports sought for retrieval (n = 49)", "Reports not retrieved (n = 8)",
                    "Assessed from abstract or web sources (n = 7)", "Excluded at eligibility (n = 0)",
                    "Reports assessed for eligibility (n = 41)", "Reports excluded after full-text assessment (n = 3)",
                    "No human interaction or user aspect is studied. (n = 1)",
                    "Not met: The study reports original empirical work: a system evaluation, user study, experiment or deployment. (n = 1)",
                    "Studies included in review (n = 45)", "Assessed from full text (n = 38)", "Categorized (n = 45)"]
        positions = [words.find(phrase) for phrase in expected]
        self.assertNotIn(-1, positions, [phrase for phrase, at in zip(expected, positions) if at < 0])
        self.assertEqual(positions, sorted(positions))
        for phase in ("Identification", "Screening", "Included"):
            self.assertIn(phase, words)
        # A wrapped box never splits a count across lines.
        for node in ET.fromstring(out["svg"]).iter("{http://www.w3.org/2000/svg}text"):
            line = "".join(node.itertext()).replace("\xa0", " ")
            self.assertNotRegex(line, r"\(n( =)?$|^(= )?\d+\)")
        # Breakdown counts this project does not report are left out rather than derived.
        for absent in ("Eligibility undetermined", "No usable evidence", "Not extracted", "Retrieved but not extracted", "changed by a reviewer"):
            self.assertNotIn(absent, words)
        self.assertNotIn('data-ui="prisma-stale"', out["panel"])
        self.assertIn("Excluded at Eligibility below", out["panel"])
        self.assertIn('data-act="prisma-download" data-format="svg"', out["panel"])
        self.assertIn('data-act="prisma-download" data-format="png"', out["panel"])

    def test_full_not_retrieved_breakdown_notes_and_stale_warning(self):
        flow = json.loads(json.dumps(LLM4HCI))
        flow["stale_stages"] = ["categorization"]
        flow["screening"].update(not_retrieved_assessed_from_web_sources=4, not_retrieved_excluded_from_web_sources=1,
                                 not_retrieved_eligibility_undetermined=1, not_retrieved_no_evidence=2, not_retrieved_not_extracted=1,
                                 reports_not_extracted=2, human_overrides=3, full_text_corrections=1)
        out = render(flow)
        words = text_of(out["svg"])
        for phrase in ("Assessed from abstract or web sources (n = 4)", "Excluded at eligibility (n = 1)", "Eligibility undetermined (n = 1)",
                       "No usable evidence (n = 2)", "Not extracted (n = 1)", "Retrieved but not extracted: 2",
                       "Decisions changed by a reviewer: 3", "Eligibility corrected by a reviewer: 1"):
            self.assertIn(phrase, words)
        self.assertIn('data-ui="prisma-stale"', out["panel"])
        self.assertIn("predate changes to Categorization & Analysis", out["panel"])

    def test_no_counts_no_panel_and_no_web_note_without_web_sources(self):
        self.assertEqual(render(None)["panel"], "")
        flow = json.loads(json.dumps(LLM4HCI))
        flow["screening"]["not_retrieved_assessed_from_web_sources"] = 0
        flow["included"]["studies_included_from_web_sources"] = 0
        out = render(flow)
        self.assertNotIn("Excluded at Eligibility below", out["panel"])
        self.assertIn("Studies included in review (n = 38)", text_of(out["svg"]))
        self.assertNotIn("Assessed from full text", text_of(out["svg"]))


class PrismaFlowProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_projection_is_the_prisma_export_and_follows_its_availability(self):
        project = make_project(self.root)
        self.assertEqual(build_rp_data(self.root, "study")["prismaFlow"], fte.prisma_counts(project))
        state = load_workflow_state(project)
        state["stages"]["extraction"]["stale"] = True
        save_workflow_state(project, state)
        self.assertIsNone(build_rp_data(self.root, "study")["prismaFlow"])  # the export is withheld too

    def test_no_extraction_results_no_flow(self):
        project = make_project(self.root)
        (project / "extraction/extraction_results.jsonl").unlink()
        self.assertIsNone(build_rp_data(self.root, "study")["prismaFlow"])


if __name__ == "__main__":
    unittest.main()
