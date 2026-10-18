"""Walk-forward testing: tune on a rolling training window, judge only on the window after it.

    |---- train 2y ----|- test 6m -|
          |---- train 2y ----|- test 6m -|
                |---- train 2y ----|- test 6m -|   ... up to the holdout

For every fold, the parameter set with the best Sharpe in the training window is picked
and only its result in the following test window counts. Stitching the test windows
together gives an out-of-sample record: performance of the *procedure* (including how it
picks parameters), never of parameters chosen with hindsight.

Speed: each parameter set is backtested once over the whole period; folds slice those
runs. When the picked parameters change between folds, the position change is charged
at the normal trading cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

import numpy as np
import pandas as pd

from trader.backtest.data import MarketData
from trader.backtest.engine import BacktestResult, run_backtest


@dataclass(frozen=True)
class Fold:
    train_start: pd.Timestamp
    train_end: pd.Timestamp     # exclusive
    test_start: pd.Timestamp
    test_end: pd.Timestamp      # exclusive


@dataclass
class WalkForwardResult:
    folds: list[Fold]
    chosen: list[dict]                  # parameters picked for each fold
    oos: BacktestResult                 # stitched out-of-sample record
    benchmark_oos: BacktestResult       # buy-and-hold over the same windows
    oos_targets: pd.DataFrame           # targets in force during the test windows
    combos: list[dict]
    combo_sharpes: list[float]          # per-bar Sharpe of every combo over the whole period
    combo_results: list[BacktestResult] = field(default_factory=list)
    fold_returns: list[float] = field(default_factory=list)

    def best_params(self, start, end) -> dict:
        """Parameters with the best Sharpe in [start, end), e.g. the final pick before the holdout."""
        scores = [per_bar_sharpe(_window(r.returns, start, end)) for r in self.combo_results]
        return self.combos[int(np.argmax(scores))]


def param_combos(grid: dict[str, list]) -> list[dict]:
    keys = list(grid)
    return [dict(zip(keys, values)) for values in product(*(grid[k] for k in keys))]


def make_folds(index: pd.DatetimeIndex, train_years: float, test_months: int, end=None) -> list[Fold]:
    end = pd.Timestamp(end) if end is not None else index[-1] + pd.Timedelta(seconds=1)
    train = pd.DateOffset(months=int(round(train_years * 12)))
    test = pd.DateOffset(months=test_months)
    folds = []
    test_start = index[0] + train
    while test_start < end:
        folds.append(Fold(test_start - train, test_start, test_start, min(test_start + test, end)))
        test_start = test_start + test
    return folds


def per_bar_sharpe(returns: pd.Series) -> float:
    std = returns.std()
    return float(returns.mean() / std) if std > 0 and np.isfinite(std) else 0.0


def _window(series, start, end):
    return series[(series.index >= start) & (series.index < end)]


def walk_forward(strategy_cls, symbols, grid: dict[str, list], data: MarketData, cost_bps: dict[str, float],
                 train_years: float, test_months: int, benchmark_targets: pd.DataFrame,
                 allow_short: bool = False) -> WalkForwardResult:
    runs = []
    for params in param_combos(grid):
        try:
            strategy = strategy_cls(symbols, **params)
        except ValueError:
            continue  # invalid combination, e.g. fast >= slow
        targets = strategy.target_weights(data)
        result = run_backtest(data, targets, cost_bps, name=strategy.label(), allow_short=allow_short)
        runs.append((params, targets.reindex(index=data.index, columns=data.symbols).ffill().fillna(0.0), result))
    if not runs:
        raise ValueError("no valid parameter combinations in the grid")

    folds = make_folds(data.index, train_years, test_months)
    if not folds:
        raise ValueError("not enough history for even one walk-forward fold")
    bench = run_backtest(data, benchmark_targets, cost_bps, name="benchmark")
    cost = pd.Series({s: cost_bps[s] / 10_000 for s in data.symbols})

    chosen, pieces, bench_pieces, target_pieces, fold_returns = [], [], [], [], []
    previous = None
    for fold in folds:
        best = max(runs, key=lambda run: per_bar_sharpe(_window(run[2].returns, fold.train_start, fold.train_end)))
        params, targets, result = best
        chosen.append(params)
        idx = result.returns.index
        mask = (idx >= fold.test_start) & (idx < fold.test_end)
        if not mask.any():
            continue
        piece = {
            "returns": result.returns[mask].copy(),
            "weights": result.weights[mask],
            "turnover": result.turnover[mask].copy(),
            "costs": result.costs[mask].copy(),
        }
        if previous is not None and previous[0] is not params:
            # switching parameter sets: trade from the old run's position to the new run's
            first = idx[mask][0]
            before = idx[idx < first][-1]
            dw = (result.weights.loc[before] - previous[2].weights.loc[before]).abs()
            switch_cost = float((dw * cost).sum())
            piece["returns"].iloc[0] = (1 + piece["returns"].iloc[0]) * (1 - switch_cost) - 1
            piece["turnover"].iloc[0] += float(dw.sum())
            piece["costs"].iloc[0] += switch_cost
        pieces.append(piece)
        bench_pieces.append(bench.returns[mask])
        target_pieces.append(targets[mask])
        fold_returns.append(float((1 + piece["returns"]).prod() - 1))
        previous = best

    def stitch(name, parts, weights=None, turnover=None, costs=None, trades=0):
        rets = pd.concat(parts)
        equity = (1 + rets).cumprod()
        return BacktestResult(
            name=name, equity=equity, returns=rets,
            weights=pd.concat(weights) if weights else pd.DataFrame(index=rets.index),
            turnover=pd.concat(turnover) if turnover else pd.Series(0.0, index=rets.index),
            costs=pd.concat(costs) if costs else pd.Series(0.0, index=rets.index),
            trades=trades,
        )

    oos = stitch(
        f"{strategy_cls.name} walk-forward", [p["returns"] for p in pieces],
        [p["weights"] for p in pieces], [p["turnover"] for p in pieces], [p["costs"] for p in pieces],
        trades=int(sum((p["turnover"] > 0).sum() for p in pieces)),
    )
    bench_oos = stitch("buy-and-hold", bench_pieces)
    return WalkForwardResult(
        folds=folds, chosen=chosen, oos=oos, benchmark_oos=bench_oos,
        oos_targets=pd.concat(target_pieces),
        combos=[r[0] for r in runs], combo_sharpes=[per_bar_sharpe(r[2].returns) for r in runs],
        combo_results=[r[2] for r in runs], fold_returns=fold_returns,
    )
