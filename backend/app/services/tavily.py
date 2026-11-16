import httpx


def normalize(query):
    return ' '.join(str(query).casefold().split())


async def forward(settings, operation, payload):
    from .snapshots import effective_request
    clean = effective_request(operation, payload)
    # The normalized query is only the snapshot key; send the agent's own wording upstream.
    if isinstance(payload.get('query'), str): clean['query'] = payload['query']
    clean['include_usage'] = True
    async with httpx.AsyncClient(timeout=settings.tool_timeout_seconds) as client:
        return await client.post('https://api.tavily.com/' + operation, json=clean,
                                 headers={'Authorization': 'Bearer ' + settings.tavily_api_key})
