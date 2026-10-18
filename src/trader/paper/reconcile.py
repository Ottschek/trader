"""Live vs backtest: is the paper bot doing what the backtest says it should?

Re-runs the frozen strategy through the backtest engine over the shadow period, with the
same constraints the bot has (10% risk cap), and compares:
  - equity:    actual paper equity vs the backtest's expected equity (tracking difference)
  - decisions: every weekend, did both trade or both stay out? (mismatches point at data or
               timing differences between live and backtest)
  - fills:     each fill price vs the 09:30 opening price the backtest assumed (slippage)
If live and backtest drift apart, the backtest was too optimistic and the research is suspect.
"""

from __future__ import annotations

import pandas as pd

from trader.backtest.data import MarketData
from trader.backtest.engine import run_backtest
from trader.backtest.runner import cost_bps
from trader.config import Settings
from trader.data.sessions import NY
from trader.data.store import bars_path, read
from trader.strategies.ideas import WeekendGapSized


def expected_run(data: MarketData, cfg: dict, settings: Settings):
    """The frozen strategy with the bot's risk cap, through the backtest engine."""
    exp = cfg["experiment"]
    strategy = WeekendGapSized(exp["symbols"], **exp["params"])
    targets = strategy.target_weights(data).clip(upper=cfg["risk"]["max_position_pct"])
    return targets, run_backtest(data, targets, cost_bps(data, settings), name="backtest")


def backtest_trades(targets: pd.DataFrame, result, start: pd.Timestamp) -> dict[str, float]:
    """{session date (New York): trade return} for backtest entries on/after `start`."""
    held = targets.fillna(0).sum(axis=1) > 0
    entries = held & ~held.shift(1, fill_value=False)
    fills = result.turnover[result.turnover > 0].index
    out = {}
    for t in entries[entries].index:
        session = t.tz_convert(NY).date().isoformat()
        if t < start:
            continue
        after = fills[fills > t]
        if len(after) < 1:
            continue
        entry_fill = after[0]
        exit_fill = after[1] if len(after) > 1 else result.equity.index[-1]
        before = result.equity[result.equity.index < entry_fill]
        if before.empty:
            continue
        out[session] = float(result.equity.loc[exit_fill] / before.iloc[-1] - 1)
    return out


def opening_price(symbol: str, session_date: str) -> float | None:
    """The 09:30 regular-session opening price (raw) on a New York date."""
    raw = read(bars_path("alpaca_stocks_30m", symbol))
    if raw.empty:
        return None
    local = raw["ts"].dt.tz_convert(NY)
    hit = raw[(local.dt.strftime("%Y-%m-%d") == session_date) & (local.dt.strftime("%H:%M") == "09:30")]
    return float(hit["open"].iloc[0]) if not hit.empty else None


def slippage(orders: list[dict]) -> list[dict]:
    """Fill price vs the open the backtest assumed, in basis points. Positive = cost to us."""
    out = []
    for o in orders:
        if o.get("status") != "filled" or not o.get("filled_avg_price") or not o.get("filled_at"):
            continue
        session = pd.Timestamp(o["filled_at"]).tz_convert(NY).date().isoformat()
        expected = opening_price(o["symbol"], session)
        if not expected:
            continue
        sign = 1 if o["side"] == "buy" else -1
        out.append({"symbol": o["symbol"], "side": o["side"], "session": session, "fill": float(o["filled_avg_price"]),
                    "expected_open": expected, "bps": round(sign * (float(o["filled_avg_price"]) / expected - 1) * 1e4, 1)})
    return out


def live_trades(decisions: list[dict], orders: list[dict]) -> dict[str, dict]:
    """{session: {decision, return}} from the bot's own records (return from fill prices)."""
    by_decision: dict[str, list[dict]] = {}
    for o in orders:
        by_decision.setdefault(o["decision_id"], []).append(o)
    exits = [d for d in decisions if d["kind"] == "exit"]
    out = {}
    for d in decisions:
        if d["kind"] not in ("entry", "no_trade", "skipped"):
            continue
        rec = {"decision": d["kind"], "move": (d.get("detail") or {}).get("move"), "return": None}
        if d["kind"] == "entry":
            buys = {o["symbol"]: o for o in by_decision.get(d["id"], []) if o.get("status") == "filled"}
            exit_d = next((x for x in exits if (x.get("detail") or {}).get("entry_decision") == d["id"]), None)
            sells = {o["symbol"]: o for o in by_decision.get(exit_d["id"], [])} if exit_d else {}
            equity = (d.get("detail") or {}).get("equity")
            done = [s for s in buys if sells.get(s, {}).get("status") == "filled"]
            if equity and done and len(done) == len(buys):
                pnl = sum((float(sells[s]["filled_avg_price"]) - float(buys[s]["filled_avg_price"])) * float(buys[s]["filled_qty"])
                          for s in done)
                rec["return"] = pnl / float(equity)
        out[d["session_date"]] = rec
    return out


