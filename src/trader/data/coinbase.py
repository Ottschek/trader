"""Hourly spot crypto bars from Coinbase (public API via ccxt, no keys needed)."""

from __future__ import annotations

import time

import ccxt
import pandas as pd

from trader.data.store import bars_path, last_ts, upsert

SOURCE = "coinbase"
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
PAGE = 300  # Coinbase returns at most 300 candles per request
SAVE_EVERY = 20_000  # save progress during long backfills


def make_exchange() -> ccxt.Exchange:
    return ccxt.coinbaseexchange({"enableRateLimit": True})


def to_frame(rows: list[list], symbol: str) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df["symbol"] = symbol
    df["source"] = SOURCE
    df["session"] = "24h"
    return df


def fetch(ex: ccxt.Exchange, symbol: str, timeframe: str, since: int, retries: int = 4) -> list[list]:
    """fetch_ohlcv with retries, so a short network blip doesn't fail the whole symbol."""
    for attempt in range(retries):
        try:
            return ex.fetch_ohlcv(symbol, timeframe, since=since, limit=PAGE)
        except ccxt.NetworkError:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** (attempt + 1))
    return []


def find_first_ms(ex: ccxt.Exchange, symbol: str, start_ms: int, end_ms: int) -> int | None:
    """First day the pair traded (cheap: scans daily candles 300 days at a time)."""
    since = start_ms
    while since < end_ms:
        rows = fetch(ex, symbol, "1d", since)
        if rows:
            return rows[0][0]
        since += PAGE * DAY_MS
    return None


def update(symbol: str, start: str, now: pd.Timestamp, ex: ccxt.Exchange | None = None, log=print) -> int:
    """Download every complete hourly bar since the last stored one (or `start`). Returns rows added."""
    ex = ex or make_exchange()
    path = bars_path(SOURCE, symbol)
    end_ms = int(now.floor("h").timestamp() * 1000)  # only bars that have fully closed

    last = last_ts(path)
    if last is None:
        since = find_first_ms(ex, symbol, int(pd.Timestamp(start, tz="UTC").timestamp() * 1000), end_ms)
        if since is None:
            log(f"  {symbol}: no data on Coinbase")
            return 0
        log(f"  {symbol}: backfilling from {pd.Timestamp(since, unit='ms', tz='UTC'):%Y-%m-%d}")
    else:
        since = int(last.timestamp() * 1000) + HOUR_MS

    added, batch = 0, []
    while since < end_ms:
        rows = [r for r in fetch(ex, symbol, "1h", since) if since <= r[0] < end_ms]
        if rows:
            batch.extend(rows)
            since = rows[-1][0] + HOUR_MS
        else:  # a window with no trades at all (exchange outage); skip past it
            since += PAGE * HOUR_MS
        if len(batch) >= SAVE_EVERY:
            added += upsert(path, to_frame(batch, symbol), key=["ts"])
            batch = []
    added += upsert(path, to_frame(batch, symbol), key=["ts"]) if batch else 0
    return added
