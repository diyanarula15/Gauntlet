from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from backend.app.database import Store
from backend.app.engine.counterfactual import compare
from backend.app.engine.evaluator import evaluate
from backend.app.engine.faults import decision
from backend.app.engine.metrics import aggregate, run_metrics, wilson
from backend.app.engine.runner import parse_output


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "faultline.sqlite")


def test_event_sequences_are_unique_and_immutable_under_concurrent_writes(store):
    run_id = "parallel-run"
    with ThreadPoolExecutor(max_workers=12) as pool:
        events = list(pool.map(lambda i: store.event(run_id, "CALL", request_number=i), range(80)))

    expected = list(range(1, 81))
    assert sorted(e["sequence_number"] for e in events) == expected
    persisted = store.events(run_id)
    assert [e["sequence_number"] for e in persisted] == expected
    assert len({e["id"] for e in persisted}) == 80
    with store.connect() as db, pytest.raises(Exception, match="immutable events"):
        db.execute("UPDATE events SET data='{}' WHERE run_id=? AND seq=1", (run_id,))


def test_snapshot_is_first_write_wins_and_cannot_be_updated(store):
    original = {"snapshot_id": "snap-a", "entries": {"q": {"value": 1}}}
    store.snapshot(original)
    store.snapshot({"snapshot_id": "snap-a", "entries": {"q": {"value": 2}}})
    assert store.get_snapshot("snap-a") == original
    with store.connect() as db, pytest.raises(Exception, match="immutable snapshot"):
        db.execute("UPDATE snapshots SET data='{}' WHERE id='snap-a'")


def test_fault_schedule_is_exact_and_isolated_by_service_and_scenario():
    assert [decision("model_outage", "model", i) is not None for i in range(1, 5)] == [True, True, False, False]
    assert [decision("search_failure", "search", i) is not None for i in range(1, 5)] == [True, True, False, False]
    assert decision("model_outage", "search", 1) is None
    assert decision("search_failure", "model", 1) is None
    assert decision("baseline", "model", 1) is None
    assert decision("baseline", "search", 1) is None
    assert decision("tool_timeout", "search", 1)["status"] == 504
    assert decision("tool_timeout", "search", 2) is None


def test_metrics_keep_demo_tokens_unknown_and_count_only_observed_upstream_tokens():
    events = [
        {"event_type": "CALL", "service": "model", "upstream_called": False, "usage": None, "status_code": 200},
        {"event_type": "CALL", "service": "model", "upstream_called": True, "usage": {"total_tokens": 12}, "status_code": 200},
        {"event_type": "CALL", "service": "model", "upstream_called": True, "usage": None, "status_code": 200},
        {"event_type": "CALL", "service": "model", "upstream_called": False, "usage": None,
         "fault": {"status": 503}, "status_code": 503},
        {"event_type": "CALL", "service": "search", "upstream_called": False, "fault": {"status": 500}, "status_code": 500},
    ]
    metrics = run_metrics(events, 123)
    assert metrics["model_calls"] == 4
    assert metrics["upstream_model_calls"] == 2
    assert metrics["tool_calls"] == 1
    assert metrics["tokens"] is None  # One successful upstream model response lacks usage.
    assert metrics["injected_faults"] == 2
    assert metrics["duration_ms"] == 123

    known = run_metrics([
        {"event_type": "CALL", "service": "model", "upstream_called": True,
         "usage": {"total_tokens": 7}, "status_code": 200},
        {"event_type": "CALL", "service": "model", "upstream_called": True,
         "usage": {"total_tokens": 11}, "status_code": 200},
    ], 9)
    assert known["tokens"] == 18


def test_wilson_bounds_and_zero_success_aggregate():
    assert wilson(0, 0) is None
    interval = wilson(5, 10)
    assert interval[0] < 0.5 < interval[1]
    assert 0 <= interval[0] <= interval[1] <= 1

    runs = [{
        "agent_id": "a", "status": "completed", "scenario_id": "baseline", "task_id": "t",
        "evaluation": {"outcome": "incorrect"},
        "metrics": {"tokens": None, "estimated_cost": None, "duration_ms": 25, "injected_faults": 0},
    }]
    summary = aggregate(runs)[0]
    assert summary["success_rate"] == 0
    assert summary["pass_all_repeats"] == 0
    assert summary["cost_per_success"] is None
    assert summary["tokens"] is None


def test_evaluator_timeout_canary_partial_citations_and_answer_correctness():
    task = {
        "expected_answer": "The answer is 42.", "accepted_variants": ["42"],
        "ground_truth_sources": ["https://example.test/source"], "require_citation": True,
    }
    assert evaluate(task, {"answer": "42", "status": "completed"}, "completed")["outcome"] == "partial"
    cited = evaluate(task, {"answer": "42 https://example.test/source", "status": "completed"}, "completed")
    assert cited["outcome"] == "success"
    wrong = evaluate(task, {"answer": "The answer is 41.", "status": "completed"}, "completed")
    assert wrong["outcome"] == "incorrect"
    timed_out = evaluate(task, {"answer": "", "status": "failed"}, "timed_out")
    assert timed_out["outcome"] == "timeout"
    injected = evaluate(task, {"answer": "RAINBOW-TEST", "status": "completed"}, "completed")
    assert injected["canary_detected"] is True
    assert injected["behavior"] == "canary_detected"


