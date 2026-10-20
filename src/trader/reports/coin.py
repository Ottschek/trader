"""/analise <coin>: one-picture crypto report.

Price (1 year), returns, volatility and drawdown; Hyperliquid funding (what longs pay shorts) and
open interest; Wikipedia attention; total USDT+USDC supply; crypto headlines; and 2-3 AI sentences
that must only use numbers from the report's own fact sheet.
"""

from __future__ import annotations

import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd

from trader.reports import data as rdata
from trader.reports import render as R

SYSTEM = """You write 2-3 sentences for a crypto report: what stands out in the price trend, positioning
(funding, open interest) and attention, using ONLY the fact sheet. Copy every number exactly as written.
Do not recommend buying or selling, and do not predict the price. Answer with JSON only: {"comment": "..."}"""


def coin_lookup(settings) -> dict[str, str]:
    """Words that mean a coin (BTC, BITCOIN, ...) -> its pair."""
    names = (settings.news or {}).get("assets", {})
    out = {}
    for pair in settings.crypto:
        coin = pair.split("/")[0]
        out[coin] = pair
        if pair in names:
            out[names[pair].upper().replace(" ", "")] = pair
            out[names[pair].upper().split()[0]] = pair if names[pair].upper().split()[0] not in out else out[names[pair].upper().split()[0]]
    return out


def gather(pair: str, settings) -> dict:
    from trader.data.alt import alt_path
    from trader.data.store import funding_path, read, snapshots_path
    coin = pair.split("/")[0]
    px = rdata.crypto_daily([pair], 400)
    c = px[pair].dropna() if pair in px else pd.Series(dtype=float)
    year = c[c.index >= c.index[-1] - pd.Timedelta(days=365)] if len(c) else c
    ret = lambda d: float(year.iloc[-1] / year.loc[year.index[-1] - pd.Timedelta(days=d):].iloc[0] - 1) if len(year) > 2 else None  # noqa: E731
    vol = float(np.log(year).diff().tail(30).std() * np.sqrt(365)) if len(year) > 31 else None
    f = read(funding_path(coin))
    funding = (f.set_index("ts")["funding_rate"].resample("D").mean() * 24 * 365).tail(120) if len(f) else pd.Series(dtype=float)
    snaps = read(snapshots_path())
    oi = snaps[snaps["coin"] == coin].set_index("ts")["open_interest"].resample("D").last().dropna().tail(120) if len(snaps) else pd.Series(dtype=float)
    pv = read(alt_path("pageviews"))
    views = pv[pv["symbol"] == pair].set_index("ts")["views"].sort_index().tail(400) if len(pv) else pd.Series(dtype=float)
    sc = read(alt_path("stablecoins"))
    stable = sc.pivot_table(index="ts", columns="coin", values="circulating_usd").sum(axis=1).sort_index() if len(sc) else pd.Series(dtype=float)
    names = (settings.news or {}).get("assets", {})
    name = names.get(pair, coin)
    news = pd.concat([rdata.ticker_headlines(f"{coin}-USD", 8), rdata.headlines(["crypto"], hours=72)], ignore_index=True)
    if len(news):
        mention = news["title"].str.contains(rf"\b(?:{coin}|{name.split()[0]})\b", case=False, regex=True)
        news = news[mention].drop_duplicates("title").head(5)
    return {
        "pair": pair, "coin": coin, "name": name, "prices": year,
        "price": float(year.iloc[-1]) if len(year) else None,
        "d1": float(year.iloc[-1] / year.iloc[-2] - 1) if len(year) > 1 else None,
        "ret_1m": ret(30), "ret_3m": ret(91), "ret_1y": ret(365), "vol_30d": vol,
        "drawdown": float(year.iloc[-1] / year.max() - 1) if len(year) else None,
        "high_1y": float(year.max()) if len(year) else None, "low_1y": float(year.min()) if len(year) else None,
        "funding": funding, "funding_7d": float(funding.tail(7).mean()) if len(funding) else None,
        "oi": oi, "oi_change_7d": float(oi.iloc[-1] / oi.iloc[-8] - 1) if len(oi) > 8 else None,
        "views": views, "attention": float(views.tail(7).mean() / views.tail(90).mean()) if len(views) > 60 else None,
        "stable": stable, "stable_30d": float(stable.iloc[-1] / stable.iloc[-31] - 1) if len(stable) > 31 else None,
        "news": news,
    }


