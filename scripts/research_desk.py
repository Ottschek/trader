"""Weekend run of the AI research desk (paper/research_desk.py): for every month-end since START
without a book yet, layer 1 picks 20 companies and the free AI writes a checked memo for each; then
both simulated portfolios (AI desk, rules only) and SPY are marked to market. Results go to D1.

Run:  .venv/bin/python scripts/research_desk.py            # local data, print only
      .venv/bin/python scripts/research_desk.py --cloud    # what the weekend workflow runs
"""

from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from trader.config import load_alpaca_secrets, load_settings
from trader.data import daily
from trader.paper import research_desk as rd
from trader.strategies.quality_value import QualityValue, month_end_decisions, universe_map

HISTORY_DAYS = 600     # price history before START: 20-day volume, public-float scaling, split checks


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cloud", action="store_true")
    args = p.parse_args()
    settings = load_settings()
    now = pd.Timestamp.now(tz="UTC")
    d1 = None
    if args.cloud:
        from trader.data import r2
        from trader.monitor.d1 import D1
        d1 = D1(settings.cloud.d1_database_id)
        bucket = settings.cloud.r2_bucket
        print(f"Pulling research data: {r2.pull(bucket, prefixes=('sec/fundamentals', 'sec/insider_purchases', 'daily/monthly_raw'))} files")
    symbols = sorted(set(universe_map()["symbol"]))
    secrets = load_alpaca_secrets()
    daily.update_monthly_raw(symbols + ["SPY"], (now - pd.Timedelta(days=70)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"), secrets)
    start = (rd.START - pd.Timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    print(f"Downloading daily prices {start} -> today for {len(symbols):,} companies")
    bars = daily.fetch_window(symbols + ["SPY"], start, now.strftime("%Y-%m-%d"), secrets)
    data = daily.load_daily(symbols + ["SPY"], bars=bars)
    strategy = QualityValue([s for s in symbols if s in data.close.columns], top_n=rd.TOP_N)

    books = [json.loads(b["book"]) for b in d1.query("SELECT book FROM research_books ORDER BY decided")] if d1 else []
    have = {b["decided"] for b in books}
    for row in month_end_decisions(data.index):
        day = data.index[row].tz_convert(rd.NY).tz_localize(None).normalize()
        if data.index[row] < rd.START or str(day.date()) in have or row == len(data.index) - 1 and day.date() == now.date():
            continue
        book = rd.make_book(data, row, strategy)
        books.append(book)
        counts = pd.Series([m["status"] for m in book["memos"]]).value_counts().to_dict()
        print(f"Book {book['decided']}: {len(book['ai'])}/{len(book['rules'])} bought by the AI desk; memos {counts}")
        if d1:
            d1.insert_many("research_books", ["decided", "book", "computed_at"], [(book["decided"], json.dumps(book), now.isoformat())])
            from trader.monitor import telegram
            telegram.send(f"📚 <b>Research desk</b> · month-end {book['decided']}\nLayer 1 picked {len(book['rules'])} companies; "
                          f"the AI wrote checked memos and bought {len(book['ai'])} "
                          f"({counts.get('veto', 0)} vetoed, {counts.get('rejected', 0)} memos rejected for unbacked claims).")
    eq = rd.simulate(books, data, settings)
    print(f"{len(books)} book(s); equity rows since {rd.START:%Y-%m-%d}: {len(eq)}")
    if len(eq):
        print(eq.tail(1).round(4).to_string())
    if d1 and len(eq):
        rows = [(ts.isoformat(), float(r.ai), float(r.rules), float(r.spy)) for ts, r in eq.iterrows()]
        d1.insert_many("research_equity", ["ts", "ai", "rules", "spy"], rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
