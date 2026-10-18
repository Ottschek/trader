"""How much money could a strategy run before its own trades move prices?

Rule of thumb: a trade shouldn't exceed `participation` (1%) of the asset's average daily
dollar volume over the previous 20 days. For each trade, the account size at which it would
hit that limit is participation x ADV / |traded weight|. The strategy's capacity is the 10th
percentile of those (90% of its trades would stay under the limit); the median is shown too.
Dollar volume uses real (unadjusted) prices. This ignores intraday liquidity (e.g. the opening
auction is thinner than the whole day), so treat it as an upper bound.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trader.backtest.data import MarketData
from trader.backtest.engine import BacktestResult


def average_daily_dollar_volume(data: MarketData, days: int = 20) -> pd.DataFrame:
    """Per symbol: mean daily dollar volume over the previous `days` days (known before each day)."""
    price = data.raw_close if data.raw_close is not None else data.close
    daily = (price * data.volume).resample("1D").sum(min_count=1)
    return daily.rolling(days, min_periods=max(5, days // 2)).mean().shift(1)


def estimate_capacity(result: BacktestResult, data: MarketData, participation: float = 0.01) -> dict:
    if result.traded is None:
        raise ValueError("the backtest result has no per-asset trades")
    adv = average_daily_dollar_volume(data)
    caps = []
    traded = result.traded
    for t, row in traded[traded.abs().sum(axis=1) > 0].iterrows():
        day = t.floor("D")
        if day not in adv.index:
            continue
        for symbol, dw in row[row != 0].items():
            a = adv.at[day, symbol]
            if np.isfinite(a) and a > 0:
                caps.append(participation * a / abs(dw))
    if not caps:
        return {"participation": participation, "trades": 0, "p10_usd": None, "median_usd": None}
    caps = np.array(caps)
    return {"participation": participation, "trades": int(len(caps)),
            "p10_usd": float(np.percentile(caps, 10)), "median_usd": float(np.median(caps))}
