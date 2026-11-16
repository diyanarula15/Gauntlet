# FAULTLINE — The AI Agent Crash-Test Lab

**Don't just benchmark agents. Discover exactly how they fail.**

A research-agent sidecar implementing the supplied **Gauntlet — Agent Benchmarking Platform Plan & System Design.md**, with FAULTLINE's existing name and paired counterfactual experience retained. Model and tool traffic pass through run-scoped recording proxies. Success, model steps, tools, tokens, money, latency, citation grounding and fault behavior are measured outside agent reasoning loops.

## Interface

The UI follows the Day Arena reference: warm paper surfaces, dark ink borders, violet actions, pixel challenger sprites, hearts for repeat outcomes and clickable trace tiles. Jersey15, Silkscreen and IBM Plex fonts are self-hosted in `frontend/public/fonts` with their OFL licenses. All scores and run states come from the backend; the style-board specimen values are not used.

## Start

Dependencies and production frontend assets are installed in this workspace:

```sh
./scripts/start.sh
```

Open **http://127.0.0.1:8017**. Start script derives the callback URL from `PORT` unless explicitly overridden. One backend worker only. For a fresh checkout:

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cd frontend
npm ci
npm run build
cd ..
./scripts/start.sh
```

Verified with Python3.12 and Node26. For frontend development run `cd frontend && npm run dev` with the backend on8017. Stop/restart the backend after changing its configuration or trusted registry.

## Implemented architecture

| Area | Working implementation |
|---|---|
| Agent connection | Approved CLI, JSON-file/plain-text CLI, HTTP and Docker adapters; no arbitrary browser command execution |
| Reference agents | Custom httpx research flow with retries (5xx and 429 with Retry-After backoff) that also exports OTLP spans; deliberately fragile variant of it with no retries or result checking; OpenAI SDK function-calling fail-fast flow; genuine LangGraph StateGraph loop; Claude-native agent speaking the Anthropic Messages API with tool use |
| Model transports | OpenAI chat completions and native Anthropic Messages (`/v1/messages`, `x-api-key` auth) through the same recording proxy; `stream: true` is served as a buffered SSE replay of the complete upstream response (both wire formats) |
| Agent telemetry | OTLP/HTTP JSON trace ingestion at `/otlp/{run}/v1/traces` (protobuf is rejected with 415); spans land in the run timeline beside proxy-observed calls |
| Tool transports | Tavily-shaped REST and official FastMCP Streamable HTTP `web_search` / `web_extract`, both through the same recording proxy |
| Suite |12 tasks: 8 cited lookup/multi-source/numerical tasks with exact/numeric/set/regex specs; 2 unanswerable tasks where the right answer is to abstain; 2 tier-3 synthesis tasks graded by a weighted rubric judge |
| Controlled failures | All 9 architecture faults M1–M3/T1–T4/I1–I2, plus M4 (primary provider outage absorbed by the gateway fallback chain) and 6 historical scenarios; declarative triggers; probabilistic decisions seeded per repeat and shared across agents, so every agent faces identical fault draws |
| Dose-response | Sweep any model/tool fault at firing probabilities p ∈ (0,1]; per-agent success-vs-p curves with Wilson bands and a breaking point (smallest p where success drops below 50%) |
| Execution |3 concurrent runs by default; per-run serialized request ordering; model/tool/time/observed-cost caps; cancellation/process-group cleanup/output bounds |
| Evidence |Immutable capture snapshots keyed by endpoint and effective parameters; content-addressed curated demo fixtures; hash verification; strict misses |
| Telemetry |Immutable proxy events; Bifrost request/session correlation and asynchronous log reconciliation including fallback children; usage/cost completeness |
| Grading |Answer, actual retrieved-citation grounding, limits, behavior and (tier 3) rubric-judge grades saved separately; `correct_abstention` label for honest refusals on unanswerable tasks |
| Statistics |Exact McNemar test on runs paired by task and repeat, Holm-corrected, with a Newcombe interval for the difference; a gap is "established" only when the adjusted p < 0.05 |
| Diagnosis |Deterministic postmortem per run (headline, root cause, findings, step timeline) built only from recorded events; one-click replay with the same seed, fault plan and evidence, plus a reproduced / not-reproduced verdict |
| Reliability |Per-cell pass^1/3/5, Wilson intervals, latency median/p95, variation, robustness, recovery vs acceptable fault tolerance, cost/success frontier and transparent score |
| Interface |Benchmark setup, live SSE trace/progress, leaderboard with Score/S/R/F/E/steps/tool calls/tokens/cost/time, n and 95% Wilson interval beside clean success, "not established" when intervals overlap, filters/weights/drill-down, repeat grid, per-fault behaviour labels linking to example runs, structured inspector (messages, reply, requested tool calls, results, per-call tokens/cost/time, running cost, OTLP spans, judge criteria, streamed/Claude-native/failover badges), postmortem card, replay, paired diff and any-two-runs diff aligned by model step, significance panel, abstention column, breaking-point charts, dose-sweep setup, JSON and HTML export |
| Persistence |SQLite WAL preserves experiments, runs, grades, append-only events and snapshots across restart |

The contracts, verification commands and limitations are documented below. Internal architecture and review notes stay local.

## Offline demonstration and live providers

**Offline demo launches actual agent processes and measures their actual requests and elapsed time.** Its model is a clearly labelled evidence-copying simulator; its search responses are curated fixtures. It is a test of control flow and instrumentation, not an experiment on model intelligence, misinformation resistance or prompt-injection obedience. No provider tokens or invented monetary costs are reported.

For real providers:

1. Follow the [official Bifrost gateway setup](https://docs.getbifrost.ai/quickstart/gateway/setting-up): `npx -y @maximhq/bifrost`, or `docker run -p 8080:8080 maximhq/bifrost`.
2. Open http://localhost:8080, configure provider credentials and a tool-capable model such as `openai/gpt-4o-mini`. Verify inference in Bifrost first.
3. Copy `.env.example` to `.env`. Set BIFROST_BASE_URL, BIFROST_MODEL and TAVILY_API_KEY. Inference virtual-key authentication uses BIFROST_API_KEY when required.
4. For gateway monetary reports/retry trails, optionally set **BIFROST_ADMIN_API_KEY to an admin login/session Bearer token** authorized for `/api/logs`. An inference virtual key is not automatically an admin credential. Public `/health` reachability does not verify provider inference or logs authorization.
5. Restart and choose live evidence, or capture evidence then choose captured evidence + live Bifrost.

Inference path: agent → `/proxy/{run}/v1/chat/completions` → Bifrost `/v1/chat/completions` → provider. The proxy attaches documented `x-bf-session-id`, `x-request-id` and `x-bf-lh-run-id`; logs are joined by returned gateway request ID. Async log deadlines, processing rows, fallback children and unavailable telemetry are handled explicitly. See [Bifrost observability](https://docs.getbifrost.ai/features/observability/default) and [response headers](https://docs.getbifrost.ai/providers/response-headers).

Tavily requests use the [documented fixed-host Bearer HTTP API](https://docs.tavily.com/documentation/api-reference/endpoint/search), request `include_usage`, and record usage.credits. Tool USD estimates require an explicitly configured credit price. API credits are not dollars; Extract's billing rules are not guessed from a fixed per-URL price.

**No authenticated Bifrost inference or live Tavily request has been verified here: credentials are absent.** Provider HTTP contracts and log reconciliation use mocked transports. Genuine local subprocess, Docker and MCP execution is separately identified in verification records.

## Agent adapter contract

Trusted local registration is `backend/app/fixtures/agents.json`. Each entry specifies an ID, `status: approved`, adapter, command/URL/image, working directory, language/framework, I/O format and model policy. The browser selects IDs only.

Input supports both the original and architecture schema:

```json
{"task_id":"research_001","question":"...","prompt":"...","query":"...","max_steps":10,"limits":{"max_steps":10,"max_seconds":90,"max_cost_usd":null}}
```

Final JSON: `{"answer":"...","citations":["https://..."],"status":"completed"}` or `{"answer":"","status":"failed","error":"..."}`. Logging before the final JSON is permitted and captured with redaction and bounds. Approved plain-text output can be enabled explicitly. File adapters substitute literal `{task_file}` and `{out_file}` command arguments; `{python}` selects the backend interpreter. Execution uses argument arrays, never a shell.

Injected endpoints/credentials:

- OPENAI_BASE_URL and OPENAI_API_KEY: per-run model proxy and ephemeral run capability.
- TAVILY_BASE_URL / TAVILY_API_BASE_URL and TAVILY_API_KEY: explicitly consumed by bundled adapters, not assumed SDK environment behavior.
- TOOLS_MCP_URL: `http://127.0.0.1:8017/mcp/{run}/`, with the same Bearer capability.
- BIFROST_MODEL: configured model. Pinned policy rewrites requests; own policy lets approved agents choose and records the requested model.

