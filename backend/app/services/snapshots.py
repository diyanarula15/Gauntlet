"""Content-addressed evidence with full effective-request keys; no live fallback."""
import copy
import hashlib
import json
from .tavily import normalize


def effective_request(operation, payload):
    if operation == 'search':
        defaults = {'query': '', 'search_depth': 'basic', 'max_results': 5, 'include_answer': False, 'include_raw_content': False, 'topic': 'general', 'include_domains': [], 'exclude_domains': []}
        allowed = set(defaults) | {'time_range', 'start_date', 'end_date', 'country', 'language', 'chunks_per_source', 'include_images', 'include_image_descriptions', 'include_favicon', 'auto_parameters', 'exact_match', 'safe_search', 'include_published_date'}
    else:
        defaults = {'urls': [], 'extract_depth': 'basic', 'format': 'markdown', 'include_images': False}
        allowed = set(defaults) | {'query', 'chunks_per_source', 'include_favicon'}
    result = {**defaults, **{k: v for k, v in payload.items() if k in allowed}}
    if 'query' in result: result['query'] = normalize(result['query'])
    for field in ('include_domains', 'exclude_domains'):
        if field in result:
            value = result[field] or []
            result[field] = sorted(set([value] if isinstance(value, str) else value))
    return result


def request_key(operation, payload):
    canonical = json.dumps({'operation': operation, 'parameters': effective_request(operation, payload)}, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()


def response_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def build_demo(tasks):
    entries = {}
    for task in tasks:
        for query in (task['query'], task['question'], *task.get('query_aliases', [])):
            payload = {'query': query}
            body = copy.deepcopy(task['evidence'])
            entries[request_key('search', payload)] = {'operation': 'search', 'normalized_query': normalize(query), 'request_parameters': effective_request('search', payload), 'response': body, 'response_hash': response_hash(body), 'source_metadata': task['ground_truth_sources']}
        for page in task['evidence'].get('results', []):
            body = {'results': [{'url': page['url'], 'raw_content': page['content']}], 'failed_results': []}
            entries[request_key('extract', {'urls': [page['url']]})] = {'operation': 'extract', 'request_parameters': effective_request('extract', {'urls': [page['url']]}), 'response': body, 'response_hash': response_hash(body), 'source_metadata': [page['url']]}
    return {'snapshot_id': 'demo-' + response_hash(entries)[:16], 'schema_version': 2, 'task_suite_id': 'research-v2', 'capture_timestamp': None, 'provenance': 'Curated offline demonstration fixtures, not a live Tavily capture', 'entries': entries}


def lookup(snapshot, operation, payload):
    entry = snapshot.get('entries', {}).get(request_key(operation, payload)) if snapshot else None
    if entry and response_hash(entry['response']) != entry['response_hash']:
        raise ValueError('Evidence snapshot integrity check failed')
    if not entry and snapshot and snapshot.get('schema_version', 1) == 1 and operation == 'search':
        # Historical snapshots remain readable; their weaker query-only matching is flagged in traces.
        entry = snapshot.get('entries', {}).get(normalize(payload.get('query', '')))
    return copy.deepcopy(entry) if entry else None
