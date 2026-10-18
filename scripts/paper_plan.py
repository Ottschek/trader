"""Compute the paper-trading weekend plan from local data and save it to D1.

Run:  .venv/bin/python scripts/paper_plan.py          (the hourly collector does this too)
      .venv/bin/python scripts/paper_plan.py --dry   (print only)
"""

from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from trader.config import load_settings
from trader.monitor.d1 import D1
from trader.paper import plan


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true")
    args = p.parse_args()
    cfg = plan.load_config()
    result = plan.build_plan(pd.Timestamp.now(tz="UTC"), cfg)
    print(f"{cfg['experiment']['label']}\n{json.dumps(result, indent=1)}")
    if not args.dry:
        plan.save_plan(D1(load_settings().cloud.d1_database_id), result)
        print("Saved to D1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
