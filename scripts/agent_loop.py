"""Agent loop ("describe a strategy, backtest it, let an AI agent fix the losing trades"), from an
Instagram reel, tested honestly. Design fixed and committed on 2026-10-19 before the first run.

  1. Start from a plain-English rule (START_DESCRIPTION), written in Darwin's rule language
     (strategies/evolved.py): hold BTC and ETH while momentum / trend / volatility conditions hold.
  2. ROUNDS times: backtest the current best rule on 2016-2017 only (Darwin's evolution window),
     show the free AI (Llama 3.3 70B on Cloudflare Workers AI) the rule, its result and its 10 worst
     losing trades with the conditions at entry, and ask for one modified rule that avoids them.
     A proposal replaces the best rule only if its training-period Sharpe is higher (the reel's loop).
  3. The final rule goes through the six tests on 2018-2024, which the loop never saw. Every
     evaluated rule counts as a trial; the full transcript is committed (config/agent_loop.json).

Run:  .venv/bin/python scripts/agent_loop.py --run         # the loop (writes config/agent_loop.json)
      .venv/bin/python scripts/agent_loop.py --validate    # the six tests (recorded in the ledger)
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from trader.backtest.data import load_market
from trader.config import load_settings
from trader.paper.news_llama import MODEL, WorkersAIError, chat, extract_json
from trader.strategies.evolved import AGENT_POPULATION, GENE_SPACE, INT_GENES, AgentRule, rule_signal
from trader.strategies.helpers import once_a_day, value_ago
from trader.validation.montecarlo import annualized_sharpe, approx_returns, bar_returns

SYMBOLS = ["BTC/USD", "ETH/USD"]
TRAIN_START, TRAIN_END = "2016-01-01", "2017-12-31 23:59"      # the same window Darwin evolved on
ROUNDS = 20
START_DESCRIPTION = "Hold BTC and ETH while 30-day momentum is positive and price is above its 100-day average."
START_RULE = {"use_mom": 1, "mom_days": 30, "mom_min": 0.0, "use_sma": 1, "sma_days": 100, "sma_ratio": 1.0,
              "use_vol": 0, "vol_max": 0.05}
DAY = pd.Timedelta(days=1)

SYSTEM = f"""You improve a crypto trading rule. The rule holds BTC and ETH (half each) while ALL enabled
conditions are true, re-decided once a day, and is in cash otherwise:
- use_mom (0/1): momentum over mom_days ({GENE_SPACE['mom_days'][0]}-{GENE_SPACE['mom_days'][1]}) must be above mom_min ({GENE_SPACE['mom_min'][0]} to {GENE_SPACE['mom_min'][1]}, e.g. 0.05 = +5%)
- use_sma (0/1): price / its sma_days-day average ({GENE_SPACE['sma_days'][0]}-{GENE_SPACE['sma_days'][1]}) must be above sma_ratio ({GENE_SPACE['sma_ratio'][0]}-{GENE_SPACE['sma_ratio'][1]})
- use_vol (0/1): 30-day daily volatility must be below vol_max ({GENE_SPACE['vol_max'][0]}-{GENE_SPACE['vol_max'][1]})
At least one condition must be enabled. Answer with JSON only:
{{"rule": {{"use_mom": 1, "mom_days": 30, "mom_min": 0.0, "use_sma": 1, "sma_days": 100, "sma_ratio": 1.0, "use_vol": 0, "vol_max": 0.05}}, "why": "one sentence"}}"""


def clamp(rule: dict) -> dict | None:
    """A valid rule from the model's answer (values clipped to the rule language), or None."""
    try:
        g = {}
        for k, (lo, hi) in GENE_SPACE.items():
            v = float(rule[k])
            g[k] = int(round(np.clip(v, lo, hi))) if k in INT_GENES else float(np.clip(v, lo, hi))
    except (KeyError, TypeError, ValueError):
        return None
    for k in ("use_mom", "use_sma", "use_vol"):
        g[k] = 1 if g[k] else 0
    return g if (g["use_mom"] or g["use_sma"] or g["use_vol"]) else None


def holdings(g: dict, data) -> dict[str, pd.Series]:
    return {s: once_a_day(rule_signal(data.close[s].dropna(), g).astype(float), stock=False).reindex(data.index).ffill().fillna(0)
            for s in SYMBOLS}


def evaluate(g: dict, data, returns: np.ndarray, cost: np.ndarray, bpy: float) -> float:
    w = np.column_stack([h / len(SYMBOLS) for h in holdings(g, data).values()])
    if w.mean() < 0.05:            # never trading isn't a strategy (Darwin's rule)
        return -9.0
    return annualized_sharpe(approx_returns(w, returns, cost), bpy)


