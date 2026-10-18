"""Alternative data collectors (all free, no keys).

Stored under data/alt/ (synced to R2 like everything else):
    stablecoins.parquet   daily circulating supply of USDT/USDC (DefiLlama, full history)
    pageviews.parquet     daily English Wikipedia pageviews per symbol (attention gauge, since 2015)
    etf_flows.parquet     daily US spot Bitcoin ETF net flows per fund, US$ millions (Farside, since Jan 2024)
    polymarket.parquet    hourly snapshot of the most-traded Polymarket markets (forward only)
    listings.parquet      new trading pairs detected on Coinbase / Kraken (forward only)
    state.json            when each daily source was last fetched, and the last seen pair lists

Every collector returns rows added; quality checks catch sources that stop updating.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pandas as pd
import requests

from trader.data.store import data_dir, read, upsert

UA = {"User-Agent": "trader-research/0.1 (https://github.com/Ottschek/trader; research bot)"}
DAILY_REFRESH = pd.Timedelta(hours=20)


def alt_path(name: str) -> Path:
    return data_dir() / "alt" / f"{name}.parquet"


def state_path() -> Path:
    return data_dir() / "alt" / "state.json"


def load_state() -> dict:
    p = state_path()
    return json.loads(p.read_text()) if p.exists() else {}


def save_state(state: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=1, sort_keys=True))


def due(state: dict, name: str, now: pd.Timestamp) -> bool:
    last = state.get("fetched", {}).get(name)
    return last is None or now - pd.Timestamp(last) >= DAILY_REFRESH


def mark(state: dict, name: str, now: pd.Timestamp) -> None:
    state.setdefault("fetched", {})[name] = now.isoformat()


def _get(url: str, retries: int = 5, **kw):
    """GET with retries; on 429 waits as long as the server asks (Retry-After), with backoff."""
    r = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=UA, timeout=30, **kw)
        except requests.ConnectionError:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** (attempt + 1))
            continue
        if r.status_code != 429:
            return r
        wait = r.headers.get("Retry-After")
        time.sleep(float(wait) if wait and wait.isdigit() else 5 * 2 ** attempt)
    return r


# --- parsers (pure, unit-tested) ------------------------------------------------------

def parse_stablecoin_chart(rows: list[dict], coin: str) -> pd.DataFrame:
    df = pd.DataFrame({
        "ts": pd.to_datetime([int(r["date"]) for r in rows], unit="s", utc=True),
        "coin": coin,
        "circulating_usd": [float((r.get("totalCirculatingUSD") or {}).get("peggedUSD", 0.0)) for r in rows],
    })
    return df[df["circulating_usd"] > 0]


def parse_pageviews(items: list[dict]) -> pd.Series:
    """Wikimedia pageviews items -> daily views indexed by UTC date."""
    if not items:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([i["timestamp"][:8] for i in items], format="%Y%m%d", utc=True)
    return pd.Series([float(i["views"]) for i in items], index=idx)


def _flow_value(text) -> float:
    s = str(text).strip().replace(",", "")
    if s in ("", "-", "nan", "None"):
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    return -float(s.strip("()")) if neg else float(s)


def parse_farside(html: str) -> pd.DataFrame:
    """Farside's ETF flow table -> long rows (ts, fund, flow_musd). Summary rows are dropped."""
    tables = pd.read_html(io.StringIO(html))
    table = max(tables, key=len)
    table.columns = [" ".join(str(c) for c in col if "Unnamed" not in str(c)).strip() if isinstance(col, tuple)
                     else str(col) for col in table.columns]
    first = table.columns[0]
    dates = pd.to_datetime(table[first], format="%d %b %Y", errors="coerce")
    table = table[dates.notna()].copy()
    table["ts"] = dates[dates.notna()].dt.tz_localize("UTC")
    funds = [c for c in table.columns if c not in (first, "ts") and c.strip()]
    long = table.melt(id_vars="ts", value_vars=funds, var_name="fund", value_name="raw")
    long["fund"] = long["fund"].str.strip()
    long["flow_musd"] = long["raw"].map(_flow_value)
    return long[["ts", "fund", "flow_musd"]].sort_values(["ts", "fund"]).reset_index(drop=True)


def parse_polymarket(markets: list[dict], now: pd.Timestamp) -> pd.DataFrame:
    rows = []
    for m in markets:
        try:
            outcomes = json.loads(m.get("outcomes") or "[]")
            prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
        except (ValueError, TypeError):
            continue
        if not prices:
            continue
        rows.append({
            "ts": now.floor("h"), "taken_at": now, "market_id": str(m.get("id")), "slug": m.get("slug"),
            "question": m.get("question"), "outcome": outcomes[0] if outcomes else None, "price": prices[0],
            "volume_24h": float(m.get("volume24hr") or 0), "liquidity": float(m.get("liquidity") or 0),
            "end_date": m.get("endDate"),
        })
    return pd.DataFrame(rows)


