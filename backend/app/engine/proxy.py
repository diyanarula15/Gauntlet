import asyncio
import copy
import hashlib
import json
import re
import secrets
import time
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
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


def _block_text(content):
    if isinstance(content, str): return content
    if isinstance(content, list): return ''.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text')
    return ''


def anthropic_as_openai(payload):
    """Translate an Anthropic Messages request into the chat shape the offline simulator reads."""
    messages = [{'role': 'system', 'content': _block_text(payload.get('system'))}] if payload.get('system') else []
    for message in payload.get('messages', []):
        content = message.get('content')
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get('type') == 'tool_result':
                    text = _block_text(block.get('content')) if not isinstance(block.get('content'), str) else block['content']
                    # A failed tool call carries no evidence; the simulator should see an empty result set, as on the chat route.
                    if block.get('is_error'): text = json.dumps({'results': [], 'error': text})
                    messages.append({'role': 'tool', 'tool_call_id': block.get('tool_use_id'), 'content': text})
            text = _block_text(content)
            if text: messages.append({'role': message.get('role'), 'content': text})
        else:
            messages.append({'role': message.get('role'), 'content': content})
    return {'messages': messages, 'tools': payload.get('tools', [])}


def openai_as_anthropic(body):
    message = body['choices'][0]['message']
    blocks = [{'type': 'text', 'text': message['content']}] if message.get('content') else []
    for call in message.get('tool_calls') or []:
        blocks.append({'type': 'tool_use', 'id': call['id'], 'name': call['function']['name'], 'input': json.loads(call['function']['arguments'] or '{}')})
    return {'id': 'msg_demo', 'type': 'message', 'role': 'assistant', 'model': body.get('model'), 'content': blocks,
            'stop_reason': 'tool_use' if message.get('tool_calls') else 'end_turn', 'stop_sequence': None, 'usage': None, 'demo': True}


def _sse(event, data):
    return (f'event: {event}\n' if event else '') + 'data: ' + json.dumps(data) + '\n\n'


