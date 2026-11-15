import asyncio
import html
import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.database import Store
from backend.app.engine import adapters
from backend.app.engine.evaluator import evaluate
from backend.app.engine.faults import decision, registry, triggered
from backend.app.main import create_app
from backend.app.services import snapshots
from backend.app.engine.proxy import router


def test_create_app_uses_isolated_store_instances(tmp_path):
    first_store = Store(tmp_path / "first.db")
    second_store = Store(tmp_path / "second.db")
    first = create_app(database=first_store, configuration=Settings(database_path=str(tmp_path / "ignored-first.db")))
    second = create_app(database=second_store, configuration=Settings(database_path=str(tmp_path / "ignored-second.db")))

    with TestClient(first) as first_client, TestClient(second) as second_client:
        assert first_client.get("/api/health").status_code == 200
        assert second_client.get("/api/health").status_code == 200
        first_store.put("marker", {"id": "only-first", "value": 1})
        assert first.state.store is first_store
        assert second.state.store is second_store
        assert first.state.store.get("marker", "only-first") == {"id": "only-first", "value": 1}
        assert second.state.store.get("marker", "only-first") is None


def proxy_app(tmp_path, *, mode="demo", max_steps=1, scenario="baseline", snapshot=None):
    store = Store(tmp_path / "proxy.db")
    settings = Settings(database_path=str(tmp_path / "proxy.db"), max_model_calls=10, max_search_calls=10)
    task = {"id": "task", "query": "known query", "max_steps": max_steps}
    store.put("run", {
        "id": "run", "status": "running", "scenario_id": scenario, "task_id": "task",
        "config": {"evidence_mode": mode, "snapshot_id": "snapshot", "seed": 31},
    })
    if snapshot is not None:
        store.snapshot(snapshot)
    app = __import__("fastapi").FastAPI()
    app.include_router(router(store, settings, {"task": task}, {"run": "capability"}))
    return TestClient(app), store


def test_proxy_model_step_cap_is_independent_of_tool_requests(tmp_path):
    search_payload = {"query": "known query"}
    snapshot = {
        "snapshot_id": "snapshot", "schema_version": 2,
        "entries": {snapshots.request_key("search", search_payload): {
            "operation": "search", "response": {"results": []},
            "response_hash": snapshots.response_hash({"results": []}),
        }},
    }
    client, store = proxy_app(tmp_path, max_steps=1, snapshot=snapshot)
    headers = {"Authorization": "Bearer capability"}

    assert client.post("/proxy/run/tavily/search", json=search_payload, headers=headers).status_code == 200
    assert client.post("/proxy/run/tavily/search", json=search_payload, headers=headers).status_code == 200
    model = {"model": "demo", "messages": [{"role": "user", "content": "hello"}]}
    assert client.post("/proxy/run/v1/chat/completions", json=model, headers=headers).status_code == 200
    capped = client.post("/proxy/run/v1/chat/completions", json=model, headers=headers)
    assert capped.status_code == 429
    assert "step cap" in capped.json()["detail"].lower()
    assert [event["service"] for event in store.events("run") if event["event_type"] == "CALL"] == [
        "search", "search", "model"
    ]


def test_nine_declarative_fault_plans_follow_trigger_and_service_scope():
    plans = registry()
    ids = ("M1", "M2", "M3", "T1", "T2", "T3", "T4", "I1", "I2")
    assert set(ids) <= plans.keys()
    for fault_id in ids:
        plan = plans[fault_id]
        trigger = plan["trigger"]
        index = {"always": 1, "first_n": trigger.get("n", 1), "nth": trigger.get("n", 1)}.get(
            trigger.get("kind"), 1
        )
        service = plan["service"]
        assert decision(fault_id, service, index, seed=17, plan=plan) is not None
        other_service = "search" if service != "search" else "model"
        assert decision(fault_id, other_service, index, seed=17, plan=plan) is None


