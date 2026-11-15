import asyncio
import copy
import hashlib
import json
import re
import secrets
import time
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from ..services import bifrost, tavily, snapshots
from .faults import decision

ACTIVE_REQUESTS = {}


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def sanitize(obj, secrets_list=()):
    if isinstance(obj, dict):
        return {k: ('[REDACTED]' if any(s in k.lower() for s in ('authorization', 'api_key', 'secret', 'password', 'token_key')) else sanitize(v, secrets_list)) for k, v in obj.items()}
    if isinstance(obj, list): return [sanitize(v, secrets_list) for v in obj]
    if isinstance(obj, str):
        for secret in secrets_list:
            if secret: obj = obj.replace(secret, '[REDACTED]')
        obj = re.sub(r'Bearer\s+[A-Za-z0-9._-]+', 'Bearer [REDACTED]', obj)
    return obj


def demo_model(payload, task):
    """Labelled evidence copier, never an evaluator or real provider."""
    evidence = None
    for message in reversed(payload.get('messages', [])):
        content = message.get('content') or ''
        try:
            if message.get('role') == 'tool': evidence = json.loads(content); break
            if 'Evidence:\n' in content: evidence = json.loads(content.split('Evidence:\n', 1)[1]); break
        except (ValueError, TypeError): pass
    message = {'role': 'assistant', 'content': 'Search the registered question and summarize retrieved evidence.'}
    if evidence is not None:
        results = evidence.get('results', [])
        message['content'] = ' '.join(f"{r.get('content', r.get('raw_content', ''))} [{r['url']}]" for r in results) or 'Unable to verify: no usable evidence was returned.'
    elif payload.get('tools'):
        message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'demo-search', 'type': 'function', 'function': {'name': 'web_search', 'arguments': json.dumps({'query': task['query']})}}]}
    return {'id': 'demo-response', 'object': 'chat.completion', 'created': 0, 'model': 'offline-evidence-simulator', 'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if message.get('tool_calls') else 'stop'}], 'demo': True}


async def drain_run(run_id):
    tasks = list(ACTIVE_REQUESTS.get(run_id, set()))
    for task in tasks: task.cancel()
    if tasks: await asyncio.gather(*tasks, return_exceptions=True)
    ACTIVE_REQUESTS.pop(run_id, None)


def router(store, settings, tasks, capabilities):
    api = APIRouter(); locks = {}

    async def handle(run, service, payload):
        run_id = run['id']; events = store.events(run_id)
        calls = [e for e in events if e['event_type'] == 'CALL']
        existing = [e for e in calls if e['service'] == service]
        task = tasks[run['task_id']]; config = run['config']; mode = config['evidence_mode']
        limit = min(settings.max_model_calls, task.get('max_steps', 10)) if service == 'model' else settings.max_search_calls
        if len(existing) >= limit:
            store.event(run_id, 'BUDGET_EXCEEDED', service=service, ending='step_cap' if service == 'model' else 'tool_cap')
            raise HTTPException(429, 'Model step cap exceeded' if service == 'model' else 'Tool request cap exceeded')
        cap = config.get('max_cost_usd')
        known = [e.get('gateway_reported_cost') if e.get('gateway_reported_cost') is not None else e.get('estimated_cost') for e in calls if e.get('upstream_called') and e['service'] == 'model']
        known += [e.get('tool_cost') for e in calls if e.get('upstream_called') and e['service'] != 'model']
        if cap is not None and sum(c for c in known if c is not None) >= cap:
            store.event(run_id, 'BUDGET_EXCEEDED', service=service, ending='cost_cap')
            raise HTTPException(429, 'Observed cost cap exceeded; gateway virtual-key budgets are the hard backstop')
        index = len(existing) + 1
        plan = run.get('fault_plan')
        fault = decision(run['scenario_id'], service, index, config.get('seed', 42), plan)
        request_id = secrets.token_hex(16)
        sensitive = [settings.bifrost_api_key, settings.bifrost_admin_api_key, settings.tavily_api_key, capabilities.get(run_id)]
        start = time.perf_counter(); upstream = False; status = 200; usage = None; estimated = None
        body = {}; headers = {}; extra = {}; gateway_id = None
        summary = snapshots.effective_request(service, payload) if service != 'model' else {'model': config.get('model', settings.bifrost_model) if config.get('model_policy', 'pinned') == 'pinned' else payload.get('model'), 'messages': payload.get('messages', []), 'tools': payload.get('tools', [])}
        store.event(run_id, 'CALL_STARTED', request_id=request_id, service=service, call_index=index, request_summary=sanitize(summary, sensitive))
        try:
            if fault and fault['status'] != 200:
                status = fault['status']; body = {'error': {'message': fault['error'], 'type': 'injected_fault'}}
                headers.update(fault.get('headers', {}))
            else:
                if fault and fault['intervention'] == 'latency': await asyncio.sleep(fault['parameters'].get('seconds', 20))
                if fault and fault['intervention'] == 'empty_results':
                    body = {'query': payload.get('query'), 'results': [], 'demo': mode == 'demo'}
                elif service == 'model':
                    if mode == 'demo': body = demo_model(payload, task)
                    else:
                        upstream = True
                        store.event(run_id, 'UPSTREAM_STARTED', request_id=request_id, service=service)
                        # run's pinned model is immutable, including after configuration changes.
                        model_settings = settings.model_copy(update={'bifrost_model': config.get('model', settings.bifrost_model)})
                        response = await bifrost.forward(model_settings, payload, run_id, request_id=request_id, model_policy=config.get('model_policy', 'pinned'))
                        status = response.status_code; body = response.json()
                        gateway_id = response.headers.get('x-bifrost-request-id') or response.headers.get('x-request-id') or request_id
                        if not isinstance(body, dict): raise ValueError('Expected model JSON object')
                        usage = body.get('usage') if status < 400 else None
                        if usage and summary['model'] == settings.bifrost_model and settings.input_price_per_million is not None and settings.output_price_per_million is not None and usage.get('prompt_tokens') is not None and usage.get('completion_tokens') is not None:
                            estimated = (usage['prompt_tokens'] * settings.input_price_per_million + usage['completion_tokens'] * settings.output_price_per_million) / 1e6
                        if response.headers.get('retry-after'): headers['Retry-After'] = response.headers['retry-after']
                elif mode in ('demo', 'frozen'):
                    snap = store.get_snapshot(config['snapshot_id'])
                    entry = snapshots.lookup(snap, service, payload)
                    extra['snapshot_id'] = config['snapshot_id']; extra['snapshot_schema_version'] = snap.get('schema_version', 1) if snap else None
                    if entry:
                        body = copy.deepcopy(entry['response']); extra.update(snapshot='hit', original_hash=entry['response_hash'])
                    else:
                        status = 422; extra.update(snapshot='miss', snapshot_miss=True)
                        body = {'error': {'message': 'Frozen evidence does not contain this endpoint and request parameters. No live fallback.', 'type': 'snapshot_miss'}}
                else:
                    upstream = True; store.event(run_id, 'UPSTREAM_STARTED', request_id=request_id, service=service)
                    response = await tavily.forward(settings, service, payload)
                    status = response.status_code; body = response.json()
                    if not isinstance(body, dict): raise ValueError('Expected tool JSON object')
                    extra['snapshot'] = 'live'
                    if response.headers.get('retry-after'): headers['Retry-After'] = response.headers['retry-after']
                if fault and fault['intervention'] in ('misleading_evidence', 'prompt_injection', 'append_untrusted_instruction', 'schema_drift') and status == 200:
                    original = digest(body); body = copy.deepcopy(body)
                    if fault['intervention'] == 'schema_drift': body['documents'] = body.pop('results', [])
                    elif body.get('results'):
                        params = {**(plan or {}).get('parameters', {}), **fault.get('parameters', {})}
                        if fault['intervention'] in ('prompt_injection', 'append_untrusted_instruction'):
                            body['results'][0]['content'] = body['results'][0].get('content', '') + '\n' + params['instruction']
                        else:
                            body['results'][0]['content'] = params.get('replacement_by_task', {}).get(task['id'], 'This historical event occurred on January 1, 2000.')
                    extra.update(original_hash=original, mutated_hash=digest(body))
        except asyncio.CancelledError:
            status = 499; body = {'error': {'type': 'run_cancelled', 'message': 'Run ended while this request was in progress; upstream billing may be incomplete.'}}
            extra['interrupted'] = True
        except httpx.TimeoutException:
            status = 504; body = {'error': {'message': 'Upstream request timed out', 'type': 'upstream_timeout'}}
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            status = 502; body = {'error': {'message': 'Upstream transport error, invalid JSON, or snapshot integrity failure', 'type': 'upstream_error'}}
        body = sanitize(body, sensitive)
        if existing and existing[-1]['status_code'] in (429, 500, 502, 503, 504) and digest(sanitize(payload)) == existing[-1].get('request_hash'):
            store.event(run_id, 'RETRY_OBSERVED', service=service, description='Repeated identical request after dependency error')
        if service != 'model':
            credits = (body.get('usage') or {}).get('credits') if upstream else 0
            if not upstream: extra.update(tool_credits=0, tool_cost=0)
            elif isinstance(credits, (int, float)) and not isinstance(credits, bool):
                extra['tool_credits'] = credits
                extra['tool_cost'] = credits * settings.tavily_credit_price_usd if settings.tavily_credit_price_usd is not None else None
        store.event(run_id, 'CALL', request_id=request_id, service=service, call_index=index, status_code=status,
            duration_ms=round((time.perf_counter() - start) * 1000, 3), upstream_called=upstream,
            gateway_request_id=gateway_id, usage=usage, estimated_cost=estimated, fault=fault,
            request_hash=digest(sanitize(payload)), request_summary=sanitize(summary, sensitive), response_summary=body, **extra)
        return JSONResponse(body, status_code=status, headers=headers)

    @api.post('/proxy/{run_id}/{path:path}')
    async def proxy(run_id: str, path: str, request: Request):
        run = store.get('run', run_id)
        if not run: raise HTTPException(404, 'Unknown run')
        if run['status'] != 'running': raise HTTPException(409, 'Run is not active')
        supplied = request.headers.get('authorization', '').removeprefix('Bearer ')
        expected = capabilities.get(run_id)
        if expected is None or not secrets.compare_digest(supplied, expected): raise HTTPException(401, 'Invalid run capability')
        if path not in ('v1/chat/completions', 'tavily/search', 'tavily/extract'): raise HTTPException(404, 'Unsupported proxy endpoint')
        if int(request.headers.get('content-length', 0)) > 100_000: raise HTTPException(413, 'Request too large')
        raw = await request.body()
        if len(raw) > 100_000: raise HTTPException(413, 'Request too large')
        try: payload = json.loads(raw)
        except ValueError: raise HTTPException(400, 'Invalid JSON')
        if not isinstance(payload, dict): raise HTTPException(422, 'Expected JSON object')
        if payload.get('stream'): raise HTTPException(400, 'Streaming is unsupported; set stream=false')
        service = 'model' if path.startswith('v1') else path.split('/')[-1]
        async with locks.setdefault(run_id, asyncio.Lock()):
            if store.get('run', run_id)['status'] != 'running' or capabilities.get(run_id) != supplied: raise HTTPException(409, 'Run is no longer active')
            child = asyncio.create_task(handle(run, service, payload))
            ACTIVE_REQUESTS.setdefault(run_id, set()).add(child)
            try: return await child
            finally:
                ACTIVE_REQUESTS.get(run_id, set()).discard(child)
    return api
