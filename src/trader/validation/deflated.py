"""Deflated Sharpe ratio (Bailey & López de Prado, 2014).

Try enough ideas and one of them will show a great Sharpe by pure luck. The deflated
Sharpe ratio is the probability that the observed Sharpe beats the best Sharpe you'd
*expect* from that many tries if none of them had any edge. It also accounts for fat
tails and skew in the returns. Every parameter set ever tested counts as a trial, which
is why the experiment ledger counts them all.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
import pandas as pd

_N = NormalDist()
EULER_GAMMA = 0.5772156649015329


@dataclass
class DeflatedSharpe:
    probability: float      # the deflated Sharpe ratio: P(true Sharpe > luck benchmark)
    sharpe: float           # observed, per bar
    expected_max: float     # best per-bar Sharpe expected from luck alone
    trials: int
    bars: int


def probabilistic_sharpe(sr: float, benchmark: float, bars: int, skew: float, kurtosis: float) -> float:
    """P(true Sharpe > benchmark), given the estimate `sr` from `bars` returns.
    `kurtosis` is the plain (not excess) kurtosis: 3 for normal returns."""
    var = 1 - skew * sr + (kurtosis - 1) / 4 * sr ** 2
    if bars < 2 or var <= 0:
        return 0.0
    return _N.cdf((sr - benchmark) * math.sqrt(bars - 1) / math.sqrt(var))


def expected_max_sharpe(trials: int, sharpe_variance: float) -> float:
    """Expected best Sharpe among `trials` skill-less strategies."""
    if trials <= 1 or sharpe_variance <= 0:
        return 0.0
    return math.sqrt(sharpe_variance) * ((1 - EULER_GAMMA) * _N.inv_cdf(1 - 1 / trials)
                                         + EULER_GAMMA * _N.inv_cdf(1 - 1 / (trials * math.e)))


def deflated_sharpe(returns: pd.Series, trials: int, trial_sharpes: list[float] | None = None) -> DeflatedSharpe:
    r = returns.dropna()
    bars = len(r)
    std = r.std()
    sr = float(r.mean() / std) if std > 0 else 0.0
    skew = float(r.skew()) if bars > 2 else 0.0
    kurt = float(r.kurt()) + 3 if bars > 3 else 3.0
    sharpes = [s for s in (trial_sharpes or []) if np.isfinite(s)]
    if len(sharpes) >= 2 and np.var(sharpes, ddof=1) > 0:
        var = float(np.var(sharpes, ddof=1))
    else:  # no spread across trials known: use the estimator's own sampling variance
        var = (1 + 0.5 * sr ** 2) / max(bars, 1)
    sr_star = expected_max_sharpe(trials, var)
    return DeflatedSharpe(probabilistic_sharpe(sr, sr_star, bars, skew, kurt), sr, sr_star, trials, bars)
