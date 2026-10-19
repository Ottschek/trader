"""Weekly run of the weekly_reversal forward shadow (paper/reversal_shadow.py).

Run:  .venv/bin/python scripts/reversal_shadow.py            # local daily universe list, print only
      .venv/bin/python scripts/reversal_shadow.py --cloud    # pull the universe list from R2, write D1
"""

from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from trader.config import load_alpaca_secrets, load_settings
from trader.data import daily, r2
from trader.paper import reversal_shadow as rs
from trader.strategies.reversal import WeeklyReversal


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cloud", action="store_true")
    args = p.parse_args()
    settings = load_settings()
    now = pd.Timestamp.now(tz="UTC")
    if args.cloud:
        print(f"Pulling the daily universe: {r2.pull(settings.cloud.r2_bucket, prefixes=('daily/',))} files")
    symbols = WeeklyReversal.default_universe()
    print(f"Downloading a fresh daily window {rs.window_start()} -> today for {len(symbols):,} symbols")
    bars = daily.fetch_window(symbols, rs.window_start(), now.strftime("%Y-%m-%d"), load_alpaca_secrets())
    data = daily.load_daily(symbols, bars=bars)
    print(f"  {len(data.symbols):,} symbols with data, last bar {data.index[-1]:%Y-%m-%d}")
    result = rs.simulate(data, settings)
    eq = result["equity"]
    print(f"Shadow since {rs.START:%Y-%m-%d}: {len(result['weeks'])} decision(s), "
          f"equity {eq.iloc[-1] if len(eq) else 1:.4f}")
    for w in result["weeks"][-3:]:
        week = "still open" if w["ret"] is None else f"{w['ret']:+.2%}"
        print(f"  {w['decided']}: {len(json.loads(w['longs']))} long / {len(json.loads(w['shorts']))} short, week {week}")
    if args.cloud:
        from trader.monitor.d1 import D1
        print(f"D1: {rs.save_to_d1(D1(settings.cloud.d1_database_id), result, now)} rows written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
