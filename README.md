# trader

Paper-money research system for US stocks and crypto on hourly data.
Research only — see [docs/plan.md](docs/plan.md) for the full plan and the rules a strategy must pass.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env   # then put your Alpaca PAPER keys in .env
```

## Commands

```bash
.venv/bin/pytest                          # run tests
.venv/bin/python scripts/check_account.py # M0: log into the paper account, print balance
.venv/bin/python scripts/collect.py       # M1: backfill / hourly update of all data + quality checks
.venv/bin/python scripts/run_backtest.py sma --symbols BTC/USD -p fast=72 -p slow=480   # M2: backtest vs buy-and-hold
.venv/bin/python scripts/validate.py sma --symbols BTC/USD    # M3: full validation, logged in the ledger
```

## Data

| Source | What | Since |
|---|---|---|
| Coinbase (ccxt) | hourly spot crypto bars — used for signals | 2016 or listing date |
| Alpaca crypto | hourly spot crypto bars — used for fill prices | 2021 |
| Alpaca stocks | 30-min raw bars → session-tagged hourly bars (pre / regular / post) | 2016 |
| Alpaca corporate actions | splits and dividends, applied on read | 2016 |
| Hyperliquid | funding rate history + hourly open interest snapshots | May 2023 / from now |

Stored as Parquet under `data/` (git-ignored), queryable with DuckDB via `trader.data.store.connect()`.

## Cloud

- **GitHub Actions** runs `scripts/collect.py --cloud` twice an hour (:07 and :37 UTC): pull data from the private
  R2 bucket `trader-data` → collect → quality checks → record in D1 → push changed files → Telegram alert if the
  set of failing checks changes. GitHub often starts these late; missed bars are caught up on the next run.
- **Cloudflare Worker** (`cloudflare/scheduler`) does the time-critical jobs on Cloudflare's clock: records
  Hyperliquid open interest every hour into D1, sends the Telegram summary at 08:00 and 22:30 Vienna time, and
  alerts if the collector hasn't run for 6 hours. Deploy with `npx wrangler deploy` in that folder.
- **Web app** (`web/`): public, read-only data health page at https://trader-web-6t6.pages.dev.
  Deploy with `npm run deploy` in `web/`.

Setup: fill in `.env` (see `.env.example`), check it with `.venv/bin/python scripts/check_setup.py`,
then upload the same values to GitHub with `gh secret set -f .env`.

Note: GitHub pauses scheduled workflows in public repos after 60 days without commits.

## Safety

- Only Alpaca **paper** keys (starting with `PK`) are accepted; the client is hard-coded to the paper endpoint.
- `.env` is git-ignored. Never commit keys or paste them anywhere.
