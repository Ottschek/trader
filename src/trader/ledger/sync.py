"""Mirror the ledger into D1 and report cards into R2, for the web app's Strategy lab.

The committed ledger file stays the source of truth; D1 is a copy the web app can query.
"""

from __future__ import annotations

import json
from pathlib import Path

from trader.ledger import ledger
from trader.monitor.d1 import D1

REPORT_PREFIX = "reports/"   # R2 key prefix; never pulled into data/ by the collector

COLUMNS = ["id", "created_at", "idea", "strategy", "symbols", "benchmark", "verdict", "trials",
           "total_trials", "commit_sha", "dirty", "research_start", "research_end", "oos_sharpe",
           "oos_cagr", "oos_max_drawdown", "bench_sharpe", "p_value", "deflated", "gates", "grid",
           "folds", "holdout", "has_report"]


def _gates(gates: list[dict]) -> list[dict]:
    """Gate results as real booleans (early entries stored numpy bools as "True"/"False")."""
    return [{**g, "passed": g["passed"] is True or str(g["passed"]).lower() == "true"} for g in gates]


def to_row(e: dict, has_report: bool = False) -> tuple:
    oos, bench = e.get("oos", {}), e.get("benchmark_oos", {})
    window = e.get("research_window") or [None, None]
    code = e.get("code", {})
    return (
        e["id"], e["created_at"], e["idea"], e["strategy"], json.dumps(e["symbols"]), e["benchmark"],
        e["verdict"], int(e["trials"]), int(e["total_trials"]), code.get("commit"), int(bool(code.get("dirty"))),
        window[0], window[1], oos.get("sharpe"), oos.get("cagr"), oos.get("max_drawdown"), bench.get("sharpe"),
        e.get("random_entry", {}).get("p_value"), e.get("deflated", {}).get("probability"),
        json.dumps(_gates(e["gates"])), json.dumps(e["grid"]), json.dumps(e.get("folds", [])),
        json.dumps(e["holdout"]) if e.get("holdout") else None, int(has_report),
    )


def sync_ledger(d1: D1, entries: list[dict] | None = None, reported: set[str] = frozenset(),
                only_missing: bool = True) -> int:
    """Upsert ledger entries into D1. With `only_missing`, skips ids already there."""
    entries = ledger.entries() if entries is None else entries
    if only_missing and entries:
        have = {r["id"] for r in d1.query("SELECT id FROM experiments")}
        entries = [e for e in entries if e["id"] not in have]
    if not entries:
        return 0
    return d1.insert_many("experiments", COLUMNS, [to_row(e, e["id"] in reported) for e in entries])


def upload_report(s3, bucket: str, card_id: str, html: str) -> str:
    key = f"{REPORT_PREFIX}{card_id}.html"
    s3.put_object(Bucket=bucket, Key=key, Body=html.encode(), ContentType="text/html; charset=utf-8")
    return key


def local_report(card_id: str, directory: Path) -> Path | None:
    return next(iter(sorted(directory.glob(f"*-{card_id}.html"))), None)
