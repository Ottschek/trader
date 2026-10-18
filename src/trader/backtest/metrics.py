"""Performance metrics. Annualization uses the actual calendar span, so it works for
24/7 crypto, stock-hours-only and mixed portfolios alike."""

from __future__ import annotations

import math

import pandas as pd

from trader.backtest.engine import BacktestResult

YEAR = pd.Timedelta(days=365.25)


def summarize(r: BacktestResult) -> dict:
    eq, ret = r.equity, r.returns
    years = max((eq.index[-1] - eq.index[0]) / YEAR, 1e-9)
    bars_per_year = len(ret) / years
    total = eq.iloc[-1] - 1.0
    std = ret.std()
    drawdown = eq / eq.cummax().clip(lower=1.0) - 1.0
    max_dd = float(drawdown.min())
    cagr = (1.0 + total) ** (1.0 / years) - 1.0 if total > -1 else -1.0
    gross = r.weights.abs().sum(axis=1)
    return {
        "name": r.name,
        "start": eq.index[0],
        "end": eq.index[-1],
        "years": years,
        "total_return": total,
        "cagr": cagr,
        "volatility": std * math.sqrt(bars_per_year) if std > 0 else 0.0,
        "sharpe": ret.mean() / std * math.sqrt(bars_per_year) if std > 0 else 0.0,
        "max_drawdown": max_dd,
        "calmar": cagr / abs(max_dd) if max_dd < 0 else float("nan"),
        "exposure": float(gross.mean()),
        "turnover_per_year": float(r.turnover.sum() / years),
        "cost_per_year": float(r.costs.sum() / years),
        "trades": r.trades,
    }


ROWS = [
    ("Total return", "total_return", "{:+.1%}"),
    ("CAGR", "cagr", "{:+.1%}"),
    ("Volatility", "volatility", "{:.1%}"),
    ("Sharpe", "sharpe", "{:.2f}"),
    ("Max drawdown", "max_drawdown", "{:.1%}"),
    ("Calmar", "calmar", "{:.2f}"),
    ("Avg exposure", "exposure", "{:.0%}"),
    ("Turnover / year", "turnover_per_year", "{:.1f}x"),
    ("Costs / year", "cost_per_year", "{:.2%}"),
    ("Trades", "trades", "{:,}"),
]


def comparison_table(*summaries: dict) -> str:
    """Side-by-side text table, e.g. strategy vs buy-and-hold."""
    names = [s["name"] for s in summaries]
    width = max(14, *(len(n) for n in names))
    lines = [f"{'':18}" + "".join(f"{n:>{width + 2}}" for n in names)]
    for label, key, fmt in ROWS:
        cells = []
        for s in summaries:
            v = s[key]
            cells.append("n/a" if isinstance(v, float) and math.isnan(v) else fmt.format(v))
        lines.append(f"{label:18}" + "".join(f"{c:>{width + 2}}" for c in cells))
    s0 = summaries[0]
    lines.append(f"Period: {s0['start']:%Y-%m-%d} -> {s0['end']:%Y-%m-%d} ({s0['years']:.1f} years)")
    return "\n".join(lines)
