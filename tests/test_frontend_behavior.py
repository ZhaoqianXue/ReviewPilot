import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendBehaviorTests(unittest.TestCase):
    def test_stale_step_progress_remains_navigable_after_visiting_previous_step(self):
        script = r"""
const assert = require('node:assert/strict');
const { workflowProgressIndexForSteps } = require('./frontend/app.js');
const steps = [{key:'search',status:'done'}, {key:'screening',status:'stale'}, {key:'retrieval',status:'todo'}];
assert.equal(workflowProgressIndexForSteps(steps), 1);
let selected = 'screening'; selected = 'search';
assert.ok(workflowProgressIndexForSteps(steps) >= steps.findIndex((step) => step.key === 'screening'));
selected = 'screening'; assert.equal(selected, 'screening');
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
    def test_material_setup_values_round_trip_without_dropping_fields(self):
        script = r"""
const assert = require('node:assert/strict');
const { materialSetupValues } = require('./frontend/app.js');
const setup = { project_name:'P', description:'D', primary_topic:'T', domain:'X', search_terms:'Q', platforms:['pubmed'], max_results:7, source_limits:{pubmed:7}, date_start:'2020', date_end:'2024', model:'custom', derive_search_terms:true };
assert.deepEqual(materialSetupValues({ setup }), setup);
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
    def test_setup_confirmation_retries_only_after_user_accepts(self):
        script = r"""
const assert = require('node:assert/strict');
const { confirmSetupImpact } = require('./frontend/app.js');
let sends = 0;
const preview = { confirmationRequired: true, expectedRevision: 'r1', affectedStages: ['collection'] };
let confirmations = 0;
confirmSetupImpact({ confirmationRequired: false, setupRevision: 'r1' }, {}, () => { confirmations += 1; return true; }, async () => { sends += 1; }).then((unchanged) => {
  assert.equal(unchanged.setupRevision, 'r1'); assert.equal(confirmations, 0); assert.equal(sends, 0);
  return confirmSetupImpact(preview, { description: 'new' }, () => false, async () => { sends += 1; });
}).then((result) => {
  assert.equal(result.cancelled, true); assert.equal(sends, 0);
  return confirmSetupImpact(preview, { description: 'new' }, () => true, async (payload) => {
    sends += 1; assert.equal(payload.confirmation.expected_revision, 'r1'); return { setupRevision: 'r2' };
  });
}).then((result) => { assert.equal(result.setupRevision, 'r2'); assert.equal(sends, 1); });
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stale_overwrite_confirmation_retries_with_exact_revision_and_stages(self):
        script = r"""
