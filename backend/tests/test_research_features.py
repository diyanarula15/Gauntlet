import asyncio
import copy
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app import main
from backend.app.database import Store
from backend.app.config import Settings, ROOT
from backend.app.engine import proxy as proxy_engine
from backend.app.engine.evaluator import evaluate
from backend.app.engine.judge import judge
from backend.app.engine.metrics import aggregate, dose_response
from backend.app.engine.proxy import router, sse_replay
from backend.app.engine.stats import holm, mcnemar_exact, newcombe_diff, significance

TASKS = {t['id']: t for t in json.loads((ROOT / 'backend/app/fixtures/tasks.json').read_text())}
SCENARIOS = {s['id']: s for s in json.loads((ROOT / 'backend/app/fixtures/scenarios.json').read_text())}


@pytest.fixture
def client(tmp_path, monkeypatch):
    instance = main.create_app(Store(str(tmp_path / 'test.db')), main.settings.model_copy(update={'tavily_api_key': ''}))
    with TestClient(instance) as c:
        yield c


def proxy_client(tmp_path, mode='demo', scenario='baseline'):
    store = Store(str(tmp_path / 'proxy.db'))
    settings = Settings(bifrost_api_key='secret-bifrost', tavily_api_key='secret-tavily')
    task = {'id': 't', 'query': 'known query'}
    store.put('run', {'id': 'r', 'status': 'running', 'scenario_id': scenario, 'task_id': 't', 'config': {'evidence_mode': mode, 'snapshot_id': 'test'}})
    store.snapshot({'snapshot_id': 'test', 'entries': {'known query': {'response': {'results': [{'title': 'Evidence', 'content': 'value', 'url': 'https://example.org'}]}, 'response_hash': 'abc'}}})
    app = FastAPI()
    app.include_router(router(store, settings, {'t': task}, {'r': 'cap'}))
    return TestClient(app), store, settings


def sse_pairs(chunks):
    """Split replayed SSE chunks into (event, data) pairs."""
    pairs = []
    for chunk in chunks:
        event, data = None, None
        for line in chunk.strip().split('\n'):
            if line.startswith('event: '):
                event = line[len('event: '):]
            elif line.startswith('data: '):
                data = line[len('data: '):]
        pairs.append((event, data))
    return pairs


def search_event(results):
    return {'event_type': 'CALL', 'service': 'search', 'status_code': 200, 'response_summary': {'results': results}}


def stat_run(agent, task, repetition, ok, scenario='baseline'):
    return {'agent_id': agent, 'task_id': task, 'repetition': repetition, 'scenario_id': scenario,
            'evaluation': {'outcome': 'success' if ok else 'failed'}}


# ---------------------------------------------------------------- stats

def test_mcnemar_exact_known_values_and_null_case():
    assert mcnemar_exact(0, 6) == pytest.approx(0.03125)
    assert mcnemar_exact(1, 5) == pytest.approx(0.21875)
    assert mcnemar_exact(0, 0) == 1.0


def test_holm_is_monotone_in_raw_order_and_capped_at_one():
    adjusted = holm([0.01, 0.04, 0.03, 0.5])
    assert adjusted == pytest.approx([0.04, 0.09, 0.09, 0.5])
    assert holm([0.6, 0.9]) == [1.0, 1.0]
    raw = [0.001, 0.2, 0.04, 0.9, 0.03]
    ordered = [adj for _, adj in sorted(zip(raw, holm(raw)))]
    assert ordered == sorted(ordered)
    assert all(r <= a <= 1.0 for r, a in zip(raw, holm(raw)))


def test_newcombe_interval_contains_point_estimate():
    diff, lo, hi = newcombe_diff(9, 10, 3, 10)
    assert diff == pytest.approx(0.6)
    assert lo <= diff <= hi
    assert -1 <= lo and hi <= 1


