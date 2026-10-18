-- D1 schema v5: live vs backtest for the paper experiment (written by the collector, read by the Paper tab).
CREATE TABLE IF NOT EXISTS paper_reconcile (
  computed_at TEXT PRIMARY KEY,
  summary     TEXT NOT NULL      -- JSON from trader.paper.reconcile.reconcile()
);
