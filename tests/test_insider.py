import numpy as np
import pandas as pd
import pytest

from trader.backtest.lookahead import check_no_lookahead
from trader.data.daily import daily_path, load_daily
from trader.strategies.earnings import EarningsDrift, reaction_rows
from trader.strategies.insider import InsiderCluster, find_clusters


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))


def write_daily(symbols, days=300, start="2023-01-02", seed=0):
    idx = pd.bdate_range(start, periods=days)
    rows = []
    rng = np.random.default_rng(seed)
    for s in symbols:
        close = 20 * np.exp(np.cumsum(rng.normal(0, 0.02, days)))
        for t, c in zip(idx, close):
            rows.append({"ts": pd.Timestamp(f"{t.date()} 00:00", tz="America/New_York").tz_convert("UTC"), "symbol": s,
                         "open": c, "high": c, "low": c, "close": c, "volume": 1e5})
    path = daily_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)
    return idx


def ev(symbol, date, owner, value=60_000):
    return {"symbol": symbol, "filing_date": pd.Timestamp(date), "owner_cik": owner, "value": value}


def test_cluster_needs_distinct_insiders_value_and_window():
    events = pd.DataFrame([ev("AAA", "2023-03-01", "o1"), ev("AAA", "2023-03-03", "o1"),   # same insider twice
                           ev("AAA", "2023-03-06", "o2"),                                   # 2nd insider: cluster of 2
                           ev("BBB", "2023-03-01", "o1"), ev("BBB", "2023-03-20", "o2"),   # too far apart
                           ev("CCC", "2023-03-01", "o1", 10_000), ev("CCC", "2023-03-02", "o2", 10_000)])  # too small
    c = find_clusters(events, min_insiders=2, window_days=10, min_value=100_000, cooldown_days=30)
    assert c.to_dict("records") == [{"symbol": "AAA", "signal_date": pd.Timestamp("2023-03-06")}]
    assert find_clusters(events, 3, 10, 100_000, 30).empty


def test_entry_at_next_open_hold_and_max_positions():
    write_daily(["AAA", "BBB", "CCC"])
    data = load_daily(["AAA", "BBB", "CCC"])
    events = pd.DataFrame([ev("AAA", "2023-03-01", "o1"), ev("AAA", "2023-03-02", "o2"),
                           ev("BBB", "2023-03-01", "o1"), ev("BBB", "2023-03-02", "o2")])
    strat = InsiderCluster(["AAA", "BBB", "CCC"], min_insiders=2, hold_days=5, events=events)
    w = strat.target_weights(data)
    held = w.index[w["AAA"] > 0].tz_convert("America/New_York").date
    assert str(held[0]) == "2023-03-02"            # target on the filing day's bar -> engine fills next open
    assert len(held) == 5
    assert w["CCC"].sum() == 0
    strat.MAX_POSITIONS = 1
    w1 = strat.target_weights(data)
    assert (w1 > 0).sum(axis=1).max() == 1         # second simultaneous cluster skipped
    assert check_no_lookahead(InsiderCluster(["AAA", "BBB", "CCC"], min_insiders=2, hold_days=5, events=events), data) == []


# --- post-earnings drift ------------------------------------------------------------


def test_reaction_session_follows_the_acceptance_time():
    days = pd.DatetimeIndex(pd.bdate_range("2023-03-06", periods=5))      # Mon..Fri
    acc = pd.Series(pd.to_datetime(["2023-03-07 07:00", "2023-03-07 12:00", "2023-03-07 16:30", "2023-03-10 18:00"])
                    .tz_localize("America/New_York").tz_convert("UTC"))
    rows = reaction_rows(acc, days)
    assert [str(days[r].date()) if r < len(days) else "after" for r in rows] == \
        ["2023-03-07", "2023-03-08", "2023-03-08", "after"]                # pre-open -> same day; later -> next session