def test_significance_pairs_on_task_and_repetition_only():
    runs = [stat_run('A', 't1', 0, True), stat_run('A', 't1', 1, True), stat_run('A', 't2', 0, True),
            stat_run('B', 't1', 0, False), stat_run('B', 't1', 1, False), stat_run('B', 't2', 0, False),
            stat_run('B', 't3', 0, False)]  # t3 has no partner in A, so it must not be paired
    result = significance(runs)
    assert result['leader'] == 'A'
    [comp] = result['comparisons']
    assert (comp['n_pairs'], comp['a_only'], comp['b_only'], comp['both'], comp['neither']) == (3, 3, 0, 0, 0)
    assert comp['p_value'] == pytest.approx(0.25)


def test_significance_marks_established_only_when_holm_adjusted_p_below_05():
    single = [stat_run('A', f't{i}', 0, True) for i in range(6)] + [stat_run('B', f't{i}', 0, False) for i in range(6)]
    [comp] = significance(single)['comparisons']
    assert comp['p_holm'] == pytest.approx(0.03125, abs=1e-4) and comp['established'] is True  # output is rounded to 4 dp

    # A second comparison doubles the family size; the same raw p=0.03125 becomes 0.0625 and is no longer established.
    two = single + [stat_run('C', f't{i}', 0, False) for i in range(6)]
    comps = {c['agent_b']: c for c in significance(two)['comparisons']}
    assert comps['B']['p_holm'] == pytest.approx(0.0625) and comps['B']['established'] is False
    assert comps['C']['p_holm'] == pytest.approx(0.0625) and comps['C']['established'] is False


# ---------------------------------------------------------------- metrics

def test_dose_response_has_p_zero_baseline_point_and_breaking_point():
    runs = [{'agent_id': 'a', 'task_id': 't', 'scenario_id': 'baseline', 'repetition': i, 'evaluation': {'outcome': 'success'}} for i in range(4)]
    runs += [{'agent_id': 'a', 'task_id': 't', 'scenario_id': 'T2@0.5', 'repetition': i, 'dose': {'base': 'T2', 'p': 0.5},
              'evaluation': {'outcome': 'success' if i < 2 else 'failed'}} for i in range(4)]
    runs += [{'agent_id': 'a', 'task_id': 't', 'scenario_id': 'T2@1', 'repetition': i, 'dose': {'base': 'T2', 'p': 1.0},
              'evaluation': {'outcome': 'failed'}} for i in range(4)]
    curve = dose_response(runs)['a']['T2']
    points = {pt['p']: pt for pt in curve['points']}
    assert sorted(points) == [0.0, 0.5, 1.0]
    assert (points[0.0]['n'], points[0.0]['rate']) == (4, 1.0)
    assert points[0.5]['rate'] == 0.5
    assert curve['breaking_point'] == 1.0  # 0.5 is not below the 50% threshold, 1.0 is


def test_aggregate_abstention_rate_is_none_without_unanswerable_runs():
    runs = [{'agent_id': 'a', 'task_id': 't', 'scenario_id': 'baseline', 'repetition': 0, 'status': 'completed',
             'evaluation': {'outcome': 'success', 'answerable': True, 'behavior': 'unknown'},
             'metrics': {'tokens': 10, 'estimated_cost': 0.1, 'total_cost': 0.1, 'duration_ms': 20, 'steps': 2, 'injected_faults': 0}}]
    row = aggregate(runs)[0]
    assert row['abstention_rate'] is None and row['over_answer_rate'] is None and row['abstention_n'] == 0


# ---------------------------------------------------------------- evaluator

def test_correct_abstention_is_acceptable_on_unanswerable_research_009():
    output = {'answer': 'I cannot verify the attendance figure; the retrieved sources do not contain it.', 'status': 'completed'}
    result = evaluate(TASKS['research_009'], output, 'completed')
    assert result['behavior'] == 'correct_abstention'
    assert result['acceptable'] is True


def test_fixture_expected_abstention_is_graded_as_correct_abstention():
    task = TASKS['research_009']
    result = evaluate(task, {'answer': task['expected_answer'], 'status': 'completed'}, 'completed')
    assert result['behavior'] == 'correct_abstention'


@pytest.mark.parametrize('answer', [
    pytest.param('I could not verify the attendance figure.'),
    pytest.param('The attendance figure is not verified.'),
])
def test_honest_could_not_verify_wording_is_correct_abstention(answer):
    result = evaluate(TASKS['research_009'], {'answer': answer, 'status': 'completed'}, 'completed')
    assert result['behavior'] == 'correct_abstention'


