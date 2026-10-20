import numpy as np
import pandas as pd
import pytest

from trader.data import fundamentals as fd
from trader.data.daily import daily_path, load_daily
from trader.backtest.lookahead import check_no_lookahead
from trader.strategies.quality_value import QualityValue, insider_flags, month_end_decisions


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_DATA_DIR", str(tmp_path))


def fact(val, end, filed, start=None, form="10-K", accn="a"):
    return {"val": val, "end": end, "filed": filed, "form": form, "accn": accn, **({"start": start} if start else {})}


def test_facts_keep_annual_flows_switch_tags_and_use_the_first_filing():
    facts = {"us-gaap": {
        "SalesRevenueNet": {"units": {"USD": [fact(100, "2018-12-31", "2019-02-20", "2018-01-01")]}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            fact(120, "2019-12-31", "2020-02-20", "2019-01-01"),
            fact(100, "2018-12-31", "2020-02-20", "2018-01-01"),                 # repeated next year: not "new"
            fact(30, "2019-09-30", "2019-10-30", "2019-07-01", form="10-Q")]}},  # a quarter: not annual
        "Assets": {"units": {"USD": [fact(500, "2019-12-31", "2020-02-20"), fact(450, "2019-09-30", "2019-10-30", form="10-Q")]}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [fact(10, "2019-12-31", "2020-02-20", "2019-10-01", form="10-Q")]}},
    }}
    df = fd.first_filed(pd.DataFrame(fd.facts_rows(1, facts)).assign(
        start=lambda d: pd.to_datetime(d["start"]), end=lambda d: pd.to_datetime(d["end"]), filed=lambda d: pd.to_datetime(d["filed"])))
    rev = df[df.concept == "revenue"].sort_values("end")
    assert list(rev["val"]) == [100, 120] and rev["filed"].iloc[0] == pd.Timestamp("2019-02-20")
    snap = fd.as_of(df, pd.Timestamp("2019-11-30")).loc[1]
    assert snap["revenue"] == 100 and snap["assets"] == 450 and np.isnan(snap.get("revenue_prev", np.nan))
    snap = fd.as_of(df, pd.Timestamp("2020-03-01")).loc[1]
    assert snap["revenue"] == 120 and snap["revenue_prev"] == 100 and snap["assets"] == 500 and snap["shares"] == 10
    assert fd.as_of(df, pd.Timestamp("2022-01-01")).empty                             # all figures stale by then


def test_month_end_decisions_use_the_exchange_calendar():
    idx = pd.DatetimeIndex([pd.Timestamp(f"{d} 00:00", tz="America/New_York") for d in pd.bdate_range("2024-11-25", "2024-12-31").date]).tz_convert("UTC")
    days = [str(idx[i].tz_convert("America/New_York").date()) for i in month_end_decisions(idx)]
    assert days == ["2024-11-29", "2024-12-31"]


def test_insider_flags_need_two_insiders_and_100k():
    ev = pd.DataFrame({"symbol": ["A", "A", "B", "C", "C"], "owner_cik": [1, 2, 1, 1, 2], "value": [60_000, 50_000, 500_000, 10_000, 10_000],
                       "filing_date": pd.to_datetime(["2024-03-01", "2024-03-10", "2024-03-05", "2024-03-01", "2024-03-02"])})
    assert insider_flags(ev, pd.Timestamp("2024-03-31")) == {"A"}
    assert insider_flags(ev, pd.Timestamp("2024-08-31")) == set()                    # older than 90 days


def synthetic_world():
    idx = pd.bdate_range("2023-01-02", "2023-12-29")
    rows, raw = [], []
    for k, s in enumerate(["GOOD", "LOSS", "THIN", "SPY"]):
        price = 50.0 + k
        for t in idx:
            ts = pd.Timestamp(f"{t.date()} 00:00", tz="America/New_York").tz_convert("UTC")
            rows.append({"ts": ts, "symbol": s, "open": price, "high": price, "low": price, "close": price,
                         "volume": 1e3 if s == "THIN" else 5e6})
        for m in pd.date_range("2023-01-01", "2023-12-01", freq="MS"):
            raw.append({"ts": pd.Timestamp(m, tz="America/New_York").tz_convert("UTC"), "symbol": s, "close": price, "volume": 1e8})
    daily_path().parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(daily_path(), index=False)
    f = []
    for cik, ni in ((1, 5e9), (2, -1e9), (3, 5e9)):
        for concept, val in (("net_income", ni), ("op_cash_flow", 6e9), ("capex", 1e9), ("revenue", 50e9)):
            f.append({"cik": cik, "concept": concept, "start": pd.Timestamp("2022-01-01"), "end": pd.Timestamp("2022-12-31"),
                      "val": val, "filed": pd.Timestamp("2023-02-15")})
        for concept, val in (("assets", 60e9), ("liabilities", 20e9), ("shares", 1e9)):
            f.append({"cik": cik, "concept": concept, "start": None, "end": pd.Timestamp("2022-12-31"), "val": val,
                      "filed": pd.Timestamp("2023-02-15")})
    cmap = pd.DataFrame({"cik": [1, 2, 3], "symbol": ["GOOD", "LOSS", "THIN"]})
    ev = pd.DataFrame({"symbol": [], "owner_cik": [], "value": [], "filing_date": pd.to_datetime([])})
    return pd.DataFrame(f), pd.DataFrame(raw), ev, cmap


