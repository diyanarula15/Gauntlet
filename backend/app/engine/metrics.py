from collections import defaultdict
from math import comb, sqrt
from statistics import median


def wilson(successes, total):
    if not total:
        return None
    z = 1.96
    p = successes / total
    d = 1 + z * z / total
    center = (p + z * z / (2 * total)) / d
    half = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / d
    return [max(0, center - half), min(1, center + half)]


def pass_k(successes, total, k):
    """Unbiased without-replacement pass^k estimator, undefined for n < k."""
    if total < k:
        return None
    return comb(successes, k) / comb(total, k) if successes >= k else 0.0


def _sum_known(rows, getter):
    values = [getter(row) for row in rows]
    return sum(values) if values and all(value is not None for value in values) else None


def _usage_value(usage, *keys):
    if not isinstance(usage, dict):
        return None
    for key in keys:
        if usage.get(key) is not None:
            return usage[key]
    return None


def _nested_usage_value(usage, section, *keys):
    if not isinstance(usage, dict):
        return None
    return _usage_value(usage.get(section), *keys)


def run_metrics(events, duration_ms):
    """Summarize proxy events. Model calls are steps; every proxy call is observable."""
    started = {}
    calls = []
    completed_ids = set()
    for event in events:
        event_type = event.get('event_type')
        request_id = event.get('request_id')
        if event_type == 'CALL_STARTED':
            started[request_id or f'__start_{len(started)}'] = event
        elif event_type == 'CALL':
            calls.append(event)
            if request_id is not None:
                completed_ids.add(request_id)
    interrupted = [e for rid, e in started.items() if rid not in completed_ids]
    interrupted_completions = [e for e in calls if e.get('interrupted')]
    # Keep a started call only when no completion with the same request ID exists.
    observed = calls + interrupted
    model = [e for e in observed if e.get('service') == 'model']
    tool = [e for e in observed if e.get('service') in ('search', 'extract')]
    upstream_model = [e for e in model if e.get('upstream_called')]
    usage_rows = upstream_model

    def token_total(e):
        u = e.get('usage') or {}
        return _usage_value(u, 'total_tokens')

    def token_sum(e, *keys):
        return _usage_value(e.get('usage'), *keys)

    prompt_tokens = _sum_known(usage_rows, lambda e: token_sum(e, 'prompt_tokens', 'input_tokens'))
    completion_tokens = _sum_known(usage_rows, lambda e: token_sum(e, 'completion_tokens', 'output_tokens'))
    def cached_read(e):
        usage = e.get('usage') or {}
        return (_usage_value(usage, 'cached_read_tokens', 'cached_tokens', 'cache_read_input_tokens')
                if _usage_value(usage, 'cached_read_tokens', 'cached_tokens', 'cache_read_input_tokens') is not None
                else _nested_usage_value(usage, 'prompt_tokens_details', 'cached_tokens', 'cached_read_tokens'))

    def cached_write(e):
        usage = e.get('usage') or {}
        return (_usage_value(usage, 'cached_write_tokens', 'cache_creation_input_tokens')
                if _usage_value(usage, 'cached_write_tokens', 'cache_creation_input_tokens') is not None
                else _nested_usage_value(usage, 'prompt_tokens_details', 'cached_write_tokens'))

    cached_read_tokens = _sum_known(usage_rows, cached_read)
    cached_write_tokens = _sum_known(usage_rows, cached_write)
    cached_parts = [v for v in (cached_read_tokens, cached_write_tokens) if v is not None]
    cached_tokens = sum(cached_parts) if cached_parts else None
    reasoning_tokens = _sum_known(usage_rows, lambda e: (
        token_sum(e, 'reasoning_tokens') if token_sum(e, 'reasoning_tokens') is not None
        else _nested_usage_value(e.get('usage'), 'completion_tokens_details', 'reasoning_tokens')
    ))
    total_tokens = _sum_known(usage_rows, token_total)
    if total_tokens is None and usage_rows:
        totals = [token_sum(e, 'prompt_tokens', 'input_tokens') for e in usage_rows]
        outputs = [token_sum(e, 'completion_tokens', 'output_tokens') for e in usage_rows]
        if all(v is not None for v in totals + outputs):
            total_tokens = sum(totals) + sum(outputs)

    reported_costs = [e.get('gateway_reported_cost', (e.get('gateway_metadata') or {}).get('reported_cost')) for e in upstream_model]
    estimates = [e.get('estimated_cost') for e in upstream_model if e.get('event_type') == 'CALL']
    gateway_reported_cost = sum(reported_costs) if reported_costs and all(v is not None for v in reported_costs) else None
    estimated_cost = sum(estimates) if estimates and all(v is not None for v in estimates) else None
    model_cost_rows = []
    for event in upstream_model:
        if event.get('event_type') != 'CALL':
            model_cost_rows.append(None)
        else:
            model_cost_rows.append(event.get('gateway_reported_cost', (event.get('gateway_metadata') or {}).get('reported_cost')))
    if all(value is not None for value in model_cost_rows) and model_cost_rows:
        model_cost = sum(model_cost_rows)
        cost_source = 'reported'
    elif estimates and len(estimates) == len(model_cost_rows) and all(value is not None for value in estimates):
        model_cost = sum(estimates)
        cost_source = 'estimated'
    else:
        model_cost = None
        cost_source = 'unknown'

    tool_cost_values = [e.get('tool_cost', e.get('cost_usd')) for e in tool]
    tool_credits_values = [e.get('tool_credits', e.get('credits')) for e in tool]
    tool_cost = sum(tool_cost_values) if tool_cost_values and all(v is not None for v in tool_cost_values) else (0 if not tool else None)
    tool_credits = sum(tool_credits_values) if tool_credits_values and all(v is not None for v in tool_credits_values) else (0 if not tool else None)
    total_cost = model_cost + tool_cost if model_cost is not None and tool_cost is not None else None

    model_duration = _sum_known([e for e in model if e.get('event_type') == 'CALL'], lambda e: e.get('duration_ms'))
    tool_duration = _sum_known([e for e in tool if e.get('event_type') == 'CALL'], lambda e: e.get('duration_ms'))
    if any(e.get('service') == 'model' for e in interrupted):
        model_duration = None
    if any(e.get('service') in ('search', 'extract') for e in interrupted):
        tool_duration = None
    model_duration = model_duration if model_duration is not None else (0 if not model else None)
    tool_duration = tool_duration if tool_duration is not None else (0 if not tool else None)
    overhead = (max(0, duration_ms - model_duration - tool_duration)
                if duration_ms is not None and model_duration is not None and tool_duration is not None else None)
    requested = 0
    for event in model:
        if event.get('event_type') != 'CALL':
            continue
        response = event.get('response_summary') or {}
        tool_calls = response.get('tool_calls') or []
        choices = response.get('choices') or []
        if not tool_calls and choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            message = first.get('message') or {}
            tool_calls = message.get('tool_calls') or []
        requested += len(tool_calls)
    lifecycle_complete = not interrupted and not interrupted_completions
    telemetry_complete = lifecycle_complete and (not upstream_model or (
        total_tokens is not None and model_cost is not None and tool_cost is not None
    ))
    return dict(
        model_calls=len(model), steps=len(model), tool_calls=len(tool), observable_calls=len(observed),
        requested_tool_calls=requested, model_tool_calls_requested=requested,
        upstream_model_calls=len(upstream_model), tokens=total_tokens, total_tokens=total_tokens,
        input_tokens=prompt_tokens, output_tokens=completion_tokens, cached_tokens=cached_tokens,
        cached_read_tokens=cached_read_tokens, cached_write_tokens=cached_write_tokens,
        reasoning_tokens=reasoning_tokens, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        model_duration_ms=model_duration, tool_duration_ms=tool_duration, overhead_duration_ms=overhead,
        gateway_reported_cost=gateway_reported_cost, estimated_cost=estimated_cost,
        model_cost=model_cost, tool_cost=tool_cost, tool_credits=tool_credits, total_cost=total_cost,
        cost_source=cost_source, cost_status=cost_source,
        accounting_complete=lifecycle_complete, telemetry_complete=telemetry_complete, duration_ms=duration_ms,
        injected_faults=sum(bool(e.get('fault')) for e in observed),
        snapshot_misses=sum(bool(e.get('snapshot_miss')) for e in observed),
    )


