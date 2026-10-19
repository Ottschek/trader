"""Try the LLM news analyst once, by hand: fetch fresh headlines, ask Claude, print what it flags
and what the call cost. Saves nothing and doesn't count toward the experiment (the hourly
collector runs the real thing in the cloud). One call costs a few US cents.

Run:  .venv/bin/python scripts/news_analyst.py            # headlines from the last 6 hours
      .venv/bin/python scripts/news_analyst.py --hours 12
"""

from __future__ import annotations

import argparse

import anthropic
import pandas as pd

from trader.config import secret, load_settings
from trader.data.news import merge_new, parse_rss, yahoo_ticker, YAHOO_RSS
from trader.data.alt import _get
from trader.paper import news_analyst as na


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=float, default=6)
    args = p.parse_args()
    cfg = load_settings().news
    now = pd.Timestamp.now(tz="UTC")
    frames = []
    for s in cfg["assets"]:
        r = _get(YAHOO_RSS.format(ticker=yahoo_ticker(s, cfg.get("yahoo_overrides"))))
        if r.status_code == 200 and r.content.strip():
            frames.append(parse_rss(r.content, s))
    fetched = merge_new(pd.DataFrame(), pd.concat(frames, ignore_index=True), now)
    fetched = fetched.assign(ts=fetched["published"])       # by hand: treat published as first seen
    rows = na.select_headlines(fetched, now - pd.Timedelta(hours=args.hours), now)
    print(f"{len(rows)} headlines from the last {args.hours:g} hours -> {cfg['model']} (effort {cfg.get('effort', 'low')})")
    client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))
    result = na.ask_claude(client, cfg, rows, now)
    for f in sorted(result["flags"], key=lambda f: (f["symbol"], -f["impact"])):
        print(f"  {f['symbol']:8} {f['impact']:+d}  {rows.at[f['id'], 'title'][:90]}\n           {f['reason']}")
    signals = na.signals_from_flags(result["flags"], rows, now, cfg["signal_threshold"])
    print(f"Status {result['status']}, model {result['model']}: {len(result['flags'])} flagged, signals: "
          + (", ".join(f"{'long' if s.side > 0 else 'short'} {s.symbol} ({s.score:+d})" for s in signals.itertuples()) or "none"))
    print(f"Tokens in/out {result['input_tokens']}/{result['output_tokens']} -> "
          f"${na.cost_usd(result['model'], result['input_tokens'], result['output_tokens']):.3f}")


if __name__ == "__main__":
    main()
