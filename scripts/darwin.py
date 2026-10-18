"""Darwin: breed rule-based strategies with a genetic algorithm, then send the survivors
through the full validation harness, counting EVERY evaluated genome as a trial.

Evolution only sees 2016-01-01 .. 2017-12-31; the walk-forward's first test window starts
2018-01-01, so the genomes are judged on data evolution never saw. Fitness is a fast
approximation (Sharpe of yesterday's weights x today's returns, minus costs).

Run:  .venv/bin/python scripts/darwin.py --evolve          # writes config/darwin_population.json
      .venv/bin/python scripts/darwin.py --validate        # validates the survivors (recorded)
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from trader.backtest.data import load_market
from trader.config import load_settings
from trader.strategies.evolved import GENE_SPACE, INT_GENES, POPULATION, EvolvedRule, rule_signal
from trader.strategies.helpers import once_a_day
from trader.ledger import sync
from trader.validation import report_html
from trader.validation.montecarlo import annualized_sharpe, approx_returns, bar_returns

SYMBOLS = ["BTC/USD", "ETH/USD"]
EVOLVE_START, EVOLVE_END = "2016-01-01", "2017-12-31 23:59"
POP, GENERATIONS, ELITE, SURVIVORS = 100, 20, 10, 10


def random_genome(rng) -> dict:
    g = {}
    for k, (lo, hi) in GENE_SPACE.items():
        g[k] = int(rng.integers(lo, hi + 1)) if k in INT_GENES else float(rng.uniform(lo, hi))
    if not (g["use_mom"] or g["use_sma"] or g["use_vol"]):
        g["use_sma"] = 1
    return g


def mutate(g: dict, rng, rate: float = 0.25) -> dict:
    g = dict(g)
    for k, (lo, hi) in GENE_SPACE.items():
        if rng.random() < rate:
            if k in ("use_mom", "use_sma", "use_vol"):
                g[k] = 1 - g[k]
            elif k in INT_GENES:
                g[k] = int(np.clip(round(g[k] + rng.normal(0, (hi - lo) * 0.15)), lo, hi))
            else:
                g[k] = float(np.clip(g[k] + rng.normal(0, (hi - lo) * 0.15), lo, hi))
    if not (g["use_mom"] or g["use_sma"] or g["use_vol"]):
        g["use_sma"] = 1
    return g


def crossover(a: dict, b: dict, rng) -> dict:
    return {k: (a[k] if rng.random() < 0.5 else b[k]) for k in a}


def fitness(g: dict, data, returns: np.ndarray, cost: np.ndarray, bpy: float) -> float:
    cols = []
    for s in SYMBOLS:
        c = data.close[s].dropna()
        cols.append(once_a_day(rule_signal(c, g).astype(float), stock=False).reindex(data.index).ffill().fillna(0) / len(SYMBOLS))
    w = np.column_stack(cols)
    if w.mean() < 0.05:            # a strategy that almost never trades isn't a strategy
        return -9.0
    return annualized_sharpe(approx_returns(w, returns, cost), bpy)


def evolve(seed: int = 0, write: bool = True, history: list | None = None) -> dict:
    s = load_settings()
    data = load_market(crypto=SYMBOLS, start=EVOLVE_START, end=EVOLVE_END)
    returns = bar_returns(data)[SYMBOLS].to_numpy()
    cost = np.array([s.costs["alpaca_crypto"].one_way_bps / 1e4] * len(SYMBOLS))
    bpy = len(data.index) / ((data.index[-1] - data.index[0]) / pd.Timedelta(days=365.25))
    rng = np.random.default_rng(seed)
    pop = [random_genome(rng) for _ in range(POP)]
    evaluated = 0
    scored = []
    for gen in range(GENERATIONS):
        scored = sorted(((fitness(g, data, returns, cost, bpy), g) for g in pop), key=lambda x: -x[0])
        evaluated += len(pop)
        if history is not None:
            history.extend(pop)
        print(f"generation {gen + 1:2}/{GENERATIONS}: best in-sample Sharpe {scored[0][0]:.2f}, median {scored[len(scored) // 2][0]:.2f}")
        elite = [g for _, g in scored[:ELITE]]

        def pick():
            a, b = rng.choice(len(scored) // 2, 2, replace=False)   # tournament among the better half
            return scored[min(a, b)][1]
        pop = elite + [mutate(crossover(pick(), pick(), rng), rng) for _ in range(POP - ELITE)]
    survivors = [{"genes": g, "in_sample_sharpe": round(f, 3)} for f, g in scored[:SURVIVORS]]
    result = {"evolved_on": [EVOLVE_START, EVOLVE_END], "symbols": SYMBOLS, "seed": seed,
              "evaluated": evaluated, "survivors": survivors}
    if write:
        POPULATION.write_text(json.dumps(result, indent=1))
        print(f"{evaluated} genomes evaluated; top {SURVIVORS} written to {POPULATION}")
    return result


def search_correlation(seed: int = 0, sample: int = 100) -> float:
    """Average return correlation among genomes the search actually evaluated (a random sample
    of all of them, not just the converged survivors), over the research period. Re-runs the
    deterministic evolution; measurement only."""
    from trader.backtest.engine import run_backtest
    from trader.backtest.runner import cost_bps
    from trader.validation.deflated import average_correlation
    from trader.validation.holdout import research_market

    history: list[dict] = []
    evolve(seed, write=False, history=history)
    rng = np.random.default_rng(seed + 1)
    picks = [history[i] for i in rng.choice(len(history), size=min(sample, len(history)), replace=False)]
    s = load_settings()
    data = research_market(s.validation, crypto=SYMBOLS)
    costs = cost_bps(data, s)
    returns = [run_backtest(data, EvolvedRule(SYMBOLS, genes=g).target_weights(data), costs).returns for g in picks]
    return average_correlation(returns)


def validate_survivors() -> int:
    from trader.validation.pipeline import validate

    pop = json.loads(POPULATION.read_text())
    n = len(pop["survivors"])
    rho = search_correlation(pop["seed"])
    card = validate(EvolvedRule, pop["symbols"], load_settings(), grid={"genome": list(range(n))},
                    extra_trials=pop["evaluated"] - n, batch_correlation=rho)
    print(f"darwin_evolved: {card.verdict.upper()} · trials counted: {card.trials} · ledger total {card.total_trials}")
    for g in card.gates:
        print(f"  {'✓' if g.passed else '✗'} {g.label:22} {g.value:>26}   needs {g.threshold}")
    path = report_html.write(card)
    print(f"Report card: {path}")
    print(sync.publish_card(card, path))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--evolve", action="store_true")
    p.add_argument("--validate", action="store_true")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()
    if args.evolve:
        evolve(args.seed)
    if args.validate:
        return validate_survivors()
    return 0


if __name__ == "__main__":
    sys.exit(main())
