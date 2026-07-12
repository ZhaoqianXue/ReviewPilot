"""Small in-process task registry for local ReviewPilot actions."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from threading import Lock
from uuid import uuid4


class TaskRunner:
    def __init__(self, max_workers: int = 2):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._tasks: dict[str, dict] = {}
        self._futures: dict[str, Future] = {}
        self._registry_lock = Lock()
        self._project_locks: dict[str, Lock] = {}

    def submit(self, project_id: str, action: str, func) -> str:
        task_id = uuid4().hex
        with self._registry_lock:
            project_lock = self._project_locks.setdefault(project_id, Lock())
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
            future = self._executor.submit(self._run, task_id, func, project_lock)
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

    def _run(self, task_id: str, func, project_lock: Lock) -> None:
        task = self._tasks[task_id]
        with project_lock:
            try:
                result = func()
            except Exception as exc:
                task["status"] = "failed"
                task["error"] = str(exc)
            else:
                task["status"] = "completed"
                task["result"] = result
            finally:
                task["updated_at"] = datetime.now().isoformat()
