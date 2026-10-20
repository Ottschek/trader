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
- GitHub Actions runs the collector twice an hour (free on a public repo). Its cron often starts late; bars catch up.
- A Cloudflare Worker (`cloudflare/scheduler`) does the time-critical jobs on time: hourly open interest snapshot
  (into D1), the daily summaries, and a watchdog alert if the collector hasn't run for 6 hours.
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
  - M1: **Data health** ✅ — collector runs, gaps, failed checks, Alpaca vs Coinbase drift.
  - M3: **Strategy lab** ✅ (`#/lab`) — report cards, experiment ledger, idea funnel and graveyard.
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

### M2 — Backtester ✅ (built 2026-10-18)
Next-bar fills, per-venue fees and spread, metrics: return, Sharpe, max drawdown, turnover, always next to buy-and-hold.
✅ Done when a deliberately cheating strategy gets caught, and a simple strategy matches hand-calculated results.

How it works (`src/trader/backtest/`):
- **Timing:** a strategy decides target weights after bar *t* closes; trades fill at the asset's next bar *open*.
  Returns are split into gap (close→open, earned by the old position) and intra (open→close, earned by the new one).
  A stock target set while the market is closed fills at the next session's first bar.
- **Costs** per trade = commission + half the spread + slippage (crypto 32.5 bps, stocks 4 bps one way).
- **Stocks:** regular-session bars by default; splits and dividends applied *forward* in the gap return of the
  first bar on/after the ex-date, so no future corporate action leaks into earlier prices.
- **Crypto:** signals from Coinbase; fills use Alpaca returns where Alpaca has consecutive bars, else Coinbase.
- **Bad ticks** (spike-and-revert) are flattened before backtesting, so dip-buying strategies can't profit from them.
- **Lookahead check:** every strategy is re-run on history truncated at 12 points; if any past decision changes
  when later data is added, the result is flagged invalid (catches shift(-1) cheats and full-sample statistics).
- Run: `.venv/bin/python scripts/run_backtest.py sma --symbols BTC/USD -p fast=72 -p slow=480`

### M3 — Validation harness + ledger ✅ (built 2026-10-18)
Walk-forward, holdout locked in code, Monte Carlo, deflated Sharpe, standard report card, every run logged.
✅ Done when random-entry strategies get rejected every time. (30/30 on real BTC, ETH, SPY history; 10/10 in tests.)

How it works (`src/trader/validation/`, rules in `config/settings.yaml` → `validation`):
- **Locked holdout:** data from 2024-10-18 on is invisible to research code (`research_market`). An idea (strategy +
  symbols, regardless of parameters) gets one look, only after passing every gate, only with committed code.
- **Walk-forward:** tune parameters on the prior 2 years, judge on the next 6 months, roll forward to the holdout.
- **Gates:** honest (lookahead check for every parameter set) · out-of-sample Sharpe ≥ 1 · beats buy-and-hold ·
  max drawdown ≥ −20% · beats 95% of random-timing shuffles of itself · deflated Sharpe ≥ 0.95 counting all trials.
- **Ledger:** `ledger/experiments.jsonl`, one line per run including rejections (the graveyard), with git commit,
  parameters tried, gate results and every trial's Sharpe. Commit it after each run.
- **Report card:** HTML in `reports/` with verdict, gates, out-of-sample equity vs buy-and-hold, random-timing
  histogram and the parameters picked in each fold.
- Run: `.venv/bin/python scripts/validate.py sma --symbols BTC/USD` (add `--holdout` for the one-time look).

### M4 — First strategies ✅ (2026-10-18)
Baselines: trend, mean reversion, regime filter. Crazy ideas using existing data: weekend gap predictor, funding rate signal.
✅ Done when each has a report card (expect most to fail). **All five rejected** — the harness doing its job.