def new_listings(previous: list[str] | None, current: list[str], exchange: str, now: pd.Timestamp) -> pd.DataFrame:
    """Pairs that appeared since the last snapshot. The first snapshot only records the baseline."""
    if previous is None:
        return pd.DataFrame(columns=["ts", "exchange", "pair"])
    added = sorted(set(current) - set(previous))
    return pd.DataFrame({"ts": [now] * len(added), "exchange": exchange, "pair": added})


# --- collectors ---------------------------------------------------------------------

def update_stablecoins(ids: dict[str, int]) -> int:
    added = 0
    for coin, sid in ids.items():
        rows = _get(f"https://stablecoins.llama.fi/stablecoincharts/all?stablecoin={sid}").json()
        added += upsert(alt_path("stablecoins"), parse_stablecoin_chart(rows, coin), key=["ts", "coin"])
    return added


def update_pageviews(titles: dict[str, list[str]], now: pd.Timestamp, start: str = "2016-01-01") -> int:
    """Each symbol is saved as soon as it's fetched, so a rate limit never loses progress.
    Symbols that failed are retried on the next run (the error is raised at the end)."""
    path = alt_path("pageviews")
    stored = read(path)
    end = (now - pd.Timedelta(days=1)).strftime("%Y%m%d")
    added, failed = 0, []
    for symbol, names in titles.items():
        mine = stored[stored["symbol"] == symbol] if not stored.empty else stored
        # re-fetch the last few days (late counts); full history for symbols we don't have yet
        since = mine["ts"].max() - pd.Timedelta(days=3) if len(mine) else pd.Timestamp(start, tz="UTC")
        total = None
        try:
            for t in names:
                url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
                       f"{requests.utils.quote(t, safe='')}/daily/{since:%Y%m%d}/{end}")
                r = _get(url)
                time.sleep(0.5)       # be polite to Wikimedia
                if r.status_code == 404:
                    continue          # article didn't exist under this title in the window
                r.raise_for_status()
                s = parse_pageviews(r.json().get("items", []))
                total = s if total is None else total.add(s, fill_value=0)
        except requests.RequestException as e:
            failed.append(f"{symbol} ({type(e).__name__})")
            continue
        if total is not None and len(total):
            added += upsert(path, pd.DataFrame({"ts": total.index, "symbol": symbol, "views": total.values}),
                            key=["ts", "symbol"])
    if failed:
        raise RuntimeError(f"pageviews incomplete, will retry: {', '.join(failed)} (+{added} rows saved)")
    return added


def update_etf_flows() -> int:
    r = _get("https://farside.co.uk/bitcoin-etf-flow-all-data/")
    r.raise_for_status()
    return upsert(alt_path("etf_flows"), parse_farside(r.text), key=["ts", "fund"])


def update_polymarket(now: pd.Timestamp, top_n: int = 100) -> int:
    url = (f"https://gamma-api.polymarket.com/markets?active=true&closed=false&limit={top_n}"
           "&order=volume24hr&ascending=false")
    r = _get(url)
    r.raise_for_status()
    return upsert(alt_path("polymarket"), parse_polymarket(r.json(), now), key=["ts", "market_id"])


def update_listings(state: dict, now: pd.Timestamp) -> int:
    pairs = {
        "coinbase": sorted(p["id"] for p in _get("https://api.exchange.coinbase.com/products").json()
                           if p.get("status") == "online"),
        "kraken": sorted(_get("https://api.kraken.com/0/public/AssetPairs").json().get("result", {}).keys()),
    }
    seen = state.setdefault("listings", {})
    frames = [new_listings(seen.get(ex), current, ex, now) for ex, current in pairs.items()]
    for ex, current in pairs.items():
        seen[ex] = current
    state["listings_checked"] = now.isoformat()
    events = pd.concat(frames, ignore_index=True)
    return upsert(alt_path("listings"), events, key=["ts", "exchange", "pair"]) if len(events) else 0


def collect_all(settings, now: pd.Timestamp, run) -> None:
    """Run every alternative-data collector through `run(dataset, fn, *args)` (the collector's
    error-isolating wrapper). Daily sources only refresh once a day."""
    cfg = settings.alt_data or {}
    state = load_state()
    if due(state, "stablecoins", now) and run("alt stablecoins", update_stablecoins, cfg.get("stablecoins", {})) is not None:
        mark(state, "stablecoins", now)
    if due(state, "pageviews", now) and run("alt pageviews", update_pageviews, cfg.get("wikipedia", {}), now) is not None:
        mark(state, "pageviews", now)
    if due(state, "etf_flows", now) and run("alt etf flows", update_etf_flows) is not None:
        mark(state, "etf_flows", now)
    run("alt polymarket", update_polymarket, now, cfg.get("polymarket_top_n", 100))
    run("alt listings", update_listings, state, now)
    save_state(state)


ALT_STALENESS_HOURS = {"stablecoins": 72, "pageviews": 96, "etf_flows": 120, "polymarket": 3}


def staleness_targets() -> dict[str, tuple[Path, float]]:
    return {name: (alt_path(name), hours) for name, hours in ALT_STALENESS_HOURS.items()}