def sse_replay(body, transport, include_usage=False):
    """Buffered streaming: replay a complete upstream response as provider-shaped SSE chunks."""
    if transport == 'anthropic':
        usage = body.get('usage') or {}
        out = [_sse('message_start', {'type': 'message_start', 'message': {**{k: v for k, v in body.items() if k not in ('content', 'stop_reason', 'usage')}, 'content': [], 'stop_reason': None, 'usage': {'input_tokens': usage.get('input_tokens', 0), 'output_tokens': 0}}})]
        for i, block in enumerate(body.get('content') or []):
            if block.get('type') == 'tool_use':
                out.append(_sse('content_block_start', {'type': 'content_block_start', 'index': i, 'content_block': {**block, 'input': {}}}))
                out.append(_sse('content_block_delta', {'type': 'content_block_delta', 'index': i, 'delta': {'type': 'input_json_delta', 'partial_json': json.dumps(block.get('input', {}))}}))
            else:
                out.append(_sse('content_block_start', {'type': 'content_block_start', 'index': i, 'content_block': {'type': 'text', 'text': ''}}))
                out.append(_sse('content_block_delta', {'type': 'content_block_delta', 'index': i, 'delta': {'type': 'text_delta', 'text': block.get('text', '')}}))
            out.append(_sse('content_block_stop', {'type': 'content_block_stop', 'index': i}))
        out.append(_sse('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': body.get('stop_reason'), 'stop_sequence': None}, 'usage': {'output_tokens': usage.get('output_tokens', 0)}}))
        out.append(_sse('message_stop', {'type': 'message_stop'}))
        return out
    base = {'id': body.get('id', 'chatcmpl'), 'object': 'chat.completion.chunk', 'created': body.get('created', 0), 'model': body.get('model')}
    out = []
    for choice in body.get('choices', []):
        message = choice.get('message', {}); index = choice.get('index', 0)
        out.append(_sse(None, {**base, 'choices': [{'index': index, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]}))
        if message.get('content'): out.append(_sse(None, {**base, 'choices': [{'index': index, 'delta': {'content': message['content']}, 'finish_reason': None}]}))
        for n, call in enumerate(message.get('tool_calls') or []):
            out.append(_sse(None, {**base, 'choices': [{'index': index, 'delta': {'tool_calls': [{'index': n, **call}]}, 'finish_reason': None}]}))
        out.append(_sse(None, {**base, 'choices': [{'index': index, 'delta': {}, 'finish_reason': choice.get('finish_reason')}]}))
    if include_usage: out.append(_sse(None, {**base, 'choices': [], 'usage': body.get('usage')}))
    out.append('data: [DONE]\n\n')
    return out


def as_anthropic_error(body):
    error = body.get('error', {}) if isinstance(body, dict) else {}
    return {'type': 'error', 'error': {'type': error.get('type', 'api_error'), 'message': error.get('message', 'Request failed')}}


LOCKS = {}
COST_CHECKED = {}


async def drain_run(run_id):
    tasks = list(ACTIVE_REQUESTS.get(run_id, set()))
    for task in tasks: task.cancel()
    if tasks: await asyncio.gather(*tasks, return_exceptions=True)
    ACTIVE_REQUESTS.pop(run_id, None); LOCKS.pop(run_id, None); COST_CHECKED.pop(run_id, None)


def router(store, settings, tasks, capabilities):
    api = APIRouter(); locks = LOCKS; cost_checked = COST_CHECKED

    async def handle(run, service, payload, transport='openai'):
        run_id = run['id']; events = store.events(run_id)
        calls = [e for e in events if e['event_type'] == 'CALL']
        existing = [e for e in calls if e['service'] == service]
        task = tasks[run['task_id']]; config = run['config']; mode = config['evidence_mode']
        limit = min(settings.max_model_calls, task.get('max_steps', 10)) if service == 'model' else settings.max_search_calls
        if len(existing) >= limit:
            store.event(run_id, 'BUDGET_EXCEEDED', service=service, ending='step_cap' if service == 'model' else 'tool_cap')
            raise HTTPException(429, 'Model step cap exceeded' if service == 'model' else 'Tool request cap exceeded')
        cap = config.get('max_cost_usd')
        if cap is not None and mode != 'demo':
            # Gateway costs normally arrive only after the run; fetch them now so the observed cap can trip between calls.
            telemetry = {e['request_id'] for e in events if e['event_type'] == 'MODEL_TELEMETRY'}
            checked = cost_checked.setdefault(run_id, set())
            unpriced = [e for e in calls if e.get('upstream_called') and e['service'] == 'model' and e.get('estimated_cost') is None and e['request_id'] not in telemetry and e['request_id'] not in checked]
            if unpriced:
                checked.update(e['request_id'] for e in unpriced)
                cap_secrets = [settings.bifrost_api_key, settings.bifrost_admin_api_key, settings.tavily_api_key, capabilities.get(run_id)]
                metadata, _ = await bifrost.reconcile(settings, unpriced)
                for rid, item in metadata.items(): store.event(run_id, 'MODEL_TELEMETRY', request_id=rid, metadata=sanitize(item, cap_secrets), source='cost_cap_check')
            calls = [e for e in bifrost.enriched_events(store.events(run_id)) if e['event_type'] == 'CALL']
        known = [e.get('gateway_reported_cost') if e.get('gateway_reported_cost') is not None else e.get('estimated_cost') for e in calls if e.get('upstream_called') and e['service'] == 'model']
        known += [e.get('tool_cost') for e in calls if e.get('upstream_called') and e['service'] != 'model']
        if cap is not None and sum(c for c in known if c is not None) >= cap:
            store.event(run_id, 'BUDGET_EXCEEDED', service=service, ending='cost_cap')
            raise HTTPException(429, 'Observed cost cap exceeded; gateway virtual-key budgets are the hard backstop')
        index = len(existing) + 1
        plan = run.get('fault_plan')
        fault = decision(run['scenario_id'], service, index, run.get('seed', config.get('seed', 42)), plan)
        streamed = bool(payload.get('stream'))
        request_id = secrets.token_hex(16)
        sensitive = [settings.bifrost_api_key, settings.bifrost_admin_api_key, settings.tavily_api_key, capabilities.get(run_id)]
        start = time.perf_counter(); upstream = False; status = 200; usage = None; estimated = None
        body = {}; headers = {}; extra = {}; gateway_id = None
        try: summary = snapshots.effective_request(service, payload) if service != 'model' else None
        except TypeError: raise HTTPException(422, 'Invalid tool request parameters')
        if summary is None:
            summary = {'model': config.get('model', settings.bifrost_model) if config.get('model_policy', 'pinned') == 'pinned' else payload.get('model'), 'messages': payload.get('messages', []), 'tools': payload.get('tools', [])}
            if transport == 'anthropic': summary.update(transport='anthropic', system=payload.get('system'))
            if streamed: summary['stream'] = True
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
                    failover = fault and fault['intervention'] == 'provider_failover'
                    if mode == 'demo':
                        if failover: await asyncio.sleep(fault['parameters'].get('primary_seconds', 0.25))
                        body = demo_model(anthropic_as_openai(payload) if transport == 'anthropic' else payload, task)
                        if transport == 'anthropic': body = openai_as_anthropic(body)
                        if failover:
                            body['model'] = str(body.get('model')) + ' (fallback)'
                            extra['failover'] = {'primary': 'simulated-primary', 'primary_status': 503, 'served_by': body['model'], 'simulated': True}
                    else:
                        upstream = True
                        store.event(run_id, 'UPSTREAM_STARTED', request_id=request_id, service=service)
                        # run's pinned model is immutable, including after configuration changes.
                        pinned = config.get('model', settings.bifrost_model)
                        upstream_payload = payload
                        if failover:
                            # Point the request at a primary configured to fail and let the gateway's fallback chain serve it.
                            pinned = settings.outage_primary_model
                            upstream_payload = {**payload, 'fallbacks': [settings.fallback_model or config.get('model', settings.bifrost_model)]}
                        model_settings = settings.model_copy(update={'bifrost_model': pinned})
                        send = bifrost.forward_anthropic if transport == 'anthropic' else bifrost.forward
                        response = await send(model_settings, upstream_payload, run_id, request_id=request_id, model_policy='pinned' if failover else config.get('model_policy', 'pinned'))
                        status = response.status_code; body = response.json()
                        gateway_id = response.headers.get('x-bifrost-request-id') or response.headers.get('x-request-id') or request_id
                        if not isinstance(body, dict): raise ValueError('Expected model JSON object')
                        if failover: extra['failover'] = {'primary': pinned, 'fallbacks': upstream_payload['fallbacks'], 'served_by': body.get('model'), 'simulated': False}
                        usage = body.get('usage') if status < 400 else None
                        if isinstance(usage, dict) and transport == 'anthropic' and usage.get('input_tokens') is not None:
                            usage = {**usage, 'prompt_tokens': usage['input_tokens'], 'completion_tokens': usage.get('output_tokens'), 'total_tokens': usage['input_tokens'] + (usage.get('output_tokens') or 0)}
                        if usage and summary['model'] == settings.bifrost_model and not failover and settings.input_price_per_million is not None and settings.output_price_per_million is not None and usage.get('prompt_tokens') is not None and usage.get('completion_tokens') is not None:
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
            usage_block = body.get('usage') if isinstance(body, dict) else None
            credits = usage_block.get('credits') if upstream and isinstance(usage_block, dict) else (None if upstream else 0)
            if not upstream: extra.update(tool_credits=0, tool_cost=0)
            elif isinstance(credits, (int, float)) and not isinstance(credits, bool):
                extra['tool_credits'] = credits
                extra['tool_cost'] = credits * settings.tavily_credit_price_usd if settings.tavily_credit_price_usd is not None else None
        store.event(run_id, 'CALL', request_id=request_id, service=service, call_index=index, status_code=status,
            duration_ms=round((time.perf_counter() - start) * 1000, 3), upstream_called=upstream,
            gateway_request_id=gateway_id, usage=usage, estimated_cost=estimated, fault=fault,
            request_hash=digest(sanitize(payload)), request_summary=sanitize(summary, sensitive), response_summary=body,
            **({'streamed': 'buffered_replay'} if streamed and service == 'model' else {}), **extra)
        if service == 'model' and transport == 'anthropic' and status >= 400: body = as_anthropic_error(body)
        if service == 'model' and streamed and status < 400 and isinstance(body, dict):
            include_usage = bool((payload.get('stream_options') or {}).get('include_usage')) if isinstance(payload.get('stream_options'), dict) else False
            chunks = sse_replay(body, transport, include_usage)
            async def replay():
                for chunk in chunks: yield chunk
            return StreamingResponse(replay(), status_code=status, media_type='text/event-stream', headers={**headers, 'Cache-Control': 'no-cache'})
        return JSONResponse(body, status_code=status, headers=headers)

    @api.post('/proxy/{run_id}/{path:path}')
    async def proxy(run_id: str, path: str, request: Request):
        run = store.get('run', run_id)
        if not run: raise HTTPException(404, 'Unknown run')
        if run['status'] != 'running': raise HTTPException(409, 'Run is not active')
        # OpenAI/Tavily clients send a Bearer token; Anthropic clients send x-api-key.
        supplied = request.headers.get('authorization', '').removeprefix('Bearer ') or request.headers.get('x-api-key', '')
        expected = capabilities.get(run_id)
        if expected is None or not secrets.compare_digest(supplied, expected): raise HTTPException(401, 'Invalid run capability')
        if path not in ('v1/chat/completions', 'v1/messages', 'tavily/search', 'tavily/extract'): raise HTTPException(404, 'Unsupported proxy endpoint')
        if int(request.headers.get('content-length', 0)) > 100_000: raise HTTPException(413, 'Request too large')
        raw = await request.body()
        if len(raw) > 100_000: raise HTTPException(413, 'Request too large')
        try: payload = json.loads(raw)
        except ValueError: raise HTTPException(400, 'Invalid JSON')
        if not isinstance(payload, dict): raise HTTPException(422, 'Expected JSON object')
        service = 'model' if path.startswith('v1') else path.split('/')[-1]
        transport = 'anthropic' if path == 'v1/messages' else 'openai'
        async with locks.setdefault(run_id, asyncio.Lock()):
            if store.get('run', run_id)['status'] != 'running' or capabilities.get(run_id) != supplied: raise HTTPException(409, 'Run is no longer active')
            child = asyncio.create_task(handle(run, service, payload, transport))
            ACTIVE_REQUESTS.setdefault(run_id, set()).add(child)
            try: return await child
            finally:
                ACTIVE_REQUESTS.get(run_id, set()).discard(child)
    return api