| Idea | Universe | OOS Sharpe | vs hold | Max DD | Random timing p | Deflated | Killed by |
|---|---|---|---|---|---|---|---|
| Trend (`ts_momentum`) | 10 coins | 0.67 | 0.68 | −62.9% | 0.112 | 0.22 | everything but honesty |
| Dip buying (`dip_buyer`) | 10 coins | 0.43 | 0.68 | −9.8% | 0.168 | 0.09 | Sharpe, hold, random, deflated |
| Regime filter (`regime_filter`) | SPY | 0.68 | 0.78 | −19.9% | 0.387 | 0.91 | Sharpe, hold, random, deflated |
| Weekend gap (`weekend_gap`) | COIN, MSTR, MARA, RIOT | 0.88 | 0.79 (SPY) | −25.7% | **0.002** | 0.56 | Sharpe, drawdown, deflated |
| Funding contrarian (`funding_contrarian`) | 10 coins, 6m/2m windows | 0.49 | 1.12 | −22.2% | 0.309 | 0.07 | everything but honesty |

Notes:
- **Weekend gap is the one real lead:** its timing beat 99.8% of random shuffles and it beat SPY, but returns
  were too volatile (Sharpe 0.88, drawdown −26%). Worth refining (e.g. position sizing by BTC move, fewer stocks).
- **Refinement (`weekend_gap_sized`, 2026-10-18):** diagnosis showed the bitcoin miners (MARA, RIOT) carried
  most of the risk and fading always lost. Fixed before testing: follow only, each stock sized to a target daily
  volatility. Result: OOS Sharpe **1.19**, beats SPY (0.79), max drawdown **−7.8%**, random timing p = 0.002 —
  5 of 6 gates — but **deflated Sharpe 0.71** with 29 trials → rejected, no holdout look. Not to be tweaked further
  on research data (that would be p-hacking); the honest next evidence is new, forward data.
- Trial count after M4: 25. Every new idea now has to clear a higher deflated-Sharpe bar.
- Engine fix found while building M4: decisions only fill once every bar they could see has closed (mixed
  crypto :00 / stock :30 bars), so no strategy can use a crypto bar that closes after the stock trade.

### M5 — Paper trading (built 2026-10-18; running as a SHADOW EXPERIMENT)
Hourly runner sends the best candidate's orders to Alpaca paper through the risk manager:
max 10% per position, stop for the day after −3%, kill switch at −15% total drawdown.
✅ Done when it runs 2 weeks without crashing and every order is explainable from the logs.

No strategy has passed every gate yet, so the pipeline runs **weekend_gap_sized as a shadow experiment**
(5 of 6 gates; failed deflated Sharpe). Purpose: genuinely new, forward out-of-sample evidence. Not a candidate.
- **Frozen rules:** `config/paper.yaml` (threshold 6%, target vol 2%, picked on pre-holdout data; start 2026-10-19).
- **Who does what:** the collector computes position sizes with the strategy's own code (`paper_plans` in D1);
  the Cloudflare Worker decides at **09:00 New York** whether BTC moved enough since the last stock close
  (Coinbase candles, as in the backtest), places **market-on-open** orders (fill 09:30, as in the backtest),
  exits at the next open, refreshes orders, snapshots equity hourly and runs the risk manager every 30 min.
- **Safety:** paper endpoint hard-coded, keys must start with `PK`, duplicate firings can't double-order,
  stale plans are skipped with an alert, Worker settings are tested against `config/paper.yaml`.
- **Telegram:** a ping for every decision, order and fill; `/status`, `/stop` (close + halt), `/resume`,
  accepted only from your chat via a secret webhook.
- **Web:** Paper tab (`#/paper`) with the shadow label, equity, plan, every decision and every order vs its
  sizing price.
- Known difference from the backtest: on a Monday holiday the live bot decides on Tuesday morning with
  Tuesday's BTC move (the backtest used Monday 08:00's).

### M6 — Monitoring ✅ (built 2026-10-18)
Web app gains live-vs-backtest comparison on top of the views added in M1–M5.
✅ Done when you can see at a glance whether live matches backtest.

- `src/trader/paper/reconcile.py`, run by every collector cloud run: re-runs the frozen strategy (with the bot's
  10% risk cap) through the backtest engine over the shadow period and compares **equity** (paper vs expected,
  tracking difference), **decisions** (each weekend: did both trade or both stay out?) and **fills** (fill price
  vs the 09:30 open the backtest assumed, in bps). Stored in D1 `paper_reconcile`.
