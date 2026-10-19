-- D1 schema v7: carry shadow simulation (Cloudflare Worker, hourly). State lives in scheduler_state ('carry_sim').
CREATE TABLE IF NOT EXISTS carry_sim (
  ts             TEXT PRIMARY KEY,
  equity         REAL NOT NULL,   -- simulated account, marked at real prices on both venues
  model_equity   REAL NOT NULL,   -- what the backtest formula would have produced
  funding_cum    REAL NOT NULL,
  fees_cum       REAL NOT NULL,
  rebalances     INTEGER NOT NULL,
  max_margin_use REAL NOT NULL,
  basis          TEXT NOT NULL,   -- JSON {coin: perp mark / Alpaca mid - 1}
  funding        TEXT NOT NULL    -- JSON {coin: hourly funding rate}
);