HTTP entries point to an approved fixed URL accepting `{"task":{...},"environment":{...}}` and returning the final JSON object. Its implementation must honor these per-request settings without process-global environment mutation. Docker entries use an approved image/argument array; `DOCKER_HARNESS_BASE_URL` must be reachable from the container. A disposable local image was used to verify its stdin/stdout contract; provider callback reachability from arbitrary images is not guaranteed.

Provider credentials never enter agent processes. Adapters require configurable endpoints: a black box that hardcodes its provider URL cannot be instrumented without a config change. Agents using private tools have incomplete tool coverage. This is a trusted local tool, **not a strong security sandbox**; keep it on loopback.

## Fairness and reliability

A **step is one model attempt**. Tool calls and total observable calls are separate. For each agent/task/condition, `pass^k = C(c,k)/C(n,k)` for observed n≥k; otherwise unavailable. Canceled or unfinished runs are visible as incomplete. Recovery counts factual success after a fired fault; fault tolerance also includes explicit grounded safe failures.

Citation grounding checks every cited URL against pages the proxy returned during that exact run, then checks cited content against registered facts. It does not prove arbitrary semantic entailment. Exact accepted variants and known-conflict checks prevent several earlier false verdicts; contradictions/paraphrases outside that narrow specification can still be misgraded.