def test_confident_number_on_unanswerable_task_is_silent_failure():
    result = evaluate(TASKS['research_009'], {'answer': 'About 12,000 people attended the livestream.', 'status': 'completed'}, 'completed')
    assert result['behavior'] == 'silent_failure'
    assert result['acceptable'] is False


def test_rubric_task_with_demo_judge_passes_evidence_answer_and_fails_no_idea():
    task = TASKS['research_011']
    events = [search_event(task['evidence']['results'])]
    good = ('Python 3.10.0 came first, released October 4, 2021. Python 3.11.0 followed on October 24, 2022, '
            'roughly 12 months later. Sources: https://www.python.org/downloads/release/python-3100/ '
            'https://www.python.org/downloads/release/python-3110/')
    judgement = asyncio.run(judge(main.settings, task, good, events, 'demo'))
    assert judgement['passed'] is True and judgement['score'] == 1.0
    result = evaluate(task, {'answer': good, 'status': 'completed', 'citations': []}, 'completed', events=events, judgement=judgement)
    assert result['outcome'] == 'success'
    assert result['grades']['grounding']['passed'] is True

    bad_judgement = asyncio.run(judge(main.settings, task, 'No idea.', events, 'demo'))
    assert bad_judgement['passed'] is False
    failed = evaluate(task, {'answer': 'No idea.', 'status': 'completed'}, 'completed', events=events, judgement=bad_judgement)
    assert failed['outcome'] != 'success'


def test_rubric_without_judgement_is_unverified_not_passed():
    task = TASKS['research_011']
    result = evaluate(task, {'answer': 'Python 3.10.0 came first.', 'status': 'completed'}, 'completed')
    assert result['outcome'] == 'unverified'
    assert result['grades']['answer']['passed'] is None


# ---------------------------------------------------------------- judge

def test_live_judge_with_unreachable_gateway_falls_back_without_raising():
    settings = main.settings.model_copy(update={'bifrost_base_url': 'http://127.0.0.1:9'})
    result = asyncio.run(judge(settings, TASKS['research_011'], 'Python 3.10.0 came first.', [], 'live'))
    assert result['method'] == 'keyword_rubric_fallback'
    assert result['judge_error'] and result['judge_error'].startswith(('ConnectError', 'judge failed'))
    assert result['passed'] is False


# ---------------------------------------------------------------- proxy (demo mode)

def test_openai_stream_true_replays_sse_and_records_buffered_replay(tmp_path):
    c, store, _ = proxy_client(tmp_path)
    r = c.post('/proxy/r/v1/chat/completions', json={'stream': True, 'messages': [{'role': 'user', 'content': 'hi'}]}, headers={'Authorization': 'Bearer cap'})
    assert r.status_code == 200 and r.headers['content-type'].startswith('text/event-stream')
    assert r.text.rstrip().endswith('data: [DONE]')
    calls = [e for e in store.events('r') if e['event_type'] == 'CALL']
    assert calls[-1]['streamed'] == 'buffered_replay'


def test_anthropic_messages_route_returns_anthropic_message_shape(tmp_path):
    c, _, _ = proxy_client(tmp_path)
    r = c.post('/proxy/r/v1/messages', json={'model': 'claude-x', 'max_tokens': 16, 'messages': [{'role': 'user', 'content': 'hello'}]}, headers={'x-api-key': 'cap'})
    assert r.status_code == 200
    body = r.json()
    assert body['type'] == 'message' and body['role'] == 'assistant'
    assert isinstance(body['content'], list) and body['content'][0]['type'] == 'text'


def test_anthropic_stream_emits_message_stop_and_rejects_wrong_key(tmp_path):
    c, _, _ = proxy_client(tmp_path)
    body = {'stream': True, 'max_tokens': 16, 'messages': [{'role': 'user', 'content': 'hello'}]}
    r = c.post('/proxy/r/v1/messages', json=body, headers={'x-api-key': 'cap'})
    assert r.status_code == 200 and 'event: message_stop' in r.text
    assert c.post('/proxy/r/v1/messages', json=body, headers={'x-api-key': 'wrong'}).status_code == 401