def test_earnings_drift_buys_after_a_big_excess_reaction_only():
    idx = write_daily(["WIN", "MEH", "SPY"], days=120)
    df = pd.read_parquet(daily_path())
    jump_day = pd.Timestamp(f"{idx[60].date()} 00:00", tz="America/New_York").tz_convert("UTC")
    after = df["ts"] >= jump_day
    df.loc[(df["symbol"] == "WIN") & after, ["open", "high", "low", "close"]] *= 1.12    # +12% gap on the release
    df.loc[(df["symbol"] == "MEH") & after, ["open", "high", "low", "close"]] *= 1.01
    df.to_parquet(daily_path(), index=False)
    data = load_daily(["WIN", "MEH", "SPY"])
    acc = pd.Timestamp(f"{idx[60].date()} 07:00", tz="America/New_York").tz_convert("UTC")   # before the open
    events = pd.DataFrame({"symbol": ["WIN", "MEH"], "accepted": [acc, acc], "accession": ["a", "b"]})
    strat = EarningsDrift(["WIN", "MEH", "SPY"], threshold=0.05, hold_days=10, events=events)
    w = strat.target_weights(data)
    held = w.index[w["WIN"] > 0].tz_convert("America/New_York").date
    assert str(held[0]) == str(idx[60].date()) and len(held) == 10     # target on the reaction day -> next open
    assert w["MEH"].sum() == 0 and "SPY" not in w.columns
    assert check_no_lookahead(strat, data) == []


def test_earnings_release_on_the_last_bar_day_is_seen_consistently():
    # regression: a pre-open release on day D must count for D's bar even when D is the last bar
    idx = write_daily(["WIN", "SPY"], days=80)
    df = pd.read_parquet(daily_path())
    for k in range(20, 70, 5):               # many releases, several on would-be cut days
        day = pd.Timestamp(f"{idx[k].date()} 00:00", tz="America/New_York").tz_convert("UTC")
        df.loc[(df["symbol"] == "WIN") & (df["ts"] >= day), ["open", "high", "low", "close"]] *= 1.08
    df.to_parquet(daily_path(), index=False)
    data = load_daily(["WIN", "SPY"])
    acc = [pd.Timestamp(f"{idx[k].date()} 07:00", tz="America/New_York").tz_convert("UTC") for k in range(20, 70, 5)]
    events = pd.DataFrame({"symbol": "WIN", "accepted": acc, "accession": [str(i) for i in range(len(acc))]})
    strat = EarningsDrift(["WIN", "SPY"], threshold=0.05, hold_days=3, events=events)
    assert check_no_lookahead(strat, data, n_cuts=40) == []


def test_simultaneous_signals_fill_the_last_slot_deterministically():
    write_daily(["AAA", "BBB", "SPY"], days=80)
    data = load_daily(["AAA", "BBB", "SPY"])
    ts = pd.Timestamp("2023-03-01 07:00", tz="America/New_York").tz_convert("UTC")
    df = pd.read_parquet(daily_path())
    day = pd.Timestamp("2023-03-01 00:00", tz="America/New_York").tz_convert("UTC")
    df.loc[df["symbol"].isin(["AAA", "BBB"]) & (df["ts"] >= day), ["open", "high", "low", "close"]] *= 1.10
    df.to_parquet(daily_path(), index=False)
    data = load_daily(["AAA", "BBB", "SPY"])
    events = pd.DataFrame({"symbol": ["BBB", "AAA"], "accepted": [ts, ts], "accession": ["b", "a"]})   # identical times
    strat = EarningsDrift(["AAA", "BBB", "SPY"], threshold=0.05, hold_days=10, events=events)
    strat.MAX_POSITIONS = 1
    w = strat.target_weights(data)
    assert w["AAA"].sum() > 0 and w["BBB"].sum() == 0          # ties go alphabetically, every time
    reversed_events = events.iloc[::-1].reset_index(drop=True)
    w2 = EarningsDrift(["AAA", "BBB", "SPY"], threshold=0.05, hold_days=10, events=reversed_events)
    w2.MAX_POSITIONS = 1
    assert w2.target_weights(data).equals(w)
