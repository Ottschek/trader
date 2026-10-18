"""M4 baselines: the three classic strategy families with real research behind them.
Equal weight per symbol; long-only (spot crypto can't be shorted on Alpaca)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from trader.backtest.data import MarketData
from trader.strategies.base import Strategy
from trader.strategies.helpers import has_history, hold_after, is_crypto, once_a_day, value_ago

DAY = pd.Timedelta(days=1)


class TsMomentum(Strategy):
    """Trend following (time-series momentum): hold an asset while its return over the
    past `lookback_days` is positive. Re-decided once a day."""
    name = "ts_momentum"
    default_grid = {"lookback_days": [7, 30, 90]}

    def __init__(self, symbols, lookback_days: int = 30):
        super().__init__(symbols, lookback_days=lookback_days)

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        lookback = self.params["lookback_days"] * DAY
        w = 1.0 / len(self.symbols)
        out = {}
        for s in self.symbols:
            c = data.close[s].dropna()
            up = (c / value_ago(c, lookback) - 1 > 0) & has_history(c, lookback)
            out[s] = once_a_day(up.astype(float), stock=not is_crypto(s)) * w
        return pd.DataFrame(out).reindex(data.index)


class DipBuyer(Strategy):
    """Short-term mean reversion: in an uptrend (price above its 100-day average), buy a
    sharp 24-hour drop of more than `drop_sigma` times normal daily volatility, and hold
    for `hold_hours`."""
    name = "dip_buyer"
    default_grid = {"drop_sigma": [2.0, 3.0], "hold_hours": [24, 72]}

    def __init__(self, symbols, drop_sigma: float = 2.0, hold_hours: int = 24):
        super().__init__(symbols, drop_sigma=drop_sigma, hold_hours=hold_hours)

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        w = 1.0 / len(self.symbols)
        out = {}
        for s in self.symbols:
            c = data.close[s].dropna()
            ret_24h = c / value_ago(c, DAY) - 1
            bars_per_day = 24 if is_crypto(s) else 7
            daily_vol = np.log(c).diff().rolling("30D").std() * np.sqrt(bars_per_day)
            uptrend = c > c.rolling("100D").mean()
            entry = (ret_24h < -self.params["drop_sigma"] * daily_vol) & uptrend & has_history(c, 100 * DAY)
            out[s] = hold_after(entry, pd.Timedelta(hours=self.params["hold_hours"])).astype(float) * w
        return pd.DataFrame(out).reindex(data.index)


class RegimeFilter(Strategy):
    """The classic market-timing rule: hold while the price is above its `sma_days`-day
    moving average, sit in cash below it. Re-decided once a day."""
    name = "regime_filter"
    default_grid = {"sma_days": [100, 200]}

    def __init__(self, symbols, sma_days: int = 200):
        super().__init__(symbols, sma_days=sma_days)

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        window = self.params["sma_days"] * DAY
        w = 1.0 / len(self.symbols)
        out = {}
        for s in self.symbols:
            c = data.close[s].dropna()
            above = (c > c.rolling(window).mean()) & has_history(c, window)
            out[s] = once_a_day(above.astype(float), stock=not is_crypto(s)) * w
        return pd.DataFrame(out).reindex(data.index)
