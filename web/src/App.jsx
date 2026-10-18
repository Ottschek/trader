import { useEffect, useMemo, useState } from "react";
import {
  ageHours, compact, driftRows, formatAge, grouped, groupDatasets, hourlyCells, maxAgeHours,
  overallStatus, relativeTime, shortDate, shortDateTime,
} from "./health.js";

const REFRESH_MS = 60 * 1000;

const STATUS = {
  ok: { icon: "✓", label: "OK" },
  warn: { icon: "!", label: "Warning" },
  fail: { icon: "✕", label: "Failing" },
  none: { icon: "", label: "No run" },
};

function useHealth() {
  const [state, setState] = useState({ phase: "loading" });
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch("/api/health", { credentials: "same-origin" });
        const body = await res.json().catch(() => ({}));
        if (!alive) return;
        if (res.status === 403 && body.error === "locked") setState({ phase: "locked", reason: body.reason });
        else if (!res.ok) setState({ phase: "error", reason: body.reason || `HTTP ${res.status}` });
        else setState({ phase: "ready", data: body, loadedAt: Date.now() });
      } catch (err) {
        if (alive) setState((s) => (s.phase === "ready" ? s : { phase: "error", reason: err.message }));
      }
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => { alive = false; clearInterval(id); };
  }, []);
  return state;
}

function StatusBadge({ status, children }) {
  const s = STATUS[status] ?? STATUS.none;
  return (
    <span className={`badge badge-${status}`}>
      <span className="badge-icon" aria-hidden="true">{s.icon}</span>
      {children ?? s.label}
    </span>
  );
}

function Tile({ label, value, sub, status }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      <div className="tile-sub">{status ? <StatusBadge status={status}>{sub}</StatusBadge> : sub}</div>
    </div>
  );
}

function RunTimeline({ runs, now }) {
  const cells = useMemo(() => hourlyCells(runs, now, 72), [runs, now]);
  const [hover, setHover] = useState(null);
  const ran = cells.filter((c) => c.run).length;
  return (
    <section className="card">
      <div className="card-head">
        <h2>Collector runs · last 72 hours</h2>
        <span className="muted">{ran} of 72 hours had a run</span>
      </div>
      <div className="timeline" role="img" aria-label={`${ran} of the last 72 hours had a collector run`}
           onMouseLeave={() => setHover(null)}>
        {cells.map((c, i) => (
          <button
            key={c.start}
            type="button"
            className={`cell cell-${c.run ? c.run.status : "none"}`}
            onMouseEnter={() => setHover(i)}
            onFocus={() => setHover(i)}
            onBlur={() => setHover(null)}
            aria-label={`${shortDateTime(new Date(c.start).toISOString())}: ${c.run ? STATUS[c.run.status].label : "no run"}`}
          />
        ))}
        {hover != null && <CellTooltip cell={cells[hover]} index={hover} total={cells.length} />}
      </div>
      <div className="timeline-axis muted">
        <span>72 h ago</span><span>48 h</span><span>24 h</span><span>now</span>
      </div>
      <div className="legend">
        {["ok", "warn", "fail", "none"].map((s) => (
          <span key={s} className="legend-item">
            <span className={`swatch cell-${s}`} aria-hidden="true" />{STATUS[s].label}
          </span>
        ))}
      </div>
    </section>
  );
}

function CellTooltip({ cell, index, total }) {
  const left = `${((index + 0.5) / total) * 100}%`;
  const r = cell.run;
  return (
    <div className="tooltip" style={{ left }} role="status">
      <div className="tooltip-title">{shortDateTime(new Date(cell.start).toISOString())}</div>
      {r ? (
        <>
          <StatusBadge status={r.status} />
          <div>{r.n_fail} fail · {r.n_warn} warn · {r.n_ok} ok</div>
          <div>+{grouped(r.rows_added)} rows · {Math.round(r.duration_s)} s · {r.trigger}</div>
          {cell.count > 1 && <div className="muted">{cell.count} runs this hour</div>}
        </>
      ) : (
        <div className="muted">No collector run this hour</div>
      )}
    </div>
  );
}

