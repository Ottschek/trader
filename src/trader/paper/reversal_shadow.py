"""weekly_reversal forward SHADOW: a simulation with fake money, no orders, frozen rules.

The strategy was rejected in research (ledger 19762903b577: OOS Sharpe 0.78, drawdown -29.3%,
timing p = 0.004). This collects new, forward evidence from START on, with exactly the committed
strategy (strategies/reversal.py) and the parameters its walk-forward picked last (PARAMS).

Every weekend (.github/workflows/weekly_shadow.yml): download a fresh daily-bar window for the
whole universe (one download, so all prices share the same split/dividend adjustments), run the
strategy, and simulate it with the backtest engine and large-cap costs. Decisions before START are
zeroed, so the simulation starts flat and nothing from the locked holdout period is evaluated.

Stored in D1: reversal_weeks (one row per Friday decision) and reversal_equity (daily equity).
"""

from __future__ import annotations

import json

import pandas as pd

from trader.backtest.engine import run_backtest
from trader.backtest.runner import cost_bps
from trader.strategies.reversal import WeeklyReversal

START = pd.Timestamp("2026-10-19", tz="UTC")
PARAMS = {"lookback_days": 20, "positions": 50}     # the walk-forward's pick in its latest fold
LEDGER_ID = "19762903b577"
BACKTEST_CAGR = 0.1741                              # out-of-sample, 2018-2024
WINDOW_DAYS = 125                                   # calendar days of history before START (lookback + ADV)
NY = "America/New_York"


def window_start() -> str:
    return (START - pd.Timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")


def simulate(data, settings) -> dict:
    """Run the frozen strategy on `data` (daily MarketData covering the window) from START on."""
    strat = WeeklyReversal(list(data.symbols), **PARAMS)
    targets = strat.target_weights(data)
    days = targets.index.tz_convert(NY)
    decisions = [i for i in range(len(targets)) if targets.index[i] >= START and days[i].dayofweek == 4]
    first = targets.index[decisions[0]] if decisions else targets.index[-1] + pd.Timedelta(days=1)
    targets.loc[targets.index < first] = 0.0          # flat until the first Friday decision on/after START
    costs = cost_bps(data, settings, strat.cost_venue)
    res = run_backtest(data, targets, costs, name=strat.label(), allow_short=True)
    equity = res.equity[res.equity.index >= START]
    equity = equity / res.equity[res.equity.index < START].iloc[-1] if (res.equity.index < START).any() else equity

    past = data.close / data.close.shift(PARAMS["lookback_days"]) - 1
    weeks = []
    for k, row in enumerate(decisions):
        t = targets.iloc[row]
        end_row = decisions[k + 1] if k + 1 < len(decisions) else len(targets) - 1
        start_eq, end_eq = res.equity.iloc[row], res.equity.iloc[end_row]
        weeks.append({
            "decided": targets.index[row].tz_convert(NY).strftime("%Y-%m-%d"),
            "longs": json.dumps([[s, round(float(past.at[targets.index[row], s]), 4)] for s in t.index[t > 0]]),
            "shorts": json.dumps([[s, round(float(past.at[targets.index[row], s]), 4)] for s in t.index[t < 0]]),
            "ret": float(end_eq / start_eq - 1) if end_row > row else None,
            "complete": int(k + 1 < len(decisions)),
            "cost": float(res.costs.iloc[row + 1]) if row + 1 < len(res.costs) else None,
        })
    return {"equity": equity, "weeks": weeks, "data_end": data.index[-1]}


def save_to_d1(d1, result: dict, now: pd.Timestamp) -> int:
    eq = result["equity"]
    n = d1.insert_many("reversal_equity", ["ts", "equity"], [(ts.isoformat(), float(v)) for ts, v in eq.items()])
    cols = ["decided", "longs", "shorts", "ret", "complete", "cost", "computed_at"]
    n += d1.insert_many("reversal_weeks", cols, [tuple(w[c] for c in cols[:-1]) + (now.isoformat(),) for w in result["weeks"]])
    return n
