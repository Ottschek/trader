"""Insider clusters: several company insiders buying their own stock in the same week.

Insiders sell for many reasons (taxes, houses, diversification) but buy with their own
money for one: they think the stock is cheap. A *cluster* (several different insiders
within days) is a stronger signal than one purchase. Data: SEC Form 4 open-market
purchases (data/sec.py).

Timing: a purchase becomes public when its Form 4 is filed, which can be after the close,
so the earliest trade is the next day's open: the target is set on the filing date's bar
and the engine fills it at the following open.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trader.backtest.data import MarketData
from trader.data.daily import alpaca_symbol
from trader.data.sec import sec_path
from trader.data.store import read
from trader.strategies.base import Strategy


def find_clusters(events: pd.DataFrame, min_insiders: int, window_days: int, min_value: float,
                  cooldown_days: int) -> pd.DataFrame:
    """(symbol, signal_date) where purchases by >= min_insiders distinct insiders, worth >= min_value
    in total, were filed within `window_days`. One signal per symbol per `cooldown_days`."""
    out = []
    window, cooldown = pd.Timedelta(days=window_days), pd.Timedelta(days=cooldown_days)
    for symbol, ev in events.sort_values("filing_date").groupby("symbol"):
        dates = ev["filing_date"].to_numpy()
        owners, values = ev["owner_cik"].to_numpy(), ev["value"].to_numpy()
        last_signal = None
        for i, d in enumerate(dates):
            if last_signal is not None and d - last_signal < cooldown:
                continue
            recent = (dates > d - window) & (dates <= d)
            if len(set(owners[recent])) >= min_insiders and values[recent].sum() >= min_value:
                out.append((symbol, pd.Timestamp(d)))
                last_signal = d
    return pd.DataFrame(out, columns=["symbol", "signal_date"])


def load_events() -> pd.DataFrame:
    ev = read(sec_path("insider_purchases.parquet"))
    if ev.empty:
        raise ValueError("no insider data: run scripts/sec_data.py first")
    ev = ev.copy()
    ev["symbol"] = ev["symbol"].map(alpaca_symbol)
    return ev


class InsiderCluster(Strategy):
    name = "insider_cluster"
    bars = "daily"
    default_grid = {"min_insiders": [2, 3], "hold_days": [20, 60]}
    POSITION = 0.05          # 5% of the account per position
    MAX_POSITIONS = 20

    def __init__(self, symbols, min_insiders: int = 3, hold_days: int = 20, window_days: int = 10,
                 min_value: float = 100_000, events: pd.DataFrame | None = None):
        super().__init__(symbols, min_insiders=min_insiders, hold_days=hold_days)
        self.window_days, self.min_value = window_days, min_value
        self._events = events

    @classmethod
    def default_universe(cls) -> list[str]:
        """Every stock with insider purchases that also has daily price data, plus SPY (benchmark)."""
        from trader.data.daily import daily_path
        bars = read(daily_path())
        have = set(bars["symbol"].unique()) if not bars.empty else set()
        return sorted(set(load_events()["symbol"].unique()) & have)

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        events = self._events if self._events is not None else load_events()
        tradable = [s for s in self.symbols if s in data.close.columns]
        last_day = data.index[-1].tz_convert("America/New_York").date()
        ev = events[events["symbol"].isin(tradable) & (events["filing_date"].dt.date <= last_day)]
        signals = find_clusters(ev, self.params["min_insiders"], self.window_days, self.min_value,
                                cooldown_days=int(self.params["hold_days"] * 1.5))
        days = pd.DatetimeIndex(data.index.tz_convert("America/New_York").date)   # the trading date of each bar
        col = {s: i for i, s in enumerate(data.close.columns)}
        w = np.zeros(data.close.shape)
        open_until = []                                   # exit row of each open position
        for sig in signals.sort_values(["signal_date", "symbol"], kind="stable").itertuples(index=False):
            row = int(days.searchsorted(pd.Timestamp(sig.signal_date)))   # bar of the filing date (or next trading day)
            if row >= len(days):
                continue                                  # filed after the data ends
            open_until = [r for r in open_until if r > row]
            if len(open_until) >= self.MAX_POSITIONS:
                continue                                  # portfolio full: skip, as a real trader would
            end = min(row + self.params["hold_days"], len(days))
            w[row:end, col[sig.symbol]] = self.position_size(data, col[sig.symbol], row)
            open_until.append(end)
        return pd.DataFrame(w, index=data.index, columns=data.close.columns)[tradable]

    def position_size(self, data: MarketData, column: int, row: int) -> float:
        return self.POSITION


class InsiderClusterSized(InsiderCluster):
    """Insider clusters with each position sized to TARGET_VOL daily volatility (past 20 days,
    fixed at entry), capped at the original 5%: the small caps that crashed together in 2020 and
    2022 get proportionally less. Same signals and grid as insider_cluster."""
    name = "insider_cluster_sized"
    TARGET_VOL = 0.02

    def position_size(self, data: MarketData, column: int, row: int) -> float:
        if not hasattr(self, "_vol") or self._vol_data is not data:
            bar_ret = (1 + data.gap) * (1 + data.intra) - 1
            self._vol = bar_ret.where(data.tradable).rolling(20, min_periods=15).std().to_numpy()
            self._vol_data = data
        vol = self._vol[row, column]
        if not np.isfinite(vol) or vol <= 0:
            return 0.0                    # not enough history to size the position: skip it
        return self.POSITION * min(1.0, self.TARGET_VOL / vol)
