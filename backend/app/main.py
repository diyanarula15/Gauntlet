"""Local research-agent sidecar: registry, experiment runner, trace and comparison API."""
import asyncio
import html
import json
import uuid
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from .config import ROOT, settings as default_settings
from .database import Store, now
from .engine.runner import AGENTS, execute
from .engine.proxy import router, sanitize, digest
from .engine.metrics import aggregate, dose_response
from .engine.stats import significance
from .engine.postmortem import postmortem
from .engine.counterfactual import compare
from .services import bifrost, snapshots as evidence, tavily
from .services.mcp_tools import create_mcp_gateway
from .services.otlp import otlp_router


class DoseInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scenario_id: str
    levels: list[float] = Field(default=[0.25, 0.5, 0.75, 1.0], min_length=1, max_length=6)


class ExperimentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default='Research resilience experiment', min_length=1, max_length=100)
    agent_ids: list[str] = Field(default=['react', 'sdk'], min_length=1, max_length=12)
    task_ids: list[str] = Field(default=['research_001'], min_length=1, max_length=30)
    scenario_ids: list[str] = Field(default=['baseline', 'T1', 'M1', 'I2'], min_length=1, max_length=20)
    repetitions: int = Field(default=3, ge=1, le=10)
    evidence_mode: Literal['demo', 'live', 'frozen'] = 'demo'
    snapshot_id: str = 'demo-v1'
    seed: int = 42
    model_policy: Literal['pinned', 'own'] = 'pinned'
    model: str | None = Field(default=None, min_length=1, max_length=128)
    max_cost_usd: float | None = Field(default=None, gt=0, le=100, allow_inf_nan=False)
    concurrency: int = Field(default=3, ge=1, le=4)
    dose: DoseInput | None = None


class CaptureInput(BaseModel):
    task_ids: list[str] | None = None
    include_extract: bool = False


