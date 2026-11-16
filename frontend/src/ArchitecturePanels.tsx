import "./research.css";
import { AgentIdentity, BehaviorBadge, agentColors } from "./ArenaPanels";
import { PixelIcon } from "./ArenaAssets";
import { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  ComposedChart,
  Area,
  Line,
  ReferenceLine,
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
const dashNum = (x: number | null | undefined) =>
  x == null ? "—" : x.toLocaleString(undefined, { maximumFractionDigits: 2 });
const dashPct = (x: number | null | undefined) =>
  x == null ? "—" : `${(100 * x).toFixed(1)}%`;
const dashMoney = (x: number | null | undefined) =>
  x == null ? "—" : `$${x.toFixed(5)}`;
const dashSecs = (ms: number | null | undefined) =>
  ms == null ? "—" : `${(ms / 1000).toFixed(1)} s`;
const interval = (x: unknown): [number, number] | null =>
  Array.isArray(x) &&
  x.length === 2 &&
  typeof x[0] === "number" &&
  typeof x[1] === "number"
    ? [x[0], x[1]]
    : null;
const ciText = (x: unknown) => {
  const ci = interval(x);
  return ci ? ` [${dashPct(ci[0])}–${dashPct(ci[1])}]` : "";
};
// asc: lower is better, so ascending order. Missing values always sort last.
const SORTS: Record<
  string,
  { label: string; get: (r: Row) => number | null | undefined; asc?: boolean }
> = {
  pass_3: { label: "pass^3", get: (r) => r.pass_3 },
  abstention: {
    label: "Abstention (highest)",
    get: (r) => r.abstention_rate,
  },
  clean_success: { label: "Clean success", get: (r) => r.clean_success },
  fault_tolerance: { label: "Fault tolerance", get: (r) => r.fault_tolerance },
  viewer_score: { label: "Weighted score", get: (r) => r.viewer_score },
  efficiency: { label: "Efficiency (E)", get: (r) => r.score_components?.E },
  median_steps: { label: "Steps (fewest)", get: (r) => r.median_steps, asc: true },
  median_tool_calls: {
    label: "Tool calls (fewest)",
    get: (r) => r.median_tool_calls,
    asc: true,
  },
  median_tokens: {
    label: "Tokens (fewest)",
    get: (r) => r.median_tokens,
    asc: true,
  },
  cost_per_success: {
    label: "Cost / success (lowest)",
    get: (r) => r.cost_per_success,
    asc: true,
  },
  median_latency_ms: {
    label: "Time (fastest)",
    get: (r) => r.median_latency_ms,
    asc: true,
  },
};

const signedPts = (x: number | null | undefined) =>
  x == null
    ? "—"
    : `${x >= 0 ? "+" : ""}${(100 * x).toFixed(1)}`;
const pValue = (x: number | null | undefined) =>
  x == null ? "—" : x.toFixed(4);
const behaviorText = (b: string) =>
  b === "correct_abstention" ? "Correct abstention" : b;
const judgeMethod = (m: string | null | undefined) =>
  m === "llm_rubric_judge"
    ? "LLM judge"
    : m === "keyword_rubric_fallback"
      ? "Keyword fallback (offline)"
      : m || "Unknown";

// McNemar verdicts on paired baseline runs. diff is taken as rate(a) - rate(b);
// comparisons are re-oriented so the leader is always listed first.
function Significance({
  significance,
  name,
}: {
  significance: Row | null;
  name: (id: string) => string;
}) {
  const comparisons: Row[] = significance?.comparisons ?? [];
  const leader: string | undefined = significance?.leader;
  return (
    <div className="research-block" aria-label="Significance">
      <h3>Is the gap real?</h3>
      {comparisons.length ? (
        <ul className="sig-list">
          {comparisons.map((c: Row) => {
            const flip = leader === c.agent_b && leader !== c.agent_a;
            const lead = flip ? c.agent_b : c.agent_a;
            const other = flip ? c.agent_a : c.agent_b;
            const d = flip ? -c.diff : c.diff;
            const lo = flip ? -c.diff_hi : c.diff_lo;
            const hi = flip ? -c.diff_lo : c.diff_hi;
            return (
              <li
                className="sig-row"
                key={`${c.agent_a}-${c.agent_b}`}
              >
                <span>
                  <b>
                    {name(lead)} vs {name(other)}
                  </b>
                  : {signedPts(d)} pts [{signedPts(lo)}, {signedPts(hi)}] ·
                  McNemar p = {pValue(c.p_value)} (Holm{" "}
                  {pValue(c.p_holm)}) · n pairs = {c.n_pairs ?? "—"} ·
                  discordant {c.a_only ?? "—"}/{c.b_only ?? "—"}
                </span>
                <span
                  className={
                    "research-chip" + (c.established ? " established" : "")
                  }
                >
                  {c.established ? "established" : "not established"}
                </span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="muted">
          Need at least two agents with paired baseline runs.
        </p>
      )}
      {significance?.method && <small>{significance.method}</small>}
    </div>
  );
}

// Dose-response per base fault: success rate against fault probability.
function DoseResponse({
  dose,
  catalog,
  name,
}: {
  dose: Row;
  catalog: Row;
  name: (id: string) => string;
}) {
  const bases = Array.from(
    new Set(Object.values(dose).flatMap((byBase: any) => Object.keys(byBase || {}))),
  );
  if (!bases.length) return null;
  return (
    <div className="research-block">
      <h3>Breaking point</h3>
      <div className="dose-grid">
        {bases.map((base) => {
          const agentIds = Object.keys(dose).filter((id) => dose[id]?.[base]);
          const ps = Array.from(
            new Set(
              agentIds.flatMap((id) =>
                dose[id][base].points.map((pt: Row) => pt.p as number),
              ),
            ),
          ).sort((a, b) => a - b);
          const data = ps.map((p) => {
            const row: Row = { p };
            agentIds.forEach((id, i) => {
              const pt = dose[id][base].points.find((q: Row) => q.p === p);
              if (pt) {
                row[`r${i}`] = pt.rate;
                row[`b${i}`] = [pt.lo, pt.hi];
              }
            });
            return row;
          });
          const colour = (id: string) =>
            agentColors[
              Math.max(
                0,
                catalog.agents.findIndex((a: Row) => a.id === id),
              ) % agentColors.length
            ];
          return (
            <div className="dose-card" key={base}>
              <h4>{base}</h4>
              <ResponsiveContainer width="100%" height={220}>
                <ComposedChart
                  data={data}
                  margin={{ top: 15, right: 20, left: 0, bottom: 10 }}
                >
                  <CartesianGrid stroke="#D9D0BD" strokeDasharray="3 5" />
                  <XAxis
                    type="number"
                    dataKey="p"
                    domain={[0, 1]}
                    ticks={ps}
                    name="Fault probability"
                    tickFormatter={(x) => Number(x).toFixed(2)}
                    stroke="#5B5668"
                  />
                  <YAxis
                    type="number"
                    domain={[0, 1]}
                    ticks={[0, 0.25, 0.5, 0.75, 1]}
                    tickFormatter={(x) => `${Math.round(100 * x)}%`}
                    stroke="#5B5668"
                  />
                  <Tooltip
                    formatter={(v: any) =>
                      Array.isArray(v)
                        ? `${rate(v[0])} – ${rate(v[1])}`
                        : rate(v)
                    }
                    labelFormatter={(p: any) => `p = ${Number(p).toFixed(2)}`}
                  />
                  <ReferenceLine
                    y={0.5}
                    stroke="#5B5668"
                    strokeDasharray="4 4"
                    label={{ value: "50%", fill: "#5B5668", fontSize: 10 }}
                  />
                  {agentIds.map((id, i) => (
                    <Area
                      key={`band-${id}`}
                      dataKey={`b${i}`}
                      type="linear"
                      stroke="none"
                      fill={colour(id)}
                      fillOpacity={0.14}
                      connectNulls
                      isAnimationActive={false}
                    />
                  ))}
                  {agentIds.map((id, i) => (
                    <Line
                      key={`line-${id}`}
                      dataKey={`r${i}`}
                      name={name(id)}
                      type="linear"
                      stroke={colour(id)}
                      strokeWidth={2}
                      dot={{ r: 3 }}
                      connectNulls
                      isAnimationActive={false}
                    />
                  ))}
                </ComposedChart>
              </ResponsiveContainer>
              <ul className="breakpoints">
                {agentIds.map((id) => {
                  const bp = dose[id][base].breaking_point;
                  return (
                    <li key={id}>
                      {name(id)}:{" "}
                      {bp == null
                        ? "holds above 50% at every tested level"
                        : `breaking point p = ${Number(bp).toFixed(2)}`}
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function JudgeBlock({ id, judge }: { id: string; judge: Row }) {
  const criteria: Row[] = judge.criteria ?? [];
  return (
    <div className="judge-block">
      <div className="judge-head">
        <h4>Rubric judge · {id}</h4>
        <span
          className={
            "research-chip" + (judge.passed === true ? " established" : "")
          }
        >
          {judge.passed === true
            ? "Passed"
            : judge.passed === false
              ? "Not passed"
              : "Unverified"}
        </span>
      </div>
      <div className="judge-meta">
        <span>
          Method <b>{judgeMethod(judge.method)}</b>
        </span>
        <span>
          Model <b>{judge.model || "—"}</b>
        </span>
        <span>
          Score <b>{dashNum(judge.score)}</b>
        </span>
      </div>
      {judge.judge_error && (
        <p className="orange" role="alert">
          Judge error: {judge.judge_error}
        </p>
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
              {criteria.map((c: Row, i: number) => (
                <tr key={c.id ?? i}>
                  <td>{c.id ?? "—"}</td>
                  <td className={c.met ? "green" : "orange"}>
                    {c.met ? "✓" : "✗"}
                  </td>
                  <td>{dashNum(c.weight)}</td>
                  <td>{c.reason || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <small className="muted">No criteria recorded.</small>
      )}
    </div>
  );
}

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
    [error, setError] = useState(""),
    [significance, setSignificance] = useState<Row | null>(null),
    [dose, setDose] = useState<Row>({});
  const name = (id: string) =>
    catalog.agents.find((a: Row) => a.id === id)?.display_name || id;
  useEffect(() => {
    setRows(experiment.leaderboard);
    setSignificance(null);
    setDose({});
    setError("");
  }, [experiment.id]);
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
        if (!disposed) {
          setRows(data.rows);
          setSignificance(data.significance ?? null);
          setDose(data.dose_response ?? {});
          setError("");
        }
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
        .sort((a, b) => {
          const spec = SORTS[sort] ?? SORTS.pass_3;
          const x = spec.get(a);
          const y = spec.get(b);
          if (x == null || y == null)
            return x == null && y == null ? 0 : x == null ? 1 : -1;
          return spec.asc ? x - y : y - x;
        }),
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
              <option value="3">3 · synthesis (rubric)</option>
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
              {Object.entries(SORTS).map(([key, s]) => (
                <option key={key} value={key}>
                  {s.label}
                </option>
              ))}
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
              <th>Score</th>
              <th>S · clean</th>
              <th>pass^1</th>
              <th>R · pass^3</th>
              <th>pass^5</th>
              <th>F · acceptable</th>
              <th>Recovered</th>
              <th>Abstains</th>
              <th>E · efficiency</th>
              <th>Steps</th>
              <th>Tool calls</th>
              <th>Tokens</th>
              <th>Cost / success</th>
              <th>Time</th>
              <th>Observed / planned</th>
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
                <td>{dashNum(r.viewer_score)}</td>
                <td>
                  {dashPct(r.clean_success)}
                  <small className="muted">
                    {" "}
                    n={r.clean_n ?? "—"}
                    {ciText(r.clean_interval)}
                  </small>
                </td>
                <td>{dashPct(r.pass_1)}</td>
                <td className="violet-text">{dashPct(r.pass_3)}</td>
                <td>{dashPct(r.pass_5)}</td>
                <td>{dashPct(r.fault_tolerance)}</td>
                <td>{dashPct(r.recovery_rate)}</td>
                <td
                  title={`Correct refusals on unanswerable tasks; over-answer rate ${dashPct(r.over_answer_rate)}`}
                >
                  {r.abstention_n ? (
                    <>
                      {r.abstention_correct ?? 0}/{r.abstention_n}
                      <small className="muted">
                        {" "}
                        {dashPct(r.abstention_rate)}
                      </small>
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td>{dashPct(r.score_components?.E)}</td>
                <td>{dashNum(r.median_steps)}</td>
                <td>{dashNum(r.median_tool_calls)}</td>
                <td>{dashNum(r.median_tokens)}</td>
                <td>{dashMoney(r.cost_per_success)}</td>
                <td>{dashSecs(r.median_latency_ms)}</td>
                <td>
                  {r.runs} / {r.planned_runs}
                </td>
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
      <Significance significance={significance} name={name} />
      <DoseResponse dose={dose} catalog={catalog} name={name} />
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
                          .map(behaviorText)
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
      {Object.entries(run.evaluation?.grades || {})
        .filter(([, grade]: [string, any]) => grade?.judge)
        .map(([key, grade]: [string, any]) => (
          <JudgeBlock key={key} id={key} judge={grade.judge} />
        ))}
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
    let received = false;
    const stream = new EventSource(`/api/runs/${runId}/events`);
    stream.onmessage = (e) => {
      let next: Row;
      try {
        next = JSON.parse(e.data);
      } catch {
        return;
      }
      if (!next || typeof next !== "object") return;
      received = true;
      setEvents((rows) =>
        rows.some((row) => row.id === next.id) ? rows : [...rows, next],
      );
      if (next.event_type === "RUN_FINISHED") stream.close();
    };
    stream.onerror = () => {
      stream.close();
      if (!received) setError(true);
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