def reconcile(data: MarketData, cfg: dict, settings: Settings, equity_rows: list[dict], orders: list[dict],
              decisions: list[dict], now: pd.Timestamp) -> dict:
    start = pd.Timestamp(cfg["experiment"]["start"], tz=NY).tz_convert("UTC")
    targets, result = expected_run(data, cfg, settings)
    rets = result.returns[result.returns.index >= start]

    eq = sorted((r for r in equity_rows if pd.Timestamp(r["ts"]) >= start), key=lambda r: r["ts"])
    start_equity = float(eq[0]["equity"]) if eq else None
    actual_return = float(eq[-1]["equity"]) / start_equity - 1 if eq else None
    expected_return = float((1 + rets).prod() - 1) if len(rets) else 0.0
    curve = []
    if start_equity and len(rets):
        expected_equity = start_equity * (1 + rets).cumprod()
        daily = expected_equity.resample("1D").last().dropna()
        curve = [[t.isoformat(), round(float(v), 2)] for t, v in daily.items()]

    bt = backtest_trades(targets, result, start)
    live = live_trades(decisions, orders)
    sessions = sorted(set(bt) | {s for s, r in live.items() if r["decision"] in ("entry", "no_trade", "skipped")})
    trades, mismatches = [], []
    for s in sessions:
        b, l = bt.get(s), live.get(s)
        live_traded = bool(l and l["decision"] == "entry")
        row = {"session": s, "backtest_traded": b is not None, "backtest_return": b,
               "live_decision": l["decision"] if l else "none", "live_return": l["return"] if l else None,
               "btc_move": l["move"] if l else None}
        row["match"] = (b is not None) == live_traded
        if not row["match"]:
            mismatches.append(s)
        trades.append(row)

    slips = slippage(orders)
    bps = [x["bps"] for x in slips]
    return {
        "computed_at": now.isoformat(),
        "start": start.isoformat(),
        "start_equity": start_equity,
        "actual_equity": float(eq[-1]["equity"]) if eq else None,
        "actual_return": actual_return,
        "expected_return": expected_return,
        "tracking_difference": (actual_return - expected_return) if actual_return is not None else None,
        "trades": trades,
        "mismatches": mismatches,
        "slippage": slips,
        "avg_slippage_bps": round(sum(bps) / len(bps), 1) if bps else None,
        "worst_slippage_bps": max(bps) if bps else None,
        "expected_curve": curve,
    }


# --- run from the collector ----------------------------------------------------------

KEEP_RUNS = 100


def _rows(d1, sql: str, params=None) -> list[dict]:
    import json

    rows = d1.query(sql, params or [])
    for r in rows:
        for k in ("detail", "positions"):
            if isinstance(r.get(k), str):
                r[k] = json.loads(r[k])
    return rows


def run_and_save(d1, settings: Settings, now: pd.Timestamp, cfg: dict | None = None) -> dict:
    """Reconcile, store the summary in D1, and return it (with any *new* mismatches flagged)."""
    import json

    from trader.backtest.data import load_market
    from trader.paper.plan import LOOKBACK, load_config

    cfg = cfg or load_config()
    exp = cfg["experiment"]
    start = pd.Timestamp(exp["start"], tz=NY).tz_convert("UTC")
    data = load_market(crypto=[exp["signal_symbol"]], stocks=exp["symbols"], start=min(start, now) - LOOKBACK)
    summary = reconcile(
        data, cfg, settings,
        equity_rows=_rows(d1, "SELECT ts, equity FROM paper_equity WHERE ts >= ? ORDER BY ts", [start.isoformat()]),
        orders=_rows(d1, "SELECT * FROM paper_orders"),
        decisions=_rows(d1, "SELECT * FROM paper_decisions ORDER BY decided_at"),
        now=now,
    )
    previous = d1.query("SELECT summary FROM paper_reconcile ORDER BY computed_at DESC LIMIT 1")
    seen = set(json.loads(previous[0]["summary"]).get("mismatches", [])) if previous else set()
    summary["new_mismatches"] = [s for s in summary["mismatches"] if s not in seen]
    d1.insert_many("paper_reconcile", ["computed_at", "summary"], [(summary["computed_at"], json.dumps(summary))])
    d1.query("DELETE FROM paper_reconcile WHERE computed_at NOT IN "
             "(SELECT computed_at FROM paper_reconcile ORDER BY computed_at DESC LIMIT ?)", [KEEP_RUNS])
    return summary
