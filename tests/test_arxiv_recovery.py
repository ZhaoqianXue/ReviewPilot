"""Offline API fault injection: preserve native queries, caps and saved progress."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import format_datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import Mock, patch

import pytest

from agents.collection_agent import CollectionAgent
from reviewpilot_core.project_store import read_json, read_jsonl
from reviewpilot_core.workflow_state import structured_action_outcome
from searchers import arxiv_transport as transport
from searchers.arxiv_search import ArxivSearcher
from searchers.sources import SourceResult, SourceSearchError, search_source


def feed(ids):
    return ('<feed xmlns="http://www.w3.org/2005/Atom">' + ''.join(
        f'<entry><id>http://arxiv.org/abs/{i}</id><title>Paper {i}</title><summary>Evidence</summary>'
        '<published>2024-01-01T00:00:00Z</published></entry>' for i in ids) + '</feed>')


def response(status=200, ids=(), headers=None, text=None):
    return Mock(status_code=status, headers=headers or {}, text=feed(ids) if text is None else text)


def ids(start, stop):
    return [f"2401.{i:05d}v1" for i in range(start, stop)]


def test_daily_cache_uses_exact_parameters_and_never_caches_errors(isolated_arxiv_transport):
    clock = isolated_arxiv_transport
    params = {"search_query": "ti:AI", "start": 0, "max_results": 1, "sortBy": "relevance"}
    with patch.object(transport.requests, "get", return_value=response(ids=ids(0, 1))) as get:
        transport.arxiv_get(params=params, timeout=1)
        cached = transport.arxiv_get(params=dict(reversed(list(params.items()))), timeout=1)
        assert cached.from_cache
        assert get.call_count == 1
        transport.arxiv_get(params={**params, "max_results": 2}, timeout=1)
        assert get.call_count == 2
        clock[0] += 86401
        transport.arxiv_get(params=params, timeout=1)
        assert get.call_count == 3
    for bad in (response(400), response(text="not xml"), response(text=feed([]).replace('</feed>',
                '<entry><id>https://arxiv.org/api/errors#bad</id></entry></feed>'))):
        with patch.object(transport.requests, "get", return_value=bad) as get:
            for _ in range(2):
                transport.arxiv_get(params={"search_query": str(bad)}, timeout=1)
            assert get.call_count == 2


def test_server_cooldown_survives_new_clients_and_does_not_block_workers(isolated_arxiv_transport):
    clock = isolated_arxiv_transport
    with patch.object(transport.requests, "get", return_value=response(429, headers={"Retry-After": "7200"})) as get:
        with pytest.raises(transport.ArxivDeferredError) as first:
            transport.arxiv_get(params={"search_query": "AI"}, timeout=1)
        assert first.value.retry_at == clock[0] + 7200
        with pytest.raises(transport.ArxivDeferredError):
            transport.arxiv_get(params={"search_query": "different project"}, timeout=1)
        assert get.call_count == 1
        assert transport.read_object(transport.state_directory() / "request.json")["retry_at"] == first.value.retry_at


def test_http_date_retry_after_is_not_capped(isolated_arxiv_transport):
    clock = isolated_arxiv_transport
    header = format_datetime(datetime.fromtimestamp(clock[0] + 7200, timezone.utc), usegmt=True)
    with patch.object(transport.requests, "get", return_value=response(503, headers={"Retry-After": header})):
        with pytest.raises(transport.ArxivDeferredError) as error:
            transport.arxiv_get(params={"search_query": "AI"}, timeout=1)
    assert error.value.retry_at >= clock[0] + 7198


def test_concurrent_identical_requests_share_one_network_call():
    def request(*args, **kwargs):
        time.sleep(0.05)
        return response(ids=ids(0, 1))
    with patch.object(transport.requests, "get", side_effect=request) as get, ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: transport.arxiv_get(params={"search_query": "same"}, timeout=1), range(2)))
    assert get.call_count == 1
    assert sum(getattr(item, "from_cache", False) is True for item in results) == 1


def test_two_processes_share_single_connection_and_request_spacing(tmp_path):
    events = tmp_path / "events.jsonl"
    script = '''
import json, sys, time
from searchers import arxiv_transport as a
from requests import Response
a.MIN_INTERVAL = 0.08
def get(*args, **kwargs):
    with open(sys.argv[1], 'a') as f: f.write(json.dumps(['start', time.time()]) + '\\n')
    time.sleep(0.12)
    with open(sys.argv[1], 'a') as f: f.write(json.dumps(['end', time.time()]) + '\\n')
    r = Response(); r.status_code = 200
    r._content = b'<feed xmlns="http://www.w3.org/2005/Atom"/>'
    return r
a.requests.get = get
a.arxiv_get(params={'search_query': sys.argv[2]}, timeout=1)
'''
    children = [subprocess.Popen([sys.executable, "-c", script, str(events), str(i)], env=os.environ.copy()) for i in range(2)]
    for child in children:
        assert child.wait(timeout=15) == 0
    recorded = [json.loads(line) for line in events.read_text().splitlines()]
    assert [item[0] for item in recorded] == ["start", "end", "start", "end"]
    assert recorded[2][1] - recorded[1][1] >= 0.065


def test_resume_keeps_raw_offset_deduplicates_versions_and_obeys_limit(tmp_path, isolated_arxiv_transport):
    clock = isolated_arxiv_transport
    output = str(tmp_path / "arxiv.jsonl")
    kwargs = {"max_results": 150, "output_file": output, "date_range": {"start": "2023-01-01", "end": "2026-10-01"}}
    with patch.object(transport.requests, "get", side_effect=[response(ids=ids(0, 100)), response(429)]) as get:
        with pytest.raises(SourceSearchError) as error:
            search_source("arxiv", 'AI AND "health care"', **kwargs)
    assert len(error.value.records) == 100
    assert error.value.progress["next_start"] == 100
    assert len(read_jsonl(Path(output))) == 100
    clock[0] += 61
    overlap = ["2401.00099v2"] + ids(100, 149)
    with patch.object(transport.requests, "get", side_effect=[response(ids=overlap), response(ids=ids(149, 150))]) as get:
        result = search_source("arxiv", 'AI AND "health care"', **kwargs)
    assert len(result.records) == 150
    assert [call.kwargs["params"]["start"] for call in get.call_args_list] == [100, 150]
    assert [call.kwargs["params"]["max_results"] for call in get.call_args_list] == [50, 1]
    assert result.executed_query == '(((ti:AI OR abs:AI) AND (ti:"health care" OR abs:"health care"))) AND submittedDate:[202301010000 TO 202610012359]'
    assert all(call.kwargs["params"]["sortBy"] == "relevance" and call.kwargs["params"]["sortOrder"] == "descending" for call in get.call_args_list)
    assert result.progress["complete"]


def test_1000_record_search_keeps_original_ten_pages_and_warm_cache():
    def request(*args, **kwargs):
        params = kwargs["params"]
        return response(ids=ids(params["start"], params["start"] + params["max_results"]))
    with patch.object(transport.requests, "get", side_effect=request) as get:
        first = ArxivSearcher().search("AI AND clinical", max_results=1000)
        second = ArxivSearcher().search("AI AND clinical", max_results=1000)
    assert first == second
    assert len(first) == 1000
    assert get.call_count == 10
    assert [call.kwargs["params"]["start"] for call in get.call_args_list] == list(range(0, 1000, 100))
    assert all(call.kwargs["params"]["max_results"] == 100 for call in get.call_args_list)


def test_changed_query_and_limit_cannot_reuse_an_unrelated_checkpoint(tmp_path):
    output = str(tmp_path / "arxiv.jsonl")
    with patch.object(transport.requests, "get", return_value=response(ids=ids(0, 3))):
        assert len(ArxivSearcher().search("old", max_results=3, output_file=output)) == 3
    with patch.object(transport.requests, "get", return_value=response(ids=ids(10, 12))) as get:
        rows = ArxivSearcher().search("new", max_results=2, output_file=output)
    assert [row["id"] for row in rows] == ids(10, 12)
    assert get.call_args.kwargs["params"]["start"] == 0
    assert get.call_args.kwargs["params"]["max_results"] == 2


def test_next_day_revalidates_changed_ranking_before_resuming(tmp_path, isolated_arxiv_transport):
    clock = isolated_arxiv_transport
    output = str(tmp_path / "arxiv.jsonl")
    with patch.object(transport.requests, "get", side_effect=[response(ids=ids(0, 100)), response(429)]):
        with pytest.raises(transport.ArxivDeferredError):
            ArxivSearcher().search("AI", max_results=150, output_file=output)
    clock[0] += 86401
    with patch.object(transport.requests, "get", side_effect=[response(ids=ids(1, 101)), response(ids=ids(101, 151))]) as get:
        result = ArxivSearcher().search("AI", max_results=150, output_file=output)
    assert [row["id"] for row in result] == ids(1, 151)
    assert get.call_count == 2  # validated page zero is then served from its daily cache
    assert Path(output + ".checkpoint.json.previous").exists()


def test_complete_and_empty_checkpoints_expire_after_a_new_day(tmp_path, isolated_arxiv_transport):
    clock = isolated_arxiv_transport
    for initial in (ids(0, 2), []):
        output = str(tmp_path / ("complete.jsonl" if initial else "empty.jsonl"))
        query = "complete query" if initial else "empty query"
        with patch.object(transport.requests, "get", side_effect=[response(ids=initial), response()]):
            ArxivSearcher().search(query, max_results=2, output_file=output)
        clock[0] += 86401
        with patch.object(transport.requests, "get", return_value=response(ids=ids(10, 12))) as get:
            rows = ArxivSearcher().search(query, max_results=2, output_file=output)
        assert get.call_count == 1
        assert [row["id"] for row in rows] == ids(10, 12)


def test_failed_next_day_refresh_retains_the_previous_partial_snapshot(tmp_path, isolated_arxiv_transport):
    clock = isolated_arxiv_transport
    output = str(tmp_path / "arxiv.jsonl")
    with patch.object(transport.requests, "get", side_effect=[response(ids=ids(0, 100)), response(429)]):
        with pytest.raises(SourceSearchError):
            search_source("arxiv", "AI", max_results=150, output_file=output)
    before = Path(output).read_bytes()
    clock[0] += 86401
    with patch.object(transport.requests, "get", return_value=response(429)):
        with pytest.raises(SourceSearchError) as error:
            search_source("arxiv", "AI", max_results=150, output_file=output)
    assert len(error.value.records) == 100
    assert not error.value.progress["complete"]
    assert error.value.progress["revalidating"]
    assert Path(output).read_bytes() == before


@pytest.mark.parametrize("text", ["Rate exceeded.", "<html>Temporary error</html>"])
def test_malformed_200_is_never_checkpointed_as_a_success(tmp_path, text):
    from searchers.http_retry import TransientSourceError
    output = str(tmp_path / "bad.jsonl")
    with patch.object(transport.requests, "get", return_value=response(text=text)):
        with pytest.raises(TransientSourceError):
            ArxivSearcher().search("AI", max_results=10, output_file=output)
    assert not Path(output + ".checkpoint.json").exists()


def test_partial_arxiv_is_published_without_changing_other_source_limits(tmp_path):
    project = tmp_path / "project"
    config = {"search_terms": "AI", "platforms": ["pubmed", "arxiv", "openalex"],
              "source_limits": {"pubmed": 2, "arxiv": 150, "openalex": 3}}
    calls = []
    def search(source, query, **kwargs):
        calls.append((source, kwargs["max_results"]))
        if source == "arxiv":
            return search_source(source, query, **kwargs)
        return SourceResult([{"id": f"{source}-{i}"} for i in range(kwargs["max_results"])], query)
    with patch("agents.collection_agent.search_source", side_effect=search), patch.object(
            transport.requests, "get", side_effect=[response(ids=ids(0, 100)), response(429)]):
        result = CollectionAgent(project).run(config)
    assert calls == [("pubmed", 2), ("arxiv", 150), ("openalex", 3)]
    assert result["platform_stats"] == {"pubmed": 2, "arxiv": 100, "openalex": 3}
    assert len(read_jsonl(project / "collected/arxiv.jsonl")) == 100
    assert sum(len(read_jsonl(path)) for path in (project / "collected").glob("*.jsonl")) == 105
    assert structured_action_outcome("collect", result) == ("partial", {"succeeded": 2, "failed": 1, "collected": 105})
    job = read_json(project / "collected/arxiv.retry.json")
    assert job["status"] == "waiting" and job["attempt"] == 0
    assert job["plan"]["source_limits"] == config["source_limits"]


def test_search_and_all_pdf_metadata_paths_share_the_same_gate(tmp_path, isolated_arxiv_transport):
    from utils.fast_pdf_downloader import FastCascadePDFDownloader
    from utils.pdf_downloader import CascadePDFDownloader
    from utils.oa_sources import arxiv_title_matches
    clock = isolated_arxiv_transport
    starts = []
    def request(*args, **kwargs):
        starts.append(clock[0])
        return response()
    session = Mock()
    session.get.side_effect = request
    title = "Large language models encode clinical knowledge"
    with patch.object(transport.requests, "get", side_effect=request):
        ArxivSearcher().search("AI", max_results=1)
        arxiv_title_matches(session, [title], {})
        downloader = FastCascadePDFDownloader(output_dir=tmp_path / "pdfs", browser_user_data_dir=tmp_path / "browser")
        downloader.session = session
        downloader.oa_index_enabled = False
        downloader._try_arxiv(None, title)
        CascadePDFDownloader._try_arxiv(downloader, None, title)
    assert len(starts) == 4
    assert all(b - a >= 3 for a, b in zip(starts, starts[1:]))
