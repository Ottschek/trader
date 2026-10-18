"""The backtest engine: target weights in, equity curve out.

For each bar t (see data.py for the timing model):
  1. gap:   positions held since the last close earn the close->open return
  2. trade: the latest targets that were *ready* by this bar's open fill now, for every
            asset that has a bar (a stock target set on Friday evening fills Monday's open).
            A decision made at row k is ready when every bar it could see has closed:
            index[k] + BAR (bars are at most 1 hour). With one asset class this is simply
            "decided at t-1, filled at t". With crypto (bars at :00) and stocks (regular
            session bars at :30) mixed, it stops a decision that saw the 13:00-14:00 crypto
            bar from filling at the 13:30 stock open.
            Each trade pays the venue's one-way cost on the traded weight.
  3. intra: the new positions earn the open->close return
Positions drift with prices between trades. An asset is traded only when its target
changes; the rest of the portfolio is left alone. Uninvested weight is cash at 0%.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trader.backtest.data import MarketData

BAR = np.timedelta64(1, "h")  # longest bar we use; a decision is ready this long after its row starts


@dataclass
class BacktestResult:
    name: str
    equity: pd.Series      # account value at each close, starting at 1.0
    returns: pd.Series     # net return per bar
    weights: pd.DataFrame  # actual weights at each close (after drift)
    turnover: pd.Series    # sum of |traded weight| per bar
    costs: pd.Series       # trading costs per bar, as a fraction of equity
    trades: int            # number of (asset, bar) fills
    traded: pd.DataFrame | None = None   # traded weight per asset and bar (signed), for capacity estimates


class BacktestError(ValueError):
    pass


def run_backtest(data: MarketData, targets: pd.DataFrame, cost_bps: dict[str, float],
                 name: str = "strategy", allow_short: bool = False, max_gross: float = 1.0) -> BacktestResult:
    """`targets`: weights per bar and symbol, decided at that bar's close. NaN = no opinion
    (keep the previous target). `cost_bps`: one-way cost per symbol in basis points."""
    unknown = set(targets.columns) - set(data.symbols)
    if unknown:
        raise BacktestError(f"targets for symbols not in the data: {sorted(unknown)}")
    tgt = targets.reindex(index=data.index, columns=data.symbols).ffill()
    values = tgt.to_numpy(dtype=float)
    if not allow_short and np.nanmin(values, initial=0) < 0:
        raise BacktestError("negative target weights but allow_short=False")
    gross = np.nansum(np.abs(values), axis=1)
    if (gross > max_gross + 1e-9).any():
        when = data.index[np.argmax(gross > max_gross + 1e-9)]
        raise BacktestError(f"targets use {gross.max():.2f}x gross exposure (limit {max_gross}x), first at {when}")

    times = data.index.tz_convert("UTC").tz_localize(None).to_numpy().astype("datetime64[ns]")
    # for each row, the last decision row whose information was complete by this row's open
    ready_row = np.searchsorted(times + BAR, times, side="right") - 1
    gap, intra = data.gap.to_numpy(), data.intra.to_numpy()
    tradable = data.tradable.to_numpy()
    cost = np.array([cost_bps[s] / 10_000 for s in data.symbols])
    n, m = gap.shape

    w = np.zeros(m)                 # current weights
    executed = np.zeros(m)          # last target we traded to
    equity = np.empty(n)
    weights = np.empty((n, m))
    turnover = np.zeros(n)
    costs = np.zeros(n)
    traded = np.zeros((n, m))
    trades = 0
    eq = 1.0

    for t in range(n):
        # 1. gap: old positions earn close -> open
        g = 1.0 + w @ gap[t]
        w = w * (1.0 + gap[t]) / g
        eq *= g
        # 2. trade at the open toward the targets decided at the previous close
        k = ready_row[t]
        if k >= 0:
            desired = np.nan_to_num(values[k], nan=0.0)
            change = tradable[t] & ~np.isclose(desired, executed, atol=1e-12)
            if change.any():
                dw = desired[change] - w[change]
                traded[t, change] = dw
                c = float(np.abs(dw) @ cost[change])
                w[change] = desired[change]
                executed[change] = desired[change]
                turnover[t] = float(np.abs(dw).sum())
                costs[t] = c
                trades += int(change.sum())
                eq *= 1.0 - c
        # 3. intra: new positions earn open -> close
        h = 1.0 + w @ intra[t]
        w = w * (1.0 + intra[t]) / h
        eq *= h
        equity[t] = eq
        weights[t] = w

    eq_s = pd.Series(equity, index=data.index, name=name)
    return BacktestResult(
        name=name,
        equity=eq_s,
        returns=eq_s.pct_change().fillna(eq_s.iloc[0] - 1.0),
        weights=pd.DataFrame(weights, index=data.index, columns=data.symbols),
        turnover=pd.Series(turnover, index=data.index),
        costs=pd.Series(costs, index=data.index),
        trades=trades,
        traded=pd.DataFrame(traded, index=data.index, columns=data.symbols),
    )