def create_app(database=None, configuration=None):
    settings = configuration or default_settings
    store = database or Store(settings.database_path)
    task_list = json.loads((ROOT / 'backend/app/fixtures/tasks.json').read_text())
    tasks = {task['id']: task for task in task_list}
    scenarios = [{**s, 'scenario_id': s['id']} for s in json.loads((ROOT / 'backend/app/fixtures/scenarios.json').read_text())]
    scenario_ids = {s['id'] for s in scenarios}
    demo_snapshot = evidence.build_demo(task_list)
    jobs = {}; capabilities = {}; creation_lock = asyncio.Lock()
    mcp_app, mcp_manager = create_mcp_gateway(settings)

    def seed():
        store.snapshot({**demo_snapshot, 'created_at': now()})
        for kind, values in (('agent', AGENTS.values()), ('task', task_list), ('fault', scenarios)):
            for obj in values: store.put(kind, obj)

    def require(kind, ident):
        obj = store.get(kind, ident)
        if not obj: raise HTTPException(404, f'Unknown {kind}')
        return obj

    @asynccontextmanager
    async def lifespan(app):
        seed()
        for kind in ('experiment', 'run'):
            for obj in store.list(kind):
                if obj['status'] in ('pending', 'running'):
                    status = 'cancelled' if obj['status'] == 'pending' else 'failed'
                    obj.update(status=status, ending='interrupted', error='Backend restarted; in-process job interrupted')
                    store.put(kind, obj)
        async with mcp_manager.run():
            yield
            for item in list(jobs.values()): item.cancel()
            await asyncio.gather(*list(jobs.values()), return_exceptions=True)

    app = FastAPI(title='FAULTLINE', version='0.2.0', lifespan=lifespan)
    app.state.store = store; app.state.settings = settings; app.state.capabilities = capabilities

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Drop non-JSON-safe fields (e.g. NaN echoed in 'input', exception objects in 'ctx').
        return JSONResponse({'detail': [{k: v for k, v in err.items() if k not in ('input', 'ctx', 'url')} for err in exc.errors()]}, status_code=422)
    app.include_router(router(store, settings, tasks, capabilities))
    app.include_router(otlp_router(store, capabilities))

    @app.middleware('http')
    async def local_origin_guard(request: Request, call_next):
        origin = request.headers.get('origin')
        allowed = {'http://localhost:5173', 'http://127.0.0.1:5173', settings.harness_base_url, settings.harness_base_url.replace('127.0.0.1', 'localhost')}
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin and origin not in allowed:
            return JSONResponse({'detail': 'Untrusted browser origin'}, status_code=403)
        return await call_next(request)

    @app.get('/api/health')
    def health(): return {'status': 'ok', 'product': 'FAULTLINE', 'version': '0.2.0'}

    @app.get('/api/catalog')
    def catalog():
        return {'agents': list(AGENTS.values()), 'tasks': task_list, 'scenarios': scenarios,
            'model': settings.bifrost_model, 'snapshot': store.get_snapshot(demo_snapshot['snapshot_id']),
            'limits': {'max_experiment_runs': settings.max_experiment_runs, 'max_concurrent_runs': settings.max_concurrent_runs},
            'definitions': {'step': 'One model request reaching the model proxy.', 'pass_k': 'Mean over tasks of C(successes,k)/C(observed repeats,k). Undefined when observed repeats < k.', 'frozen': 'Exact effective endpoint/parameter replay with no live fallback.'}}

    @app.get('/api/integrations')
    async def integrations():
        gateway = await bifrost.health(settings)
        return {'bifrost': gateway, 'tavily': {'configured': bool(settings.tavily_api_key), 'connected': None, 'message': 'Configured credentials are distinct from a verified live request.'},
                'live_ready': gateway['connected'] and bool(settings.tavily_api_key), 'cost': 'Reported gateway cost preferred; explicit model/tool prices used only for labelled estimates.'}

    @app.get('/api/experiments')
    def experiments(): return store.list('experiment')

    async def job(experiment):
        experiment['status'] = 'running'; store.put('experiment', experiment)
        gate = asyncio.Semaphore(min(experiment['config']['concurrency'], settings.max_concurrent_runs))
        async def one(run_id):
            async with gate:
                if require('experiment', experiment['id'])['status'] == 'cancelled': return
                run = require('run', run_id)
                try:
                    await execute(store, settings, run, tasks[run['task_id']], capabilities)
                except Exception as exc:
                    run = require('run', run_id)
                    run.update(status='failed', ending='crash', error='Harness error: ' + type(exc).__name__)
                    store.event(run_id, 'RUN_FINISHED', status='failed', ending='crash')
                    store.put('run', run)
                current = require('experiment', experiment['id'])
                if current['status'] != 'cancelled':
                    current['completed_runs'] += 1; store.put('experiment', current)
        try:
            async with asyncio.TaskGroup() as group:
                for run_id in experiment['run_ids']: group.create_task(one(run_id))
            current = require('experiment', experiment['id'])
            if current['status'] != 'cancelled': current.update(status='completed', finished_at=now()); store.put('experiment', current)
        except asyncio.CancelledError:
            current = require('experiment', experiment['id']); current.update(status='cancelled', finished_at=now()); store.put('experiment', current)
        except Exception as exc:
            current = require('experiment', experiment['id']); current.update(status='failed', error='Benchmark orchestration failed: ' + type(exc).__name__); store.put('experiment', current)
        finally:
            for run_id in experiment['run_ids']:
                run = require('run', run_id)
                if run['status'] == 'pending': run['status'] = 'cancelled'; run['ending'] = 'cancelled'; store.put('run', run)
            jobs.pop(experiment['id'], None)

    @app.post('/api/experiments', status_code=202)
    async def create(config: ExperimentInput):
        for values, known in ((config.agent_ids, AGENTS), (config.task_ids, tasks), (config.scenario_ids, scenario_ids)):
            if len(set(values)) != len(values) or not set(values) <= set(known): raise HTTPException(422, 'Unknown or duplicate catalog IDs')
        if 'baseline' not in config.scenario_ids: raise HTTPException(422, 'Include baseline for controlled comparisons')
        plans = {s['id']: s for s in scenarios if s['id'] in config.scenario_ids}
        if config.dose:
            base = next((s for s in scenarios if s['id'] == config.dose.scenario_id), None)
            if not base or base['id'] == 'baseline' or base.get('service') == 'instruction': raise HTTPException(422, 'Dose-response needs a model or tool fault')
            levels = sorted(set(config.dose.levels))
            if any(not 0 < p <= 1 for p in levels): raise HTTPException(422, 'Dose levels must be in (0, 1]')
            for p in levels:
                ident = f"{base['id']}@{p:g}"
                plans[ident] = {**base, 'id': ident, 'scenario_id': ident, 'display_name': f"{base['display_name']} @ p={p:g}", 'trigger': {'kind': 'probability', 'p': p}, 'dose': {'base': base['id'], 'p': p}}
        if 'M4' in plans and config.evidence_mode != 'demo' and not settings.outage_primary_model: raise HTTPException(422, 'Set OUTAGE_PRIMARY_MODEL (and optionally FALLBACK_MODEL) to run M4 against a live gateway')
        count = len(config.agent_ids) * len(config.task_ids) * len(plans) * config.repetitions
        if count > settings.max_experiment_runs: raise HTTPException(422, f'Maximum {settings.max_experiment_runs} runs per experiment')
        if config.evidence_mode == 'demo': config.snapshot_id = demo_snapshot['snapshot_id']
        elif config.evidence_mode == 'frozen':
            snap = store.get_snapshot(config.snapshot_id)
            if snap and snap.get('schema_version',1)<2: raise HTTPException(422, 'Recapture legacy evidence to obtain full-parameter schema-v2 matching')
            if not snap or snap['provenance'].startswith('Curated'): raise HTTPException(422, 'Frozen live-model mode requires captured evidence; use demo for curated fixtures')
        if config.evidence_mode != 'demo':
            if config.evidence_mode == 'live' and not settings.tavily_api_key: raise HTTPException(422, 'Configure TAVILY_API_KEY for live evidence')
            if not (await bifrost.health(settings))['connected']: raise HTTPException(422, 'Bifrost is not connected. Start the gateway and configure a provider.')
        async with creation_lock:
            if any(e['status'] in ('pending', 'running') for e in store.list('experiment')): raise HTTPException(409, 'One experiment at a time; wait or cancel the active job')
            values = config.model_dump(); values['model'] = config.model or settings.bifrost_model
            if values['max_cost_usd'] is None: values['max_cost_usd'] = settings.default_max_cost_usd
            experiment = {'id': uuid.uuid4().hex, 'name': config.name, 'config': values, 'status': 'pending', 'created_at': now(), 'completed_runs': 0, 'total_runs': count, 'run_ids': [], 'schema_version': 2}
            for agent in config.agent_ids:
                for task_id in config.task_ids:
                    for repeat in range(config.repetitions):
                        for scenario, plan in plans.items():
                            # Same seed for every agent at a given repeat, so probabilistic faults hit agents identically (paired design).
                            run = {'id': uuid.uuid4().hex, 'experiment_id': experiment['id'], 'agent_id': agent, 'task_id': task_id, 'scenario_id': scenario, 'repetition': repeat, 'status': 'pending', 'config': values,
                                'fault_plan': plan, 'seed': config.seed + repeat, 'schema_version': 2, **({'dose': plan['dose']} if plan.get('dose') else {})}
                            store.put('run', run); experiment['run_ids'].append(run['id'])
            store.put('experiment', experiment); jobs[experiment['id']] = asyncio.create_task(job(experiment)); return experiment

    @app.get('/api/experiments/{ident}')
    def experiment(ident: str):
        exp = require('experiment', ident); runs = [require('run', run_id) for run_id in exp['run_ids']]
        return {**exp, 'runs': runs, 'leaderboard': aggregate(runs), 'significance': significance(runs), 'dose_response': dose_response(runs)}

    @app.get('/api/experiments/{ident}/leaderboard')
    def leaderboard(ident: str, scenario: str | None = None, tier: int | None = None):
        runs = experiment(ident)['runs']
        if scenario: runs = [r for r in runs if r['scenario_id'] == scenario]
        if tier: runs = [r for r in runs if tasks.get(r['task_id'], {}).get('tier', 1) == tier]
        return {'rows': aggregate(runs), 'significance': significance(runs), 'dose_response': dose_response(runs), 'run_ids': [r['id'] for r in runs], 'filters': {'scenario': scenario, 'tier': tier}, 'limitation': 'Composite requires complete S/R/F/E. Missing components remain unavailable.'}

    @app.post('/api/experiments/{ident}/cancel')
    async def cancel(ident: str):
        exp = require('experiment', ident)
        if exp['status'] not in ('pending', 'running'): raise HTTPException(409, 'Experiment is already terminal')
        exp['status'] = 'cancelled'; store.put('experiment', exp)
        if ident in jobs: jobs[ident].cancel()
        return exp

    def detailed_run(ident):
        run = require('run', ident)
        events = bifrost.enriched_events(store.events(ident))
        return {**run, 'events': events, 'postmortem': postmortem(run, events) if run.get('evaluation') else None}

    @app.get('/api/runs/{ident}')
    def run(ident: str): return detailed_run(ident)

    @app.get('/api/runs/{ident}/events')
    async def stream(ident: str, request: Request):
        require('run', ident)
        async def feed():
            sequence = 0
            while not await request.is_disconnected():
                for event in store.events(ident):
                    if event['sequence_number'] > sequence:
                        sequence = event['sequence_number']; yield 'id: ' + str(sequence) + '\ndata: ' + json.dumps(event) + '\n\n'
                if require('run', ident)['status'] not in ('pending', 'running'): break
                await asyncio.sleep(.3)
        return StreamingResponse(feed(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})

    @app.get('/api/runs/{ident}/comparison')
    def comparison(ident: str):
        run = require('run', ident)
        base = next((b for b in store.list('run') if b['experiment_id'] == run['experiment_id'] and b['agent_id'] == run['agent_id'] and b['task_id'] == run['task_id'] and b['repetition'] == run['repetition'] and b['scenario_id'] == 'baseline'), None)
        if not base: raise HTTPException(404, 'No paired baseline')
        return compare(base, run, detailed_run(base['id'])['events'], detailed_run(run['id'])['events'])

    @app.get('/api/compare')
    def arbitrary_comparison(left: str, right: str):
        baseline = detailed_run(left); fault = detailed_run(right)
        if baseline['task_id'] != fault['task_id']: raise HTTPException(422, 'Choose two runs of the same task')
        return compare(baseline, fault, baseline['events'], fault['events'])

    @app.post('/api/runs/{ident}/replay', status_code=202)
    async def replay(ident: str):
        original = require('run', ident)
        if original['status'] in ('pending', 'running'): raise HTTPException(409, 'Run is still in progress')
        async with creation_lock:
            if any(e['status'] in ('pending', 'running') for e in store.list('experiment')): raise HTTPException(409, 'One experiment at a time; wait or cancel the active job')
            values = {**original['config'], 'agent_ids': [original['agent_id']], 'task_ids': [original['task_id']], 'scenario_ids': [original['scenario_id']], 'repetitions': 1}
            experiment = {'id': uuid.uuid4().hex, 'name': 'Replay of ' + original['agent_id'] + ' · ' + original['task_id'] + ' · ' + original['scenario_id'], 'config': values, 'status': 'pending', 'created_at': now(),
                          'completed_runs': 0, 'total_runs': 1, 'run_ids': [], 'schema_version': 2, 'replay_of': ident}
            run = {k: original[k] for k in ('agent_id', 'task_id', 'scenario_id', 'repetition', 'config', 'fault_plan', 'seed', 'dose') if k in original}
            run.update(id=uuid.uuid4().hex, experiment_id=experiment['id'], status='pending', schema_version=2, replay_of=ident)
            store.put('run', run); experiment['run_ids'].append(run['id'])
            store.put('experiment', experiment); jobs[experiment['id']] = asyncio.create_task(job(experiment)); return experiment

    @app.get('/api/experiments/{ident}/export')
    def export(ident: str):
        exp = experiment(ident)
        return JSONResponse({**exp, 'traces': {run_id: detailed_run(run_id)['events'] for run_id in exp['run_ids']}, 'methodology': 'Controlled paired comparison, not deterministic replay. Demo responses are simulated. Costs unknown without gateway reporting or explicit pricing. pass^k is undefined when n<k; incomplete/cancelled observations are disclosed.'}, headers={'Content-Disposition': f'attachment; filename=faultline-{ident}.json'})

    @app.get('/api/experiments/{ident}/report.html', response_class=HTMLResponse)
    def report(ident: str):
        exp = experiment(ident)
        rows = ''.join('<tr>' + ''.join('<td>' + html.escape(str(value)) + '</td>' for value in [run['agent_id'], run['task_id'], run['scenario_id'], run['repetition'] + 1, run.get('evaluation', {}).get('outcome', run['status']), run.get('evaluation', {}).get('behavior', 'unknown'), run.get('metrics', {}).get('steps', 'unavailable'), run.get('metrics', {}).get('tokens', 'unknown')]) + '</tr>' for run in exp['runs'])
        return '<!doctype html><html><head><meta charset="utf-8"><title>FAULTLINE Report</title><style>body{font:15px system-ui;max-width:1100px;margin:40px auto;padding:20px}table{border-collapse:collapse;width:100%}td,th{padding:9px;border:1px solid #ddd;text-align:left}code{overflow-wrap:anywhere}</style></head><body><h1>FAULTLINE — ' + html.escape(exp['name']) + '</h1><p>Evidence: ' + html.escape(exp['config']['evidence_mode']) + '. Controlled comparison; live models can be stochastic. Demo models are simulated. Unknown costs are not zero.</p><p>Method: deterministic answer and retrieved-citation grading; narrow heuristics, not semantic entailment. Canary presence is not proof of instruction obedience.</p><p>Snapshot: <code>' + html.escape(exp['config']['snapshot_id']) + '</code></p><table><thead><tr>' + ''.join('<th>' + s + '</th>' for s in ('Agent', 'Task', 'Condition', 'Repeat', 'Outcome', 'Behavior', 'Model steps', 'Tokens')) + '</tr></thead><tbody>' + rows + '</tbody></table></body></html>'

    @app.post('/api/snapshots/capture', status_code=201)
    async def capture(config: CaptureInput = CaptureInput()):
        if not settings.tavily_api_key: raise HTTPException(422, 'Configure TAVILY_API_KEY to capture live evidence')
        selected = config.task_ids or list(tasks)
        if not set(selected) <= set(tasks): raise HTTPException(422, 'Unknown task IDs')
        entries = {}
        try:
            for task_id in selected:
                task = tasks[task_id]
                requests = [('search', {'query': task['query']})]
                if config.include_extract: requests += [('extract', {'urls': [url]}) for url in task['ground_truth_sources']]
                for operation, payload in requests:
                    response = await tavily.forward(settings, operation, payload)
                    if not response.is_success: raise HTTPException(502, 'Tavily capture failed; snapshot not committed')
                    body = sanitize(response.json(), [settings.tavily_api_key, settings.bifrost_api_key])
                    entries[evidence.request_key(operation, payload)] = {'operation': operation, 'normalized_query': tavily.normalize(payload.get('query', '')), 'request_parameters': evidence.effective_request(operation, payload), 'response': body, 'response_hash': evidence.response_hash(body), 'source_metadata': [r.get('url') for r in body.get('results', [])]}
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            raise HTTPException(502, 'Evidence capture failed; snapshot not committed')
        snap = {'snapshot_id': uuid.uuid4().hex, 'schema_version': 2, 'task_suite_id': 'research-v2', 'capture_timestamp': now(), 'provenance': 'Live Tavily capture', 'entries': entries}
        store.snapshot(snap); return snap

    @app.get('/api/snapshots')
    def snapshots():
        with store.connect() as db: rows = db.execute('SELECT data FROM snapshots').fetchall()
        return [json.loads(row[0]) for row in rows]

    app.mount('/mcp/{run_id}', mcp_app)
    if (ROOT / 'frontend/dist').exists(): app.mount('/', StaticFiles(directory=ROOT / 'frontend/dist', html=True), name='frontend')
    return app


import httpx
settings = default_settings
app = create_app()
store = app.state.store
