import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendBehaviorTests(unittest.TestCase):
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