function Problems({ checks }) {
  const order = { fail: 0, warn: 1 };
  const problems = checks.filter((c) => c.status !== "ok")
    .sort((a, b) => order[a.status] - order[b.status] || a.dataset.localeCompare(b.dataset));
  return (
    <section className="card">
      <div className="card-head">
        <h2>Open problems · latest run</h2>
        <span className="muted">{checks.length} checks</span>
      </div>
      {problems.length === 0 ? (
        <p className="empty"><StatusBadge status="ok">All checks pass</StatusBadge></p>
      ) : (
        <ul className="problems">
          {problems.map((p) => (
            <li key={`${p.check_name}|${p.dataset}`}>
              <StatusBadge status={p.status} />
              <div>
                <div className="problem-title">{p.dataset} <span className="muted">· {p.check_name}</span></div>
                <div className="muted">{p.detail}</div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Drift({ checks }) {
  const rows = driftRows(checks);
  if (!rows.length) return null;
  return (
    <section className="card">
      <div className="card-head">
        <h2>Alpaca vs Coinbase price drift</h2>
        <span className="muted">last 30 days · warn above 20 bps</span>
      </div>
      <div className="table-wrap">
      <table className="table">
        <thead><tr><th>Coin</th><th className="num">Median</th><th className="num">95th pct</th><th>Status</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.symbol}>
              <td>{r.symbol}</td>
              <td className="num">{r.median ?? "—"} bps</td>
              <td className="num">{r.p95 ?? "—"} bps</td>
              <td><StatusBadge status={r.status} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </section>
  );
}

function Datasets({ datasets, now }) {
  const groups = groupDatasets(datasets);
  return (
    <section className="card">
      <div className="card-head">
        <h2>Stored data</h2>
        <span className="muted">{datasets.length} files</span>
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Dataset</th><th className="num">Rows</th><th>From</th><th>Newest</th><th className="num">Age</th></tr>
          </thead>
          {groups.map((g) => (
            <tbody key={g.label}>
              <tr className="group-row"><th colSpan={5}>{g.label}</th></tr>
              {g.rows.map((d) => {
                const age = ageHours(d.last_ts, now);
                const limit = maxAgeHours(d);
                const stale = limit != null && age != null && age > limit;
                return (
                  <tr key={d.dataset}>
                    <td>{d.symbol ?? d.dataset.split("/").pop()}</td>
                    <td className="num">{grouped(d.rows)}</td>
                    <td>{shortDate(d.first_ts)}</td>
                    <td>{shortDateTime(d.last_ts)}</td>
                    <td className="num">
                      {stale ? <StatusBadge status="fail">{formatAge(age)}</StatusBadge> : formatAge(age)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          ))}
        </table>
      </div>
    </section>
  );
}

function Dashboard({ data, now }) {
  const { runs, checks, datasets, latest_run_at: latest } = data;
  const status = overallStatus(checks);
  const last24 = runs.filter((r) => now - new Date(r.run_at).getTime() < 24 * 3600 * 1000).length;
  const fails = checks.filter((c) => c.status === "fail").length;
  const warns = checks.filter((c) => c.status === "warn").length;
  const rows = datasets.reduce((s, d) => s + (d.rows || 0), 0);

  return (
    <>
      <div className="tiles">
        <Tile label="Last run" value={latest ? relativeTime(latest, now) : "never"}
              sub={latest ? STATUS[status].label : "—"} status={latest ? status : null} />
        <Tile label="Runs · last 24 h" value={`${last24}`} sub="of 24 expected"
              status={last24 >= 22 ? "ok" : last24 >= 12 ? "warn" : "fail"} />
        <Tile label="Open problems" value={`${fails + warns}`} sub={`${fails} failing · ${warns} warnings`}
              status={fails ? "fail" : warns ? "warn" : "ok"} />
        <Tile label="Rows stored" value={compact(rows)} sub={`${datasets.length} files in R2`} />
      </div>
      <RunTimeline runs={runs} now={now} />
      <div className="two-col">
        <Problems checks={checks} />
        <Drift checks={checks} />
      </div>
      <Datasets datasets={datasets} now={now} />
    </>
  );
}

export default function App() {
  const state = useHealth();
  const now = state.loadedAt ?? Date.now();
  return (
    <div className="page">
      <header className="header">
        <div className="brand">
          <span className="logo" aria-hidden="true" />
          <div>
            <div className="brand-name">Trader</div>
            <div className="muted">Data health</div>
          </div>
        </div>
        {state.phase === "ready" && (
          <span className="muted small">Updated {shortDateTime(new Date(now).toISOString())} · refreshes every minute</span>
        )}
      </header>
      <main>
        {state.phase === "loading" && <p className="notice">Loading…</p>}
        {state.phase === "locked" && (
          <div className="notice">
            <strong>Locked.</strong> {state.reason} The data appears once the login is in front of this site.
          </div>
        )}
        {state.phase === "error" && <div className="notice notice-error"><strong>Could not load data.</strong> {state.reason}</div>}
        {state.phase === "ready" && <Dashboard data={state.data} now={now} />}
      </main>
    </div>
  );
}
