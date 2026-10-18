"""Hyperliquid perpetuals: funding rate history + hourly open interest snapshots.

Public API, no keys. Reachable from GitHub's US servers (unlike Binance/Bybit).
Funding history exists from May 2023; open interest has no history API, so we
record it ourselves every hour from now on.
"""

from __future__ import annotations

import time

import pandas as pd
import requests

from trader.data.store import funding_path, last_ts, snapshots_path, upsert

URL = "https://api.hyperliquid.xyz/info"
PAGE = 500  # fundingHistory returns at most 500 rows per request
PAUSE_S = 1.1  # stay well under the API's rate limit


def _post(payload: dict, retries: int = 5) -> object:
    for attempt in range(retries):
        try:
            r = requests.post(URL, json=payload, timeout=30)
        except requests.ConnectionError:  # network blip: wait and retry
            if attempt == retries - 1:
                raise
            time.sleep(2 ** (attempt + 1))
            continue
        if r.status_code == 429:  # rate limited: back off and retry
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Hyperliquid kept rate-limiting {payload['type']}")


def update_funding(coin: str, log=print) -> int:
    path = funding_path(coin)
    last = last_ts(path, "time")
    start_ms = 0 if last is None else int(last.timestamp() * 1000) + 1

    rows: list[dict] = []
    while True:
        page = _post({"type": "fundingHistory", "coin": coin, "startTime": start_ms})
        rows.extend(page)
        if len(page) < PAGE:
            break
        start_ms = page[-1]["time"] + 1
        time.sleep(PAUSE_S)

    if not rows:
        return 0
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df["ts"] = df["time"].dt.floor("h")
    df = df.rename(columns={"fundingRate": "funding_rate"})
    df["funding_rate"] = df["funding_rate"].astype(float)
    df["premium"] = df["premium"].astype(float)
    return upsert(path, df[["ts", "time", "coin", "funding_rate", "premium"]], key=["ts"])


def snapshot(coins: list[str], now: pd.Timestamp) -> int:
    """Record current open interest, funding and prices for each coin. One row per coin per hour."""
    meta, ctxs = _post({"type": "metaAndAssetCtxs"})
    index = {u["name"]: i for i, u in enumerate(meta["universe"])}
    rows = []
    for coin in coins:
        if coin not in index:
            continue
        c = ctxs[index[coin]]
        rows.append({
            "ts": now.floor("h"),
            "taken_at": now,
            "coin": coin,
            "open_interest": float(c["openInterest"]),
            "funding_rate": float(c["funding"]),
            "premium": float(c["premium"]) if c.get("premium") is not None else None,
            "mark_px": float(c["markPx"]),
            "oracle_px": float(c["oraclePx"]),
            "day_notional_volume": float(c["dayNtlVlm"]),
        })
    return upsert(snapshots_path(), pd.DataFrame(rows), key=["ts", "coin"])


SNAPSHOT_COLUMNS = ["ts", "taken_at", "coin", "open_interest", "funding_rate", "premium",
                    "mark_px", "oracle_px", "day_notional_volume"]


def merge_worker_snapshots(rows: list[dict]) -> int:
    """Add snapshots the Cloudflare Worker recorded in D1. It takes them on time every hour,
    which GitHub's cron can't guarantee, so in cloud mode these replace our own snapshot."""
    if not rows:
        return 0
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["taken_at"] = pd.to_datetime(df["taken_at"], utc=True)
    return upsert(snapshots_path(), df[SNAPSHOT_COLUMNS], key=["ts", "coin"])