- **Telegram alert** when a new decision mismatch appears (each mismatch alerts once).
- **Paper tab:** paper equity vs backtest chart, live-vs-backtest stats and a weekend-by-weekend table.
- **Keep-alive** (`.github/workflows/keepalive.yml`): re-enables the scheduled collector twice a month so GitHub's
  60-day inactivity pause can't silently stop it; the Worker's watchdog is the backstop.

### M7+ — The big stuff
Strategy evolution ("Darwin"), LLM news analyst, AI trading desk, perps on a futures testnet,
insider and hype data, more crazy ideas — all through the same funnel.

### M7 progress (2026-10-18/19)
- **Alternative data collectors** (forward + history where free): DefiLlama stablecoin supply (2017–), Wikipedia
  attention (2016–), Farside spot BTC ETF flows (2024–), hourly Polymarket top-100 snapshots, Coinbase/Kraken
  new-pair detection.
- **Weekly Telegram report** (Sunday 20:00 Vienna) from the Worker.
- **Data cleaning fix:** only thin-volume spike-and-revert bars are flattened (the old rule wrongly erased the real
  May 2021 crash and the Jan 2021 DOGE frenzy); Alpaca crypto bars that disagree with Coinbase by > 5% fall back
  to Coinbase. **Capacity estimates** on report cards (1% of 20-day average daily dollar volume).
- **combo_sleeves** (weekend gap + SPY regime + crypto trend): rejected — OOS Sharpe 0.84, drawdown −28.8%.
- **Darwin** (genetic algorithm, 2000 genomes bred on 2016–2017, all counted as trials): in-sample Sharpe 4.20 →
  out-of-sample 0.89, drawdown −60.6%, deflated Sharpe 0.02 → rejected. The textbook overfitting demonstration.
- **Effective trials** (decided before the SEC results): the deflated Sharpe counts N_eff = ρ + (1 − ρ)·N per
  batch. Darwin's ρ was measured on a sample of all 2000 genomes it evaluated (0.77 → 464), not its converged
  survivors (0.99 → 18, too lenient). Ledger: ~2050 raw → ~486 effective trials.
- **Insider clusters** (275,520 SEC Form 4 purchases 2016–2026, 8,111 stocks, daily prices for 90% of purchases):
  rejected — OOS Sharpe 0.33 vs SPY 0.76, drawdown −57.8%, but timing p = 0.002 (real information, not enough
  return after small-cap costs and crashes).
- **Post-earnings drift** (67,194 8-K item 2.02 releases, 1,159 companies; survivorship-biased universe):
  rejected — Sharpe 0.42, p = 0.956 (no edge). Two runs before the clean one exposed real strategy bugs via the
  lookahead check (release cutoff at the bar's start instead of its close; unstable tie-break between
  simultaneous signals); both stay in the ledger.
- **Carry simulation** (instead of the Hyperliquid testnet, which needs a real mainnet deposit): the Worker runs an
  always-on cash-and-carry on $100k virtual since 2026-10-19 08:00 UTC: Alpaca spot at the real ask, Hyperliquid
  perp at the real impact bid, real funding, both venues' fees, margin re-hedge at 80%. Shown next to the
  backtest model so the reality gap (spreads, cross-venue basis) is visible.
- **stablecoin_printer** (p = 0.585) and **attention_filter** (p = 0.735): rejected, no timing information.
- **insider_cluster_sized** (same signals, 2% daily-volatility sizing): rejected — Sharpe −0.19; sizing cut the
  drawdown (−36.6% vs −57.8%) but not the problem; timing p = 0.034.
- **Monthly refresh** (GitHub Actions, 3rd of each month): new SEC insider quarters, recent 8-K releases and daily
  prices; the hourly collector only pulls its own folders from R2. The SEC moved quarters from 2026q2 on to a new
  folder; the downloader tries both.
