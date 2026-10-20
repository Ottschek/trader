"""Data for reports: news feeds, prices (Alpaca), macro series (FRED), crypto totals (CoinGecko),
and a table of every tracked company's quality / value / growth / momentum today."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import numpy as np
import pandas as pd
import requests

UA = {"User-Agent": "trader-research/0.1 (https://github.com/Ottschek/trader; research bot)"}

FEEDS = {
    "markets": [("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
                ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
                ("CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
                ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories")],
    "world": [("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
              ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml")],
    "crypto": [("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
               ("Cointelegraph", "https://cointelegraph.com/rss")],
}
YAHOO_TICKER = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"


def get(url: str, timeout: int = 20, **kw) -> requests.Response | None:
    """GET that never raises: a dead source just means less in the report."""
    try:
        r = requests.get(url, headers=UA, timeout=timeout, **kw)
        return r if r.status_code == 200 else None
    except requests.RequestException:
        return None


def rss(url: str, source: str) -> list[dict]:
    r = get(url)
    if r is None:
        return []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title = re.sub(r"\s+", " ", item.findtext("title") or "").strip()
        pub = item.findtext("pubDate")
        if not title:
            continue
        try:
            ts = pd.Timestamp(parsedate_to_datetime(pub)) if pub else pd.NaT
            ts = ts.tz_localize("UTC") if ts is not pd.NaT and ts.tzinfo is None else ts.tz_convert("UTC") if ts is not pd.NaT else ts
        except (TypeError, ValueError):
            ts = pd.NaT
        out.append({"title": title, "link": (item.findtext("link") or "").strip(), "published": ts, "source": source})
    return out


def headlines(groups: list[str], hours: float = 24, now: pd.Timestamp | None = None) -> pd.DataFrame:
    now = now or pd.Timestamp.now(tz="UTC")
    rows = [h for g in groups for source, url in FEEDS[g] for h in rss(url, source)]
    df = pd.DataFrame(rows, columns=["title", "link", "published", "source"])
    if df.empty:
        return df
    df = df[df["published"].isna() | (df["published"] > now - pd.Timedelta(hours=hours))]
    return df.drop_duplicates("title").sort_values("published", ascending=False, na_position="last").reset_index(drop=True)


def ticker_headlines(ticker: str, limit: int = 6) -> pd.DataFrame:
    rows = rss(YAHOO_TICKER.format(ticker=ticker), "Yahoo Finance")
    return pd.DataFrame(rows, columns=["title", "link", "published", "source"]).drop_duplicates("title").head(limit)


def fred(series: str) -> pd.Series:
    """A FRED series (no key needed for the CSV download); empty if unavailable."""
    r = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}")
    if r is None:
        return pd.Series(dtype=float)
    from io import StringIO
    df = pd.read_csv(StringIO(r.text))
    s = pd.to_numeric(df.iloc[:, 1], errors="coerce")
    s.index = pd.to_datetime(df.iloc[:, 0])
    return s.dropna()


def crypto_total() -> dict | None:
    """Total crypto market cap and its 24h change (CoinGecko, free)."""
    r = get("https://api.coingecko.com/api/v3/global")
    if r is None:
        return None
    d = r.json().get("data", {})
    try:
        return {"market_cap": float(d["total_market_cap"]["usd"]), "change_24h": float(d["market_cap_change_percentage_24h_usd"]) / 100}
    except (KeyError, TypeError, ValueError):
        return None


# --- prices (Alpaca) ------------------------------------------------------------------------------

def stock_daily(symbols: list[str], days: int = 400) -> pd.DataFrame:
    """Adjusted daily closes, wide (date x symbol)."""
    from trader.config import load_alpaca_secrets
    from trader.data.daily import fetch_window
    now = pd.Timestamp.now(tz="UTC")
    bars = fetch_window(symbols, (now - pd.Timedelta(days=days)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"),
                        load_alpaca_secrets(), log=lambda m: None)
    if bars.empty:
        return pd.DataFrame()
    return bars.pivot_table(index="ts", columns="symbol", values="close").sort_index()


def crypto_daily(pairs: list[str], days: int = 400) -> pd.DataFrame:
    from alpaca.data.historical import CryptoHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame
    now = pd.Timestamp.now(tz="UTC")
    res = CryptoHistoricalDataClient().get_crypto_bars(CryptoBarsRequest(symbol_or_symbols=pairs, timeframe=TimeFrame.Day,
                                                                          start=now - pd.Timedelta(days=days)))
    if res.df.empty:
        return pd.DataFrame()
    return res.df.reset_index().pivot_table(index="timestamp", columns="symbol", values="close").sort_index()


def monthly(symbols: list[str], months: int, adjusted: bool) -> pd.DataFrame:
    """Monthly closes (wide, month period x symbol), adjusted or not."""
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from trader.config import load_alpaca_secrets
    sec = load_alpaca_secrets()
    client = StockHistoricalDataClient(sec.api_key, sec.secret_key)
    start = pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=months)
    frames = []
    for i in range(0, len(symbols), 200):
        try:
            res = client.get_stock_bars(StockBarsRequest(symbol_or_symbols=symbols[i:i + 200], timeframe=TimeFrame.Month, start=start,
                                                         adjustment=Adjustment.ALL if adjusted else Adjustment.RAW, feed=DataFeed.SIP))
        except Exception:  # noqa: BLE001 - one bad batch only thins the comparison
            continue
        if not res.df.empty:
            frames.append(res.df.reset_index())
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    df["month"] = df["timestamp"].dt.tz_convert("America/New_York").dt.tz_localize(None).dt.to_period("M")
    return df.pivot_table(index="month", columns="symbol", values="close").sort_index()


# --- every tracked company, today --------------------------------------------------------------

def pct_of(values: pd.Series, x: float) -> float | None:
    """Percentile (0-100) of x among values."""
    v = values.dropna()
    if pd.isna(x) or v.empty:
        return None
    return float((v < x).mean() * 100 + (v == x).mean() * 50)


def universe_table_path():
    from trader.data.store import data_dir
    return data_dir() / "reports_cache" / "universe_table.parquet"


def cached_universe_table(max_age_hours: float = 20) -> tuple[pd.DataFrame, bool]:
    """(table, rebuilt): universe_table(), rebuilt at most once per `max_age_hours` (~1.5 minutes)."""
    path = universe_table_path()
    if path.exists():
        table = pd.read_parquet(path)
        built = pd.Timestamp(table["built_at"].iloc[0]) if "built_at" in table and len(table) else None
        if built is not None and pd.Timestamp.now(tz="UTC") - built < pd.Timedelta(hours=max_age_hours):
            return table, False
    table = universe_table()
    table["built_at"] = pd.Timestamp.now(tz="UTC").isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(path)
    return table, True


def universe_table(now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Ratios, market cap, momentum and industry for the ~1,500 tracked companies, as of now."""
    from trader.data.fundamentals import as_of, load_fundamentals, ratios
    from trader.data.sec import company_info_path
    from trader.data.store import read
    from trader.strategies.quality_value import universe_map
    now = now or pd.Timestamp.now(tz="UTC")
    snap = as_of(load_fundamentals(), now)
    cmap = universe_map().drop_duplicates("symbol").drop_duplicates("cik")
    cmap = cmap[cmap["cik"].isin(snap.index)]
    symbols = sorted(cmap["symbol"])
    raw, adj = monthly(symbols, 20, adjusted=False), monthly(symbols, 20, adjusted=True)
    snap = snap.loc[cmap["cik"]].set_axis(cmap["symbol"].to_numpy())
    snap = snap[snap.index.isin(raw.columns)]
    price = raw.iloc[-1].reindex(snap.index)
    mcap = price * snap.get("shares", pd.Series(np.nan, index=snap.index))
    if "public_float" in snap and len(adj):
        months = adj.index
        for s in snap.index[mcap.isna()]:
            when = snap.at[s, "public_float_date"] if "public_float_date" in snap else None
            if pd.isna(snap.at[s, "public_float"]) or pd.isna(when) or s not in adj:
                continue
            m = pd.Timestamp(when).to_period("M")
            if m in months and pd.notna(adj.at[m, s]) and pd.notna(adj[s].iloc[-1]):
                mcap[s] = snap.at[s, "public_float"] * adj[s].iloc[-1] / adj.at[m, s]
    r = ratios(snap, mcap)
    r["market_cap"] = mcap
    r["price"] = price
    r["momentum"] = adj.iloc[-2] / adj.iloc[-13] - 1 if len(adj) >= 13 else np.nan   # 12-1 months
    info = read(company_info_path())
    if not info.empty:
        sic = info.set_index("cik")["sic"]
        r["sic"] = cmap.set_index("symbol")["cik"].reindex(r.index).map(sic)
    r["cik"] = cmap.set_index("symbol")["cik"].reindex(r.index)
    return r


def scores(row: pd.Series, table: pd.DataFrame) -> dict[str, float | None]:
    """Quality, value, growth, momentum (0-100 percentiles vs every tracked company)."""
    def pct(c, sign):        # sign -1: lower is better (accruals, leverage)
        return pct_of(sign * table[c], sign * row.get(c, np.nan)) if c in table else None

    def avg(items):
        vals = [v for v in (pct(c, sign) for c, sign in items) if v is not None]
        return float(np.mean(vals)) if vals else None
    return {"quality": avg([("roa", 1), ("cash_roa", 1), ("accruals", -1), ("leverage", -1)]),
            "value": avg([("earnings_yield", 1), ("fcf_yield", 1)]),
            "growth": avg([("revenue_growth", 1)]),
            "momentum": avg([("momentum", 1)])}
