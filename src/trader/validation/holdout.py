"""The locked holdout: the most recent ~2 years of data, kept out of all research.

Research code loads data through `research_market()`, which stops just before
`validation.holdout_start`. The only way to see the holdout is `holdout_market()`,
which the validation pipeline calls once per idea, after every other gate passed,
with committed code, and records in the ledger. A second look is refused: once you've
seen the holdout, it's no longer out-of-sample for that idea.
"""

from __future__ import annotations

import pandas as pd

from trader.backtest.data import MarketData, load_market
from trader.config import ValidationSettings


class HoldoutLocked(Exception):
    pass


def research_end(v: ValidationSettings) -> pd.Timestamp:
    return pd.Timestamp(v.holdout_start, tz="UTC") - pd.Timedelta(seconds=1)


def _load(crypto, stocks, start, end, daily=False, carry=False, spot=(), **kwargs) -> MarketData:
    if carry:   # funding-harvest pairs (backtest/carry.py), plus spot coins for the benchmark
        from trader.backtest.carry import load_carry
        return load_carry([c for c in crypto if c not in spot], start=start, end=end, spot=spot)
    if daily:   # broad daily-bar stock universe (data/daily.py)
        from trader.data.daily import load_daily
        return load_daily(list(stocks) + list(crypto), start=start, end=end)
    return load_market(crypto=crypto, stocks=stocks, start=start, end=end, **kwargs)


def research_market(v: ValidationSettings, crypto=(), stocks=(), **kwargs) -> MarketData:
    """Data for research and walk-forward: everything before the holdout, nothing after."""
    if "end" in kwargs:
        raise HoldoutLocked("research data always ends at the holdout; don't pass `end`")
    data = _load(crypto, stocks, v.research_start, research_end(v), **kwargs)
    assert data.index.max() < pd.Timestamp(v.holdout_start, tz="UTC"), "holdout leaked into research data"
    return data


def holdout_market(v: ValidationSettings, crypto=(), stocks=(), **kwargs) -> MarketData:
    """Full history including the holdout. Only for the one-time holdout evaluation:
    strategies need the earlier history to warm up their indicators."""
    return _load(crypto, stocks, v.research_start, None, **kwargs)
