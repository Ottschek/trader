"""Overnight hold ("night trading beats day trading"): own the market overnight, sit out the day.

Documented since the 1990s (e.g. Cliff, Cooper & Gulen 2008): most US stock-index gains came
from close to next open, while open-to-close returns were near zero. Idea from an Instagram
reel the user sent; design fixed on 2026-10-19, before any test.

With hourly bars a decision at a bar's close fills at the next bar's open, so "buy near the
close" is the decision on the 14:30 bar (filled at the 15:30 bar's open, half an hour before the
close) and "sell after the open" is the decision on the first `exit_after_bars` bar(s) of the
next session (exit_after_bars=1 -> sold at 10:30, 2 -> at 11:30). Equal weights across the symbols.
Days that close early (13:00) have no 14:30 bar, so the strategy sits those nights out.
Trading twice a day, costs matter: 4 bps each way per trade at the alpaca_stocks profile.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trader.backtest.data import MarketData
from trader.strategies.base import Strategy

NY = "America/New_York"


class OvernightHold(Strategy):
    name = "overnight_hold"
    default_grid = {"exit_after_bars": [1, 2]}

    def __init__(self, symbols, exit_after_bars: int = 1):
        super().__init__(symbols, exit_after_bars=exit_after_bars)

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        local = data.index.tz_convert(NY)
        minutes = local.hour * 60 + local.minute
        day = local.normalize()
        bar_of_day = pd.Series(np.arange(len(local)), index=local).groupby(day).cumcount().to_numpy()   # 0 = first bar
        enter = minutes == 14 * 60 + 30
        exit_ = bar_of_day == self.params["exit_after_bars"] - 1
        w = np.full(len(local), np.nan)
        w[0] = 0.0
        w[exit_] = 0.0
        w[enter] = 1.0 / len(self.symbols)
        col = pd.Series(w, index=data.index).ffill()
        return pd.DataFrame({s: col for s in self.symbols}, index=data.index)
