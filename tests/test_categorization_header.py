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
  categorizationSetupPanel: () => 'SETUP', categorizationAnalysisSummary: () => 'SUMMARY',
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
assert.match(html, /Excluded at Full Text<\/div><div[^>]*>3</);
assert.match(html, /Not an LLM screening study\.<\/span><span[^>]*>2</);
assert.match(html, /Not met: Systematic reviews\.<\/span><span[^>]*>1</);
assert.match(html, /1 paper assessed from web sources/);
html = context.categorizeCanvas({ ...view, categorizationWorkflow: { ...workflow, done: true } });
assert.match(html, /17 papers · 4 categories/);
assert.doesNotMatch(html, /16 papers/);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_header_without_exclusions_keeps_two_tiles_and_old_states_still_render(self):
        result = run_node(r"""
const base = { isNewProject: false, categorizationSummary: { papers: 0, groups: 0 }, catDraft: {} };
let html = context.categorizeCanvas({ ...base, categorizationWorkflow: { done: false,
  metrics: { papersExtracted: 5, papersInAnalysis: 5, fields: 2 }, fullTextExclusions: { total: 0, reasons: [] } } });
assert.match(html, /grid-template-columns:repeat\(2,/);
assert.doesNotMatch(html, /Excluded at Full Text|full-text-exclusions|extracted/);
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


if __name__ == "__main__":
    unittest.main()
