import { PageState, StatusBadge, Tile, useJson } from "./ui.jsx";
import { shortDate, shortDateTime } from "./health.js";

const MAX_POSITION = 0.10;   // risk manager cap (config/paper.yaml)
const usd = (v) => (v == null ? "—" : `$${Number(v).toLocaleString("en", { maximumFractionDigits: 0 })}`);
const pct = (v, d = 1) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%`);

const KIND = {
  entry: ["ok", "Entry"], exit: ["none", "Exit"], no_trade: ["none", "No trade"], skipped: ["warn", "Skipped"],
  risk_close: ["fail", "Risk close"], manual_stop: ["fail", "Manual stop"],
};

function ShadowBanner() {
  return (
    <section className="card shadow-banner">
      <span className="shadow-icon" aria-hidden="true">🧪</span>
      <div>
        <h2>Shadow experiment · not a validated candidate</h2>
        <p className="muted">
          <b>weekend_gap_sized</b> passed 5 of 6 research gates but failed the deflated Sharpe (0.71, 29 trials).
          It runs on the Alpaca <b>paper</b> account (fake money) with frozen rules to collect new, forward
          out-of-sample evidence. After a weekend where BTC rose more than 6%, it buys COIN, MSTR, MARA and RIOT
          at Monday's open (sized by volatility, max 10% each) and sells at the next open.
        </p>
      </div>
    </section>
  );
}

function EquityChart({ points, expected }) {
  const actual = points.map((p) => [Date.parse(p.ts), p.equity]);
  const exp = (expected || []).map(([t, v]) => [Date.parse(t), v]);
  if (actual.length < 2) return <p className="muted">The equity curve appears after a few hourly snapshots.</p>;
  const all = [...actual, ...exp];
  const W = 760, H = 220, L = 60, R = 84, T = 10, B = 24;
  const t0 = Math.min(...all.map((p) => p[0])), t1 = Math.max(...all.map((p) => p[0]));
  let lo = Math.min(...all.map((p) => p[1])), hi = Math.max(...all.map((p) => p[1]));
  if (hi - lo < 1) { lo -= 50; hi += 50; }
  const x = (t) => L + ((W - L - R) * (t - t0)) / Math.max(t1 - t0, 1);
  const y = (v) => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  const path = (pts) => pts.map(([t, v]) => `${x(t).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const lastA = actual.at(-1), lastE = exp.at(-1);
  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} className="equity-svg" role="img"
           aria-label={`Paper equity ${usd(lastA[1])}${lastE ? `, backtest expects ${usd(lastE[1])}` : ""}`}>
        {[lo, (lo + hi) / 2, hi].map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="grid-line" />
            <text x={L - 8} y={y(v) + 4} textAnchor="end" className="axis-text">{usd(v)}</text>
          </g>
        ))}
        <text x={L} y={H - 6} className="axis-text">{shortDate(new Date(t0).toISOString())}</text>
        <text x={W - R} y={H - 6} textAnchor="end" className="axis-text">{shortDate(new Date(t1).toISOString())}</text>
        {exp.length > 1 && <polyline points={path(exp)} className="expected-line" />}
        <polyline points={path(actual)} className="equity-line" />
        <text x={x(lastA[0]) + 6} y={y(lastA[1]) + 4} className="series-label">Paper</text>
        {lastE && <text x={x(lastE[0]) + 6} y={y(lastE[1]) + (Math.abs(y(lastE[1]) - y(lastA[1])) < 14 ? 18 : 4)}
                        className="series-label">Backtest</text>}
      </svg>
      <div className="legend">
        <span className="legend-item"><span className="swatch-line paper" aria-hidden="true" />Paper account (actual)</span>
        <span className="legend-item"><span className="swatch-line backtest" aria-hidden="true" />Backtest (expected)</span>
      </div>
    </>
  );
}

