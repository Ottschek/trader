"""Strategies bred by the genetic algorithm in scripts/darwin.py ("Darwin").

A genome is a small rule over daily decisions on a basket:
    hold the basket while  [momentum over mom_days > mom_min]        (if use_mom)
                      and  [price / SMA(sma_days) > sma_ratio]        (if use_sma)
                      and  [30-day daily volatility < vol_max]        (if use_vol)
The evolved population lives in config/darwin_population.json (committed, so every
validated genome is reproducible). EvolvedRule(genome=i) is the i-th survivor.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from trader.backtest.data import MarketData
from trader.config import PROJECT_ROOT
from trader.strategies.base import Strategy
from trader.strategies.helpers import has_history, is_crypto, once_a_day, value_ago

POPULATION = PROJECT_ROOT / "config" / "darwin_population.json"
DAY = pd.Timedelta(days=1)

GENE_SPACE = {
    "use_mom": (0, 1), "mom_days": (3, 180), "mom_min": (-0.2, 0.3),
    "use_sma": (0, 1), "sma_days": (10, 300), "sma_ratio": (0.9, 1.1),
    "use_vol": (0, 1), "vol_max": (0.01, 0.12),
}
INT_GENES = {"use_mom", "use_sma", "use_vol", "mom_days", "sma_days"}


def rule_signal(close: pd.Series, g: dict) -> pd.Series:
    """True where the genome says 'hold', using only past data (daily re-decision applied by caller)."""
    sig = pd.Series(True, index=close.index)
    longest = 30
    if g["use_mom"]:
        sig &= (close / value_ago(close, int(g["mom_days"]) * DAY) - 1) > g["mom_min"]
        longest = max(longest, int(g["mom_days"]))
    if g["use_sma"]:
        sig &= close / close.rolling(f"{int(g['sma_days'])}D").mean() > g["sma_ratio"]
        longest = max(longest, int(g["sma_days"]))
    if g["use_vol"]:
        bars_per_day = 24
        vol = np.log(close).diff().rolling("30D").std() * np.sqrt(bars_per_day)
        sig &= vol < g["vol_max"]
    return sig & has_history(close, longest * DAY)


def load_population(path: Path = POPULATION) -> list[dict]:
    return json.loads(path.read_text())["survivors"] if path.exists() else []


class EvolvedRule(Strategy):
    name = "darwin_evolved"
    default_grid = {"genome": list(range(10))}

    def __init__(self, symbols, genome: int = 0, genes: dict | None = None):
        super().__init__(symbols, genome=genome)
        self.genes = genes if genes is not None else load_population()[genome]["genes"]

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        w = 1.0 / len(self.symbols)
        out = {}
        for s in self.symbols:
            c = data.close[s].dropna()
            hold = rule_signal(c, self.genes).astype(float)
            out[s] = once_a_day(hold, stock=not is_crypto(s)) * w
        return pd.DataFrame(out).reindex(data.index)
