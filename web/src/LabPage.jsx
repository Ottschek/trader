import { useState } from "react";
import { PageState, StatusBadge, Tile, useJson } from "./ui.jsx";
import { shortDateTime } from "./health.js";

const VERDICT = {
  rejected: { status: "fail", label: "Rejected" },
  candidate: { status: "ok", label: "Candidate" },
  holdout_passed: { status: "ok", label: "Passed holdout" },
  holdout_failed: { status: "fail", label: "Failed holdout" },
};
const ALIVE = new Set(["candidate", "holdout_passed"]);
const FILTERS = [
  ["all", "All"],
  ["alive", "Candidates"],
  ["graveyard", "Graveyard"],
];

const num = (v, digits = 2) => (v == null ? "—" : v.toFixed(digits));
const pct = (v) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);

function GateDots({ gates }) {
  return (
    <span className="gates" role="list" aria-label="Gate results">
      {gates.map((g) => (
        <span key={g.name} role="listitem" className={`gate gate-${g.passed ? "ok" : "fail"}`}
              title={`${g.label}: ${g.value} (needs ${g.threshold}) · ${g.passed ? "pass" : "fail"}`}
              aria-label={`${g.label}: ${g.passed ? "pass" : "fail"}`}>
          {g.passed ? "✓" : "✕"}
        </span>
      ))}
    </span>
  );
}

function Funnel({ experiments }) {
  const steps = [
    ["Tested", experiments.length],
    ["Passed research gates", experiments.filter((e) => e.verdict !== "rejected").length],
    ["Passed holdout", experiments.filter((e) => e.verdict === "holdout_passed").length],
    ["Paper trading", 0],
  ];
  const max = Math.max(1, steps[0][1]);
  return (
    <section className="card">
      <div className="card-head">
        <h2>Idea funnel</h2>
        <span className="muted">every idea must survive each stage before the next</span>
      </div>
      <ol className="funnel">
        {steps.map(([label, n], i) => (
          <li key={label}>
            <div className="funnel-label"><span>{label}</span><strong>{n}</strong></div>
            <div className="funnel-track">
              <div className="funnel-bar" style={{ width: `${(n / max) * 100}%`, opacity: 1 - i * 0.18 }} />
            </div>
          </li>
        ))}
      </ol>
      <p className="muted small">Paper trading starts in M5.</p>
    </section>
  );
}

function Ledger({ experiments }) {
  const [filter, setFilter] = useState("all");
  const shown = experiments.filter((e) =>
    filter === "all" ? true : filter === "alive" ? ALIVE.has(e.verdict) : !ALIVE.has(e.verdict));
  return (
    <section className="card">
      <div className="card-head">
        <h2>Experiment ledger</h2>
        <div className="chips" role="tablist" aria-label="Filter experiments">
          {FILTERS.map(([key, label]) => (
            <button key={key} type="button" role="tab" aria-selected={filter === key}
                    className={`chip ${filter === key ? "chip-on" : ""}`} onClick={() => setFilter(key)}>
              {label}
            </button>
          ))}
        </div>
      </div>
      {shown.length === 0 ? (
        <p className="empty muted">
          {experiments.length === 0
            ? "No experiments yet. Run scripts/validate.py to test an idea."
            : filter === "alive" ? "No candidates yet. That's normal: most ideas die." : "The graveyard is empty."}
        </p>
      ) : (
        <div className="table-wrap">
          <table className="table ledger">
            <thead>
              <tr>
                <th>Date</th><th>Idea</th><th>Verdict</th><th>Gates</th>
                <th className="num">OOS Sharpe</th><th className="num">vs hold</th>
                <th className="num">Max DD</th><th className="num">Trials</th><th>Report</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((e) => {
                const v = VERDICT[e.verdict] ?? VERDICT.rejected;
                const failed = e.gates.filter((g) => !g.passed).map((g) => g.label);
                return (
                  <tr key={e.id}>
                    <td>{shortDateTime(e.created_at)}</td>
                    <td>
                      <div className="idea-name">{e.strategy}</div>
                      <div className="muted small">{e.symbols.join(", ")}</div>
                    </td>
                    <td>
                      <StatusBadge status={v.status}>{v.label}</StatusBadge>
                      {e.verdict === "rejected" && failed.length > 0 && (
                        <div className="muted small killed">Killed by: {failed.join(", ")}</div>
                      )}
                    </td>
                    <td><GateDots gates={e.gates} /></td>
                    <td className="num">{num(e.oos_sharpe)}</td>
                    <td className="num">{num(e.bench_sharpe)}</td>
                    <td className="num">{pct(e.oos_max_drawdown)}</td>
                    <td className="num">{e.trials}</td>
                    <td>
                      {e.has_report
                        ? <a href={`/reports/${e.id}`} target="_blank" rel="noopener">Open ↗</a>
                        : <span className="muted">—</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted small">
        Gates: no lookahead · out-of-sample Sharpe ≥ 1 · beats buy-and-hold · drawdown ≥ −20% · beats random timing ·
        deflated Sharpe ≥ 0.95. Hover a gate for its value.
      </p>
    </section>
  );
}

function Lab({ data }) {
  const ex = data.experiments;
  const ideas = new Set(ex.map((e) => e.idea)).size;
  const trials = ex.reduce((s, e) => s + e.trials, 0);
  const alive = ex.filter((e) => ALIVE.has(e.verdict)).length;
  return (
    <>
      <div className="tiles">
        <Tile label="Ideas tested" value={ideas} sub={`${ex.length} validation run${ex.length === 1 ? "" : "s"}`} />
        <Tile label="Trials counted" value={trials} sub="every one raises the bar" />
        <Tile label="Candidates" value={alive} sub={alive ? "passed every research gate" : "none yet"}
              status={alive ? "ok" : null} />
        <Tile label="Graveyard" value={ex.length - alive} sub="rejected ideas, kept on record" />
      </div>
      <Funnel experiments={ex} />
      <Ledger experiments={ex} />
    </>
  );
}

export default function LabPage() {
  const state = useJson("/api/experiments");
  return <PageState state={state}>{(data) => <Lab data={data} />}</PageState>;
}
