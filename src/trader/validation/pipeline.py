"""The full validation funnel for one idea, producing a report card and a ledger entry.

Gates (all must pass, thresholds from settings.validation, fixed in advance):
  1. honest        the lookahead check finds nothing, for every parameter set tried
  2. oos_sharpe    walk-forward out-of-sample Sharpe >= min_oos_sharpe
  3. beats_hold    out-of-sample Sharpe above buy-and-hold over the same windows, after costs
  4. drawdown      out-of-sample max drawdown no worse than max_oos_drawdown
  5. random_entry  beats the shuffled-timing versions of itself (p <= max_p_value)
  6. deflated      deflated Sharpe >= min_deflated_sharpe, counting every trial in the ledger
Only a strategy that passes all six may look at the locked holdout, once, with committed code.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, replace

import pandas as pd

from trader.backtest.capacity import estimate_capacity
from trader.backtest.data import MarketData
from trader.backtest.engine import BacktestResult, run_backtest
from trader.backtest.lookahead import check_no_lookahead
from trader.backtest.metrics import summarize
from trader.backtest.runner import benchmark_symbol, cost_bps, is_crypto
from trader.config import Settings, ValidationSettings
from trader.ledger import ledger
from trader.strategies.examples import BuyAndHold
from trader.validation import holdout as ho
from trader.validation.deflated import deflated_sharpe
from trader.validation.montecarlo import random_entry_test
from trader.validation.walkforward import param_combos, walk_forward

LOOKAHEAD_CUTS = 6


@dataclass
class Gate:
    name: str
    label: str
    passed: bool
    value: str
    threshold: str

    def __post_init__(self):
        # comparisons on numpy numbers give numpy bools, which JSON would store as "True"/"False"
        self.passed = bool(self.passed)


@dataclass
class ReportCard:
    id: str
    created_at: str
    idea: str
    strategy: str
    symbols: list[str]
    benchmark: str
    grid: dict
    trials: int
    total_trials: int
    code: dict
    research_window: list[str]
    train_years: float
    test_months: int
    gates: list[Gate]
    verdict: str                      # rejected | candidate | holdout_passed | holdout_failed
    oos: dict = field(default_factory=dict)
    benchmark_oos: dict = field(default_factory=dict)
    folds: list[dict] = field(default_factory=list)
    random_entry: dict = field(default_factory=dict)
    deflated: dict = field(default_factory=dict)
    lookahead_problems: list[str] = field(default_factory=list)
    capacity: dict = field(default_factory=dict)   # informational: account size before trades move prices
    holdout: dict | None = None
    # kept in memory for the HTML report, not written to the ledger
    equity: pd.Series | None = None
    benchmark_equity: pd.Series | None = None
    random_sharpes: list[float] | None = None
    trial_sharpes: list[float] = field(default_factory=list)

    @property
    def passed_research(self) -> bool:
        return all(g.passed for g in self.gates)

    def ledger_entry(self) -> dict:
        d = asdict(self)
        for k in ("equity", "benchmark_equity", "random_sharpes"):
            d.pop(k)
        d["trial_sharpes"] = [round(s, 6) for s in self.trial_sharpes]
        return d


def _metrics(summary: dict) -> dict:
    keep = ("total_return", "cagr", "volatility", "sharpe", "max_drawdown", "calmar", "exposure",
            "turnover_per_year", "cost_per_year", "trades", "years")
    return {k: (round(float(summary[k]), 6) if k != "trades" else int(summary[k])) for k in keep}


def _slice_result(r: BacktestResult, start) -> BacktestResult:
    mask = r.returns.index >= start
    rets = r.returns[mask]
    return BacktestResult(r.name, (1 + rets).cumprod(), rets, r.weights[mask], r.turnover[mask],
                          r.costs[mask], int((r.turnover[mask] > 0).sum()))


def validate(strategy_cls, symbols, settings: Settings, grid: dict | None = None, data: MarketData | None = None,
             benchmark: str | None = None, holdout: bool = False, record: bool = True, seed: int = 0,
             validation: ValidationSettings | None = None, train_months: int | None = None,
             test_months: int | None = None, extra_trials: int = 0) -> ReportCard:
    """`train_months`/`test_months` override the walk-forward windows for ideas whose data is
    too short for the defaults (e.g. funding history starts 2023). The override is recorded.
    `extra_trials`: candidates tried before this run that aren't in the grid (e.g. every genome
    Darwin evaluated); they count toward the deflated Sharpe like any other trial."""
    v = validation or settings.validation
    if train_months or test_months:
        v = replace(v, train_years=(train_months or v.train_years * 12) / 12, test_months=test_months or v.test_months)
    grid = grid or strategy_cls.default_grid
    symbols = list(symbols)
    bench = benchmark or benchmark_symbol(strategy_cls(symbols, **param_combos(grid)[0]), settings)
    if data is None:
        wanted = sorted(set(symbols) | {bench} | set(strategy_cls.signal_symbols))
        data = ho.research_market(v, crypto=[s for s in wanted if is_crypto(s)],
                                  stocks=[s for s in wanted if not is_crypto(s)], funding=strategy_cls.needs_funding,
                                  daily=strategy_cls.bars == "daily")
    if data.index.max() >= pd.Timestamp(v.holdout_start, tz="UTC"):
        raise ho.HoldoutLocked("research data must end before the holdout")
    costs = cost_bps(data, settings)
    combos = param_combos(grid)
    key = ledger.idea_key(strategy_cls.name, symbols)
    prior_trials, prior_sharpes = ledger.total_trials(), ledger.trial_sharpes()

    # 1. honesty, for every parameter set
    problems = []
    for params in combos:
        try:
            strat = strategy_cls(symbols, **params)
        except ValueError:
            continue
        problems += [f"{strat.label()}: {p}" for p in check_no_lookahead(strat, data, LOOKAHEAD_CUTS)]

    # 2-4. walk-forward
    wf = walk_forward(strategy_cls, symbols, grid, data, costs, v.train_years, v.test_months,
                      BuyAndHold([bench]).target_weights(data))
    oos, boos = summarize(wf.oos), summarize(wf.benchmark_oos)

    # 5. random entry, on the out-of-sample record
    rnd = random_entry_test(wf.oos_targets, data, costs, trials=v.random_trials, seed=seed)

    # 6. deflated Sharpe, counting every trial ever run
    trials = len(wf.combos) + extra_trials
    dsr = deflated_sharpe(wf.oos.returns, prior_trials + trials, prior_sharpes + wf.combo_sharpes)

    gates = [
        Gate("honest", "No lookahead", not problems, f"{len(problems)} problems", "0 problems"),
        Gate("oos_sharpe", "Out-of-sample Sharpe", oos["sharpe"] >= v.min_oos_sharpe,
             f"{oos['sharpe']:.2f}", f">= {v.min_oos_sharpe:.2f}"),
        Gate("beats_hold", "Beats buy-and-hold", oos["sharpe"] > boos["sharpe"],
             f"{oos['sharpe']:.2f} vs {boos['sharpe']:.2f}", f"> hold {bench}"),
        Gate("drawdown", "Max drawdown", oos["max_drawdown"] >= -v.max_oos_drawdown,
             f"{oos['max_drawdown']:.1%}", f">= -{v.max_oos_drawdown:.0%}"),
        Gate("random_entry", "Beats random timing", rnd.p_value <= v.max_p_value,
             f"p = {rnd.p_value:.3f}", f"<= {v.max_p_value:.2f}"),
        Gate("deflated", "Deflated Sharpe", dsr.probability >= v.min_deflated_sharpe,
             f"{dsr.probability:.2f} ({dsr.trials} trials)", f">= {v.min_deflated_sharpe:.2f}"),
    ]
    # capacity of the parameter set the walk-forward picked most often
    picks = [json.dumps(c, sort_keys=True) for c in wf.chosen]
    favourite = max(set(picks), key=picks.count)
    fav_result = wf.combo_results[[json.dumps(c, sort_keys=True) for c in wf.combos].index(favourite)]
    capacity = {**estimate_capacity(fav_result, data), "params": json.loads(favourite)}

    code = ledger.code_version()
    card = ReportCard(
        id=uuid.uuid4().hex[:12],
        created_at=pd.Timestamp.now(tz="UTC").isoformat(),
        idea=key, strategy=strategy_cls.name, symbols=symbols, benchmark=bench, grid=grid,
        trials=trials, total_trials=prior_trials + trials, code=code,
        research_window=[data.index[0].isoformat(), data.index[-1].isoformat()],
        train_years=v.train_years, test_months=v.test_months,
        gates=gates, verdict="candidate" if all(g.passed for g in gates) else "rejected",
        oos=_metrics(oos), benchmark_oos=_metrics(boos),
        folds=[{"test_start": f.test_start.isoformat(), "test_end": f.test_end.isoformat(),
                "params": p, "return": round(r, 6)}
               for f, p, r in zip(wf.folds, wf.chosen, wf.fold_returns)],
        random_entry={"strategy_sharpe": round(rnd.strategy_sharpe, 4), "p_value": round(rnd.p_value, 4),
                      "random_median": round(float(pd.Series(rnd.random_sharpes).median()), 4),
                      "random_p95": round(float(pd.Series(rnd.random_sharpes).quantile(0.95)), 4),
                      "trials": int(len(rnd.random_sharpes))},
        deflated={"probability": round(dsr.probability, 4), "sharpe_per_bar": round(dsr.sharpe, 6),
                  "expected_max_per_bar": round(dsr.expected_max, 6), "trials": dsr.trials, "bars": dsr.bars},
        lookahead_problems=problems[:20], capacity=capacity,
        equity=wf.oos.equity, benchmark_equity=wf.benchmark_oos.equity,
        random_sharpes=[float(x) for x in rnd.random_sharpes], trial_sharpes=wf.combo_sharpes,
    )

    if holdout:
        card.holdout = _evaluate_holdout(card, strategy_cls, symbols, wf, settings, v, bench, key, code)
        card.verdict = "holdout_passed" if card.holdout["passed"] else "holdout_failed"
    if record:
        ledger.append(card.ledger_entry())
    return card


def _evaluate_holdout(card, strategy_cls, symbols, wf, settings, v, bench, key, code) -> dict:
    if not card.passed_research:
        raise ho.HoldoutLocked("the holdout is only for strategies that passed every research gate")
    if code["dirty"] or not code["commit"]:
        raise ho.HoldoutLocked("commit your code first: the holdout result must point to an exact code version")
    seen = ledger.holdout_entry(key)
    if seen:
        raise ho.HoldoutLocked(f"this idea already used its one look at the holdout (run {seen['id']} "
                               f"on {seen['created_at'][:10]})")
    start = pd.Timestamp(v.holdout_start, tz="UTC")
    train = pd.DateOffset(months=int(round(v.train_years * 12)))
    params = wf.best_params(start - train, start)      # final pick, using only pre-holdout data
    wanted = sorted(set(symbols) | {bench} | set(strategy_cls.signal_symbols))
    full = ho.holdout_market(v, crypto=[s for s in wanted if is_crypto(s)],
                             stocks=[s for s in wanted if not is_crypto(s)], funding=strategy_cls.needs_funding,
                             daily=strategy_cls.bars == "daily")
    costs = cost_bps(full, settings)
    strat = strategy_cls(symbols, **params)
    res = _slice_result(run_backtest(full, strat.target_weights(full), costs, name=strat.label()), start)
    bres = _slice_result(run_backtest(full, BuyAndHold([bench]).target_weights(full), costs, name="hold"), start)
    s, b = summarize(res), summarize(bres)
    passed = s["sharpe"] >= v.min_oos_sharpe and s["sharpe"] > b["sharpe"] and s["max_drawdown"] >= -v.max_oos_drawdown
    return {"params": params, "start": start.isoformat(), "end": res.returns.index[-1].isoformat(),
            "metrics": _metrics(s), "benchmark": _metrics(b), "passed": bool(passed), "commit": code["commit"]}