def test_probability_faults_are_seeded_repeatable_and_run_scoped():
    plan = {
        "service": "search", "trigger": {"kind": "probability", "p": 0.43},
        "effect": {"kind": "http_error", "status": 503, "body": "seeded"},
    }
    for seed in (0, 7, 42, 999):
        expected = triggered(plan["trigger"], 2, seed, "probability-test")
        first = decision("probability-test", "search", 2, seed=seed, plan=plan)
        second = decision("probability-test", "search", 2, seed=seed, plan=plan)
        assert (first is not None) is expected
        assert first == second
        assert decision("probability-test", "model", 2, seed=seed, plan=plan) is None
    assert decision("zero-probability", "search", 2, seed=4, plan={**plan, "trigger": {"kind": "probability", "p": 0}}) is None
    assert decision("certain-probability", "search", 2, seed=4, plan={**plan, "trigger": {"kind": "probability", "p": 1}}) is not None


def test_effective_snapshot_requests_include_full_search_and_extract_parameters():
    search = snapshots.effective_request("search", {
        "query": "  Voyager   1 ", "search_depth": "advanced", "max_results": 9,
        "include_answer": True, "include_raw_content": True, "topic": "news",
        "include_domains": ["z.test", "a.test", "z.test"], "exclude_domains": ["x.test"],
        "time_range": "month", "start_date": "2026-01-01", "end_date": "2026-02-01",
        "country": "us", "language": "en", "chunks_per_source": 4, "include_images": True,
        "include_image_descriptions": True, "include_favicon": True, "auto_parameters": True,
        "exact_match": True, "safe_search": True, "include_published_date": True, "api_key": "drop-me",
    })
    assert set(search) == {
        "query", "search_depth", "max_results", "include_answer", "include_raw_content", "topic",
        "include_domains", "exclude_domains", "time_range", "start_date", "end_date", "country",
        "language", "chunks_per_source", "include_images", "include_image_descriptions", "include_favicon",
        "auto_parameters", "exact_match", "safe_search", "include_published_date",
    }
    assert search["query"] == "voyager 1"
    assert search["include_domains"] == ["a.test", "z.test"]
    extract = snapshots.effective_request("extract", {
        "urls": ["https://example.test/page"], "extract_depth": "advanced", "format": "text",
        "include_images": True, "query": "source", "chunks_per_source": 3, "include_favicon": True,
        "api_key": "drop-me",
    })
    assert extract == {
        "urls": ["https://example.test/page"], "extract_depth": "advanced", "format": "text",
        "include_images": True, "query": "source", "chunks_per_source": 3, "include_favicon": True,
    }


def test_frozen_extract_is_immutable_and_exact_misses_do_not_fallback(tmp_path):
    payload = {"urls": ["https://example.test/page"], "extract_depth": "advanced"}
    response = {"results": [{"url": "https://example.test/page", "raw_content": "captured"}], "failed_results": []}
    entry = {
        "operation": "extract", "request_parameters": snapshots.effective_request("extract", payload),
        "response": response, "response_hash": snapshots.response_hash(response),
    }
    snap = {"snapshot_id": "snapshot", "schema_version": 2,
            "entries": {snapshots.request_key("extract", payload): entry}}
    client, store = proxy_app(tmp_path, mode="frozen", snapshot=snap)
    headers = {"Authorization": "Bearer capability"}

    hit = client.post("/proxy/run/tavily/extract", json=payload, headers=headers)
    assert hit.status_code == 200 and hit.json() == response
    changed = {**payload, "extract_depth": "basic"}
    miss = client.post("/proxy/run/tavily/extract", json=changed, headers=headers)
    assert miss.status_code == 422
    assert store.events("run")[-1]["snapshot_miss"] is True
    assert store.events("run")[-1]["upstream_called"] is False

    returned = snapshots.lookup(store.get_snapshot("snapshot"), "extract", payload)
    returned["response"]["results"][0]["raw_content"] = "mutated copy"
    assert snapshots.lookup(store.get_snapshot("snapshot"), "extract", payload)["response"] == response
    store.snapshot({**snap, "entries": {}})
    assert store.get_snapshot("snapshot")["entries"]
    with store.connect() as db, pytest.raises(Exception, match="immutable snapshot"):
        db.execute("UPDATE snapshots SET data='{}' WHERE id='snapshot'")


