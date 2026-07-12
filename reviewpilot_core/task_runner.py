"""Small in-process task registry for local ReviewPilot actions."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from threading import Lock
from uuid import uuid4


class TaskConflictError(RuntimeError):
    def __init__(self, task: dict):
        self.task = dict(task)
        super().__init__(
            f"Project '{task['project_id']}' already has running action '{task['action']}'"
        )


class TaskRunner:
    def __init__(self, max_workers: int = 2):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._tasks: dict[str, dict] = {}
        self._futures: dict[str, Future] = {}
        self._registry_lock = Lock()

    def submit(self, project_id: str, action: str, func) -> str:
        with self._registry_lock:
            active_task = self._active_for_project_unlocked(project_id)
            if active_task is not None:
                raise TaskConflictError(active_task)
            task_id = uuid4().hex
            self._tasks[task_id] = {
                "task_id": task_id,
                "project_id": project_id,
                "action": action,
                "status": "running",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "result": None,
                "error": None,
            }
            future = self._executor.submit(self._run, task_id, func)
            self._futures[task_id] = future
        return task_id

    def get(self, task_id: str) -> dict | None:
        with self._registry_lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def wait(self, task_id: str, timeout: float | None = None) -> dict:
        future = self._futures[task_id]
        future.result(timeout=timeout)
        return self.get(task_id)

    def active_for_project(self, project_id: str) -> dict | None:
        with self._registry_lock:
            task = self._active_for_project_unlocked(project_id)
            return dict(task) if task else None

    def _active_for_project_unlocked(self, project_id: str) -> dict | None:
        return next(
            (
                task
                for task in self._tasks.values()
                if task["project_id"] == project_id and task["status"] == "running"
            ),
            None,
        )

    def _run(self, task_id: str, func) -> None:
        try:
            result = func()
        except Exception as exc:
            with self._registry_lock:
                task = self._tasks[task_id]
                task["status"] = "failed"
                task["error"] = str(exc)
                task["updated_at"] = datetime.now().isoformat()
        else:
            with self._registry_lock:
                task = self._tasks[task_id]
                task["status"] = "completed"
                task["result"] = result
                task["updated_at"] = datetime.now().isoformat()
