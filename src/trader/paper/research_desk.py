"""AI research desk (layer 2): a forward-only SHADOW with fake money, no orders. Rules frozen 2026-10-20.

Every month-end, layer 1 (strategies/quality_value.py) ranks the companies; for each of its top 20
the free AI (Llama 3.3 70B on Cloudflare Workers AI) reads a fact sheet built from the SEC filings
and writes a memo: buy or veto, thesis, risks, and what would make it sell. It may only use the
fact sheet, and the code checks it:
  - every citation must name a fact on the sheet with the right value
  - every number written in the thesis and risks must match a fact (years aside)
  - a buy needs at least 2 valid citations, a veto at least 1
A memo that fails any check is rejected: no valid reasons, no buy.

Two simulated portfolios ($100k each, equal weight, rebalanced monthly) start from START:
  "ai"    = picks with a valid buy memo        "rules" = all 20 picks (layer 1 alone)
The gap between them shows whether the AI's judgment adds anything.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from trader.paper.news_llama import MODEL, WorkersAIError, chat, extract_json

START = pd.Timestamp("2026-10-20", tz="UTC")
TOP_N = 20
NY = "America/New_York"

SYSTEM = """You are an equity analyst at a disciplined fund. You write a short memo on ONE company and
decide "buy" or "veto". Use ONLY the facts on the fact sheet; you know nothing else about the company.

Rules:
- Every claim must be backed by facts from the sheet. List each fact you rely on in "citations" with
  its exact key and its value exactly as shown on the sheet.
- Any number you write in "thesis" or "risks" must be copied exactly from the sheet.
- Veto only for a concrete reason visible in the facts (for example shrinking revenue, high leverage,
  profit not backed by cash flow, or a valuation that only looks cheap because profit just collapsed).
- "sell_if": one measurable condition from the same kind of facts that would make you sell.

