import json

import anyio
import httpx
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from backend.app.config import Settings
from backend.app.services import mcp_tools


def test_fastmcp_streamable_http_forwards_run_scoped_tools(monkeypatch):
    seen = []

    def proxy_handler(request):
        assert request.headers["authorization"] == "Bearer run-capability"
        body = json.loads(request.content)
        seen.append((request.url.path, body))
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"results": [{"url": "https://example.org"}]})
        return httpx.Response(200, json={"results": [{"url": body["urls"][0], "raw_content": "page"}]})

    def proxy_client(settings):
        return httpx.AsyncClient(
            timeout=settings.tool_timeout_seconds,
            transport=httpx.MockTransport(proxy_handler),
        )

    monkeypatch.setattr(mcp_tools, "_proxy_client", proxy_client)
    gateway, session_manager = mcp_tools.create_mcp_gateway(
        Settings(harness_base_url="http://harness", tool_timeout_seconds=4)
    )
    host = FastAPI()
    host.mount("/mcp/{run_id}", gateway)

    async def exercise():
        async with session_manager.run():
            async with httpx.AsyncClient(
                base_url="http://localhost:8000",
                transport=httpx.ASGITransport(app=host),
                headers={"Authorization": "Bearer run-capability"},
            ) as http_client:
                async with streamable_http_client(
                    "http://localhost:8000/mcp/run-123/", http_client=http_client
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        listed = await session.list_tools()
                        assert {tool.name for tool in listed.tools} == {
                            "web_search",
                            "web_extract",
                        }
                        search_tool = next(
                            tool for tool in listed.tools if tool.name == "web_search"
                        )
                        assert search_tool.inputSchema["properties"]["max_results"]["default"] == 5
                        search = await session.call_tool(
                            "web_search", {"query": "known query"}
                        )
                        extract = await session.call_tool(
                            "web_extract", {"urls": ["https://example.org"]}
                        )
                        assert search.isError is not True
                        assert extract.isError is not True

    anyio.run(exercise)
    assert seen == [
        (
            "/proxy/run-123/tavily/search",
            {"query": "known query", "max_results": 5},
        ),
        (
            "/proxy/run-123/tavily/extract",
            {"urls": ["https://example.org"]},
        ),
    ]


def test_mounted_gateway_reaches_actual_recording_proxy(tmp_path, monkeypatch):
    from backend.app.main import create_app
    from backend.app.database import Store
    store = Store(tmp_path / 'mounted-mcp.sqlite')
    configuration = Settings(harness_base_url='http://localhost:8000')
    host = create_app(store, configuration)
    def internal_proxy_client(settings):
        return httpx.AsyncClient(base_url=settings.harness_base_url, transport=httpx.ASGITransport(app=host))
    monkeypatch.setattr(mcp_tools, '_proxy_client', internal_proxy_client)
    async def exercise():
        async with host.router.lifespan_context(host):
            snap = next(s for s in _snapshots(store) if s.get('schema_version') == 2)
            task = store.get('task', 'research_001')
            store.put('run', {'id':'mounted-run','task_id':task['id'],'status':'running','scenario_id':'baseline','config':{'evidence_mode':'demo','snapshot_id':snap['snapshot_id']}})
            host.state.capabilities['mounted-run']='mounted-capability'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), headers={'Authorization':'Bearer mounted-capability'}) as client:
                async with streamable_http_client('http://localhost:8000/mcp/mounted-run/', http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool('web_search', {'query':task['query']})
                        assert result.isError is not True
                        assert len([e for e in store.events('mounted-run') if e['event_type']=='CALL']) == 1
                        assert store.events('mounted-run')[-1]['snapshot']=='hit'
    anyio.run(exercise)


def _snapshots(store):
    with store.connect() as db:
        return [json.loads(row[0]) for row in db.execute('SELECT data FROM snapshots')]
