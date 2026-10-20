"""/news and the 08:00 / 22:30 briefings: what markets did and why, in one picture + a few lines.

Moves: S&P 500 / Nasdaq-100 / Dow / Russell 2000 (ETFs SPY, QQQ, DIA, IWM), total crypto market
(CoinGecko) + BTC / ETH / SOL, oil (USO), gold (GLD), the dollar (UUP) and the 10-year Treasury
yield (FRED). Explanations come from the free AI, but each one must cite headlines we actually
collected in the last 24 hours (Yahoo, CNBC, MarketWatch, BBC, CoinDesk, Cointelegraph) and quote
the start of one of them; any number it writes must be in the moves table or a cited headline.
Unbacked explanations are dropped.
"""

from __future__ import annotations

import re

import pandas as pd

from trader.reports import data as rdata
from trader.reports import render as R

STOCKS = [("S&P 500", "SPY"), ("Nasdaq-100", "QQQ"), ("Dow Jones", "DIA"), ("Russell 2000", "IWM")]
MACRO = [("Oil", "USO"), ("Gold", "GLD"), ("US dollar", "UUP")]
COINS = [("Bitcoin", "BTC/USD"), ("Ether", "ETH/USD"), ("Solana", "SOL/USD")]
TOPICS = ["stocks", "crypto", "oil", "gold", "rates", "dollar", "world"]

KEYWORDS = {
    "stocks": r"stock|s&p|nasdaq|dow|wall street|shares|equit|earnings|rally|sell-?off",
    "crypto": r"bitcoin|crypto|ether|ethereum|solana|btc|stablecoin|coinbase|token",
    "oil": r"\boil\b|crude|opec|brent|wti|barrel|gasoline",
    "gold": r"\bgold\b|bullion|precious metal",
    "rates": r"treasur|yield|\bfed\b|federal reserve|interest rate|rate cut|rate hike|inflation|powell|bond",
    "dollar": r"dollar|currenc|\bfx\b|euro|yen|yuan",
}
TOPIC_NAMES = ["S&P 500", "Nasdaq-100", "Russell 2000", "10-year yield", "Dow 30"]
TOPIC_MOVES = {"stocks": ["S&P 500", "Nasdaq-100", "Dow Jones"], "crypto": ["Crypto market", "Bitcoin", "Ether"],
               "oil": ["Oil"], "gold": ["Gold"], "rates": ["10-year yield"], "dollar": ["US dollar"]}

WHY = """You check whether news explains a market move. You get one move and a few numbered headlines
that mention the topic. If one or more of them plausibly explains the move, write ONE sentence
explaining it, cite the headline numbers, and copy the first five words of the main one into "quote".
Any number you write must appear in the move or in a cited headline. If none of them explains the
move, answer {"explains": false}. Answer with JSON only:
{"explains": true, "text": "one sentence", "headlines": [2], "quote": "first five words of headline 2"}"""

WORLD = """Pick the 3 most important world or economy news items from the numbered headlines and
summarize each in one sentence, using only what the headline says. Cite its number and copy its first
five words into "quote". Answer with JSON only:
{"items": [{"text": "one sentence", "headlines": [4], "quote": "first five words of headline 4"}]}"""


def crypto_change_24h(pairs: list[str]) -> dict[str, tuple[float, float]]:
    """{pair: (price, change over the last 24 hours)} from Alpaca hourly bars."""
    from alpaca.data.historical import CryptoHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame
    now = pd.Timestamp.now(tz="UTC")
    try:
        res = CryptoHistoricalDataClient().get_crypto_bars(CryptoBarsRequest(symbol_or_symbols=pairs, timeframe=TimeFrame.Hour,
                                                                              start=now - pd.Timedelta(hours=30)))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    if res.df.empty:
        return out
    df = res.df.reset_index()
    for pair, g in df.groupby("symbol"):
        c = g.set_index("timestamp")["close"].sort_index()
        ago = c[c.index <= c.index[-1] - pd.Timedelta(hours=24)]
        if len(ago):
            out[pair] = (float(c.iloc[-1]), float(c.iloc[-1] / ago.iloc[-1] - 1))
    return out


def moves() -> dict:
    """{label: {"value", "change", "kind"}} for every tile."""
    out = {}
    px = rdata.stock_daily([t for _, t in STOCKS + MACRO], 12)
    for label, t in STOCKS + MACRO:
        if t in px and px[t].notna().sum() >= 2:
            c = px[t].dropna()
            out[label] = {"value": float(c.iloc[-1]), "change": float(c.iloc[-1] / c.iloc[-2] - 1), "kind": "etf",
                          "ticker": t, "date": str(c.index[-1].date())}
    total = rdata.crypto_total()
    if total:
        out["Crypto market"] = {"value": total["market_cap"], "change": total["change_24h"], "kind": "cap"}
    coins = crypto_change_24h([p for _, p in COINS])
    for label, pair in COINS:
        if pair in coins:
            out[label] = {"value": coins[pair][0], "change": coins[pair][1], "kind": "coin"}
    y = rdata.fred("DGS10")
    if len(y) >= 2:
        out["10-year yield"] = {"value": float(y.iloc[-1]) / 100, "change": float(y.iloc[-1] - y.iloc[-2]) / 100, "kind": "yield",
                                "date": str(y.index[-1].date())}
    return out


