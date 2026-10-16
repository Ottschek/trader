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

## Safety

- Only Alpaca **paper** keys (starting with `PK`) are accepted; the client is hard-coded to the paper endpoint.
- `.env` is git-ignored. Never commit keys or paste them anywhere.
