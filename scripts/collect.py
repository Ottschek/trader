"""Hourly collector. The same command does the first backfill and every update after:
each source resumes from its last stored bar (or from settings.history_start).

Run:  .venv/bin/python scripts/collect.py
      .venv/bin/python scripts/collect.py --sources coinbase,hyperliquid --symbols BTC/USD
      .venv/bin/python scripts/collect.py --cloud    # what GitHub Actions runs every hour

Locally: exit code 1 if any quality check fails.
With --cloud: pulls data from R2 first, then records the run in D1, pushes changed files
back to R2 and sends a Telegram alert when the set of failing checks changes. Failing
checks are reported via Telegram, so the exit code is only non-zero if the run itself breaks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import pandas as pd

from trader.config import ConfigError, load_alpaca_secrets, load_settings
from trader.data import alpaca_data, alt, coinbase, hyperliquid
from trader.data import quality as q
from trader.data.store import bars_path, data_dir, funding_path, read, snapshots_path

ALL_SOURCES = ["coinbase", "alpaca_crypto", "hyperliquid", "alpaca_stocks", "alt"]


def coin_of(symbol: str) -> str:
    return symbol.split("/")[0]


def download(sources, crypto, stocks, settings, now, snapshot=True) -> tuple[list[q.CheckResult], int]:
    """Run every downloader; a failure in one symbol is recorded and the rest carry on.
    Returns the failures and the total number of rows added. `snapshot=False` skips our own
    open interest snapshot (in cloud mode the Cloudflare Worker records it on time)."""
    results: list[q.CheckResult] = []
    added = 0

    def run(dataset: str, fn, *args):
        nonlocal added
        t0 = time.time()
        try:
            n = fn(*args)
            added += n
            print(f"  {dataset}: +{n} rows ({time.time() - t0:.0f}s)")
            return n
        except Exception as e:
            print(f"  {dataset}: ERROR {type(e).__name__}: {e}")
            results.append(q.CheckResult("download", dataset, q.FAIL, f"{type(e).__name__}: {e}"[:300]))
            return None

    if "coinbase" in sources:
        print("Coinbase spot (hourly)")
        ex = coinbase.make_exchange()
        for s in crypto:
            run(f"coinbase {s}", coinbase.update, s, settings.history_start, now, ex)

    if "alpaca_crypto" in sources:
        print("Alpaca crypto (hourly)")
        for s in crypto:
            run(f"alpaca_crypto {s}", alpaca_data.update_crypto, s, settings.history_start, now)

    if "hyperliquid" in sources:
        print("Hyperliquid funding + open interest")
        for s in crypto:
            run(f"funding {coin_of(s)}", hyperliquid.update_funding, coin_of(s))
        if snapshot:
            run("hyperliquid snapshot", hyperliquid.snapshot, [coin_of(s) for s in crypto], now)

    if "alpaca_stocks" in sources:
        print("Alpaca stocks (30m -> session hourly) + corporate actions")
        try:
            secrets = load_alpaca_secrets()
        except ConfigError as e:
            print(f"  skipped: {e}")
            results.append(q.CheckResult("download", "alpaca_stocks", q.WARN, "skipped: no Alpaca paper keys yet"))
        else:
            for s in stocks:
                run(f"alpaca_stocks {s}", alpaca_data.update_stock, s, settings.history_start, now, secrets)
            run("corporate actions", alpaca_data.update_corporate_actions, list(stocks), settings.history_start, secrets)
    if "alt" in sources:
        print("Alternative data (stablecoins, Wikipedia attention, ETF flows, Polymarket, listings)")
        alt.collect_all(settings, now, run)
    return results, added


def run_checks(sources, crypto, stocks, now) -> list[q.CheckResult]:
    results: list[q.CheckResult] = []

    def bar_checks(source: str, symbol: str, max_age_h: float, spike: float, gaps: bool):
        name = f"{source} {symbol}"
        df = read(bars_path(source, symbol))
        if df.empty:
            results.append(q.CheckResult("staleness", name, q.FAIL, "no data"))
            return df
        results.extend([q.check_ohlc(df, name), q.check_duplicates(df, name),
                        q.check_spikes(df, name, spike), q.check_staleness(df, name, now, max_age_h)])
        if gaps:
            results.append(q.check_gaps(df, name))
        return df

    for s in crypto:
        cb = bar_checks("coinbase", s, 3, 0.25, True) if "coinbase" in sources else pd.DataFrame()
        al = bar_checks("alpaca_crypto", s, 3, 0.25, True) if "alpaca_crypto" in sources else pd.DataFrame()
        if not cb.empty and not al.empty:
            results.append(q.check_drift(al, cb, f"alpaca vs coinbase {s}"))

    if "hyperliquid" in sources:
        for s in crypto:
            df = read(funding_path(coin_of(s)))
            name = f"funding {coin_of(s)}"
            results.extend([q.check_duplicates(df, name), q.check_staleness(df, name, now, 3)] if not df.empty
                           else [q.CheckResult("staleness", name, q.FAIL, "no data")])
        snap = read(snapshots_path())
        results.append(q.check_staleness(snap, "hyperliquid snapshot", now, 2))

    if "alt" in sources:
        for name, (path, max_age) in alt.staleness_targets().items():
            df = read(path)
            results.append(q.check_staleness(df, f"alt {name}", now, max_age) if not df.empty
                           else q.CheckResult("staleness", f"alt {name}", q.FAIL, "no data"))
        checked = alt.load_state().get("listings_checked")
        age = (now - pd.Timestamp(checked)) / pd.Timedelta(hours=1) if checked else None
        results.append(q.CheckResult("staleness", "alt listings", q.OK if age is not None and age <= 3 else q.FAIL,
                                     f"pair lists checked {age:.1f}h ago" if age is not None else "never checked"))

    if "alpaca_stocks" in sources and any(bars_path("alpaca_stocks", s).exists() for s in stocks):
        for s in stocks:
            # stocks don't trade on weekends/holidays: allow up to 4 days without a bar
            bar_checks("alpaca_stocks", s, 96, 0.15, False)
    return results


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sources", default=",".join(ALL_SOURCES))
    p.add_argument("--symbols", help="comma-separated subset, e.g. BTC/USD,SPY")
    p.add_argument("--cloud", action="store_true", help="sync with R2, record in D1, alert via Telegram")
    args = p.parse_args()

    settings = load_settings()
    sources = args.sources.split(",")
    crypto, stocks = list(settings.crypto), list(settings.stocks)
    if args.symbols:
        wanted = set(args.symbols.split(","))
        crypto = [s for s in crypto if s in wanted]
        stocks = [s for s in stocks if s in wanted]

    now = pd.Timestamp.now(tz="UTC")
    t0 = time.time()
    print(f"Collector run {now:%Y-%m-%d %H:%M} UTC -> {data_dir()}")
    out = data_dir() / "checks" / "latest.json"

    if args.cloud:
        from trader.data import r2
        print(f"Pulling data from R2 bucket {settings.cloud.r2_bucket}")
        print(f"  {r2.pull(settings.cloud.r2_bucket, prefixes=r2.COLLECTOR_PREFIXES)} files downloaded")
        merge_worker_snapshots(settings)
    previous = json.loads(out.read_text())["results"] if out.exists() else None

    results, added = download(sources, crypto, stocks, settings, now, snapshot=not args.cloud)
    results += run_checks(sources, crypto, stocks, now)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"run_at": now.isoformat(), "status": q.worst(results),
                               "results": [r.to_dict() for r in results]}, indent=1))

    if args.cloud:
        news_analyst(settings, now)
        report_to_cloud(settings, now, results, previous, added, time.time() - t0)

    print("\nChecks:")
    for r in results:
        if r.status != q.OK:
            print(f"  {r.status.upper():4} {r.check:10} {r.dataset}: {r.detail}")
    counts = {s: sum(r.status == s for r in results) for s in (q.OK, q.WARN, q.FAIL)}
    print(f"  {counts[q.OK]} ok, {counts[q.WARN]} warn, {counts[q.FAIL]} fail -> overall {q.worst(results).upper()}")
    if args.cloud:
        return 0  # failing checks were reported via Telegram; only a crash should fail the job
    return 1 if q.worst(results) == q.FAIL else 0


def merge_worker_snapshots(settings) -> None:
    """Copy open interest snapshots the Cloudflare Worker stored in D1 into the Parquet file."""
    from trader.data.store import last_ts
    from trader.monitor.d1 import D1

    since = last_ts(snapshots_path())
    rows = D1(settings.cloud.d1_database_id).query(
        "SELECT * FROM oi_snapshots WHERE ts > ? ORDER BY ts", [since.isoformat() if since is not None else ""])
    print(f"  {hyperliquid.merge_worker_snapshots(rows)} open interest snapshots merged from the Worker")


def sync_ledger_to_d1(settings) -> None:
    """Copy committed ledger entries that D1 doesn't have yet (for the Strategy lab page)."""
    from trader.data import r2
    from trader.ledger import sync
    from trader.monitor.d1 import D1

    keys = r2.remote_index(r2.client(), settings.cloud.r2_bucket)
    reported = {k[len(sync.REPORT_PREFIX):-len(".html")] for k in keys if k.startswith(sync.REPORT_PREFIX)}
    n = sync.sync_ledger(D1(settings.cloud.d1_database_id), reported=reported)
    if n:
        print(f"Ledger: {n} new experiments synced to D1")


