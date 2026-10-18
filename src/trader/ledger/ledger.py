"""The experiment ledger: every validation run ever, including the failures.

One JSON object per line in ledger/experiments.jsonl (committed to git, so history can't
be quietly rewritten). It provides:
  - the total number of trials, which the deflated Sharpe ratio needs
  - the exact code version of every result (git commit + whether the tree was dirty)
  - the holdout lock: an idea may look at the holdout only once
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from trader.config import PROJECT_ROOT


def ledger_path() -> Path:
    return Path(os.environ.get("TRADER_LEDGER_PATH", PROJECT_ROOT / "ledger" / "experiments.jsonl"))


def code_version() -> dict:
    """Current git commit and whether there are uncommitted changes to tracked code."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True).stdout.strip()
    commit = git("rev-parse", "HEAD") or None
    dirty = bool(git("status", "--porcelain", "--untracked-files=no", "--", "src", "scripts", "config"))
    return {"commit": commit, "dirty": dirty}


def idea_key(strategy_name: str, symbols) -> str:
    """An 'idea' is a strategy family on a set of symbols, regardless of its parameters:
    tweaking parameters after seeing the holdout must not buy a second look."""
    raw = json.dumps({"strategy": strategy_name, "symbols": sorted(symbols)}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def entries() -> list[dict]:
    path = ledger_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append(entry: dict) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")


def total_trials() -> int:
    """Raw count of every strategy ever tried."""
    return sum(int(e.get("trials", 0)) for e in entries())


def total_effective_trials() -> float:
    """Independent trials: each batch counts N_eff = rho + (1 - rho) * N (falls back to the raw count)."""
    return sum(float(e.get("effective_trials", e.get("trials", 0))) for e in entries())


def trial_sharpes() -> list[float]:
    return [s for e in entries() for s in e.get("trial_sharpes", [])]


def holdout_entry(key: str) -> dict | None:
    return next((e for e in entries() if e.get("idea") == key and e.get("holdout")), None)