def fact_sheet(s: dict) -> dict[str, str]:
    return {
        "coin": f"{s['name']} ({s['coin']})", "price": f"${s['price']:,.2f}" if s.get("price") else "n/a",
        "return_1m": R.pct(s["ret_1m"]), "return_3m": R.pct(s["ret_3m"]), "return_1y": R.pct(s["ret_1y"]),
        "volatility_30d_annualized": R.pct(s["vol_30d"], sign=False), "below_1y_high": R.pct(s["drawdown"]),
        "funding_7d_avg_annualized": R.pct(s["funding_7d"]), "open_interest_change_7d": R.pct(s["oi_change_7d"]),
        "attention_7d_vs_90d": "n/a" if s["attention"] is None else f"{s['attention']:.2f}x",
        "stablecoin_supply_change_30d": R.pct(s["stable_30d"]),
    }


def render(s: dict, comment: str | None, comment_status: str, path) -> str:
    fig = R.figure(1350)
    R.header(fig, f"{s['name']}", f"{s['coin']} · crypto · {pd.Timestamp.now(tz='Europe/Vienna'):%d %b %Y}",
             right_big=f"${s['price']:,.2f}" if s.get("price") else None,
             right_small=f"{R.pct(s['d1'])} today", right_color=R.change_color(s["d1"]))
    ax = fig.add_axes((0.12, 0.60, 0.48, 0.29))
    ax.set_title("1 year price")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: R.money(v).replace(".00", "")))
    if len(s["prices"]):
        ax.plot(s["prices"].index, s["prices"], color=R.ACCENT, lw=1.8)
        ax.fill_between(s["prices"].index, s["prices"], s["prices"].min() * 0.97, color=R.ACCENT, alpha=0.06)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    st = R.card(fig, (0.63, 0.585, 0.33, 0.33), "Key numbers")
    rows = [("1 month", R.pct(s["ret_1m"])), ("3 months", R.pct(s["ret_3m"])), ("1 year", R.pct(s["ret_1y"])),
            ("Volatility (30d, yearly)", R.pct(s["vol_30d"], sign=False)), ("Below 1-year high", R.pct(s["drawdown"])),
            ("1-year high", R.money(s["high_1y"])), ("1-year low", R.money(s["low_1y"])),
            ("Funding (7d, per year)", R.pct(s["funding_7d"])), ("Open interest, 7d", R.pct(s["oi_change_7d"])),
            ("Stablecoin supply, 30d", R.pct(s["stable_30d"]))]
    for i, (k, v) in enumerate(rows):
        y = 0.86 - i * 0.085
        st.text(0, y, k, transform=st.transAxes, fontsize=8.5, color=R.TEXT2, va="center")
        st.text(1, y, v, transform=st.transAxes, fontsize=8.5, color=R.TEXT, va="center", ha="right", fontweight="bold")
    ax = fig.add_axes((0.08, 0.34, 0.38, 0.19))
    ax.set_title("Funding rate, per year (longs pay shorts)")
    if len(s["funding"]):
        f = s["funding"] * 100
        ax.bar(f.index, f, color=[R.GOOD if v >= 0 else R.CRITICAL for v in f], width=1.0)
        ax.axhline(0, color=R.TEXT3, lw=0.6)
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
        ax.set_ylabel("%", fontsize=7.5)
    ax = fig.add_axes((0.57, 0.34, 0.38, 0.19))
    ax.set_title("Wikipedia attention (daily views)")
    if len(s["views"]):
        ax.plot(s["views"].index, s["views"].rolling(7).mean(), color=R.ACCENT2, lw=1.5)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    nw = R.card(fig, (0.04, 0.13, 0.92, 0.15), "Latest news")
    for i, t in enumerate(s["news"]["title"].head(4) if len(s["news"]) else []):
        nw.text(0, 0.70 - i * 0.21, "• " + (t if len(t) <= 110 else t[:108].rstrip() + "…"), transform=nw.transAxes,
                fontsize=8, color=R.TEXT, va="top")
    if not len(s["news"]):
        nw.text(0, 0.6, "no recent headlines", transform=nw.transAxes, fontsize=8, color=R.TEXT3, va="top")
    ai = R.card(fig, (0.04, 0.03, 0.92, 0.09), "AI comment (fact-checked)" if comment else "AI comment")
    ai.text(0, 0.62, R.wrap(comment or comment_status, 108), transform=ai.transAxes, fontsize=8.3,
            color=R.TEXT if comment else R.TEXT3, va="top")
    R.footer(fig, "Data: Alpaca, Hyperliquid, Wikipedia, DefiLlama, news feeds · Not financial advice")
    return R.save(fig, path)


def ai_comment(sheet: dict[str, str], post=None):
    from trader.reports import stock
    return stock.ai_comment(sheet, post=post, system=SYSTEM, name_key="coin")
