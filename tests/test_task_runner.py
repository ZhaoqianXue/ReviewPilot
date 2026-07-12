import unittest
from threading import Event, Lock
from time import sleep

from reviewpilot_core.task_runner import TaskRunner


class TaskRunnerTests(unittest.TestCase):
    def test_same_project_tasks_run_serially(self):
        runner = TaskRunner(max_workers=2)
        first_started = Event()
        release_first = Event()
        events = []
        events_lock = Lock()

        def record(value):
            with events_lock:
                events.append(value)

        def first():
            record("first-start")
            first_started.set()
            release_first.wait(timeout=2)
            record("first-end")

        def second():
            record("second-start")

        first_id = runner.submit("demo", "first", first)
        self.assertTrue(first_started.wait(timeout=1))
        second_id = runner.submit("demo", "second", second)
        sleep(0.05)
        self.assertNotIn("second-start", events)
        release_first.set()
        runner.wait(first_id, timeout=2)
        runner.wait(second_id, timeout=2)

        self.assertEqual(events, ["first-start", "first-end", "second-start"])

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


if __name__ == "__main__":
    unittest.main()
