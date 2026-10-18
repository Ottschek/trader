"""Put a strategy idea through the full validation funnel and record it in the ledger.

Run:  .venv/bin/python scripts/validate.py sma --symbols BTC/USD
      .venv/bin/python scripts/validate.py sma --symbols BTC/USD -g fast=24,72 -g slow=480,1000
      .venv/bin/python scripts/validate.py random --symbols ETH/USD      # must always be rejected
      .venv/bin/python scripts/validate.py sma --symbols BTC/USD --holdout   # one-time look, candidates only

Every run is appended to ledger/experiments.jsonl (commit it) and counts toward the trial
total that the deflated Sharpe uses. An HTML report card is written to reports/.
"""

from __future__ import annotations

import argparse
import sys

from trader.config import load_settings
from trader.strategies.baselines import DipBuyer, RegimeFilter, TsMomentum
from trader.strategies.examples import RandomEntry, SmaCross
from trader.strategies.ideas import FundingContrarian, WeekendGap, WeekendGapSized
from trader.validation import report_html
from trader.validation.holdout import HoldoutLocked
from trader.validation.pipeline import validate

STRATEGIES = {"sma": SmaCross, "random": RandomEntry, "momentum": TsMomentum, "dip": DipBuyer,
              "regime": RegimeFilter, "weekend": WeekendGap, "weekend_sized": WeekendGapSized,
              "funding": FundingContrarian}


def _value(text: str):
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return text


def parse_grid(items: list[str]) -> dict | None:
    if not items:
        return None
    grid = {}
    for item in items:
        key, _, values = item.partition("=")
        grid[key] = [_value(x) for x in values.split(",")]
    return grid


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("strategy", choices=sorted(STRATEGIES))
    p.add_argument("--symbols", required=True)
    p.add_argument("-g", "--grid", action="append", default=[], help="parameter values to try, e.g. fast=24,72")
    p.add_argument("--benchmark")
    p.add_argument("--train-months", type=int, help="override walk-forward training window (recorded)")
    p.add_argument("--test-months", type=int, help="override walk-forward test window (recorded)")
    p.add_argument("--holdout", action="store_true", help="use this idea's one look at the locked holdout")
    p.add_argument("--no-record", action="store_true", help="don't write to the ledger (for debugging only)")
    args = p.parse_args()

    cls = STRATEGIES[args.strategy]
    try:
        card = validate(cls, args.symbols.split(","), load_settings(), grid=parse_grid(args.grid),
                        benchmark=args.benchmark, holdout=args.holdout, record=not args.no_record,
                        train_months=args.train_months, test_months=args.test_months)
    except HoldoutLocked as e:
        print(f"✗ Holdout refused: {e}")
        return 3

    print(f"{card.strategy} on {', '.join(card.symbols)}: {card.verdict.upper()}")
    for g in card.gates:
        print(f"  {'✓' if g.passed else '✗'} {g.label:22} {g.value:>24}   needs {g.threshold}")
    if card.holdout:
        h = card.holdout
        print(f"  holdout: Sharpe {h['metrics']['sharpe']:.2f} vs hold {h['benchmark']['sharpe']:.2f}, "
              f"max drawdown {h['metrics']['max_drawdown']:.1%} -> {'PASSED' if h['passed'] else 'FAILED'}")
    print(f"\nTrials this run: {card.trials} · total in ledger: {card.total_trials}")
    path = report_html.write(card)
    print(f"Report card: {path}")
    if not args.no_record:
        publish(card, path)
    print("Commit ledger/experiments.jsonl to keep the record.")
    return 0


def publish(card, path) -> None:
    """Upload the report card to R2 and the ledger entry to D1 for the web app's Strategy lab.
    Optional: if the cloud keys aren't set up, the local ledger and report still exist."""
    try:
        from trader.data import r2
        from trader.ledger import sync
        from trader.monitor.d1 import D1

        cloud = load_settings().cloud
        sync.upload_report(r2.client(), cloud.r2_bucket, card.id, path.read_text())
        sync.sync_ledger(D1(cloud.d1_database_id), [card.ledger_entry()], reported={card.id}, only_missing=False)
        print("Published to the Strategy lab: https://trader-web-6t6.pages.dev/#/lab")
    except Exception as e:  # noqa: BLE001 - publishing is best-effort
        print(f"(Not published to the web app: {type(e).__name__}: {e})")


if __name__ == "__main__":
    sys.exit(main())