def moves_sheet(m: dict) -> dict[str, str]:
    sheet = {}
    for label, v in m.items():
        if v["kind"] == "yield":
            sheet[label] = f"{v['value'] * 100:.2f}% ({v['change'] * 1e4:+.0f} bp)"
        else:
            sheet[label] = R.pct(v["change"])
    return sheet


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def check_items(items, heads: pd.DataFrame, sheet: dict[str, str]) -> tuple[list[dict], int]:
    """(valid items, dropped count): cited headlines must exist, the quote must start one of them,
    and every number must be in the moves table or a cited headline."""
    from trader.paper.research_desk import _norm as num_norm
    from trader.paper.research_desk import _numbers
    good, dropped = [], 0
    for it in items if isinstance(items, list) else []:
        try:
            ids = [int(i) for i in it.get("headlines", [])]
            topic, text, quote = it.get("topic"), str(it.get("text", "")).strip(), _norm(it.get("quote", ""))
        except (TypeError, ValueError, AttributeError):
            dropped += 1
            continue
        cited = [heads.at[i, "title"] for i in ids if 0 <= i < len(heads)]
        if topic not in TOPICS or not text or not cited or len(quote) < 8 or not any(_norm(c).startswith(quote[:40]) for c in cited):
            dropped += 1
            continue
        bare = lambda n: num_norm(n).lstrip("-")  # noqa: E731 - "fell 1 bp" matches "-1 bp"
        sources = list(sheet.values()) + list(sheet.keys()) + list(TOPIC_NAMES) + cited   # names: "Nasdaq-100", "10-year"
        allowed = {bare(n) for t in sources for n in _numbers(t)} | {bare(v) for v in sheet.values()}
        nums = [n for n in _numbers(text) if not re.fullmatch(r"(19|20)\d\d", bare(n))]
        if any(bare(n) not in allowed for n in nums):
            dropped += 1
            continue
        good.append({"topic": topic, "text": text, "sources": sorted({heads.at[i, "source"] for i in ids if 0 <= i < len(heads)}),
                     "headlines": [heads.at[i, "title"] for i in ids if 0 <= i < len(heads)]})
    return good, dropped


def explain(m: dict, heads: pd.DataFrame, post=None) -> tuple[list[dict], str]:
    """One short AI call per market topic (only headlines that mention it), plus one for world news."""
    from trader.paper.news_llama import WorkersAIError, chat, extract_json
    if heads.empty:
        return [], "no headlines collected"
    sheet = moves_sheet(m)
    kwargs = {"post": post} if post else {}
    items, dropped, unexplained = [], 0, []
    try:
        for topic, pattern in KEYWORDS.items():
            labels = [lbl for lbl in TOPIC_MOVES[topic] if lbl in sheet]
            if not labels:
                continue
            subset = heads[heads["title"].str.contains(pattern, case=False, regex=True)].head(12)
            if subset.empty:
                unexplained.append(topic)
                continue
            msg = ("Move: " + "; ".join(f"{lbl} {sheet[lbl]}" for lbl in labels) + "\nHeadlines:\n"
                   + "\n".join(f"{i}. {t}" for i, t in zip(subset.index, subset["title"])))
            text, _ = chat([{"role": "system", "content": WHY}, {"role": "user", "content": msg}], 250, **kwargs)
            answer = extract_json(text) or {}
            if not answer.get("explains"):
                unexplained.append(topic)
                continue
            good, bad = check_items([{**answer, "topic": topic}], heads, {lbl: sheet[lbl] for lbl in labels})
            items += good
            dropped += bad
        world = heads[heads["source"].str.startswith("BBC")].head(25)
        if len(world):
            msg = "Headlines:\n" + "\n".join(f"{i}. {t}" for i, t in zip(world.index, world["title"]))
            text, _ = chat([{"role": "system", "content": WORLD}, {"role": "user", "content": msg}], 500, **kwargs)
            raw = (extract_json(text) or {}).get("items", [])
            good, bad = check_items([{**it, "topic": "world"} for it in raw if isinstance(it, dict)], heads, {})
            items += good[:3]
            dropped += bad
    except WorkersAIError as e:
        return items, f"AI unavailable today ({e})"
    status = f"{len(items)} explanation(s) backed by headlines"
    if dropped:
        status += f", {dropped} unbacked dropped"
    if unexplained:
        status += f"; no headline explains: {', '.join(unexplained)}"
    return items, status


def gather(post=None) -> dict:
    m = moves()
    heads = rdata.headlines(["markets", "world", "crypto"], hours=24).head(70).reset_index(drop=True)
    items, status = explain(m, heads, post)
    return {"moves": m, "headlines": heads, "items": items, "status": status}


TILE_DETAIL = {"Oil": "USO oil fund", "Gold": "GLD gold fund", "US dollar": "UUP dollar fund", "S&P 500": "SPY",
               "Nasdaq-100": "QQQ", "Dow Jones": "DIA", "Russell 2000": "IWM"}