def save_paper_plan(settings, now) -> None:
    """Position sizes for the next paper weekend trade (read by the Worker at 09:00 New York)."""
    from trader.monitor.d1 import D1
    from trader.paper import plan

    try:
        p = plan.build_plan(now)
        plan.save_plan(D1(settings.cloud.d1_database_id), p)
        print(f"Paper plan as of {p['as_of'][:16]}: {p['sizes']}")
    except Exception as e:  # noqa: BLE001 - never let paper trading break data collection
        print(f"Paper plan not updated: {type(e).__name__}: {e}")


def reconcile_paper(settings, now) -> None:
    """Live vs backtest for the paper experiment; Telegram alert when a new decision mismatch appears."""
    from trader.monitor import telegram
    from trader.monitor.d1 import D1
    from trader.paper import reconcile

    try:
        r = reconcile.run_and_save(D1(settings.cloud.d1_database_id), settings, now)
        track = r["tracking_difference"]
        print(f"Paper vs backtest: {len(r['trades'])} weekend sessions, {len(r['mismatches'])} mismatches, "
              f"tracking difference {'n/a' if track is None else f'{track:+.2%}'}")
        if r["new_mismatches"]:
            telegram.send("⚠️ <b>Paper vs backtest mismatch</b> on " + ", ".join(r["new_mismatches"]) +
                          ": the live bot and the backtest disagreed about trading. See the Paper tab: "
                          "https://trader-web-6t6.pages.dev/#/paper")
    except Exception as e:  # noqa: BLE001 - never let paper trading break data collection
        print(f"Paper reconcile skipped: {type(e).__name__}: {e}")


