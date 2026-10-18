# Trader — research plan

Paper-money research system for **US stocks + crypto** on **hourly data**.
Research only until a strategy passes every rule in "Definition of profitable" below.

## Core principle

"Advanced" means *strict testing*, not clever strategies. Most hobby bots that "work"
are fooling themselves with one of these:

- **Overfitting** — tried 500 parameter combos, kept the luckiest one.
- **Lookahead bias** — the backtest used data it couldn't have had at that moment.
- **Survivorship bias** — only tested on assets that still exist today.
- **Ignoring costs** — fees, spread and slippage turn +8% into −3%.

The system is built so any idea can be plugged in and judged fairly.

## Definition of profitable (fixed now, no moving goalposts)

A strategy is only a candidate if it:

1. Beats buy-and-hold of a broad index ETF (and BTC for crypto strategies), risk-adjusted, **after costs**.
2. Passes walk-forward testing **and** the locked holdout (looked at once).
3. Has out-of-sample Sharpe > ~1 and max drawdown < ~20%.
4. Survives the deflated Sharpe check, given how many ideas have been tried in total.
5. Runs 3–6 months on paper with results consistent with the backtest.

## Architecture (6 layers)

| Layer | Job |
|---|---|
| **Data** | Download + store hourly bars (Parquet + DuckDB), quality checks, forward collector for funding/OI |
| **Strategies** | Plugins with one shape: data in → target positions out |
| **Backtest** | Fills at the *next* bar, realistic fees/spread/slippage per venue, metrics vs buy-and-hold |
| **Validation** | Walk-forward, locked holdout, Monte Carlo / random-entry tests, deflated Sharpe, experiment ledger |
| **Execution** | Broker adapters (Alpaca paper first), risk manager, kill switch, full decision log |
| **Monitoring** | Reports, dashboard, live-vs-backtest comparison |

Design rules:

- **Brain and hands are separate.** Strategies only output target positions; execution handles orders.
  Switching broker = swapping an adapter.
- **Shared core, small market adapters.** Stocks: market hours, splits, dividends.
  Crypto: 24/7, funding payments, different fees.
- **Portfolio-level risk** across both markets (crypto crashes drag crypto-linked stocks).
- **Experiment ledger**: every test ever run, with results and the exact code version.
- **Capacity awareness**: estimate how much capital a strategy can handle before the edge disappears.

## Tech stack (free)

- Python 3.12, pandas, Parquet (pyarrow) + DuckDB
- `alpaca-py` — paper trading + data for US stocks and spot crypto
- `ccxt` — crypto exchange data, futures testnet later
- `pytest` — including tests that prove the backtester can't cheat
- HTML report cards, dashboard later

## Decisions (settled 2026-10-16)

