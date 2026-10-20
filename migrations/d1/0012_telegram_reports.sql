-- D1 schema v12: archive of Telegram reports (/analise, /news, briefings); pictures in R2 under telegram/<id>.png.
CREATE TABLE IF NOT EXISTS telegram_reports (
  id         TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  kind       TEXT NOT NULL,      -- stock | coin | news | briefing
  title      TEXT NOT NULL,
  caption    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS telegram_reports_created ON telegram_reports (created_at);
