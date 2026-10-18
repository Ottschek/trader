-- D1 schema v3: the experiment ledger, mirrored from ledger/experiments.jsonl for the Strategy lab page.

CREATE TABLE IF NOT EXISTS experiments (
  id               TEXT PRIMARY KEY,
  created_at       TEXT NOT NULL,
  idea             TEXT NOT NULL,     -- strategy + symbols, regardless of parameters
  strategy         TEXT NOT NULL,
  symbols          TEXT NOT NULL,     -- JSON array
  benchmark        TEXT NOT NULL,
  verdict          TEXT NOT NULL,     -- rejected | candidate | holdout_passed | holdout_failed
  trials           INTEGER NOT NULL,
  total_trials     INTEGER NOT NULL,
  commit_sha       TEXT,
  dirty            INTEGER NOT NULL,
  research_start   TEXT,
  research_end     TEXT,
  oos_sharpe       REAL,
  oos_cagr         REAL,
  oos_max_drawdown REAL,
  bench_sharpe     REAL,
  p_value          REAL,
  deflated         REAL,
  gates            TEXT NOT NULL,     -- JSON array of {name, label, passed, value, threshold}
  grid             TEXT NOT NULL,     -- JSON
  folds            TEXT NOT NULL,     -- JSON array
  holdout          TEXT,              -- JSON or NULL
  has_report       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_experiments_created ON experiments (created_at);
