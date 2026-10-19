"""Reality vs model: how the forward experiments compare with what their backtests predicted.

First run planned for 2026-11-19 (one month in); run it again any time. Reads D1 (written by the
Worker and the collector) and writes reports/review-YYYY-MM-DD.md.

The yardsticks were fixed on 2026-10-19, before any forward data existed:
  Paper weekend gap
    - every weekend decision matches the backtest's (no mismatches)
    - tracking difference (live return - backtest return) within +-1 percentage point
    - average fill vs open within 2x the cost model's slippage + half spread
  Carry simulation (judged after the one-time opening costs, i.e. from its first hourly row)
    - simulated annual return at least half of the model's over the same hours
    - and at least half of the backtest's out-of-sample CAGR (funding_harvest in the ledger)
    - no margin rebalance needed more than once a week on average
  News word list: reported only; fewer than 100 scored signals is too early to judge.

Run:  .venv/bin/python scripts/review.py              # print and save
      .venv/bin/python scripts/review.py --telegram   # also send the summary
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MIN_NEWS_SIGNALS = 100


def annualize(ret: float, days: float) -> float | None:
    return (1 + ret) ** (365 / days) - 1 if days >= 1 else None


def review_paper(rec: dict | None, cost_bps: float) -> dict:
    if not rec:
        return {"status": "no data", "lines": ["No reconciliation yet."]}
    sessions = len(rec["trades"])
    traded = sum(1 for t in rec["trades"] if t.get("live_decision") == "entry")
    track, slip = rec.get("tracking_difference"), rec.get("avg_slippage_bps")
    checks = {"decisions match": not rec["mismatches"],
              "tracking within 1 pp": track is None or abs(track) <= 0.01,
              f"avg fill within {2 * cost_bps:.0f} bps": slip is None or slip <= 2 * cost_bps}
    lines = [f"{sessions} weekend session(s) checked, {traded} traded",
             f"Live {fmt(rec.get('actual_return'))} vs backtest {fmt(rec.get('expected_return'))} "
             f"(tracking {fmt(track)})",
             f"Average fill vs open: {'n/a' if slip is None else f'{slip} bps'} (cost model {cost_bps:.1f} bps)"]
    lines += [f"{'PASS' if ok else 'FAIL'}  {name}" for name, ok in checks.items()]
    status = "too early" if traded == 0 else ("matches" if all(checks.values()) else "differs")
    return {"status": status, "checks": checks, "lines": lines}


def review_carry(rows: list[dict], backtest_cagr: float | None) -> dict:
    if len(rows) < 2:
        return {"status": "no data", "lines": ["Fewer than two hourly rows."]}
    first, last = rows[0], rows[-1]
    days = (pd.Timestamp(last["ts"]) - pd.Timestamp(first["ts"])) / pd.Timedelta(days=1)
    sim = last["equity"] / first["equity"] - 1
    model = last["model_equity"] / first["model_equity"] - 1
    sim_a, model_a = annualize(sim, days), annualize(model, days)
    weeks = max(days / 7, 1)
    checks = {"at least half the model": sim_a is not None and model_a is not None and (model_a <= 0 or sim_a >= 0.5 * model_a),
              "at least half the backtest": sim_a is not None and backtest_cagr is not None and sim_a >= 0.5 * backtest_cagr,
              "at most one rebalance a week": last["rebalances"] / weeks <= 1}
    lines = [f"{days:.1f} days since the pairs were opened (opening costs excluded)",
             f"Simulated {fmt(sim)} ({fmt(sim_a)} a year) vs model {fmt(model)} ({fmt(model_a)} a year); "
             f"backtest out-of-sample {fmt(backtest_cagr)} a year",
             f"Funding collected ${last['funding_cum']:,.0f}, fees paid ${last['fees_cum']:,.0f} (incl. opening), "
             f"{last['rebalances']} rebalance(s), peak margin use {last['max_margin_use']:.0%}"]
    lines += [f"{'PASS' if ok else 'FAIL'}  {name}" for name, ok in checks.items()]
    status = "too early" if days < 7 else ("matches" if all(checks.values()) else "differs")
    return {"status": status, "checks": checks, "lines": lines, "sim_annual": sim_a, "model_annual": model_a}


def review_news(signals: list[dict]) -> dict:
    lex = [s for s in signals if s.get("source") == "lexicon"]
    lines = [f"{len(lex)} word-list signal(s)"]
    for h in (1, 5):
        net = np.array([s[f"net_{h}d"] for s in lex if s.get(f"net_{h}d") is not None])
        if len(net):
            t = net.mean() / net.std(ddof=1) * np.sqrt(len(net)) if len(net) > 2 and net.std(ddof=1) > 0 else None
            lines.append(f"{h}-day net excess: avg {fmt(net.mean(), 2)} over {len(net)} scored, "
                         f"{(net > 0).mean():.0%} winners" + (f", t = {t:.2f}" if t is not None else ""))
    scored = sum(1 for s in lex if s.get("net_5d") is not None)
    return {"status": "too early" if scored < MIN_NEWS_SIGNALS else "enough data", "lines": lines}


def fmt(x, d: int = 2) -> str:
    return "n/a" if x is None else f"{x * 100:+.{d}f}%"


def build(d1, settings, now: pd.Timestamp) -> dict:
    from trader.ledger import ledger

    rec = d1.query("SELECT summary FROM paper_reconcile ORDER BY computed_at DESC LIMIT 1")
    carry = d1.query("SELECT * FROM carry_sim ORDER BY ts")
    signals = d1.query("SELECT * FROM news_signals")
    harvest = [e for e in ledger.entries() if e["strategy"] == "funding_harvest"]
    cagr = harvest[-1]["oos"]["cagr"] if harvest else None
    cost = settings.costs["alpaca_stocks"]
    return {"now": now, "paper": review_paper(json.loads(rec[0]["summary"]) if rec else None,
                                              cost.slippage_bps + cost.spread_bps / 2),
            "carry": review_carry(carry, cagr), "news": review_news(signals)}


def to_markdown(r: dict) -> str:
    out = [f"# Reality vs model · {r['now']:%Y-%m-%d}", ""]
    for key, title in (("paper", "Paper weekend gap"), ("carry", "Carry simulation"), ("news", "News word list")):
        out += [f"## {title}: {r[key]['status']}", ""] + [f"- {line}" for line in r[key]["lines"]] + [""]
    return "\n".join(out)


def main() -> None:
    from trader.config import load_settings
    from trader.monitor.d1 import D1

    p = argparse.ArgumentParser()
    p.add_argument("--telegram", action="store_true")
    args = p.parse_args()
    settings = load_settings()
    now = pd.Timestamp.now(tz="UTC")
    r = build(D1(settings.cloud.d1_database_id), settings, now)
    md = to_markdown(r)
    path = ROOT / "reports" / f"review-{now:%Y-%m-%d}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(md)
    print(md)
    print(f"Saved {path}")
    if args.telegram:
        from trader.monitor import telegram
        telegram.send("🔎 <b>Reality vs model</b>\n" + "\n".join(
            f"{t}: {r[k]['status']}" for k, t in (("paper", "Paper weekend gap"), ("carry", "Carry simulation"),
                                                  ("news", "News word list"))))


if __name__ == "__main__":
    main()
