import numpy as np
import pandas as pd

from trader.backtest.data import MarketData
from trader.backtest.engine import run_backtest
from trader.backtest.lookahead import check_no_lookahead
from trader.strategies.turn_of_month import TurnOfMonth, trading_days, turn_of_month_windows


def test_nyse_calendar_matches_known_years_and_holidays():
    assert len(trading_days("2023-01-01", "2023-12-31")) == 250
    assert len(trading_days("2024-01-01", "2024-12-31")) == 252
    days = set(trading_days("2021-06-01", "2022-12-31").strftime("%Y-%m-%d"))
    assert "2021-06-18" in days and "2022-06-20" not in days       # Juneteenth only from 2022 (observed Monday)
    assert "2021-12-31" in days                                     # Jan 1 2022 was a Saturday: no Friday closure
    assert "2022-12-26" not in days and "2022-04-15" not in days    # Christmas observed, Good Friday


def test_windows_run_from_the_last_days_of_a_month_to_the_first_of_the_next():
    w = turn_of_month_windows(trading_days("2024-10-01", "2024-11-30"), days_before=2, days_after=3)
    assert [(a.strftime("%m-%d"), b.strftime("%m-%d")) for a, b in w] == [("10-30", "11-05")]


def hourly(start="2024-10-21", days=30):
    times = [pd.Timestamp(f"{d} {h}", tz="America/New_York") for d in trading_days(start, pd.Timestamp(start) + pd.Timedelta(days=days)).date
             for h in ("09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30")]
    idx = pd.DatetimeIndex(times).tz_convert("UTC")
    rng = np.random.default_rng(0)
    intra = pd.DataFrame({s: rng.normal(0, 0.002, len(idx)) for s in ("SPY", "QQQ", "IWM")}, index=idx)
    close = (1 + intra).cumprod() * 100
    return MarketData(close=close, open=close, volume=close * 0 + 1e6, gap=intra * 0, intra=intra,
                      tradable=close.notna(), venue={s: "alpaca_stocks" for s in close}, raw_close=close)


def test_turn_of_month_holds_only_inside_the_window_without_lookahead():
    data = hourly()
    syms = ["SPY", "QQQ", "IWM"]
    w = TurnOfMonth(syms, days_before=2, days_after=3).target_weights(data)
    r = run_backtest(data, w, {s: 4.0 for s in syms})
    held = r.weights.sum(axis=1).gt(0)
    local = held.index.tz_convert("America/New_York")
    held_days = sorted(set(local[held].strftime("%m-%d")))
    assert held_days == ["10-30", "10-31", "11-01", "11-04", "11-05"]     # 15:30 Oct 30 -> 15:30 Nov 5
    assert not held[(local.strftime("%m-%d") == "11-05") & (local.hour == 15)].any()
    assert abs(w.max().max() - 1 / 3) < 1e-12
    assert check_no_lookahead(TurnOfMonth(syms, days_before=1, days_after=2), data) == []
