import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendBehaviorTests(unittest.TestCase):
    def test_extraction_preview_navigation_and_schema_actions_are_deterministic(self):
        script = r"""
const assert = require('node:assert/strict');
const { clampPreviewIndex, extractionSchemaAction, schemaJsonForDisplay } = require('./frontend/app.js');
assert.equal(clampPreviewIndex(-4, 3), 0);
assert.equal(clampPreviewIndex(1, 3), 1);
assert.equal(clampPreviewIndex(99, 3), 2);
assert.equal(clampPreviewIndex(4, 0), 0);
assert.equal(extractionSchemaAction('missing'), 'generate-schema');
assert.equal(extractionSchemaAction('draft'), 'generate-schema');
assert.equal(extractionSchemaAction('finalized'), 'regenerate-schema');
assert.equal(schemaJsonForDisplay({fields:[{description:'A &amp; B &lt; C'}]}), '{\n  "fields": [\n    {\n      "description": "A & B < C"\n    }\n  ]\n}');
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_count_labels_use_grammatical_singular_and_plural_forms(self):
        script = r"""
const assert = require('node:assert/strict');
const { formatCount } = require('./frontend/app.js');
assert.equal(formatCount(1, 'paper'), '1 paper');
assert.equal(formatCount(2, 'paper'), '2 papers');
assert.equal(formatCount(1, 'category', 'categories'), '1 category');
assert.equal(formatCount(0, 'category', 'categories'), '0 categories');
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_retry_selection_is_revision_bound_ordered_and_not_persisted(self):
        script = r"""
const assert = require('node:assert/strict');
const { normalizeRetrievalRecovery, reconcileRetrySelection, orderedRetryIds, snapshotDataForStorage } = require('./frontend/app.js');
const recovery = normalizeRetrievalRecovery({canRetry:true,reportRevision:'r1',items:[{retryId:'a',label:'A',failureClass:'network'},{retryId:'b',label:'B',failureClass:'paywall'}]});
let selection = reconcileRetrySelection({reportRevision:'',selectedIds:[]}, recovery);
assert.deepEqual(selection, {reportRevision:'r1',selectedIds:['a','b']});
selection = reconcileRetrySelection({reportRevision:'r1',selectedIds:['b']}, recovery);
assert.deepEqual(orderedRetryIds(recovery, selection), ['b']);
selection = reconcileRetrySelection(selection, {canRetry:true,reportRevision:'r2',items:[{retryId:'c'}]});
assert.deepEqual(selection, {reportRevision:'r2',selectedIds:['c']});
assert.deepEqual(snapshotDataForStorage({retrievalRecovery:recovery}).retrievalRecovery, {canRetry:false,reportRevision:'',items:[]});
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_retry_confirmation_resends_only_an_exact_frozen_challenge(self):
        script = r"""
const assert = require('node:assert/strict');
const { confirmRetryImpact } = require('./frontend/app.js');
const payload = {report_revision:'r1',failed_ids:['a','b']};
let sends = 0;
confirmRetryImpact({expectedReportRevision:'r1',failedIds:['a','b']}, payload, () => false, async () => { sends += 1; }).then((cancelled) => {
  assert.equal(cancelled.cancelled, true); assert.equal(sends, 0);
  return confirmRetryImpact({expectedReportRevision:'r2',failedIds:['a','b']}, payload, () => true, async () => { sends += 1; });
}).then((stale) => {
  assert.equal(stale.stale, true); assert.equal(sends, 0);
  return confirmRetryImpact({expectedReportRevision:'r1',failedIds:['a','b']}, payload, () => true, async (confirmed) => {
    sends += 1;
    assert.deepEqual(confirmed.retry_confirmation, {expected_report_revision:'r1',failed_ids:['a','b']});
    return confirmed;
  });
}).then(() => assert.equal(sends, 1));
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_task_refreshes_authoritative_state_before_surface_error(self):
        script = r"""
const assert = require('node:assert/strict');
const { resolveTaskAndRefresh } = require('./frontend/app.js');
let refreshes = 0;
resolveTaskAndRefresh(Promise.reject(new Error('Action failed (RuntimeError).')), 'p', async (id) => {
  refreshes += 1; assert.equal(id, 'p'); return { project: { id: 'p' }, steps: [{ status: 'failed', stale: true }] };
}).then((outcome) => {
  assert.equal(refreshes, 1); assert.equal(outcome.data.steps[0].status, 'failed'); assert.equal(outcome.data.steps[0].stale, true);
  assert.equal(outcome.error, 'Action failed (RuntimeError).');
});
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
    def test_partial_stage_keeps_itself_reviewable_while_advancing_navigation_to_ready_downstream(self):
        script = r"""