function LiveVsBacktest({ r }) {
  if (!r) return null;
  const fmt = (v) => (v == null ? "—" : pct(v, 2));
  return (
    <section className="card">
      <div className="card-head">
        <h2>Live vs backtest</h2>
        <span className="muted">the frozen strategy re-run through the backtest engine · {shortDateTime(r.computed_at)}</span>
      </div>
      <div className="mini-stats">
        <div><span className="muted small">Paper return</span><strong>{fmt(r.actual_return)}</strong></div>
        <div><span className="muted small">Backtest expected</span><strong>{fmt(r.expected_return)}</strong></div>
        <div><span className="muted small">Tracking difference</span><strong>{fmt(r.tracking_difference)}</strong></div>
        <div><span className="muted small">Avg fill vs open</span>
          <strong>{r.avg_slippage_bps == null ? "—" : `${r.avg_slippage_bps} bps`}</strong></div>
        <div><span className="muted small">Decision mismatches</span>
          <strong>{r.mismatches.length ? <StatusBadge status="fail">{r.mismatches.length}</StatusBadge> : <StatusBadge status="ok">0</StatusBadge>}</strong></div>
      </div>
      {r.trades.length === 0 ? (
        <p className="muted">No weekend sessions since the experiment started ({shortDate(r.start)}).</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Session</th><th className="num">BTC move</th><th>Backtest</th><th>Paper</th><th>Agree?</th></tr></thead>
            <tbody>
              {r.trades.map((t) => (
                <tr key={t.session}>
                  <td>{t.session}</td>
                  <td className="num">{t.btc_move == null ? "—" : pct(t.btc_move)}</td>
                  <td>{t.backtest_traded ? `traded ${fmt(t.backtest_return)}` : "no trade"}</td>
                  <td>{t.live_decision === "entry" ? `traded ${fmt(t.live_return)}` : t.live_decision.replace("_", " ")}</td>
                  <td><StatusBadge status={t.match ? "ok" : "fail"}>{t.match ? "yes" : "no"}</StatusBadge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted small">Positive slippage = paid more than the backtest assumed. A mismatch means live and backtest
        disagreed about trading (different data or timing) and triggers a Telegram alert.</p>
    </section>
  );
}

function CarrySim({ rows }) {
  if (!rows || rows.length === 0) {
    return (
      <section className="card">
        <div className="card-head"><h2>Carry simulation</h2><span className="muted">shadow · no real orders · no wallet</span></div>
        <p className="muted">Starts with the next hourly run: long spot on Alpaca + short perp on Hyperliquid in 10 coins, $100,000 virtual.</p>
      </section>
    );
  }
  const first = rows[0], last = rows.at(-1);
  const days = Math.max((Date.parse(last.ts) - Date.parse(first.ts)) / 86400000, 1 / 24);
  const ret = last.equity / 100000 - 1, model = last.model_equity / 100000 - 1;
  const annual = days >= 7 ? (1 + ret) ** (365 / days) - 1 : null;
  const points = rows.map((r) => ({ ts: r.ts, equity: r.equity }));
  const expected = rows.map((r) => [r.ts, r.model_equity]);
  return (
    <section className="card">
      <div className="card-head">
        <h2>Carry simulation: cash-and-carry, always on</h2>
        <span className="muted">shadow · no real orders · real prices from Alpaca and Hyperliquid · since {shortDate(first.ts)}</span>
      </div>
      <div className="mini-stats">
        <div><span className="muted small">Simulated return</span><strong>{pct(ret, 2)}</strong></div>
        <div><span className="muted small">Annualized</span><strong>{annual == null ? "after 7 days" : pct(annual, 1)}</strong></div>
        <div><span className="muted small">Backtest model</span><strong>{pct(model, 2)}</strong></div>
        <div><span className="muted small">Funding collected</span><strong>{usd(last.funding_cum)}</strong></div>
        <div><span className="muted small">Fees paid · rebalances</span><strong>{usd(last.fees_cum)} · {last.rebalances}</strong></div>
      </div>
      <EquityChart points={points} expected={expected} />
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Coin</th><th className="num">Perp vs spot (basis)</th><th className="num">Funding now (per year)</th></tr></thead>
          <tbody>
            {Object.keys(last.basis).map((c) => (
              <tr key={c}><td>{c}</td><td className="num">{(last.basis[c] * 1e4).toFixed(1)} bps</td>
                <td className="num">{pct(last.funding[c] * 24 * 365, 1)}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">Peak margin use {pct(last.max_margin_use, 0)} of a pair's 25% perp margin (pairs are re-hedged at 80%).
        The model line is what the backtest formula predicts; the gap is the reality cost (spreads, cross-venue basis).</p>
    </section>
  );
}

function scoredStats(signals, h) {
  const net = signals.map((s) => s[`net_${h}d`]).filter((v) => v != null);
  if (net.length === 0) return { n: 0, mean: null, hit: null };
  return { n: net.length, mean: net.reduce((a, b) => a + b, 0) / net.length, hit: net.filter((v) => v > 0).length / net.length };
}

function SourceStats({ label, signals, extra }) {
  const d1 = scoredStats(signals, 1), d5 = scoredStats(signals, 5);
  const hit = (d) => `${d.n} scored${d.hit != null ? ` · ${Math.round(d.hit * 100)}% winners` : ""}`;
  return (
    <div className="mini-stats">
      <div><span className="muted small">{label}</span><strong>{signals.length} signals</strong></div>
      <div><span className="muted small">1-day net excess (avg)</span><strong>{pct(d1.mean, 2)}</strong><span className="muted small">{hit(d1)}</span></div>
      <div><span className="muted small">5-day net excess (avg)</span><strong>{pct(d5.mean, 2)}</strong><span className="muted small">{hit(d5)}</span></div>
      {extra}
    </div>
  );
}

function NewsSignals({ news }) {
  const runs = news?.runs ?? [], signals = news?.signals ?? [];
  const lexicon = signals.filter((s) => s.source === "lexicon"), claude = signals.filter((s) => s.source !== "lexicon");
  const month = new Date().toISOString().slice(0, 7);
  const spent = runs.filter((r) => r.ts.startsWith(month)).reduce((a, r) => a + r.cost_usd, 0);
  return (
    <section className="card">
      <div className="card-head">
        <h2>News signals</h2>
        <span className="muted">shadow · no real orders · Yahoo Finance headlines, scored every 6 hours</span>
      </div>
      <SourceStats label="Word list (free)" signals={lexicon} />
      {runs.length > 0
        ? <SourceStats label="LLM analyst (Claude)" signals={claude}
            extra={<div><span className="muted small">Spent this month</span><strong>${spent.toFixed(2)}</strong><span className="muted small">of $5.00</span></div>} />
        : <p className="muted small">LLM analyst: off (no API key). It is built and would start on its own if a key were added.</p>}
      {signals.length === 0 && <p className="muted">No signals yet. Headlines are collected every hour.</p>}
      {signals.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Decided</th><th>Source</th><th>Asset</th><th>Side</th><th>Why</th>
              <th className="num">1 day</th><th className="num">5 days</th></tr></thead>
            <tbody>
              {signals.slice(0, 30).map((s) => (
                <tr key={s.id}>
                  <td>{shortDateTime(s.decided_at)}</td>
                  <td>{s.source === "lexicon" ? "Word list" : "Claude"}</td>
                  <td>{s.symbol}</td>
                  <td><StatusBadge status={s.side > 0 ? "ok" : "fail"}>{s.side > 0 ? "Long" : "Short"} {s.score > 0 ? "+" : ""}{s.score}</StatusBadge></td>
                  <td className="small">{s.headlines.map((h, i) => (
                    <div key={i}><a href={h.link} target="_blank" rel="noreferrer">{h.title}</a>
                      <span className="muted"> · {h.impact > 0 ? "+" : ""}{h.impact}{h.reason !== "word list" ? ` ${h.reason}` : ""}</span></div>
                  ))}</td>
                  <td className="num">{s.net_1d == null ? "pending" : pct(s.net_1d, 2)}</td>
                  <td className="num">{s.net_5d == null ? "pending" : pct(s.net_5d, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted small">A signal is a 6-hour window where one asset's headline scores add up to ±2 or more.
        Returns run from the next open, minus the benchmark (SPY for stocks, BTC for coins) and round-trip costs.
        Rules frozen on 19 Oct 2026; forward only, no backtest.</p>
    </section>
  );
}

function Plan({ plan }) {
  if (!plan) return null;
  return (
    <section className="card">
      <div className="card-head">
        <h2>Next weekend trade: position plan</h2>
        <span className="muted">from the strategy code · as of {shortDateTime(plan.as_of)}</span>
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Stock</th><th className="num">Strategy size</th><th className="num">After 10% cap</th>
            <th className="num">Daily volatility</th><th className="num">Last price</th></tr></thead>
          <tbody>
            {Object.keys(plan.sizes).map((s) => (
              <tr key={s}>
                <td>{s}</td>
                <td className="num">{pct(plan.sizes[s])}</td>
                <td className="num">{pct(Math.min(plan.sizes[s], MAX_POSITION))}</td>
                <td className="num">{pct(plan.daily_vol[s])}</td>
                <td className="num">{plan.ref_prices[s] != null ? `$${plan.ref_prices[s]}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">Only used if BTC rises more than {pct(plan.params.threshold, 0)} between the last stock
        close and 09:00 New York on the first trading morning after a weekend or holiday.</p>
    </section>
  );
}

function Decisions({ decisions }) {
  return (
    <section className="card">
      <div className="card-head"><h2>Decisions</h2><span className="muted">every 09:00 weekend check, trade or not</span></div>
      {decisions.length === 0 ? <p className="muted">No decisions yet. The first is at 09:00 New York on the first trading day after a weekend.</p> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>When</th><th>Decision</th><th className="num">BTC move</th><th>Details</th></tr></thead>
            <tbody>
              {decisions.map((d) => {
                const [status, label] = KIND[d.kind] ?? ["none", d.kind];
                const det = d.detail || {};
                const what = det.reason || (det.orders ? det.orders.map((o) => `${o.side === "sell" ? "SELL" : "BUY"} ${o.qty} ${o.symbol}`).join(", ") : "");
                return (
                  <tr key={d.id}>
                    <td>{shortDateTime(d.decided_at)}</td>
                    <td><StatusBadge status={status}>{label}</StatusBadge></td>
                    <td className="num">{det.move != null ? `${pct(det.move)} (needs > ${pct(det.threshold, 0)})` : "—"}</td>
                    <td className="small details">{what}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Orders({ orders }) {
  return (
    <section className="card">
      <div className="card-head"><h2>Orders</h2><span className="muted">fill price vs the price used for sizing</span></div>
      {orders.length === 0 ? <p className="muted">No orders yet.</p> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Submitted</th><th>Order</th><th>Status</th><th className="num">Fill</th><th className="num">Sizing price</th><th className="num">Difference</th></tr></thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.id}>
                  <td>{shortDateTime(o.submitted_at)}</td>
                  <td>{o.side.toUpperCase()} {o.qty} {o.symbol} <span className="muted small">{o.order_type}/{o.time_in_force}</span></td>
                  <td><StatusBadge status={o.status === "filled" ? "ok" : ["canceled", "expired", "rejected"].includes(o.status) ? "fail" : "none"}>{o.status}</StatusBadge></td>
                  <td className="num">{o.filled_avg_price ? `$${o.filled_avg_price.toFixed(2)}` : "—"}</td>
                  <td className="num">{o.ref_price ? `$${o.ref_price}` : "—"}</td>
                  <td className="num">{o.filled_avg_price && o.ref_price ? pct(o.filled_avg_price / o.ref_price - 1, 2) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Paper({ data }) {
  const eq = data.equity;
  const last = eq.at(-1);
  const first = eq[0];
  const halted = data.state.halted;
  const open = data.state.open_trade;
  return (
    <>
      <ShadowBanner />
      <div className="tiles">
        <Tile label="Paper equity" value={usd(last?.equity)} sub={last ? `snapshot ${shortDateTime(last.ts)}` : "no snapshot yet"} />
        <Tile label="Since start" value={first && last ? pct(last.equity / first.equity - 1, 2) : "—"}
              sub={first ? `from ${usd(first.equity)} on ${shortDate(first.ts)}` : "starts with the first snapshot"} />
        <Tile label="Open trade" value={open ? "Yes" : "None"} sub={open ? `entered ${open.session_date}` : "waiting for a big BTC weekend"} />
        <Tile label="Trading" value={halted ? "Halted" : "Active"} sub={halted ? halted.reason : "risk limits OK"}
              status={halted ? "fail" : "ok"} />
      </div>
      <section className="card">
        <div className="card-head"><h2>Paper equity vs backtest</h2><span className="muted">hourly snapshots, last 90 days</span></div>
        <EquityChart points={eq} expected={data.reconcile?.expected_curve} />
      </section>
      <LiveVsBacktest r={data.reconcile} />
      <CarrySim rows={data.carry} />
      <NewsSignals news={data.news} />
      <Plan plan={data.plan} />
      <Decisions decisions={data.decisions} />
      <Orders orders={data.orders} />
      <p className="muted small">Control the bot from Telegram: /status · /stop (close positions and halt) · /resume.</p>
    </>
  );
}

export default function PaperPage() {
  const state = useJson("/api/paper");
  return <PageState state={state}>{(data) => <Paper data={data} />}</PageState>;
}
