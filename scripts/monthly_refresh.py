"""Monthly refresh of the research data: new SEC insider quarters, recent 8-K earnings releases,
recent daily prices for every stock in the daily universe, company fundamentals (SEC bulk XBRL)
and unadjusted monthly closes (for market caps). Run by .github/workflows/monthly.yml.

Run:  .venv/bin/python scripts/monthly_refresh.py            (local data)
      .venv/bin/python scripts/monthly_refresh.py --cloud    (pull sec/ + daily/ from R2 first, push after)
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import pandas as pd

from trader.config import load_alpaca_secrets, load_settings
from trader.data import daily, fundamentals, r2, sec
from trader.data.store import read
from trader.strategies.earnings import earnings_universe
from trader.strategies.quality_value import universe_map

START = "2016-01-01"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cloud", action="store_true")
    args = p.parse_args()
    bucket = load_settings().cloud.r2_bucket
    now = pd.Timestamp.now(tz="UTC")
    if args.cloud:
        print(f"Pulling research data: {r2.pull(bucket, prefixes=r2.RESEARCH_PREFIXES)} files")
    print("SEC insider quarters:")
    sec.update_insider_purchases(now)
    print("SEC 8-K earnings releases (recent filings):")
    sec.update_earnings_8k(earnings_universe(1500), refresh=True)
    symbols = set(read(sec.sec_path("insider_purchases.parquet"))["symbol"]) | set(
        read(sec.sec_path("earnings_8k.parquet"))["symbol"]) | {"SPY"}
    print(f"Daily prices for {len(symbols):,} symbols:")
    daily.update_daily_bars(sorted(symbols), START, now.strftime("%Y-%m-%d"), load_alpaca_secrets(), refresh=True)
    print("Company fundamentals (SEC companyfacts.zip) and unadjusted monthly closes:")
    universe = universe_map()
    with tempfile.TemporaryDirectory() as tmp:
        fundamentals.update_fundamentals(earnings_universe(1500), Path(tmp) / "companyfacts.zip")
    daily.update_monthly_raw(sorted(set(universe["symbol"])) + ["SPY"], "2015-01-01", now.strftime("%Y-%m-%d"), load_alpaca_secrets())
    if args.cloud:
        print(f"Pushed {r2.push(bucket)} changed files to R2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
