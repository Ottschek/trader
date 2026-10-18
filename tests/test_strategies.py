import numpy as np
import pandas as pd
import pytest

from trader.backtest.data import load_market
from trader.backtest.lookahead import check_no_lookahead
from trader.data.store import bars_path, funding_path
from trader.strategies.baselines import DipBuyer, RegimeFilter, TsMomentum
from trader.strategies.helpers import hold_after, value_ago
from trader.strategies.ideas import FundingContrarian, WeekendGap


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))


def write_bars(source, symbol, ts, closes, session=None):
    closes = np.asarray(closes, dtype=float)
    df = pd.DataFrame({"ts": ts, "open": np.r_[closes[0], closes[:-1]], "close": closes, "volume": 1.0})
    df["high"] = df[["open", "close"]].max(axis=1)
    df["low"] = df[["open", "close"]].min(axis=1)
    if session:
        df["session"] = session
    path = bars_path(source, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def crypto_walk(symbol, start="2023-01-01", days=400, seed=0, drift=0.0):
    ts = pd.date_range(start, periods=days * 24, freq="h", tz="UTC")
    write_bars("coinbase", symbol, ts, 100 * np.exp(np.cumsum(np.random.default_rng(seed).normal(drift, 0.01, len(ts)))))
    return ts


def stock_hours(days, start="2024-01-01"):
    out = []
    for d in pd.bdate_range(start, periods=days):
        for t in ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]:
            out.append(pd.Timestamp(f"{d.date()} {t}", tz="America/New_York").tz_convert("UTC"))
    return pd.DatetimeIndex(out)


# --- helpers -------------------------------------------------------------------

def test_helpers_look_only_backwards():
    s = pd.Series([1.0, 2.0, 3.0, 4.0], index=pd.date_range("2024-01-01", periods=4, freq="h", tz="UTC"))
    assert np.isnan(value_ago(s, pd.Timedelta(hours=2)).iloc[1])
    assert value_ago(s, pd.Timedelta(hours=2)).iloc[3] == 2.0
    entries = pd.Series([False, True, False, False], index=s.index)
    assert list(hold_after(entries, pd.Timedelta(hours=2))) == [False, True, True, False]


# --- every M4 strategy is honest -----------------------------------------------------

def test_baselines_pass_lookahead_check():
    crypto_walk("AAA/USD", seed=1, drift=0.0002)
    crypto_walk("BBB/USD", seed=2)
    data = load_market(crypto=["AAA/USD", "BBB/USD"])
    for strat in [TsMomentum(["AAA/USD", "BBB/USD"], lookback_days=7),
                  DipBuyer(["AAA/USD", "BBB/USD"], drop_sigma=1.5, hold_hours=24),
                  RegimeFilter(["AAA/USD", "BBB/USD"], sma_days=100)]:
        assert check_no_lookahead(strat, data) == [], strat
        w = strat.target_weights(data)
        assert w.max().max() <= 0.5 + 1e-12 and w.min().min() >= 0


def test_momentum_only_changes_once_a_day():
    crypto_walk("AAA/USD", seed=3, days=60)
    data = load_market(crypto=["AAA/USD"])
    w = TsMomentum(["AAA/USD"], lookback_days=7).target_weights(data)["AAA/USD"]
    changes = w.index[w.diff().fillna(0).ne(0)]
    assert len(changes) > 0 and all(t.hour == 0 for t in changes)


# --- weekend gap ----------------------------------------------------------------------

def setup_weekend(btc_jump):
    stock_ts = stock_hours(10)                                     # Mon 1 Jan .. Fri 12 Jan 2024
    write_bars("alpaca_stocks", "MINR", stock_ts, [10.0] * len(stock_ts), session="regular")
    btc_ts = pd.date_range("2023-12-31", "2024-01-13", freq="h", tz="UTC")
    btc = np.full(len(btc_ts), 40_000.0)
    # BTC jumps on Saturday 6 Jan and stays up
    btc[btc_ts >= pd.Timestamp("2024-01-06 12:00", tz="UTC")] *= 1 + btc_jump
    write_bars("coinbase", "BTC/USD", btc_ts, btc)
    return load_market(crypto=["BTC/USD"], stocks=["MINR"])


def test_weekend_gap_trades_monday_session_only():
    data = setup_weekend(btc_jump=0.08)
    strat = WeekendGap(["MINR"], threshold=0.05, mode="follow")
    w = strat.target_weights(data)["MINR"]
    held = w[w > 0].index.tz_convert("America/New_York")
    assert len(held) > 0
    assert {t.date().isoformat() for t in held} == {"2024-01-08"}   # Monday after the jump
    assert held.min().hour == 8                                    # decided at 08:00, before the 09:30 open
    assert held.max().hour < 16
    assert check_no_lookahead(strat, data) == []
    assert (WeekendGap(["MINR"], threshold=0.05, mode="fade").target_weights(data)["MINR"] == 0).all()


def test_weekend_gap_ignores_small_moves():
    data = setup_weekend(btc_jump=0.01)
    assert (WeekendGap(["MINR"], threshold=0.05).target_weights(data)["MINR"] == 0).all()


# --- funding contrarian ----------------------------------------------------------------

def test_funding_contrarian_buys_after_negative_funding_spike():
    ts = crypto_walk("BTC/USD", start="2023-06-01", days=60, seed=4)
    rng = np.random.default_rng(5)
    rate = rng.normal(0.00001, 0.000002, len(ts))
    spike = pd.Timestamp("2023-07-15 10:00", tz="UTC")
    rate[ts == spike] = -0.0005                                    # shorts suddenly crowded
    path = funding_path("BTC")
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"ts": ts, "time": ts, "coin": "BTC", "funding_rate": rate, "premium": 0.0}).to_parquet(path, index=False)

    data = load_market(crypto=["BTC/USD"], funding=True)
    strat = FundingContrarian(["BTC/USD"], z=3.0, hold_hours=24)
    w = strat.target_weights(data)["BTC/USD"]
    held = w[w > 0].index
    assert held.min() == spike
    assert held.max() < spike + pd.Timedelta(hours=24)
    assert check_no_lookahead(strat, data) == []
    with pytest.raises(ValueError, match="funding"):
        strat.target_weights(load_market(crypto=["BTC/USD"]))
