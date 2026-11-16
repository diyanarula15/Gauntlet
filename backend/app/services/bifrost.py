"""Bifrost inference and asynchronous per-request log reconciliation."""
import asyncio
import time
import httpx


def auth_headers(settings, admin=False):
    key = settings.bifrost_admin_api_key if admin else settings.bifrost_api_key
    return {'Authorization': 'Bearer ' + key} if key else {}


async def forward(settings, payload, run_id, request_id=None, model_policy='pinned'):
    headers = {'Content-Type': 'application/json', 'x-bf-lh-run-id': run_id, 'x-bf-session-id': run_id, **auth_headers(settings)}
    if request_id: headers['x-request-id'] = request_id
    body = {**payload, 'stream': False}
    if model_policy == 'pinned': body['model'] = settings.bifrost_model
    caller_cap = body.pop('max_tokens', None) or body.get('max_completion_tokens') or settings.max_output_tokens
    body['max_completion_tokens'] = min(int(caller_cap), settings.max_output_tokens)
    async with httpx.AsyncClient(timeout=settings.model_timeout_seconds) as client:
        return await client.post(settings.bifrost_base_url.rstrip('/') + '/v1/chat/completions', json=body, headers=headers)


async def forward_anthropic(settings, payload, run_id, request_id=None, model_policy='pinned'):
    """Native Anthropic Messages transport through Bifrost's Anthropic-compatible route."""
    headers = {'Content-Type': 'application/json', 'anthropic-version': '2023-06-01', 'x-bf-lh-run-id': run_id, 'x-bf-session-id': run_id, **auth_headers(settings)}
    if settings.bifrost_api_key: headers['x-api-key'] = settings.bifrost_api_key
    if request_id: headers['x-request-id'] = request_id
    body = {**payload, 'stream': False}
    if model_policy == 'pinned': body['model'] = settings.bifrost_model
    body['max_tokens'] = min(int(body.get('max_tokens') or settings.max_output_tokens), settings.max_output_tokens)
    async with httpx.AsyncClient(timeout=settings.model_timeout_seconds) as client:
        return await client.post(settings.bifrost_base_url.rstrip('/') + '/anthropic/v1/messages', json=body, headers=headers)


async def health(settings):
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(settings.bifrost_base_url.rstrip('/') + '/health', headers=auth_headers(settings))
        return {'connected': response.is_success, 'status_code': response.status_code, 'model': settings.bifrost_model, 'inference_verified': False, 'message': 'Reachability probe; provider inference and log access are verified separately.'}
    except httpx.HTTPError:
        return {'connected': False, 'model': settings.bifrost_model, 'inference_verified': False, 'message': 'Start Bifrost and configure a provider.'}


def log_metadata(row):
    # Fields verified against Bifrost log-store schema. Never infer money from arbitrary response fields.
    usage = row.get('token_usage') or {}
    if not usage and isinstance(row.get('usage'), dict): usage = row['usage']
    cost = row.get('cost')
    if isinstance(cost, dict): cost = cost.get('total_cost')
    if not isinstance(cost, (int, float)) or isinstance(cost, bool): cost = None
    return {'gateway_reported_cost': cost, 'usage': usage or None, 'provider': row.get('provider'),
            'model': row.get('model'), 'latency_ms': row.get('latency'), 'attempt_trail': row.get('attempt_trail'),
            'number_of_retries': row.get('number_of_retries'), 'gateway_request_id': row.get('id') or row.get('request_id')}


async def _reconcile(settings, calls, completed=None):
    pending = {e['request_id']: e for e in calls if e.get('upstream_called') and e.get('service') == 'model'}
    result = completed if completed is not None else {}; deadline = time.monotonic() + settings.bifrost_logs_timeout_seconds
    async with httpx.AsyncClient(timeout=1.5) as client:
        while pending and time.monotonic() < deadline:
            for request_id, event in list(pending.items()):
                query_id = event.get('gateway_request_id') or request_id
                try:
                    response = await client.get(settings.bifrost_base_url.rstrip('/') + '/api/logs', params={'request_id': query_id, 'limit': 1}, headers=auth_headers(settings, admin=True))
                    if response.status_code in (401, 403, 404): return result, 'Gateway log access unavailable; inference response usage retained.'
                    response.raise_for_status(); data = response.json()
                    rows = data.get('logs', [])
                    row = next((r for r in rows if r.get('id') == query_id or r.get('request_id') == query_id), None)
                    if row and row.get('status') not in ('processing', 'pending'):
                        item = log_metadata(row)
                        children_response = await client.get(settings.bifrost_base_url.rstrip('/') + '/api/logs', params={'parent_request_id': query_id, 'limit': 100}, headers=auth_headers(settings, admin=True))
                        if children_response.is_success:
                            children = children_response.json().get('logs', [])
                            children = list({child.get('id'): child for child in children if child.get('parent_request_id') == query_id}.values())
                            if any(child.get('status') in ('processing','pending') for child in children): continue
                            if children:
                                parts = [item, *[log_metadata(child) for child in children]]
                                item['gateway_reported_cost'] = sum(part['gateway_reported_cost'] for part in parts) if all(part['gateway_reported_cost'] is not None for part in parts) else None
                                usages = [part['usage'] for part in parts]
                                item['usage'] = {key: sum(u[key] for u in usages) for key in ('prompt_tokens','completion_tokens','total_tokens') if all(isinstance(u,dict) and u.get(key) is not None for u in usages)} or None
                                item['fallbacks'] = [log_metadata(child) for child in children]
                        else:
                            item['gateway_reported_cost'] = None
                            item['usage'] = None
                            item['fallback_lookup_complete'] = False
                        result[request_id] = item; pending.pop(request_id)
                except (httpx.HTTPError, ValueError, AttributeError, TypeError):
                    continue
                if time.monotonic() >= deadline: break
            if pending: await asyncio.sleep(.15)
    return result, 'complete' if not pending else 'Gateway log deadline reached; unmatched requests remain incomplete.'


def enriched_events(events):
    telemetry = {e['request_id']: e['metadata'] for e in events if e['event_type'] == 'MODEL_TELEMETRY'}
    result = []
    for event in events:
        if event['event_type'] == 'CALL' and event.get('request_id') in telemetry:
            metadata = telemetry[event['request_id']]
            event = {**event, 'gateway': metadata, 'gateway_reported_cost': metadata.get('gateway_reported_cost'), 'usage': metadata.get('usage') or event.get('usage')}
        result.append(event)
    return result


async def reconcile(settings, calls):
    completed = {}
    try:
        async with asyncio.timeout(settings.bifrost_logs_timeout_seconds):
            return await _reconcile(settings, calls, completed)
    except TimeoutError:
        return completed, 'Gateway log reconciliation deadline reached; response usage retained and gateway costs unavailable.'
