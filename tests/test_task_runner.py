import unittest
from threading import Event

from reviewpilot_core.task_runner import TaskConflictError, TaskRunner


class TaskRunnerTests(unittest.TestCase):
    def test_submit_rejects_same_project_while_allowing_another_project(self):
        runner = TaskRunner(max_workers=2)
        first_started = Event()
        release_first = Event()
        other_started = Event()
        rejected_calls = []

        def first():
            first_started.set()
            release_first.wait(timeout=2)

        first_id = runner.submit("demo", "first", first)
        self.assertTrue(first_started.wait(timeout=1))
        for action in ("first", "second"):
            with self.assertRaises(TaskConflictError) as raised:
                runner.submit("demo", action, lambda: rejected_calls.append("ran"))
            self.assertEqual(raised.exception.task["task_id"], first_id)
            self.assertEqual(raised.exception.task["action"], "first")
            self.assertIn("already has running action", str(raised.exception))
            raised.exception.task["status"] = "tampered"
        active_task = runner.active_for_project("demo")
        self.assertEqual(active_task["status"], "running")
        active_task["status"] = "tampered"
        self.assertEqual(runner.active_for_project("demo")["status"], "running")
        self.assertEqual(rejected_calls, [])

        other_id = runner.submit("other", "second", other_started.set)
        self.assertTrue(other_started.wait(timeout=1))
        runner.wait(other_id, timeout=2)
        release_first.set()
        runner.wait(first_id, timeout=2)

    def test_submit_accepts_same_project_after_active_task_completes(self):
        runner = TaskRunner(max_workers=1)

        first_id = runner.submit("demo", "first", lambda: None)
        runner.wait(first_id, timeout=2)
        self.assertIsNone(runner.active_for_project("demo"))
        second_id = runner.submit("demo", "second", lambda: "done")

        self.assertEqual(runner.wait(second_id, timeout=2)["result"], "done")

    def test_submit_records_completed_task_result(self):
        runner = TaskRunner(max_workers=1)

        task_id = runner.submit("demo", "add", lambda: {"value": 3})
        task = runner.wait(task_id, timeout=2)

        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["project_id"], "demo")
        self.assertEqual(task["action"], "add")
        self.assertEqual(task["result"], {"value": 3})

    def test_submit_records_failed_task_error(self):
        runner = TaskRunner(max_workers=1)

        def fail():
            raise RuntimeError("broken")

        task_id = runner.submit("demo", "fail", fail)
        task = runner.wait(task_id, timeout=2)

        self.assertEqual(task["status"], "failed")
        self.assertIn("broken", task["error"])
        retry_id = runner.submit("demo", "retry", lambda: "recovered")
        self.assertEqual(runner.wait(retry_id, timeout=2)["result"], "recovered")


if __name__ == "__main__":
    unittest.main()
