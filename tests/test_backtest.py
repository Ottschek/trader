import numpy as np
import pandas as pd
import pytest

from trader.backtest.data import load_market
from trader.backtest.engine import BacktestError, run_backtest
from trader.backtest.lookahead import check_no_lookahead
from trader.backtest.metrics import summarize
from trader.data.store import bars_path, corporate_actions_path
from trader.strategies.base import Strategy
from trader.strategies.examples import BuyAndHold, PeekAhead, SmaCross


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))


def write_bars(source, symbol, ts, opens, closes, session=None):
    df = pd.DataFrame({"ts": pd.to_datetime(ts, utc=True), "open": opens, "close": closes})
    df["high"] = df[["open", "close"]].max(axis=1)
    df["low"] = df[["open", "close"]].min(axis=1)
    df["volume"] = 1.0
    if session:
        df["session"] = session
    path = bars_path(source, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def write_actions(rows):
    path = corporate_actions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def hours(n, start="2024-01-01"):
    return pd.date_range(start, periods=n, freq="h", tz="UTC")


def random_walk(n, seed=0, start=100.0):
    rng = np.random.default_rng(seed)
    closes = start * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    opens = np.r_[start, closes[:-1]]
    return opens, closes


# --- engine: hand-calculated ---------------------------------------------------

def test_engine_matches_hand_calculation():
    # buy decided at bar 0's close -> fills at bar 1's open (101)
    # sell decided at bar 2's close -> fills at bar 3's open (100)
    write_bars("coinbase", "TEST/USD", hours(5), [100, 101, 102, 100, 105], [100, 102, 99, 105, 110])
    data = load_market(crypto=["TEST/USD"])
    targets = pd.DataFrame({"TEST/USD": [1.0, 1.0, 0.0, 0.0, 0.0]}, index=data.index)
    r = run_backtest(data, targets, {"TEST/USD": 10.0})   # 10 bps per trade

    expected = 0.999 * (102 / 101) * (99 / 102) * (100 / 99) * 0.999   # = 0.999^2 * 100/101
    assert r.equity.iloc[-1] == pytest.approx(expected, rel=1e-12)
    assert r.equity.iloc[0] == 1.0                     # nothing happens on the first bar
    assert r.trades == 2
    assert list(r.turnover.round(12)) == [0, 1, 0, 1, 0]
    assert r.weights["TEST/USD"].iloc[1] == pytest.approx(1.0)
    assert r.weights["TEST/USD"].iloc[3] == 0.0


def test_buy_and_hold_is_price_ratio_minus_one_entry_cost():
    opens, closes = random_walk(500, seed=1)
    write_bars("coinbase", "TEST/USD", hours(500), opens, closes)
    data = load_market(crypto=["TEST/USD"])
    r = run_backtest(data, BuyAndHold(["TEST/USD"]).target_weights(data), {"TEST/USD": 25.0})
    assert r.equity.iloc[-1] == pytest.approx(0.9975 * closes[-1] / opens[1], rel=1e-10)
    assert r.trades == 1


def test_half_weight_leaves_half_in_cash():
    write_bars("coinbase", "TEST/USD", hours(3), [100, 100, 200], [100, 200, 200])
    data = load_market(crypto=["TEST/USD"])
    r = run_backtest(data, pd.DataFrame({"TEST/USD": 0.5}, index=data.index), {"TEST/USD": 0.0})
    assert r.equity.iloc[-1] == pytest.approx(1.5)            # half doubled, half stayed cash
    assert r.weights["TEST/USD"].iloc[1] == pytest.approx(2 / 3)  # drifted up with the price


def test_engine_rejects_leverage_and_unwanted_shorts():
    write_bars("coinbase", "TEST/USD", hours(3), [1, 1, 1], [1, 1, 1])
    data = load_market(crypto=["TEST/USD"])
    with pytest.raises(BacktestError, match="gross exposure"):
        run_backtest(data, pd.DataFrame({"TEST/USD": 1.5}, index=data.index), {"TEST/USD": 0})
    with pytest.raises(BacktestError, match="negative"):
        run_backtest(data, pd.DataFrame({"TEST/USD": -0.5}, index=data.index), {"TEST/USD": 0})
    with pytest.raises(BacktestError, match="not in the data"):
        run_backtest(data, pd.DataFrame({"NOPE/USD": 0.5}, index=data.index), {"TEST/USD": 0})


def test_short_position_profits_when_price_falls():
    write_bars("coinbase", "TEST/USD", hours(3), [100, 100, 80], [100, 80, 80])
    data = load_market(crypto=["TEST/USD"])
    r = run_backtest(data, pd.DataFrame({"TEST/USD": -1.0}, index=data.index), {"TEST/USD": 0}, allow_short=True)
    assert r.equity.iloc[-1] == pytest.approx(1.2)


# --- stocks: sessions, closed hours, corporate actions ------------------------------

def stock_hours(days, start="2024-01-02"):
    """Regular-session bar starts (09:30 ... 15:30 New York) for consecutive weekdays."""
    out = []
    for d in pd.bdate_range(start, periods=days):
        for t in ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]:
            out.append(pd.Timestamp(f"{d.date()} {t}", tz="America/New_York").tz_convert("UTC"))
    return pd.DatetimeIndex(out)


