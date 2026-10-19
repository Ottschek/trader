"""Headline word-list score: the free stand-in for the LLM news analyst (forward-only SHADOW,
no orders, not a validated strategy). Rules frozen on 2026-10-19 before the first run.

Every `every_hours` (the analyst's setting), headlines first seen since the last run and published
within a day (data/news.py) are scored:
  - a headline counts for an asset only if it came from that asset's feed AND names it
    (ALIASES), so generic crypto news in a small coin's feed doesn't count for that coin
  - headline score = positive minus negative event phrases (POSITIVE / NEGATIVE), capped at +-2.
    Only news *events* count; price-move words ("surges", "falls") are left out on purpose:
    they describe moves that already happened
  - per asset, |sum of headline scores| >= `signal_threshold` is a long/short signal
Signals are scored exactly like the analyst's (news_analyst.score_signals): next open, against
SPY/BTC, after round-trip costs, at 1 and 5 days.

Files: data/alt/news_lex_signals.parquet; state.json["news_lexicon"] = {last_run, cutoff}.
"""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from trader.data import alt
from trader.data.news import headlines_path
from trader.data.store import read, upsert
from trader.paper import news_analyst as na

POSITIVE = [
    r"beats", r"beat (estimates|expectations|forecasts)", r"tops? (estimates|expectations|forecasts)", r"(raises|boosts|lifts|ups) (its )?(guidance|outlook|forecast)",
    r"record (revenue|sales|profit|earnings|quarter)", r"upgraded?", r"upgrades", r"outperform", r"buy rating",
    r"approv(al|ed|es)", r"wins? (a |an )?\w* ?(contract|deal|order)", r"buyback", r"share repurchase",
    r"(raises|hikes|boosts) (its )?dividend", r"breakthrough", r"strong demand", r"inflows?", r"bullish",
    r"partnership", r"expands? (deal|partnership)",
]
NEGATIVE = [
    r"miss(es|ed)", r"miss (estimates|expectations)", r"falls? short", r"(cuts|lowers|slashes|trims) (its )?(guidance|outlook|forecast)", r"warns?",
    r"profit warning", r"downgraded?", r"downgrades", r"underperform", r"sell rating", r"lawsuits?", r"sued",
    r"sues", r"probe", r"investigation", r"(sec|doj|ftc) (charges|sues|probe)", r"fraud", r"hack(ed|ers?)?",
    r"exploit(ed)?", r"outage", r"recalls?", r"layoffs?", r"job cuts", r"bankruptcy", r"defaults?", r"delist(ed|ing)?",
    r"outflows?", r"bearish", r"bann?(ed|s)?",
]
_POS = [re.compile(rf"\b{p}\b") for p in POSITIVE]
_NEG = [re.compile(rf"\b{p}\b") for p in NEGATIVE]

ALIASES = {
    "SPY": ["s&p 500", "s&p", "spy", "stock market", "wall street"], "QQQ": ["nasdaq", "qqq"],
    "IWM": ["russell 2000", "small caps", "small-cap", "iwm"], "TLT": ["treasury", "treasuries", "bond yields", "tlt"],
    "GLD": ["gold", "gld"], "AAPL": ["apple", "aapl", "iphone"], "MSFT": ["microsoft", "msft", "azure"],
    "NVDA": ["nvidia", "nvda"], "AMZN": ["amazon", "amzn", "aws"], "GOOGL": ["alphabet", "google", "googl"],
    "META": ["meta", "facebook", "instagram"], "TSLA": ["tesla", "tsla", "musk"], "JPM": ["jpmorgan", "jpm", "dimon"],
    "XOM": ["exxon", "xom"], "COIN": ["coinbase"], "MSTR": ["strategy", "microstrategy", "mstr", "saylor"],
    "MARA": ["mara", "marathon"], "RIOT": ["riot"], "BTC/USD": ["bitcoin", "btc"], "ETH/USD": ["ethereum", "ether", "eth"],
    "SOL/USD": ["solana", "sol"], "DOGE/USD": ["dogecoin", "doge"], "LTC/USD": ["litecoin", "ltc"],
    "AVAX/USD": ["avalanche", "avax"], "LINK/USD": ["chainlink"], "AAVE/USD": ["aave"],
    "BCH/USD": ["bitcoin cash", "bch"],
}


def signals_path():
    return alt.alt_path("news_lex_signals")


def headline_score(title: str) -> int:
    t = title.lower()
    score = sum(bool(p.search(t)) for p in _POS) - sum(bool(p.search(t)) for p in _NEG)
    return int(np.clip(score, -2, 2))


def mentions(title: str, symbol: str) -> bool:
    t = title.lower()
    return any(re.search(rf"(?<![\w$]){re.escape(a)}(?!\w)", t) for a in ALIASES.get(symbol, []))


def score_headlines(rows: pd.DataFrame, assets: list[str]) -> list[dict]:
    """(symbol, score, title, link) for each headline-asset pair that counts."""
    out = []
    for r in rows.itertuples():
        score = headline_score(r.title)
        if score == 0:
            continue
        for symbol in str(r.tags).split(","):
            if symbol in assets and mentions(r.title, symbol):
                out.append({"symbol": symbol, "score": score, "title": r.title, "link": r.link})
    return out


def signals_from_scores(scored: list[dict], now: pd.Timestamp, threshold: int) -> pd.DataFrame:
    by_symbol: dict[str, list[dict]] = {}
    for s in scored:
        by_symbol.setdefault(s["symbol"], []).append(s)
    out = []
    for symbol, items in sorted(by_symbol.items()):
        total = sum(i["score"] for i in items)
        if abs(total) < threshold:
            continue
        evidence = [{"title": i["title"], "link": i["link"], "impact": i["score"], "reason": "word list"} for i in items]
        out.append({"id": f"lex-{now:%Y%m%dT%H%M}-{symbol}", "decided_at": now, "symbol": symbol,
                    "side": int(np.sign(total)), "score": int(total), "headlines": json.dumps(evidence)})
    return pd.DataFrame(out, columns=["id", "decided_at", "symbol", "side", "score", "headlines"])


def run(settings, now: pd.Timestamp) -> str:
    cfg = settings.news or {}
    if not cfg or now < pd.Timestamp(cfg["start"], tz="UTC"):
        return "not started"
    state = alt.load_state()
    ls = state.setdefault("news_lexicon", {})
    if not na.due(ls, now, cfg["every_hours"]):
        return f"not due (last run {ls['last_run'][:16]})"
    cutoff = pd.Timestamp(ls["cutoff"]) if ls.get("cutoff") else now - pd.Timedelta(hours=cfg["every_hours"])
    rows = na.select_headlines(read(headlines_path()), cutoff, now)
    scored = score_headlines(rows, list(cfg["assets"])) if len(rows) else []
    signals = signals_from_scores(scored, now, cfg["signal_threshold"])
    if len(signals):
        upsert(signals_path(), signals, key=["id"])
    ls.update(last_run=now.isoformat(), cutoff=now.isoformat())
    alt.save_state(state)
    picks = ", ".join(f"{'long' if s.side > 0 else 'short'} {s.symbol} ({s.score:+d})" for s in signals.itertuples())
    return f"{len(rows)} headlines, {len(scored)} scored, {picks or 'no signals'}"
