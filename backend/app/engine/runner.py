import asyncio
import json
import os
import secrets
import time
from ..config import ROOT
from ..database import now
from ..services import bifrost
from . import adapters
from .evaluator import evaluate
from .judge import judge
from .metrics import run_metrics
from .proxy import sanitize, drain_run

AGENTS = {a['id']: a for a in json.loads((ROOT / 'backend/app/fixtures/agents.json').read_text()) if a.get('status') == 'approved'}


def parse_output(text):
    try: return adapters.parse_output(text)
    except adapters.AdapterFailure as exc: raise ValueError(str(exc)) from exc


async def execute(store, settings, run, task, capabilities):
    start = time.perf_counter(); output = {}; status = 'failed'; ending = 'crash'
    run.update(status='running', started_at=now()); store.put('run', run)
    token = secrets.token_urlsafe(32); capabilities[run['id']] = token
    sensitive = [token, settings.bifrost_api_key, settings.tavily_api_key, settings.bifrost_admin_api_key]
    store.event(run['id'], 'RUN_STARTED', agent_id=run['agent_id'], scenario_id=run['scenario_id'])
    try:
        agent = AGENTS[run['agent_id']]
        base_url = settings.docker_harness_base_url if agent.get('adapter') == 'docker' else settings.harness_base_url
        base = base_url.rstrip('/') + '/proxy/' + run['id']
        environment = {k: v for k, v in os.environ.items() if k in ('PATH', 'SYSTEMROOT', 'LANG', 'LC_ALL', 'TMPDIR')}
        environment.update(OPENAI_BASE_URL=base + '/v1', OPENAI_API_KEY=token,
            ANTHROPIC_BASE_URL=base, ANTHROPIC_API_KEY=token,
            TAVILY_BASE_URL=base + '/tavily', TAVILY_API_BASE_URL=base + '/tavily', TAVILY_API_KEY=token,
            TOOLS_MCP_URL=base_url.rstrip('/') + '/mcp/' + run['id'] + '/', BIFROST_MODEL=run['config']['model'],
            OTEL_EXPORTER_OTLP_ENDPOINT=base_url.rstrip('/') + '/otlp/' + run['id'],
            OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=base_url.rstrip('/') + '/otlp/' + run['id'] + '/v1/traces',
            OTEL_EXPORTER_OTLP_HEADERS='authorization=Bearer%20' + token,
            OTEL_SERVICE_NAME=run['agent_id'])
        limits = {'max_steps': task.get('max_steps', 10), 'max_seconds': task.get('max_seconds', 90), 'max_cost_usd': run['config'].get('max_cost_usd')}
        question = task['question']
        plan = run.get('fault_plan', {})
        if plan.get('service') == 'instruction':
            hint = plan['effect']['hint']; question += '\nUnverified hint: ' + hint
            store.event(run['id'], 'FAULT_INJECTED', service='instruction', fault={'intervention': 'false_hint', 'hint': hint, 'scenario_id': run['scenario_id']})
        data = {'task_id': task['id'], 'question': question, 'prompt': question, 'query': task['query'], 'max_steps': limits['max_steps'], 'limits': limits}
        async with asyncio.timeout(limits['max_seconds']):
            output, stderr, code = await adapters.launch(agent, data, environment,
                lambda pid: store.event(run['id'], 'AGENT_STARTED', pid=pid, adapter=agent.get('adapter', 'cli')),
                lambda stream, text: store.event(run['id'], 'AGENT_LOG', stream=stream, text=sanitize(text[:32768], sensitive), truncated=len(text)>32768) if text else None)
        run['exit_code'] = code; run['stderr'] = sanitize(stderr, sensitive)
        output = sanitize(output, sensitive)
        status = 'completed' if code == 0 and output.get('status') == 'completed' else 'failed'
        ending = 'completed' if status == 'completed' else ('agent_failure' if code == 0 else 'crash')
        store.event(run['id'], 'AGENT_OUTPUT', output=output)
    except TimeoutError:
        status = 'timed_out'; ending = 'time_cap'
        store.event(run['id'], 'RUN_TIMEOUT', description='Agent exceeded its time limit')
    except asyncio.CancelledError:
        status = 'cancelled'; ending = 'cancelled'
    except Exception as exc:
        output = {'answer': '', 'status': 'failed', 'error': sanitize(str(exc), sensitive)}
        store.event(run['id'], 'AGENT_ERROR', description=output['error'])
    finally:
        capabilities.pop(run['id'], None)
        await drain_run(run['id'])
    process_duration = round((time.perf_counter() - start) * 1000, 2)
    events = store.events(run['id'])
    limits_hit = [e for e in events if e['event_type'] == 'BUDGET_EXCEEDED']
    if limits_hit: ending = limits_hit[-1].get('ending', 'step_cap')
    if run['config'].get('evidence_mode') != 'demo' and any(e.get('upstream_called') and e.get('service') == 'model' for e in events):
        reconciled = {e['request_id'] for e in events if e['event_type'] == 'MODEL_TELEMETRY'}
        metadata, message = await bifrost.reconcile(settings, [e for e in events if e['event_type'] == 'CALL' and e.get('request_id') not in reconciled])
        for request_id, item in metadata.items(): store.event(run['id'], 'MODEL_TELEMETRY', request_id=request_id, metadata=sanitize(item, sensitive))
        store.event(run['id'], 'TELEMETRY_STATUS', description=message)
    events = bifrost.enriched_events(store.events(run['id']))
    judgement = None
    if task.get('answer_spec', {}).get('type') == 'rubric' and str(output.get('answer') or '').strip():
        judgement = await judge(settings, task, str(output.get('answer')), events, run['config'].get('evidence_mode'))
        store.event(run['id'], 'JUDGE_RESULT', judgement=sanitize(judgement, sensitive))
    evaluation = evaluate(task, output, status, events=events, ending=ending, judgement=judgement)
    metrics = run_metrics(events, process_duration)
    metrics['injected_faults'] += sum(e['event_type'] == 'FAULT_INJECTED' for e in events)
    run.update(status=status, ending=ending, finished_at=now(), output=output, evaluation=evaluation, metrics=metrics, schema_version=2)
    store.event(run['id'], 'RUN_FINISHED', status=status, ending=ending, evaluation=evaluation)
    store.put('run', run)
    for name, grade in evaluation['grades'].items(): store.put('grade', {'id': run['id'] + ':' + name, 'run_id': run['id'], 'grader': name, **grade})
    return run