def grounding_task():
    return {
        "id": "grounding", "answerable": True, "must_cite": True,
        "ground_truth_sources": ["https://grounded.test/source"],
        "answer_spec": {"type": "exact", "accept": ["the verified answer"]},
    }


def retrieved_event(url, content):
    return {"event_type": "CALL", "service": "search", "status_code": 200,
            "response_summary": {"results": [{"url": url, "content": content}]}}


def test_grounding_rejects_fabricated_citations():
    task = grounding_task()
    output = {"status": "completed", "answer": "The verified answer [https://fabricated.test/page]"}
    result = evaluate(task, output, "completed", events=[retrieved_event("https://grounded.test/source", "The verified answer")])
    assert result["grades"]["answer"]["passed"] is True
    assert result["grades"]["grounding"]["passed"] is False
    assert result["grades"]["grounding"]["unretrieved"] == ["https://fabricated.test/page"]


def test_grounding_rejects_expected_citation_when_nothing_was_retrieved():
    task = grounding_task()
    output = {"status": "completed", "answer": "The verified answer [https://grounded.test/source]"}
    result = evaluate(task, output, "completed", events=[])
    assert result["grades"]["grounding"]["passed"] is False
    assert result["grades"]["grounding"]["retrieved_count"] == 0
    assert result["grades"]["grounding"]["unretrieved"] == ["https://grounded.test/source"]


def test_grounding_rejects_retrieved_source_that_contradicts_the_answer():
    task = grounding_task()
    url = "https://grounded.test/source"
    output = {"status": "completed", "answer": f"The verified answer [{url}]"}
    result = evaluate(task, output, "completed", events=[retrieved_event(url, "The source says the answer is incorrect.")])
    assert result["grades"]["answer"]["passed"] is True
    assert result["grades"]["grounding"]["passed"] is False
    assert result["grades"]["grounding"]["evidence_supports_answer"] is False


def test_http_adapter_sends_task_and_environment_envelope(monkeypatch):
    real_client = httpx.AsyncClient
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"answer": "received", "status": "completed"})

    monkeypatch.setattr(adapters.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs
    ))
    agent = {"adapter": "http", "url": "http://approved-agent.test/run"}
    task = {"task_id": "t", "question": "q", "limits": {"max_seconds": 2}}
    environment = {"OPENAI_BASE_URL": "http://127.0.0.1/proxy/run/v1"}
    result, stderr, code = asyncio.run(adapters.launch(agent, task, environment, lambda _pid: None))
    assert seen == {"url": agent["url"], "payload": {"task": task, "environment": environment}}
    assert result == {"answer": "received", "status": "completed"}
    assert stderr == "" and code == 0


def test_cli_plain_text_and_temporary_task_output_file_contract():
    plain_script = "import sys; sys.stdout.write('Answer from CLI https://source.test/page\\n')"
    plain_agent = {"adapter": "cli", "output_format": "plain_text",
                   "command": ["{python}", "-c", plain_script]}
    plain, _, code = asyncio.run(adapters.launch(plain_agent, {"question": "q"}, {"PATH": "/usr/bin"}, lambda _pid: None))
    assert code == 0
    assert plain["answer"].startswith("Answer from CLI")
    assert plain["citations"] == ["https://source.test/page"]

    file_script = (
        "import json,pathlib,sys; task=pathlib.Path(sys.argv[1]); out=pathlib.Path(sys.argv[2]); "
        "assert task.exists(); payload=json.loads(task.read_text()); "
        "out.write_text(json.dumps({'answer':payload['question'],'status':'completed'}))"
    )
    file_agent = {"adapter": "cli", "output_format": "json_file",
                  "command": ["{python}", "-c", file_script, "{task_file}", "{out_file}"]}
    task = {"question": "read through the temporary task file"}
    file_result, _, file_code = asyncio.run(adapters.launch(file_agent, task, {"PATH": "/usr/bin"}, lambda _pid: None))
    assert file_code == 0
    assert file_result == {"answer": task["question"], "status": "completed"}


