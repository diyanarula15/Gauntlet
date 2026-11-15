#!/usr/bin/env python3
"""Run and validate the local FAULTLINE retry/fail-fast demonstration."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_URL = "http://127.0.0.1:8017"
POLL_SECONDS = 0.5
POLL_LIMIT_SECONDS = 120


def request_json(base_url: str, path: str, *, method: str = "GET", body: Any = None) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(base_url.rstrip("/") + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} returned HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not reach FAULTLINE at {base_url}: {exc}") from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_run(run: dict[str, Any], agent: str, scenario: str) -> None:
    events = run.get("events", [])
    calls = [event for event in events if event.get("event_type") == "CALL"]
    model_calls = [event for event in calls if event.get("service") == "model"]
    search_calls = [event for event in calls if event.get("service") == "search"]
    fault_calls = [event for event in calls if event.get("fault")]
    injected_per_run = 2 if agent == "react" else 1
    metrics = run.get("metrics", {})

    require(all(event.get("upstream_called") is False for event in calls),
            f"{agent}/{scenario}: demo CALL unexpectedly reached an upstream service")
    require(metrics.get("tokens") is None,
            f"{agent}/{scenario}: demo token count should be null")

    if scenario == "baseline":
        require(run.get("status") == "completed", f"{agent} baseline did not complete")
        require(run.get("evaluation", {}).get("outcome") == "success",
                f"{agent} baseline did not answer successfully")
        require(not fault_calls, f"{agent} baseline unexpectedly injected a fault")
        require(metrics.get("injected_faults") == 0,
                f"{agent} baseline injected-fault metric should be zero")
        return

    require(len(fault_calls) == injected_per_run,
            f"{agent}/{scenario}: expected exactly {injected_per_run} injected events, got {len(fault_calls)}")
    require(metrics.get("injected_faults") == injected_per_run,
            f"{agent}/{scenario}: injected-fault metric mismatch")

    if agent == "react":
        require(run.get("status") == "completed" and run.get("evaluation", {}).get("outcome") == "success",
                f"react/{scenario} did not recover successfully")
        require(run.get("evaluation", {}).get("behavior") == "recovered",
                f"react/{scenario} was not marked recovered")
        if scenario == "search_failure":
            require((len(search_calls), len(model_calls)) == (3, 2),
                    f"react/search_failure expected 3 search and 2 model calls, got {len(search_calls)} and {len(model_calls)}")
        else:
            require((len(search_calls), len(model_calls)) == (1, 4),
                    f"react/model_outage expected 1 search and 4 model calls, got {len(search_calls)} and {len(model_calls)}")
    else:
        require(run.get("status") == "failed" and run.get("evaluation", {}).get("outcome") == "failed",
                f"sdk/{scenario} should fail fast")
        if scenario == "search_failure":
            require((len(search_calls), len(model_calls)) == (1, 1),
                    f"sdk/search_failure expected 1 search and 1 model call, got {len(search_calls)} and {len(model_calls)}")
        else:
            require((len(search_calls), len(model_calls)) == (0, 1),
                    f"sdk/model_outage expected 0 search and 1 model call, got {len(search_calls)} and {len(model_calls)}")


def check_comparison(base_url: str, run: dict[str, Any], agent: str, scenario: str) -> dict[str, Any]:
    comparison = request_json(base_url, f"/api/runs/{run['id']}/comparison")
    require(comparison.get("comparable_evidence") is True,
            f"{agent}/{scenario}: paired evidence is not comparable")
    recovered = comparison.get("recovered")
    require(recovered is (agent == "react"),
            f"{agent}/{scenario}: unexpected paired recovery value {recovered}")
    deltas = comparison.get("deltas", {})
    expected_model_delta = (0 if scenario == "search_failure" else 2) if agent == "react" else -1
    require(deltas.get("model_calls") == expected_model_delta,
            f"{agent}/{scenario}: unexpected paired model-call delta {deltas.get('model_calls')}")
    expected_tool_delta = (2 if scenario == "search_failure" else 0) if agent == "react" else (-1 if scenario == "model_outage" else 0)
    require(deltas.get("tool_calls") == expected_tool_delta,
            f"{agent}/{scenario}: unexpected paired tool-call delta {deltas.get('tool_calls')}")
    return {"agent_id": agent, "scenario_id": scenario, "repetition": run["repetition"],
            "recovered": recovered, "deltas": deltas,
            "first_divergence": comparison.get("first_divergence")}


def run_demo(base_url: str, output_path: Path) -> dict[str, Any]:
    request_json(base_url, "/api/health")
    created = request_json(base_url, "/api/experiments", method="POST", body={
        "name": "Local retry and fail-fast verification",
        "agent_ids": ["react", "sdk"],
        "task_ids": ["research_001"],
        "scenario_ids": ["baseline", "search_failure", "model_outage"],
        "repetitions": 2,
        "evidence_mode": "demo",
        "snapshot_id": "demo-v1",
        "seed": 42,
    })
    experiment_id = created["id"]
    deadline = time.monotonic() + POLL_LIMIT_SECONDS
    while True:
        current = request_json(base_url, f"/api/experiments/{experiment_id}")
        if current.get("status") in ("completed", "failed", "cancelled"):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Experiment {experiment_id} did not finish within {POLL_LIMIT_SECONDS}s")
        time.sleep(POLL_SECONDS)

    require(current.get("status") == "completed",
            f"Experiment ended with status {current.get('status')}: {current.get('error', '')}")
    runs = current.get("runs", [])
    require(len(runs) == 12, f"Expected 12 run records, got {len(runs)}")
    run_map = {(r["agent_id"], r["scenario_id"], r["repetition"]): r for r in runs}
    require(len(run_map) == 12, "Run records contain duplicate agent/scenario/repetition keys")

    comparisons = []
    for (agent, scenario, repetition), run in sorted(run_map.items()):
        # GET run records carry the event trace; the experiment list itself does not.
        detailed = request_json(base_url, f"/api/runs/{run['id']}")
        check_run(detailed, agent, scenario)
        if scenario != "baseline":
            comparisons.append(check_comparison(base_url, detailed, agent, scenario))

    persisted = request_json(base_url, f"/api/experiments/{experiment_id}")
    require(persisted.get("status") == "completed" and len(persisted.get("runs", [])) == 12,
            "Experiment did not persist with all run records")
    exported = request_json(base_url, f"/api/experiments/{experiment_id}/export")
    traces = exported.get("traces")
    require(isinstance(traces, dict) and len(traces) == 12,
            "Export does not contain all 12 event traces")
    require(set(traces) == {run["id"] for run in runs}, "Export trace IDs do not match experiment run IDs")
    for run_id, trace in traces.items():
        require(isinstance(trace, list) and trace and all(isinstance(event, dict) for event in trace),
                f"Export trace {run_id} is empty or malformed")

    report = {
        "verification": "passed",
        "experiment_id": experiment_id,
        "experiment_status": persisted["status"],
        "config": persisted["config"],
        "run_count": len(runs),
        "trace_count": len(traces),
        "checks": {
            "react_baseline_success": True,
            "sdk_baseline_success": True,
            "react_fault_scenarios_recovered": True,
            "sdk_fault_scenarios_failed_fast": True,
            "demo_upstream_calls": 0,
            "demo_token_counts": None,
            "paired_comparisons": comparisons,
            "experiment_reloaded": True,
            "complete_export_traces": True,
        },
        "traces": traces,
        "methodology": exported.get("methodology"),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {key: value for key, value in report.items() if key != "traces"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="docs/verified-demo.json", help="path for the validated experiment export")
    args = parser.parse_args()
    base_url = os.environ.get("FAULTLINE_URL", DEFAULT_URL)
    try:
        summary = run_demo(base_url, Path(args.output))
    except Exception as exc:
        print(json.dumps({"verification": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved complete trace export to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
