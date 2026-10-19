-- D1 schema v10: weekly_reversal forward shadow (simulation, written by the weekend workflow).
CREATE TABLE IF NOT EXISTS reversal_equity (
  ts      TEXT PRIMARY KEY,
  equity  REAL NOT NULL          -- 1.0 at the start (2026-10-19), after costs
);
CREATE TABLE IF NOT EXISTS reversal_weeks (
  decided     TEXT PRIMARY KEY,  -- Friday (New York date) the book was chosen; filled Monday open
  longs       TEXT NOT NULL,     -- JSON [[symbol, past return]]: last period's losers
  shorts      TEXT NOT NULL,     -- JSON [[symbol, past return]]: last period's winners
  ret         REAL,              -- the week's return (NULL while the week is open)
  complete    INTEGER NOT NULL,
  cost        REAL,              -- trading cost at the Monday rebalance (fraction of equity)
  computed_at TEXT NOT NULL
);
