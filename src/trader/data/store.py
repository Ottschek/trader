"""Local Parquet storage + DuckDB views.

Layout under the data directory (default: <project>/data, override with TRADER_DATA_DIR):

    bars/<source>/<SYMBOL>.parquet          price bars, one file per source and symbol
    funding/hyperliquid/<COIN>.parquet      funding rate history
    snapshots/hyperliquid_ctx.parquet       hourly open interest / mark price snapshots
    corporate_actions/alpaca.parquet        splits and dividends
    checks/latest.json                      result of the last quality check run

All timestamps are UTC and mark the *start* of the bar.
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pandas as pd

from trader.config import PROJECT_ROOT


def data_dir() -> Path:
    return Path(os.environ.get("TRADER_DATA_DIR", PROJECT_ROOT / "data"))


def safe_name(symbol: str) -> str:
    """'BTC/USD' -> 'BTC-USD' so it can be used as a file name."""
    return symbol.replace("/", "-")


def bars_path(source: str, symbol: str) -> Path:
    return data_dir() / "bars" / source / f"{safe_name(symbol)}.parquet"


def funding_path(coin: str) -> Path:
    return data_dir() / "funding" / "hyperliquid" / f"{coin}.parquet"


def snapshots_path() -> Path:
    return data_dir() / "snapshots" / "hyperliquid_ctx.parquet"


def corporate_actions_path() -> Path:
    return data_dir() / "corporate_actions" / "alpaca.parquet"


def read(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.exists() else pd.DataFrame()


def upsert(path: Path, new: pd.DataFrame, key: list[str]) -> int:
    """Merge `new` rows into the file at `path`; newer rows win on duplicate keys.

    Returns how many rows the file grew by. Writes atomically so a crash
    mid-write never leaves a half-written file behind.
    """
    if new.empty:
        return 0
    old = read(path)
    merged = new if old.empty else pd.concat([old, new], ignore_index=True)
    merged = merged.drop_duplicates(subset=key, keep="last").sort_values(key).reset_index(drop=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    merged.to_parquet(tmp, index=False)
    tmp.replace(path)
    return len(merged) - len(old)


def last_ts(path: Path, column: str = "ts") -> pd.Timestamp | None:
    if not path.exists():
        return None
    ts = pd.read_parquet(path, columns=[column])[column]
    return ts.max() if len(ts) else None


def connect() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with a view per dataset that exists on disk.

    Views: bars, funding, snapshots, corporate_actions.
    """
    con = duckdb.connect()
    root = data_dir()
    patterns = {
        "bars": "bars/*/*.parquet",
        "funding": "funding/*/*.parquet",
        "snapshots": "snapshots/*.parquet",
        "corporate_actions": "corporate_actions/*.parquet",
    }
    for view, pattern in patterns.items():
        if any(root.glob(pattern)):
            glob = (root / pattern).as_posix().replace("'", "''")
            con.execute(f"CREATE VIEW {view} AS SELECT * FROM read_parquet('{glob}', union_by_name=true)")
    return con
