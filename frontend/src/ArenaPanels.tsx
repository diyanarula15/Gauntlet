import { AgentAvatar, PixelIcon } from "./ArenaAssets";
type Data = Record<string, any>;
export const agentColors = [
  "#2F6FED",
  "#F2811D",
  "#EE5A7A",
  "#6B9B1E",
  "#0E9AA7",
  "#5C6B8A",
];
export function AgentIdentity({
  id,
  name,
  size = 32,
  index = 0,
}: {
  id: string;
  name: string;
  size?: number;
  index?: number;
}) {
  return (
    <span className="agent-identity">
      <AgentAvatar id={id} size={size} color={agentColors[index % 6]} />
      <span>{name}</span>
    </span>
  );
}
export function Meter({
  value,
  color,
  label,
}: {
  value: number | null | undefined;
  color: string;
  label: string;
}) {
  return (
    <span
      className="pixel-meter"
      aria-label={`${label}: ${value == null ? "not established" : Math.round(value * 100) + "%"}`}
    >
      <b>{label}</b>
      <span className="meter-blocks">
        {Array.from({ length: 10 }, (_, i) => (
          <i
            key={i}
            style={{
              background:
                value != null && i < Math.round(value * 10)
                  ? color
                  : "var(--sunken)",
            }}
          />
        ))}
      </span>
      <small>{value == null ? "—" : `${Math.round(value * 100)}%`}</small>
    </span>
  );
}
export function ChallengerCards({
  experiment,
  catalog,
  onInspect,
}: {
  experiment: Data | null;
  catalog: Data;
  onInspect: (id: string) => void;
}) {
  const agents = catalog.agents.filter(
    (a: Data) => !experiment || experiment.config.agent_ids.includes(a.id),
  );
  return (
    <div className="challenger-grid">
      {agents.map((a: Data, i: number) => {
        const color =
          agentColors[
            catalog.agents.findIndex((agent: Data) => agent.id === a.id) % 6
          ];
        const result = experiment?.leaderboard.find(
          (r: Data) => r.agent_id === a.id,
        );
        return (
          <button
            className="challenger-card"
            key={a.id}
            onClick={() => onInspect(a.id)}
            style={{ "--agent-color": color } as React.CSSProperties}
          >
            <div className="challenger-top">
              <span className="eyebrow">
                CHALLENGER {String(i + 1).padStart(2, "0")}
              </span>
              <span className="tag">{a.framework}</span>
            </div>
            <div className="challenger-body">
              <AgentAvatar id={a.id} size={62} color={color} />
              <div>
                <h3>{a.display_name}</h3>
                <small>{a.retry_policy}</small>
              </div>
              <div className="challenger-score">
                <strong>
                  {result?.score == null ? "—" : Math.round(result.score)}
                </strong>
                <small>{result?.score == null ? "UNRANKED" : "SCORE"}</small>
              </div>
            </div>
            <div className="challenger-meters">
              <Meter
                value={result?.clean_success}
                color="var(--green)"
                label="S"
              />
              <Meter value={result?.pass_3} color="var(--violet)" label="R" />
              <Meter
                value={result?.fault_tolerance}
                color="var(--teal)"
                label="F"
              />
              <Meter
                value={result?.score_components?.E}
                color="var(--amber)"
                label="E"
              />
            </div>
            <div className="challenger-bottom">
              <span>
                {result
                  ? `${result.runs}/${result.planned_runs} observed`
                  : "Awaiting first run"}
              </span>
              <span>Inspect challenger ↗</span>
            </div>
          </button>
        );
      })}
    </div>
  );
}
export function BehaviorBadge({ behavior }: { behavior: string }) {
  const labels: Data = {
    recovered: "Recovered",
    safe_failure: "Safe failure",
    correct_abstention: "Correct abstention",
    silent_failure: "Silent failure",
    canary_detected: "Canary detected",
    runaway: "Runaway",
    crash: "Crash",
    honest_failure: "Reported failure",
    unknown: "Unclassified",
  };
  return (
    <span className={"badge behavior-" + behavior}>
      <PixelIcon
        name={
          behavior === "recovered"
            ? "answer"
            : behavior === "runaway"
              ? "limit"
              : behavior === "canary_detected"
                ? "trap"
                : "start"
        }
        size={13}
      />
      {labels[behavior] || behavior.replaceAll("_", " ")}
    </span>
  );
}
export function TraceTiles({ events }: { events: Data[] }) {
  const calls = events.filter((e) => e.event_type === "CALL");
  return (
    <div className="trace-track" aria-label="Execution path">
      {calls.map((e, i) => {
        const type = e.fault
          ? "trap"
          : e.service === "model"
            ? "model"
            : "tool";
        return (
          <div className="trace-track-step" key={e.id}>
            <button
              className={"trace-tile tile-" + type}
              aria-label={`Jump to ${e.service} call ${i + 1}`}
              title={`${e.service} · HTTP ${e.status_code}`}
              onClick={() => {
                const target = document.getElementById(
                  "event-" + e.id,
                ) as HTMLDetailsElement | null;
                if (target) {
                  target.open = true;
                  target.scrollIntoView({
                    behavior: "smooth",
                    block: "center",
                  });
                  target.focus();
                }
              }}
            >
              <PixelIcon name={type} size={23} />
            </button>
            <small>{i + 1}</small>
            {i < calls.length - 1 && <span className="tile-link" />}
          </div>
        );
      })}
    </div>
  );
}
