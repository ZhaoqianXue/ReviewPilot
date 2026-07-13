import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier, Event, Lock, Thread

from reviewpilot_core.task_runner import TaskConflictError, TaskRunner


class TaskRunnerTests(unittest.TestCase):
    def test_structured_workflow_terminal_status_is_not_relabelled_completed(self):
        runner = TaskRunner()
        try:
            partial_id = runner.submit("partial", "collect", lambda: {"status": "partial", "data": {"succeeded": 1, "failed": 1}})
            failed_id = runner.submit("failed", "collect", lambda: {"status": "failed", "data": {"succeeded": 0, "failed": 1}})
            self.assertEqual(runner.wait(partial_id, 2)["status"], "partial")
            failed = runner.wait(failed_id, 2)
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["result"]["data"]["failed"], 1)
        finally:
            runner.shutdown()
    def test_setup_mutation_reserves_only_its_project(self):
        runner = TaskRunner(max_workers=1)
        entered, release = Event(), Event()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(runner.run_if_idle, "one", lambda: (entered.set(), release.wait(2)))
            self.assertTrue(entered.wait(1))
            try:
                with self.assertRaises(TaskConflictError):
                    runner.submit("one", "collect", lambda: None)
                other = runner.submit("two", "collect", lambda: None)
                self.assertIsNotNone(runner.get(other))
            finally:
                release.set()
            future.result(timeout=1)
        runner.shutdown()

    def test_concurrent_setup_mutation_conflicts_and_failed_mutation_releases_reservation(self):
        runner = TaskRunner(max_workers=1)
        entered, release = Event(), Event()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(runner.run_if_idle, "one", lambda: (entered.set(), release.wait(2)))
            self.assertTrue(entered.wait(1))
            with self.assertRaises(TaskConflictError):
                runner.run_if_idle("one", lambda: None)
            release.set()
            future.result(timeout=1)
        with self.assertRaisesRegex(RuntimeError, "broken"):
            runner.run_if_idle("one", lambda: (_ for _ in ()).throw(RuntimeError("broken")))
        self.assertIsNone(runner.active_for_project("one"))
        runner.shutdown()
    def test_submit_prepares_registered_task_before_worker_can_start(self):
        runner = TaskRunner(max_workers=1)
        events = []
        try:
            task_id = runner.submit("demo", "collect", lambda: events.append("work"), prepare=lambda: events.append("prepare"))
            runner.wait(task_id, timeout=2)
        finally:
            runner.shutdown()

        self.assertEqual(events, ["prepare", "work"])

    def test_submit_rolls_back_preparation_when_executor_rejects(self):
        runner = TaskRunner(max_workers=1)
        events = []
        runner._executor.submit = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("rejected"))
        try:
            with self.assertRaisesRegex(RuntimeError, "rejected"):
                runner.submit("demo", "collect", lambda: None, prepare=lambda: events.append("prepare"), rollback=lambda: events.append("rollback"))
        finally:
            runner.shutdown(wait=False)

        self.assertEqual(events, ["prepare", "rollback"])

    def test_shutdown_is_public_and_rejects_new_tasks(self):
        runner = TaskRunner(max_workers=1)
        runner.shutdown()

        with self.assertRaises(RuntimeError):
            runner.submit("demo", "collect", lambda: None)

    def test_task_timestamps_are_browser_parseable_utc_with_milliseconds(self):
        runner = TaskRunner(max_workers=1)
        task_id = runner.submit("demo", "collect", lambda: None)
        task = runner.wait(task_id, timeout=2)

        for field in ("created_at", "updated_at"):
            value = task[field]
            parsed = datetime.fromisoformat(value)
            self.assertRegex(value, r"\.\d{3}\+00:00$")
            self.assertEqual(parsed.utcoffset(), timedelta(0))

    def test_submit_rejects_same_project_while_allowing_another_project(self):
        runner = TaskRunner(max_workers=2)
        first_started = Event()
        release_first = Event()
        other_started = Event()
        rejected_calls = []

        def first():
            first_started.set()
            release_first.wait()

        first_id = None
        try:
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
        finally:
            release_first.set()
            if first_id is not None:
                runner.wait(first_id, timeout=2)

    def test_submit_rolls_back_task_when_executor_rejects_submission(self):
        runner = TaskRunner(max_workers=1)
        original_submit = runner._executor.submit

        def reject_submission(*args, **kwargs):
            raise RuntimeError("executor unavailable")

        try:
            runner._executor.submit = reject_submission
            with self.assertRaisesRegex(RuntimeError, "executor unavailable"):
                runner.submit("demo", "collect", lambda: None)
            self.assertIsNone(runner.active_for_project("demo"))
        finally:
            runner._executor.submit = original_submit

    def test_concurrent_same_project_submitters_accept_exactly_one(self):
        runner = TaskRunner(max_workers=1)
        barrier = Barrier(3)
        release = Event()
        work_started = Event()
        results = []
        results_lock = Lock()
        ran_actions = []

        def work(action):
            ran_actions.append(action)
            work_started.set()
            release.wait()

        def submit(action):
            barrier.wait()
            try:
                task_id = runner.submit("demo", action, lambda: work(action))
                result = ("accepted", action, task_id)
            except TaskConflictError as exc:
                result = ("rejected", action, exc)
            with results_lock:
                results.append(result)

        threads = [Thread(target=submit, args=(action,)) for action in ("first", "second")]
        try:
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(timeout=2)
            self.assertTrue(work_started.wait(timeout=1))
            self.assertEqual([kind for kind, _, _ in results].count("accepted"), 1)
            self.assertEqual([kind for kind, _, _ in results].count("rejected"), 1)
            rejected_action = next(action for kind, action, _ in results if kind == "rejected")
            self.assertNotIn(rejected_action, ran_actions)
        finally:
            release.set()
            for thread in threads:
                thread.join(timeout=2)
            for kind, _, value in results:
                if kind == "accepted":
                    runner.wait(value, timeout=2)

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
        self.assertEqual(task["error"], "Task failed (RuntimeError).")
        retry_id = runner.submit("demo", "retry", lambda: "recovered")
        self.assertEqual(runner.wait(retry_id, timeout=2)["result"], "recovered")


if __name__ == "__main__":
    unittest.main()