def test_stock_target_set_while_market_closed_fills_at_next_open():
    ts = stock_hours(2)
    write_bars("alpaca_stocks", "TEST", ts, [100.0] * 14, [100.0] * 7 + [110.0] * 7, session="regular")
    crypto_ts = pd.date_range(ts[0], ts[-1], freq="h")
    write_bars("coinbase", "C/USD", crypto_ts, [1.0] * len(crypto_ts), [1.0] * len(crypto_ts))
    data = load_market(crypto=["C/USD"], stocks=["TEST"])

    evening = pd.Timestamp("2024-01-02 20:00", tz="America/New_York").tz_convert("UTC")
    targets = pd.DataFrame({"TEST": 0.0, "C/USD": 0.0}, index=data.index)
    targets.loc[evening:, "TEST"] = 1.0
    r = run_backtest(data, targets, {"TEST": 0.0, "C/USD": 0.0})

    filled = r.turnover[r.turnover > 0].index
    assert list(filled) == [ts[7]]           # 09:30 next morning, the first bar the stock trades
    assert r.equity.iloc[-1] == pytest.approx(1.10)


def test_split_does_not_look_like_a_crash():
    ts = stock_hours(2)
    # 4:1 split effective on day 2: price goes from 400 to 100 overnight, nothing really happened
    write_bars("alpaca_stocks", "SPLT", ts, [400.0] * 7 + [100.0] * 7, [400.0] * 7 + [100.0] * 7, session="regular")
    write_actions([{"symbol": "SPLT", "type": "forward_splits", "ex_date": pd.Timestamp("2024-01-03"),
                    "old_rate": 1.0, "new_rate": 4.0, "rate": None, "special": None}])
    data = load_market(stocks=["SPLT"])
    assert data.gap["SPLT"].abs().max() == pytest.approx(0.0)
    r = run_backtest(data, BuyAndHold(["SPLT"]).target_weights(data), {"SPLT": 0.0})
    assert r.equity.iloc[-1] == pytest.approx(1.0)
    # adjusted prices continue smoothly from the first real price; no future info in early prices
    assert data.close["SPLT"].iloc[0] == 400.0
    assert data.close["SPLT"].iloc[-1] == pytest.approx(400.0)


def test_cash_dividend_counts_as_return():
    ts = stock_hours(2)
    # $2 dividend, ex-date day 2: price drops from 100 to 98 but holders received $2
    write_bars("alpaca_stocks", "DIV", ts, [100.0] * 7 + [98.0] * 7, [100.0] * 7 + [98.0] * 7, session="regular")
    write_actions([{"symbol": "DIV", "type": "cash_dividends", "ex_date": pd.Timestamp("2024-01-03"),
                    "old_rate": None, "new_rate": None, "rate": 2.0, "special": False}])
    data = load_market(stocks=["DIV"])
    r = run_backtest(data, BuyAndHold(["DIV"]).target_weights(data), {"DIV": 0.0})
    assert r.equity.iloc[-1] == pytest.approx(1.0)


def test_regular_session_filter():
    ts = stock_hours(1)
    pre = pd.Timestamp("2024-01-02 08:00", tz="America/New_York").tz_convert("UTC")
    all_ts = ts.insert(0, pre)
    write_bars("alpaca_stocks", "SESS", all_ts, [50.0] + [100.0] * 7, [50.0] + [100.0] * 7,
               session=["pre"] + ["regular"] * 7)
    assert len(load_market(stocks=["SESS"]).index) == 7
    assert len(load_market(stocks=["SESS"], stock_session="all").index) == 8


# --- data cleaning -----------------------------------------------------------------