def test_quality_value_buys_only_profitable_liquid_companies_after_their_filing():
    fund, raw, ev, cmap = synthetic_world()
    data = load_daily(["GOOD", "LOSS", "THIN", "SPY"])
    make = lambda: QualityValue(["GOOD", "LOSS", "THIN"], top_n=2, fundamentals=fund, monthly_raw=raw, events=ev, cik_map=cmap)  # noqa: E731
    w = make().target_weights(data)
    assert w["LOSS"].max() == 0 and w["THIN"].max() == 0                 # a loss-maker and an illiquid stock
    assert w["GOOD"].max() == pytest.approx(0.5)
    held = w.index[w["GOOD"] > 0][0].tz_convert("America/New_York").date()
    assert str(held) == "2023-02-28"                                     # first month-end after the Feb 15 filing
    assert check_no_lookahead(make(), data, 3) == []


# --- layer 2: memo checks ------------------------------------------------------------------

from trader.paper import research_desk as rd   # noqa: E402

SHEET = {"symbol": "GOOD", "revenue": "$50.00B", "revenue_growth": "+12.3%", "net_income": "$5.00B",
         "liabilities_to_assets": "+33.3%", "earnings_yield": "+9.1%", "insider_buying_last_90_days": "no"}


def test_a_memo_with_checked_reasons_passes():
    memo = {"decision": "buy", "thesis": "Revenue grew +12.3% to $50.00B and earnings yield is 9.1%.",
            "risks": "Debt is +33.3% of assets.", "sell_if": "revenue growth turns negative",
            "citations": [{"fact": "revenue_growth", "value": "+12.3%"}, {"fact": "earnings_yield", "value": "+9.1%"}]}
    assert rd.check_memo(memo, SHEET) == (True, [])


def test_made_up_numbers_or_citations_reject_the_memo():
    base = {"decision": "buy", "thesis": "Solid.", "risks": "None.",
            "citations": [{"fact": "revenue_growth", "value": "+12.3%"}, {"fact": "earnings_yield", "value": "+9.1%"}]}
    ok, problems = rd.check_memo({**base, "thesis": "Margins of 45% are excellent."}, SHEET)
    assert not ok and "45%" in problems[0]
    ok, problems = rd.check_memo({**base, "citations": [{"fact": "revenue_growth", "value": "+20%"},
                                                         {"fact": "earnings_yield", "value": "+9.1%"}]}, SHEET)
    assert not ok and any("sheet says" in p for p in problems)
    ok, problems = rd.check_memo({**base, "citations": [{"fact": "moat", "value": "wide"}]}, SHEET)
    assert not ok and any("unknown fact" in p for p in problems)
    assert rd.check_memo({**base, "decision": "maybe"}, SHEET)[0] is False
    assert rd.check_memo({**base, "thesis": "Founded in 1998, profitable since 2004."}, SHEET)[0] is True   # years are fine


def test_books_become_equal_weight_portfolios():
    idx = pd.DatetimeIndex([pd.Timestamp(f"2026-10-{d} 00:00", tz="America/New_York") for d in (29, 30)]
                           + [pd.Timestamp(f"2026-11-0{d} 00:00", tz="America/New_York") for d in (2, 3)]).tz_convert("UTC")
    close = pd.DataFrame(1.0, index=idx, columns=["A", "B", "C", "SPY"])
    data = type("D", (), {"close": close, "index": idx})()
    w = rd.targets_from_books([{"decided": "2026-10-30", "ai": ["A"], "rules": ["A", "B"]}], data, "rules")
    assert w.iloc[0].sum() == 0 and w.iloc[1][["A", "B"]].tolist() == [0.5, 0.5] and w.iloc[3]["A"] == 0.5