def test_cancel_marks_running_and_pending_runs_terminal(tmp_path, monkeypatch):
    import backend.app.main as main

    started = threading.Event()
    cancelled = threading.Event()

    async def blocked_execute(store, settings, run, task, capabilities):
        run["status"] = "running"
        store.put("run", run)
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            run["status"] = "cancelled"
            run["ending"] = "cancelled"
            store.put("run", run)
            cancelled.set()
            raise

    monkeypatch.setattr(main, "execute", blocked_execute)
    store = Store(tmp_path / "cancel.db")
    app = create_app(database=store, configuration=Settings(database_path=str(tmp_path / "cancel.db")))
    with TestClient(app) as client:
        response = client.post("/api/experiments", json={
            "agent_ids": ["react"], "task_ids": ["research_001"],
            "scenario_ids": ["baseline", "T1"], "repetitions": 2, "evidence_mode": "demo",
        })
        assert response.status_code == 202
        experiment = response.json()
        assert started.wait(2)
        cancelled_response = client.post(f"/api/experiments/{experiment['id']}/cancel")
        assert cancelled_response.status_code == 200
        assert cancelled.wait(2)
        current = client.get(f"/api/experiments/{experiment['id']}").json()
        assert current["status"] == "cancelled"
        assert all(run["status"] == "cancelled" for run in current["runs"])


def test_api_sse_json_export_and_html_report_use_factory_store(tmp_path):
    store = Store(tmp_path / "report.db")
    experiment_id = "experiment-contract"
    run_id = "run-contract"
    store.put("experiment", {
        "id": experiment_id, "name": "<Architecture contract>", "status": "completed",
        "config": {"evidence_mode": "demo", "snapshot_id": "demo-contract"},
        "run_ids": [run_id], "total_runs": 1, "completed_runs": 1,
    })
    store.put("run", {
        "id": run_id, "experiment_id": experiment_id, "agent_id": "react", "task_id": "research_001",
        "scenario_id": "baseline", "repetition": 0, "status": "completed", "ending": "completed",
        "evaluation": {"outcome": "success", "behavior": "unknown"},
        "metrics": {"steps": 2, "tokens": 12},
    })
    store.event(run_id, "RUN_STARTED", marker="sse-contract")
    store.event(run_id, "RUN_FINISHED", status="completed")
    app = create_app(database=store, configuration=Settings(database_path=str(tmp_path / "report.db")))

    with TestClient(app) as client:
        sse = client.get(f"/api/runs/{run_id}/events")
        assert sse.status_code == 200
        assert sse.headers["content-type"].startswith("text/event-stream")
        assert '"marker": "sse-contract"' in sse.text

        exported = client.get(f"/api/experiments/{experiment_id}/export")
        assert exported.status_code == 200
        assert exported.headers["content-disposition"] == f"attachment; filename=faultline-{experiment_id}.json"
        assert [event["event_type"] for event in exported.json()["traces"][run_id]] == ["RUN_STARTED", "RUN_FINISHED"]

        report = client.get(f"/api/experiments/{experiment_id}/report.html")
        assert report.status_code == 200
        assert report.headers["content-type"].startswith("text/html")
        assert html.escape("<Architecture contract>") in report.text
        assert "<h1>FAULTLINE — &lt;Architecture contract&gt;</h1>" in report.text


def test_numeric_units_and_uncertain_failures_remain_distinct():
    from backend.app.engine.evaluator import check_answer, evaluate
    task = {'expected_answer':'227.9 million kilometers','answer_spec':{'type':'numeric','value':227.9,'unit':'million kilometers','tolerance':.05}}
    assert check_answer(task,'227.9 million kilometers')[0] is True
    assert check_answer(task,'227.9 dollars')[0] is False
    result = evaluate({'expected_answer':'42'}, {'answer':'Perhaps 41, but I am not sure.','status':'completed'}, 'completed', events=[])
    assert result['outcome']=='incorrect'
    assert result['behavior']=='unknown'
