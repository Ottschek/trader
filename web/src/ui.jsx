import { useEffect, useState } from "react";

export const REFRESH_MS = 60 * 1000;

export const STATUS = {
  ok: { icon: "✓", label: "OK" },
  warn: { icon: "!", label: "Warning" },
  fail: { icon: "✕", label: "Failing" },
  none: { icon: "", label: "No run" },
};

/** Fetch JSON from an API route, refreshing every minute. */
export function useJson(url) {
  const [state, setState] = useState({ phase: "loading" });
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch(url, { credentials: "same-origin", cache: "no-store" });
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
  }, [url]);
  return state;
}

export function StatusBadge({ status, children }) {
  const s = STATUS[status] ?? STATUS.none;
  return (
    <span className={`badge badge-${status}`}>
      <span className="badge-icon" aria-hidden="true">{s.icon}</span>
      {children ?? s.label}
    </span>
  );
}

export function Tile({ label, value, sub, status }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      <div className="tile-sub">{status ? <StatusBadge status={status}>{sub}</StatusBadge> : sub}</div>
    </div>
  );
}

export function PageState({ state, children }) {
  if (state.phase === "loading") return <p className="notice">Loading…</p>;
  if (state.phase === "locked") return <div className="notice"><strong>Locked.</strong> {state.reason}</div>;
  if (state.phase === "error") return <div className="notice notice-error"><strong>Could not load data.</strong> {state.reason}</div>;
  return children(state.data, state.loadedAt);
}
