"""Run scheduled recovery through real project contracts with offline arXiv replies."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import web_app
from agents.collection_agent import CollectionAgent, retryable_sources
from reviewpilot_core.collection_recovery import CollectionRetryScheduler, job_path, retry_projection
from reviewpilot_core.project_store import read_json, read_jsonl
from reviewpilot_core.state_projection import build_rp_data
from reviewpilot_core.task_runner import TaskRunner, TaskConflictError
from reviewpilot_core.workflow_state import complete_action, initialize_workflow_state, load_workflow_state, save_workflow_state, start_action
from searchers import arxiv_transport as transport
from searchers.sources import SourceResult, SourceSearchError, search_source
from test_arxiv_recovery import response, ids


@pytest.fixture
def recovery_project(tmp_path, monkeypatch):
    runner = TaskRunner(max_workers=2)
    monkeypatch.setattr(web_app, "task_runner", runner)
    project = tmp_path / "review"
    project.mkdir()
    config = {"project_name": "Local recovery", "description": "Review AI", "search_terms": "AI",
              "platforms": ["pubmed", "arxiv"], "source_limits": {"pubmed": 2, "arxiv": 150},
              "date_range": {"start": "2023-01-01", "end": "2026-10-01"}}
    (project / "search_conditions.json").write_text(json.dumps(config))
    initialize_workflow_state(project)
    def search(source, query, **kwargs):
        if source == "arxiv":
            return search_source(source, query, **kwargs)
        return SourceResult([{"id": f"{source}-{i}"} for i in range(kwargs["max_results"])], query)
    monkeypatch.setattr("agents.collection_agent.search_source", search)
    start_action(project, "collect")
    with patch.object(transport.requests, "get", side_effect=[response(ids=ids(0, 100)), response(429)]):
        result = CollectionAgent(project).run(config)
    complete_action(project, "collect", result)
    yield project, config, runner
    runner.shutdown()


def run_due(project, runner, clock):
    clock[0] = read_json(job_path(project))["retry_at"] + 1
    # New scheduler instance deliberately exercises recovery from persisted jobs.
    scheduler = CollectionRetryScheduler(project.parent, web_app.submit_project_action, runner, clock=lambda: clock[0])
    scheduler.tick()


def test_background_resume_preserves_other_sources_and_requires_no_llm(recovery_project, isolated_arxiv_transport):
    project, config, runner = recovery_project
    pubmed = (project / "collected/pubmed.jsonl").read_bytes()
    state = build_rp_data(project.parent, project.name)
    assert state["collectionRecovery"]["status"] == "waiting"
    assert state["executedQueries"]["arxiv"]["progress"]["records"] == 100
    with patch.object(transport.requests, "get", return_value=response(ids=ids(100, 150))) as get, \
         patch.object(web_app.LeadAgent, "handle_message", side_effect=AssertionError("Recovery must make no Lead Agent / LLM calls")):
        run_due(project, runner, isolated_arxiv_transport)
    assert get.call_count == 1
    assert get.call_args.kwargs["params"]["start"] == 100
    assert get.call_args.kwargs["params"]["max_results"] == 50
    assert read_json(job_path(project))["status"] == "done"
    assert (project / "collected/pubmed.jsonl").read_bytes() == pubmed
    assert len(read_jsonl(project / "collected/arxiv.jsonl")) == 150
    assert load_workflow_state(project)["stages"]["collection"]["status"] == "completed"
    assert build_rp_data(project.parent, project.name)["platformIssues"] == []


def test_automatic_retry_budget_is_bounded_and_retains_partial_rows(recovery_project, isolated_arxiv_transport):
    project, _config, runner = recovery_project
    with patch.object(transport.requests, "get", return_value=response(429)) as get:
        for attempt in range(1, 4):
            run_due(project, runner, isolated_arxiv_transport)
            job = read_json(job_path(project))
            assert job["attempt"] == attempt
            assert job["status"] == ("exhausted" if attempt == 3 else "waiting")
            if attempt < 3:
                assert job["retry_at"] == isolated_arxiv_transport[0] + (300 if attempt == 1 else 900)
        CollectionRetryScheduler(project.parent, web_app.submit_project_action, runner).tick()
    assert get.call_count == 3
    assert len(read_jsonl(project / "collected/arxiv.jsonl")) == 100
    assert retry_projection(project)["status"] == "exhausted"


@pytest.mark.parametrize("change", ["query", "limit", "screening", "draft"])
def test_changed_setup_or_started_review_prevents_automatic_dataset_changes(recovery_project, isolated_arxiv_transport, change):
    project, config, runner = recovery_project
    if change in {"query", "limit"}:
        config["search_terms"] = "new query" if change == "query" else config["search_terms"]
        if change == "limit":
            config["source_limits"]["arxiv"] = 151
        (project / "search_conditions.json").write_text(json.dumps(config))
    elif change == "screening":
        ledger = load_workflow_state(project)
        ledger["stages"]["screening"]["attempt"] = 1
        save_workflow_state(project, ledger)
    else:
        (project / "memory").mkdir(exist_ok=True)
        (project / "memory/search_setup_draft.json").write_text('{}')
    with patch.object(transport.requests, "get", side_effect=AssertionError("No network request should run")):
        run_due(project, runner, isolated_arxiv_transport)
    assert read_json(job_path(project))["status"] == ("cancelled" if change in {"query", "limit"} else "blocked")
    assert len(read_jsonl(project / "collected/arxiv.jsonl")) == 100


def test_two_schedulers_do_not_run_the_same_job_twice(recovery_project, isolated_arxiv_transport):
    project, _config, runner = recovery_project
    isolated_arxiv_transport[0] = read_json(job_path(project))["retry_at"] + 1
    schedulers = [CollectionRetryScheduler(project.parent, web_app.submit_project_action, runner,
                                         clock=lambda: isolated_arxiv_transport[0]) for _ in range(2)]
    with patch.object(transport.requests, "get", return_value=response(ids=ids(100, 150))) as get, ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda scheduler: scheduler.tick(), schedulers))
    assert get.call_count == 1
    assert read_json(job_path(project))["status"] == "done"


def test_interrupted_background_task_resumes_after_restart(recovery_project, isolated_arxiv_transport):
    project, _config, runner = recovery_project
    job = read_json(job_path(project))
    job.update(status="running", attempt=1)
    job_path(project).write_text(json.dumps(job))
    start_action(project, "collect")  # simulate a process that died before completing this task
    with patch.object(transport.requests, "get", return_value=response(ids=ids(100, 150))) as get:
        run_due(project, runner, isolated_arxiv_transport)
    assert get.call_count == 1, read_json(job_path(project))
    assert read_json(job_path(project))["status"] == "done"
    assert len(read_jsonl(project / "collected/arxiv.jsonl")) == 150


def test_manual_collection_cannot_race_a_job_in_another_worker(recovery_project):
    project, _config, _runner = recovery_project
    with transport.file_lock(project / "collected/arxiv.retry.lock"):
        with pytest.raises(TaskConflictError):
            web_app.submit_project_action(project.parent, project.name, "collect", input_data={"retry_sources": ["arxiv"]})
    assert load_workflow_state(project)["stages"]["collection"]["status"] == "partial"


def test_source_retry_preserves_other_failed_source_errors(tmp_path):
    config = {"search_terms": "AI", "platforms": ["pubmed", "arxiv"], "source_limits": {"pubmed": 2, "arxiv": 2}}
    with patch("agents.collection_agent.search_source", side_effect=SourceSearchError("rejected")):
        CollectionAgent(tmp_path).run(config)
    with patch("agents.collection_agent.search_source", return_value=SourceResult([{"id": "a"}], "AI")):
        result = CollectionAgent(tmp_path).run({**config, "retry_sources": ["arxiv"]})
    assert result["platform_errors"] == {"pubmed": "rejected"}
    assert result["platform_stats"] == {"pubmed": 0, "arxiv": 1}


def test_shared_cooldown_does_not_spend_a_job_attempt_without_sending_http(recovery_project, isolated_arxiv_transport):
    project, _config, runner = recovery_project
    isolated_arxiv_transport[0] = read_json(job_path(project))["retry_at"] + 1
    with patch.object(transport.requests, "get", return_value=response(429)):
        with pytest.raises(transport.ArxivDeferredError):
            transport.arxiv_get(params={"search_query": "another project"}, timeout=1)
    with patch.object(transport.requests, "get", side_effect=AssertionError("Shared cooldown forbids a request")) as get:
        run_due(project, runner, isolated_arxiv_transport)
    job = read_json(job_path(project))
    assert get.call_count == 0
    assert job["attempt"] == 0 and job["status"] == "waiting"


def test_changed_records_with_equal_counts_invalidate_existing_screening(recovery_project, isolated_arxiv_transport):
    project, _config, runner = recovery_project
    ledger = load_workflow_state(project)
    ledger["stages"]["screening"].update(status="completed", attempt=1,
        last_valid={"status": "completed", "attempt": 1, "updated_at": ledger["stages"]["collection"]["updated_at"], "counts": {}})
    save_workflow_state(project, ledger)
    previous = read_json(project / "collected/summary.json")
    rows = [{"id": value, "source": "arxiv"} for value in ids(500, 600)]
    failure = SourceSearchError("arxiv unavailable", previous["executed_queries"]["arxiv"]["query"], records=rows,
                               progress={"records": 100, "limit": 150, "complete": False})
    with patch("agents.collection_agent.search_source", side_effect=failure), \
         patch.object(web_app.LeadAgent, "_ensure_relevance_prompt", return_value=project / "prompts/relevance_prompt.json"), \
         patch.object(web_app.LeadAgent, "_verify_stage_artifacts", return_value=[]):
        task = web_app.submit_project_action(project.parent, project.name, "collect", input_data={"retry_sources": ["arxiv"]})
        result = runner.wait(task, timeout=10)
    assert result["status"] == "partial", result
    assert load_workflow_state(project)["stages"]["screening"]["stale"]


def test_retry_keeps_the_original_open_ended_date_range_after_midnight():
    from datetime import date
    from agents.collection_agent import collection_plan
    original = {"search_terms": "AI", "platforms": ["arxiv"], "source_limits": {"arxiv": 1000},
                "date_range": {"start": "2023-01-01", "end": ""}}
    plan = collection_plan(original)
    summary = {**plan, "platform_errors": {"arxiv": "429"}, "requested_date_range": original["date_range"]}
    class FutureDate(date):
        @classmethod
        def today(cls):
            return cls(2030, 1, 1)
    with patch("reviewpilot_core.publication_dates.date", FutureDate):
        assert retryable_sources(summary, original) == ["arxiv"]
