import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowRight,
  Beaker,
  Check,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Download,
  GitCompareArrows,
  Layers,
  LayoutDashboard,
  Play,
  Radio,
  ShieldAlert,
  Square,
  Terminal,
  TriangleAlert,
  Zap,
} from "lucide-react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import "./style.css";
import {
  ChallengerCards,
  AgentIdentity,
  BehaviorBadge,
  TraceTiles,
  agentColors,
} from "./ArenaPanels";
import { AgentAvatar, PixelIcon } from "./ArenaAssets";
import {
  CompareWithSelect,
  EventDetail,
  FaultCellLabel,
  Postmortem,
  TraceTotalsStats,
  runLabel,
} from "./InspectorPanels";
import {
  ReliabilityBoard,
  RepeatComparison,
  GradingPanel,
  LiveTrace,
} from "./ArchitecturePanels";
type Obj = Record<string, any>;
async function api(path: string, body?: Obj) {
  const r = await fetch(
    "/api" + path,
    body
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : undefined,
  );
  if (!r.ok) {
    const text = await r.text();
    let message = `HTTP ${r.status}: ${text.slice(0, 200)}`;
    try {
      const e = JSON.parse(text);
      if (e?.detail != null) {
        message =
          typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail);
      }
    } catch {
      // Non-JSON error body: keep the status-based message.
    }
    throw new Error(message);
  }
  return r.json();
}
const pct = (v: number | null | undefined) =>
  v == null ? "—" : `${Math.round(v * 100)}%`;
const num = (v: number | null | undefined) =>
  v == null
    ? "Unknown"
    : v.toLocaleString(undefined, { maximumFractionDigits: 1 });
const nav = [
  ["Overview", LayoutDashboard],
  ["Benchmark lab", Beaker],
  ["Experiments", Radio],
  ["Leaderboard", Layers],
  ["Compare agents", GitCompareArrows],
  ["Fault matrix", ShieldAlert],
  ["Trace explorer", Terminal],
  ["Counterfactual", GitCompareArrows],
  ["Run report", Download],
] as const;
const Event = ({ e }: { e: Obj }) => (
  <details
    id={"event-" + e.id}
    data-seq={(e.sequence_number ?? e.seq) ?? undefined}
    tabIndex={-1}
    className={
      "event " +
      (e.fault ? "injected " : "") +
      "service-" +
      (e.service || "lifecycle")
    }
  >
    <summary>
      <span className="event-icon">
        <PixelIcon
          name={
            e.fault
              ? "trap"
              : e.event_type === "BUDGET_EXCEEDED"
                ? "limit"
                : e.service === "model"
                  ? "model"
                  : ["search", "extract"].includes(e.service)
                    ? "tool"
                    : e.event_type === "AGENT_OUTPUT"
                      ? "answer"
                      : "start"
          }
          size={18}
        />
      </span>
      <span>
        <b>
          {e.event_type === "CALL"
            ? `${e.service === "model" ? "Model completion" : e.service === "search" ? "Search evidence" : "Extract evidence"}`
            : e.event_type.replaceAll("_", " ")}
        </b>
        <small>
          {e.fault
            ? e.fault.intervention
            : e.request_summary?.query ||
              e.description ||
              e.service ||
              (e.event_type === "AGENT_SPAN"
                ? `${e.name || "span"} · agent telemetry (OTLP)`
                : e.event_type === "JUDGE_RESULT"
                  ? `Rubric grader · ${e.judgement?.method || "unknown method"}`
                  : "Harness lifecycle")}
        </small>
      </span>
      <span className="event-meta">
        {e.status_code && (
          <em className={e.status_code >= 400 ? "orange" : "green"}>
            HTTP {e.status_code}
          </em>
        )}
        {e.duration_ms != null && <small>{num(e.duration_ms)} ms</small>}
      </span>
      <ChevronDown size={14} />
    </summary>
    <div className="event-detail">
      <p>
        {e.upstream_called === false
          ? "Upstream not called"
          : e.upstream_called
            ? "Upstream called"
            : ""}
      </p>
      <EventDetail e={e} />
    </div>
  </details>
);

