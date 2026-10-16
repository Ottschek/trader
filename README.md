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
```

## Safety

- Only Alpaca **paper** keys (starting with `PK`) are accepted; the client is hard-coded to the paper endpoint.
- `.env` is git-ignored. Never commit keys or paste them anywhere.
