"""Daily Telegram status summary (08:00 and 22:30 Vienna time, see settings.cloud.summary_times).

GitHub's cron only knows UTC, so the workflow fires at both possible UTC hours and this
script only sends when it's actually summary time in Vienna. --force sends regardless.
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from trader.config import load_settings
from trader.monitor import report, telegram
from trader.monitor.d1 import D1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    cloud = load_settings().cloud
    now = pd.Timestamp.now(tz="UTC")
    slot = report.is_summary_time(now, cloud.timezone, cloud.summary_times)
    if not (slot or args.force):
        print(f"Not summary time in {cloud.timezone} ({now.tz_convert(cloud.timezone):%H:%M}); nothing sent.")
        return 0
    telegram.send(report.summary_text(D1(cloud.d1_database_id), now, cloud.timezone))
    print(f"Summary sent ({slot or 'forced'}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
