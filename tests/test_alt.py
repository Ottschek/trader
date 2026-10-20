import json

import pandas as pd
import pytest

from trader.data import alt


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))


NOW = pd.Timestamp("2026-10-18 21:00", tz="UTC")


def test_stablecoin_chart_parsing():
    rows = [{"date": "1700000000", "totalCirculatingUSD": {"peggedUSD": 9.1e10}},
            {"date": "1700086400", "totalCirculatingUSD": {"peggedUSD": 9.2e10}},
            {"date": "1700172800", "totalCirculatingUSD": {}}]
    df = alt.parse_stablecoin_chart(rows, "USDT")
    assert list(df["circulating_usd"]) == [9.1e10, 9.2e10]
    assert str(df["ts"].dt.tz) == "UTC" and (df["coin"] == "USDT").all()


def test_pageviews_parsing_and_renamed_titles_add_up():
    a = alt.parse_pageviews([{"timestamp": "2024010100", "views": 10}, {"timestamp": "2024010200", "views": 20}])
    b = alt.parse_pageviews([{"timestamp": "2024010200", "views": 5}])
    total = a.add(b, fill_value=0)
    assert list(total) == [10, 25]
    assert alt.parse_pageviews([]).empty


FARSIDE = """
<table>
<thead><tr><th></th><th>IBIT</th><th>FBTC</th><th>Total</th></tr></thead>
<tbody>
<tr><td>11 Jan 2024</td><td>111.7</td><td>227.0</td><td>655.3</td></tr>
<tr><td>12 Jan 2024</td><td>386.0</td><td>(12.5)</td><td>-</td></tr>
<tr><td>Total</td><td>497.7</td><td>214.5</td><td>655.3</td></tr>
<tr><td>Average</td><td>1</td><td>2</td><td>3</td></tr>
</tbody></table>
"""


def test_farside_table_parsing_handles_negatives_dashes_and_summary_rows():
    df = alt.parse_farside(FARSIDE)
    assert sorted(df["fund"].unique()) == ["FBTC", "IBIT", "Total"]
    assert df["ts"].dt.strftime("%Y-%m-%d").unique().tolist() == ["2024-01-11", "2024-01-12"]
    jan12 = df[df["ts"].dt.day == 12].set_index("fund")["flow_musd"]
    assert jan12["FBTC"] == -12.5 and jan12["Total"] == 0.0 and jan12["IBIT"] == 386.0


def test_polymarket_snapshot_parsing():
    markets = [
        {"id": 1, "slug": "fed-cut", "question": "Fed cuts in December?", "outcomes": json.dumps(["Yes", "No"]),
         "outcomePrices": json.dumps(["0.62", "0.38"]), "volume24hr": 12345.6, "liquidity": 999, "endDate": "2026-12-10"},
        {"id": 2, "outcomes": "not json", "outcomePrices": "[]"},
    ]
    df = alt.parse_polymarket(markets, NOW)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["price"] == 0.62 and row["outcome"] == "Yes" and row["ts"] == NOW.floor("h")


def test_listing_detection_needs_a_baseline_then_reports_new_pairs():
    assert alt.new_listings(None, ["BTC-USD"], "coinbase", NOW).empty
    df = alt.new_listings(["BTC-USD"], ["BTC-USD", "NEW-USD"], "coinbase", NOW)
    assert list(df["pair"]) == ["NEW-USD"] and list(df["exchange"]) == ["coinbase"]
    assert alt.new_listings(["A", "B"], ["A"], "kraken", NOW).empty      # delistings aren't listings


def test_daily_sources_refresh_once_a_day():
    state = {}
    assert alt.due(state, "stablecoins", NOW)
    alt.mark(state, "stablecoins", NOW)
    assert not alt.due(state, "stablecoins", NOW + pd.Timedelta(hours=5))
    assert alt.due(state, "stablecoins", NOW + pd.Timedelta(hours=21))


def test_collect_all_skips_daily_sources_when_fresh_and_retries_failures(monkeypatch):
    calls = []

    def fake_run(name, fn, *args):
        calls.append(name)
        return None if name == "alt pageviews" else 1    # pageviews fail this time

    class S:
        alt_data = {"stablecoins": {}, "wikipedia": {}, "polymarket_top_n": 5}

    alt.collect_all(S(), NOW, fake_run)
    assert calls == ["alt stablecoins", "alt pageviews", "alt polymarket", "alt listings"]   # no more ETF flows
    calls.clear()
    alt.collect_all(S(), NOW + pd.Timedelta(hours=1), fake_run)
    assert calls == ["alt pageviews", "alt polymarket", "alt listings"]   # failed daily source retried, fresh ones skipped
