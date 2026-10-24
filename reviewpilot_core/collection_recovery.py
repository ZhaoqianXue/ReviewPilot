"""Durable, bounded arXiv retries while the local web application is running."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
from threading import Event, Thread
import time

from .atomic_files import atomic_write_json
from .project_store import iter_project_dirs, read_json
from searchers.arxiv_transport import file_lock

RETRY_DELAYS = (60, 300, 900)


def _now():
    return time.time()


def job_path(project):
    return Path(project) / "collected/arxiv.retry.json"


def plan_signature(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()


def update_retry(project, plan, failure, *, reset=False, now=None):
    """Persist a due time, preserving the retry budget across automatic runs."""
    path = job_path(project)
    previous = read_json(path, {}) or {}
    signature = plan_signature(plan)
    attempt = previous.get("attempt", 0) if previous.get("signature") == signature and not reset else 0
    if failure is None or not failure.transient:
        if previous:
            atomic_write_json(path, {**previous, "status": "done" if failure is None else "stopped"})
        return
    if not reset and failure.request_sent is False:
        attempt = max(0, attempt - 1)  # another request's shared cooldown spent no HTTP attempt here
    now = _now() if now is None else now
    exhausted = attempt >= len(RETRY_DELAYS)
    due = None if exhausted else max(now + RETRY_DELAYS[attempt], failure.retry_at or 0)
    atomic_write_json(path, {"signature": signature, "plan": plan, "status": "exhausted" if exhausted else "waiting",
                            "attempt": attempt, "retry_at": due, "error": str(failure)})


def retry_projection(project):
    job = read_json(job_path(project), {}) or {}
    if job.get("status") not in {"waiting", "running", "exhausted", "blocked"}:
        return {}
    due = job.get("retry_at")
    return {"status": job["status"], "attempt": job.get("attempt", 0), "maxAttempts": len(RETRY_DELAYS),
            "retryAt": datetime.fromtimestamp(due, timezone.utc).isoformat(timespec="seconds") if due else None,
            "reason": job.get("reason", "")}


class CollectionRetryScheduler:
    """One small poller per server; a file lock claims each job across workers."""

    def __init__(self, output_root, submit, runner, *, clock=time.time):
        self.root = Path(output_root)
        self.submit = submit
        self.runner = runner
        self.clock = clock
        self._stop = Event()
        self._thread = None

    def start(self):
        self._thread = Thread(target=self._loop, name="arxiv-recovery", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self):
        while not self._stop.wait(5):
            try:
                self.tick()
            except Exception:
                logging.getLogger(__name__).exception("Could not scan pending arXiv collections")

    def tick(self):
        for project in iter_project_dirs(self.root):
            if project.is_symlink() or not job_path(project).exists():
                continue
            try:
                self._run_due(project)
            except BlockingIOError:
                continue  # another process owns this job
            except Exception:
                logging.getLogger(__name__).exception("Could not recover arXiv collection for %s", project.name)

    def _run_due(self, project):
        from agents.collection_agent import retryable_sources
        from .workflow_state import fail_action, load_workflow_state, save_workflow_state

        with file_lock(project / "collected/arxiv.retry.lock", blocking=False):
            job = read_json(job_path(project), {}) or {}
            if job.get("status") not in {"waiting", "running"} or (job.get("retry_at") or 0) > self.clock():
                return
            if self.runner.active_for_project(project.name):
                return
            summary = read_json(project / "collected/summary.json", {}) or {}
            config = read_json(project / "search_conditions.json", {}) or {}
            ledger = load_workflow_state(project)
            collection = ledger["stages"]["collection"]
            interrupted = job.get("status") == "running" and collection["status"] == "running"
            matches = ("arxiv" in retryable_sources(summary, config)
                       and isinstance(job.get("plan"), dict)
                       and job.get("signature") == plan_signature(job["plan"])
                       and all(summary.get(key) == job["plan"].get(key) for key in ("query", "platforms", "date_range", "source_limits")))
            if not matches or (collection["stale"] and not interrupted):
                atomic_write_json(job_path(project), {**job, "status": "cancelled"})
                return
            if interrupted:
                # The file lock was free and this process has no active task:
                # recover a server that exited while its retry was in progress.
                fail_action(project, "collect", RuntimeError("arXiv recovery was interrupted; resuming saved pages."))
                ledger = load_workflow_state(project)
                collection = ledger["stages"]["collection"]
                collection["stale"] = False  # the interrupted rerun used this same saved setup
                save_workflow_state(project, ledger)
            # Do not change the dataset underneath screening or confirmed later work.
            # A researcher can still use the existing explicit failed-source retry.
            if (project / "memory/search_setup_draft.json").exists() or any(
                    stage.get("attempt", 0) for name, stage in ledger["stages"].items() if name != "collection"):
                atomic_write_json(job_path(project), {**job, "status": "blocked", "reason": "Retry arXiv manually after reviewing the saved results."})
                return
            attempt = job.get("attempt", 0) + (job.get("status") != "running")
            if attempt > len(RETRY_DELAYS):
                atomic_write_json(job_path(project), {**job, "status": "exhausted"})
                return
            atomic_write_json(job_path(project), {**job, "status": "running", "attempt": attempt})
            try:
                task = self.submit(self.root, project.name, "collect", input_data={"retry_sources": ["arxiv"]}, background=True)
            except Exception:
                # No HTTP call started; don't spend an attempt on a project conflict.
                atomic_write_json(job_path(project), {**job, "status": "waiting"})
                raise
            # Keep the job lock until the normal project task finishes. This wait
            # runs on the poller thread, not a request or a collection worker.
            while True:
                state = self.runner.get(task)
                if state is None or state["status"] != "running":
                    break
                time.sleep(0.1)
            current = read_json(job_path(project), {}) or {}
            if current.get("status") == "running" and not self._stop.is_set():
                due = self.clock() + RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                atomic_write_json(job_path(project), {**current, "status": "exhausted" if attempt >= len(RETRY_DELAYS) else "waiting", "retry_at": due})
