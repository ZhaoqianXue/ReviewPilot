"""Coding rules as the Extraction canvas shows them, without a browser or model calls."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agents.lead_agent import LeadAgent
from reviewpilot_core.coding_rules import save_draft
from reviewpilot_core.state_projection import build_rp_data
from reviewpilot_core.workflow_state import complete_action, load_workflow_state, mark_stages_stale, start_action

from tests.test_extraction_agent_finalize import RULES, _project

ROOT = Path(__file__).resolve().parents[1]


def _node(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=10)


class CodingRulesActivityTests(unittest.TestCase):
    def test_confirming_rules_shows_in_extraction_activity_even_when_extraction_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = _project(root)
            save_draft(project, RULES)
            data = build_rp_data(root, project.name)
            self.assertEqual(data["codingRules"]["status"], "draft")
            self.assertEqual(data["quietActions"]["extraction"], "confirm-coding-rules")
            LeadAgent(root).handle_message(project_id=project.name, action="confirm-coding-rules",
                                           input_data={"revision": data["codingRules"]["revision"]})
            mark_stages_stale(project, ["extraction"])
            lines = build_rp_data(root, project.name)["activityByStep"]["extraction"]
            self.assertIn({"tag": "rules", "msg": "Coding rules confirmed. Run Information Extraction when ready."},
                          [{"tag": line["tag"], "msg": line["msg"]} for line in lines])


class CodingRulesCanvasHelpersTests(unittest.TestCase):
    def test_confirm_payload_sends_only_changes_and_drops_fields_left_without_rules(self):
        script = r"""
const assert = require('node:assert/strict');
const { codingRulesDraftFromRules, codingRulesConfirmPayload, workflowActionLabel, autoAdvanceStepForTask } = require('./frontend/app.js');
const saved = {preamble: 'Code what the study reports.', fields: [{field: 'design', rules: ['Name the design.']}]};
let draft = codingRulesDraftFromRules(saved, ['design', 'sample_size']);
assert.deepEqual(draft.fields.map(f => [f.field, f.rules.length]), [['design', 1], ['sample_size', 0]]);
assert.deepEqual(codingRulesConfirmPayload(saved, draft, 'r1'), {payload: {revision: 'r1'}, error: ''});
draft.fields[1].rules.push('  Count   randomized participants. ', '');
assert.deepEqual(codingRulesConfirmPayload(saved, draft, 'r1').payload,
  {revision: 'r1', fields: [{field: 'design', rules: ['Name the design.']}, {field: 'sample_size', rules: ['Count randomized participants.']}]});
draft = codingRulesDraftFromRules(saved, ['design']);
draft.fields[0].rules = [];
draft.preamble = 'Code only what the paper states.';
assert.deepEqual(codingRulesConfirmPayload(saved, draft, 'r1').payload, {revision: 'r1', preamble: 'Code only what the paper states.', fields: []});
draft.fields[0].rules = Array.from({length: 9}, (_, i) => `Rule ${i}`);
assert.match(codingRulesConfirmPayload(saved, draft, 'r1').error, /Design has 9 rules; at most 8/);
draft.preamble = ' ';
assert.equal(codingRulesConfirmPayload(saved, draft, 'r1').error, 'The general coding instructions cannot be empty.');
// Finalizing the schema drafts coding rules; it must not move the researcher on to Categorization.
assert.equal(workflowActionLabel('finalize-and-run-extraction'), 'Finalizing schema and drafting coding rules');
const steps = [{key: 'extraction'}, {key: 'categorize'}];
assert.equal(autoAdvanceStepForTask({action: 'finalize-and-run-extraction', taskStatus: 'completed', originStep: 'extraction', visibleStep: 'extraction', steps}), '');
assert.equal(autoAdvanceStepForTask({action: 'run-extraction', taskStatus: 'completed', originStep: 'extraction', visibleStep: 'extraction', steps}), 'categorize');
"""
        result = _node(script)
        self.assertEqual(result.returncode, 0, result.stderr)


class FinalizeStopsAtCodingRulesTests(unittest.TestCase):
    def test_finalize_that_waits_for_rule_confirmation_leaves_extraction_ready_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(Path(tmp))
            ledger = load_workflow_state(project)
            for name in ("collection", "screening", "retrieval"):
                stage = ledger["stages"][name]
                stage.update(status="completed", attempt=1, last_valid={"status": "completed", "attempt": 1, "updated_at": stage["updated_at"], "counts": {}})
            from reviewpilot_core.workflow_state import save_workflow_state
            save_workflow_state(project, ledger)
            start_action(project, "finalize-and-run-extraction")
            complete_action(project, "finalize-and-run-extraction", {"coding_rules": {"status": "draft"}})
            stage = load_workflow_state(project)["stages"]["extraction"]
            self.assertEqual((stage["status"], stage["error"]), ("ready", None))
            start_action(project, "finalize-and-run-extraction")
            complete_action(project, "finalize-and-run-extraction", {"processed": 1, "errors": 0})
            self.assertEqual(load_workflow_state(project)["stages"]["extraction"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
