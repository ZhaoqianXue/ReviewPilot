import unittest

from reviewpilot_core.task_runner import TaskRunner


class TaskRunnerTests(unittest.TestCase):
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
