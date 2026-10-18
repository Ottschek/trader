-- D1 schema v2: written by the Cloudflare Worker (cloudflare/scheduler).

-- Hyperliquid open interest, recorded on time every hour. The collector copies these
-- into the snapshots Parquet file in R2 on each run.
CREATE TABLE IF NOT EXISTS oi_snapshots (
  ts                  TEXT NOT NULL,   -- hour start, UTC
  taken_at            TEXT NOT NULL,
  coin                TEXT NOT NULL,
  open_interest       REAL NOT NULL,
  funding_rate        REAL NOT NULL,
  premium             REAL,
  mark_px             REAL NOT NULL,
  oracle_px           REAL NOT NULL,
  day_notional_volume REAL NOT NULL,
  PRIMARY KEY (ts, coin)
);

-- Small key/value store for the Worker (e.g. whether the watchdog alert is active).
CREATE TABLE IF NOT EXISTS scheduler_state (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
