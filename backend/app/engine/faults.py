"""Declarative fault schedules keyed by run-local request count and seed."""
import hashlib
import json
from ..config import ROOT


def registry():
    return {s['id']: s for s in json.loads((ROOT / 'backend/app/fixtures/scenarios.json').read_text())}


def triggered(trigger, index, seed, fault_id):
    kind = trigger.get('kind')
    if kind == 'always': return True
    if kind == 'first_n': return index <= trigger.get('n', 1)
    if kind == 'nth': return index == trigger.get('n', 1)
    if kind == 'probability':
        value = int(hashlib.sha256(f'{seed}:{fault_id}:{index}'.encode()).hexdigest()[:16], 16) / 2**64
        return value < trigger.get('p', 0)
    return index <= trigger['request_number_lte'] if 'request_number_lte' in trigger else index == trigger.get('request_number', -1)


def decision(scenario, service, index, seed=42, plan=None):
    plan = plan or registry().get(scenario, {})
    target = plan.get('service', plan.get('target'))
    target = {'web_search': 'search', 'web_extract': 'extract'}.get(target, target)
    if target != service or not triggered(plan.get('trigger', {}), index, seed, scenario): return None
    effect = plan.get('effect', {})
    intervention = effect.get('kind', plan.get('intervention'))
    params = plan.get('parameters', {})
    if intervention == 'http_error':
        code = effect.get('status', params.get('status_code', 503))
        return {'status': code, 'error': effect.get('body', params.get('message', 'Controlled dependency failure')), 'intervention': 'http_error', 'headers': effect.get('headers', {})}
    if intervention == 'timeout_simulation': return {'status': 504, 'error': 'Explicit simulated tool timeout (no upstream call)', 'intervention': intervention}
    mapped = {'replace_snippet': 'misleading_evidence', 'append_untrusted_instruction': 'prompt_injection'}
    return {'status': 200, 'intervention': mapped.get(intervention, intervention), 'parameters': effect}
