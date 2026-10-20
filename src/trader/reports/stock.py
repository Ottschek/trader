"""/analise <company>: one-picture stock report.

Price vs SPY (1 year), key stats, revenue / profit / free cash flow over 5 years (SEC filings),
a peer bubble map (same SEC industry: value vs quality, bubble = market cap), a scorecard
(quality, value, growth, momentum as percentiles vs ~1,500 companies), headlines, insider buying,
and 2-3 AI sentences that must only use numbers from the report's own fact sheet.
"""

from __future__ import annotations

import re

import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle

from trader.reports import data as rdata
from trader.reports import render as R

SYSTEM = """You write 2-3 sentences for a stock report: the company's main strengths and main risks,
using ONLY the fact sheet. Copy every number exactly as written on the sheet. Do not recommend buying
or selling, and do not predict the price. Answer with JSON only: {"comment": "..."}"""


# --- finding the company -------------------------------------------------------------------------

def resolve(query: str, coins: dict[str, str], companies: pd.DataFrame) -> dict:
    """{kind: coin|stock|ambiguous|unknown, ...}. `coins`: lookup word -> pair; `companies`: cik, symbol, name."""
    q = query.strip()
    key = q.upper().replace("/USD", "").replace("-USD", "")
    if key in coins:
        return {"kind": "coin", "pair": coins[key]}
    sym = q.upper().replace("-", ".")
    exact = companies[companies["symbol"].str.replace("-", ".") == sym]
    if len(exact):
        r = exact.iloc[0]
        return {"kind": "stock", "symbol": sym, "cik": int(r["cik"]), "name": r["name"]}
    words = [w for w in re.findall(r"[a-z0-9]+", q.lower()) if w not in ("inc", "corp", "the", "co")]
    if not words:
        return {"kind": "unknown", "query": q}
    names = companies["name"].str.lower()
    hits = companies[names.apply(lambda n: all(re.search(rf"\b{re.escape(w)}", n) for w in words))]
    starts = hits[hits["name"].str.lower().str.startswith(words[0])]
    hits = starts if len(starts) else hits
    hits = hits.drop_duplicates("cik")
    suffixes = {"inc", "corp", "corporation", "co", "company", "ltd", "plc", "holdings", "group", "the", "class", "a", "sa", "nv", "ag", "se"}
    strong = hits[hits["name"].str.lower().apply(lambda n: [w for w in re.findall(r"[a-z0-9]+", n) if w not in suffixes] == words)]
    if len(strong) == 1:                       # "apple" -> Apple Inc., not Apple Hospitality REIT
        hits = strong
    if len(hits) == 1:
        r = hits.iloc[0]
        return {"kind": "stock", "symbol": r["symbol"].replace("-", "."), "cik": int(r["cik"]), "name": r["name"]}
    if len(hits) > 1:
        return {"kind": "ambiguous", "query": q, "options": [f"{r.symbol} ({r.name})" for r in hits.head(6).itertuples()]}
    return {"kind": "unknown", "query": q}


# --- data ------------------------------------------------------------------------------------------

def company_facts(cik: int) -> pd.DataFrame:
    from trader.data.fundamentals import facts_rows
    from trader.data.sec import sec_get
    r = sec_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
    if r.status_code != 200:
        return pd.DataFrame()
    df = pd.DataFrame(facts_rows(cik, r.json().get("facts", {})))
    if df.empty:
        return df
    for c in ("start", "end", "filed"):
        df[c] = pd.to_datetime(df[c])
    return df


def annual_history(df: pd.DataFrame, years: int = 5) -> pd.DataFrame:
    """Latest reported (restated) annual figures per fiscal year: revenue, net income, free cash flow."""
    flows = df[df["concept"].isin(["revenue", "net_income", "op_cash_flow", "capex"])]
    if flows.empty:
        return pd.DataFrame()
    latest = flows.sort_values(["filed", "priority"]).groupby(["concept", "end"]).tail(1)
    t = latest.pivot_table(index="end", columns="concept", values="val").sort_index().tail(years)
    if "op_cash_flow" in t:
        t["free_cash_flow"] = t["op_cash_flow"] - t.get("capex", pd.Series(0, index=t.index)).fillna(0)
    return t


def insiders(symbol: str, days: int = 180) -> dict:
    from trader.strategies.insider import load_events
    try:
        ev = load_events()
    except ValueError:
        return {"purchases": 0, "insiders": 0, "value": 0.0}
    ev = ev[(ev["symbol"] == symbol) & (ev["filing_date"] > pd.Timestamp.now() - pd.Timedelta(days=days))]
    return {"purchases": int(len(ev)), "insiders": int(ev["owner_cik"].nunique()), "value": float(ev["value"].sum())}


