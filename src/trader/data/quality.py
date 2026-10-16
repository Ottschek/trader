"""Data quality checks. Each check returns a CheckResult: ok, warn or fail.

fail = the data is wrong or stale and must not be traded on.
warn = worth a look (e.g. history gaps or spikes), but not necessarily wrong.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass(frozen=True)
class CheckResult:
    check: str
    dataset: str
    status: str
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def check_ohlc(df: pd.DataFrame, dataset: str) -> CheckResult:
    """Prices positive and high/low actually bracket open/close."""
    bad = (
        (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (df["high"] < df[["open", "close"]].max(axis=1) - 1e-9)
        | (df["low"] > df[["open", "close"]].min(axis=1) + 1e-9)
        | (df["volume"] < 0)
    )
    n = int(bad.sum())
    return CheckResult("ohlc", dataset, FAIL if n else OK, f"{n} impossible bars" if n else f"{len(df)} bars")


def check_duplicates(df: pd.DataFrame, dataset: str, key: list[str] | None = None) -> CheckResult:
    n = int(df.duplicated(subset=key or ["ts"]).sum())
    return CheckResult("duplicates", dataset, FAIL if n else OK, f"{n} duplicate timestamps")


def check_gaps(df: pd.DataFrame, dataset: str, warn_missing_pct: float = 1.0) -> CheckResult:
    """For 24/7 markets: how many hours are missing between first and last bar."""
    if len(df) < 2:
        return CheckResult("gaps", dataset, OK, "too little data")
    ts = df["ts"].sort_values()
    expected = int((ts.iloc[-1] - ts.iloc[0]) / pd.Timedelta(hours=1)) + 1
    missing = expected - ts.nunique()
    pct = 100 * missing / expected
    max_gap_h = int(ts.diff().max() / pd.Timedelta(hours=1)) - 1
    status = WARN if pct > warn_missing_pct else OK
    return CheckResult("gaps", dataset, status, f"{missing} missing hours ({pct:.2f}%), longest gap {max_gap_h}h")


def check_spikes(df: pd.DataFrame, dataset: str, threshold: float = 0.25) -> CheckResult:
    """Bad-tick heuristic: a huge 1-bar move that immediately reverses."""
    if len(df) < 3:
        return CheckResult("spikes", dataset, OK, "too little data")
    r = np.log(df.sort_values("ts")["close"]).diff()
    spikes = (r.abs() > threshold) & (r.shift(-1).abs() > threshold) & (np.sign(r) != np.sign(r.shift(-1)))
    n = int(spikes.sum())
    return CheckResult("spikes", dataset, WARN if n else OK, f"{n} spike-and-revert bars (>{threshold:.0%})")


def check_staleness(df: pd.DataFrame, dataset: str, now: pd.Timestamp, max_age_hours: float) -> CheckResult:
    if df.empty:
        return CheckResult("staleness", dataset, FAIL, "no data")
    age_h = (now - df["ts"].max()) / pd.Timedelta(hours=1)
    status = FAIL if age_h > max_age_hours else OK
    return CheckResult("staleness", dataset, status, f"last bar {age_h:.1f}h ago (limit {max_age_hours}h)")


def check_drift(a: pd.DataFrame, b: pd.DataFrame, dataset: str,
                warn_bps: float = 20, fail_bps: float = 100, recent_hours: int = 24 * 30) -> CheckResult:
    """Median close-price difference between two sources over the last ~30 days."""
    m = a[["ts", "close"]].merge(b[["ts", "close"]], on="ts", suffixes=("_a", "_b"))
    if m.empty:
        return CheckResult("drift", dataset, WARN, "no overlapping bars")
    m = m[m["ts"] >= m["ts"].max() - pd.Timedelta(hours=recent_hours)]
    bps = (10_000 * (m["close_a"] / m["close_b"] - 1)).abs()
    med = float(bps.median())
    status = FAIL if med > fail_bps else WARN if med > warn_bps else OK
    return CheckResult("drift", dataset, status, f"median {med:.1f} bps, p95 {bps.quantile(0.95):.1f} bps over {len(m)} bars")


def worst(results: list[CheckResult]) -> str:
    statuses = {r.status for r in results}
    return FAIL if FAIL in statuses else WARN if WARN in statuses else OK