def test_injected_model_outage_on_anthropic_route_returns_anthropic_error(tmp_path):
    c, store, _ = proxy_client(tmp_path)
    run = store.get('run', 'r'); run['fault_plan'] = copy.deepcopy(SCENARIOS['M1']); store.put('run', run)
    r = c.post('/proxy/r/v1/messages', json={'messages': [{'role': 'user', 'content': 'hi'}]}, headers={'x-api-key': 'cap'})
    assert r.status_code == 503
    assert r.json() == {'type': 'error', 'error': {'type': 'injected_fault', 'message': 'Controlled persistent model outage'}}
    assert store.events('r')[-1]['fault']['intervention'] == 'http_error'


def test_provider_failover_fault_returns_200_and_records_failover_on_call(tmp_path):
    c, store, _ = proxy_client(tmp_path)
    run = store.get('run', 'r'); run['fault_plan'] = copy.deepcopy(SCENARIOS['M4']); store.put('run', run)
    r = c.post('/proxy/r/v1/chat/completions', json={'messages': [{'role': 'user', 'content': 'hi'}]}, headers={'Authorization': 'Bearer cap'})
    assert r.status_code == 200
    call = [e for e in store.events('r') if e['event_type'] == 'CALL'][-1]
    assert call['fault']['intervention'] == 'provider_failover'
    assert call['failover']['simulated'] is True
    assert call['status_code'] == 200


# ---------------------------------------------------------------- SSE replay

def test_sse_replay_tool_use_input_round_trips_through_input_json_delta():
    original_input = {'query': 'q "quoted" ünïcode', 'n': 2, 'nested': {'a': [1, 2]}}
    body = {'id': 'msg', 'model': 'm', 'stop_reason': 'tool_use', 'usage': {'input_tokens': 3, 'output_tokens': 4},
            'content': [{'type': 'tool_use', 'id': 'tu_1', 'name': 'web_search', 'input': original_input}]}
    pairs = sse_pairs(sse_replay(body, 'anthropic'))
    deltas = [json.loads(data) for event, data in pairs if event == 'content_block_delta']
    partial = [d['delta']['partial_json'] for d in deltas if d['delta']['type'] == 'input_json_delta']
    assert len(partial) == 1
    assert json.loads(partial[0]) == original_input
    starts = [json.loads(data) for event, data in pairs if event == 'content_block_start']
    assert starts[0]['content_block']['input'] == {}
    assert pairs[-1][0] == 'message_stop'


def test_sse_replay_openai_include_usage_emits_empty_choices_usage_chunk():
    body = {'id': 'c', 'model': 'm', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'hi'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3}}
    chunks = sse_replay(body, 'openai', include_usage=True)
    assert chunks[-1] == 'data: [DONE]\n\n'
    usage_chunk = json.loads(chunks[-2][len('data: '):])
    assert usage_chunk['choices'] == []
    assert usage_chunk['usage'] == body['usage']
    without = sse_replay(body, 'openai')
    assert all('usage' not in json.loads(c[len('data: '):]) for c in without[:-1])


# ---------------------------------------------------------------- run state cleanup

def test_drain_run_cancels_requests_and_forgets_lock_and_cost_state():
    async def scenario():
        blocker = asyncio.create_task(asyncio.sleep(60))
        proxy_engine.LOCKS['r'] = asyncio.Lock()
        proxy_engine.COST_CHECKED['r'] = {'req-1'}
        proxy_engine.ACTIVE_REQUESTS['r'] = {blocker}
        await proxy_engine.drain_run('r')
        return blocker.cancelled()

    assert asyncio.run(scenario()) is True
    assert 'r' not in proxy_engine.LOCKS
    assert 'r' not in proxy_engine.COST_CHECKED
    assert 'r' not in proxy_engine.ACTIVE_REQUESTS


# ---------------------------------------------------------------- OTLP ingestion

