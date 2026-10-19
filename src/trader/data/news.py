"""Free news headlines for the LLM news analyst (paper/news_analyst.py).

Source: Yahoo Finance's per-ticker RSS feeds (stocks and coins alike, e.g. NVDA, BTC-USD).
Collected every collector run, with or without an Anthropic key, so the archive grows from now on.

    data/alt/news_headlines.parquet   guid, published, ts, title, link, tags
        ts = when our collector first saw the headline (when it became known to us). The analyst
        only uses headlines by ts, so a story that shows up late in a feed is never back-dated.
        tags = the feeds the headline appeared in (comma-separated). Feeds for smaller coins
        are often generic crypto news, so tags are a hint, not the affected asset.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import pandas as pd

from trader.data.alt import _get, alt_path
from trader.data.store import read, upsert

YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
COLUMNS = ["guid", "published", "ts", "title", "link", "tags"]


def headlines_path():
    return alt_path("news_headlines")


def yahoo_ticker(symbol: str, overrides: dict | None = None) -> str:
    if overrides and symbol in overrides:
        return overrides[symbol]
    return symbol.replace("/", "-")          # BTC/USD -> BTC-USD


def parse_rss(xml: str | bytes, symbol: str) -> pd.DataFrame:
    """Items of one RSS feed as rows (ts, the first-seen time, is added by merge_new)."""
    rows = []
    for item in ET.fromstring(xml).iter("item"):
        title = (item.findtext("title") or "").strip()
        pub = item.findtext("pubDate")
        if not title or not pub:
            continue
        link = (item.findtext("link") or "").strip()
        published = pd.Timestamp(parsedate_to_datetime(pub))
        rows.append({"guid": (item.findtext("guid") or link or title).strip(),
                     "published": published.tz_localize("UTC") if published.tzinfo is None else published.tz_convert("UTC"),
                     "title": title, "link": link, "tags": symbol})
    return pd.DataFrame(rows, columns=["guid", "published", "title", "link", "tags"])


def merge_new(stored: pd.DataFrame, fetched: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """Rows to write: new headlines (ts = now) and known ones that gained a tag.
    A headline in several feeds becomes one row with all its tags."""
    if fetched.empty:
        return pd.DataFrame(columns=COLUMNS)
    tags = fetched.groupby("guid")["tags"].agg(lambda t: ",".join(sorted(set(t))))
    fresh = fetched.drop_duplicates("guid").set_index("guid").assign(tags=tags)
    known = stored.set_index("guid") if not stored.empty else pd.DataFrame(columns=COLUMNS).set_index("guid")
    out = []
    for guid, row in fresh.iterrows():
        if guid in known.index:
            old = known.loc[guid]
            merged = ",".join(sorted(set(old["tags"].split(",")) | set(row["tags"].split(","))))
            if merged != old["tags"]:
                out.append({**old.to_dict(), "guid": guid, "tags": merged})
        else:
            out.append({"guid": guid, "published": row["published"], "ts": now,
                        "title": row["title"], "link": row["link"], "tags": row["tags"]})
    return pd.DataFrame(out, columns=COLUMNS)


def update_headlines(symbols: list[str], now: pd.Timestamp, overrides: dict | None = None) -> int:
    frames = []
    for s in symbols:
        r = _get(YAHOO_RSS.format(ticker=yahoo_ticker(s, overrides)))
        if r.status_code == 200 and r.content.strip():
            frames.append(parse_rss(r.content, s))
    fetched = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    rows = merge_new(read(headlines_path()), fetched, now)
    return upsert(headlines_path(), rows, key=["guid"]) if len(rows) else 0