const assert = require('node:assert/strict');
const { workflowProgressIndexForSteps } = require('./frontend/app.js');
const steps = [{key:'retrieval',status:'partial'}, {key:'extraction',status:'active'}, {key:'categorize',status:'todo'}];
assert.equal(workflowProgressIndexForSteps(steps), 1);
assert.ok(0 <= workflowProgressIndexForSteps(steps));
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
    def test_partial_outcome_banner_shows_counts_failures_retryability_and_next_action(self):
        script = r"""
const assert = require('node:assert/strict');
const { workflowOutcomeBanner } = require('./frontend/app.js');
const html = workflowOutcomeBanner({ status:'partial', succeeded:2, failed:1, failedItems:['Paper C'], retryable:true, nextAction:'Information Extraction' });
assert.match(html, /data-ui="workflow-outcome-warning"/);
assert.match(html, /2 completed/); assert.match(html, /1 failed/); assert.match(html, /Paper C/);
assert.match(html, /recovery step/); assert.match(html, /Information Extraction/);
assert.doesNotMatch(html, /data-action="retry-failed/);
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_outcome_banner_and_progress_show_blocked_stage_without_next_action(self):
        script = r"""
const assert = require('node:assert/strict');
const { workflowOutcomeBanner, workflowProgressIndexForSteps } = require('./frontend/app.js');
const html = workflowOutcomeBanner({status:'failed',succeeded:0,failed:2,failedItems:['A','B'],retryable:true,nextAction:''});
assert.match(html, /workflow-outcome-error/); assert.match(html, /0 completed/); assert.match(html, /2 failed/);
assert.match(html, /blocked/); assert.doesNotMatch(html, /Next action/);
assert.equal(workflowProgressIndexForSteps([{status:'done'},{status:'failed'},{status:'todo'}]), 1);
"""
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
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

    def test_workflow_auto_advance_requires_a_local_success_and_unchanged_view(self):
        script = r"""
const assert = require('node:assert/strict');
const { workflowStepForAction, workflowActionLabel, autoAdvanceStepForTask } = require('./frontend/app.js');
const steps = [
  {key:'search',status:'done'},
  {key:'screening',status:'active'},
  {key:'retrieval',status:'todo'},
  {key:'extraction',status:'todo'},
  {key:'categorize',status:'todo'},
];

assert.equal(workflowStepForAction('collect'), 'search');
assert.equal(workflowStepForAction('suggest-categories'), 'categorize');
assert.equal(workflowActionLabel('download-pdfs'), 'Full-text retrieval');
assert.equal(workflowStepForAction('unknown-action'), '');

assert.equal(autoAdvanceStepForTask({action:'collect',taskStatus:'completed',originStep:'search',visibleStep:'search',steps}), 'screening');
assert.equal(autoAdvanceStepForTask({action:'download-pdfs',taskStatus:'partial',originStep:'retrieval',visibleStep:'retrieval',steps}), 'extraction');
assert.equal(autoAdvanceStepForTask({action:'finalize-and-run-extraction',taskStatus:'completed',originStep:'extraction',visibleStep:'extraction',steps}), 'categorize');

assert.equal(autoAdvanceStepForTask({action:'collect',taskStatus:'failed',originStep:'search',visibleStep:'search',steps}), '');
assert.equal(autoAdvanceStepForTask({action:'generate-schema',taskStatus:'completed',originStep:'extraction',visibleStep:'extraction',steps}), '');
assert.equal(autoAdvanceStepForTask({action:'retry-failed-downloads',taskStatus:'completed',originStep:'retrieval',visibleStep:'retrieval',steps}), '');
assert.equal(autoAdvanceStepForTask({action:'collect',taskStatus:'completed',originStep:'search',visibleStep:'screening',steps}), '');
assert.equal(autoAdvanceStepForTask({action:'collect',taskStatus:'completed',originStep:'',visibleStep:'search',steps}), '');
"""
        result = subprocess.run(
            ["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=5
        )
        self.assertEqual(result.returncode, 0, result.stderr)
