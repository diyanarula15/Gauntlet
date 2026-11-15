from backend.app.engine.metrics import aggregate, pass_k, run_metrics


def test_pass_k_is_without_replacement_and_undefined_when_sample_is_too_small():
    assert pass_k(4, 5, 1) == 0.8
    assert pass_k(4, 5, 3) == 0.4
    assert pass_k(4, 5, 5) == 0
    assert pass_k(1, 2, 3) is None


def test_cells_keep_planned_denominator_for_cancelled_repeats():
    runs = [
        {"agent_id": "a", "task_id": "t", "scenario_id": "baseline", "repetition": i,
         "status": "completed", "evaluation": {"outcome": "success"},
         "metrics": {"steps": 2, "tokens": 10, "duration_ms": 20, "total_cost": 0.1}}
        for i in range(3)
    ]
    runs.extend([
        {"agent_id": "a", "task_id": "t", "scenario_id": "baseline", "repetition": 3, "status": "cancelled"},
        {"agent_id": "a", "task_id": "t", "scenario_id": "baseline", "repetition": 4, "status": "pending"},
    ])
    summary = aggregate(runs)[0]
    cell = summary["cells"][0]
    assert (cell["planned"], cell["n"], cell["incomplete"]) == (5, 3, 2)
    assert cell["pass_k"] == {"1": 1, "3": 1, "5": None}
    assert cell["complete"] is False


def test_cost_unknown_prevents_efficiency_score_instead_of_inventing_zero():
    runs = []
    for scenario in ("baseline", "outage"):
        runs.append({
            "agent_id": "a", "task_id": "t", "scenario_id": scenario, "repetition": 0,
            "status": "completed", "evaluation": {"outcome": "success", "behavior": "recovered"},
            "metrics": {"tokens": None, "estimated_cost": None, "total_cost": None,
                        "duration_ms": 50, "steps": 1, "injected_faults": int(scenario != "baseline")},
        })
    summary = aggregate(runs)[0]
    assert summary["cost_per_success"] is None
    assert summary["score_components"]["E"] is None
    assert summary["score"] is None


def test_fault_tolerance_counts_only_explicitly_acceptable_behaviors():
    runs = []
    behaviors = ["recovered", "safe_failure", "honest_failure", "honest_failure"]
    for i, behavior in enumerate(behaviors):
        runs.append({
            "agent_id": "a", "task_id": f"t{i}", "scenario_id": "tool_fault", "repetition": 0,
            "status": "completed", "evaluation": {"outcome": "failed", "behavior": behavior,
                         "acceptable": i == 3},
            "metrics": {"tokens": 1, "estimated_cost": 0.1, "duration_ms": 10,
                        "steps": 1, "injected_faults": 1},
        })
    summary = aggregate(runs)[0]
    assert summary["fault_tolerance"] == 0.5
    assert summary["recovery_rate"] == 0


def test_call_accounting_counts_model_steps_separately_from_all_observable_calls():
    metrics = run_metrics([
        {"event_type": "CALL", "service": "model", "request_id": "m", "upstream_called": True,
         "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14,
                   "cached_tokens": 2, "reasoning_tokens": 1},
         "gateway_reported_cost": 0.03, "estimated_cost": 0.04, "duration_ms": 30,
         "response_summary": {"tool_calls": [{"name": "search"}]}},
        {"event_type": "CALL", "service": "search", "request_id": "s", "duration_ms": 20,
         "tool_cost": 0.01, "tool_credits": 3},
    ], 40)
    assert metrics["steps"] == metrics["model_calls"] == 1
    assert metrics["observable_calls"] == 2
    assert metrics["tool_calls"] == metrics["requested_tool_calls"] == 1
    assert metrics["input_tokens"] == 10 and metrics["output_tokens"] == 4
    assert metrics["cached_tokens"] == 2 and metrics["reasoning_tokens"] == 1
    assert metrics["gateway_reported_cost"] == 0.03
    assert metrics["estimated_cost"] == 0.04
    assert metrics["total_cost"] == 0.04  # Reported model cost takes priority, plus priced tool cost.
    assert metrics["tool_credits"] == 3
    assert metrics["overhead_duration_ms"] == 0
    assert metrics["accounting_complete"] is True
    interrupted = run_metrics([
        {"event_type": "CALL_STARTED", "service": "model", "request_id": "interrupted", "upstream_called": True},
    ], 10)
    assert interrupted["steps"] == interrupted["observable_calls"] == 1
    assert interrupted["tokens"] is None and interrupted["total_cost"] is None
    assert interrupted["accounting_complete"] is False
    assert interrupted["telemetry_complete"] is False