def _cv(values):
    if not values:
        return None
    mean = sum(values) / len(values)
    return sqrt(sum((v - mean) ** 2 for v in values) / len(values)) / mean if mean else 0.0


def _percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    pos = (len(values) - 1) * p
    lower = int(pos)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (pos - lower)


def _scored(run):
    return (run.get('status') not in ('pending', 'running', 'cancelled')
            and isinstance(run.get('metrics'), dict)
            and isinstance(run.get('evaluation'), dict))


def _cell(task_id, scenario_id, planned_runs):
    planned = len(planned_runs)
    scored_runs = [r for r in planned_runs if _scored(r)]
    outcomes = [r.get('evaluation', {}).get('outcome') == 'success' for r in scored_runs]
    successes = sum(outcomes)
    durations = [r['metrics'].get('duration_ms') for r in scored_runs if r['metrics'].get('duration_ms') is not None]
    tokens = [r['metrics'].get('tokens') for r in scored_runs if r['metrics'].get('tokens') is not None]
    steps = [r['metrics'].get('steps', r['metrics'].get('model_calls')) for r in scored_runs]
    steps = [v for v in steps if v is not None]
    costs = [r['metrics'].get('total_cost', r['metrics'].get('estimated_cost')) for r in scored_runs]
    known_costs = [v for v in costs if v is not None]
    return dict(
        task_id=task_id, scenario_id=scenario_id, planned=planned, n=len(scored_runs),
        incomplete=planned - len(scored_runs), complete=planned == len(scored_runs),
        successes=successes, success_rate=successes / len(scored_runs) if scored_runs else None,
        confidence_interval=wilson(successes, len(scored_runs)),
        pass_k={str(k): pass_k(successes, len(scored_runs), k) for k in (1, 3, 5)},
        pass_1=pass_k(successes, len(scored_runs), 1), pass_3=pass_k(successes, len(scored_runs), 3),
        pass_5=pass_k(successes, len(scored_runs), 5),
        cost_per_success=sum(known_costs) / successes if successes and len(known_costs) == len(scored_runs) else None,
        median_latency_ms=median(durations) if durations else None,
        p95_latency_ms=_percentile(durations, .95),
        coefficient_variation=dict(
            steps=_cv(steps) if len(steps) == len(scored_runs) else None,
            tokens=_cv(tokens) if len(tokens) == len(scored_runs) else None,
            duration_ms=_cv(durations) if len(durations) == len(scored_runs) else None,
        ),
    )