def news_analyst(settings, now) -> None:
    """News shadows: the LLM analyst (needs a key) and the free word-list score, then score past signals."""
    from trader.paper import news_analyst as na
    from trader.paper import news_lexicon as lex
    from trader.paper import news_llama as llama

    for label, step, path in (("News analyst", na.run, na.signals_path()), ("News word list", lex.run, lex.signals_path()),
                              ("News free AI (Llama)", llama.run, llama.signals_path())):
        try:
            print(f"{label}: {step(settings, now)}")
            scored = na.rescore(settings, path=path)
            if len(scored):
                s = na.summary(scored)
                print(f"  {s['signals']} signals so far; 1-day net excess {s['1d']['mean_net']} over {s['1d']['n']} scored")
        except Exception as e:  # noqa: BLE001 - never let the news experiments break data collection
            print(f"{label} skipped: {type(e).__name__}: {e}")


def mirror_news(settings, now) -> None:
    from trader.monitor.d1 import D1
    from trader.paper import news_analyst as na

    try:
        na.mirror_to_d1(D1(settings.cloud.d1_database_id), now)
    except Exception as e:  # noqa: BLE001
        print(f"News analyst D1 mirror skipped: {type(e).__name__}: {e}")


def report_to_cloud(settings, now, results, previous, added, duration_s) -> None:
    from trader.data import r2
    from trader.monitor import report, telegram
    from trader.monitor.d1 import D1

    trigger = {"schedule": "schedule", "workflow_dispatch": "manual"}.get(os.environ.get("GITHUB_EVENT_NAME", ""), "local")
    print("Recording run in D1")
    report.record_run(D1(settings.cloud.d1_database_id), now, results, added, duration_s, trigger)
    print(f"Pushing changed files to R2: {r2.push(settings.cloud.r2_bucket)} uploaded")
    sync_ledger_to_d1(settings)
    save_paper_plan(settings, now)
    reconcile_paper(settings, now)
    mirror_news(settings, now)
    alert = report.failure_alert(results, previous)
    if alert:
        telegram.send(alert)
        print("Telegram alert sent")


if __name__ == "__main__":
    sys.exit(main())