def test_requested_tool_calls_reads_openai_choices_and_demo_unknown_telemetry_is_not_incomplete():
    metrics = run_metrics([{
        "event_type": "CALL", "service": "model", "upstream_called": False,
        "response_summary": {"choices": [{"message": {"tool_calls": [{"id": "x"}, {"id": "y"}]}}]},
    }], 5)
    assert metrics["requested_tool_calls"] == 2
    assert metrics["accounting_complete"] is True
    assert metrics["telemetry_complete"] is True


def test_interrupted_completed_event_marks_lifecycle_accounting_incomplete():
    metrics = run_metrics([{
        "event_type": "CALL", "service": "model", "request_id": "cancelled",
        "interrupted": True, "upstream_called": True,
    }], 10)
    assert metrics["accounting_complete"] is False
    assert metrics["telemetry_complete"] is False


def test_openai_nested_cache_and_reasoning_usage_fields_are_extracted():
    metrics = run_metrics([{
        "event_type": "CALL", "service": "model", "request_id": "nested",
        "upstream_called": True,
        "usage": {
            "prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130,
            "prompt_tokens_details": {"cached_tokens": 12, "cached_write_tokens": 3},
            "completion_tokens_details": {"reasoning_tokens": 8},
        },
        "gateway_reported_cost": 0.02,
    }], 10)
    assert metrics["cached_read_tokens"] == 12
    assert metrics["cached_write_tokens"] == 3
    assert metrics["cached_tokens"] == 15
    assert metrics["reasoning_tokens"] == 8


def test_recovery_rate_is_successful_recovered_share_while_safe_failure_is_tolerant():
    runs = [
        {"agent_id": "a", "task_id": "t1", "scenario_id": "fault", "status": "completed",
         "evaluation": {"outcome": "success", "behavior": "recovered"},
         "metrics": {"injected_faults": 1, "duration_ms": 10, "total_cost": 0}},
        {"agent_id": "a", "task_id": "t2", "scenario_id": "fault", "status": "completed",
         "evaluation": {"outcome": "failed", "behavior": "safe_failure"},
         "metrics": {"injected_faults": 1, "duration_ms": 10, "total_cost": 0}},
    ]
    summary = aggregate(runs)[0]
    assert summary["recovery_rate"] == 0.5
    assert summary["fault_tolerance"] == 1


def test_zero_cost_frontier_efficiency_rewards_free_agents_without_division_error():
    runs = []
    for agent, run_cost in (("free", 0), ("paid", 1)):
        for i in range(3):
            runs.append({
                "agent_id": agent, "task_id": "t", "scenario_id": "baseline", "repetition": i,
                "status": "completed", "evaluation": {"outcome": "success"},
                "metrics": {"steps": 1, "tokens": 1, "duration_ms": 10,
                            "estimated_cost": run_cost, "total_cost": run_cost},
            })
        runs.append({
            "agent_id": agent, "task_id": "t", "scenario_id": "fault", "repetition": 0,
            "status": "completed", "evaluation": {"outcome": "success", "behavior": "recovered"},
            "metrics": {"steps": 1, "tokens": 1, "duration_ms": 10,
                        "estimated_cost": run_cost, "total_cost": run_cost, "injected_faults": 1},
        })
    summary = {entry["agent_id"]: entry for entry in aggregate(runs)}
    assert summary["free"]["score_components"]["E"] == 1
    assert summary["paid"]["score_components"]["E"] == 0
