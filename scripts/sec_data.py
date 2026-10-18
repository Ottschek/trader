"""Download SEC data and the daily prices the SEC-based strategies need.

Run:  .venv/bin/python scripts/sec_data.py insider    # Form 4 purchases 2016-today + daily bars for those stocks
      .venv/bin/python scripts/sec_data.py earnings   # 8-K earnings releases + daily bars for the earnings universe
Needs SEC_CONTACT_EMAIL (SEC rule) and the Alpaca paper keys in .env.
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from trader.config import load_alpaca_secrets
from trader.data import daily, sec
from trader.data.store import read

START = "2016-01-01"


def insider(now: pd.Timestamp) -> None:
    print("Insider purchases (SEC quarterly Form 4 data sets):")
    sec.update_insider_purchases(now)
    ev = read(sec.sec_path("insider_purchases.parquet"))
    symbols = sorted(set(ev["symbol"].unique()) | {"SPY"})
    print(f"{len(ev):,} purchases in {ev['symbol'].nunique():,} stocks. Daily bars:")
    daily.update_daily_bars(symbols, START, now.strftime("%Y-%m-%d"), load_alpaca_secrets())
    bars = read(daily.daily_path())
    have = set(bars["symbol"].unique())
    covered = ev["symbol"].map(daily.alpaca_symbol).isin(have)
    print(f"Price coverage: {covered.mean():.0%} of purchases ({ev[covered]['symbol'].nunique():,} stocks). "
          "Missing ones are mostly delisted companies (survivorship bias: results may look better than reality).")


def earnings(now: pd.Timestamp, max_companies: int) -> None:
    from trader.strategies.earnings import earnings_universe
    companies = earnings_universe(max_companies)
    print(f"8-K earnings releases for {len(companies):,} companies:")
    sec.update_earnings_8k(companies, since=START)
    daily.update_daily_bars(sorted(set(companies["symbol"]) | {"SPY"}), START, now.strftime("%Y-%m-%d"), load_alpaca_secrets())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=["insider", "earnings"])
    p.add_argument("--max-companies", type=int, default=1500)
    args = p.parse_args()
    now = pd.Timestamp.now(tz="UTC")
    insider(now) if args.what == "insider" else earnings(now, args.max_companies)
    return 0


if __name__ == "__main__":
    sys.exit(main())
