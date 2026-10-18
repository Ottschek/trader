import dataclasses

import numpy as np
import pandas as pd
import pytest

from trader.config import load_settings
from trader.data.store import bars_path
from trader.ledger import ledger
from trader.strategies.base import Strategy
from trader.strategies.examples import RandomEntry
from trader.validation import holdout as ho
from trader.validation.deflated import deflated_sharpe, expected_max_sharpe, probabilistic_sharpe
from trader.validation.montecarlo import permute_runs
from trader.validation.pipeline import validate
from trader.validation.walkforward import make_folds

# Small, fast setup: 18 months of hourly data, the last 3 months locked as holdout.
START, HOLDOUT, END = "2023-01-01", "2024-04-01", "2024-07-01"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TRADER_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))


@pytest.fixture
def settings():
    s = load_settings()
    v = dataclasses.replace(s.validation, research_start=START, holdout_start=HOLDOUT,
                            train_years=0.5, test_months=2, random_trials=200)
    return dataclasses.replace(s, validation=v)


def write_series(symbol, returns, index):
    closes = 100 * np.cumprod(1 + returns)
    opens = np.r_[100.0, closes[:-1]]
    df = pd.DataFrame({"ts": index, "open": opens, "close": closes, "volume": 1.0})
    df["high"] = df[["open", "close"]].max(axis=1)
    df["low"] = df[["open", "close"]].min(axis=1)
    path = bars_path("coinbase", symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def hourly_index():
    return pd.date_range(START, END, freq="h", tz="UTC", inclusive="left")


def random_walk(symbol="TEST/USD", seed=0, vol=0.005):
    idx = hourly_index()
    write_series(symbol, np.random.default_rng(seed).normal(0, vol, len(idx)), idx)


def planted_edge(symbol="EDGE/USD", seed=0):
    """Zero-drift asset with a real, exploitable pattern: it rises 1% per hour at 10:00 and
    11:00 UTC and gives it back slowly over the other 22 hours."""
    idx = hourly_index()
    hours = idx.hour
    drift = np.where(np.isin(hours, [10, 11]), 0.01, -0.02 / 22)
    write_series(symbol, drift + np.random.default_rng(seed).normal(0, 0.003, len(idx)), idx)


class TimeOfDay(Strategy):
    """Holds during 10:00-12:00 UTC. Decides at the close of the bar before, as the engine requires."""
    name = "time_of_day"
    default_grid = {"hours": [(9, 10)]}

    def __init__(self, symbols, hours=(9, 10)):
        super().__init__(symbols, hours=tuple(hours))

    def target_weights(self, data):
        hold = np.isin(data.index.hour, self.params["hours"]).astype(float)
        return pd.DataFrame({s: hold / len(self.symbols) for s in self.symbols}, index=data.index)


# --- holdout lock ------------------------------------------------------------------

def test_research_data_never_contains_the_holdout(settings):
    random_walk()
    data = ho.research_market(settings.validation, crypto=["TEST/USD"])
    assert data.index.max() < pd.Timestamp(HOLDOUT, tz="UTC")
    assert ho.holdout_market(settings.validation, crypto=["TEST/USD"]).index.max() >= pd.Timestamp(HOLDOUT, tz="UTC")
    with pytest.raises(ho.HoldoutLocked):
        ho.research_market(settings.validation, crypto=["TEST/USD"], end=END)


# --- walk-forward folds --------------------------------------------------------------

def test_folds_roll_forward_without_overlap_or_gaps():
    idx = pd.date_range("2016-01-01", "2024-10-17", freq="h", tz="UTC")
    folds = make_folds(idx, train_years=2, test_months=6)
    assert folds[0].test_start == pd.Timestamp("2018-01-01", tz="UTC")
    for a, b in zip(folds, folds[1:]):
        assert a.test_end == b.test_start                       # contiguous test windows
    for f in folds:
        assert f.train_end == f.test_start                      # train strictly before test
        assert f.train_start == f.test_start - pd.DateOffset(years=2)
    assert folds[-1].test_end <= idx[-1] + pd.Timedelta(seconds=1)


# --- random-entry shuffle -----------------------------------------------------------

def test_shuffle_keeps_exposure_and_holding_times():
    values = np.array([0, 0, 1, 1, 1, 0, 0.5, 0.5, 0, 0, 0, 1], dtype=float)
    shuffled = permute_runs(values, np.random.default_rng(3))
    assert sorted(shuffled) == sorted(values)
    assert shuffled.sum() == values.sum()


# --- deflated Sharpe ----------------------------------------------------------------

def test_more_trials_means_higher_bar():
    assert expected_max_sharpe(1, 0.01) == 0.0
    assert 0 < expected_max_sharpe(10, 0.01) < expected_max_sharpe(1000, 0.01)
    assert probabilistic_sharpe(0.0, 0.0, 1000, 0.0, 3.0) == pytest.approx(0.5)
    r = pd.Series(np.random.default_rng(1).normal(0.0005, 0.01, 5000))
    few = deflated_sharpe(r, trials=1).probability
    many = deflated_sharpe(r, trials=500, trial_sharpes=list(np.random.default_rng(2).normal(0, 0.02, 500))).probability
    assert many < few


# --- the done-when criteria -----------------------------------------------------------

@pytest.mark.parametrize("seed", range(10))
def test_random_entry_is_always_rejected(settings, seed):
    random_walk(seed=seed)
    card = validate(RandomEntry, ["TEST/USD"], settings, grid={"seed": [seed], "p_enter": [0.02], "hold": [24]},
                    benchmark="TEST/USD", record=False)
    assert card.verdict == "rejected"
    assert card.gates[0].passed                          # it's honest, just useless


def test_real_edge_passes_and_holdout_is_one_look_only(settings, monkeypatch):
    planted_edge()
    monkeypatch.setattr(ledger, "code_version", lambda: {"commit": "abc123", "dirty": False})
    card = validate(TimeOfDay, ["EDGE/USD"], settings, benchmark="EDGE/USD")
    assert card.verdict == "candidate", [(g.name, g.value) for g in card.gates if not g.passed]

    passed = validate(TimeOfDay, ["EDGE/USD"], settings, benchmark="EDGE/USD", holdout=True)
    assert passed.verdict == "holdout_passed"
    assert passed.holdout["start"].startswith(HOLDOUT)

    with pytest.raises(ho.HoldoutLocked, match="already used"):
        validate(TimeOfDay, ["EDGE/USD"], settings, benchmark="EDGE/USD", holdout=True)


def test_holdout_refused_for_dirty_code_and_rejected_ideas(settings, monkeypatch):
    planted_edge()
    monkeypatch.setattr(ledger, "code_version", lambda: {"commit": "abc123", "dirty": True})
    with pytest.raises(ho.HoldoutLocked, match="commit"):
        validate(TimeOfDay, ["EDGE/USD"], settings, benchmark="EDGE/USD", holdout=True)
    random_walk()
    monkeypatch.setattr(ledger, "code_version", lambda: {"commit": "abc123", "dirty": False})
    with pytest.raises(ho.HoldoutLocked, match="passed every research gate"):
        validate(RandomEntry, ["TEST/USD"], settings, benchmark="TEST/USD", holdout=True)


def test_ledger_counts_every_trial(settings):
    random_walk()
    validate(RandomEntry, ["TEST/USD"], settings, grid={"seed": [1, 2, 3], "p_enter": [0.02], "hold": [24]},
             benchmark="TEST/USD")
    card = validate(RandomEntry, ["TEST/USD"], settings, grid={"seed": [4, 5], "p_enter": [0.02], "hold": [24]},
                    benchmark="TEST/USD")
    assert card.total_trials == 5
    assert ledger.total_trials() == 5
    assert len(ledger.entries()) == 2
    assert all(e["verdict"] == "rejected" for e in ledger.entries())


def test_gate_results_are_stored_as_real_booleans(settings):
    import json
    random_walk()
    validate(RandomEntry, ["TEST/USD"], settings, grid={"seed": [1], "p_enter": [0.02], "hold": [24]},
             benchmark="TEST/USD")
    raw = ledger.ledger_path().read_text()
    assert '"passed": "' not in raw                       # never the strings "True"/"False"
    assert all(isinstance(g["passed"], bool) for g in json.loads(raw.splitlines()[0])["gates"])


def test_extra_trials_count_toward_the_ledger(settings):
    random_walk()
    card = validate(RandomEntry, ["TEST/USD"], settings, grid={"seed": [1], "p_enter": [0.02], "hold": [24]},
                    benchmark="TEST/USD", extra_trials=99)
    assert card.trials == 100 and ledger.total_trials() == 100


def test_effective_trials_shrink_for_correlated_batches():
    from trader.validation.deflated import average_correlation, effective_trials
    idx = pd.date_range("2024-01-01", periods=24 * 200, freq="h", tz="UTC")
    rng = np.random.default_rng(3)
    base = pd.Series(rng.normal(0, 0.01, len(idx)), index=idx)
    clones = [base + pd.Series(rng.normal(0, 0.001, len(idx)), index=idx) for _ in range(5)]
    independent = [pd.Series(rng.normal(0, 0.01, len(idx)), index=idx) for _ in range(5)]
    rho_c, rho_i = average_correlation(clones), average_correlation(independent)
    assert rho_c > 0.95 and rho_i < 0.1
    assert effective_trials(2000, rho_c) < 120                 # near-copies count as few trials
    assert effective_trials(5, rho_i) > 4.5                    # unrelated ideas count fully
    assert effective_trials(7, None) == 7.0


def test_deflated_gate_uses_effective_totals(settings):
    random_walk()
    card = validate(RandomEntry, ["TEST/USD"], settings, grid={"seed": [1, 2, 3], "p_enter": [0.02], "hold": [24]},
                    benchmark="TEST/USD")
    assert card.effective_trials <= card.trials
    assert ledger.total_effective_trials() == pytest.approx(card.effective_trials, abs=0.01)
    assert "eff. trials" in card.gates[-1].value
