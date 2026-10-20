-- D1 schema v11: AI research desk shadow (layer 2 of quality_value), written by the weekend workflow.
CREATE TABLE IF NOT EXISTS research_books (
  decided     TEXT PRIMARY KEY,   -- month-end (New York date) the picks were made; filled next open
  book        TEXT NOT NULL,      -- JSON {decided, rules: [...], ai: [...], memos: [{symbol, sheet, status, memo, problems}]}
  computed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_equity (
  ts    TEXT PRIMARY KEY,
  ai    REAL NOT NULL,            -- AI desk portfolio (valid buy memos only), 1.0 at the start
  rules REAL NOT NULL,            -- layer 1 alone (all 20 picks)
  spy   REAL NOT NULL             -- holding SPY
);
