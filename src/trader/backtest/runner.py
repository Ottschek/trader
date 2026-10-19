"""One call to backtest a strategy honestly: load data, check for lookahead, run it with
costs, and run the buy-and-hold benchmark on the same data."""

from __future__ import annotations

from dataclasses import dataclass

from trader.backtest.data import MarketData, load_market
from trader.backtest.engine import BacktestResult, run_backtest
from trader.backtest.lookahead import check_no_lookahead
from trader.backtest.metrics import comparison_table, summarize
from trader.config import Settings
from trader.strategies.base import Strategy
from trader.strategies.examples import BuyAndHold


@dataclass
class Report:
    strategy: BacktestResult
    benchmark: BacktestResult
    summaries: list[dict]
    lookahead_problems: list[str]

    @property
    def honest(self) -> bool:
        return not self.lookahead_problems

    def table(self) -> str:
        return comparison_table(*self.summaries)


def is_crypto(symbol: str) -> bool:
    return "/" in symbol


def benchmark_symbol(strategy: Strategy, settings: Settings) -> str:
    crypto = all(is_crypto(s) for s in strategy.symbols)
    return settings.benchmarks["crypto" if crypto else "stocks"]


def cost_bps(data: MarketData, settings: Settings, venue: str | None = None) -> dict[str, float]:
    """One-way cost per symbol: its own venue's, or `venue`'s for every symbol if given."""
    return {s: settings.costs[venue or data.venue[s]].one_way_bps for s in data.symbols}


def backtest(strategy: Strategy, settings: Settings, start=None, end=None, data: MarketData | None = None,
             benchmark: str | None = None, allow_short: bool = False) -> Report:
    bench = benchmark or benchmark_symbol(strategy, settings)
    if data is None:
        symbols = set(strategy.symbols) | {bench} | set(strategy.signal_symbols)
        data = load_market(crypto=sorted(s for s in symbols if is_crypto(s)),
                           stocks=sorted(s for s in symbols if not is_crypto(s)), start=start, end=end,
                           funding=strategy.needs_funding)
    costs = cost_bps(data, settings, strategy.cost_venue)
    problems = check_no_lookahead(strategy, data)
    result = run_backtest(data, strategy.target_weights(data), costs, name=strategy.label(),
                          allow_short=allow_short or strategy.allow_short)
    bench_result = run_backtest(data, BuyAndHold([bench]).target_weights(data), costs, name=f"hold {bench}")
    return Report(result, bench_result, [summarize(result), summarize(bench_result)], problems)
