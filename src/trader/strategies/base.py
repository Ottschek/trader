"""Every strategy has the same shape: market data in, target weights out.

`target_weights(data)` returns a DataFrame indexed like `data.index` with one column per
symbol it trades. A row is the portfolio the strategy wants, decided after that bar
closed; the engine fills it at the next open. NaN means "no change". Weights are
fractions of equity (0.5 = half the account), long-only unless the backtest allows shorts.

A strategy must only use data up to each bar. The lookahead check enforces this by
re-running the strategy on truncated history and comparing its past decisions.
"""

from __future__ import annotations

import pandas as pd

from trader.backtest.data import MarketData


class Strategy:
    name = "strategy"
    default_grid: dict[str, list] = {}   # parameter values the validation walk-forward tries
    signal_symbols: list[str] = []       # extra symbols it reads but never trades (e.g. BTC for crypto stocks)
    needs_funding = False                # needs Hyperliquid funding rates in the data
    bars = "hourly"                      # "daily" -> validated on the broad daily-bar stock universe
    cost_venue: str | None = None        # every trade pays this venue's costs (strategies that only trade liquid names)
    allow_short = False                  # negative weights allowed (validated with shorts in every backtest)

    def __init__(self, symbols, **params):
        self.symbols = list(symbols)
        self.params = params

    @classmethod
    def default_universe(cls) -> list[str] | None:
        """Symbols to validate on when none are given (broad-universe strategies override this)."""
        return None

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        raise NotImplementedError

    def label(self) -> str:
        params = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name}({params})" if params else self.name

    def __repr__(self) -> str:
        return f"<{self.label()} on {', '.join(self.symbols)}>"