def losing_trades(g: dict, data, cost_per_side: float, n: int = 10) -> list[dict]:
    """The n worst holding periods (per coin) with the conditions at entry."""
    ret = bar_returns(data)
    out = []
    for s, h in holdings(g, data).items():
        held = h.to_numpy() > 0
        close = data.close[s]
        mom = close / value_ago(close, int(g["mom_days"] if g["use_mom"] else 30) * DAY) - 1
        sma = close / close.rolling(f"{int(g['sma_days'] if g['use_sma'] else 100)}D").mean()
        vol = np.log(close).diff().rolling("30D").std() * np.sqrt(24)
        i = 0
        while i < len(held):
            if not held[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(held) and held[j + 1]:
                j += 1
            r = float(np.prod(1 + ret[s].iloc[i + 1:j + 2].fillna(0).to_numpy()) - 1 - 2 * cost_per_side)
            t = data.index[i]
            out.append({"coin": s.split("/")[0], "entry": f"{t:%Y-%m-%d}", "exit": f"{data.index[j]:%Y-%m-%d}",
                        "return": round(r, 4), "momentum_at_entry": round(float(mom.iloc[i]), 3),
                        "price_vs_sma_at_entry": round(float(sma.iloc[i]), 3), "volatility_at_entry": round(float(vol.iloc[i]), 4)})
            i = j + 1
    return sorted((t for t in out if t["return"] < 0), key=lambda t: t["return"])[:n]


def run_loop(post=None) -> dict:
    s = load_settings()
    data = load_market(crypto=SYMBOLS, start=TRAIN_START, end=TRAIN_END)
    returns = bar_returns(data)[SYMBOLS].to_numpy()
    side = s.costs["alpaca_crypto"].one_way_bps / 1e4
    cost = np.array([side] * len(SYMBOLS))
    bpy = len(data.index) / ((data.index[-1] - data.index[0]) / pd.Timedelta(days=365.25))
    best, best_fit = START_RULE, evaluate(START_RULE, data, returns, cost, bpy)
    evaluated = [START_RULE]
    transcript = [{"round": 0, "rule": START_RULE, "why": START_DESCRIPTION, "in_sample_sharpe": round(best_fit, 3), "kept": True}]
    print(f"start: in-sample Sharpe {best_fit:.2f}")
    kwargs = {"post": post} if post else {}
    for rnd in range(1, ROUNDS + 1):
        losers = losing_trades(best, data, side)
        prompt = (f"Current rule: {json.dumps(best)}\nIn-sample Sharpe (2016-2017, after costs): {best_fit:.2f}\n"
                  f"Its 10 worst losing trades:\n" + "\n".join(json.dumps(t) for t in losers) +
                  "\nPropose ONE modified rule that would have avoided these losing trades while keeping the winners.")
        try:
            text, _ = chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}], 400, MODEL, **kwargs)
        except WorkersAIError as e:
            transcript.append({"round": rnd, "error": str(e)})
            print(f"round {rnd:2}: {e}")
            continue
        answer = extract_json(text) or {}
        rule = clamp(answer.get("rule", {})) if isinstance(answer, dict) else None
        if rule is None:
            transcript.append({"round": rnd, "error": "no valid rule", "answer": text[:300]})
            print(f"round {rnd:2}: no valid rule")
            continue
        fit = evaluate(rule, data, returns, cost, bpy)
        evaluated.append(rule)
        kept = fit > best_fit
        transcript.append({"round": rnd, "rule": rule, "why": str(answer.get("why", ""))[:300],
                           "in_sample_sharpe": round(fit, 3), "kept": kept})
        print(f"round {rnd:2}: in-sample Sharpe {fit:5.2f} {'KEPT' if kept else 'rejected'} · {str(answer.get('why', ''))[:90]}")
        if kept:
            best, best_fit = rule, fit
    result = {"idea": "agent loop: AI fixes losing trades (Instagram reel)", "model": MODEL,
              "evolved_on": [TRAIN_START, TRAIN_END], "symbols": SYMBOLS, "start_description": START_DESCRIPTION,
              "evaluated": len(evaluated), "evaluated_rules": evaluated,
              "survivors": [{"genes": best, "in_sample_sharpe": round(best_fit, 3)}], "transcript": transcript}
    AGENT_POPULATION.write_text(json.dumps(result, indent=1))
    print(f"{len(evaluated)} rules evaluated; final in-sample Sharpe {best_fit:.2f} -> {AGENT_POPULATION}")
    return result


def validate_final() -> int:
    from trader.backtest.engine import run_backtest
    from trader.backtest.runner import cost_bps
    from trader.ledger import sync
    from trader.validation import report_html
    from trader.validation.deflated import average_correlation
    from trader.validation.holdout import research_market
    from trader.validation.pipeline import validate

    loop = json.loads(AGENT_POPULATION.read_text())
    s = load_settings()
    data = research_market(s.validation, crypto=SYMBOLS)
    costs = cost_bps(data, s)
    rules = loop["evaluated_rules"]
    rho = average_correlation([run_backtest(data, AgentRule(SYMBOLS, genes=g).target_weights(data), costs).returns
                               for g in rules]) if len(rules) > 1 else 1.0
    card = validate(AgentRule, SYMBOLS, s, grid={"genome": [0]}, extra_trials=loop["evaluated"] - 1, batch_correlation=rho)
    print(f"agent_loop: {card.verdict.upper()} · trials counted: {card.trials} · ledger total {card.total_trials}")
    for gate in card.gates:
        print(f"  {'✓' if gate.passed else '✗'} {gate.label:22} {gate.value:>26}   needs {gate.threshold}")
    path = report_html.write(card)
    print(f"Report card: {path}")
    print(sync.publish_card(card, path))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run", action="store_true")
    p.add_argument("--validate", action="store_true")
    args = p.parse_args()
    if args.run:
        run_loop()
    if args.validate:
        return validate_final()
    if not (args.run or args.validate):
        p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