function App() {
  const [page, setPage] = useState("Leaderboard"),
    [catalog, setCatalog] = useState<Obj | null>(null),
    [experiments, setExperiments] = useState<Obj[]>([]),
    [selected, setSelected] = useState(""),
    [exp, setExp] = useState<Obj | null>(null),
    [trace, setTrace] = useState<Obj | null>(null),
    [pair, setPair] = useState<Obj | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [integrations, setIntegrations] = useState<Obj | null>(null);
  const [agents, setAgents] = useState(["react", "sdk"]),
    [tasks, setTasks] = useState(["research_001"]),
    [scenarios, setScenarios] = useState(["baseline", "search_failure"]),
    [reps, setReps] = useState(3),
    [mode, setMode] = useState("demo"),
    [snapshots, setSnapshots] = useState<Obj[]>([]),
    [snapshot, setSnapshot] = useState(""),
    [scenarioFilter, setScenarioFilter] = useState("all"),
    [modelPolicy, setModelPolicy] = useState("pinned"),
    [model, setModel] = useState(""),
    [concurrency, setConcurrency] = useState(3),
    [costCap, setCostCap] = useState("");
  const [replaying, setReplaying] = useState(false),
    [replayCheck, setReplayCheck] = useState<Obj | null>(null),
    [diffLabels, setDiffLabels] = useState<{
      left: string;
      right: string;
    } | null>(null),
    [dose, setDose] = useState(""),
    [doseLevels, setDoseLevels] = useState("0.25, 0.5, 0.75, 1");
  const refresh = async () => {
    try {
      const list = await api("/experiments");
      setExperiments(list);
      setSelected(
        (current) =>
          current ||
          list.find((item: Obj) => item.status === "completed")?.id ||
          "",
      );
    } catch (e) {
      setError(String(e));
    }
  };
  useEffect(() => {
    api("/catalog")
      .then(setCatalog)
      .catch((e) => setError(String(e)));
    api("/integrations")
      .then(setIntegrations)
      .catch((e) => setError(String(e)));
    api("/snapshots")
      .then(setSnapshots)
      .catch((e) => setError(String(e)));
    refresh();
  }, []);
  useEffect(() => {
    if (!selected) {
      setExp(null);
      return;
    }
    setTrace(null);
    setPair(null);
    let stopped = false;
    let timer: ReturnType<typeof setInterval> | undefined;
    const load = async () => {
      try {
        const data = await api("/experiments/" + selected);
        if (!stopped) {
          setExp(data);
          setExperiments((previous) =>
            previous.map((item) =>
              item.id === data.id
                ? {
                    ...item,
                    status: data.status,
                    completed_runs: data.completed_runs,
                  }
                : item,
            ),
          );
          if (["completed", "cancelled", "failed"].includes(data.status)) {
            clearInterval(timer);
          }
        }
      } catch (e) {
        if (!stopped) setError(String(e));
      }
    };
    load();
    timer = setInterval(load, 1200);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [selected]);
  const runs: Obj[] = exp?.runs || [];
  const terminal = runs.filter(
    (r) =>
      !["pending", "running", "cancelled"].includes(r.status) &&
      r.metrics &&
      r.evaluation,
  );
  const success = terminal.filter((r) => r.evaluation?.outcome === "success");
  const toggle = (list: string[], id: string, set: (x: string[]) => void) =>
    set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  const doseList = [
    ...new Set(
      doseLevels
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)
        .map(Number),
    ),
  ];
  const doseValid =
    !dose ||
    (doseList.length >= 1 &&
      doseList.length <= 6 &&
      doseList.every((v) => Number.isFinite(v) && v > 0 && v <= 1));
  const doseCount = dose && doseValid ? doseList.length : 0;
  // Dose levels add extra conditions next to the chosen scenarios.
  const plannedRuns =
    agents.length * tasks.length * (scenarios.length + doseCount) * reps;
  const start = async () => {
    setBusy(true);
    setError("");
    try {
      const x = await api("/experiments", {
        name: "Research resilience / " + new Date().toLocaleDateString(),
        agent_ids: agents,
        task_ids: tasks,
        scenario_ids: scenarios,
        repetitions: reps,
        model_policy: modelPolicy,
        model: model || null,
        concurrency,
        max_cost_usd: costCap ? Number(costCap) : null,
        evidence_mode: mode,
        ...(mode === "live"
          ? {}
          : { snapshot_id: mode === "frozen" ? snapshot : "demo-v1" }),
        ...(dose && doseValid
          ? { dose: { scenario_id: dose, levels: doseList } }
          : {}),
      });
      setSelected(x.id);
      setPage("Experiments");
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const openTrace = async (r: Obj) => {
    try {
      setTrace(await api("/runs/" + r.id));
      setPage("Trace explorer");
    } catch (e) {
      setError(String(e));
    }
  };
  const openPair = async (r: Obj) => {
    try {
      setPair(await api("/runs/" + r.id + "/comparison"));
      setDiffLabels(null);
      setPage("Counterfactual");
    } catch (e) {
      setError(String(e));
    }
  };
  const openDiff = async (rightId: string) => {
    if (!trace?.id || !rightId) return;
    try {
      const data = await api(
        `/compare?left=${encodeURIComponent(trace.id)}&right=${encodeURIComponent(rightId)}`,
      );
      setPair(data);
      setDiffLabels({
        left: runLabel(data.baseline),
        right: runLabel(data.fault),
      });
      setPage("Counterfactual");
    } catch (e) {
      setError(String(e));
    }
  };
  const replayRun = async () => {
    if (!trace?.id || replaying) return;
    setReplaying(true);
    setError("");
    setReplayCheck(null);
    try {
      let replayExp: Obj = await api("/runs/" + trace.id + "/replay", {});
      while (!["completed", "cancelled", "failed"].includes(replayExp.status)) {
        await new Promise((resolve) => setTimeout(resolve, 800));
        replayExp = await api("/experiments/" + replayExp.id);
      }
      const replayId: string | undefined = replayExp.run_ids?.[0];
      if (replayExp.status !== "completed" || !replayId) {
        throw new Error(`Replay ${replayExp.status}: no completed run`);
      }
      const replayed: Obj = await api("/runs/" + replayId);
      const before = {
        outcome: trace.evaluation?.outcome ?? trace.status,
        behavior: trace.evaluation?.behavior ?? "unknown",
      };
      const after = {
        outcome: replayed.evaluation?.outcome ?? replayed.status,
        behavior: replayed.evaluation?.behavior ?? "unknown",
      };
      const same =
        before.outcome === after.outcome && before.behavior === after.behavior;
      setReplayCheck({
        replayId,
        reproduced: same,
        text: same
          ? "Reproduced: same outcome and behaviour"
          : `Not reproduced: outcome ${before.outcome}→${after.outcome}, behaviour ${before.behavior}→${after.behavior}`,
      });
      await openDiff(replayId);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setReplaying(false);
    }
  };
  const loading = !catalog;
  const name = (id: string) =>
    catalog?.agents.find((a: Obj) => a.id === id)?.display_name || id;
  const runTable = (list: Obj[]) => (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Agent / task</th>
            <th>Scenario</th>
            <th>Outcome</th>
            <th>Calls M / T</th>
            <th>Duration</th>
            <th>Inspect</th>
          </tr>
        </thead>
        <tbody>
          {list.map((r) => (
            <tr key={r.id}>
              <td>
                <AgentIdentity
                  id={r.agent_id}
                  name={name(r.agent_id)}
                  index={
                    catalog?.agents.findIndex(
                      (a: Obj) => a.id === r.agent_id,
                    ) ?? 0
                  }
                />
                <small>
                  {r.task_id} · repeat {r.repetition + 1}
                </small>
              </td>
              <td>{r.scenario_id.replaceAll("_", " ")}</td>
              <td>
                <span
                  className={
                    "badge outcome-" + (r.evaluation?.outcome || r.status)
                  }
                >
                  {r.evaluation?.outcome || r.status}
                </span>
                {r.scenario_id !== "baseline" && r.evaluation && (
                  <small>
                    <BehaviorBadge behavior={r.evaluation.behavior} />
                  </small>
                )}
              </td>
              <td>
                {r.metrics
                  ? `${r.metrics.model_calls} / ${r.metrics.tool_calls}`
                  : "—"}
              </td>
              <td>{r.metrics ? num(r.metrics.duration_ms) + " ms" : "—"}</td>
              <td>
                <button
                  className="iconbutton"
                  aria-label="Open trace"
                  onClick={() => openTrace(r)}
                >
                  <Terminal size={16} />
                </button>
                {r.scenario_id !== "baseline" && (
                  <button
                    className="iconbutton"
                    aria-label="Compare with baseline"
                    onClick={() => openPair(r)}
                  >
                    <GitCompareArrows size={16} />
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
  const empty = (text: string) => (
    <div className="empty">
      <Beaker size={30} />
      <h3>{text}</h3>
      <p>Run a controlled experiment to collect execution evidence.</p>
      <button className="primary" onClick={() => setPage("Benchmark lab")}>
        Configure a benchmark <ArrowRight size={15} />
      </button>
    </div>
  );
  return (
    <div className="shell">
      <aside>
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setPage("Leaderboard");
          }}
        >
          <span className="brand-icon">G</span>GAUNTLET
        </a>
        <nav aria-label="Main navigation">
          {(
            [
              ["Leaderboard", "Leaderboard"],
              ["Compare agents", "Versus"],
              ["Trace explorer", "Replay"],
              ["Benchmark lab", "New run"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              className={page === id ? "active" : ""}
              aria-label={label === id ? id : `${label}: ${id}`}
              onClick={() => setPage(id)}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="workspace">
          <span className="dot" /> Research arena <small>v0.2</small>
        </div>
        <button
          className="primary top-run"
          aria-label="Start run: new benchmark"
          onClick={() => setPage("Benchmark lab")}
        >
          <PixelIcon name="start" size={18} />
          Start run
        </button>
        <div className="aside-bottom">
          <div>
            <span className="dot" /> Harness operational
          </div>
          <small>v0.1 · Research agents</small>
          <p>
            Don't just benchmark agents.
            <br />
            Discover exactly how they fail.
          </p>
        </div>
      </aside>
      <main>
        <header>
          <div className="secondary-nav" aria-label="More views">
            {nav
              .filter(
                ([label]) =>
                  ![
                    "Leaderboard",
                    "Compare agents",
                    "Trace explorer",
                    "Benchmark lab",
                  ].includes(label),
              )
              .map(([label, Icon]) => (
                <button
                  key={label}
                  className={page === label ? "active" : ""}
                  onClick={() => setPage(label)}
                >
                  <Icon size={13} />
                  {label}
                </button>
              ))}
          </div>
          <span className="breadcrumb">
            Workspace <ChevronRight size={13} /> <b>{page}</b>
          </span>
          <div>
            <span className="badge">
              {(exp?.config.evidence_mode || mode) === "demo"
                ? "OFFLINE DEMO"
                : "LIVE MODEL"}
            </span>
            <span className="arena-label">DAY ARENA</span>
          </div>
        </header>
        <div className="content">
          {error && (
            <div className="error" role="alert">
              <TriangleAlert size={17} />
              {error}
              <button onClick={() => setError("")}>Dismiss</button>
            </div>
          )}
          {loading ? (
            <div className="empty">Connecting to the local harness…</div>
          ) : (
            <>
              <div className="page-heading">
                <div>
                  <p className="eyebrow">
                    <span className="dot purple" /> RESEARCH AGENTS / THE
                    CRASH-TEST ARENA
                  </p>
                  <h1>
                    {page === "Overview" ? (
                      <>
                        Break your agents.
                        <br />
                        <span>Before production does.</span>
                      </>
                    ) : page === "Counterfactual" ? (
                      <>
                        One intervention.
                        <br />
                        <span>Every observable difference.</span>
                      </>
                    ) : (
                      (
                        {
                          Leaderboard: "Hall of Champions",
                          "Compare agents": "Challenger versus challenger",
                          "Trace explorer": "Replay the run",
                          "Benchmark lab": "Enter the arena",
                          Experiments: "Runs in progress",
                          "Fault matrix": "The fault arena",
                          "Run report": "The run record",
                        } as Obj
                      )[page] || page
                    )}
                  </h1>
                  <p>
                    {page === "Overview"
                      ? "Controlled failures. Observable traces. Evidence-backed reliability."
                      : page === "Benchmark lab"
                        ? "Define the conditions. Keep the comparison fair."
                        : "Inspect measured execution, from the first request to the final answer."}
                  </p>
                </div>
                {page === "Overview" && (
                  <button
                    className="primary"
                    onClick={() => setPage("Benchmark lab")}
                  >
                    <Play size={16} /> Run benchmark
                  </button>
                )}
              </div>
              <div className="notice">
                <ShieldAlert size={16} />
                <span>
                  {(exp?.config.evidence_mode || mode) === "demo"
                    ? "Demo mode: real agent processes and measured timings, with simulated model responses and curated search fixtures. No provider tokens or costs are claimed."
                    : "Live model responses can vary. Frozen evidence misses never fall back to live search."}
                </span>
              </div>
              {page !== "Benchmark lab" && (
                <div className="experiment-picker">
                  <label htmlFor="experiment">Experiment</label>
                  <select
                    id="experiment"
                    value={selected}
                    onChange={(e) => {
                      setSelected(e.target.value);
                      setTrace(null);
                      setPair(null);
                    }}
                  >
                    <option value="">Select an experiment</option>
                    {experiments.map((e) => (
                      <option key={e.id} value={e.id}>
                        {e.name} · {e.id.slice(0, 6)}
                      </option>
                    ))}
                  </select>
                  {exp && <span className="badge">{exp.status}</span>}
                </div>
              )}
              {page === "Overview" && (
                <>
                  <div className="stats">
                    <Stat
                      label="Clean pass^3"
                      value={
                        exp?.leaderboard.length &&
                        exp.leaderboard.every((r: Obj) => r.pass_3 != null)
                          ? pct(
                              exp.leaderboard.reduce(
                                (n: number, r: Obj) => n + r.pass_3,
                                0,
                              ) / exp.leaderboard.length,
                            )
                          : "Not established"
                      }
                      sub={`${success.length} factual successes / ${terminal.length} observed · n≥3 for pass^3`}
                      icon={<Check />}
                    />
                    <Stat
                      label="Recorded runs"
                      value={String(terminal.length)}
                      sub={
                        exp
                          ? `${exp.total_runs} planned in experiment`
                          : "No experiment selected"
                      }
                      icon={<Layers />}
                    />
                    <Stat
                      label="Injected interventions"
                      value={String(
                        terminal.reduce(
                          (n, r) => n + r.metrics.injected_faults,
                          0,
                        ),
                      )}
                      sub="Applied by the recording proxy"
                      icon={<Zap />}
                    />
                    <Stat
                      label="Provider tokens"
                      value={
                        terminal.length &&
                        terminal.every((r) => r.metrics.tokens != null)
                          ? num(
                              terminal.reduce(
                                (n, r) => n + r.metrics.tokens,
                                0,
                              ),
                            )
                          : "Unknown"
                      }
                      sub="Never estimated from response length"
                      icon={<Activity />}
                    />
                  </div>
                  <div className="grid-two">
                    <section className="panel">
                      <div className="panel-title">
                        <h2>Resilience under pressure</h2>
                        <span className="badge">PAIRED EXECUTION</span>
                      </div>
                      <p>Same task. Same evidence. One controlled fault.</p>
                      <div className="mini-flow">
                        <span>
                          <CircleDot /> Baseline
                        </span>
                        <i />
                        <span className="fault-node">
                          <Zap /> Intervention
                        </span>
                        <i />
                        <span>
                          <GitCompareArrows /> Compare
                        </span>
                      </div>
                      {exp?.leaderboard.length ? (
                        <Chart rows={exp.leaderboard} />
                      ) : (
                        <div className="chart-empty">
                          Your measured comparison appears here after a run.
                        </div>
                      )}
                      <button
                        className="textbutton"
                        onClick={() => setPage("Counterfactual")}
                      >
                        Explore counterfactual traces <ArrowRight size={15} />
                      </button>
                    </section>
                    <section className="panel">
                      <div className="panel-title">
                        <h2>Integration health</h2>
                        <button
                          className="textbutton"
                          onClick={() =>
                            api("/integrations")
                              .then(setIntegrations)
                              .catch((e) => setError(String(e)))
                          }
                        >
                          Refresh
                        </button>
                      </div>
                      <Integration
                        title="Bifrost"
                        subtitle={catalog.model}
                        state={
                          integrations?.bifrost.connected
                            ? "Connected"
                            : "Disconnected"
                        }
                        ok={!!integrations?.bifrost.connected}
                      />
                      <Integration
                        title="Tavily"
                        subtitle="Research evidence · Search + Extract"
                        state={
                          integrations?.tavily.configured
                            ? "Key configured"
                            : "Not configured"
                        }
                        ok={!!integrations?.tavily.configured}
                      />
                      <Integration
                        title="Evidence snapshot"
                        subtitle={`${Object.keys(catalog.snapshot?.entries || {}).length} registered query variants · curated fixtures`}
                        state={catalog.snapshot.snapshot_id.slice(0, 13)}
                        ok
                      />
                      <p className="muted">
                        Provider credentials stay in the backend. Configure .env
                        for live experiments.
                      </p>
                    </section>
                  </div>
                  <section className="panel">
                    <div className="panel-title">
                      <h2>Recent experiments</h2>
                      <span className="muted">Persisted in SQLite</span>
                    </div>
                    {experiments.length ? (
                      <div className="recent">
                        {experiments.map((e) => (
                          <button
                            key={e.id}
                            onClick={() => {
                              setSelected(e.id);
                              setPage("Experiments");
                            }}
                          >
                            <span className="recent-icon">
                              <Beaker size={20} />
                            </span>
                            <span>
                              <b>{e.name}</b>
                              <small>
                                {new Date(e.created_at).toLocaleString()} ·{" "}
                                {e.config.evidence_mode}
                              </small>
                            </span>
                            <span className="badge">{e.status}</span>
                            <span>
                              {e.completed_runs}/{e.total_runs} runs
                            </span>
                            <ArrowRight size={16} />
                          </button>
                        ))}
                      </div>
                    ) : (
                      empty("Your first fault line starts here")
                    )}
                  </section>
                </>
              )}
              {page === "Benchmark lab" && (
                <div className="lab-grid">
                  <div>
                    <section className="panel">
                      <div className="section-number">
                        01 <h2>Select research agents</h2>
                      </div>
                      <div className="agent-options">
                        {catalog.agents.map((a: Obj) => (
                          <label
                            key={a.id}
                            className={
                              "agent-option " +
                              (agents.includes(a.id) ? "chosen" : "")
                            }
                          >
                            <input
                              type="checkbox"
                              checked={agents.includes(a.id)}
                              onChange={() => toggle(agents, a.id, setAgents)}
                            />
                            <AgentAvatar
                              id={a.id}
                              size={54}
                              color={agentColors[catalog.agents.indexOf(a) % 6]}
                            />
                            <b>{a.display_name}</b>
                            <small>{a.framework}</small>
                            <span>{a.retry_policy}</span>
                          </label>
                        ))}
                      </div>
                    </section>
                    <section className="panel">
                      <div className="section-number">
                        02 <h2>Choose the task suite</h2>
                      </div>
                      <p>
                        Research fundamentals · {catalog.tasks.length} tasks ·
                        independent factual ground truth
                      </p>
                      {catalog.tasks.map((t: Obj) => (
                        <label key={t.id} className="choice-row">
                          <input
                            type="checkbox"
                            checked={tasks.includes(t.id)}
                            onChange={() => toggle(tasks, t.id, setTasks)}
                          />
                          <span>
                            <b>{t.title}</b>
                            <small>
                              {t.category.replaceAll("_", " ")} · {t.difficulty}{" "}
                              · accepted fact evaluation
                            </small>
                            <small>{t.question}</small>
                          </span>
                        </label>
                      ))}
                    </section>
                    <section className="panel">
                      <div className="section-number">
                        03 <h2>Introduce controlled failure</h2>
                      </div>
                      {catalog.scenarios.map((s: Obj) => (
                        <label key={s.id} className="choice-row">
                          <input
                            type="checkbox"
                            checked={scenarios.includes(s.id)}
                            onChange={() =>
                              toggle(scenarios, s.id, setScenarios)
                            }
                          />
                          <span>
                            <b>{s.display_name}</b>
                            <small>{s.description}</small>
                          </span>
                          {s.id !== "baseline" && (
                            <Zap size={15} className="orange" />
                          )}
                        </label>
                      ))}
                    </section>
                  </div>
                  <div>
                    <section className="panel sticky">
                      <p className="eyebrow">EXPERIMENT PLAN</p>
                      <h2>Controlled comparison</h2>
                      <label className="field">
                        Evidence mode
                        <select
                          value={mode}
                          onChange={(e) => setMode(e.target.value)}
                        >
                          <option value="demo">
                            Offline demo · curated evidence
                          </option>
                          <option value="live">Live Tavily + Bifrost</option>
                          <option value="frozen">
                            Captured evidence + live Bifrost
                          </option>
                        </select>
                      </label>
                      {mode === "frozen" && (
                        <label className="field">
                          Snapshot
                          <select
                            value={snapshot}
                            onChange={(e) => setSnapshot(e.target.value)}
                          >
                            <option value="">Select a captured snapshot</option>
                            {snapshots
                              .filter((s) => s.capture_timestamp)
                              .map((s) => (
                                <option
                                  key={s.snapshot_id}
                                  value={s.snapshot_id}
                                >
                                  {s.snapshot_id.slice(0, 10)} ·{" "}
                                  {s.capture_timestamp}
                                </option>
                              ))}
                          </select>
                        </label>
                      )}
                      <label className="field">
                        Model policy
                        <select
                          value={modelPolicy}
                          onChange={(e) => setModelPolicy(e.target.value)}
                        >
                          <option value="pinned">
                            Pinned model · identical gateway model
                          </option>
                          <option value="own">
                            Agent-selected model · disclose differences
                          </option>
                        </select>
                      </label>
                      <label className="field">
                        Gateway model
                        <input
                          placeholder={catalog.model}
                          value={model}
                          onChange={(e) => setModel(e.target.value)}
                        />
                      </label>
                      <label className="field">
                        Concurrent runs
                        <input
                          type="number"
                          min={1}
                          max={catalog.limits.max_concurrent_runs}
                          value={concurrency}
                          onChange={(e) =>
                            setConcurrency(Number(e.target.value))
                          }
                        />
                      </label>
                      <label className="field">
                        Observed cost cap per run (USD)
                        <input
                          type="number"
                          min="0.01"
                          step="0.01"
                          value={costCap}
                          onChange={(e) => setCostCap(e.target.value)}
                          placeholder="Optional"
                        />
                        <small>
                          Checked between calls using available costs. A gateway
                          virtual-key budget is the hard backstop.
                        </small>
                      </label>
                      <label className="field">
                        Repetitions per condition
                        <input
                          type="number"
                          min={1}
                          max={10}
                          value={reps}
                          onChange={(e) => setReps(Number(e.target.value))}
                        />
                      </label>
                      <label className="field">
                        Dose-response sweep
                        <select
                          aria-label="Dose-response fault"
                          value={dose}
                          onChange={(e) => setDose(e.target.value)}
                        >
                          <option value="">Off</option>
                          {catalog.scenarios
                            .filter(
                              (s: Obj) =>
                                s.id !== "baseline" && s.service !== "instruction",
                            )
                            .map((s: Obj) => (
                              <option key={s.id} value={s.id}>
                                {s.display_name}
                              </option>
                            ))}
                        </select>
                        {dose && (
                          <>
                            <input
                              aria-label="Dose levels"
                              value={doseLevels}
                              onChange={(e) => setDoseLevels(e.target.value)}
                              placeholder="0.25, 0.5, 0.75, 1"
                            />
                            <small
                              className={doseValid ? "" : "orange"}
                            >
                              {doseValid
                                ? `Fault fires with probability p per request at each level (${doseList.length} levels).`
                                : "Levels must be numbers in (0, 1], up to 6."}
                            </small>
                          </>
                        )}
                      </label>
                      <div className="plan-lines">
                        <span>
                          Agents <b>{agents.length}</b>
                        </span>
                        <span>
                          Tasks <b>{tasks.length}</b>
                        </span>
                        <span>
                          Scenarios <b>{scenarios.length}</b>
                        </span>
                        {doseCount > 0 && (
                          <span>
                            Dose levels <b>{doseCount}</b>
                          </span>
                        )}
                        <span>
                          Model{" "}
                          <b>
                            {mode === "demo"
                              ? "Evidence simulator"
                              : catalog.model}
                          </b>
                        </span>
                        <span>
                          Snapshot timestamp{" "}
                          <b>
                            {mode === "demo"
                              ? "Curated · no capture"
                              : "See captured snapshot"}
                          </b>
                        </span>
                      </div>
                      <div className="run-count">
                        <strong>{plannedRuns}</strong>
                        <span>planned executions</span>
                      </div>
                      <p className="muted">
                        Maximum {catalog.limits.max_experiment_runs} runs. One
                        experiment at a time. Live requests may incur provider
                        charges; cost is unknown without gateway reports or
                        explicit pricing.
                      </p>
                      {!scenarios.includes("baseline") && (
                        <p className="orange">
                          Include a baseline for paired comparisons.
                        </p>
                      )}
                      <button
                        className="primary full"
                        disabled={
                          busy ||
                          !agents.length ||
                          !tasks.length ||
                          !scenarios.includes("baseline") ||
                          reps < 1 ||
                          reps > 10 ||
                          !doseValid ||
                          plannedRuns > catalog.limits.max_experiment_runs ||
                          (mode === "frozen" && !snapshot)
                        }
                        onClick={start}
                      >
                        <Play size={16} />
                        {busy ? "Starting…" : "Run benchmark"}
                      </button>
                      <button
                        className="textbutton"
                        disabled={busy}
                        onClick={async () => {
                          setBusy(true);
                          try {
                            const s = await api("/snapshots/capture", {});
                            setSnapshots([...snapshots, s]);
                            setSnapshot(s.snapshot_id);
                          } catch (e) {
                            setError(String(e));
                          } finally {
                            setBusy(false);
                          }
                        }}
                      >
                        Capture live evidence snapshot
                      </button>
                    </section>
                  </div>
                </div>
              )}
              {page === "Experiments" &&
                (exp ? (
                  <>
                    <section className="panel">
                      <div className="panel-title">
                        <h2>{exp.name}</h2>
                        {["running", "pending"].includes(exp.status) && (
                          <button
                            className="secondary"
                            onClick={async () => {
                              try {
                                await api(
                                  "/experiments/" + exp.id + "/cancel",
                                  {},
                                );
                                await refresh();
                              } catch (e) {
                                setError(String(e));
                              }
                            }}
                          >
                            <Square size={13} /> Cancel experiment
                          </button>
                        )}
                      </div>
                      <div className="progress-label">
                        <span>
                          {exp.status} · {exp.completed_runs} / {exp.total_runs}{" "}
                          finished
                        </span>
                        <b>{pct(exp.completed_runs / exp.total_runs)}</b>
                      </div>
                      <div className="progress">
                        <i
                          style={{
                            width: `${(exp.completed_runs / exp.total_runs) * 100}%`,
                          }}
                        />
                      </div>
                      {runs.find((r) => r.status === "running") && (
                        <p className="cyan">
                          Running:{" "}
                          {name(
                            runs.find((r) => r.status === "running")!.agent_id,
                          )}{" "}
                          ·{" "}
                          {
                            runs.find((r) => r.status === "running")!
                              .scenario_id
                          }
                        </p>
                      )}
                    </section>
                    {runs.find((r) => r.status === "running") && (
                      <LiveTrace
                        runId={runs.find((r) => r.status === "running")!.id}
                      />
                    )}
                    <section className="panel">
                      <h2>Execution records</h2>
                      {runTable(runs)}
                    </section>
                  </>
                ) : (
                  empty("No experiment selected")
                ))}
              {page === "Leaderboard" && (
                <ChallengerCards
                  experiment={exp}
                  catalog={catalog}
                  onInspect={(id) => {
                    setScenarioFilter("all");
                    const first = runs.find(
                      (r) => r.agent_id === id && r.metrics,
                    );
                    if (first) openTrace(first);
                    else setPage("Benchmark lab");
                  }}
                />
              )}
              {page === "Leaderboard" &&
                (exp ? (
                  <ReliabilityBoard
                    experiment={exp}
                    catalog={catalog}
                    onInspect={openTrace}
                  />
                ) : (
                  empty("No measured rankings yet")
                ))}
              {page === "Compare agents" &&
                (exp ? (
                  <RepeatComparison
                    experiment={exp}
                    catalog={catalog}
                    onInspect={openTrace}
                  />
                ) : (
                  empty("No comparison yet")
                ))}
              {page === "Fault matrix" &&
                (exp ? (
                  <section className="panel">
                    <h2>Where resilience breaks</h2>
                    <p>Click a cell to inspect its actual runs.</p>
                    <div
                      className="matrix"
                      style={{
                        gridTemplateColumns: `180px repeat(${exp.config.scenario_ids.length}, minmax(110px,1fr))`,
                      }}
                    >
                      <div />
                      {exp.config.scenario_ids.map((s: string) => (
                        <b key={s}>{s.replaceAll("_", " ")}</b>
                      ))}
                      {exp.config.agent_ids.map((a: string) => (
                        <React.Fragment key={a}>
                          <b>{name(a)}</b>
                          {exp.config.scenario_ids.map((s: string) => {
                            const rs = terminal.filter(
                                (r) => r.agent_id === a && r.scenario_id === s,
                              ),
                              v = rs.length
                                ? rs.filter(
                                    (r) => r.evaluation?.outcome === "success",
                                  ).length / rs.length
                                : null,
                              leaderRow = (exp.leaderboard || []).find(
                                (x: Obj) => x?.agent_id === a,
                              ),
                              faultLabel: Obj | null =
                                leaderRow?.fault_labels?.[s] ?? null;
                            return (
                              <button
                                key={s}
                                className={
                                  "matrix-cell " +
                                  (v === 1 ? "good" : v === 0 ? "bad" : "")
                                }
                                onClick={() => {
                                  if (faultLabel?.example_run_id) {
                                    openTrace({ id: faultLabel.example_run_id });
                                    return;
                                  }
                                  setScenarioFilter(s);
                                  setPage("Trace explorer");
                                }}
                              >
                                <strong>{pct(v)}</strong>
                                <small>{rs.length} completed</small>
                                <FaultCellLabel label={faultLabel} />
                              </button>
                            );
                          })}
                        </React.Fragment>
                      ))}
                    </div>
                  </section>
                ) : (
                  empty("No fault matrix yet")
                ))}
              {page === "Trace explorer" &&
                (exp ? (
                  <section className="panel">
                    <div className="panel-title">
                      <h2>Observable execution</h2>
                      <select
                        value={scenarioFilter}
                        onChange={(e) => setScenarioFilter(e.target.value)}
                      >
                        <option value="all">All scenarios</option>
                        {exp.config.scenario_ids.map((s: string) => (
                          <option key={s}>{s}</option>
                        ))}
                      </select>
                    </div>
                    {runTable(
                      runs.filter(
                        (r) =>
                          scenarioFilter === "all" ||
                          r.scenario_id === scenarioFilter,
                      ),
                    )}
                    {trace && (
                      <>
                        <div className="trace-heading">
                          <h3>
                            {name(trace.agent_id)} / {trace.scenario_id}
                          </h3>
                          <span className="badge">
                            {trace.evaluation?.outcome || trace.status}
                          </span>
                          <CompareWithSelect
                            runs={runs}
                            trace={trace}
                            label={(r) =>
                              `${name(r.agent_id)} · ${r.scenario_id} · repeat ${(r.repetition ?? 0) + 1} · ${r.evaluation?.outcome || r.status}`
                            }
                            onChoose={openDiff}
                          />
                          <button
                            className="secondary"
                            disabled={replaying}
                            onClick={replayRun}
                          >
                            <Play size={14} />
                            {replaying ? "Replaying…" : "Replay run"}
                          </button>
                        </div>
                        {trace.postmortem && (
                          <Postmortem pm={trace.postmortem} />
                        )}
                        <TraceTotalsStats events={trace.events || []} />
                        <TraceTiles events={trace.events || []} />
                        <BehaviorBadge
                          behavior={trace.evaluation?.behavior || "unknown"}
                        />
                        <GradingPanel run={trace} />
                        {(trace.events || []).map((e: Obj) => (
                          <Event key={e.id} e={e} />
                        ))}
                        {trace.output?.answer && (
                          <div className="answer">
                            <p className="eyebrow">FINAL ANSWER</p>
                            {trace.output.answer}
                          </div>
                        )}
                      </>
                    )}
                  </section>
                ) : (
                  empty("No trace selected")
                ))}
              {page === "Counterfactual" &&
                (exp ? (
                  <>
                    <section className="panel">
                      <div className="panel-title">
                        <h2>
                          {diffLabels ? "Run comparison" : "Baseline ↔ intervention"}
                        </h2>
                        <select
                          aria-label="Select faulted run"
                          value={pair?.fault?.id || ""}
                          onChange={(e) => {
                            const r = runs.find((x) => x.id === e.target.value);
                            if (r) openPair(r);
                          }}
                        >
                          <option value="">Choose a faulted run</option>
                          {terminal
                            .filter((r) => r.scenario_id !== "baseline")
                            .map((r) => (
                              <option key={r.id} value={r.id}>
                                {name(r.agent_id)} · {r.task_id} ·{" "}
                                {r.scenario_id} · repeat {r.repetition + 1}
                              </option>
                            ))}
                        </select>
                      </div>
                      {pair ? (
                        <>
                          {replayCheck &&
                            pair.fault?.id === replayCheck.replayId && (
                              <p
                                role="status"
                                className={
                                  "replay-verdict " +
                                  (replayCheck.reproduced ? "good" : "warn")
                                }
                              >
                                {replayCheck.text}
                              </p>
                            )}
                          <p>{pair.limitation}</p>
                          <div className="stats compact">
                            <Stat
                              label={
                                diffLabels
                                  ? "Outcome: left → right"
                                  : "Baseline → fault outcome"
                              }
                              value={`${pair.baseline.evaluation?.outcome || pair.baseline.status} → ${pair.fault.evaluation?.outcome || pair.fault.status}`}
                              sub={
                                diffLabels
                                  ? "Measured difference between the two runs"
                                  : pair.recovered
                                    ? "Recovered after intervention"
                                    : "No recovery observed"
                              }
                            />
                            <Stat
                              label="Extra model / tool calls"
                              value={`${pair.deltas.model_calls ?? "—"} / ${pair.deltas.tool_calls ?? "—"}`}
                              sub="Measured request difference"
                            />
                            <Stat
                              label="Additional latency"
                              value={num(pair.deltas.duration_ms) + " ms"}
                              sub={`Extra tokens: ${num(pair.deltas.tokens)}`}
                            />
                            <Stat
                              label="Additional model cost"
                              value={
                                pair.deltas.estimated_cost == null
                                  ? "Unknown"
                                  : "$" + pair.deltas.estimated_cost.toFixed(6)
                              }
                              sub="Estimate only with configured pricing"
                            />
                          </div>
                          <div className="analysis">
                            <Zap size={19} />
                            <div>
                              <b>{pair.explanation}</b>
                              <p>
                                First observed divergence:{" "}
                                {pair.first_divergence_step != null
                                  ? `step ${pair.first_divergence_step}`
                                  : pair.first_divergence
                                    ? `aligned row ${pair.first_divergence}`
                                    : "none"}
                                . Evidence coverage:{" "}
                                {pair.comparable_evidence
                                  ? "comparable frozen responses"
                                  : "equivalence not established"}
                                .
                              </p>
                            </div>
                          </div>
                          <div className="comparison-head">
                            <span>
                              {diffLabels ? diffLabels.left : "BASELINE"}{" "}
                              {!diffLabels && (
                                <small>
                                  {String(pair.baseline?.id ?? "").slice(0, 8)}
                                </small>
                              )}
                            </span>
                            <span>
                              {diffLabels ? diffLabels.right : "FAULT INJECTED"}{" "}
                              {!diffLabels && (
                                <small>{pair.fault?.scenario_id}</small>
                              )}
                            </span>
                          </div>
                          {(pair.alignment || []).map((row: Obj, i: number) => (
                            <div
                              className={"comparison-row " + row.kind}
                              key={i}
                            >
                              <div>
                                {row.baseline ? (
                                  <Event e={row.baseline} />
                                ) : (
                                  <div className="missing">
                                    No corresponding call
                                  </div>
                                )}
                              </div>
                              <div>
                                {row.fault ? (
                                  <Event e={row.fault} />
                                ) : (
                                  <div className="missing">
                                    No corresponding call
                                  </div>
                                )}
                              </div>
                              <span className="diff-label">
                                {row.kind === "equal" ? "matched" : row.kind}
                                {row.baseline_step != null ||
                                row.fault_step != null
                                  ? ` · step ${row.baseline_step ?? "—"} / ${row.fault_step ?? "—"}`
                                  : ""}
                              </span>
                            </div>
                          ))}
                          <div className="comparison-row outputs">
                            <div>
                              <h3>
                                {diffLabels
                                  ? `Answer · ${diffLabels.left}`
                                  : "Baseline answer"}
                              </h3>
                              <p>
                                {pair.baseline.output?.answer ||
                                  "No answer returned"}
                              </p>
                            </div>
                            <div>
                              <h3>
                                {diffLabels
                                  ? `Answer · ${diffLabels.right}`
                                  : "Faulted answer"}
                              </h3>
                              <p>
                                {pair.fault.output?.answer ||
                                  pair.fault.output?.error ||
                                  "No answer returned"}
                              </p>
                            </div>
                          </div>
                        </>
                      ) : (
                        <div className="empty">
                          Choose a completed faulted run to inspect its paired
                          baseline.
                        </div>
                      )}
                    </section>
                  </>
                ) : (
                  empty("No paired execution yet")
                ))}
              {page === "Run report" &&
                (exp ? (
                  <section className="panel">
                    <div className="panel-title">
                      <h2>Evidence-backed run report</h2>
                      <a
                        className="secondary"
                        href={"/api/experiments/" + exp.id + "/report.html"}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Readable HTML report
                      </a>
                      <a
                        className="primary"
                        href={"/api/experiments/" + exp.id + "/export"}
                        download
                      >
                        <Download size={16} /> Export JSON
                      </a>
                    </div>
                    <dl className="report">
                      <dt>Experiment</dt>
                      <dd>
                        {exp.name} / {exp.id}
                      </dd>
                      <dt>Conditions</dt>
                      <dd>
                        {exp.config.agent_ids.join(", ")} ·{" "}
                        {exp.config.scenario_ids.join(", ")} ·{" "}
                        {exp.config.repetitions} repetitions
                      </dd>
                      <dt>Evidence</dt>
                      <dd>
                        {exp.config.evidence_mode} / {exp.config.snapshot_id}
                      </dd>
                      <dt>Model</dt>
                      <dd>
                        {exp.config.evidence_mode === "demo"
                          ? "Offline evidence simulator"
                          : exp.config.model}
                      </dd>
                      <dt>Evaluation</dt>
                      <dd>
                        Deterministic required facts / accepted phrase checks.
                        No paid model judge.
                      </dd>
                      <dt>Cost accounting</dt>
                      <dd>
                        Unknown without provider usage and explicitly configured
                        token pricing. Demo tokens are unavailable.
                      </dd>
                      <dt>Limitations</dt>
                      <dd>
                        Local subprocess runner is not a security sandbox. Proxy
                        observes network calls, not private reasoning. Live
                        responses may be stochastic. Frozen query misses limit
                        evidence comparability. Phrase matching has known
                        limits.
                      </dd>
                    </dl>
                    {runTable(terminal)}
                  </section>
                ) : (
                  empty("No report yet")
                ))}
            </>
          )}
          <footer>
            <span>
              <PixelIcon name="start" size={14} /> GAUNTLET
            </span>
            <span>Controlled conditions. Honest measurements.</span>
            <span>RESEARCH AGENT EVALUATION</span>
          </footer>
        </div>
      </main>
    </div>
  );
}
function Stat({
  label,
  value,
  sub,
  icon,
}: {
  label: string;
  value: string;
  sub: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="stat">
      <div>
        <span>{label}</span>
        {icon}
      </div>
      <strong>{value}</strong>
      <small>{sub}</small>
    </div>
  );
}
function Integration({
  title,
  subtitle,
  state,
  ok,
}: {
  title: string;
  subtitle: string;
  state: string;
  ok: boolean;
}) {
  return (
    <div className="integration">
      <span className="integration-icon">{title[0]}</span>
      <span>
        <b>{title}</b>
        <small>{subtitle}</small>
      </span>
      <span className={"badge " + (ok ? "green" : "")}>
        <i className={"dot " + (ok ? "" : "gray")} />
        {state}
      </span>
    </div>
  );
}
function Chart({ rows }: { rows: Obj[] }) {
  return (
    <div className="chart">
      <ResponsiveContainer width="100%" height={220}>
        <BarChart
          data={rows.map((r) => ({
            name: r.agent_id,
            baseline:
              r.baseline_success == null
                ? null
                : Math.round(r.baseline_success * 100),
            faulted:
              r.fault_success == null
                ? null
                : Math.round(r.fault_success * 100),
          }))}
        >
          <CartesianGrid
            strokeDasharray="3 5"
            stroke="#D9D0BD"
            vertical={false}
          />
          <XAxis
            dataKey="name"
            stroke="#5B5668"
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            domain={[0, 100]}
            stroke="#5B5668"
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => v + "%"}
          />
          <Tooltip
            contentStyle={{
              background: "#FFFDF8",
              border: "2px solid #1D1A26",
              borderRadius: 8,
            }}
          />
          <Bar
            dataKey="baseline"
            isAnimationActive={false}
            name="Baseline %"
            fill="#5B3FD6"
            radius={[4, 4, 0, 0]}
            maxBarSize={44}
          />
          <Bar
            dataKey="faulted"
            isAnimationActive={false}
            name="Faulted %"
            fill="#12A5B0"
            radius={[4, 4, 0, 0]}
            maxBarSize={44}
          />
        </BarChart>
      </ResponsiveContainer>
      <div className="legend">
        <span>
          <i style={{ background: "#5B3FD6" }} /> Baseline
        </span>
        <span>
          <i style={{ background: "#12A5B0" }} /> Faulted
        </span>
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
