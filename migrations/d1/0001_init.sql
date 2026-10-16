-- D1 schema v1: collector runs, check results, per-dataset status (read by the web app).

CREATE TABLE IF NOT EXISTS collector_runs (
  run_at      TEXT PRIMARY KEY,          -- ISO timestamp, UTC
  status      TEXT NOT NULL,             -- ok | warn | fail
  n_ok        INTEGER NOT NULL,
  n_warn      INTEGER NOT NULL,
  n_fail      INTEGER NOT NULL,
  rows_added  INTEGER NOT NULL,
  duration_s  REAL NOT NULL,
  trigger     TEXT NOT NULL              -- schedule | manual | local
);

CREATE TABLE IF NOT EXISTS check_results (
  run_at   TEXT NOT NULL,
  check_name TEXT NOT NULL,
  dataset  TEXT NOT NULL,
  status   TEXT NOT NULL,
  detail   TEXT NOT NULL,
  PRIMARY KEY (run_at, check_name, dataset)
);
CREATE INDEX IF NOT EXISTS idx_check_results_status ON check_results (status, run_at);

-- One row per stored dataset, overwritten every run.
CREATE TABLE IF NOT EXISTS dataset_status (
  dataset    TEXT PRIMARY KEY,           -- e.g. 'bars/coinbase/BTC-USD'
  kind       TEXT NOT NULL,              -- bars | funding | snapshots | corporate_actions
  source     TEXT NOT NULL,
  symbol     TEXT,
  rows       INTEGER NOT NULL,
  first_ts   TEXT,
  last_ts    TEXT,
  updated_at TEXT NOT NULL
);