def fact_sheet(info: dict, s: dict) -> dict[str, str]:
    sc = s["scores"]
    return {
        "company": f"{info['name']} ({info['symbol']})", "industry": s.get("industry") or "n/a",
        "price": f"${s['price']:,.2f}" if s.get("price") else "n/a", "market_cap": R.money(s.get("market_cap")),
        "return_1y": R.pct(s.get("ret_1y")), "spy_return_1y": R.pct(s.get("spy_1y")),
        "revenue": R.money(s.get("revenue")), "revenue_growth": R.pct(s.get("revenue_growth")),
        "net_income": R.money(s.get("net_income")), "free_cash_flow": R.money(s.get("fcf")),
        "return_on_assets": R.pct(s.get("roa")), "earnings_yield": R.pct(s.get("earnings_yield")),
        "free_cash_flow_yield": R.pct(s.get("fcf_yield")), "liabilities_to_assets": R.pct(s.get("leverage"), sign=False),
        **{f"{k}_score": ("n/a" if v is None else f"{v:.0f}/100") for k, v in sc.items()},
        "insider_purchases_6m": f"{s['insiders']['purchases']} by {s['insiders']['insiders']} insiders",
    }


def gather(info: dict, table: pd.DataFrame) -> dict:
    """Everything the report shows, for one company."""
    from trader.data.fundamentals import as_of, first_filed, ratios
    sym = info["symbol"]
    facts = company_facts(info["cik"])
    snap = as_of(first_filed(facts), pd.Timestamp.now()) if len(facts) else pd.DataFrame()
    snap = snap.iloc[0] if len(snap) else pd.Series(dtype=float)
    px = rdata.stock_daily([sym, "SPY"], 400)
    price = float(px[sym].dropna().iloc[-1]) if sym in px and px[sym].notna().any() else None
    year = px.loc[px.index >= px.index[-1] - pd.Timedelta(days=365)] if len(px) else px
    ret = lambda c, d: (float(year[c].dropna().iloc[-1] / year[c].dropna().loc[year.index[-1] - pd.Timedelta(days=d):].iloc[0] - 1)  # noqa: E731
                        if c in year and year[c].notna().sum() > 2 else None)
    if sym in table.index:
        row = table.loc[sym]
        mcap = row["market_cap"]
    else:
        mcap = price * snap["shares"] if price and "shares" in snap and pd.notna(snap["shares"]) else np.nan
        row = ratios(pd.DataFrame([snap]).set_axis([sym]), pd.Series([mcap], index=[sym])).iloc[0]
        if sym in px and len(px[sym].dropna()) > 260:
            c = px[sym].dropna()
            row["momentum"] = c.iloc[-22] / c.iloc[-252] - 1
    industry = None
    sic = None
    if "sic" in table and sym in table.index:
        sic = table.at[sym, "sic"]
    if sic is None or (isinstance(sic, float) and np.isnan(sic)):
        from trader.data.sec import sec_get
        r = sec_get(f"https://data.sec.gov/submissions/CIK{info['cik']:010d}.json")
        if r.status_code == 200:
            sic, industry = str(r.json().get("sic") or ""), r.json().get("sicDescription")
    if industry is None and sic:
        from trader.data.sec import company_info_path
        from trader.data.store import read
        ci = read(company_info_path())
        m = ci[ci["sic"] == str(sic)]
        industry = m["sic_description"].iloc[0] if len(m) else None
    peers, peer_group = table.iloc[0:0], None
    if sic and "sic" in table:
        codes = table["sic"].fillna("").astype(str)
        for digits, label in ((4, industry or f"SIC {sic}"), (3, f"industry group {str(sic)[:3]}x"), (2, f"sector {str(sic)[:2]}xx")):
            peers = table[codes.str.startswith(str(sic)[:digits]) & table["market_cap"].notna()]
            peer_group = label
            if len(peers) >= 5:
                break
    return {
        "price": price, "prices": year, "ret_1y": ret(sym, 365), "spy_1y": ret("SPY", 365), "ret_3m": ret(sym, 91),
        "market_cap": mcap, "revenue": snap.get("revenue"), "revenue_growth": row.get("revenue_growth"),
        "net_income": snap.get("net_income"),
        "fcf": (snap.get("op_cash_flow") - (snap.get("capex") if pd.notna(snap.get("capex")) else 0)) if pd.notna(snap.get("op_cash_flow")) else None,
        "roa": row.get("roa"), "earnings_yield": row.get("earnings_yield"), "fcf_yield": row.get("fcf_yield"),
        "leverage": row.get("leverage"), "scores": rdata.scores(row, table), "history": annual_history(facts) if len(facts) else pd.DataFrame(),
        "peers": peers, "peer_group": peer_group, "industry": industry, "insiders": insiders(sym),
        "news": rdata.ticker_headlines(sym.replace(".", "-")),
        "fiscal_year_end": snap.get("fiscal_year_end"), "row": row,
    }


