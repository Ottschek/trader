"""Catch strategies that peek at the future.

Run the strategy on the full history, then again on history cut off at several points.
An honest strategy makes the same decisions for the past no matter how much later data
exists. If adding later data changes an earlier decision, the strategy used the future.
"""

from __future__ import annotations

import numpy as np

from trader.backtest.data import MarketData

DEFAULT_CUTS = 12


def check_no_lookahead(strategy, data: MarketData, n_cuts: int = DEFAULT_CUTS) -> list[str]:
    """Returns a list of problems (empty = no lookahead found)."""
    full = strategy.target_weights(data)
    n = len(data.index)
    if n < 10:
        return []
    # cut points spread over the back 70% of the history, never the very last bar
    positions = np.unique(np.linspace(n * 0.3, n - 2, n_cuts).astype(int))
    problems = []
    for pos in positions:
        cut = data.index[pos]
        part = strategy.target_weights(data.until(cut))
        a = full.loc[:cut].reindex(columns=full.columns).to_numpy(dtype=float)
        b = part.reindex(index=full.loc[:cut].index, columns=full.columns).to_numpy(dtype=float)
        differs = ~np.isclose(a, b, equal_nan=True, atol=1e-12)
        if differs.any():
            row, col = np.argwhere(differs)[0]
            problems.append(
                f"decision for {full.columns[col]} at {full.index[row]} changed when data after {cut} "
                f"was added ({b[row, col]:.4g} -> {a[row, col]:.4g})"
            )
    return problems
