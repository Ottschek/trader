-- D1 schema v4: paper trading (shadow experiment). Written by the collector (plans) and the
-- Cloudflare Worker (decisions, orders, equity, state); read by the web app's Paper tab.

-- Position sizes for the next weekend trade, computed by the strategy code after each stock close.
CREATE TABLE IF NOT EXISTS paper_plans (
  as_of           TEXT PRIMARY KEY,   -- start of the last stock bar used (UTC)
  computed_at     TEXT NOT NULL,
  experiment      TEXT NOT NULL,
  params          TEXT NOT NULL,      -- JSON
  sizes           TEXT NOT NULL,      -- JSON {symbol: weight of equity}
  ref_prices      TEXT NOT NULL,      -- JSON {symbol: last close}, to turn weights into share counts
  daily_vol       TEXT NOT NULL       -- JSON {symbol: daily volatility used}
);

-- Every 09:00 decision on a trading day, including "no trade".
CREATE TABLE IF NOT EXISTS paper_decisions (
  id              TEXT PRIMARY KEY,
  decided_at      TEXT NOT NULL,
  session_date    TEXT NOT NULL,      -- New York date of the session the orders fill in
  kind            TEXT NOT NULL,      -- entry | exit | no_trade | skipped | risk_close | manual_stop
  detail          TEXT NOT NULL,      -- JSON: BTC move, threshold, plan, sizes, reasons
  experiment      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_orders (
  id              TEXT PRIMARY KEY,   -- Alpaca order id
  client_order_id TEXT NOT NULL,
  decision_id     TEXT NOT NULL,
  submitted_at    TEXT NOT NULL,
  symbol          TEXT NOT NULL,
  side            TEXT NOT NULL,
  qty             REAL NOT NULL,
  order_type      TEXT NOT NULL,
  time_in_force   TEXT NOT NULL,
  ref_price       REAL,               -- price used for sizing
  status          TEXT NOT NULL,
  filled_qty      REAL,
  filled_avg_price REAL,
  filled_at       TEXT,
  updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_equity (
  ts              TEXT PRIMARY KEY,
  equity          REAL NOT NULL,
  cash            REAL NOT NULL,
  last_equity     REAL,               -- previous trading day's closing equity (from Alpaca)
  positions       TEXT NOT NULL       -- JSON [{symbol, qty, market_value, unrealized_pl}]
);

-- Key/value state: open_trade, halted, peak_equity (shares the scheduler_state table's shape).
CREATE TABLE IF NOT EXISTS paper_state (
  key             TEXT PRIMARY KEY,
  value           TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