def ai_comment(sheet: dict[str, str], post=None, system: str = SYSTEM, name_key: str = "company") -> tuple[str | None, str]:
    """(comment, status): the AI's 2-3 sentences, or None when it fails the checks twice."""
    from trader.paper.news_llama import WorkersAIError, chat, extract_json
    from trader.paper.research_desk import check_memo
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": "Fact sheet:\n" + "\n".join(f"{k}: {v}" for k, v in sheet.items())}]
    kwargs = {"post": post} if post else {}
    for _ in range(2):
        try:
            text, _ = chat(messages, 300, **kwargs)
        except WorkersAIError as e:
            return None, f"AI unavailable ({e})"
        answer = extract_json(text) or {}
        comment = str(answer.get("comment", "")).strip()
        ok, problems = check_memo({"decision": "veto", "thesis": comment, "risks": "",
                                   "citations": [{"fact": name_key, "value": sheet[name_key]}]}, sheet)
        if comment and ok:
            return comment, "checked"
        messages += [{"role": "assistant", "content": text[:1500]},
                     {"role": "user", "content": f"Not accepted: {'; '.join(problems) or 'empty'}. Use only numbers exactly as on the sheet."}]
    return None, "AI comment withheld: it made claims the data doesn't back"


# --- picture ---------------------------------------------------------------------------------------