def aggregate(runs):
    """Aggregate planned run records into agent/task/scenario cells and summaries."""
    by_agent = defaultdict(list)
    for run in runs:
        if run.get('agent_id') and run.get('task_id') and run.get('scenario_id'):
            by_agent[run['agent_id']].append(run)

    result = []
    for agent, agent_runs in by_agent.items():
        groups = defaultdict(list)
        for run in agent_runs:
            groups[(run['task_id'], run['scenario_id'])].append(run)
        cells = [_cell(task, scenario, planned) for (task, scenario), planned in groups.items()]
        cells.sort(key=lambda cell: (cell['task_id'], cell['scenario_id']))
        clean_rates = {cell['task_id']: cell['success_rate'] for cell in cells
                       if cell['scenario_id'] == 'baseline' and cell['success_rate'] is not None}
        for cell in cells:
            cell['robustness_ratio'] = (cell['success_rate'] / clean_rates[cell['task_id']]
                                        if cell['scenario_id'] != 'baseline'
                                        and cell['success_rate'] is not None
                                        and clean_rates.get(cell['task_id']) else None)
        scored = [r for r in agent_runs if _scored(r)]
        baseline_cells = [c for c in cells if c['scenario_id'] == 'baseline' and c['n']]
        fault_cells = [c for c in cells if c['scenario_id'] != 'baseline' and c['n']]
        successes = sum(r['evaluation'].get('outcome') == 'success' for r in scored)
        base_runs = [r for r in scored if r.get('scenario_id') == 'baseline']
        fault_runs = [r for r in scored if r.get('scenario_id') != 'baseline']
        clean_success = (sum(c['success_rate'] for c in baseline_cells) / len(baseline_cells)) if baseline_cells else None
        pass3 = (sum(c['pass_3'] for c in baseline_cells if c['pass_3'] is not None) /
                 len([c for c in baseline_cells if c['pass_3'] is not None])) if any(c['pass_3'] is not None for c in baseline_cells) else None
        exposed = [r for r in fault_runs if r['metrics'].get('injected_faults')]
        def fault_acceptable(run):
            evaluation = run.get('evaluation', {})
            behavior = evaluation.get('behavior')
            return behavior in ('recovered', 'safe_failure')
        fault_tolerance = sum(fault_acceptable(r) for r in exposed) / len(exposed) if exposed else None
        recovery_rate = sum(
            r.get('evaluation', {}).get('behavior') == 'recovered'
            and r.get('evaluation', {}).get('outcome') == 'success' for r in exposed
        ) / len(exposed) if exposed else None
        all_costs = [r['metrics'].get('total_cost', r['metrics'].get('estimated_cost')) for r in scored]
        total_cost = sum(all_costs) if all_costs and all(v is not None for v in all_costs) else None
        cost_per_success = total_cost / successes if total_cost is not None and successes else None
        clean_successes = sum(r['evaluation'].get('outcome') == 'success' for r in base_runs)
        clean_total = len(base_runs)
        fault_successes = sum(r['evaluation'].get('outcome') == 'success' for r in fault_runs)
        legacy_groups = [[r for r in groups[(c['task_id'], c['scenario_id'])] if _scored(r)] for c in baseline_cells]
        legacy_groups = [g for g in legacy_groups if g]
        legacy_pass_all = (sum(all(r['evaluation'].get('outcome') == 'success' for r in g)
                               for g in legacy_groups) / len(legacy_groups)) if legacy_groups else None
        metric_runs = [r['metrics'] for r in scored]
        mean_latency = (sum(m['duration_ms'] for m in metric_runs if m.get('duration_ms') is not None) /
                        len([m for m in metric_runs if m.get('duration_ms') is not None])) if any(m.get('duration_ms') is not None for m in metric_runs) else None
        tokens_values = [m.get('tokens') for m in metric_runs]
        token_sum = sum(tokens_values) if tokens_values and all(t is not None for t in tokens_values) else None
        result.append(dict(
            agent_id=agent, runs=len(scored), planned_runs=len(agent_runs), incomplete_runs=len(agent_runs) - len(scored),
            success_rate=successes / len(scored) if scored else None,
            baseline_success=clean_successes / clean_total if clean_total else None,
            fault_success=fault_successes / len(fault_runs) if fault_runs else None,
            recovery_rate=recovery_rate,
            fault_tolerance=fault_tolerance,
            pass_all_repeats=legacy_pass_all,
            clean_success=clean_success, pass_1=(sum(c['pass_1'] for c in baseline_cells if c['pass_1'] is not None) / len([c for c in baseline_cells if c['pass_1'] is not None])) if any(c['pass_1'] is not None for c in baseline_cells) else None,
            pass_3=pass3, pass_5=(sum(c['pass_5'] for c in baseline_cells if c['pass_5'] is not None) / len([c for c in baseline_cells if c['pass_5'] is not None])) if any(c['pass_5'] is not None for c in baseline_cells) else None,
            robustness_ratio=(fault_successes / len(fault_runs) / clean_success if fault_runs and clean_success else None),
            mean_latency_ms=mean_latency, tokens=token_sum, cost_per_success=cost_per_success,
            total_cost=total_cost, confidence_interval=wilson(successes, len(scored)),
            cells=cells, frontier=False, score=None,
            score_components=dict(S=clean_success, R=pass3, F=fault_tolerance, E=None),
        ))

    costs = [entry['cost_per_success'] for entry in result if entry['cost_per_success'] is not None]
    lowest = min(costs) if costs else None
    for entry in result:
        if lowest is None or entry['cost_per_success'] is None:
            efficiency = None
        elif lowest == 0:
            efficiency = 1.0 if entry['cost_per_success'] == 0 else 0.0
        else:
            efficiency = min(1.0, lowest / entry['cost_per_success'])
        entry['score_components']['E'] = efficiency
        components = entry['score_components']
        if all(components[key] is not None for key in ('S', 'R', 'F', 'E')):
            entry['score'] = 100 * (.40 * components['S'] + .25 * components['R'] + .20 * components['F'] + .15 * components['E'])
    for entry in result:
        c = entry['cost_per_success']
        s = entry['clean_success']
        if c is not None and s is not None:
            entry['frontier'] = not any(other is not entry and other['cost_per_success'] is not None and other['clean_success'] is not None
                                       and other['cost_per_success'] <= c and other['clean_success'] >= s
                                       and (other['cost_per_success'] < c or other['clean_success'] > s) for other in result)
    return sorted(result, key=lambda x: (x['score'] is None, -(x['score'] or 0), -(x['success_rate'] or 0)))
