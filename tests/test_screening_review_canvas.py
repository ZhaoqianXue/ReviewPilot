"""Review guidance and screening evidence as the canvas shows them, without a browser or model calls."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agents.lead_agent import LeadAgent
from reviewpilot_core import record_review as review
from reviewpilot_core.atomic_files import atomic_write_jsonl
from reviewpilot_core.screening_guidance import confirm, save_draft
from reviewpilot_core.state_projection import build_rp_data
from reviewpilot_core.workflow_state import load_workflow_state, mark_stages_stale, save_workflow_state

from tests.test_record_review import make_project
from tests.test_screening_agent import GUIDANCE

ROOT = Path(__file__).resolve().parents[1]


def _node(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=10)


class ScreeningEvidenceRowsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = make_project(Path(self.tmp.name))
        save_draft(self.project, GUIDANCE)
        confirm(self.project, {})
        ledger = load_workflow_state(self.project)  # as if screening then ran with this guidance
        for stage in ledger['stages'].values():
            stage['stale'] = False
        save_workflow_state(self.project, ledger)
        included = review.read_jsonl(self.project / 'filtered/included_papers.jsonl')
        included[0]['screening_evidence'] = {'reason': 'Clinical study.', 'likelihood': 'definitely include', 'criterion': 'Clinical study',
                                             'quote': 'We enrolled 42 adults.', 'uncertain': False, 'quote_verified': True, 'criterion_matched': True}
        excluded = review.read_jsonl(self.project / 'filtered/excluded_papers.jsonl')
        excluded[0]['screening_evidence'] = {'reason': 'Animal work.', 'likelihood': 'probably exclude', 'criterion': '', 'quote': '',
                                             'uncertain': True, 'quote_verified': False, 'criterion_matched': False, 'exclusion_verified': False}
        atomic_write_jsonl(self.project / 'filtered/included_papers.jsonl', included)
        atomic_write_jsonl(self.project / 'filtered/excluded_papers.jsonl', excluded)

    def test_rows_carry_likelihood_and_say_why_an_exclusion_needs_review(self):
        rows = {row['title']: row for row in review.projection(self.project)['screening']}
        kept, flagged = rows['Clinical study of AI'], rows['Animal study']
        self.assertEqual((kept['likelihood'], kept['flag'], kept['uncertain']), ('definitely include', '', False))
        self.assertEqual(flagged['likelihood'], 'probably exclude')
        self.assertTrue(flagged['uncertain'])
        self.assertIn('excerpt was not found', flagged['flag'])
        self.assertIn('not one of the confirmed rules', flagged['flag'])

    def test_confirmed_guidance_rules_are_approved_criteria_for_a_human_decision(self):
        groups = {group['group']: group['rules'] for group in review.projection(self.project)['rules']}
        self.assertEqual(groups['Inclusion criteria'], ['Clinical study'])
        self.assertEqual(groups['Review guidance · exclude when'], GUIDANCE['exclude_when'])
        row = next(r for r in review.projection(self.project)['screening'] if r['title'] == 'Animal study')
        review.save_screening(self.project, {'key': row['key'], 'decision': 'exclude', 'criterion': GUIDANCE['exclude_when'][2],
                                             'reason': 'Checked the abstract', 'revision': review.revision(self.project)})
        saved = review.read_jsonl(self.project / 'filtered/excluded_papers.jsonl')[0]['human_screening']
        self.assertEqual(saved['criterion'], GUIDANCE['exclude_when'][2])
        row = next(r for r in review.projection(self.project)['screening'] if r['title'] == 'Animal study')
        self.assertEqual((row['reviewed'], row['uncertain'], row['flag'], row['likelihood']), (True, False, '', ''))
        with self.assertRaises(ValueError):
            review.save_screening(self.project, {'key': row['key'], 'decision': 'exclude', 'criterion': 'An invented rule',
                                                 'reason': 'x', 'revision': review.revision(self.project)})

    def test_withdrawn_guidance_is_not_an_approved_criterion(self):
        save_draft(self.project, GUIDANCE)  # a redraft withdraws the confirmed guidance from the instruction
        groups = [group['group'] for group in review.approved_rules(self.project)]
        self.assertEqual(groups, ['Inclusion criteria', 'Exclusion criteria'])


class GuidanceActivityTests(unittest.TestCase):
    def test_guidance_steps_appear_in_screening_activity_even_when_screening_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = make_project(root)
            save_draft(project, GUIDANCE)
            revision = build_rp_data(root, 'study')['screeningGuidance']['revision']
            LeadAgent(root).handle_message(project_id='study', action='confirm-screening-guidance', input_data={'revision': revision})
            mark_stages_stale(project, ['screening'])
            self.assertTrue(load_workflow_state(project)['stages']['screening']['stale'])
            lines = build_rp_data(root, 'study')['activityByStep']['screening']
            self.assertIn({'tag': 'guidance', 'msg': 'Review guidance confirmed. Run screening when ready.'},
                          [{'tag': line['tag'], 'msg': line['msg']} for line in lines])
            self.assertEqual([m for m in build_rp_data(root, 'study')['messages'] if 'guidance confirmed' in m['text']], [])


class GuidanceCanvasHelpersTests(unittest.TestCase):
    def test_confirm_payload_sends_only_changed_sections_and_checks_limits(self):
        script = r"""
