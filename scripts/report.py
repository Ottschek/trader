"""Build a Telegram report and send it: /analise <company or coin>, /news, or a scheduled briefing.
Started by the Cloudflare Worker through .github/workflows/report.yml.

Run:  .venv/bin/python scripts/report.py --kind analise --query apple --dry-run   # picture saved, nothing sent
      .venv/bin/python scripts/report.py --kind news --when Morning --cloud       # what the workflow runs
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from trader.config import load_settings
from trader.reports import coin, deliver, news, stock
from trader.reports import data as rdata

CLOUD_PREFIXES = {"analise": ("sec/fundamentals", "sec/company_info", "sec/insider_purchases", "reports_cache/",
                              "funding/", "snapshots/", "alt/pageviews", "alt/stablecoins"),
                  "news": ()}


def analise(query: str, settings, out: Path, send, cloud: bool) -> int:
    from trader.data.sec import company_tickers
    target = stock.resolve(query, coin.coin_lookup(settings), company_tickers())
    if target["kind"] == "unknown":
        send(text=f"🔎 I couldn't find a US-listed company or tracked coin called <b>{query}</b>. Try its ticker, e.g. /analise AAPL.")
        return 0
    if target["kind"] == "ambiguous":
        send(text="🔎 Which one did you mean?\n" + "\n".join(f"• /analise {o.split(' ')[0]} {o[o.index(' '):]}" for o in target["options"]))
        return 0
    if target["kind"] == "coin":
        s = coin.gather(target["pair"], settings)
        comment, status = coin.ai_comment(coin.fact_sheet(s))
        path = coin.render(s, comment, status, out / "report.png")
        title = f"{s['name']} ({s['coin']})"
        caption = (f"<b>{title}</b> ${s['price']:,.2f} · {rdata_pct(s['d1'])} today\n1 year {rdata_pct(s['ret_1y'])} · "
                   f"{rdata_pct(s['drawdown'])} below the 1-year high · funding {rdata_pct(s['funding_7d'])}/year")
        send(path=path, caption=caption, kind="coin", title=title)
        return 0
    table, rebuilt = rdata.cached_universe_table()
    if rebuilt and cloud:
        upload(settings, rdata.universe_table_path(), "reports_cache/universe_table.parquet")
    s = stock.gather(target, table)
    comment, status = stock.ai_comment(stock.fact_sheet(target, s))
    path = stock.render(target, s, comment, status, out / "report.png")
    sc = s["scores"]
    title = f"{target['name']} ({target['symbol']})"
    caption = (f"<b>{title}</b> " + (f"${s['price']:,.2f}" if s.get("price") else "") + f" · 1 year {rdata_pct(s['ret_1y'])}\n"
               + " · ".join(f"{k.capitalize()} {('n/a' if v is None else f'{v:.0f}')}" for k, v in sc.items()) + " (of 100)")
    send(path=path, caption=caption, kind="stock", title=title)
    return 0


def rdata_pct(x) -> str:
    from trader.reports import render as R
    return R.pct(x)


def briefing(when: str, out: Path, send) -> int:
    b = news.gather()
    path = news.render(b, when, out / "report.png")
    send(path=path, caption=news.caption(b, when), kind="briefing" if when in ("Morning", "Evening") else "news",
         title=f"{when} market briefing")
    return 0


def upload(settings, path: Path, key: str) -> None:
    from trader.data import r2
    r2.client().put_object(Bucket=settings.cloud.r2_bucket, Key=key, Body=path.read_bytes())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--kind", choices=["analise", "news"], required=True)
    p.add_argument("--query", default="")
    p.add_argument("--when", default="Latest", help="Morning / Evening for the scheduled briefings")
    p.add_argument("--cloud", action="store_true", help="pull data from R2 first; archive the report")
    p.add_argument("--dry-run", action="store_true", help="save the picture, send nothing")
    args = p.parse_args()
    settings = load_settings()
    if args.cloud and CLOUD_PREFIXES[args.kind]:
        from trader.data import r2
        print(f"Pulling data: {r2.pull(settings.cloud.r2_bucket, prefixes=CLOUD_PREFIXES[args.kind])} files")
    out = Path(tempfile.mkdtemp())

    def send(text: str | None = None, path=None, caption: str = "", kind: str = "", title: str = ""):
        if args.dry_run:
            print(text or f"[{kind}] {title}: {path}\n{caption}")
            return
        if text:
            from trader.monitor import telegram
            telegram.send(text)
        else:
            deliver.deliver(path, caption, kind, title, deliver.new_id(), settings, archive=args.cloud)

    try:
        if args.kind == "analise":
            return analise(args.query.strip(), settings, out, send, args.cloud)
        return briefing(args.when, out, send)
    except Exception as e:  # noqa: BLE001 - always tell the user something went wrong
        if not args.dry_run:
            from trader.monitor import telegram
            telegram.send(f"⚠️ The report couldn't be built: {type(e).__name__}: {str(e)[:200]}")
        raise


if __name__ == "__main__":
    sys.exit(main())
