import React from "react";

type Obj = Record<string, any>;

const clip = (text: unknown, max: number) => {
  const s = typeof text === "string" ? text : "";
  return s.length > max ? `${s.slice(0, max)}…` : s;
};

const textOf = (content: unknown): string => {
  if (typeof content === "string") return content;
  if (Array.isArray(content))
    return content
      .map((part: any) =>
        typeof part === "string" ? part : (part?.text ?? ""),
      )
      .filter(Boolean)
      .join("\n");
  if (content == null) return "";
  try {
    return JSON.stringify(content);
  } catch {
    return String(content);
  }
};

const fmtMs = (v: unknown) =>
  typeof v === "number" && Number.isFinite(v)
    ? `${Math.round(v).toLocaleString()} ms`
    : "unknown";

export const formatCost = (v: number | null | undefined) =>
  v == null ? "unknown" : `$${v.toFixed(6)}`;

export const humanize = (s: unknown) => {
  if (typeof s !== "string" || !s.trim()) return "No label";
  const t = s.replaceAll("_", " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
};

export const isCallEvent = (e: Obj) =>
  e?.event_type === "CALL" ||
  e?.type === "model_call" ||
  e?.type === "tool_call" ||
  ["model", "search", "extract"].includes(e?.service);

export const isModelEvent = (e: Obj) =>
  e?.service === "model" || e?.type === "model_call";

// gateway_reported_cost ?? estimated_cost ?? tool_cost; null when unknown.
export const eventCost = (e: Obj): number | null => {
  for (const v of [e?.gateway_reported_cost, e?.estimated_cost, e?.tool_cost]) {
    if (typeof v === "number" && Number.isFinite(v)) return v;
  }
  return null;
};

export const eventTokens = (e: Obj) => {
  const u = e?.usage;
  const input = typeof u?.prompt_tokens === "number" ? u.prompt_tokens : null;
  const output =
    typeof u?.completion_tokens === "number" ? u.completion_tokens : null;
  const total =
    typeof u?.total_tokens === "number"
      ? u.total_tokens
      : input != null && output != null
        ? input + output
        : null;
  return { input, output, total };
};

// Run-level totals across call events. Unknown costs/tokens are counted, not guessed.
export const traceTotals = (events: Obj[] = []) => {
  let cost = 0;
  let unknownCost = 0;
  let tokens = 0;
  let unknownTokens = 0;
  let knownCost = 0;
  let knownTokens = 0;
  for (const e of Array.isArray(events) ? events : []) {
    if (!isCallEvent(e)) continue;
    const c = eventCost(e);
    if (c == null) unknownCost += 1;
    else {
      cost += c;
      knownCost += 1;
    }
    if (isModelEvent(e)) {
      const t = eventTokens(e).total;
      if (t == null) unknownTokens += 1;
      else {
        tokens += t;
        knownTokens += 1;
      }
    }
  }
  return { cost, unknownCost, tokens, unknownTokens, knownCost, knownTokens };
};

export function TraceTotalsStats({ events }: { events: Obj[] }) {
  const t = traceTotals(events);
  const costText =
    t.knownCost === 0 && t.unknownCost > 0
      ? "unknown"
      : t.unknownCost > 0
      ? `≥ $${t.cost.toFixed(6)} (some unknown)`
      : `$${t.cost.toFixed(6)}`;
  const tokenText = t.knownTokens === 0 && t.unknownTokens > 0 ? "unknown" : `${t.tokens.toLocaleString()}${t.unknownTokens > 0 ? " (some unknown)" : ""}`;
  return (
    <div className="stats compact">
      <div className="stat">
        <div>
          <span>Run cost</span>
        </div>
        <strong>{costText}</strong>
        <small>Sum of known per-call costs</small>
      </div>
      <div className="stat">
        <div>
          <span>Total tokens</span>
        </div>
        <strong>{tokenText}</strong>
        <small>Model calls, prompt + completion</small>
      </div>
    </div>
  );
}

export function CompareWithSelect({
  runs,
  trace,
  label,
  onChoose,
}: {
  runs: Obj[];
  trace: Obj;
  label: (r: Obj) => string;
  onChoose: (id: string) => void;
}) {
  const candidates = (Array.isArray(runs) ? runs : []).filter(
    (r) => r?.id && r.id !== trace?.id && r.task_id === trace?.task_id,
  );
  if (!candidates.length) return null;
  return (
    <select
      aria-label="Compare with another run"
      value=""
      onChange={(e) => {
        if (e.target.value) onChoose(e.target.value);
      }}
    >
      <option value="">Compare with…</option>
      {candidates.map((r) => (
        <option key={r.id} value={r.id}>
          {label(r)}
        </option>
      ))}
    </select>
  );
}

export function EventDetail({ e }: { e: Obj }) {
  const req: Obj = e?.request_summary || {};
  const res: Obj = e?.response_summary || {};
  const call = isCallEvent(e);
  const model = isModelEvent(e);
  const message: Obj | undefined = res?.choices?.[0]?.message;
  const messages: Obj[] = Array.isArray(req.messages) ? req.messages : [];
  const toolCalls: Obj[] = Array.isArray(message?.tool_calls)
    ? message.tool_calls
    : [];
  const results: Obj[] = Array.isArray(res.results) ? res.results : [];
  const urls: unknown[] = Array.isArray(req.urls) ? req.urls : [];
  const tokens = eventTokens(e);
  const cost = eventCost(e);
  const span = e?.event_type === "AGENT_SPAN";
  const judgement: Obj | null =
    e?.event_type === "JUDGE_RESULT" && e.judgement && typeof e.judgement === "object"
      ? e.judgement
      : null;
  const failover: Obj | null =
    model && e?.failover && typeof e.failover === "object" ? e.failover : null;

  return (
    <div className="inspector">
      {e?.fault && (
        <div className="fault-note injected">
          <b>Injected fault</b>
          <span>
            {typeof e.fault.intervention === "string"
              ? e.fault.intervention
              : clip(JSON.stringify(e.fault), 300)}
          </span>
        </div>
      )}
      {call && (
        <div className="inspector-meta">
          <span>
            Tokens in: <b>{tokens.input ?? "unknown"}</b>
          </span>
          <span>
            Tokens out: <b>{tokens.output ?? "unknown"}</b>
          </span>
          <span>
            Cost: <b>{formatCost(cost)}</b>
          </span>
          <span>
            Duration: <b>{fmtMs(e?.duration_ms)}</b>
          </span>
        </div>
      )}
      {model && (
        <>
          {(e?.streamed === "buffered_replay" ||
            req.transport === "anthropic" ||
            failover) && (
            <div className="inspector-badges">
              {e?.streamed === "buffered_replay" && (
                <span className="badge model-badge">streamed (buffered)</span>
              )}
              {req.transport === "anthropic" && (
                <span className="badge model-badge">Claude-native</span>
              )}
              {failover && (
                <span
                  className={
                    "badge model-badge failover" +
                    (failover.simulated ? " simulated" : "")
                  }
                  title={`Primary ${String(failover.primary ?? "unknown")}${failover.primary_status != null ? ` returned ${String(failover.primary_status)}` : ""}`}
                >
                  gateway failover → {String(failover.served_by ?? "unknown")}
                  {failover.simulated ? " · simulated" : ""}
                </span>
              )}
            </div>
          )}
          <div className="inspector-block">
            <b>Request messages</b>
            {messages.length ? (
              messages.map((m, i) => (
                <div className="inspector-msg" key={i}>
                  <small>{typeof m?.role === "string" ? m.role : "message"}</small>
                  <pre>{clip(textOf(m?.content), 600) || "(empty)"}</pre>
                </div>
              ))
            ) : (
              <p>No messages recorded</p>
            )}
          </div>
          <div className="inspector-block">
            <b>Reply</b>
            <pre>{clip(textOf(message?.content), 1200) || "(no text reply)"}</pre>
          </div>
          <div className="inspector-block">
            <b>Requested tool calls</b>
            {toolCalls.length ? (
              toolCalls.map((tc, i) => (
                <div className="inspector-msg" key={i}>
                  <small>{tc?.function?.name ?? tc?.name ?? "unknown function"}</small>
                  <pre>
                    {clip(textOf(tc?.function?.arguments ?? tc?.arguments), 600) ||
                      "(no arguments)"}
                  </pre>
                </div>
              ))
            ) : (
              <p>No tool calls requested</p>
            )}
          </div>
        </>
      )}
      {call && !model && (
        <>
          <div className="inspector-block">
            <b>Arguments</b>
            {req.query != null && <p>Query: {String(req.query)}</p>}
            {req.max_results != null && <p>Max results: {String(req.max_results)}</p>}
            {urls.length > 0 && <p>URLs: {urls.map((u) => String(u)).join(", ")}</p>}
            {req.query == null && req.max_results == null && urls.length === 0 && (
              <p>No arguments recorded</p>
            )}
          </div>
          <p className="inspector-meta">
            Snapshot: <b>{e?.snapshot ?? "not recorded"}</b>
          </p>
          <div className="inspector-block">
            <b>Results</b>
            {results.length ? (
              results.map((r, i) => (
                <div className="inspector-msg" key={i}>
                  <small>{r?.title || "Untitled"}</small>
                  {r?.url && (
                    <a href={String(r.url)} target="_blank" rel="noreferrer">
                      {String(r.url)}
                    </a>
                  )}
                  {r?.content && <p>{clip(String(r.content), 200)}</p>}
                </div>
              ))
            ) : (
              <p>No results returned</p>
            )}
          </div>
        </>
      )}
      {span && <SpanDetail e={e} />}
      {judgement && <JudgementDetail j={judgement} />}
      {!call && !span && !judgement && (
        <p>{e?.description || e?.event_type || "Harness event"}</p>
      )}
      <details className="raw-event">
        <summary>Raw event</summary>
        <pre>{JSON.stringify(e, null, 2)}</pre>
      </details>
    </div>
  );
}

export function FaultCellLabel({ label }: { label?: Obj | null }) {
  if (!label) return null;
  return (
    <small className="fault-label">
      {humanize(label.dominant)} · n={label.n ?? "?"}
    </small>
  );
}

const BEHAVIOR_LABELS: Record<string, string> = {
  recovered: "Recovered",
  safe_failure: "Safe failure",
  silent_failure: "Silent failure",
  canary_detected: "Canary detected",
  runaway: "Runaway",
  crash: "Crash",
  honest_failure: "Reported failure",
  correct_abstention: "Correct abstention",
  clean_success: "Clean success",
  unknown: "Unclassified",
};

export const behaviorLabel = (behavior: unknown): string => {
  if (typeof behavior !== "string" || !behavior) return "Unclassified";
  return BEHAVIOR_LABELS[behavior] ?? humanize(behavior);
};

// "react · T1 · r2": agent, scenario and 1-based repeat of a run row.
export const runLabel = (r: Obj | null | undefined) =>
  `${r?.agent_id ?? "unknown agent"} · ${r?.scenario_id ?? "unknown scenario"} · r${Number(r?.repetition ?? 0) + 1}`;

function SpanDetail({ e }: { e: Obj }) {
  const attrs: Obj =
    e?.attributes && typeof e.attributes === "object" && !Array.isArray(e.attributes)
      ? e.attributes
      : {};
  const entries = Object.entries(attrs);
  return (
    <div className="inspector-block span-block">
      <b>Agent span</b>
      <div className="inspector-msg">
        <small>{String(e?.name ?? "unnamed span")}</small>
        <div className="inspector-meta">
          <span>
            Duration: <b>{fmtMs(e?.duration_ms)}</b>
          </span>
          <span>
            Service: <b>{String(e?.service_name ?? "unknown")}</b>
          </span>
          <span>
            Status: <b>{String(e?.status ?? "unknown")}</b>
          </span>
        </div>
        {entries.length ? (
          <dl className="span-attrs">
            {entries.map(([k, v]) => (
              <React.Fragment key={k}>
                <dt>{k}</dt>
                <dd>{clip(textOf(v), 300)}</dd>
              </React.Fragment>
            ))}
          </dl>
        ) : (
          <p>No attributes recorded</p>
        )}
        <small>
          trace {String(e?.trace_id ?? "—")} · span {String(e?.span_id ?? "—")}
          {e?.parent_span_id ? ` · parent ${String(e.parent_span_id)}` : ""}
        </small>
      </div>
    </div>
  );
}

function JudgementDetail({ j }: { j: Obj }) {
  const criteria: Obj[] = Array.isArray(j.criteria) ? j.criteria : [];
  const verdict =
    j.passed === true ? "passed" : j.passed === false ? "not passed" : "unknown";
  return (
    <div className="inspector-block judge-block">
      <b>Judge verdict · {verdict}</b>
      <div className="inspector-meta">
        <span>
          Score: <b>{j.score == null ? "unknown" : String(j.score)}</b>
        </span>
        <span>
          Method: <b>{String(j.method ?? "unknown")}</b>
        </span>
        <span>
          Model: <b>{String(j.model ?? "unknown")}</b>
        </span>
      </div>
      {j.judge_error && (
        <p className="judge-error">Judge error: {String(j.judge_error)}</p>
      )}
      {criteria.length ? (
        <div className="table-scroll">
          <table className="judge-table">
            <thead>
              <tr>
                <th>Criterion</th>
                <th>Met</th>
                <th>Weight</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {criteria.map((c, i) => (
                <tr key={String(c?.id ?? i)}>
                  <td>{String(c?.id ?? "—")}</td>
                  <td className={c?.met ? "met-yes" : "met-no"}>
                    {c?.met ? "✓" : "✗"}
                  </td>
                  <td>{c?.weight == null ? "—" : String(c.weight)}</td>
                  <td>{clip(String(c?.reason ?? ""), 300) || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p>No criteria recorded</p>
      )}
    </div>
  );
}

const SEVERITIES = ["info", "warn", "bad", "good"];

// Scrolls to the event row rendered with data-seq; opens it if collapsed.
const jumpToSeq = (seq: unknown) => {
  if (seq == null) return;
  const el = document.querySelector<HTMLDetailsElement>(
    `[data-seq="${String(seq).replace(/"/g, '\\"')}"]`,
  );
  if (!el) return;
  el.open = true;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
};

export function Postmortem({ pm }: { pm: Obj }) {
  const timeline: Obj[] = Array.isArray(pm?.timeline) ? pm.timeline : [];
  const findings: string[] = Array.isArray(pm?.findings)
    ? pm.findings.map((f: unknown) => String(f))
    : [];
  const root: Obj | null =
    pm?.root_cause && typeof pm.root_cause === "object" ? pm.root_cause : null;
  const verdict = typeof pm?.verdict === "string" ? pm.verdict : "unknown";
  return (
    <section className="postmortem" aria-label="Postmortem">
      <div className="postmortem-head">
        <p className="eyebrow">POSTMORTEM</p>
        <span className={"badge behavior-" + verdict}>{behaviorLabel(verdict)}</span>
      </div>
      <h3 className="postmortem-headline">
        {String(pm?.headline ?? "No headline")}
      </h3>
      <p className="postmortem-root">
        <b>Root cause</b>{" "}
        {root ? (
          <>
            <button
              type="button"
              className="textbutton"
              onClick={() => jumpToSeq(root.seq)}
            >
              step {String(root.seq ?? "?")}
            </button>{" "}
            {String(root.text ?? "")}
          </>
        ) : (
          "Not identified"
        )}
      </p>
      {findings.length > 0 && (
        <ul className="postmortem-findings">
          {findings.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      )}
      {timeline.length > 0 && (
        <ol className="postmortem-timeline">
          {timeline.map((t, i) => {
            const sev = SEVERITIES.includes(t?.severity) ? t.severity : "info";
            return (
              <li key={i}>
                <button
                  type="button"
                  className={"pm-step sev-" + sev}
                  title={typeof t?.kind === "string" ? t.kind : undefined}
                  onClick={() => jumpToSeq(t?.seq)}
                >
                  <span className="pm-step-num">
                    step {String(t?.step ?? t?.seq ?? i + 1)}
                  </span>
                  <span>{String(t?.text ?? "")}</span>
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
