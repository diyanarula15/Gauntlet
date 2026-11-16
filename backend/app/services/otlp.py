"""OTLP/HTTP JSON trace ingestion: agents export spans, the harness stores them as run events."""

from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

EVENT_TYPE = 'AGENT_SPAN'
MAX_BODY_BYTES = 256 * 1024
MAX_SPANS_PER_REQUEST = 200
MAX_ATTRIBUTE_STRING = 2000
PROTOCOL_HINT = ('Only OTLP/HTTP JSON is supported; '
                 'set OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/json')


def _any_value(value):
    """Convert an OTLP JSON AnyValue into a plain python value."""
    if not isinstance(value, dict):
        return None
    if 'stringValue' in value:
        return str(value['stringValue'])[:MAX_ATTRIBUTE_STRING]
    if 'intValue' in value:
        # OTLP JSON encodes 64-bit integers as strings.
        try:
            return int(value['intValue'])
        except (TypeError, ValueError):
            return None
    if 'doubleValue' in value:
        try:
            return float(value['doubleValue'])
        except (TypeError, ValueError):
            return None
    if 'boolValue' in value:
        return bool(value['boolValue'])
    if 'arrayValue' in value:
        values = (value.get('arrayValue') or {}).get('values') or []
        return [_any_value(v) for v in values if isinstance(v, dict)]
    if 'kvlistValue' in value:
        return _flatten((value.get('kvlistValue') or {}).get('values'))
    if 'bytesValue' in value:
        return str(value['bytesValue'])[:MAX_ATTRIBUTE_STRING]
    return None


def _flatten(attributes):
    flat = {}
    for item in attributes or []:
        if isinstance(item, dict) and isinstance(item.get('key'), str):
            flat[item['key']] = _any_value(item.get('value'))
    return flat


def _nanos(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _span_event(service_name, span):
    start_ns = _nanos(span.get('startTimeUnixNano'))
    end_ns = _nanos(span.get('endTimeUnixNano'))
    duration_ms = round((end_ns - start_ns) / 1e6, 3) if start_ns is not None and end_ns is not None else None
    status = span.get('status')
    return {
        'trace_id': span.get('traceId'),
        'span_id': span.get('spanId'),
        'parent_span_id': span.get('parentSpanId') or None,
        'name': span.get('name'),
        'kind': span.get('kind'),
        'start_ns': start_ns,
        'end_ns': end_ns,
        'duration_ms': duration_ms,
        'status': status.get('code') if isinstance(status, dict) else None,
        'attributes': _flatten(span.get('attributes')),
        'service_name': service_name,
    }


def _iter_spans(payload):
    """Yield (service_name, span) for every span dict in the request, in order."""
    for resource in payload.get('resourceSpans') or []:
        if not isinstance(resource, dict):
            continue
        resource_attributes = _flatten((resource.get('resource') or {}).get('attributes'))
        service_name = resource_attributes.get('service.name')
        scopes = resource.get('scopeSpans')
        if scopes is None:
            scopes = resource.get('instrumentationLibrarySpans')  # legacy OTLP key
        for scope in scopes or []:
            if not isinstance(scope, dict):
                continue
            for span in scope.get('spans') or []:
                if isinstance(span, dict):
                    yield service_name, span


def otlp_router(store, capabilities) -> APIRouter:
    api = APIRouter()

    @api.post('/otlp/{run_id}/v1/traces')
    async def export_traces(run_id: str, request: Request):
        from ..engine.proxy import sanitize

        run = store.get('run', run_id)
        if not run:
            raise HTTPException(404, 'Unknown run')
        if run['status'] != 'running':
            raise HTTPException(409, 'Run is not active')
        supplied = request.headers.get('authorization', '').removeprefix('Bearer ')
        expected = capabilities.get(run_id)
        if expected is None or not secrets.compare_digest(supplied.encode(), expected.encode()):
            raise HTTPException(401, 'Invalid run capability')
        content_type = request.headers.get('content-type', '').split(';')[0].strip().lower()
        if content_type != 'application/json':
            raise HTTPException(415, PROTOCOL_HINT)
        declared = request.headers.get('content-length', '')
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            raise HTTPException(413, 'Trace export body too large')
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            raise HTTPException(413, 'Trace export body too large')
        try:
            payload = json.loads(raw)
        except ValueError:
            raise HTTPException(400, 'Invalid JSON')
        if not isinstance(payload, dict):
            raise HTTPException(400, 'Expected ExportTraceServiceRequest JSON object')
        if not isinstance(payload.get('resourceSpans', []), list):
            raise HTTPException(400, 'resourceSpans must be a list')

        spans = list(_iter_spans(payload))
        accepted = spans[:MAX_SPANS_PER_REQUEST]
        rejected = len(spans) - len(accepted)
        for service_name, span in accepted:
            fields = sanitize(_span_event(service_name, span), [expected])
            store.event(run_id, EVENT_TYPE, **fields)

        if rejected:
            body = {'partialSuccess': {
                'rejectedSpans': rejected,
                'errorMessage': f'accepted {len(accepted)} spans; limit is {MAX_SPANS_PER_REQUEST} per request',
            }}
        else:
            body = {'partialSuccess': {}}
        return JSONResponse(body, status_code=200)

    return api
