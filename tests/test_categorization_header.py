import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RENDER = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { formatCount } = require('./frontend/app.js');
const source = fs.readFileSync('./frontend/app.js', 'utf8');
const slice = (from, to) => source.slice(source.indexOf(from), source.indexOf(to));
const context = vm.createContext({
  formatCount, gate: () => 'GATE', emptyHint: (text) => text, evidenceFieldLabel: (name) => name, buttonStyle: '',
  emptyCategorizationWorkflow: () => ({ done: false, metrics: { papersExtracted: 0, fields: 0 } }),
  categorizationSetupPanel: () => 'SETUP', categorizationAnalysisSummary: () => 'SUMMARY', prismaFlowPanel: () => '', runReportPanel: () => '',
});
vm.runInContext(slice('  function categorizeCanvas(v) {', '  function categorizationSetupPanel('), context);
vm.runInContext(slice('  function uncategorizedNote(', '  function exportPackageSection('), context);
vm.runInContext(slice('  function fullResultsTable(', '  function evidenceFieldLabel('), context);
"""


def run_node(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "-e", RENDER + script], cwd=ROOT, text=True, capture_output=True, timeout=10)


class CategorizationHeaderTests(unittest.TestCase):
    def test_header_counts_the_analysis_set_and_lists_full_text_exclusions(self):
        result = run_node(r"""
const workflow = { done: false, metrics: { papersExtracted: 20, papersInAnalysis: 17, fields: 11, papersWithoutCategory: 1 },
  fullTextExclusions: { total: 3, atFullText: 2, fromWebSources: 1,
    reasons: [{ label: 'Not an LLM screening study.', count: 2 }, { label: 'Not met: Systematic reviews.', count: 1 }] } };