def test_bad_tick_is_flattened_but_real_move_kept():
    closes = [100, 100, 300, 100, 100, 100, 200, 200, 200]   # spike at bar 2, real jump at bar 6
    opens = [100] + closes[:-1]
    write_bars("coinbase", "TEST/USD", hours(len(closes)), opens, closes)
    data = load_market(crypto=["TEST/USD"])
    assert data.cleaned["TEST/USD"] == 1
    assert data.close["TEST/USD"].iloc[2] == pytest.approx(100.0)
    assert data.close["TEST/USD"].iloc[-1] == pytest.approx(200.0)
    assert load_market(crypto=["TEST/USD"], clean=False).close["TEST/USD"].iloc[2] == pytest.approx(300.0)


# --- lookahead check ---------------------------------------------------------------

class FullSampleZScore(Strategy):
    """Subtle cheat: normalizes with the mean/std of the WHOLE history, which includes the future."""
    name = "zscore_cheat"

    def target_weights(self, data):
        c = data.close[self.symbols[0]]
        z = (c - c.mean()) / c.std()
        return pd.DataFrame({self.symbols[0]: (z < 0).astype(float)}, index=data.index)


def test_lookahead_check_catches_cheats_and_passes_honest_strategies():
    opens, closes = random_walk(3000, seed=7)
    write_bars("coinbase", "TEST/USD", hours(3000), opens, closes)
    data = load_market(crypto=["TEST/USD"])
    assert check_no_lookahead(PeekAhead(["TEST/USD"]), data)
    assert check_no_lookahead(FullSampleZScore(["TEST/USD"]), data)
    assert check_no_lookahead(SmaCross(["TEST/USD"], fast=10, slow=50), data) == []
    assert check_no_lookahead(BuyAndHold(["TEST/USD"]), data) == []


def test_cheat_would_look_amazing_without_the_check():
    opens, closes = random_walk(3000, seed=7)
    write_bars("coinbase", "TEST/USD", hours(3000), opens, closes)
    data = load_market(crypto=["TEST/USD"])
    cheat = run_backtest(data, PeekAhead(["TEST/USD"]).target_weights(data), {"TEST/USD": 0.0})
    hold = run_backtest(data, BuyAndHold(["TEST/USD"]).target_weights(data), {"TEST/USD": 0.0})
    assert summarize(cheat)["sharpe"] > 5 > summarize(hold)["sharpe"]


# --- metrics -----------------------------------------------------------------------

def test_max_drawdown_and_flat_sharpe():
    write_bars("coinbase", "TEST/USD", hours(5), [100, 100, 120, 60, 90], [100, 120, 60, 90, 90])
    data = load_market(crypto=["TEST/USD"], clean=False)
    hold = summarize(run_backtest(data, BuyAndHold(["TEST/USD"]).target_weights(data), {"TEST/USD": 0.0}))
    assert hold["max_drawdown"] == pytest.approx(-0.5)        # 120 -> 60
    flat = summarize(run_backtest(data, pd.DataFrame({"TEST/USD": 0.0}, index=data.index), {"TEST/USD": 0.0}))
    assert flat["sharpe"] == 0.0 and flat["total_return"] == 0.0


def test_mixed_timing_never_fills_before_the_decision_bar_closed():
    # crypto bars start on the hour, regular-session stock bars at :30
    ts = stock_hours(1)                                   # 09:30 ... 15:30 New York
    write_bars("alpaca_stocks", "STK", ts, [100.0] * 7, [100.0] * 7, session="regular")
    crypto_ts = pd.date_range(ts[0].floor("h"), ts[-1].ceil("h"), freq="h")
    write_bars("coinbase", "C/USD", crypto_ts, [1.0] * len(crypto_ts), [1.0] * len(crypto_ts))
    data = load_market(crypto=["C/USD"], stocks=["STK"])

    decide = crypto_ts[1]                                 # crypto bar 10:00-11:00 NY: its close is known at 11:00
    targets = pd.DataFrame({"STK": 0.0, "C/USD": 0.0}, index=data.index)
    targets.loc[decide:, "STK"] = 1.0
    r = run_backtest(data, targets, {"STK": 0.0, "C/USD": 0.0})
    filled = r.turnover[r.turnover > 0].index[0]
    assert filled >= decide + pd.Timedelta(hours=1)       # not at the 10:30 stock open
    assert filled == ts[2]                                # but at 11:30, the first open after 11:00
