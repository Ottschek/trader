"""What the collector reports after each run: D1 rows for the web app, Telegram alerts and summaries."""

from __future__ import annotations

from html import escape

import pandas as pd

from trader.data import quality as q
from trader.data.store import data_dir
from trader.monitor.d1 import D1

KEEP_CHECK_DAYS = 30


# --- dataset status (what the data health page shows) ------------------------

def dataset_status_rows(now: pd.Timestamp) -> list[tuple]:
    """One row per stored Parquet file: kind, source, symbol, rows, first/last timestamp."""
    root = data_dir()
    rows = []
    for path in sorted(root.glob("**/*.parquet")):
        rel = path.relative_to(root).with_suffix("").as_posix()
        parts = rel.split("/")
        kind = parts[0]
        source = parts[1] if kind in ("bars", "funding") else ("hyperliquid" if kind == "snapshots" else "alpaca")
        symbol = parts[2] if kind in ("bars", "funding") else None
        ts = pd.read_parquet(path, columns=["ex_date" if kind == "corporate_actions" else "ts"]).iloc[:, 0]
        rows.append((rel, kind, source, symbol, len(ts),
                     ts.min().isoformat() if len(ts) else None,
                     ts.max().isoformat() if len(ts) else None,
                     now.isoformat()))
    return rows


def record_run(d1: D1, run_at: pd.Timestamp, results: list[q.CheckResult], rows_added: int,
               duration_s: float, trigger: str) -> None:
    run = run_at.isoformat()
    counts = {s: sum(r.status == s for r in results) for s in (q.OK, q.WARN, q.FAIL)}
    d1.insert_many("collector_runs",
                   ["run_at", "status", "n_ok", "n_warn", "n_fail", "rows_added", "duration_s", "trigger"],
                   [(run, q.worst(results), counts[q.OK], counts[q.WARN], counts[q.FAIL],
                     rows_added, round(duration_s, 1), trigger)])
    d1.insert_many("check_results", ["run_at", "check_name", "dataset", "status", "detail"],
                   [(run, r.check, r.dataset, r.status, r.detail) for r in results])
    d1.insert_many("dataset_status",
                   ["dataset", "kind", "source", "symbol", "rows", "first_ts", "last_ts", "updated_at"],
                   dataset_status_rows(run_at))
    cutoff = (run_at - pd.Timedelta(days=KEEP_CHECK_DAYS)).isoformat()
    d1.query("DELETE FROM check_results WHERE run_at < ?", [cutoff])


# --- alerts ------------------------------------------------------------------

def _fail_keys(results: list[dict]) -> set[tuple[str, str]]:
    return {(r["check"], r["dataset"]) for r in results if r["status"] == q.FAIL}


def failure_alert(results: list[q.CheckResult], previous: list[dict] | None) -> str | None:
    """Message to send, or None. Only alerts when the set of failures changes, so a
    problem that persists doesn't ping every hour; also says when everything recovered."""
    now_fails = _fail_keys([r.to_dict() for r in results])
    before = _fail_keys(previous or [])
    if now_fails == before:
        return None
    if not now_fails:
        return "✅ <b>Collector recovered</b>: all checks pass again."
    new = now_fails - before
    lines = [f"🚨 <b>Collector: {len(now_fails)} failing check(s)</b>" + (f", {len(new)} new" if before else "")]
    for r in results:
        if r.status == q.FAIL:
            mark = "🆕 " if (r.check, r.dataset) in new and before else "• "
            lines.append(f"{mark}{escape(r.check)} · {escape(r.dataset)}: {escape(r.detail)}")
    return "\n".join(lines[:40])


# --- daily summary -----------------------------------------------------------

def is_summary_time(now: pd.Timestamp, tz: str, times: tuple[str, ...], window_min: int = 50) -> str | None:
    """Which configured summary time (if any) we're within `window_min` minutes after.

    GitHub's cron only knows UTC and can start a few minutes late, so the summary
    workflow fires at both possible UTC hours and this decides which run sends."""
    local = now.tz_convert(tz)
    for t in times:
        hh, mm = map(int, t.split(":"))
        target = local.normalize() + pd.Timedelta(hours=hh, minutes=mm)
        if pd.Timedelta(0) <= local - target < pd.Timedelta(minutes=window_min):
            return t
    return None


def summary_text(d1: D1, now: pd.Timestamp, tz: str) -> str:
    since = (now - pd.Timedelta(hours=24)).isoformat()
    runs = d1.query("SELECT status, rows_added FROM collector_runs WHERE run_at >= ?", [since])
    latest = d1.query("SELECT run_at FROM collector_runs ORDER BY run_at DESC LIMIT 1")
    datasets = d1.query("SELECT kind, count(*) AS n, sum(rows) AS rows FROM dataset_status GROUP BY kind ORDER BY kind")

    local = now.tz_convert(tz)
    lines = [f"📊 <b>Trader status</b> · {local:%a %d %b %H:%M}"]
    if not runs:
        lines.append("⚠️ No collector runs in the last 24h!")
    else:
        failed = sum(r["status"] == q.FAIL for r in runs)
        added = sum(r["rows_added"] for r in runs)
        lines.append(f"Runs (24h): {len(runs)}/24 · {failed} with failures · +{added:,} rows")
    if latest:
        problems = d1.query("SELECT status, check_name, dataset, detail FROM check_results "
                            "WHERE run_at = ? AND status != 'ok' ORDER BY status, dataset",
                            [latest[0]["run_at"]])
        fails = [p for p in problems if p["status"] == q.FAIL]
        warns = [p for p in problems if p["status"] == q.WARN]
        lines.append(f"Latest run: {len(fails)} fail, {len(warns)} warn")
        for p in fails[:10]:
            lines.append(f"• ❌ {escape(p['check_name'])} · {escape(p['dataset'])}: {escape(p['detail'])}")
    for d in datasets:
        lines.append(f"{d['kind']}: {d['n']} files, {int(d['rows'] or 0):,} rows")
    return "\n".join(lines)
