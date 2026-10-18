"""The weekend plan: position sizes for the next weekend-gap trade, computed with the
strategy's own code from data up to the last stock close. The Cloudflare Worker reads the
latest plan from D1 at 09:00 New York and only decides *whether* to trade (the BTC move)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

from trader.backtest.data import MarketData, load_market
from trader.config import PROJECT_ROOT
from trader.data.store import bars_path, read
from trader.monitor.d1 import D1
from trader.strategies.ideas import WeekendGapSized

PAPER_CONFIG = PROJECT_ROOT / "config" / "paper.yaml"
LOOKBACK = pd.Timedelta(days=60)   # enough for the 30-day volatility window


def load_config(path: Path = PAPER_CONFIG) -> dict:
    return yaml.safe_load(path.read_text())


def compute_plan(data: MarketData, cfg: dict, now: pd.Timestamp) -> dict:
    exp = cfg["experiment"]
    strategy = WeekendGapSized(exp["symbols"], **exp["params"])
    sizes, vols, prices, last_bar = {}, {}, {}, None
    for s in exp["symbols"]:
        size, vol = strategy.size_series(data, s)
        size, vol = size.dropna(), vol.dropna()
        if size.empty:
            continue
        sizes[s] = round(float(size.iloc[-1]), 6)
        vols[s] = round(float(vol.iloc[-1]), 6)
        raw = read(bars_path("alpaca_stocks", s))      # real (unadjusted) price for share counts
        raw = raw[raw["session"] == "regular"] if "session" in raw else raw
        prices[s] = float(raw.sort_values("ts")["close"].iloc[-1]) if not raw.empty else None
        last_bar = max(last_bar, size.index[-1]) if last_bar is not None else size.index[-1]
    if last_bar is None:
        raise ValueError("no stock data to size the paper trade")
    return {
        "as_of": last_bar.isoformat(),
        "computed_at": now.isoformat(),
        "experiment": exp["id"],
        "params": exp["params"],
        "sizes": sizes,
        "ref_prices": prices,
        "daily_vol": vols,
    }


def build_plan(now: pd.Timestamp, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    exp = cfg["experiment"]
    data = load_market(crypto=[exp["signal_symbol"]], stocks=exp["symbols"], start=now - LOOKBACK)
    return compute_plan(data, cfg, now)


def save_plan(d1: D1, plan: dict) -> None:
    d1.insert_many(
        "paper_plans",
        ["as_of", "computed_at", "experiment", "params", "sizes", "ref_prices", "daily_vol"],
        [(plan["as_of"], plan["computed_at"], plan["experiment"], json.dumps(plan["params"]),
          json.dumps(plan["sizes"]), json.dumps(plan["ref_prices"]), json.dumps(plan["daily_vol"]))],
    )
