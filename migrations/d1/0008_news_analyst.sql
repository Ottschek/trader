-- D1 schema v8: LLM news analyst shadow experiment (written by the hourly collector).
CREATE TABLE IF NOT EXISTS news_runs (
  ts            TEXT PRIMARY KEY,
  model         TEXT NOT NULL,
  headlines     INTEGER NOT NULL,   -- new headlines read this run
  flagged       INTEGER NOT NULL,   -- headlines Claude judged price-relevant
  signals       INTEGER NOT NULL,
  input_tokens  INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  cost_usd      REAL NOT NULL,
  status        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS news_signals (
  id          TEXT PRIMARY KEY,     -- decision time + symbol
  decided_at  TEXT NOT NULL,
  symbol      TEXT NOT NULL,
  side        INTEGER NOT NULL,     -- +1 long, -1 short
  score       INTEGER NOT NULL,
  headlines   TEXT NOT NULL,        -- JSON [{title, link, impact, reason}]
  entry_ts    TEXT,
  entry_px    REAL,
  ret_1d REAL, excess_1d REAL, net_1d REAL,   -- net = side x excess - round-trip costs
  ret_5d REAL, excess_5d REAL, net_5d REAL
);
CREATE INDEX IF NOT EXISTS news_signals_decided ON news_signals (decided_at);
