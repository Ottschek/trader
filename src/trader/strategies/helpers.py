"""Small building blocks for strategies. All of them only look backwards in time."""

from __future__ import annotations

import pandas as pd

from trader.data.sessions import NY


def is_crypto(symbol: str) -> bool:
    return "/" in symbol


def value_ago(series: pd.Series, delta: pd.Timedelta) -> pd.Series:
    """For each bar, the series' last value at or before (bar time - delta). NaN if none."""
    past = series.reindex(series.index - delta, method="ffill")
    return pd.Series(past.to_numpy(), index=series.index)


def has_history(series: pd.Series, delta: pd.Timedelta) -> pd.Series:
    """True once the series has at least `delta` of history behind the bar."""
    if series.empty:  # e.g. a coin that hadn't launched yet at this point in history
        return pd.Series(False, index=series.index, dtype=bool)
    return pd.Series(series.index - series.index[0] >= delta, index=series.index)


def daily_decision_points(index: pd.DatetimeIndex, stock: bool) -> pd.Series:
    """Bars where a once-a-day strategy re-decides: the last regular-session bar of the day
    for stocks (15:00 New York or later), the first bar of the UTC day for crypto."""
    if stock:
        return pd.Series(index.tz_convert(NY).hour >= 15, index=index)
    day = index.floor("D")
    return pd.Series(pd.Series(day, index=index).diff().ne(pd.Timedelta(0)).to_numpy(), index=index)


def once_a_day(signal: pd.Series, stock: bool) -> pd.Series:
    """Only let the signal change at the daily decision point (cuts turnover and costs)."""
    points = daily_decision_points(signal.index, stock)
    return signal.where(points).ffill().fillna(0.0)


def hold_after(entries: pd.Series, hold: pd.Timedelta) -> pd.Series:
    """True for `hold` after each entry signal (a new entry restarts the clock)."""
    last = pd.Series(entries.index.where(entries.to_numpy()), index=entries.index).ffill()
    since = pd.Series(entries.index, index=entries.index) - last
    return (since < hold).fillna(False)
