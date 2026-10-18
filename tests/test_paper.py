import tomllib

import numpy as np
import pandas as pd
import pytest

from trader.backtest.data import load_market
from trader.config import PROJECT_ROOT, load_settings
from trader.data.store import bars_path
from trader.paper import plan, reconcile


def worker_vars() -> dict:
    return tomllib.loads((PROJECT_ROOT / "cloudflare" / "scheduler" / "wrangler.toml").read_text())["vars"]


def test_worker_settings_match_the_frozen_experiment():
    cfg, v = plan.load_config(), worker_vars()
    exp, risk = cfg["experiment"], cfg["risk"]
    assert v["PAPER_EXPERIMENT"] == exp["id"]
    assert v["PAPER_SYMBOLS"].split(",") == exp["symbols"]
    assert float(v["PAPER_THRESHOLD"]) == exp["params"]["threshold"]
    assert v["PAPER_START"] == str(exp["start"])
    assert float(v["PAPER_WEEKEND_GAP_HOURS"]) == cfg["execution"]["weekend_gap_hours"]
    assert float(v["RISK_MAX_POSITION"]) == risk["max_position_pct"]
    assert float(v["RISK_MAX_DAILY_LOSS"]) == risk["max_daily_loss_pct"]
    assert float(v["RISK_KILL_DRAWDOWN"]) == risk["kill_switch_drawdown_pct"]


def test_paper_risk_is_never_looser_than_the_global_limits():
    r, g = plan.load_config()["risk"], load_settings().risk
    assert r["max_position_pct"] <= g.max_position_pct
    assert r["max_daily_loss_pct"] <= g.max_daily_loss_pct
    assert r["kill_switch_drawdown_pct"] <= g.kill_switch_drawdown_pct


def test_experiment_is_labeled_and_uses_pre_holdout_params():
    exp = plan.load_config()["experiment"]
    assert "SHADOW" in exp["label"] and "not a validated candidate" in exp["label"]
    assert pd.Timestamp(exp["start"]) > pd.Timestamp(load_settings().validation.holdout_start)


# --- live vs backtest -----------------------------------------------------------


def _write(source, symbol, ts, closes, session=None):
    closes = np.asarray(closes, dtype=float)
    df = pd.DataFrame({"ts": ts, "open": np.r_[closes[0], closes[:-1]], "close": closes, "volume": 1.0})
    df["high"] = df[["open", "close"]].max(axis=1)
    df["low"] = df[["open", "close"]].min(axis=1)
    if session is not None:
        df["session"] = session
    path = bars_path(source, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return df


def _session_bars(days, start):
    out = []
    for d in pd.bdate_range(start, periods=days):
        for t in ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]:
            out.append(pd.Timestamp(f"{d.date()} {t}", tz="America/New_York").tz_convert("UTC"))
    return pd.DatetimeIndex(out)


@pytest.fixture
def weekend_world(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))
    ts = _session_bars(45, "2023-11-27")
    rng = np.random.default_rng(8)
    closes = 50 * np.exp(np.cumsum(rng.normal(0, 0.004, len(ts))))
    _write("alpaca_stocks", "CALM", ts, closes, session="regular")
    # raw 30-minute bars: the 09:30 bar opens at the previous hourly close (what the backtest fills at)
    raw = _write("alpaca_stocks_30m", "CALM", ts, closes, session="regular")
    btc_ts = pd.date_range("2023-11-26", "2024-02-01", freq="h", tz="UTC")
    btc = np.full(len(btc_ts), 40_000.0)
    btc[btc_ts >= pd.Timestamp("2024-01-13 12:00", tz="UTC")] *= 1.08      # big weekend before Mon 15 Jan
    _write("coinbase", "BTC/USD", btc_ts, btc)
    cfg = {"experiment": {"start": "2024-01-08", "symbols": ["CALM"], "params": {"threshold": 0.05, "target_vol": 0.02}},
           "risk": {"max_position_pct": 0.10}}
    data = load_market(crypto=["BTC/USD"], stocks=["CALM"])
    open_0115 = float(raw.loc[raw["ts"] == pd.Timestamp("2024-01-15 09:30", tz="America/New_York").tz_convert("UTC"), "open"].iloc[0])
    return data, cfg, open_0115


