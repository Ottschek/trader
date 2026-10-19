"""Short-term reversal: buy last week's biggest losers, short the biggest winners.

Documented since Jegadeesh (1990) and Lehmann (1990): over a week or a month, stocks that fell
most tend to bounce and those that rose most tend to give some back (overreaction, liquidity
provision). Most studies find the effect is strongest in small, illiquid stocks and largely
eaten by trading costs. Idea from an Instagram reel the user sent; design fixed on 2026-10-19,
before any test:

  - universe on each decision day: stocks with a 20-day average dollar volume >= MIN_DOLLAR_VOLUME
    and a price >= MIN_PRICE (liquid, easy to borrow), as known at that day's close
  - every Friday close: rank them by their return over the last `lookback_days` trading days;
    long the `positions` worst, short the `positions` best, equal weight, 50% long / 50% short
    (gross 1x); filled at Monday's open, held one week. No Friday bar (holiday) -> hold another week
  - costs: the large-cap profile (7 bps one way); borrow fees are not modeled (~0.3% a year for
    liquid names)

Bias warning: the daily universe is built from today's SEC company list plus past insider/earnings
filers, so some companies that disappeared since 2016 are missing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trader.backtest.data import MarketData
from trader.data.store import read
from trader.strategies.base import Strategy

NY = "America/New_York"


class WeeklyReversal(Strategy):
    name = "weekly_reversal"
    bars = "daily"
    allow_short = True
    cost_venue = "alpaca_largecaps"
    default_grid = {"lookback_days": [5, 20], "positions": [20, 50]}
    MIN_DOLLAR_VOLUME = 50e6
    MIN_PRICE = 5.0

    def __init__(self, symbols, lookback_days: int = 5, positions: int = 20):
        super().__init__(symbols, lookback_days=lookback_days, positions=positions)

    @classmethod
    def default_universe(cls) -> list[str]:
        from trader.data.daily import daily_path
        return sorted(read(daily_path())["symbol"].unique())

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        cols = [s for s in self.symbols if s in data.close.columns and s != "SPY"]
        close = data.close[cols]
        raw = data.raw_close[cols]
        tradable = data.tradable[cols]
        adv = (raw * data.volume[cols]).where(tradable).rolling(20, min_periods=15).mean()
        past = close / close.shift(self.params["lookback_days"]) - 1
        eligible = (tradable & (adv >= self.MIN_DOLLAR_VOLUME) & (raw >= self.MIN_PRICE) & past.notna()).to_numpy()
        past_v = past.to_numpy()
        names = np.array(cols)
        n = self.params["positions"]
        fridays = data.index.tz_convert(NY).dayofweek == 4

        w = np.full(close.shape, np.nan)
        w[0] = 0.0
        for row in np.flatnonzero(fridays):
            ok = np.flatnonzero(eligible[row])
            target = np.zeros(len(cols))
            if len(ok) >= 2 * n:
                order = ok[np.lexsort((names[ok], past_v[row, ok]))]   # by return, ties by symbol
                target[order[:n]] = 0.5 / n                             # losers: long
                target[order[-n:]] = -0.5 / n                           # winners: short
            w[row] = target
        out = pd.DataFrame(w, index=data.index, columns=cols).ffill()
        return out