Model costs prefer reported Bifrost USD. Explicit prices allow labelled estimates from actual tokens; missing information remains unknown. Tool charges require actual credits and an explicit price. Interrupted/fallback-incomplete telemetry is disclosed. Observed cost caps are checked between calls using available costs; when a cap is set on a live run, the proxy fetches Bifrost's per-request cost from `/api/logs` before each call (needs BIFROST_ADMIN_API_KEY) so the cap can trip mid-run; **a Bifrost virtual-key budget is the hard monetary backstop**, since an already submitted request can exceed a local soft cap.

**Rubric judge.** Tier-3 answers are scored against weighted criteria. Live runs use one temperature-0 call to `JUDGE_MODEL` (default: the run model) through Bifrost, judging only against pages retrieved in that run; demo runs, and any judge error, fall back to a disclosed keyword rubric. Judge calls are recorded as `JUDGE_RESULT` and are not charged to the agent.

**Abstention.** Unanswerable tasks have evidence that looks relevant but lacks the fact. Abstention and over-answer rates use clean (baseline) runs only, since refusing because a tool is down is not calibration. The offline simulator copies evidence and never abstains, so demo agents over-answer; meaningful abstention numbers need a live model.

**Gateway failover (M4).** Live runs need `OUTAGE_PRIMARY_MODEL` (a model routed to a failing provider) and optionally `FALLBACK_MODEL`; the proxy sends the request with Bifrost `fallbacks` and records who served it. In demo mode the failover is simulated and labelled so.

Frozen replay does **not** silently go live on a miss, intentionally preserving the original fairness requirement where the newer document proposed an expanding cache. Exact supported queries/parameters are visible in snapshot records; different SDK-generated queries may miss. Legacy query-only snapshots remain readable for historical traces but must be recaptured for new frozen experiments. New demo snapshot IDs change with corpus content, preserving immutable history.

## Verification

```sh
.venv/bin/python -m pytest backend/tests -q
cd frontend && npm run build
cd ..
.venv/bin/python scripts/verify_e2e.py --output docs/verified-demo-v2.json
```

The last command needs the backend running. It verifies the original repeated12-run demonstration against the new engine. Independent review ran24/24 baselines across3 agents×8 tasks, then27 scenario runs. See [independent results](docs/independent-v2-verification.json).

Browser tooling is installed separately under `/tmp/faultline-browser`. With Playwright installed, run `NODE_PATH=/path/to/node_modules node scripts/browser-check-v2.cjs`; it exercises27 actual subprocess runs including nine20-second latency faults, live traces, pass^3 controls, repeat grid, grader detail, comparison and both report formats. See v2 verification (local documentation).

## Deliberate limits / next work

No authenticated live-provider validation: the LLM judge, Anthropic route through Bifrost and live M4 failover are tested only with mocks and offline fallbacks. Streaming is a buffered replay, so time-to-first-token is not measured. OTLP accepts JSON only. Distributed jobs and hosted deployment are out of scope by design: the trust model is a single loopback process. Gateway logs/admin contracts are version tolerant and tested with fixtures; exact deployment behavior still needs credentials. SQLite logical entities use typed JSON documents rather than a destructive seven-table migration; stored event names are uppercase (`RUN_STARTED`, `CALL`, …), and every event read through the API also carries the spec's `type` (`run_start`, `model_call`, `tool_call`, `fault`, `limit`, `log`, `run_end`), `seq` and `ts_start`; compression is not implemented. A larger Recharts bundle produces a non-blocking size warning. Replays reuse seed, fault plan and evidence but cannot make a live model deterministic; the reproduced verdict reports what happened.

Demo media and internal planning/review Markdown stay local and are excluded from Git.
