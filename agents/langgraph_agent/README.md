# LangGraph research agent

This CLI agent uses a real LangGraph `StateGraph` to alternate between model
calls and the proxied `web_search` tool. It reads one task JSON object from
stdin and writes one JSON result to stdout, matching the other demo agents.

Install the framework in the project virtual environment with:

```sh
.venv/bin/pip install langgraph==1.2.22
```

It uses the existing `openai` and `httpx` packages. Model calls go through
`OPENAI_BASE_URL`; search goes through `TAVILY_BASE_URL`. Both have finite
timeouts (25 seconds for model calls and 12 seconds for search by default;
`FAULTLINE_SEARCH_TIMEOUT` can override the search timeout). Retries are
off by default; `LANGGRAPH_MODEL_RETRIES` and `LANGGRAPH_SEARCH_RETRIES` can
set a bounded value from 0 to 3.
