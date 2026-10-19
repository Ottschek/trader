"""Strategies invented by the "brain" (scripts/brain.py): the free AI writes a rule in this small,
fixed language; it never writes code. Each idea lives in config/brain/ideas.jsonl.

A rule holds an equal share of each of its assets while ALL its conditions are true:
    {"name": "...", "thesis": "who is forced to trade, and why that moves prices",
     "symbols": ["SPY", "QQQ"], "decide": "daily" | "hourly",
     "conditions": [{"feature": "return", "hours": 120, "op": "<", "value": -0.03}, ...]}
Every feature only looks backwards (the lookahead test checks it like any other strategy).
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from trader.backtest.data import MarketData
from trader.strategies.base import Strategy
from trader.strategies.helpers import is_crypto, once_a_day, value_ago

NY = "America/New_York"
HOUR = pd.Timedelta(hours=1)

# feature -> (description, allowed hours range or None, allowed value range)
FEATURES = {
    "return":         ("the asset's return over the last `hours`", (1, 2160), (-0.5, 0.5)),
    "price_vs_sma":   ("price divided by its `hours` moving average", (24, 4320), (0.7, 1.3)),
    "volatility":     ("standard deviation of hourly returns over `hours`", (24, 2160), (0.0, 0.1)),
    "rsi":            ("RSI over `hours` (0-100)", (6, 336), (0, 100)),
    "volume_ratio":   ("volume this hour divided by its `hours` average", (24, 720), (0.0, 10.0)),
    "funding_annual": ("crypto only: average Hyperliquid funding over `hours`, per year", (8, 720), (-1.0, 2.0)),
    "btc_return":     ("Bitcoin's return over the last `hours`", (1, 2160), (-0.5, 0.5)),
    "hour_of_day":    ("New York hour of the bar (0-23)", None, (0, 23)),
    "day_of_week":    ("New York weekday of the bar (0 = Monday)", None, (0, 6)),
}
MAX_CONDITIONS = 4


def validate_spec(spec: dict, allowed_symbols: list[str]) -> dict:
    """A clean copy of `spec`, or ValueError saying what is wrong (the brain gets the message back)."""
    if not isinstance(spec, dict):
        raise ValueError("the idea must be a JSON object")
    name = re.sub(r"[^a-z0-9_]+", "_", str(spec.get("name", "")).lower()).strip("_")[:40]
    if len(name) < 3:
        raise ValueError("name: give a short snake_case name")
    symbols = spec.get("symbols")
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 10 or any(s not in allowed_symbols for s in symbols):
        raise ValueError(f"symbols: 1-10 of {allowed_symbols}")
    conds = spec.get("conditions")
    if not isinstance(conds, list) or not 1 <= len(conds) <= MAX_CONDITIONS:
        raise ValueError(f"conditions: 1-{MAX_CONDITIONS} conditions")
    clean = []
    for c in conds:
        if not isinstance(c, dict) or c.get("feature") not in FEATURES:
            raise ValueError(f"unknown feature {c.get('feature') if isinstance(c, dict) else c!r}; use one of {list(FEATURES)}")
        _, hours_range, value_range = FEATURES[c["feature"]]
        if c.get("op") not in (">", "<"):
            raise ValueError("op must be '>' or '<'")
        try:
            value = float(c["value"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"{c['feature']}: value must be a number") from None
        if not value_range[0] <= value <= value_range[1]:
            raise ValueError(f"{c['feature']}: value must be within {value_range}")
        item = {"feature": c["feature"], "op": c["op"], "value": value}
        if hours_range:
            try:
                hours = int(c["hours"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"{c['feature']}: needs hours") from None
            if not hours_range[0] <= hours <= hours_range[1]:
                raise ValueError(f"{c['feature']}: hours must be within {hours_range}")
            item["hours"] = hours
        if c["feature"] == "funding_annual" and not all(is_crypto(s) for s in symbols):
            raise ValueError("funding_annual only works when every symbol is a coin")
        clean.append(item)
    decide = spec.get("decide", "daily")
    if decide not in ("daily", "hourly"):
        raise ValueError("decide must be 'daily' or 'hourly'")
    return {"name": name, "thesis": str(spec.get("thesis", ""))[:500], "symbols": symbols,
            "decide": decide, "conditions": clean}


def feature(c: dict, s: str, data: MarketData) -> pd.Series:
    close = data.close[s].dropna()
    f, h = c["feature"], c.get("hours")
    if f == "return":
        return close / value_ago(close, h * HOUR) - 1
    if f == "price_vs_sma":
        return close / close.rolling(f"{h}h").mean()
    if f == "volatility":
        return np.log(close).diff().rolling(f"{h}h").std()
    if f == "rsi":
        d = close.diff()
        up, down = d.clip(lower=0).rolling(f"{h}h").mean(), (-d.clip(upper=0)).rolling(f"{h}h").mean()
        return 100 - 100 / (1 + up / down.replace(0, np.nan))
    if f == "volume_ratio":
        v = data.volume[s].reindex(close.index)
        return v / v.rolling(f"{h}h").mean()
    if f == "funding_annual":
        if data.funding is None or s not in data.funding:
            return pd.Series(np.nan, index=close.index)
        fr = data.funding[s].dropna()
        return (fr.rolling(f"{h}h").mean() * 24 * 365).reindex(close.index, method="ffill")
    if f == "btc_return":
        btc = data.close["BTC/USD"].dropna()
        return (btc / value_ago(btc, h * HOUR) - 1).reindex(close.index, method="ffill")
    local = close.index.tz_convert(NY)
    return pd.Series(local.hour if f == "hour_of_day" else local.dayofweek, index=close.index, dtype=float)


class BrainRule(Strategy):
    """Base for brain ideas; make_strategy() builds one subclass per idea."""
    spec: dict = {}
    default_grid = {"version": [1]}

    def __init__(self, symbols, version: int = 1):
        super().__init__(symbols, version=version)

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        w = 1.0 / len(self.symbols)
        out = {}
        for s in self.symbols:
            close = data.close[s].dropna()
            hold = pd.Series(True, index=close.index)
            for c in self.spec["conditions"]:
                x = feature(c, s, data)
                hold &= (x > c["value"]) if c["op"] == ">" else (x < c["value"])   # NaN -> False
            sig = hold.astype(float).reindex(data.index).ffill().fillna(0.0)
            if self.spec["decide"] == "daily":
                sig = once_a_day(sig, stock=not is_crypto(s))
            out[s] = sig * w
        return pd.DataFrame(out, index=data.index)


def make_strategy(spec: dict) -> type[BrainRule]:
    uses = {c["feature"] for c in spec["conditions"]}
    return type(f"Brain_{spec['name']}", (BrainRule,), {
        "name": f"brain_{spec['name']}", "spec": spec,
        "signal_symbols": ["BTC/USD"] if "btc_return" in uses else [],
        "needs_funding": "funding_annual" in uses,
    })