def test_otlp_rejects_protobuf_and_stores_json_spans_for_valid_capability(tmp_path):
    instance = main.create_app(Store(str(tmp_path / 'otlp.db')), main.settings.model_copy(update={'tavily_api_key': ''}))
    with TestClient(instance) as c:
        c.app.state.store.put('run', {'id': 'r', 'status': 'running'})
        c.app.state.capabilities['r'] = 'cap'
        headers = {'Authorization': 'Bearer cap'}
        protobuf = c.post('/otlp/r/v1/traces', content=b'\x0a\x00', headers={**headers, 'Content-Type': 'application/x-protobuf'})
        assert protobuf.status_code == 415
        payload = {'resourceSpans': [{'resource': {'attributes': [{'key': 'service.name', 'value': {'stringValue': 'agent-under-test'}}]},
                                      'scopeSpans': [{'spans': [{'traceId': 't1', 'spanId': 's1', 'name': 'agent.step', 'kind': 1,
                                                                 'startTimeUnixNano': '1000000', 'endTimeUnixNano': '3000000',
                                                                 'attributes': [{'key': 'step.index', 'value': {'intValue': '1'}}]}]}]}]}
        r = c.post('/otlp/r/v1/traces', json=payload, headers=headers)
        assert r.status_code == 200 and r.json() == {'partialSuccess': {}}
        spans = [e for e in c.app.state.store.events('r') if e['event_type'] == 'AGENT_SPAN']
        assert len(spans) == 1
        assert spans[0]['name'] == 'agent.step' and spans[0]['duration_ms'] == 2.0
        assert spans[0]['service_name'] == 'agent-under-test' and spans[0]['attributes'] == {'step.index': 1}


# ---------------------------------------------------------------- experiment API

def test_dose_base_must_be_a_model_or_tool_fault(client):
    assert client.post('/api/experiments', json={'dose': {'scenario_id': 'baseline'}}).status_code == 422
    assert client.post('/api/experiments', json={'dose': {'scenario_id': 'I1'}}).status_code == 422


def test_valid_dose_creates_expected_run_count_with_dose_and_seed(client, monkeypatch):
    async def no_execution(*args, **kwargs):
        return None
    monkeypatch.setattr(main, 'execute', no_execution)
    body = {'agent_ids': ['react', 'sdk'], 'task_ids': ['research_001'], 'scenario_ids': ['baseline'], 'repetitions': 1,
            'seed': 7, 'dose': {'scenario_id': 'T2', 'levels': [0.5, 1]}}
    r = client.post('/api/experiments', json=body)
    assert r.status_code == 202
    experiment = r.json()
    agents, tasks, repeats, scenario_count = 2, 1, 1, len(body['scenario_ids'])
    assert experiment['total_runs'] == agents * tasks * repeats * (scenario_count + 2) == 6
    runs = [client.app.state.store.get('run', run_id) for run_id in experiment['run_ids']]
    assert len(runs) == 6
    dosed = [run for run in runs if 'dose' in run]
    assert sorted(run['dose']['p'] for run in dosed) == [0.5, 0.5, 1.0, 1.0]
    assert all(run['dose']['base'] == 'T2' for run in dosed)
    assert all(run['seed'] == 7 for run in runs)
    assert {run['scenario_id'] for run in dosed} == {'T2@0.5', 'T2@1'}


@pytest.mark.parametrize('status', ['pending', 'running'])
def test_replay_of_active_run_is_conflict(client, status):
    client.app.state.store.put('run', {'id': 'live', 'status': status, 'agent_id': 'react', 'task_id': 'research_001',
                                       'scenario_id': 'baseline', 'repetition': 0, 'config': {}, 'fault_plan': None, 'seed': 42})
    assert client.post('/api/runs/live/replay').status_code == 409


def test_postmortem_does_not_flag_conflict_on_correct_variant():
    from backend.app.engine.postmortem import postmortem
    run = {'status': 'completed', 'evaluation': {'outcome': 'success', 'behavior': 'unknown', 'grades': {
        'answer': {'detail': 'Explicit registered complete-answer variant (after conflicting-claim check).'}, 'grounding': {'unretrieved': []}}}, 'output': {'answer': 'Oct. 4, 2021'}}
    pm = postmortem(run, [])
    assert pm['verdict'] == 'clean_success'
    assert not any('conflicting' in f for f in pm['findings'])
