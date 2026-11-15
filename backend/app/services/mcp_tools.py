"""Run-scoped FastMCP tools that forward through the local Tavily proxy."""

from __future__ import annotations

from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP
from starlette.requests import Request


def _proxy_client(settings: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=settings.tool_timeout_seconds + 2)


def _run_scope(ctx: Context) -> tuple[str, str]:
    request = ctx.request_context.request
    if not isinstance(request, Request):
        raise ValueError("MCP tool call is missing its HTTP request context")

    run_id = request.path_params.get("run_id")
    authorization = request.headers.get("authorization")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("MCP endpoint is missing the run ID path parameter")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ValueError("MCP tool call requires a bearer capability")
    return run_id, authorization


async def _forward(
    settings: Any,
    ctx: Context,
    operation: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    run_id, authorization = _run_scope(ctx)
    url = (
        f"{settings.harness_base_url.rstrip('/')}/proxy/{run_id}/tavily/{operation}"
    )
    async with _proxy_client(settings) as client:
        response = await client.post(
            url,
            headers={"Authorization": authorization},
            json=payload,
        )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("Tavily proxy response must be a JSON object")
    return result


def create_mcp_gateway(settings: Any) -> tuple[Any, Any]:
    """Build the stateless Streamable HTTP app and its SDK session manager.

    Mount the app at ``/mcp/{run_id}`` and enter the returned session manager's
    ``run()`` context from the host application's lifespan.
    """
    server = FastMCP(
        name="Faultline research tools",
        instructions="Search and extract through the run-scoped Faultline proxy.",
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
    )

    @server.tool(description="Search the web through this run's evidence proxy.")
    async def web_search(query: str, ctx: Context, max_results: int = 5) -> dict[str, Any]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if not 1 <= max_results <= 20:
            raise ValueError("max_results must be between 1 and 20")
        return await _forward(
            settings,
            ctx,
            "search",
            {"query": query, "max_results": max_results},
        )

    @server.tool(description="Extract page content through this run's evidence proxy.")
    async def web_extract(urls: list[str], ctx: Context) -> dict[str, Any]:
        if not urls or any(not isinstance(url, str) or not url.strip() for url in urls):
            raise ValueError("urls must be a nonempty list of URL strings")
        return await _forward(settings, ctx, "extract", {"urls": urls})

    app = server.streamable_http_app()
    return app, server.session_manager
