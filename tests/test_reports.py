import pandas as pd

from trader.reports import news, stock
from trader.reports import data as rdata

COMPANIES = pd.DataFrame({"cik": [1, 2, 3, 4, 5], "symbol": ["AAPL", "APLE", "BAC", "BMO", "BRK-B"],
                          "name": ["Apple Inc.", "Apple Hospitality REIT, Inc.", "BANK OF AMERICA CORP /DE/",
                                   "BANK OF MONTREAL /CAN/", "BERKSHIRE HATHAWAY INC"]})
COINS = {"BTC": "BTC/USD", "BITCOIN": "BTC/USD"}


def test_names_tickers_and_coins_resolve():
    assert stock.resolve("apple", COINS, COMPANIES)["symbol"] == "AAPL"            # exact name beats Apple Hospitality
    assert stock.resolve("AAPL", COINS, COMPANIES)["symbol"] == "AAPL"
    assert stock.resolve("brk.b", COINS, COMPANIES)["symbol"] == "BRK.B"
    assert stock.resolve("bitcoin", COINS, COMPANIES) == {"kind": "coin", "pair": "BTC/USD"}
    r = stock.resolve("bank", COINS, COMPANIES)
    assert r["kind"] == "ambiguous" and len(r["options"]) == 2
    assert stock.resolve("bank of america", COINS, COMPANIES)["symbol"] == "BAC"
    assert stock.resolve("zzzz", COINS, COMPANIES)["kind"] == "unknown"


def test_percentiles_and_scores():
    assert rdata.pct_of(pd.Series([1, 2, 3, 4]), 3.5) == 75.0
    table = pd.DataFrame({"roa": [0.0, 0.1, 0.2], "cash_roa": [0.0, 0.1, 0.2], "accruals": [0.1, 0.0, -0.1],
                          "leverage": [0.9, 0.5, 0.1], "earnings_yield": [0.01, 0.05, 0.1], "fcf_yield": [0.01, 0.05, 0.1],
                          "revenue_growth": [0.0, 0.1, 0.2], "momentum": [-0.1, 0.0, 0.1]})
    best = rdata.scores(table.iloc[2], table)
    assert best["quality"] > 80 and best["value"] > 80 and rdata.scores(table.iloc[0], table)["quality"] < 20


HEADS = pd.DataFrame({"title": ["Oil prices jump as OPEC cuts output", "Bitcoin slides below $80,000 on ETF outflows",
                                "Stocks rally as tech rebounds"], "source": ["CNBC", "CoinDesk", "MarketWatch"]})


def test_briefing_explanations_must_cite_and_quote_real_headlines():
    sheet = {"Oil": "+2.1%", "Bitcoin": "-1.5%", "Nasdaq-100": "+1.2%"}
    items = [
        {"topic": "oil", "text": "Oil rose 2.1% after OPEC cut output.", "headlines": [0], "quote": "Oil prices jump as OPEC"},
        {"topic": "crypto", "text": "Bitcoin fell below $80,000 on ETF outflows.", "headlines": [1], "quote": "Bitcoin slides below $80,000 on"},
        {"topic": "stocks", "text": "The Nasdaq-100 rose 1.2% as tech rebounded.", "headlines": [2], "quote": "Oil prices jump as OPEC"},  # wrong quote
        {"topic": "gold", "text": "Gold rose 3% on war fears.", "headlines": [0], "quote": "Oil prices jump as OPEC"},                  # 3% invented
        {"topic": "oil", "text": "Oil rose.", "headlines": [9], "quote": "Oil prices jump as OPEC"},                                   # no such headline
    ]
    good, dropped = news.check_items(items, HEADS, sheet)
    assert [g["topic"] for g in good] == ["oil", "crypto"] and dropped == 3
    assert good[0]["sources"] == ["CNBC"]


def test_stock_fact_sheet_formats_numbers_the_ai_must_copy():
    s = {"price": 210.0, "market_cap": 3.2e12, "ret_1y": 0.25, "spy_1y": 0.15, "revenue": 4e11, "revenue_growth": 0.064,
         "net_income": 1.1e11, "fcf": 9.9e10, "roa": 0.29, "earnings_yield": 0.034, "fcf_yield": 0.03, "leverage": 0.72,
         "scores": {"quality": 60.4, "value": 28.0, "growth": None, "momentum": 67.0}, "industry": "Electronic Computers",
         "insiders": {"purchases": 0, "insiders": 0, "value": 0.0}}
    sheet = stock.fact_sheet({"name": "Apple Inc.", "symbol": "AAPL"}, s)
    assert sheet["market_cap"] == "$3.20T" and sheet["revenue_growth"] == "+6.4%" and sheet["growth_score"] == "n/a"
    assert sheet["quality_score"] == "60/100" and sheet["liabilities_to_assets"] == "72.0%"
