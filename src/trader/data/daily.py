"""Daily stock bars for broad universes (insider clusters, earnings drift).

Alpaca daily bars, split- and dividend-adjusted (adjustment="all"). Adjusted price *levels*
contain later corporate actions, but these strategies only trade on events and use returns
after them, which adjustments leave correct. Stored long in data/daily/stocks.parquet.
Every symbol trades at the `alpaca_smallcaps` cost profile (30 bps one way).
"""

from __future__ import annotations

import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from trader.backtest.data import MarketData, _utc, adjusted_prices, asset_returns, flatten_spikes
from trader.config import AlpacaSecrets
from trader.data.store import data_dir, read, upsert

VENUE = "alpaca_smallcaps"
BATCH = 200
SPIKE = 0.30   # daily bad-tick threshold (small caps move a lot for real)


def daily_path():
    return data_dir() / "daily" / "stocks.parquet"


def alpaca_symbol(sec_symbol: str) -> str:
    return sec_symbol.replace("-", ".")   # SEC writes BRK-B, Alpaca BRK.B


def update_daily_bars(symbols: list[str], start: str, end: str, secrets: AlpacaSecrets, log=print,
                      refresh: bool = False) -> int:
    """New symbols get full history from `start`. With `refresh`, symbols we already have also get
    their recent bars (from a few days before the newest stored bar)."""
    client = StockHistoricalDataClient(secrets.api_key, secrets.secret_key)
    have = read(daily_path())
    known = set(have["symbol"].unique()) if not have.empty else set()
    wanted = {alpaca_symbol(s) for s in symbols}
    added = _fetch(client, sorted(wanted - known), start, end, log)
    if refresh and known:
        recent = (have["ts"].max() - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
        added += _fetch(client, sorted(wanted & known), recent, end, log)
    return added


def _fetch(client, todo: list[str], start: str, end: str, log) -> int:
    added = 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        try:
            res = client.get_stock_bars(StockBarsRequest(symbol_or_symbols=batch, timeframe=TimeFrame.Day, start=start,
                                                         end=end, adjustment=Adjustment.ALL, feed=DataFeed.SIP))
        except Exception as e:  # noqa: BLE001 - one bad symbol shouldn't stop the batch loop
            log(f"  daily batch {i // BATCH + 1}: {type(e).__name__}: {str(e)[:120]}")
            continue
        if res.df.empty:
            continue
        df = res.df.reset_index().rename(columns={"timestamp": "ts"})[["ts", "symbol", "open", "high", "low", "close", "volume"]]
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        added += upsert(daily_path(), df, key=["ts", "symbol"])
        log(f"  daily: {min(i + BATCH, len(todo))}/{len(todo)} symbols, +{len(df):,} bars")
    return added


def load_daily(symbols, start=None, end=None, clean: bool = True) -> MarketData:
    all_bars = read(daily_path())
    if all_bars.empty:
        raise ValueError("no daily bars stored")
    if start is not None:
        all_bars = all_bars[all_bars["ts"] >= _utc(start)]
    if end is not None:
        all_bars = all_bars[all_bars["ts"] <= _utc(end)]
    wanted = {alpaca_symbol(s) for s in symbols}
    cols = {k: {} for k in ("close", "open", "volume", "gap", "intra", "raw_close")}
    cleaned = {}
    for symbol, bars in all_bars[all_bars["symbol"].isin(wanted)].groupby("symbol"):
        bars = bars.sort_values("ts")
        if len(bars) < 3:
            continue
        bars, cleaned[symbol] = flatten_spikes(bars, SPIKE) if clean else (bars, 0)
        r = asset_returns(bars)
        c, o = adjusted_prices(r)
        cols["close"][symbol], cols["open"][symbol], cols["volume"][symbol] = c, o, r["volume"]
        cols["gap"][symbol], cols["intra"][symbol], cols["raw_close"][symbol] = r["gap"], r["intra"], r["close"]
    frames = {k: pd.DataFrame(v).sort_index() for k, v in cols.items()}
    return MarketData(
        close=frames["close"], open=frames["open"], volume=frames["volume"],
        gap=frames["gap"].fillna(0.0), intra=frames["intra"].fillna(0.0), tradable=frames["close"].notna(),
        venue={s: VENUE for s in frames["close"].columns}, cleaned=cleaned, raw_close=frames["raw_close"],
    )