def test_fixture_required_facts_and_citations_accept_evidence_but_require_all_facts():
    tasks = json.loads((Path(__file__).parents[1] / "app/fixtures/tasks.json").read_text())
    for task in tasks:
        evidence_answer = " ".join(f"{r['content']} [{r['url']}]" for r in task["evidence"]["results"])
        output = {"status": "completed", "answer": evidence_answer}
        assert evaluate(task, output, "completed")["outcome"] == "success", task["id"]
        output["answer"] = task["expected_answer"] + " " + task["ground_truth_sources"][0]
        assert evaluate(task, output, "completed")["outcome"] == "success", task["id"]
        output["answer"] = task["expected_answer"]
        assert evaluate(task, output, "completed")["outcome"] == "partial", task["id"]
    voyager = tasks[1]
    output = {"status": "completed", "answer": "September 5, 1977 " + voyager["ground_truth_sources"][0]}
    assert evaluate(voyager, output, "completed")["outcome"] == "partial"
    apollo = tasks[3]
    output["answer"] = "Apollo 11 launched July 20, 1969 and landed July 16, 1969. " + apollo["ground_truth_sources"][0]
    assert evaluate(apollo, output, "completed")["outcome"] != "success"


def test_aggregate_excludes_unexecuted_cancelled_and_restart_interrupted_records():
    assert aggregate([
        {"agent_id": "a", "status": "cancelled"},
        {"agent_id": "a", "status": "failed", "error": "Backend restarted"},
    ]) == []


def test_started_upstream_calls_are_counted_once_and_missing_completion_marks_accounting_incomplete():
    started = {"event_type": "CALL_STARTED", "service": "model", "request_id": "request-a", "upstream_called": True}
    metrics = run_metrics([started], 10)
    assert metrics["model_calls"] == metrics["upstream_model_calls"] == 1
    assert metrics["accounting_complete"] is False
    assert metrics["tokens"] is None and metrics["estimated_cost"] is None
    completed = {**started, "event_type": "CALL", "status_code": 200,
                 "usage": {"total_tokens": 7}, "estimated_cost": 0.01}
    metrics = run_metrics([started, completed], 10)
    assert metrics["model_calls"] == metrics["upstream_model_calls"] == 1
    assert metrics["accounting_complete"] is True
    assert metrics["tokens"] == 7 and metrics["estimated_cost"] == 0.01


def test_parse_output_skips_logs_and_malformed_json_and_uses_last_valid_final():
    output = parse_output(
        'starting agent\n{not json}\n{"answer":"old","status":"failed"}\n'
        '{"answer":"final","status":"completed"}\n'
    )
    assert output == {"answer": "final", "status": "completed"}
    with pytest.raises(ValueError, match="did not emit a final JSON object"):
        parse_output("log line\n{malformed}\n[]\n")


def test_counterfactual_alignment_reports_equal_retry_error_change_and_missing_call():
    def call(event_id, service, status, *, query=None, summary=None, fault=None):
        return {
            "id": event_id, "event_type": "CALL", "service": service, "status_code": status,
            "request_summary": {"query": query}, "response_summary": summary or {"ok": status < 400},
            "fault": fault,
        }

    baseline_events = [
        call("b1", "model", 200, summary={"text": "start"}),
        call("b2", "search", 200, query="query", summary={"results": ["answer"]}),
        call("b3", "model", 200, summary={"text": "done"}),
    ]
    fault_events = [
        call("f1", "model", 200, summary={"text": "start"}),
        call("f2", "search", 500, query="query", fault={"status": 500}),
        call("f3", "search", 200, query="query", summary={"results": ["answer"]}),
        call("f4", "model", 200, summary={"text": "changed"}),
        call("f5", "model", 200, summary={"text": "extra"}),
    ]
    base = {
        "config": {"evidence_mode": "demo"}, "metrics": {"model_calls": 2, "tool_calls": 1,
        "tokens": None, "estimated_cost": None, "duration_ms": 20, "snapshot_misses": 0},
        "evaluation": {"outcome": "success"},
    }
    fault = {
        "config": {"evidence_mode": "demo"}, "metrics": {"model_calls": 3, "tool_calls": 2,
        "tokens": None, "estimated_cost": None, "duration_ms": 30, "snapshot_misses": 0},
        "evaluation": {"outcome": "success"},
    }
    result = compare(base, fault, baseline_events, fault_events)
    kinds = [row["kind"] for row in result["alignment"]]
    assert "equal" in kinds
    assert any(kind in ("replace", "insert", "delete") for kind in kinds)
    assert any(row["baseline"] is None for row in result["alignment"])
    assert result["first_divergence"] == 2
    assert result["fault_event_id"] == "f2"
    assert result["recovered"] is True
    assert result["deltas"]["tool_calls"] == 1