- **LLM news analyst** (forward-only shadow, rules frozen 2026-10-19): the collector stores Yahoo Finance headlines
  for 27 assets every hour (free). Every 6 hours Claude (Opus 5.5, low effort, structured output) flags headlines
  with an expected 1–5 day impact (−2..+2); per asset, |sum| ≥ 2 is a long/short signal, scored from the next
  open against SPY/BTC after round-trip costs at 1 and 5 days. Spend capped at $5/month in code (paced evenly).
  No backtest on purpose: the model would know what happened next. Runs once ANTHROPIC_API_KEY is set.
  Decision 2026-10-19: no paid API key, so the analyst stays built but dormant.
- **News word-list score** (free forward shadow, rules frozen 2026-10-19): the same headlines and schedule, scored
  with fixed lists of positive/negative news-event phrases (beats/misses, guidance, upgrades, lawsuits, hacks...;
  price-move words left out). A headline counts for an asset only if it came from that asset's feed and names it.
  Same signal threshold and forward scoring as the analyst.
- **Collector kick:** GitHub skipped hourly runs for up to 4 hours at a time, so the Worker (reliable 30-minute
  clock) starts the collector through GitHub's API when the last run is over 50 minutes old and none is queued.
  Needs a fine-grained token (GITHUB_DISPATCH_TOKEN, this repo only, Actions read/write).
- **insider_cluster_large** (designed and committed before testing): the same clusters, but only in stocks trading
  >= $50M a day (20-day average known at the signal), with a large-cap cost profile (7 bps one way).
  Rejected: OOS Sharpe 0.08 vs SPY 0.76, drawdown −45.3%, timing p = 0.739. In liquid stocks the timing
  information is gone: the insider edge lives in small caps, where costs eat it.
- **weekly_reversal** (idea from a reel; designed and committed before testing): every Friday, long the 20-50 worst
  and short the 20-50 best liquid stocks (>= $50M a day, >= $5) over the last 5 or 20 days, 50/50, held a week,
  large-cap costs. Rejected, but the closest call so far: OOS Sharpe 0.78 (SPY 0.76), 17.4% a year, timing
  p = 0.004; fails Sharpe >= 1, drawdown (−29.3%) and the deflated Sharpe (0.26 at ~495 effective trials).
  Costs 4.2% a year; borrow fees not modeled.