const view = { isNewProject: false, categorizationWorkflow: workflow, categorizationSummary: { papers: 16, groups: 4 }, catDraft: {} };
let html = context.categorizeCanvas(view);
assert.match(html, /17 papers · 11 fields/);
assert.match(html, /grid-template-columns:repeat\(3,/);
assert.match(html, /Papers in Analysis<\/div><div[^>]*>17<\/div><div[^>]*>of 20 extracted</);
assert.match(html, /Excluded at Eligibility<\/div><div[^>]*>3</);
assert.match(html, /Not an LLM screening study\.<\/span><span[^>]*>2</);
assert.match(html, /Not met: Systematic reviews\.<\/span><span[^>]*>1</);
assert.match(html, /1 paper assessed from web sources/);
html = context.categorizeCanvas({ ...view, categorizationWorkflow: { ...workflow, done: true } });
assert.match(html, /17 papers · 4 categories/);
assert.doesNotMatch(html, /16 papers/);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_papers_not_assessed_without_full_text_get_their_own_tile_and_note(self):
        result = run_node(r"""
const workflow = { done: false, metrics: { papersExtracted: 48, papersInAnalysis: 38, fields: 5 },
  fullTextExclusions: { total: 3, atFullText: 3, fromWebSources: 0, reasons: [{ label: 'Off topic', count: 3 }] },
  notAssessed: { noEvidence: 7, eligibilityUndetermined: 0 } };
let html = context.categorizeCanvas({ isNewProject: false, categorizationWorkflow: workflow, categorizationSummary: { papers: 0, groups: 0 }, catDraft: {} });
assert.match(html, /grid-template-columns:repeat\(4,/);
assert.match(html, /Papers in Analysis<\/div><div[^>]*>38<\/div><div[^>]*>of 48 extracted</);
assert.match(html, /Not Assessed<\/div><div[^>]*>7<\/div><div[^>]*>no full text</);
assert.match(html, /data-ui="not-assessed-papers"/);
assert.match(html, /No usable evidence<\/span><span[^>]*>7</);
assert.doesNotMatch(html, /Eligibility undetermined/);
html = context.categorizeCanvas({ isNewProject: false, categorizationSummary: { papers: 0, groups: 0 }, catDraft: {},
  categorizationWorkflow: { ...workflow, metrics: { papersExtracted: 10, papersInAnalysis: 8, fields: 5 },
    fullTextExclusions: { total: 0, reasons: [] }, notAssessed: { noEvidence: 0, eligibilityUndetermined: 2 } } });
assert.match(html, /grid-template-columns:repeat\(3,/);
assert.match(html, /of 10 extracted/);
assert.match(html, /Eligibility undetermined<\/span><span[^>]*>2</);
assert.doesNotMatch(html, /Excluded at Eligibility|full-text-exclusions/);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_uncategorized_count_and_not_assessed_cover_only_the_analysis_set(self):
        import json
        import tempfile
        from reviewpilot_core import fulltext_eligibility as fte
        from reviewpilot_core.atomic_files import atomic_write_json
        from reviewpilot_core.state_projection import build_rp_data
        from tests.test_fulltext_eligibility import make_project
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = make_project(root)
            results = project / "extraction/extraction_results.jsonl"
            # A web row written before evidence tiers existed: no usable evidence, so out of the analysis set.
            with results.open("a") as handle:
                handle.write(json.dumps({"paper_id": "p5", "title": "Paper 5", "extraction_source": "web_search_fallback",
                                         "extraction_status": "success", fte.FIELD: "", "participants": None,
                                         "extracted_data": {fte.FIELD: "", "participants": None}}) + "\n")
            # A mapping written under the old rule still lists Paper 5 without a category.
            atomic_write_json(project / "categorization/categorization_mapping.json", {
                "field": "participants", "mode": "multiple", "categories": ["Small"],
                "mapping": {"Paper 1": ["Small"], "Paper 3": [], "Paper 5": []}})
            workflow = build_rp_data(root, "study")["categorizationWorkflow"]
        self.assertEqual(workflow["notAssessed"], {"noEvidence": 1, "eligibilityUndetermined": 0})
        self.assertEqual(workflow["metrics"]["papersWithoutCategory"], 1)  # Paper 3 only

    def test_header_without_exclusions_keeps_two_tiles_and_old_states_still_render(self):
        result = run_node(r"""
const base = { isNewProject: false, categorizationSummary: { papers: 0, groups: 0 }, catDraft: {} };
let html = context.categorizeCanvas({ ...base, categorizationWorkflow: { done: false,
  metrics: { papersExtracted: 5, papersInAnalysis: 5, fields: 2 }, fullTextExclusions: { total: 0, reasons: [] } } });
assert.match(html, /grid-template-columns:repeat\(2,/);
assert.doesNotMatch(html, /Excluded at Eligibility|full-text-exclusions|extracted/);
html = context.categorizeCanvas({ ...base, categorizationWorkflow: { done: false, metrics: { papersExtracted: 4, fields: 2 } } });
assert.match(html, /4 papers · 2 fields/);
assert.match(html, /Papers in Analysis<\/div><div[^>]*>4</);
assert.equal(context.categorizeCanvas({ ...base, categorizationWorkflow: { done: false, metrics: { papersExtracted: 0, fields: 0 } } }), 'GATE');
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_papers_excluded_offers_skip_instead_of_category_generation(self):
        result = run_node(r"""
const workflow = { done: false, metrics: { papersExtracted: 2, papersInAnalysis: 0, fields: 3 },
  fullTextExclusions: { total: 2, atFullText: 2, fromWebSources: 0, reasons: [{ label: 'Animal-only study', count: 2 }] } };
let html = context.categorizeCanvas({ isNewProject: false, categorizationWorkflow: workflow, categorizationSummary: { papers: 0, groups: 0 }, catDraft: {} });
assert.match(html, /0 papers · 3 fields/);
assert.match(html, /nothing to categorize/);
assert.match(html, /data-act="skip-categorization"/);
assert.doesNotMatch(html, /SETUP|SUMMARY/);
html = context.categorizeCanvas({ isNewProject: false, categorizationWorkflow: workflow, categorizationSummary: { papers: 0, groups: 0 }, catDraft: { skipped: true } });
assert.match(html, /SUMMARY/);
assert.doesNotMatch(html, /nothing to categorize/);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_categorized_column_is_labelled_after_its_field(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('./frontend/app.js', 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('  function evidenceFieldLabel('), source.indexOf('  function activityMessage(')), context);
assert.equal(context.evidenceFieldLabel('datasets_used'), 'Datasets');
assert.equal(context.evidenceFieldLabel('datasets_used_category'), 'Datasets Category');
assert.equal(context.evidenceFieldLabel('llm_systems_category'), 'LLM Systems Category');
assert.equal(context.evidenceFieldLabel('category'), 'Category');
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_uncategorized_note_and_capped_full_results_count(self):
        result = run_node(r"""
assert.equal(context.uncategorizedNote(0), '');
assert.equal(context.uncategorizedNote(undefined), '');
assert.match(context.uncategorizedNote(1), /1 paper is in no category/);
assert.match(context.uncategorizedNote(2), /2 papers are in no category/);
const table = { columns: ['title'], rows: [{ title: 'A' }, { title: 'B' }] };
assert.match(context.fullResultsTable({ ...table, total: 2 }), /All 2 results/);
assert.match(context.fullResultsTable(table), /All 2 results/);
assert.match(context.fullResultsTable({ ...table, total: 245 }), /Showing the first 2 of 245 results/);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_prisma_flow_opens_step_five_above_the_header_counts(self):
        result = run_node(r"""
context.prismaFlowPanel = () => 'PRISMA';
context.runReportPanel = () => 'RUNREPORT';
const workflow = { done: false, metrics: { papersExtracted: 20, papersInAnalysis: 20, fields: 5 } };
const view = { isNewProject: false, categorizationWorkflow: workflow, categorizationSummary: { papers: 20, groups: 0 }, catDraft: {} };
let html = context.categorizeCanvas(view);
assert.ok(html.indexOf('Categorization & Analysis') < html.indexOf('PRISMA'));
assert.ok(html.indexOf('PRISMA') < html.indexOf('data-ui="categorization-metrics"'));
assert.ok(html.indexOf('data-ui="categorization-metrics"') < html.indexOf('SETUP'));
assert.ok(html.indexOf('SETUP') < html.indexOf('RUNREPORT'));
html = context.categorizeCanvas({ ...view, categorizationWorkflow: { ...workflow, done: true } });
assert.ok(html.indexOf('PRISMA') < html.indexOf('SUMMARY'));
assert.equal(html.split('PRISMA').length, 2);
""")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
