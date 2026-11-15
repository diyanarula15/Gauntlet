import { AgentIdentity, BehaviorBadge } from "./ArenaPanels";
import { PixelIcon } from "./ArenaAssets";
import { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Cell,
} from "recharts";
import { ArrowRight, Check, Radio, ShieldAlert } from "lucide-react";
type Row = Record<string, any>;
const rate = (x: number | null | undefined) =>
  x == null ? "Not established" : `${(100 * x).toFixed(1)}%`;
const value = (x: number | null | undefined, unit = "") =>
  x == null
    ? "Unknown"
    : `${x.toLocaleString(undefined, { maximumFractionDigits: 2 })}${unit}`;
const money = (x: number | null | undefined) =>
  x == null ? "Unknown" : `$${x.toFixed(5)}`;

export function ReliabilityBoard({
  experiment,
  catalog,
  onInspect,
}: {
  experiment: Row;
  catalog: Row;
  onInspect: (run: Row) => void;
}) {
  const [tier, setTier] = useState("all"),
    [scenario, setScenario] = useState("all"),
    [rows, setRows] = useState<Row[]>(experiment.leaderboard),
    [sort, setSort] = useState("pass_3"),
    [weights, setWeights] = useState({ S: 40, R: 25, F: 20, E: 15 }),
    [drill, setDrill] = useState<string | null>(null),
    [error, setError] = useState("");
  const name = (id: string) =>
    catalog.agents.find((a: Row) => a.id === id)?.display_name || id;
  useEffect(() => {
    let disposed = false;
    const params = new URLSearchParams();
    if (tier !== "all") params.set("tier", tier);
    if (scenario !== "all") params.set("scenario", scenario);
    fetch(`/api/experiments/${experiment.id}/leaderboard?${params}`)
      .then(async (r) => {
        if (!r.ok) throw Error("Could not load filtered leaderboard");
        return r.json();
      })
      .then((data) => {
        if (!disposed) setRows(data.rows);
      })
      .catch((e) => {
        if (!disposed) setError(String(e));
      });
    return () => {
      disposed = true;
    };
  }, [experiment.id, experiment.completed_runs, tier, scenario]);
  const ranked = useMemo<Row[]>(
    () =>
      rows
        .map((row): Row => {
          const sum = Object.values(weights).reduce((a, b) => a + b, 0);
          const parts = row.score_components;
          const score =
            sum && Object.keys(weights).every((k) => parts[k] != null)
              ? (Object.entries(weights).reduce(
                  (total, [key, w]) => total + parts[key] * w,
                  0,
                ) /
                  sum) *
                100
              : null;
          return { ...row, viewer_score: score };
        })
        .sort((a, b) => (b[sort] ?? -Infinity) - (a[sort] ?? -Infinity)),
    [rows, weights, sort],
  );
  const plotted = ranked.filter(
    (r) => r.cost_per_success != null && r.clean_success != null,
  );
  const selectedRuns = experiment.runs.filter(
    (r: Row) =>
      (!drill || r.agent_id === drill) &&
      (scenario === "all" || r.scenario_id === scenario) &&
      (tier === "all" ||
        String(catalog.tasks.find((t: Row) => t.id === r.task_id)?.tier) ===
          tier),
  );
  return (
    <section className="panel">
      <div className="panel-title">
        <div>
          <h2>Reliability, beyond an average</h2>
          <p className="muted">
            pass^k = C(successes,k) / C(observed repeats,k), averaged across
            baseline tasks. Missing samples stay unavailable.
          </p>
        </div>
        <div className="filter-row">
          <label>
            Tier
            <select
              aria-label="Leaderboard tier"
              value={tier}
              onChange={(e) => setTier(e.target.value)}
            >
              <option value="all">All tiers</option>
              <option value="1">1 · lookup</option>
              <option value="2">2 · multi-hop</option>
            </select>
          </label>
          <label>
            Condition
            <select
              aria-label="Leaderboard condition"
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
            >
              <option value="all">All conditions</option>
              {experiment.config.scenario_ids.map((s: string) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </label>
          <label>
            Sort
            <select
              aria-label="Leaderboard sort"
              value={sort}
              onChange={(e) => setSort(e.target.value)}
            >
              <option value="pass_3">pass^3</option>
              <option value="clean_success">Clean success</option>
              <option value="fault_tolerance">Fault tolerance</option>
              <option value="viewer_score">Weighted score</option>
            </select>
          </label>
        </div>
      </div>
      {error && <p role="alert">{error}</p>}
      <div className="frontier-layout">
        <div>
          <h3>Success / cost frontier</h3>
          {plotted.length ? (
            <ResponsiveContainer width="100%" height={240}>
              <ScatterChart
                margin={{ top: 20, right: 30, left: 0, bottom: 15 }}
              >
                <CartesianGrid stroke="#D9D0BD" strokeDasharray="3 5" />
                <XAxis
                  type="number"
                  dataKey="cost_per_success"
                  name="Cost per success"
                  tickFormatter={(x) => "$" + x}
                  stroke="#5B5668"
                />
                <YAxis
                  type="number"
                  dataKey="clean_success"
                  name="Clean success"
                  domain={[0, 1]}
                  tickFormatter={(x) => rate(x)}
                  stroke="#5B5668"
                />
                <Tooltip
                  cursor={{ strokeDasharray: "3 3" }}
                  formatter={(x: any, key: any) =>
                    key === "Clean success" ? rate(x) : money(x)
                  }
                />
                <Scatter data={plotted}>
                  {plotted.map((r) => (
                    <Cell
                      key={r.agent_id}
                      fill={r.frontier ? "#2FB36A" : "#5B3FD6"}
                    />
                  ))}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          ) : (
            <div className="chart-empty">
              Cost frontier unavailable: complete monetary measurements are
              required. Demo costs are unknown.
            </div>
          )}
          <small>
            Green marks nondominated observations. Costs include tools when
            configured; different models are disclosed by policy.
          </small>
        </div>
        <div className="weights">
          <h3>Transparent score weights</h3>
          {Object.entries(weights).map(([key, w]) => (
            <label key={key}>
              <span>
                {
                  (
                    {
                      S: "Clean success",
                      R: "pass^3 reliability",
                      F: "Fault tolerance",
                      E: "Cost efficiency",
                    } as Row
                  )[key]
                }{" "}
                <b>{w}</b>
              </span>
              <input
                aria-label={`${key} score weight`}
                type="range"
                min="0"
                max="100"
                value={w}
                onChange={(e) =>
                  setWeights({ ...weights, [key]: Number(e.target.value) })
                }
              />
            </label>
          ))}
          <small>
            Weights are normalized to their sum. A score requires all four
            measured components; missing cost is never replaced with zero.
          </small>
        </div>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Agent</th>
              <th>S · clean</th>
              <th>pass^1</th>
              <th>pass^3</th>
              <th>pass^5</th>
              <th>Recovered</th>
              <th>F · acceptable</th>
              <th>Cost / success</th>
              <th>Observed / planned</th>
              <th>Score</th>
              <th>Inspect</th>
            </tr>
          </thead>
          <tbody>
            {ranked.map((r) => (
              <tr key={r.agent_id}>
                <td>
                  <AgentIdentity
                    id={r.agent_id}
                    name={name(r.agent_id)}
                    index={catalog.agents.findIndex(
                      (a: Row) => a.id === r.agent_id,
                    )}
                  />
                  <small>
                    {experiment.config.model_policy === "own"
                      ? "Agent-selected model"
                      : "Pinned: " + experiment.config.model}
                  </small>
                </td>
                <td>{rate(r.clean_success)}</td>
                <td>{rate(r.pass_1)}</td>
                <td className="violet-text">{rate(r.pass_3)}</td>
                <td>{rate(r.pass_5)}</td>
                <td>{rate(r.recovery_rate)}</td>
                <td>{rate(r.fault_tolerance)}</td>
                <td>{money(r.cost_per_success)}</td>
                <td>
                  {r.runs} / {r.planned_runs}
                </td>
                <td>{value(r.viewer_score)}</td>
                <td>
                  <button
                    className="textbutton"
                    onClick={() =>
                      setDrill(drill === r.agent_id ? null : r.agent_id)
                    }
                  >
                    Runs <ArrowRight size={12} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {drill && (
        <div className="drilldown">
          <h3>{name(drill)} · source observations</h3>
          <div className="run-chips">
            {selectedRuns.map((r: Row) => (
              <button
                key={r.id}
                className={
                  "run-chip " +
                  (r.evaluation?.outcome === "success" ? "pass" : "")
                }
                onClick={() => onInspect(r)}
              >
                {r.task_id} / {r.scenario_id} / {r.repetition + 1}
                <small>{r.evaluation?.outcome || r.status}</small>
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

export function RepeatComparison({
  experiment,
  catalog,
  onInspect,
}: {
  experiment: Row;
  catalog: Row;
  onInspect: (r: Row) => void;
}) {
  const [scenario, setScenario] = useState("baseline");
  const names = (id: string) =>
    catalog.agents.find((a: Row) => a.id === id)?.display_name || id;
  return (
    <section className="panel">
      <div className="panel-title">
        <div>
          <h2>One task. Every repeat.</h2>
          <p className="muted">
            Each square opens its run. Green passes; orange fails; outlined
            squares are incomplete. Safe failure is shown separately from
            factual success.
          </p>
        </div>
        <select
          aria-label="Comparison condition"
          value={scenario}
          onChange={(e) => setScenario(e.target.value)}
        >
          {experiment.config.scenario_ids.map((s: string) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Task</th>
              {experiment.config.agent_ids.map((id: string) => (
                <th key={id}>{names(id)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {experiment.config.task_ids.map((taskId: string) => (
              <tr key={taskId}>
                <td>
                  {catalog.tasks.find((t: Row) => t.id === taskId)?.title ||
                    taskId}
                </td>
                {experiment.config.agent_ids.map((agentId: string) => {
                  const runs = experiment.runs
                    .filter(
                      (r: Row) =>
                        r.task_id === taskId &&
                        r.agent_id === agentId &&
                        r.scenario_id === scenario,
                    )
                    .sort((a: Row, b: Row) => a.repetition - b.repetition);
                  const cell = experiment.leaderboard
                    .find((a: Row) => a.agent_id === agentId)
                    ?.cells.find(
                      (c: Row) =>
                        c.task_id === taskId && c.scenario_id === scenario,
                    );
                  return (
                    <td key={agentId}>
                      <div className="repeat-squares">
                        {runs.map((r: Row) => (
                          <button
                            key={r.id}
                            className={
                              "repeat-square " +
                              (["pending", "running", "cancelled"].includes(
                                r.status,
                              )
                                ? "pending"
                                : r.evaluation?.outcome === "success"
                                  ? "pass"
                                  : r.evaluation
                                    ? "fail"
                                    : "pending")
                            }
                            title={`Repeat ${r.repetition + 1}: ${r.evaluation?.outcome || r.status}; ${r.evaluation?.behavior || "pending"}`}
                            aria-label={`${names(agentId)} ${taskId} repeat ${r.repetition + 1}`}
                            onClick={() => onInspect(r)}
                          >
                            <PixelIcon
                              name="heart"
                              size={21}
                              filled={r.evaluation?.outcome === "success"}
                            />
                          </button>
                        ))}
                      </div>
                      <small>
                        pass^3 {rate(cell?.pass_3)} · {cell?.n || 0}/
                        {cell?.planned || runs.length} observed
                      </small>
                      <small>
                        {runs
                          .map((r: Row) => r.evaluation?.behavior)
                          .filter(Boolean)
                          .join(" · ")}
                      </small>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function GradingPanel({ run }: { run: Row }) {
  const metrics = run.metrics || {};
  return (
    <section className="grading-panel">
      <div className="panel-title">
        <h3>Why this run received its verdict</h3>
        <span className="badge">{run.ending || run.status}</span>
      </div>
      <div className="grade-grid">
        {Object.entries(run.evaluation?.grades || {}).map(
          ([key, grade]: [string, any]) => (
            <div className="grade" key={key}>
              <span>
                {grade.passed === true ? (
                  <Check size={14} />
                ) : (
                  <ShieldAlert size={14} />
                )}{" "}
                {key}
              </span>
              <b
                className={
                  grade.passed === true
                    ? "green"
                    : grade.passed === false
                      ? "orange"
                      : ""
                }
              >
                {grade.passed === true
                  ? "Passed"
                  : grade.passed === false
                    ? "Not passed"
                    : "Unverified"}
              </b>
              <p>{grade.detail}</p>
              {grade.unretrieved?.length > 0 && (
                <small>Never retrieved: {grade.unretrieved.join(", ")}</small>
              )}
            </div>
          ),
        )}
      </div>
      <div className="inspector-metrics">
        <span>
          Model steps <b>{metrics.steps ?? "—"}</b>
        </span>
        <span>
          Tools executed <b>{metrics.tool_calls ?? "—"}</b>
        </span>
        <span>
          Tool calls requested by model{" "}
          <b>{metrics.requested_tool_calls ?? "—"}</b>
        </span>
        <span>
          Input / output tokens{" "}
          <b>
            {value(metrics.input_tokens)} / {value(metrics.output_tokens)}
          </b>
        </span>
        <span>
          Model / tool / overhead time{" "}
          <b>
            {value(metrics.model_duration_ms, " ms")} /{" "}
            {value(metrics.tool_duration_ms, " ms")} /{" "}
            {value(metrics.overhead_duration_ms, " ms")}
          </b>
        </span>
        <span>
          Model cost · {metrics.cost_source || "unknown"}{" "}
          <b>{money(metrics.model_cost)}</b>
        </span>
        <span>
          Tool credits / total cost{" "}
          <b>
            {value(metrics.tool_credits)} / {money(metrics.total_cost)}
          </b>
        </span>
        <span>
          Accounting lifecycle{" "}
          <b>
            {metrics.accounting_complete === false
              ? "Interrupted"
              : "Complete observed events"}
          </b>
        </span>
      </div>
      <p className="muted">
        A direct-tool agent can execute tools without a model tool-call request.
        A difference is observable; it alone does not prove invented tool
        results. Grading checks registered facts and retrieved evidence, not
        private reasoning.
      </p>
    </section>
  );
}

export function LiveTrace({ runId }: { runId: string }) {
  const [events, setEvents] = useState<Row[]>([]),
    [error, setError] = useState(false);
  useEffect(() => {
    setEvents([]);
    setError(false);
    const stream = new EventSource(`/api/runs/${runId}/events`);
    stream.onmessage = (e) => {
      const next = JSON.parse(e.data);
      setEvents((rows) =>
        rows.some((row) => row.id === next.id) ? rows : [...rows, next],
      );
      if (next.event_type === "RUN_FINISHED") stream.close();
    };
    stream.onerror = () => {
      setError(true);
      stream.close();
    };
    return () => stream.close();
  }, [runId]);
  return (
    <section className="panel">
      <div className="panel-title">
        <h2>
          <Radio size={15} /> Live observable events
        </h2>
        <span className="badge">{runId.slice(0, 10)}</span>
      </div>
      <div className="live-events">
        {events.slice(-12).map((e) => (
          <div key={e.id} className={e.fault ? "orange" : ""}>
            <span>#{e.sequence_number}</span>
            <b>{e.event_type.replaceAll("_", " ")}</b>
            <span>{e.service || e.description || ""}</span>
            <small>{e.status_code ? `HTTP ${e.status_code}` : ""}</small>
          </div>
        ))}
        {!events.length && <p>Waiting for the first persisted event…</p>}
      </div>
      {error && (
        <small>
          Live stream ended. Experiment status continues to update through
          polling.
        </small>
      )}
    </section>
  );
}
