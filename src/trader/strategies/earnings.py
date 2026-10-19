"""Post-earnings drift: after a strongly positive earnings surprise, stocks tend to keep
drifting up for weeks, because investors underreact to the news.

No analyst estimates are free, so the market's own reaction measures the surprise: the
stock's return over the first full session after the release, minus SPY's return that day
("earnings announcement return"). Releases come from SEC 8-K filings with item 2.02 and their
exact acceptance time (data/sec.py).

Timing: the reaction session is the first session whose open comes after the filing was
accepted; the reaction is known at that session's close, so the earliest trade is the next
open (target on the reaction day's bar).

Bias warning: the universe is today's companies (SEC's current ticker list), so companies
that disappeared since 2016 are missing: survivorship bias makes results look better.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trader.backtest.data import MarketData
from trader.data.daily import alpaca_symbol
from trader.data.sec import sec_path
from trader.data.store import read
from trader.strategies.base import Strategy

NY = "America/New_York"


def earnings_universe(max_companies: int = 1500) -> pd.DataFrame:
    """The first `max_companies` of SEC's current company list (roughly the largest), as (cik, symbol)."""
    from trader.data.sec import company_tickers
    t = company_tickers().drop_duplicates("cik")
    t = t[t["symbol"].str.fullmatch(r"[A-Z][A-Z.\-]{0,6}")]
    return t.head(max_companies).reset_index(drop=True)


def reaction_rows(accepted: pd.Series, bar_days: pd.DatetimeIndex) -> np.ndarray:
    """Row of the reaction session for each acceptance time: the first session whose 09:30 open
    comes after the filing was accepted."""
    local = accepted.dt.tz_convert(NY)
    day = pd.DatetimeIndex(local.dt.tz_localize(None).dt.normalize())
    before_open = (local.dt.hour * 60 + local.dt.minute) < 9 * 60 + 30
    first_day = np.where(before_open, day, day + pd.Timedelta(days=1))
    return bar_days.searchsorted(pd.DatetimeIndex(first_day))


class EarningsDrift(Strategy):
    name = "earnings_drift"
    bars = "daily"
    default_grid = {"threshold": [0.04, 0.08], "hold_days": [20, 60]}
    POSITION = 0.05
    MAX_POSITIONS = 20

    def __init__(self, symbols, threshold: float = 0.04, hold_days: int = 20, events: pd.DataFrame | None = None):
        super().__init__(symbols, threshold=threshold, hold_days=hold_days)
        self._events = events

    @classmethod
    def default_universe(cls) -> list[str]:
        from trader.data.daily import daily_path
        ev = read(sec_path("earnings_8k.parquet"))
        bars = read(daily_path())
        have = set(bars["symbol"].unique()) if not bars.empty else set()
        return sorted(set(ev["symbol"].map(alpaca_symbol)) & have)

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        events = self._events if self._events is not None else read(sec_path("earnings_8k.parquet"))
        events = events.assign(symbol=events["symbol"].map(alpaca_symbol))
        tradable = [s for s in self.symbols if s in data.close.columns and s != "SPY"]
        # a daily bar carries information up to that day's close, so releases accepted before the
        # last bar's close are known when deciding on it (bar timestamps are the day's midnight)
        known_until = data.index[-1] + pd.Timedelta(days=1)
        events = events[events["symbol"].isin(tradable) & (events["accepted"] < known_until)].sort_values("accepted")
        bar_days = pd.DatetimeIndex(data.index.tz_convert(NY).tz_localize(None).normalize())
        rows = reaction_rows(events["accepted"], bar_days)
        bar_ret = (1 + data.gap) * (1 + data.intra) - 1           # close-to-close return per bar
        market = bar_ret["SPY"] if "SPY" in bar_ret else pd.Series(0.0, index=data.index)
        col = {s: i for i, s in enumerate(data.close.columns)}
        w = np.zeros(data.close.shape)
        open_until = []
        for symbol, row in zip(events["symbol"], rows):
            if row >= len(bar_days) or not data.tradable.iat[row, col[symbol]]:
                continue
            excess = bar_ret.iat[row, col[symbol]] - market.iat[row]
            if excess <= self.params["threshold"]:
                continue
            open_until = [r for r in open_until if r > row]
            if len(open_until) >= self.MAX_POSITIONS:
                continue
            end = min(row + self.params["hold_days"], len(bar_days))
            w[row:end, col[symbol]] = self.POSITION       # target on the reaction day's bar -> filled next open
            open_until.append(end)
        return pd.DataFrame(w, index=data.index, columns=data.close.columns)[tradable]
