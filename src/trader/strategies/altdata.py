"""Ideas built on the alternative data collectors (data/alt.py). Both designs were fixed
before any test. Data only counts once it was actually published:
  - stablecoin supply dated day d: known 1 day later (DefiLlama's daily point)
  - Wikipedia pageviews for day d: known 2 days later (the API lags about a day)
"""

from __future__ import annotations

import pandas as pd

from trader.backtest.data import MarketData
from trader.data.alt import alt_path
from trader.data.store import read
from trader.strategies.base import Strategy

STABLE_DELAY = pd.Timedelta(days=1)
VIEWS_DELAY = pd.Timedelta(days=2)


def last_true_before(signal: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """For each time in `index`: the most recent time (<= it) at which `signal` (indexed by the
    time it became known) was True. NaT if none yet."""
    when = pd.Series(pd.NaT, index=signal.index, dtype=f"datetime64[ns, {index.tz}]")
    hits = signal.fillna(False).to_numpy(dtype=bool)
    when[hits] = signal.index[hits].tz_convert(index.tz)
    out = when.ffill().reindex(index, method="ffill")
    return pd.Series(out.to_numpy(), index=index, dtype=f"datetime64[ns, {index.tz}]")


class StablecoinPrinter(Strategy):
    """Fresh stablecoin supply is dry powder waiting to buy crypto. When total USDT + USDC supply
    grew more than `threshold` over 7 days, hold BTC and ETH for `hold_days` after the last such day."""
    name = "stablecoin_printer"
    default_grid = {"threshold": [0.01, 0.02], "hold_days": [7, 14]}

    def __init__(self, symbols, threshold: float = 0.01, hold_days: int = 7, supply: pd.Series | None = None):
        super().__init__(symbols, threshold=threshold, hold_days=hold_days)
        self._supply = supply

    def total_supply(self) -> pd.Series:
        if self._supply is not None:
            return self._supply
        s = read(alt_path("stablecoins"))
        return s.pivot_table(index="ts", columns="coin", values="circulating_usd").sum(axis=1).sort_index()

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        supply = self.total_supply()
        growth = supply / supply.shift(7) - 1
        signal = (growth > self.params["threshold"])
        signal.index = signal.index + STABLE_DELAY                 # index = when it became known
        last = last_true_before(signal, data.index)
        held = (pd.Series(data.index, index=data.index) - last) < pd.Timedelta(days=self.params["hold_days"])
        w = held.fillna(False).astype(float) / len(self.symbols)
        return pd.DataFrame({s: w for s in self.symbols}, index=data.index)


class AttentionFilter(Strategy):
    """Hype peaks mark tops: hold the coin basket, but move a coin to cash for `cash_days` after its
    Wikipedia attention (7-day average views) spikes above `spike` x its 90-day average."""
    name = "attention_filter"
    default_grid = {"spike": [2.0, 3.0], "cash_days": [7, 21]}

    def __init__(self, symbols, spike: float = 2.0, cash_days: int = 7, views: pd.DataFrame | None = None):
        super().__init__(symbols, spike=spike, cash_days=cash_days)
        self._views = views

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        views = self._views if self._views is not None else read(alt_path("pageviews"))
        w = 1.0 / len(self.symbols)
        out = {}
        rows = pd.Series(data.index, index=data.index)
        for s in self.symbols:
            v = views[views["symbol"] == s].set_index("ts")["views"].sort_index()
            tradable = data.close[s].notna()
            if v.empty:
                out[s] = tradable.astype(float) * w
                continue
            ratio = v.rolling(7).mean() / v.rolling(90, min_periods=60).mean()
            spike = ratio > self.params["spike"]
            spike.index = spike.index + VIEWS_DELAY
            last = last_true_before(spike, data.index)
            in_cash = (rows - last) < pd.Timedelta(days=self.params["cash_days"])
            out[s] = (~in_cash.fillna(False) & tradable).astype(float) * w
        return pd.DataFrame(out, index=data.index)
