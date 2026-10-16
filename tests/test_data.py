import pandas as pd
import pytest

from trader.data import quality as q
from trader.data import store
from trader.data.sessions import tag_sessions, to_session_hourly


@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))
    return tmp_path


def bars(ts, closes, **extra):
    df = pd.DataFrame({"ts": pd.to_datetime(ts, utc=True), "close": closes})
    df["open"] = df["close"]
    df["high"] = df["close"] + 1
    df["low"] = df["close"] - 1
    df["volume"] = 10.0
    for k, v in extra.items():
        df[k] = v
    return df


def ny_30m(day: str, start: str, end: str) -> pd.Series:
    """30-minute bar start times in New York time, returned as UTC."""
    idx = pd.date_range(f"{day} {start}", f"{day} {end}", freq="30min", tz="America/New_York", inclusive="left")
    return pd.Series(idx.tz_convert("UTC"))


# --- store -----------------------------------------------------------------

def test_upsert_merges_and_newer_rows_win():
    path = store.bars_path("test", "BTC/USD")
    assert path.name == "BTC-USD.parquet"
    assert store.upsert(path, bars(["2024-01-01 00:00", "2024-01-01 01:00"], [1.0, 2.0]), ["ts"]) == 2
    # one overlapping (corrected) bar + one new bar
    assert store.upsert(path, bars(["2024-01-01 01:00", "2024-01-01 02:00"], [2.5, 3.0]), ["ts"]) == 1
    df = store.read(path)
    assert list(df["close"]) == [1.0, 2.5, 3.0]
    assert store.last_ts(path) == pd.Timestamp("2024-01-01 02:00", tz="UTC")


def test_duckdb_views_see_stored_bars():
    store.upsert(store.bars_path("test", "ETH/USD"), bars(["2024-01-01 00:00"], [5.0], symbol="ETH/USD"), ["ts"])
    con = store.connect()
    assert con.execute("SELECT symbol, close FROM bars").fetchall() == [("ETH/USD", 5.0)]


# --- sessions --------------------------------------------------------------

def test_session_tags_follow_new_york_time_across_dst():
    # 09:00 and 09:30 New York in winter (UTC-5) and summer (UTC-4)
    ts = pd.Series(pd.to_datetime(
        ["2024-01-10 14:00", "2024-01-10 14:30", "2024-07-10 13:00", "2024-07-10 13:30", "2024-07-10 20:00"], utc=True))
    assert list(tag_sessions(ts)) == ["pre", "regular", "pre", "regular", "post"]


def test_hourly_bars_never_mix_sessions():
    ts = ny_30m("2024-07-10", "04:00", "20:00")
    raw = bars(ts, list(range(len(ts))), symbol="SPY")
    hourly = to_session_hourly(raw)

    local = hourly["ts"].dt.tz_convert("America/New_York").dt.strftime("%H:%M")
    regular = hourly[hourly["session"] == "regular"]
    assert list(local[hourly["session"] == "regular"]) == ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]
    assert list(local[hourly["session"] == "pre"]) == ["04:00", "05:00", "06:00", "07:00", "08:00", "09:00"]
    assert len(hourly[hourly["session"] == "post"]) == 4
    # 15:30 regular is a half bar: it closes at 16:00, not 16:30
    last = regular.iloc[-1]
    assert (last["ts_end"] - last["ts"]) == pd.Timedelta(minutes=30)
    # aggregation: first bar 09:30 covers the 09:30 and 10:00 thirty-minute bars
    first = regular.iloc[0]
    assert first["volume"] == 20.0
    assert first["close"] == raw.loc[raw["ts"] == first["ts"] + pd.Timedelta(minutes=30), "close"].item()


# --- quality ---------------------------------------------------------------

def test_ohlc_check_catches_impossible_bar():
    df = bars(["2024-01-01 00:00", "2024-01-01 01:00"], [10.0, 11.0])
    assert q.check_ohlc(df, "x").status == q.OK
    df.loc[1, "high"] = 5.0  # high below close
    assert q.check_ohlc(df, "x").status == q.FAIL


def test_gap_check_counts_missing_hours():
    df = bars(["2024-01-01 00:00", "2024-01-01 01:00", "2024-01-01 05:00"], [1.0, 1.0, 1.0])
    r = q.check_gaps(df, "x")
    assert r.status == q.WARN
    assert "3 missing hours" in r.detail and "longest gap 3h" in r.detail


def test_spike_check_flags_bad_tick_but_not_a_real_move():
    ts = pd.date_range("2024-01-01", periods=5, freq="h", tz="UTC")
    bad_tick = bars(ts, [100.0, 100.0, 300.0, 100.0, 100.0])
    real_move = bars(ts, [100.0, 100.0, 300.0, 300.0, 300.0])
    assert q.check_spikes(bad_tick, "x").status == q.WARN
    assert q.check_spikes(real_move, "x").status == q.OK


def test_staleness():
    df = bars(["2024-01-01 00:00"], [1.0])
    assert q.check_staleness(df, "x", pd.Timestamp("2024-01-01 02:00", tz="UTC"), 3).status == q.OK
    assert q.check_staleness(df, "x", pd.Timestamp("2024-01-01 05:00", tz="UTC"), 3).status == q.FAIL


def test_drift_between_sources():
    ts = pd.date_range("2024-01-01", periods=10, freq="h", tz="UTC")
    a = bars(ts, [100.0] * 10)
    assert q.check_drift(a, bars(ts, [100.05] * 10), "x").status == q.OK      # 5 bps
    assert q.check_drift(a, bars(ts, [100.5] * 10), "x").status == q.WARN     # 50 bps
    assert q.check_drift(a, bars(ts, [102.0] * 10), "x").status == q.FAIL     # 200 bps
