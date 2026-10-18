"""Backtest one strategy against buy-and-hold, with costs and the lookahead check.

Run:  .venv/bin/python scripts/run_backtest.py sma --symbols BTC/USD -p fast=24 -p slow=168
      .venv/bin/python scripts/run_backtest.py hold --symbols ETH/USD --start 2021-01-01
      .venv/bin/python scripts/run_backtest.py peek --symbols BTC/USD     # the deliberate cheat

Uses the local data/ folder (pull it from R2 first if it's empty).
"""

from __future__ import annotations

import argparse
import sys

from trader.backtest.runner import backtest
from trader.config import load_settings
from trader.strategies.baselines import DipBuyer, RegimeFilter, TsMomentum
from trader.strategies.examples import BuyAndHold, PeekAhead, SmaCross
from trader.strategies.ideas import FundingContrarian, WeekendGap

STRATEGIES = {"hold": BuyAndHold, "sma": SmaCross, "peek": PeekAhead, "momentum": TsMomentum,
              "dip": DipBuyer, "regime": RegimeFilter, "weekend": WeekendGap, "funding": FundingContrarian}


def parse_param(text: str):
    key, _, value = text.partition("=")
    for cast in (int, float):
        try:
            return key, cast(value)
        except ValueError:
            pass
    return key, value


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("strategy", choices=sorted(STRATEGIES))
    p.add_argument("--symbols", required=True, help="comma-separated, e.g. BTC/USD,ETH/USD or SPY,QQQ")
    p.add_argument("-p", "--param", action="append", default=[], help="strategy parameter, e.g. fast=24")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--benchmark", help="override the buy-and-hold benchmark symbol")
    args = p.parse_args()

    strategy = STRATEGIES[args.strategy](args.symbols.split(","), **dict(parse_param(x) for x in args.param))
    report = backtest(strategy, load_settings(), start=args.start, end=args.end, benchmark=args.benchmark)

    if report.honest:
        print("✓ Lookahead check passed: decisions never changed when future data was added.\n")
    else:
        print("✗ LOOKAHEAD DETECTED: this strategy uses future data. The numbers below are INVALID.")
        for problem in report.lookahead_problems[:3]:
            print(f"  - {problem}")
        print()
    print(report.table())
    print("\nCosts are charged on every trade; fills happen at the next bar's open.")
    return 0 if report.honest else 2


if __name__ == "__main__":
    sys.exit(main())