def _tile(label: str, v: dict | None) -> tuple[str, str, str]:
    """(big number, detail line, color) for a tile: the move is the big number, the level the detail."""
    if not v:
        return "n/a", "", R.TEXT3
    if v["kind"] == "yield":
        return f"{v['change'] * 1e4:+.0f} bp", f"now {v['value'] * 100:.2f}%", R.change_color(-v["change"], flat=0.00005)
    level = R.money(v["value"]).replace(".00", "") if v["kind"] in ("cap", "coin") else f"{TILE_DETAIL.get(label, '')} ${v['value']:,.2f}"
    return R.pct(v["change"]), level.strip(), R.change_color(v["change"])


def render(b: dict, when: str, path) -> str:
    fig = R.figure(1350)
    now = pd.Timestamp.now(tz="Europe/Vienna")
    spx = b["moves"].get("S&P 500", {}).get("change")
    crypto = b["moves"].get("Crypto market", b["moves"].get("Bitcoin", {})).get("change")
    verdict = " · ".join(f"{name} {'up' if ch > 0 else 'down'} {R.pct(ch)}" for name, ch in
                         (("Stocks", spx), ("crypto", crypto)) if ch is not None and ch == ch)
    R.header(fig, f"{when} market briefing", f"{now:%A, %d %b %Y · %H:%M} Vienna", right_small=verdict,
             right_color=R.change_color(spx))
    ax = fig.add_axes((0.04, 0.545, 0.92, 0.335))
    ax.set_axis_off()
    groups = [("Stocks (last session)", [l for l, _ in STOCKS]), ("Crypto (24 hours)", ["Crypto market"] + [l for l, _ in COINS]),
              ("Oil, gold, rates, dollar", ["Oil", "Gold", "10-year yield", "US dollar"])]
    for gi, (gname, labels) in enumerate(groups):
        y0 = 0.69 - gi * 0.345
        ax.text(0, y0 + 0.27, gname, transform=ax.transAxes, fontsize=9.5, fontweight="bold", color=R.TEXT2)
        for k, label in enumerate(labels):
            value, detail, color = _tile(label, b["moves"].get(label))
            R.tile(ax, k * 0.255, y0, 0.235, 0.25, label, value, detail, color)
    why = R.card(fig, (0.04, 0.255, 0.92, 0.27), "Why it moved (each line backed by the cited headlines)")
    market_items = [i for i in b["items"] if i["topic"] != "world"]
    y = 0.80
    for it in market_items[:4]:
        line = R.wrap(f"{it['topic'].capitalize()}: {it['text']}", 110)
        why.text(0, y, line, transform=why.transAxes, fontsize=8.4, color=R.TEXT, va="top")
        n = line.count("\n") + 1
        why.text(0, y - 0.09 * n, "Source: " + ", ".join(it["sources"]), transform=why.transAxes, fontsize=7, color=R.TEXT3, va="top")
        y -= 0.09 * n + 0.11
    quiet = b["status"].split("; no headline explains: ")
    if len(quiet) == 2:
        why.text(0, max(y, 0.02), f"No headline explains today's move in: {quiet[1]}.", transform=why.transAxes,
                 fontsize=7.8, color=R.TEXT3, va="top")
    elif not market_items:
        why.text(0, 0.8, b["status"], transform=why.transAxes, fontsize=8.5, color=R.TEXT3, va="top")
    world = R.card(fig, (0.04, 0.03, 0.92, 0.205), "World")
    y = 0.74
    for it in [i for i in b["items"] if i["topic"] == "world"][:3]:
        text = f"• {it['text']} ({', '.join(it['sources'])})"
        world.text(0, y, text if len(text) <= 112 else text[:110].rstrip() + "…", transform=world.transAxes,
                   fontsize=8.2, color=R.TEXT, va="top")
        y -= 0.24
    R.footer(fig, "Data: Alpaca (fund prices), CoinGecko, FRED, news feeds · AI explanations checked against headlines · Not financial advice")
    return R.save(fig, path)


def caption(b: dict, when: str) -> str:
    """5-8 Telegram lines."""
    m = b["moves"]
    parts = [f"<b>{when} briefing</b>"]
    line = " · ".join(f"{k} {R.pct(m[k]['change'])}" for k in ("S&P 500", "Nasdaq-100") if k in m)
    if line:
        parts.append("📈 " + line)
    line = " · ".join(f"{k} {R.pct(m[k]['change'])}" for k in ("Crypto market", "Bitcoin") if k in m)
    if line:
        parts.append("🪙 " + line)
    macro = [f"Oil {R.pct(m['Oil']['change'])}" if "Oil" in m else None, f"Gold {R.pct(m['Gold']['change'])}" if "Gold" in m else None,
             f"10y {m['10-year yield']['value'] * 100:.2f}%" if "10-year yield" in m else None]
    if any(macro):
        parts.append("🛢 " + " · ".join(x for x in macro if x))
    for it in b["items"][:4]:
        parts.append("• " + it["text"])
    return "\n".join(parts)[:1000]

