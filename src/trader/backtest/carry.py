"""Funding-harvest "assets": long spot + short perpetual of the same coin, equal size.

Price moves cancel out, so one hour's return of the pair is
    + funding rate paid to shorts that hour (Hyperliquid funds hourly; positive = longs pay shorts)
    - change in the perp's premium over spot (the short perp loses when the premium widens)
scaled by MARGIN_FACTOR, because the perp short needs margin on top of the spot purchase
(assumed 25%, so $1 of pair needs $1.25 of capital). Both legs trade at the `carry` cost profile.

The funding rate for hour h is known at h (it settles then), and the premium is measured at
the same time, so the hour's return is only earned by a position held through it.
"""

from __future__ import annotations

import pandas as pd

from trader.backtest.data import MarketData, _utc
from trader.data.store import funding_path, read

VENUE = "carry"
MARGIN_FACTOR = 1 / 1.25


def carry_returns(funding: pd.DataFrame) -> pd.Series:
    f = funding.drop_duplicates("ts").set_index("ts").sort_index()
    return (f["funding_rate"] - f["premium"].diff().fillna(0.0)) * MARGIN_FACTOR


SPOT_SUFFIX = " spot"


def load_carry(symbols, start=None, end=None, spot=()) -> MarketData:
    """MarketData whose 'assets' are funding-harvest pairs, named like the coins ("BTC/USD").
    `spot`: coins to add as ordinary spot assets named "BTC/USD spot" (for the buy-and-hold
    benchmark, which the rules define as holding BTC itself)."""
    cols = {k: {} for k in ("intra", "funding", "premium")}
    for s in symbols:
        f = read(funding_path(s.split("/")[0]))
        if f.empty:
            continue
        if start is not None:
            f = f[f["ts"] >= _utc(start)]
        if end is not None:
            f = f[f["ts"] <= _utc(end)]
        if len(f) < 2:
            continue
        cols["intra"][s] = carry_returns(f)
        cols["funding"][s] = f.drop_duplicates("ts").set_index("ts")["funding_rate"]
        cols["premium"][s] = f.drop_duplicates("ts").set_index("ts")["premium"]
    intra = pd.DataFrame(cols["intra"]).sort_index()
    tradable = intra.notna()
    level = (1 + intra.fillna(0.0)).cumprod()            # a price-like index for strategies that want one
    gap = pd.DataFrame(0.0, index=intra.index, columns=intra.columns)
    close, opn = level.where(tradable), level.where(tradable)
    venue = {s: VENUE for s in intra.columns}
    if spot:
        from trader.backtest.data import load_market
        sp = load_market(crypto=list(spot), start=intra.index[0], end=intra.index[-1])
        for s in spot:
            name = s + SPOT_SUFFIX
            gap[name] = sp.gap[s].reindex(intra.index).fillna(0.0)
            intra[name] = sp.intra[s].reindex(intra.index).fillna(0.0)
            close[name], opn[name] = sp.close[s].reindex(intra.index), sp.open[s].reindex(intra.index)
            tradable[name] = sp.tradable[s].reindex(intra.index).fillna(False).astype(bool)
            venue[name] = sp.venue[s]
    return MarketData(
        close=close, open=opn, volume=pd.DataFrame(1.0, index=intra.index, columns=intra.columns),
        gap=gap, intra=intra.fillna(0.0), tradable=tradable, venue=venue,
        funding=pd.DataFrame(cols["funding"]).reindex(intra.index),
    )