def _equity():
    return [{"ts": "2024-01-08T15:00:00+00:00", "equity": 100_000.0}, {"ts": "2024-01-17T15:00:00+00:00", "equity": 100_150.0}]


def test_matching_live_trade_and_slippage(weekend_world):
    data, cfg, open_px = weekend_world
    decisions = [
        {"id": "2024-01-15-entry", "kind": "entry", "session_date": "2024-01-15", "detail": {"move": 0.08, "equity": 100_000}},
        {"id": "2024-01-16-exit", "kind": "exit", "session_date": "2024-01-16", "detail": {"entry_decision": "2024-01-15-entry"}},
    ]
    orders = [
        {"decision_id": "2024-01-15-entry", "symbol": "CALM", "side": "buy", "status": "filled", "filled_qty": 100,
         "filled_avg_price": open_px * 1.001, "filled_at": "2024-01-15T14:30:01Z"},
        {"decision_id": "2024-01-16-exit", "symbol": "CALM", "side": "sell", "status": "filled", "filled_qty": 100,
         "filled_avg_price": open_px * 1.011, "filled_at": "2024-01-16T14:30:01Z"},
    ]
    r = reconcile.reconcile(data, cfg, load_settings(), _equity(), orders, decisions, pd.Timestamp("2024-01-31", tz="UTC"))
    assert r["mismatches"] == []
    t = next(x for x in r["trades"] if x["session"] == "2024-01-15")
    assert t["backtest_traded"] and t["live_decision"] == "entry" and t["match"]
    assert t["live_return"] == pytest.approx(100 * open_px * 0.010 / 100_000)
    buy = next(s for s in r["slippage"] if s["side"] == "buy")
    assert buy["expected_open"] == pytest.approx(open_px)
    assert buy["bps"] == pytest.approx(10.0, abs=0.05)             # paid 0.1% above the open
    assert r["actual_return"] == pytest.approx(0.0015)
    assert r["expected_curve"] and r["tracking_difference"] is not None


def test_live_no_trade_where_backtest_traded_is_a_mismatch(weekend_world):
    data, cfg, _ = weekend_world
    decisions = [{"id": "2024-01-15-entry", "kind": "no_trade", "session_date": "2024-01-15", "detail": {"move": 0.03}}]
    r = reconcile.reconcile(data, cfg, load_settings(), _equity(), [], decisions, pd.Timestamp("2024-01-31", tz="UTC"))
    assert r["mismatches"] == ["2024-01-15"]


def test_before_the_experiment_starts_everything_is_empty(weekend_world):
    data, cfg, _ = weekend_world
    cfg = {**cfg, "experiment": {**cfg["experiment"], "start": "2030-01-01"}}
    r = reconcile.reconcile(data, cfg, load_settings(), [], [], [], pd.Timestamp("2024-01-31", tz="UTC"))
    assert r["trades"] == [] and r["actual_return"] is None and r["expected_return"] == 0.0


class FakeD1:
    def __init__(self, decisions):
        self.decisions, self.saved = decisions, []

    def query(self, sql, params=None):
        import json
        if sql.startswith("SELECT summary FROM paper_reconcile"):
            return [{"summary": self.saved[-1][1]}] if self.saved else []
        if "FROM paper_decisions" in sql:
            return [{**d, "detail": json.dumps(d["detail"])} for d in self.decisions]
        return []

    def insert_many(self, table, cols, rows):
        self.saved.extend(rows)
        return len(rows)


def test_new_mismatches_are_flagged_once(weekend_world):
    data, cfg, _ = weekend_world
    cfg = {**cfg, "experiment": {**cfg["experiment"], "signal_symbol": "BTC/USD"}}
    d1 = FakeD1([{"id": "x", "kind": "no_trade", "session_date": "2024-01-15", "detail": {"move": 0.03}, "decided_at": "t"}])
    now = pd.Timestamp("2024-01-31", tz="UTC")
    first = reconcile.run_and_save(d1, load_settings(), now, cfg)
    assert first["new_mismatches"] == ["2024-01-15"]
    second = reconcile.run_and_save(d1, load_settings(), now + pd.Timedelta(hours=1), cfg)
    assert second["mismatches"] == ["2024-01-15"] and second["new_mismatches"] == []
