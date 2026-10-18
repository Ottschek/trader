"""Reference strategies: the benchmark, a simple honest rule, and a deliberate cheat
(used to prove the lookahead check catches cheating)."""

from __future__ import annotations

import pandas as pd

from trader.backtest.data import MarketData
from trader.strategies.base import Strategy


class BuyAndHold(Strategy):
    """Equal weight in every symbol from the first bar, never rebalanced."""
    name = "buy_and_hold"

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        w = 1.0 / len(self.symbols)
        return pd.DataFrame(w, index=data.index, columns=self.symbols)


class SmaCross(Strategy):
    """Hold a symbol while its fast moving average is above the slow one (trend following).
    Equal weight per symbol; averages are taken over the symbol's own bars."""
    name = "sma_cross"

    def __init__(self, symbols, fast: int = 24, slow: int = 24 * 7):
        if fast >= slow:
            raise ValueError("fast window must be shorter than slow window")
        super().__init__(symbols, fast=fast, slow=slow)

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        fast, slow = self.params["fast"], self.params["slow"]
        w = 1.0 / len(self.symbols)
        out = {}
        for s in self.symbols:
            close = data.close[s].dropna()
            signal = close.rolling(fast).mean() > close.rolling(slow).mean()
            out[s] = (signal.astype(float) * w).where(close.rolling(slow).count() == slow, 0.0)
        return pd.DataFrame(out).reindex(data.index)


class PeekAhead(Strategy):
    """CHEATS on purpose: buys when the *next* bar closes higher. A backtest would show
    impossible returns; the lookahead check must flag it."""
    name = "peek_ahead"

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        w = 1.0 / len(self.symbols)
        out = {}
        for s in self.symbols:
            close = data.close[s].dropna()
            out[s] = (close.shift(-1) > close).astype(float) * w
        return pd.DataFrame(out).reindex(data.index)