**Repo & visibility**
- Code lives in `~/trader` and the **public** GitHub repo `Ottschek/trader`. Code, report cards, ledger and paper results are public.
- Raw price data lives in a **private** Cloudflare R2 bucket (provider data terms don't allow republishing it).

**Data**
- History starts **2016** (max available). Each asset joins backtests when it started trading — the universe grows over time, no lookahead.
- **Stocks:** Alpaca hourly bars. Regular and extended hours are both stored, each bar tagged with its session.
  Prices are stored **raw** plus split/dividend events; adjustments are applied on read.
- **Crypto spot:** both Coinbase (via ccxt) and Alpaca, with a drift check between them.
  Signals use Coinbase prices; simulated fills are costed at Alpaca prices (the venue we paper trade on).
- **Funding rates + open interest:** Hyperliquid (public API, reachable from GitHub's US servers, unlike Binance/Bybit).
  Funding history is backfilled; open interest is recorded hourly going forward.
- M1 builds the historical backfill and the hourly collector together.

**Running it**
- GitHub Actions runs the hourly collector (free on a public repo). A Cloudflare Worker cron (`cloudflare/scheduler`)
  starts it every hour at :05 and the summaries on time, because GitHub's own cron started runs hours late.
- Telegram bot sends: failures and broken checks, a status summary at 08:00 and 22:30 Vienna time, and every paper trade (from M5).
  It also obeys `/stop` from your chat ID as a backup kill switch.

**Secrets** (never in chat, never in git): Alpaca paper keys, Cloudflare R2 + D1 credentials, Telegram bot token + chat ID,
GitHub token for triggering jobs. Each lives in local `.env`, GitHub Secrets, and/or Cloudflare secrets as needed.

## Web app

A public, read-only dashboard at https://trader-web-6t6.pages.dev, grown milestone by milestone.

- **Stack:** React (Vite) + TradingView Lightweight Charts, hosted on **Cloudflare Pages** (free `*.pages.dev` address).
  Clean light design, responsive for phone and desktop.
- **Login:** none (decided 2026-10-18). Anyone with the link can view; every request that changes something
  is refused. **Open for M5:** the control panel and kill switch cannot be public, so protecting them
  (Cloudflare Access, a password, or Telegram-only controls) gets decided when they are built.
- **Data:** after each hourly run the bot writes summaries (equity, trades, positions, data checks, report cards)
  to **Cloudflare D1**. Balances and positions are read **live** from Alpaca, so paper keys are also stored in Cloudflare.
  Alpaca has no read-only keys, so the server code only uses them for reads and the kill switch.
- **Controls:**
  - Turn strategies on/off and change risk limits. Changes go into a D1 control table that the bot reads at the start
    of every run; every change is logged.
  - Hard caps live in code; going above them needs an extra confirmation and is logged.
  - **Kill switch acts instantly**: cancels open orders and closes positions via Alpaca. Telegram `/stop` does the same.
  - Start backtests and jobs on demand by triggering GitHub Actions workflows.
- **Views, added as milestones land:**
  - M1: **Data health** — collector runs, gaps, failed checks, Alpaca vs Coinbase drift.
  - M3: **Strategy lab** — report cards, experiment ledger, idea funnel and graveyard.
  - M5: **Overview** (equity curve vs buy-and-hold, P&L, drawdown) and **Positions + trade log** with the reason for each trade.
  - M5: **Control panel** and kill switch.

## Folder structure

```
config/          settings: symbols, risk limits, fees per venue
src/trader/
  data/          downloaders, hourly collector, quality checks
  strategies/    one file per strategy (plugins)
  backtest/      simulation engine, costs, metrics
  validation/    walk-forward, holdout, Monte Carlo, overfitting checks
  execution/     broker adapters, risk manager, kill switch
  ledger/        log of every experiment
  monitor/       reports and dashboard
tests/
scripts/         run_hourly, run_backtest, make_report, check_account
docs/            this plan
```

## Milestones

Each has a "done when" test.

### M0 — Setup
Project skeleton, config, Alpaca paper account connection. Keys live in a local `.env`, never in chat or git.
✅ Done when `scripts/check_account.py` logs into the paper account and prints the fake balance.

### M1 — Data
Hourly history since 2016 for ~20 US stocks/ETFs (Alpaca) and ~10 cryptos (Coinbase + Alpaca), Hyperliquid funding history.
Hourly GitHub Actions collector writes to R2, records Hyperliquid open interest, runs quality checks
(gaps, splits, bad ticks, Coinbase vs Alpaca drift), writes summaries to D1 and alerts via Telegram.
Web app v1: data health page.
✅ Done when the database fills itself every hour, all checks pass, and the data health page shows it.

### M2 — Backtester
Next-bar fills, per-venue fees and spread, metrics: return, Sharpe, max drawdown, turnover, always next to buy-and-hold.
✅ Done when a deliberately cheating strategy gets caught, and a simple strategy matches hand-calculated results.

### M3 — Validation harness + ledger
Walk-forward, holdout locked in code, Monte Carlo, deflated Sharpe, standard report card, every run logged.
✅ Done when random-entry strategies get rejected every time.

### M4 — First strategies
Baselines: trend, mean reversion, regime filter. Crazy ideas using existing data: weekend gap predictor, funding rate signal.
✅ Done when each has a report card (expect most to fail).

### M5 — Paper trading
Hourly runner sends the best candidate's orders to Alpaca paper through the risk manager:
max 10% per position, stop for the day after −3%, kill switch at −15% total drawdown.
✅ Done when it runs 2 weeks without crashing and every order is explainable from the logs.

### M6 — Monitoring
Web app gains live-vs-backtest comparison on top of the views added in M1–M5.
✅ Done when you can see at a glance whether live matches backtest.

### M7+ — The big stuff
Strategy evolution ("Darwin"), LLM news analyst, AI trading desk, perps on a futures testnet,
insider and hype data, more crazy ideas — all through the same funnel.

## Idea backlog

Every idea goes: quick test → full walk-forward → paper → or the graveyard.

**Cross-asset (crypto ↔ stocks)**
- Weekend gap predictor — BTC's weekend move predicts Monday open for COIN, MSTR, miners.
- Bitcoin ETF flow signal — daily spot ETF inflows/outflows as a BTC signal.
- Risk-on/off regime switch — Hidden Markov Model over BTC, Nasdaq, volatility.

**Crypto-native**
- Funding rate harvesting — long spot, short perp, collect funding.
- Liquidation cascade hunter — ride forced-liquidation cascades, then the snap-back.
- Stablecoin printer — trade large USDT/USDC mint events.
- Whale watcher — large wallets moving coins to exchanges.
- Listing pump — measure how fast exchange-listing jumps fade.

**Alternative data (stocks)**
- Insider clusters — several insiders buying in one week (SEC Form 4).
- Post-earnings drift — surprises keep drifting for weeks.
- Hype velocity — acceleration of Reddit / Google Trends mentions.
- Prediction markets — Polymarket odds on macro events as signals.

**AI / machine-generated**
- Strategy evolution ("Darwin") — genetic algorithm + strict validation.
- LLM news analyst — score headlines/filings/calls, trade the scores (hard spending cap).
- AI trading desk — bull, bear and risk-officer agents; risk agent can veto.
- Reinforcement learning agent — mostly a learning experiment.

⚠️ AI ideas can't be honestly backtested on news from before the model's knowledge cutoff
(the model "knows the future"). Test them only on later news or forward in paper trading.

## Budget (30€ total)

- Everything runs on free tiers: GitHub Actions (public repo), Cloudflare Pages/R2/D1/Access, Telegram, Alpaca paper.
- A small server (~4–5€/month) only if GitHub Actions turns out too unreliable.
- Rest: buffer for small LLM costs or one month of better data if a strategy looks promising.

## Path to real money

A separate gate, not part of this project yet: months of consistent paper results → maybe a tiny
real amount → scale slowly. EU taxes and broker rules get looked at only then.
