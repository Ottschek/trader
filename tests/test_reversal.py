import numpy as np
import pandas as pd
import pytest

from trader.backtest.engine import run_backtest
from trader.backtest.lookahead import check_no_lookahead
from trader.data.daily import daily_path, load_daily
from trader.strategies.reversal import WeeklyReversal


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))


def write(symbols, vol=1e7, price=20.0, days=120, seed=0):
    idx = pd.bdate_range("2023-01-02", periods=days)
    rng = np.random.default_rng(seed)
    rows = []
    for k, s in enumerate(symbols):
        close = price * np.exp(np.cumsum(rng.normal(0, 0.02, days)))
        v = vol[k] if isinstance(vol, list) else vol
        for t, c in zip(idx, close):
            rows.append({"ts": pd.Timestamp(f"{t.date()} 00:00", tz="America/New_York").tz_convert("UTC"), "symbol": s,
                         "open": c, "high": c, "low": c, "close": c, "volume": v})
    daily_path().parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(daily_path(), index=False)


def test_friday_ranking_longs_losers_shorts_winners_and_holds_the_week():
    syms = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    write(syms)
    data = load_daily(syms)
    w = WeeklyReversal(syms, lookback_days=5, positions=2).target_weights(data)
    days = data.index.tz_convert("America/New_York")
    friday = np.flatnonzero(days.dayofweek == 4)[3]
    past = data.close.iloc[friday] / data.close.iloc[friday - 5] - 1
    ranked = past[syms].sort_values().index
    row = w.iloc[friday]
    assert row[ranked[:2]].tolist() == [0.25, 0.25] and row[ranked[-2:]].tolist() == [-0.25, -0.25]
    assert row[ranked[2]] == 0
    assert (w.iloc[friday + 1] == row).all()                      # held through the next week
    assert np.abs(w).sum(axis=1).max() == pytest.approx(1.0)
    r = run_backtest(data, w, {s: 7.0 for s in syms}, allow_short=True)
    assert r.trades > 0
    assert check_no_lookahead(WeeklyReversal(syms, lookback_days=5, positions=2), data) == []


def test_illiquid_or_cheap_stocks_are_never_traded():
    syms = ["AAA", "BBB", "CCC", "DDD", "THIN"]
    write(syms, vol=[1e7, 1e7, 1e7, 1e7, 1e4])
    data = load_daily(syms)
    w = WeeklyReversal(syms, lookback_days=5, positions=2).target_weights(data)
    assert (w["THIN"] == 0).all()
    write(syms, price=2.0, vol=1e8)                              # $200M a day, but a $2 stock
    w2 = WeeklyReversal(syms, lookback_days=5, positions=1).target_weights(load_daily(syms))
    assert (w2 == 0).all().all()                                  # nothing eligible -> flat


def test_shadow_starts_flat_on_the_start_date_and_records_weekly_books():
    import json
    from types import SimpleNamespace

    from trader.config import VenueCosts
    from trader.paper import reversal_shadow as rs

    syms = [f"S{i:03d}" for i in range(110)]
    idx = pd.bdate_range("2026-08-03", "2026-11-13")
    rng = np.random.default_rng(3)
    rows = []
    for s in syms:
        close = 30 * np.exp(np.cumsum(rng.normal(0, 0.02, len(idx))))
        rows += [{"ts": pd.Timestamp(f"{t.date()} 00:00", tz="America/New_York").tz_convert("UTC"), "symbol": s,
                  "open": c, "high": c, "low": c, "close": c, "volume": 5e6} for t, c in zip(idx, close)]
    data = load_daily(syms, bars=pd.DataFrame(rows))
    settings = SimpleNamespace(costs={"alpaca_largecaps": VenueCosts(0, 6, 4)})
    out = rs.simulate(data, settings)
    assert out["equity"].index[0] >= rs.START and out["equity"].iloc[0] == pytest.approx(1.0)
    assert [w["decided"] for w in out["weeks"]] == ["2026-10-23", "2026-10-30", "2026-11-06", "2026-11-13"]
    first = out["weeks"][0]
    assert len(json.loads(first["longs"])) == 50 and len(json.loads(first["shorts"])) == 50
    assert first["complete"] == 1 and first["ret"] is not None
    assert out["weeks"][-1]["complete"] == 0 and out["weeks"][-1]["ret"] is None    # decided on the last bar
    assert out["equity"][out["equity"].index < pd.Timestamp("2026-10-26", tz="America/New_York")].nunique() == 1  # flat until the first fill


def test_shadow_params_are_the_walk_forwards_latest_pick():
    import json
    from pathlib import Path

    from trader.paper import reversal_shadow as rs
    entries = [json.loads(line) for line in (Path(__file__).resolve().parents[1] / "ledger" / "experiments.jsonl").read_text().splitlines()]
    card = next(e for e in entries if e["id"] == rs.LEDGER_ID)
    assert card["folds"][-1]["params"] == rs.PARAMS
    assert card["oos"]["cagr"] == pytest.approx(rs.BACKTEST_CAGR, abs=1e-4)
