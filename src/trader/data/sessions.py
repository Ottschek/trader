"""US stock trading sessions.

A plain clock-hour bar like 09:00–10:00 ET mixes pre-market (09:00–09:30) and
regular trading (09:30–10:00). To tag every bar with exactly one session, stocks
are downloaded as 30-minute bars and aggregated into hourly bars *within* each
session, anchored at the session start:

    pre      04:00–09:30 ET  -> 04:00, 05:00, ..., 08:00, 09:00 (half bar)
    regular  09:30–16:00 ET  -> 09:30, 10:30, ..., 14:30, 15:30 (half bar)
    post     16:00–20:00 ET  -> 16:00, 17:00, 18:00, 19:00
"""

from __future__ import annotations

import numpy as np
import pandas as pd

NY = "America/New_York"

# (name, start minute of day, end minute of day) in New York time
SESSIONS = (
    ("pre", 4 * 60, 9 * 60 + 30),
    ("regular", 9 * 60 + 30, 16 * 60),
    ("post", 16 * 60, 20 * 60),
)


def tag_sessions(ts_utc: pd.Series) -> pd.Series:
    """Session name for each bar start time; 'other' outside 04:00–20:00 ET."""
    local = ts_utc.dt.tz_convert(NY)
    minute = local.dt.hour * 60 + local.dt.minute
    out = pd.Series("other", index=ts_utc.index, dtype=object)
    for name, start, end in SESSIONS:
        out[(minute >= start) & (minute < end)] = name
    return out


def to_session_hourly(bars_30m: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 30-minute bars (one symbol) into session-aligned hourly bars."""
    if bars_30m.empty:
        return bars_30m.copy()

    df = bars_30m.sort_values("ts").copy()
    df["session"] = tag_sessions(df["ts"])
    df = df[df["session"] != "other"]

    local = df["ts"].dt.tz_convert(NY)
    minute = local.dt.hour * 60 + local.dt.minute
    session_start = df["session"].map({name: start for name, start, _ in SESSIONS})
    session_end = df["session"].map({name: end for name, _, end in SESSIONS})
    bucket_minute = session_start + ((minute - session_start) // 60) * 60
    end_minute = np.minimum(bucket_minute + 60, session_end)
    day = local.dt.normalize()
    df["bucket"] = (day + pd.to_timedelta(bucket_minute, unit="m")).dt.tz_convert("UTC")
    # when the hourly bar closes (half bars at 09:00 pre and 15:30 regular close after 30 min)
    df["ts_end"] = (day + pd.to_timedelta(end_minute, unit="m")).dt.tz_convert("UTC")

    has_vwap = "vwap" in df.columns
    if has_vwap:
        df["_pv"] = df["vwap"] * df["volume"]

    agg = {"ts_end": "first", "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if "trade_count" in df.columns:
        agg["trade_count"] = "sum"
    if has_vwap:
        agg["_pv"] = "sum"
    for col in ("symbol", "source"):
        if col in df.columns:
            agg[col] = "first"

    out = df.groupby(["bucket", "session"], sort=True).agg(agg).reset_index()
    if has_vwap:
        out["vwap"] = np.where(out["volume"] > 0, out["_pv"] / out["volume"].where(out["volume"] > 0), out["close"])
        out = out.drop(columns="_pv")
    return out.rename(columns={"bucket": "ts"})