const assert = require('node:assert/strict');
const { guidanceDraftFromGuidance, guidanceConfirmPayload } = require('./frontend/app.js');
const saved = GUIDANCE;
let draft = guidanceDraftFromGuidance(saved);
assert.deepEqual(guidanceConfirmPayload(saved, null, 'r1'), {payload: {revision: 'r1'}, error: ''});
assert.deepEqual(guidanceConfirmPayload(saved, draft, 'r1'), {payload: {revision: 'r1'}, error: ''});
draft.tie_breakers = [...draft.tie_breakers, '  Keep  surveys of   co-ops. ', ''];
assert.deepEqual(guidanceConfirmPayload(saved, draft, 'r1').payload,
  {revision: 'r1', tie_breakers: [saved.tie_breakers[0], 'Keep surveys of co-ops.']});
draft = guidanceDraftFromGuidance(saved);
draft.include_when = draft.include_when.slice(0, 3);
assert.match(guidanceConfirmPayload(saved, draft, 'r1').error, /Include when the record reports needs 4 to 12 items; it has 3/);
draft = guidanceDraftFromGuidance(saved);
draft.exclude_when = [...draft.exclude_when, draft.exclude_when[0].toUpperCase()];
assert.match(guidanceConfirmPayload(saved, draft, 'r1').error, /lists the same item twice/);
draft = guidanceDraftFromGuidance(saved);
draft.review_focus = '   ';
assert.equal(guidanceConfirmPayload(saved, draft, 'r1').error, 'Review focus cannot be empty.');
""".replace('GUIDANCE', json.dumps(GUIDANCE))
        result = _node(script)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_results_list_shows_evidence_and_leads_to_flagged_records(self):
        script = r"""
const assert = require('node:assert/strict');
global.window = {};
require('./frontend/review.js');
const rows = [
  {key:'a',title:'Kept',abstract:'We enrolled 42 adults.',decision:'include',reason:'Clinical.',criterion:'Clinical study',quote:'We enrolled 42 adults.',likelihood:'definitely include',flag:'',uncertain:false,reviewed:false},
  {key:'b',title:'Dropped',abstract:'Mice only.',decision:'exclude',reason:'Animal work.',criterion:'',quote:'',likelihood:'probably exclude',flag:'Check this exclusion: the excerpt was not found in the title or abstract.',uncertain:true,reviewed:false},
];
const data = {project:{id:'p'},reviewWorkbench:{revision:'r',screening:rows,extraction:[],removed:[],rules:[{group:'Inclusion criteria',rules:['Clinical study']},{group:'Review guidance · exclude when',rules:['animal-only work']}]}};
let paints = 0;
const ui = window.ReviewWorkbench.create({getData:()=>data,paint:()=>{paints++;},setData:()=>{},busy:()=>false});
let html = ui.screening();
assert.match(html, /Definitely include/);
assert.match(html, /Decisive rule:<\/span> Clinical study/);
assert.match(html, /“We enrolled 42 adults\.”/);
assert.match(html, /1 decision needs your review, including 1 exclusion whose excerpt or rule could not be verified/);
assert.match(html, /Needs review \(1\)/);
(async () => {
  await ui.click('review-show-flagged', {dataset:{}});
  html = ui.screening();
  assert.doesNotMatch(html, /needs-review-notice/);
  assert.match(html, /Check this exclusion/);
  assert.doesNotMatch(html, />Kept</);
  await ui.click('review-screen-detail', {dataset:{key:'b'}});
  const dialog = ui.dialog();
  assert.match(dialog, /<optgroup label="Review guidance · exclude when"><option value="animal-only work"/);
  assert.match(dialog, /Probably exclude/);
  const empty = window.ReviewWorkbench.create({getData:()=>({project:{id:'q'},reviewWorkbench:{screening:[],extraction:[],removed:[]}}),paint:()=>{},setData:()=>{},busy:()=>false});
  assert.equal(empty.screening(), '');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        result = _node(script)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