Answer with JSON only:
{"decision": "buy" or "veto", "thesis": "2-3 sentences", "risks": "1-2 sentences", "sell_if": "...",
 "citations": [{"fact": "revenue_growth", "value": "+12.3%"}]}"""


def _bn(x) -> str:
    return "n/a" if pd.isna(x) else f"${x / 1e9:,.2f}B"


def _pct(x) -> str:
    return "n/a" if pd.isna(x) else f"{x * 100:+.1f}%"


def fact_sheet(symbol: str, pick: pd.Series, snap: pd.Series) -> dict[str, str]:
    """The facts the AI may use, formatted exactly as it must quote them."""
    g = lambda k: snap.get(k, np.nan)  # noqa: E731
    fcf = g("op_cash_flow") - (g("capex") if pd.notna(g("capex")) else 0)
    return {
        "symbol": symbol,
        "fiscal_year_end": str(pd.Timestamp(g("fiscal_year_end")).date()) if pd.notna(g("fiscal_year_end")) else "n/a",
        "revenue": _bn(g("revenue")), "revenue_previous_year": _bn(g("revenue_prev")),
        "revenue_growth": _pct(pick.get("revenue_growth")),
        "net_income": _bn(g("net_income")), "operating_cash_flow": _bn(g("op_cash_flow")), "free_cash_flow": _bn(fcf),
        "total_assets": _bn(g("assets")), "total_liabilities": _bn(g("liabilities")),
        "liabilities_to_assets": _pct(pick.get("leverage")), "return_on_assets": _pct(pick.get("roa")),
        "cash_return_on_assets": _pct(pick.get("cash_roa")), "accruals_to_assets": _pct(pick.get("accruals")),
        "earnings_yield": _pct(pick.get("earnings_yield")), "free_cash_flow_yield": _pct(pick.get("fcf_yield")),
        "market_cap": _bn(pick.get("market_cap")),
        "insider_buying_last_90_days": "yes: 2+ insiders bought" if pick.get("insider", 0) > 0 else "no",
        "layer1_rank_score": f"{pick.get('score', np.nan):.2f}",
    }


_NUM = re.compile(r"[-+]?\$?\d[\d,]*(?:\.\d+)?\s*[%B]?")


def _numbers(text: str) -> list[str]:
    return [m.group(0).strip() for m in _NUM.finditer(text or "")]


def _norm(v: str) -> str:
    return re.sub(r"[\s$,+]", "", str(v)).lower()


def check_memo(memo: dict, sheet: dict[str, str]) -> tuple[bool, list[str]]:
    """(valid, problems): citations must match the sheet; numbers in the text must come from it."""
    problems = []
    if not isinstance(memo, dict) or memo.get("decision") not in ("buy", "veto"):
        return False, ["decision must be buy or veto"]
    cites = memo.get("citations")
    if not isinstance(cites, list):
        return False, ["citations missing"]
    valid = 0
    for c in cites:
        if not isinstance(c, dict) or c.get("fact") not in sheet:
            problems.append(f"unknown fact {c.get('fact') if isinstance(c, dict) else c!r}")
        elif _norm(c.get("value")) != _norm(sheet[c["fact"]]):
            problems.append(f"{c['fact']}: cited {c.get('value')!r}, sheet says {sheet[c['fact']]!r}")
        else:
            valid += 1
    needed = 2 if memo["decision"] == "buy" else 1
    if valid < needed:
        problems.append(f"{valid} valid citation(s), needs {needed}")
    allowed = {_norm(v) for v in sheet.values()} | {_norm(n) for v in sheet.values() for n in _numbers(v)}
    for n in _numbers(f"{memo.get('thesis', '')} {memo.get('risks', '')}"):
        bare = _norm(n)
        if re.fullmatch(r"(19|20)\d\d", bare) or bare in ("1", "2", "3"):     # years and small counts
            continue
        if bare not in allowed:
            problems.append(f"number {n!r} is not on the fact sheet")
    return not problems, problems


def write_memo(sheet: dict[str, str], post=None) -> dict:
    kwargs = {"post": post} if post else {}
    try:
        text, _ = chat([{"role": "system", "content": SYSTEM},
                        {"role": "user", "content": "Fact sheet:\n" + "\n".join(f"{k}: {v}" for k, v in sheet.items())}],
                       700, MODEL, **kwargs)
    except WorkersAIError as e:
        return {"status": "error", "problems": [str(e)]}
    memo = extract_json(text)
    ok, problems = check_memo(memo, sheet)
    if not ok:
        return {"status": "rejected", "problems": problems, "memo": memo if isinstance(memo, dict) else {"raw": text[:500]}}
    return {"status": memo["decision"], "problems": [], "memo": memo}


def make_book(data, decision_row: int, strategy, post=None) -> dict:
    """Layer 1 picks at a month-end + one memo each. Returns the month's book."""
    from trader.data.fundamentals import as_of
    from trader.strategies.quality_value import universe_map

    picks = strategy.picks(data, decision_row).head(TOP_N)
    date = data.index[decision_row].tz_convert(NY).tz_localize(None).normalize()
    snap = as_of(strategy._inputs()[0], date)
    cik = universe_map().drop_duplicates("symbol").set_index("symbol")["cik"]
    memos = []
    for symbol, pick in picks.iterrows():
        row = snap.loc[cik[symbol]] if cik.get(symbol) in snap.index else pd.Series(dtype=float)
        sheet = fact_sheet(symbol, pick, row)
        result = write_memo(sheet, post)
        memos.append({"symbol": symbol, "sheet": sheet, **result})
    return {"decided": str(date.date()), "rules": list(picks.index),
            "ai": [m["symbol"] for m in memos if m["status"] == "buy"], "memos": memos}


def targets_from_books(books: list[dict], data, key: str) -> pd.DataFrame:
    """Equal-weight targets from monthly books, set on each book's decision day (filled next open)."""
    cols = list(data.close.columns)
    w = pd.DataFrame(np.nan, index=data.index, columns=cols)
    w.iloc[0] = 0.0
    days = data.index.tz_convert(NY).tz_localize(None).normalize()
    for b in sorted(books, key=lambda b: b["decided"]):
        rows = np.flatnonzero(days == pd.Timestamp(b["decided"]))
        if not len(rows):
            continue
        held = [s for s in b[key] if s in w.columns]
        target = pd.Series(0.0, index=cols)
        if held:
            target[held] = 1.0 / len(held)
        w.iloc[rows[0]] = target.to_numpy()
    return w.ffill()


def simulate(books: list[dict], data, settings) -> pd.DataFrame:
    """Daily equity (1.0 at START) of both portfolios and of holding SPY."""
    from trader.backtest.engine import run_backtest
    from trader.backtest.runner import cost_bps

    costs = cost_bps(data, settings, "alpaca_largecaps")
    out = {}
    for key in ("ai", "rules"):
        eq = run_backtest(data, targets_from_books(books, data, key), costs).equity
        out[key] = eq
    spy = pd.DataFrame(0.0, index=data.index, columns=data.close.columns)
    spy["SPY"] = 1.0
    out["spy"] = run_backtest(data, spy, costs).equity
    df = pd.DataFrame(out)
    df = df[df.index >= START]
    return df / df.iloc[0] if len(df) else df
