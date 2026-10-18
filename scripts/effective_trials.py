"""Add effective (independent) trial counts to past ledger entries.

For each entry without one, every parameter set of its grid is backtested over the research
data (a measurement, not a new test: no gates, nothing appended), their average daily-return
correlation rho is measured, and N_eff = rho + (1 - rho) * N is stored next to the raw count.
Results, verdicts and raw trial counts are left untouched.

Run:  .venv/bin/python scripts/effective_trials.py
"""

from __future__ import annotations

import json
import sys

from trader.backtest.engine import run_backtest
from trader.backtest.runner import cost_bps, is_crypto
from trader.config import load_settings
from trader.ledger import ledger
from trader.strategies.baselines import DipBuyer, RegimeFilter, TsMomentum
from trader.strategies.evolved import EvolvedRule
from trader.strategies.examples import RandomEntry, SmaCross
from trader.strategies.ideas import ComboSleeves, FundingContrarian, WeekendGap, WeekendGapSized
from trader.validation.deflated import average_correlation, effective_trials
from trader.validation.holdout import research_market
from trader.validation.walkforward import param_combos

CLASSES = {c.name: c for c in [SmaCross, RandomEntry, TsMomentum, DipBuyer, RegimeFilter, WeekendGap, WeekendGapSized,
                               FundingContrarian, ComboSleeves, EvolvedRule]}


def main() -> int:
    s = load_settings()
    entries = ledger.entries()
    changed = 0
    for e in entries:
        if "effective_trials" in e:
            continue
        cls = CLASSES[e["strategy"]]
        if cls is EvolvedRule:          # measure the whole search, not just the converged survivors
            import importlib.util
            from trader.config import PROJECT_ROOT
            spec = importlib.util.spec_from_file_location("darwin", PROJECT_ROOT / "scripts" / "darwin.py")
            darwin = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(darwin)
            rho = darwin.search_correlation(json.loads(darwin.POPULATION.read_text())["seed"])
            e["avg_correlation"] = round(rho, 4)
            e["effective_trials"] = round(effective_trials(int(e["trials"]), rho), 2)
            changed += 1
            print(f"{e['strategy']:20} trials {e['trials']:5}  rho {e['avg_correlation']} (sampled search)  effective {e['effective_trials']}")
            continue
        wanted = sorted(set(e["symbols"]) | {e["benchmark"]} | set(cls.signal_symbols))
        data = research_market(s.validation, crypto=[x for x in wanted if is_crypto(x)],
                               stocks=[x for x in wanted if not is_crypto(x)], funding=cls.needs_funding)
        costs = cost_bps(data, s)
        returns = []
        for params in param_combos(e["grid"]):
            try:
                strat = cls(e["symbols"], **params)
            except ValueError:
                continue
            returns.append(run_backtest(data, strat.target_weights(data), costs).returns)
        rho = average_correlation(returns)
        e["avg_correlation"] = round(rho, 4) if rho is not None else None
        e["effective_trials"] = round(effective_trials(int(e["trials"]), rho), 2)
        changed += 1
        print(f"{e['strategy']:20} trials {e['trials']:5}  rho {e['avg_correlation']}  effective {e['effective_trials']}")
    if changed:
        path = ledger.ledger_path()
        path.write_text("".join(json.dumps(x, sort_keys=True, default=str) + "\n" for x in entries))
    print(f"raw trials {ledger.total_trials()} -> effective {ledger.total_effective_trials():.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