- **weekly_reversal forward shadow** (simulation, fake money, no orders, from 2026-10-19): every weekend a GitHub
  workflow downloads a fresh daily window for the whole universe (one download, so splits can't create fake jumps),
  runs the committed strategy with the walk-forward's latest parameters (20-day lookback, 50 a side) and stores
  equity and weekly books in D1. Shown on the Paper tab against the backtest's 17.4% a year.
- **overnight_hold** (idea from a reel; designed and committed before testing): SPY/QQQ from the 15:30 bar to 10:30
  or 11:30 next day. Rejected: Sharpe −0.32, −6.7% a year, drawdown −47.5%. Costs were 20.1% a year (trading twice
  a day at 4 bps each way), so before costs it earned roughly +13% a year: the overnight effect is real, the cost
  of harvesting it daily is the problem. Whether 4 bps is too pessimistic for SPY can only be settled by real fills.
- **Free AI news scorer** (forward shadow, rules frozen 2026-10-19): the LLM analyst's prompt and signal rules run
  on Cloudflare Workers AI (Llama 3.3 70B) every 6 hours, ~165 of the free 10,000 daily neurons per run (the free
  Workers plan refuses calls over the allowance instead of billing). Strict JSON-schema mode returned empty lists,
  so the model answers in plain JSON and must quote the start of each flagged headline: flags whose quote doesn't
  match the headline they point to are repaired (unique match) or dropped (Llama sometimes mixes up ids).
- **agent_loop** (reel: "describe a strategy, backtest it, let an AI agent fix the losing trades"; designed and
  committed before running): start "hold BTC/ETH while 30-day momentum > 0 and price > 100-day average", then 20
  rounds where Llama 3.3 70B sees the 10 worst losing trades on 2016-2017 and rewrites the rule (Darwin's rule
  language), kept only if the training Sharpe improves. No proposal ever beat the start (2.75 in-sample): it kept
  adding filters that cut the winners along with the losers. The start rule then failed the six tests: OOS Sharpe
  0.96 (BTC 0.68), timing p = 0.038, drawdown −55.2%, deflated 0.00; 21 rules counted as trials.
- **turn_of_month** (reel on "who is forced to trade"; designed and committed before testing): SPY/QQQ/IWM from the
  last 1-2 trading days of a month to the 2nd-3rd of the next, NYSE holiday calendar (known in advance). Rejected:
  OOS Sharpe 0.00, timing p = 0.804. The effect documented in 1987-2008 studies isn't there in 2018-2024.
- **Test 5 for always-invested strategies** (committed before use): shuffling timing means nothing for a strategy
  that is always in the market, so strategies declare `always_on` by design and test 5 becomes "return above zero"
  (daily returns demeaned, resampled in 5-day blocks). Note: funding_harvest was NOT unfairly rejected: it does
  time its entries, and its timing was worse than random (p = 0.994).
- **carry_always_on: FIRST CANDIDATE, HOLDOUT PASSED.** Every carry pair all the time (what the carry simulation
  runs), designed after funding_harvest's timing failed. Research (2023-05 to 2024-10, 0.93 years out of sample):
  23.7% a year, Sharpe 25.4, drawdown −0.3%, p = 0.002, deflated 1.00. One holdout look (2024-10-18 to 2026-10-19,
  2 years): 8.7% a year, Sharpe 16.8, drawdown −0.3% (BTC: 10.3%, −54%). Caveats: the backtest charges almost no
  costs (it never trades after opening; real hedges need rebalancing and margin top-ups), cross-venue basis is
  only modelled from hourly data, and exchange/stablecoin failure risk isn't in any backtest. Next gate: the live
  carry simulation must track the model (Nov 19 review). No real money.
- **The brain** (scripts/brain.py, weekly workflow): the free AI (Llama 3.3 70B) invents strategies in a fixed rule
  language (strategies/brain.py: return, trend, volatility, RSI, volume, funding, BTC move, hour, weekday; no code);
  it sees the graveyard and its earlier ideas first. Each idea runs the six tests (recorded trial); passing all six
  triggers its one holdout look; Telegram reports who died and who survived. Two ideas a week, on purpose: every
  idea raises the deflated-Sharpe bar. Ideas live in ledger/brain_ideas.jsonl; the workflow commits ledger/ back,
  so pull before committing locally. First idea (funding_mean_reversion) died: Sharpe 0.29, timing p = 0.88.
- **Farside ETF flows stopped (2026-10-20):** since 2026-10-19 the site serves Cloudflare's bot challenge to every
  visitor, so every hourly run failed that download and Telegram reported it. Collection is switched off (no
  attempt to get around bot protection); history to 2026-10-16 stays. No strategy or experiment used it.
- **Paper decision bug (2026-10-19):** the first Monday decision crashed: at 13:00:19 UTC Coinbase hadn't yet
  published the 12:00-13:00 BTC candle, and the day was already marked as decided, so it never retried and left
  no record. Fixed: the BTC lookup retries for ~2 minutes, then falls back to Alpaca's BTC bar; any error is now
  recorded as a "skipped" decision with the reason (never silent). Monday was filled in afterwards as no_trade
  (BTC +1.26%, threshold +6%), with a note saying so; no order would have been placed either way.
- **quality_value (layer 1 of "research companies, invest with valid reasons")**: fundamentals from the SEC's bulk
  XBRL file, point-in-time by first filing date (fixes along the way: companies switch revenue tags; multi-class
  companies lack a share total, so diluted average shares with a public-float fallback; split correction via
  unadjusted/adjusted month closes). Monthly top 20/40 by quality + value + insider bonus, profitable and
  cash-generating only. Rejected: OOS Sharpe 0.65 (SPY 0.76), drawdown −43.0%, timing p = 0.627.
- **AI research desk (layer 2, forward shadow from 2026-10-20)**: each month-end, layer 1's top 20 get a Llama memo
  built only from an SEC fact sheet; every citation and every number is checked, an unbacked memo means no buy.
  "AI desk" (valid buys) vs "rules only" (all 20) vs SPY, $100k virtual each, on the Paper tab.
- Open: none of the M7 list; everything is either rejected or collecting forward evidence.

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
