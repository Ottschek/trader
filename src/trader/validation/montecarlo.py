"""Random-entry test: would the same trading pattern placed at random times do as well?

The strategy's position history (per asset) is cut into runs of constant position, e.g.
"flat 30 bars, long 12 bars, flat 50 bars ...". Shuffling the order of those runs keeps
everything about *how* it trades (exposure, number of trades, holding times) and destroys
only *when* it trades. If the real timing isn't better than most shuffles, there's no edge.

Both the strategy and its shuffles are scored with the same fast approximation (weights
decided at bar t-1 earn bar t's full return, minus costs on weight changes), so the
comparison is apples to apples.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trader.backtest.data import MarketData


@dataclass
class RandomEntryResult:
    strategy_sharpe: float          # annualized, approximation
    random_sharpes: np.ndarray      # annualized, one per shuffle
    p_value: float                  # share of shuffles at least as good (with +1 smoothing)


def bar_returns(data: MarketData) -> pd.DataFrame:
    return (1 + data.gap) * (1 + data.intra) - 1


def permute_runs(values: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Shuffle the order of runs of equal consecutive values."""
    if len(values) == 0:
        return values
    change = np.flatnonzero(np.diff(values) != 0) + 1
    starts = np.r_[0, change]
    lengths = np.diff(np.r_[starts, len(values)])
    order = rng.permutation(len(starts))
    return np.repeat(values[starts][order], lengths[order])


def approx_returns(weights: np.ndarray, returns: np.ndarray, cost: np.ndarray) -> np.ndarray:
    held = np.vstack([np.zeros((1, weights.shape[1])), weights[:-1]])
    traded = np.abs(np.diff(np.vstack([np.zeros((1, weights.shape[1])), held]), axis=0))
    return (held * returns).sum(axis=1) - (traded * cost).sum(axis=1)


def annualized_sharpe(r: np.ndarray, bars_per_year: float) -> float:
    std = r.std(ddof=1) if len(r) > 1 else 0.0
    return float(r.mean() / std * np.sqrt(bars_per_year)) if std > 0 else 0.0


def random_entry_test(targets: pd.DataFrame, data: MarketData, cost_bps: dict[str, float],
                      trials: int = 500, seed: int = 0) -> RandomEntryResult:
    """`targets`: the (held) target weights during the out-of-sample windows."""
    targets = targets.loc[:, (targets != 0).any()]          # assets never held can't contribute: skip them
    if targets.shape[1] == 0:
        return RandomEntryResult(0.0, np.zeros(trials), 1.0)
    returns = bar_returns(data).reindex(index=targets.index, columns=targets.columns).fillna(0.0).to_numpy()
    w = targets.to_numpy(dtype=float)
    cost = np.array([cost_bps[s] / 10_000 for s in targets.columns])
    years = max((targets.index[-1] - targets.index[0]) / pd.Timedelta(days=365.25), 1e-9)
    bpy = len(targets) / years

    actual = annualized_sharpe(approx_returns(w, returns, cost), bpy)
    rng = np.random.default_rng(seed)
    sharpes = np.empty(trials)
    for i in range(trials):
        shuffled = np.column_stack([permute_runs(w[:, j], rng) for j in range(w.shape[1])])
        sharpes[i] = annualized_sharpe(approx_returns(shuffled, returns, cost), bpy)
    p = (1 + int((sharpes >= actual - 1e-12).sum())) / (1 + trials)
    return RandomEntryResult(actual, sharpes, p)


def bootstrap_mean_test(returns: pd.Series, trials: int = 500, block_days: int = 5, seed: int = 0) -> RandomEntryResult:
    """For strategies that are always invested (no timing to shuffle): is the average return reliably
    above zero? Daily returns are demeaned (the "no edge" world) and resampled in blocks of
    `block_days` consecutive days, which keeps their short-term clustering. p = share of resampled
    Sharpes at least as high as the real one (+1 smoothing)."""
    daily = (1 + returns.fillna(0)).groupby(returns.index.tz_convert("UTC").normalize()).prod() - 1
    r = daily.to_numpy()
    n = len(r)
    if n < 2 * block_days:
        return RandomEntryResult(0.0, np.zeros(trials), 1.0)
    actual = annualized_sharpe(r, 365)
    centred = r - r.mean()
    rng = np.random.default_rng(seed)
    blocks = int(np.ceil(n / block_days))
    sharpes = np.empty(trials)
    for i in range(trials):
        starts = rng.integers(0, n - block_days + 1, blocks)
        sample = np.concatenate([centred[s:s + block_days] for s in starts])[:n]
        sharpes[i] = annualized_sharpe(sample, 365)
    p = (1 + int((sharpes >= actual - 1e-12).sum())) / (1 + trials)
    return RandomEntryResult(actual, sharpes, p)
