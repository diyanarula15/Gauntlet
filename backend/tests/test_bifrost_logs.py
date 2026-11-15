"""Regression contracts for asynchronous gateway telemetry and cost attribution."""
import asyncio
import copy
import json
import time

import httpx

from backend.app.config import Settings
from backend.app.services import bifrost


def install_transport(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(bifrost.httpx, 'AsyncClient', lambda **kwargs: client(
        transport=httpx.MockTransport(handler), **kwargs))


def model_call():
    return {'event_type': 'CALL', 'request_id': 'local', 'gateway_request_id': 'gateway',
            'service': 'model', 'upstream_called': True, 'usage': {'total_tokens': 15}}


def test_processing_logs_are_polled_and_fallback_cost_is_joined_without_mutation(monkeypatch):
    reads = []

    def handler(request):
        assert request.headers['Authorization'] == 'Bearer admin-session'
        assert 'inference-key' not in request.headers['Authorization']
        reads.append(dict(request.url.params))
        if request.url.params.get('request_id'):
            row = {'id': 'gateway', 'status': 'processing'} if len(reads) == 1 else {
                'id': 'gateway', 'status': 'error', 'cost': 0,
                'token_usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}
            return httpx.Response(200, json={'logs': [row]})
        assert request.url.params['parent_request_id'] == 'gateway'
        child = {'id': 'child', 'parent_request_id': 'gateway', 'status': 'success', 'cost': .02,
                 'token_usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}
        return httpx.Response(200, json={'logs': [child, child]})

    install_transport(monkeypatch, handler)
    settings = Settings(bifrost_api_key='inference-key', bifrost_admin_api_key='admin-session',
                        bifrost_logs_timeout_seconds=1)
    original = model_call()
    unchanged = copy.deepcopy(original)
    metadata, status = asyncio.run(bifrost.reconcile(settings, [original]))
    assert status == 'complete'
    assert [query.get('request_id') for query in reads[:2]] == ['gateway', 'gateway']
    assert metadata['local']['gateway_reported_cost'] == .02
    assert metadata['local']['usage']['total_tokens'] == 15
    assert len(metadata['local']['fallbacks']) == 1
    enriched = bifrost.enriched_events([original, {
        'event_type': 'MODEL_TELEMETRY', 'request_id': 'local', 'metadata': metadata['local']}])
    assert original == unchanged
    assert enriched[0]['gateway_reported_cost'] == .02


def test_unavailable_admin_logs_preserve_inference_usage_and_unknown_cost(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(401, json={'error': 'login required'}))
    metadata, message = asyncio.run(bifrost.reconcile(Settings(), [model_call()]))
    assert metadata == {} and 'unavailable' in message.lower()
    events = bifrost.enriched_events([model_call()])
    assert events[0]['usage'] == {'total_tokens': 15}
    assert events[0].get('gateway_reported_cost') is None


def test_log_reconciliation_has_a_total_deadline(monkeypatch):
    async def never_answers(request):
        await asyncio.Event().wait()

    install_transport(monkeypatch, never_answers)
    start = time.monotonic()
    metadata, message = asyncio.run(bifrost.reconcile(
        Settings(bifrost_logs_timeout_seconds=.03), [model_call()]))
    assert time.monotonic() - start < 1
    assert metadata == {} and 'deadline' in message.lower()


def test_failed_fallback_lookup_does_not_report_free_root_as_total_cost(monkeypatch):
    def handler(request):
        if request.url.params.get('request_id'):
            return httpx.Response(200, json={'logs': [{
                'id': 'gateway', 'status': 'error', 'cost': 0,
                'token_usage': {'total_tokens': 0}}]})
        return httpx.Response(503, json={'error': 'temporarily unavailable'})

    install_transport(monkeypatch, handler)
    metadata, message = asyncio.run(bifrost.reconcile(Settings(), [model_call()]))
    assert metadata['local']['gateway_reported_cost'] is None
    assert metadata['local']['fallback_lookup_complete'] is False
    assert metadata['local']['usage'] is None
    enriched = bifrost.enriched_events([model_call(), {
        'event_type': 'MODEL_TELEMETRY', 'request_id': 'local', 'metadata': metadata['local']}])
    assert enriched[0]['usage'] == {'total_tokens': 15}
    assert enriched[0]['gateway_reported_cost'] is None


def test_forward_preserves_small_caller_budget_and_run_correlation(monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        assert body['model'] == 'pinned-model'
        assert body['max_completion_tokens'] == 32
        assert 'max_tokens' not in body
        assert request.headers['x-bf-session-id'] == 'run-id'
        assert request.headers['x-request-id'] == 'local-request'
        return httpx.Response(200, json={'choices': []})

    install_transport(monkeypatch, handler)
    response = asyncio.run(bifrost.forward(
        Settings(bifrost_model='pinned-model', max_output_tokens=1024),
        {'model': 'agent-selected', 'max_tokens': 32}, 'run-id', request_id='local-request'))
    assert response.status_code == 200