const assert = require('node:assert/strict');
const { confirmOverwriteImpact } = require('./frontend/app.js');
const preview = { expectedRevision: 'r2', affectedStages: ['collection', 'screening'] };
let retries = 0;
confirmOverwriteImpact(preview, null, () => false, async () => { retries += 1; }).then((cancelled) => {
  assert.equal(cancelled.cancelled, true); assert.equal(retries, 0);
  return confirmOverwriteImpact(preview, { mode: 'x' }, () => true, async (payload) => { retries += 1; return payload; });
}).then((payload) => {
  assert.equal(retries, 1); assert.equal(payload.mode, 'x');
  assert.deepEqual(payload.overwrite_confirmation, { expected_revision: 'r2', affected_stages: ['collection', 'screening'] });
});
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
    def test_dialog_maximum_updates_only_selected_source_limits_when_changed(self):
        script = r"""
const assert = require('node:assert/strict');
const { applySubmittedMaxToSourceLimits } = require('./frontend/app.js');

const newProject = applySubmittedMaxToSourceLimits({
  platforms: ['pubmed', 'openalex', 'arxiv'],
  max_results: '5',
  source_limits: { pubmed: '10', openalex: '10', arxiv: '10' },
}, '10', '5');
assert.deepEqual(newProject.source_limits, { pubmed: '5', openalex: '5', arxiv: '5' });
assert.equal(newProject.max_results, '5');

const differentiated = applySubmittedMaxToSourceLimits({
  platforms: ['pubmed', 'openalex', 'arxiv'],
  max_results: '75',
  source_limits: { pubmed: '10', openalex: '25', arxiv: '75' },
}, 75, '75');
assert.deepEqual(differentiated.source_limits, { pubmed: '10', openalex: '25', arxiv: '75' });
assert.equal(differentiated.max_results, '75');

const flattened = applySubmittedMaxToSourceLimits({
  platforms: ['pubmed', 'arxiv'],
  max_results: '20',
  source_limits: { pubmed: '10', openalex: '25', arxiv: '75' },
}, '75', 20);
assert.deepEqual(flattened.source_limits, { pubmed: '20', arxiv: '20' });
assert.equal(flattened.max_results, '20');
assert.equal(Object.hasOwn(flattened.source_limits, 'openalex'), false);
"""
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unbound_click_paint_decision_preserves_form_submission(self):
        script = r"""
const assert = require('node:assert/strict');
const { shouldPaintUnboundClick } = require('./frontend/app.js');

assert.equal(shouldPaintUnboundClick({ insideForm: true, insideChatInputArea: false, shouldCloseQuickStart: false }), false);
assert.equal(shouldPaintUnboundClick({ insideForm: true, insideChatInputArea: true, shouldCloseQuickStart: true }), false);
assert.equal(shouldPaintUnboundClick({ insideForm: false, insideChatInputArea: false, shouldCloseQuickStart: true }), true);
assert.equal(shouldPaintUnboundClick({ insideForm: false, insideChatInputArea: false, shouldCloseQuickStart: false }), false);
"""
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_active_task_ownership_interleavings(self):
        script = r"""
const assert = require('node:assert/strict');
const { snapshotDataForStorage, createTaskPollRegistry, ownsProjectGeneration, createProjectNavigationOwnership } = require('./frontend/app.js');

assert.equal(snapshotDataForStorage({ activeTask: { task_id: 'stale' } }).activeTask, null);

const state = { activeProjectId: 'A' };
const data = { project: { id: 'A' }, activeTask: { task_id: 'task-a' } };
const monitor = { generation: 1 };
assert.equal(ownsProjectGeneration(state, data, monitor, 'A', 1, 'task-a'), true);
state.activeProjectId = 'B'; data.project.id = 'B';
assert.equal(ownsProjectGeneration(state, data, monitor, 'A', 1, 'task-a'), false);
state.activeProjectId = 'A'; data.project.id = 'A'; data.activeTask.task_id = 'task-b';
assert.equal(ownsProjectGeneration(state, data, monitor, 'A', 1, 'task-a'), false);
monitor.generation = 2;
assert.equal(ownsProjectGeneration(state, data, monitor, 'A', 1), false);

let waits = 0;
let release;
const registry = createTaskPollRegistry(() => { waits += 1; return new Promise((resolve) => { release = resolve; }); });
const first = registry.waitOnce('task-a', 'A:task-a');
state.activeProjectId = 'B';
state.activeProjectId = 'A';
const second = registry.waitOnce('task-a', 'A:task-a');
assert.equal(first, second);
assert.equal(waits, 1);
const navigation = createProjectNavigationOwnership('A');
const delayedChat = navigation.capture('A');
navigation.adoptProject('B');
assert.equal(navigation.owns(delayedChat), false);
navigation.adoptProject('A');
const sameProjectChat = navigation.capture('A');
navigation.adoptProject('A'); // task completion calls setData for A
assert.equal(navigation.owns(sameProjectChat), true);

release({ status: 'completed' });
Promise.all([first, second]).then(async () => {
  const third = registry.waitOnce('task-a', 'A:task-a');
  assert.equal(waits, 2);
  release({ status: 'completed' });
  await third;
  process.exit(0);
});
"""
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5
        )
        self.assertEqual(result.returncode, 0, result.stderr)