def render(info: dict, s: dict, comment: str | None, comment_status: str, path) -> str:
    fig = R.figure(1500)
    sym = info["symbol"]
    day = s["prices"][sym].dropna() if sym in s["prices"] else pd.Series(dtype=float)
    d1 = float(day.iloc[-1] / day.iloc[-2] - 1) if len(day) > 1 else None
    R.header(fig, f"{info['name']}", f"{sym} · {s.get('industry') or 'industry n/a'} · {pd.Timestamp.now(tz='Europe/Vienna'):%d %b %Y}",
             right_big=f"${s['price']:,.2f}" if s.get("price") else None,
             right_small=f"{R.pct(d1)} today · {R.money(s.get('market_cap'))}", right_color=R.change_color(d1))

    # price vs SPY
    ax = fig.add_axes((0.07, 0.64, 0.53, 0.235))
    ax.set_title("1 year: price vs S&P 500 (rebased to 100)")
    if len(day):
        p = s["prices"]
        for col, color, lw in (("SPY", R.ACCENT2, 1.2), (sym, R.ACCENT, 1.8)):
            if col in p and p[col].notna().any():
                c = p[col].dropna()
                ax.plot(c.index, c / c.iloc[0] * 100, color=color, lw=lw, label=col)
        ax.legend(frameon=False, fontsize=8, loc="upper left")
        ax.tick_params(axis="x", labelsize=7)
        for lbl in ax.get_xticklabels():
            lbl.set_rotation(0)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    # key stats
    st = R.card(fig, (0.63, 0.625, 0.33, 0.27), "Key numbers")
    rows = [("Market cap", R.money(s.get("market_cap"))), ("1-year return", R.pct(s.get("ret_1y"))),
            ("S&P 500, 1 year", R.pct(s.get("spy_1y"))), ("Revenue (last FY)", R.money(s.get("revenue"))),
            ("Revenue growth", R.pct(s.get("revenue_growth"))), ("Net income", R.money(s.get("net_income"))),
            ("Free cash flow", R.money(s.get("fcf"))), ("Return on assets", R.pct(s.get("roa"))),
            ("Earnings yield (1/PE)", R.pct(s.get("earnings_yield"))), ("Debt: liabilities/assets", R.pct(s.get("leverage"), sign=False))]
    for i, (k, v) in enumerate(rows):
        y = 0.86 - i * 0.085
        st.text(0, y, k, transform=st.transAxes, fontsize=8.5, color=R.TEXT2, va="center")
        st.text(1, y, v, transform=st.transAxes, fontsize=8.5, color=R.TEXT, va="center", ha="right", fontweight="bold")

    # financial history
    ax = fig.add_axes((0.07, 0.36, 0.40, 0.21))
    ax.set_title("Revenue, profit, free cash flow ($B)")
    h = s["history"]
    if len(h):
        x = np.arange(len(h))
        for k, (col, color) in enumerate((("revenue", R.MUTED), ("net_income", R.ACCENT), ("free_cash_flow", R.GOOD))):
            if col in h:
                ax.bar(x + (k - 1) * 0.27, h[col] / 1e9, width=0.26, color=color, label=col.replace("_", " "))
        ax.set_xticks(x, [f"FY{d.year}" for d in h.index], fontsize=7.5)
        ax.legend(frameon=False, fontsize=7, loc="upper left")
        ax.axhline(0, color=R.BORDER, lw=0.8)
    else:
        ax.text(0.5, 0.5, "no SEC financial data", ha="center", va="center", color=R.TEXT3, transform=ax.transAxes)

    # peer bubble map
    ax = fig.add_axes((0.56, 0.36, 0.40, 0.21))
    ax.set_title("Peers: value vs quality")
    ax.text(0.99, 0.97, s.get("peer_group") or "", transform=ax.transAxes, fontsize=7, color=R.TEXT3, va="top", ha="right")
    peers = s["peers"]
    row = s["row"]
    if len(peers) >= 2:
        pts = peers.drop(index=[sym], errors="ignore").dropna(subset=["earnings_yield", "roa"]).copy()
        pts = pts[(pts["earnings_yield"].abs() < 0.5) & (pts["roa"].abs() < 0.6)]
        size = (pts["market_cap"] / pts["market_cap"].max()) ** 0.5 * 900 + 20
        ax.scatter(pts["earnings_yield"] * 100, pts["roa"] * 100, s=size, color=R.ACCENT2, alpha=0.45, edgecolor="white", lw=0.8)
        for name, r_ in pts.nlargest(5, "market_cap").iterrows():
            if name != sym:
                ax.annotate(name, (r_["earnings_yield"] * 100, r_["roa"] * 100), fontsize=6.5, color=R.TEXT2, ha="center", va="center")
    if pd.notna(row.get("earnings_yield")) and pd.notna(row.get("roa")):
        ax.scatter([row["earnings_yield"] * 100], [row["roa"] * 100], s=260, color=R.ACCENT, edgecolor="white", lw=1.5, zorder=5)
        ax.annotate(sym, (row["earnings_yield"] * 100, row["roa"] * 100), fontsize=8, fontweight="bold", color=R.TEXT,
                    xytext=(0, 12), textcoords="offset points", ha="center")
    ax.margins(0.15)
    ax.set_xlabel("earnings yield % (cheaper →)", fontsize=7.5)
    ax.set_ylabel("return on assets % (better ↑)", fontsize=7.5)
    if len(peers) < 2:
        ax.text(0.5, 0.92, "few tracked peers in this industry", ha="center", va="top", color=R.TEXT3, fontsize=7.5, transform=ax.transAxes)

    # scorecard
    sc = R.card(fig, (0.04, 0.125, 0.44, 0.19), "Scorecard (vs ~1,500 US companies)")
    for i, (k, v) in enumerate(s["scores"].items()):
        y = 0.68 - i * 0.19
        sc.text(0, y, k.capitalize(), transform=sc.transAxes, fontsize=9, color=R.TEXT2, va="center")
        sc.add_patch(Rectangle((0.27, y - 0.05), 0.58, 0.1, transform=sc.transAxes, color=R.BORDER))
        if v is not None:
            color = R.GOOD if v >= 66 else R.WARNING if v >= 33 else R.CRITICAL
            sc.add_patch(Rectangle((0.27, y - 0.05), 0.58 * v / 100, 0.1, transform=sc.transAxes, color=color))
        sc.text(1, y, "n/a" if v is None else f"{v:.0f}", transform=sc.transAxes, fontsize=9.5, fontweight="bold", color=R.TEXT, va="center", ha="right")

    # news + insiders
    nw = R.card(fig, (0.52, 0.125, 0.44, 0.19), "Latest news & insiders")
    ins = s["insiders"]
    nw.text(0, 0.80, f"Insider purchases (6 months): {ins['purchases']} by {ins['insiders']} insider(s), {R.money(ins['value'])}",
            transform=nw.transAxes, fontsize=7.8, color=R.TEXT2, va="top")
    news = s["news"]
    for i, t in enumerate(news["title"].head(4) if len(news) else []):
        nw.text(0, 0.62 - i * 0.17, "• " + (t if len(t) <= 60 else t[:58].rstrip() + "…"), transform=nw.transAxes,
                fontsize=7.8, color=R.TEXT, va="top")
    if not len(news):
        nw.text(0, 0.6, "no recent headlines", transform=nw.transAxes, fontsize=8, color=R.TEXT3, va="top")

    # AI comment
    ai = R.card(fig, (0.04, 0.03, 0.92, 0.085), "AI comment (fact-checked)" if comment else "AI comment")
    ai.text(0, 0.62, R.wrap(comment or comment_status, 108), transform=ai.transAxes, fontsize=8.3,
            color=R.TEXT if comment else R.TEXT3, va="top")
    R.footer(fig, "Data: SEC filings, Alpaca, Yahoo Finance · Scores are percentiles, not advice · Not financial advice")
    return R.save(fig, path)
