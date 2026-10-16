"""Alpaca market data: crypto bars (no keys), stock bars + corporate actions (paper keys).

Stocks are stored twice:
    bars/alpaca_stocks_30m/   raw 30-minute bars, the source of truth
    bars/alpaca_stocks/       session-aligned hourly bars built from them (see sessions.py)
Prices are RAW (not split/dividend adjusted); adjustments are applied on read.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from alpaca.data.enums import Adjustment, CorporateActionsType, DataFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.historical.corporate_actions import CorporateActionsClient
from alpaca.data.requests import CorporateActionsRequest, CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from trader.config import AlpacaSecrets
from trader.data.sessions import tag_sessions, to_session_hourly
from trader.data.store import bars_path, corporate_actions_path, last_ts, read, upsert

CRYPTO_SOURCE = "alpaca_crypto"
STOCK_30M_SOURCE = "alpaca_stocks_30m"
STOCK_SOURCE = "alpaca_stocks"
# Alpaca's free plan serves full-market (SIP) data except the most recent 15 minutes.
SIP_DELAY = pd.Timedelta(minutes=16)


def _start_after(path, start: str, step: pd.Timedelta) -> pd.Timestamp:
    last = last_ts(path)
    return pd.Timestamp(start, tz="UTC") if last is None else last + step


def _bars_frame(df: pd.DataFrame, source: str) -> pd.DataFrame:
    df = df.reset_index().rename(columns={"timestamp": "ts"})
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["source"] = source
    return df


def update_crypto(symbol: str, start: str, now: pd.Timestamp) -> int:
    path = bars_path(CRYPTO_SOURCE, symbol)
    begin = _start_after(path, start, pd.Timedelta(hours=1))
    end = now.floor("h")  # only fully closed bars
    if begin >= end:
        return 0
    res = CryptoHistoricalDataClient().get_crypto_bars(
        CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Hour, start=begin, end=end)
    )
    if res.df.empty:
        return 0
    df = _bars_frame(res.df, CRYPTO_SOURCE)
    df = df[df["ts"] < end]
    df["session"] = "24h"
    return upsert(path, df, key=["ts"])


def update_stock(symbol: str, start: str, now: pd.Timestamp, secrets: AlpacaSecrets) -> int:
    """Fetch new 30-minute bars, then rebuild the session-aligned hourly file. Returns 30m rows added."""
    raw_path = bars_path(STOCK_30M_SOURCE, symbol)
    begin = _start_after(raw_path, start, pd.Timedelta(minutes=30))
    cutoff = now - SIP_DELAY
    added = 0
    if begin < cutoff:
        client = StockHistoricalDataClient(secrets.api_key, secrets.secret_key)
        res = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame(30, TimeFrameUnit.Minute),
            start=begin,
            end=cutoff,
            adjustment=Adjustment.RAW,
            feed=DataFeed.SIP,
        ))
        if not res.df.empty:
            df = _bars_frame(res.df, STOCK_30M_SOURCE)
            df = df[df["ts"] + pd.Timedelta(minutes=30) <= cutoff]  # drop the still-open bar
            df["session"] = tag_sessions(df["ts"])
            added = upsert(raw_path, df, key=["ts"])

    raw = read(raw_path)
    if not raw.empty:
        hourly = to_session_hourly(raw)
        hourly = hourly[hourly["ts_end"] <= cutoff]  # drop an hour whose second half isn't in yet
        hourly["source"] = STOCK_SOURCE
        path = bars_path(STOCK_SOURCE, symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        hourly.to_parquet(path, index=False)
    return added


CA_TYPES = [
    CorporateActionsType.FORWARD_SPLIT,
    CorporateActionsType.REVERSE_SPLIT,
    CorporateActionsType.CASH_DIVIDEND,
    CorporateActionsType.STOCK_DIVIDEND,
]


def update_corporate_actions(symbols: list[str], start: str, secrets: AlpacaSecrets) -> int:
    client = CorporateActionsClient(secrets.api_key, secrets.secret_key)
    rows = []
    for symbol in symbols:  # one request per symbol keeps each response under the page limit
        res = client.get_corporate_actions(CorporateActionsRequest(
            symbols=[symbol], types=CA_TYPES, start=pd.Timestamp(start).date(), end=date.today(),
        ))
        for kind, actions in res.data.items():
            for a in actions:
                d = a.model_dump()
                rows.append({
                    "symbol": d["symbol"],
                    "type": kind,
                    "ex_date": d["ex_date"],
                    "old_rate": d.get("old_rate"),
                    "new_rate": d.get("new_rate"),
                    "rate": d.get("rate"),  # cash per share for dividends
                    "special": d.get("special"),
                })
    if not rows:
        return 0
    df = pd.DataFrame(rows)
    df["ex_date"] = pd.to_datetime(df["ex_date"])
    return upsert(corporate_actions_path(), df, key=["symbol", "type", "ex_date"])
