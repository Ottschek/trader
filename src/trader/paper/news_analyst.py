"""LLM news analyst: a forward-only SHADOW experiment (no orders, not a validated strategy).

Every `every_hours`, Claude reads the headlines our collector saw since the last run
(data/news.py) and flags the ones that should move one of our assets over the next 1-5 days:
impact -2..+2. Per asset, the run's impacts are added up; |sum| >= `signal_threshold` is a
signal (long if positive, short if negative). The rules were fixed on 2026-10-19, before the
first run, and only forward data counts: there is no backtest, because old headlines can't be
scored without the model already knowing what happened next.

Each signal is scored like a trade that could really have been made:
  entry  = open of the asset's first bar that starts after the decision time
  exit   = close of the last bar that ended within `h` days of the entry (h = 1 and 5)
  excess = return minus the benchmark's over the same window (SPY for stocks, BTC for coins;
           the benchmarks themselves count raw)
  net    = side x excess - round-trip costs of the asset's venue

Spending is capped in code: runs pause once this month's spend runs ahead of an even pace
through the month (budget x fraction of the month gone), and never start if the worst case
could break the monthly budget.

Files (under data/alt/, synced to R2):  news_signals.parquet, news_runs.parquet,
state.json["news"] = {last_run, cutoff, spend: {"YYYY-MM": usd}}.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from trader.data import alt
from trader.data.news import headlines_path
from trader.data.store import read, upsert

BAR = pd.Timedelta(hours=1)
MAX_TOKENS = 8000
MAX_HEADLINES = 250          # per run; a normal 6-hour window has far fewer
MAX_AGE = pd.Timedelta(hours=24)   # ignore old stories that only now appeared in a feed
DUE_SLACK = pd.Timedelta(minutes=20)  # the collector runs at :07 and :37
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# US$ per million tokens (input, output). Unknown models (e.g. a server-side fallback) are
# billed at the most expensive rate, so the budget can only be overestimated.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0), "claude-opus-4-8": (5.0, 25.0),
          "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}
MAX_PRICE = (10.0, 50.0)

SYSTEM = """You are an equity and crypto news analyst. You read fresh headlines and judge which \
ones carry new information that should move the price of one of these assets over the next 1 to \
5 trading days, beyond what the market already knew:

{assets}

Flag a headline only if it has a real expected price impact on one listed asset. Skip generic \
market commentary, listicles ("3 stocks to buy"), price recaps ("X rose 4% today"), opinion and \
forecast pieces, and stories that repeat known news. Most headlines should not be flagged.

For each flagged headline give:
- id: the headline's id
- symbol: the listed asset it affects most directly (use the symbol exactly as listed; the feed \
tags are only a hint, feeds for smaller coins carry general crypto news)
- impact: +2 strongly positive, +1 positive, -1 negative, -2 strongly negative
- reason: at most 15 words"""


def schema(symbols: list[str]) -> dict:
    flag = {"type": "object",
            "properties": {"id": {"type": "integer"}, "symbol": {"type": "string", "enum": symbols},
                           "impact": {"type": "integer", "enum": [-2, -1, 1, 2]}, "reason": {"type": "string"}},
            "required": ["id", "symbol", "impact", "reason"], "additionalProperties": False}
    return {"type": "object", "properties": {"flags": {"type": "array", "items": flag}},
            "required": ["flags"], "additionalProperties": False}


def signals_path():
    return alt.alt_path("news_signals")


def runs_path():
    return alt.alt_path("news_runs")


# --- budget -------------------------------------------------------------------------

def month_key(now: pd.Timestamp) -> str:
    return f"{now:%Y-%m}"


def allowance(budget: float, now: pd.Timestamp) -> float:
    """Spend allowed so far this month on an even pace (at least one day's share)."""
    start = now.normalize().replace(day=1)
    fraction = (now - start) / (start + pd.offsets.MonthBegin(1) - start)
    return budget * min(1.0, max(fraction, 1 / now.days_in_month))


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    p_in, p_out = PRICES.get(model, MAX_PRICE)
    return (input_tokens * p_in + output_tokens * p_out) / 1e6


def budget_block(spent: float, budget: float, now: pd.Timestamp, model: str, input_estimate: int) -> str | None:
    """Why this run must not call Claude, or None if it may."""
    worst = cost_usd(model, input_estimate, MAX_TOKENS)
    if spent + worst > budget:
        return f"monthly budget: ${spent:.2f} of ${budget:.2f} spent"
    if spent >= allowance(budget, now):
        return f"paced: ${spent:.2f} spent, ${allowance(budget, now):.2f} allowed so far this month"
    return None


# --- headlines -> prompt -> flags ------------------------------------------------------

def select_headlines(headlines: pd.DataFrame, cutoff: pd.Timestamp, now: pd.Timestamp) -> pd.DataFrame:
    """Headlines first seen after `cutoff` (and up to now), published within the last day."""
    if headlines.empty:
        return headlines
    h = headlines[(headlines["ts"] > cutoff) & (headlines["ts"] <= now) & (headlines["published"] > now - MAX_AGE)]
    h = h.drop_duplicates("title").sort_values(["published", "guid"]).tail(MAX_HEADLINES)
    return h.reset_index(drop=True)


def user_message(rows: pd.DataFrame, now: pd.Timestamp) -> str:
    lines = [f"Now: {now:%Y-%m-%d %H:%M} UTC. Headlines (id | published UTC | feed tags | title):"]
    lines += [f"{i} | {r.published:%Y-%m-%d %H:%M} | {r.tags} | {r.title}" for i, r in enumerate(rows.itertuples())]
    return "\n".join(lines)


def ask_claude(client, cfg: dict, rows: pd.DataFrame, now: pd.Timestamp) -> dict:
    """One Claude call. Returns {status, model, input_tokens, output_tokens, flags}."""
    assets = cfg["assets"]
    response = client.beta.messages.create(
        model=cfg["model"], max_tokens=MAX_TOKENS,
        betas=[FALLBACK_BETA], fallbacks="default",
        output_config={"effort": cfg.get("effort", "low"), "format": {"type": "json_schema", "schema": schema(list(assets))}},
        system=SYSTEM.format(assets="\n".join(f"- {s}: {name}" for s, name in assets.items())),
        messages=[{"role": "user", "content": user_message(rows, now)}],
    )
    out = {"model": response.model, "input_tokens": response.usage.input_tokens,
           "output_tokens": response.usage.output_tokens, "flags": []}
    if response.stop_reason == "refusal":
        return {**out, "status": "refused"}
    if response.stop_reason == "max_tokens":
        return {**out, "status": "truncated"}
    text = next((b.text for b in response.content if b.type == "text"), "")
    flags = json.loads(text)["flags"]
    valid = [f for f in flags if 0 <= f["id"] < len(rows) and f["symbol"] in assets]
    return {**out, "status": "ok", "flags": valid}


def signals_from_flags(flags: list[dict], rows: pd.DataFrame, now: pd.Timestamp, threshold: int) -> pd.DataFrame:
    by_symbol: dict[str, list[dict]] = {}
    for f in flags:
        by_symbol.setdefault(f["symbol"], []).append(f)
    out = []
    for symbol, fs in sorted(by_symbol.items()):
        score = sum(f["impact"] for f in fs)
        if abs(score) < threshold:
            continue
        evidence = [{"title": rows.at[f["id"], "title"], "link": rows.at[f["id"], "link"],
                     "impact": f["impact"], "reason": f["reason"]} for f in fs]
        out.append({"id": f"{now:%Y%m%dT%H%M}-{symbol}", "decided_at": now, "symbol": symbol,
                    "side": int(np.sign(score)), "score": int(score), "headlines": json.dumps(evidence)})
    return pd.DataFrame(out, columns=["id", "decided_at", "symbol", "side", "score", "headlines"])


# --- forward scoring ----------------------------------------------------------------------

def window_return(open_: pd.Series, close: pd.Series, start: pd.Timestamp, limit: pd.Timestamp):
    """(entry time, entry price, return) from the first open at/after `start` to the last close
    whose bar ended by `limit`; None if there is no such window."""
    o, c = open_.dropna(), close.dropna()
    o = o[o.index >= start]
    if o.empty:
        return None
    entry = o.index[0]
    c = c[(c.index >= entry) & (c.index + BAR <= limit)]
    if c.empty:
        return None
    return entry, float(o.iloc[0]), float(c.iloc[-1] / o.iloc[0] - 1)


def score_signals(signals: pd.DataFrame, open_: pd.DataFrame, close: pd.DataFrame, data_end: pd.Timestamp,
                  benchmark: dict[str, str], cost_bps: dict[str, float], horizons=(1, 5)) -> pd.DataFrame:
    """Fill entry and per-horizon returns for every signal whose window is complete.
    `data_end`: the time up to which prices have been collected."""
    out = signals.copy()
    for h in horizons:
        for col in (f"ret_{h}d", f"excess_{h}d", f"net_{h}d"):
            if col not in out:
                out[col] = np.nan
    for col in ("entry_ts", "entry_px"):
        if col not in out:
            out[col] = None
    for i, s in out.iterrows():
        sym = s["symbol"]
        if sym not in open_:
            continue
        first = open_[sym].dropna()
        first = first[first.index > s["decided_at"]]
        if first.empty:
            continue
        entry = first.index[0]
        out.at[i, "entry_ts"], out.at[i, "entry_px"] = entry, float(first.iloc[0])
        bench = benchmark[sym]
        for h in horizons:
            limit = entry + pd.Timedelta(days=h)
            if data_end < limit or not np.isnan(out.at[i, f"net_{h}d"]):
                continue
            mine = window_return(open_[sym], close[sym], entry, limit)
            ref = window_return(open_[bench], close[bench], entry, limit) if bench != sym else (None, None, 0.0)
            if mine is None or ref is None:
                continue
            excess = mine[2] - ref[2]
            out.at[i, f"ret_{h}d"] = mine[2]
            out.at[i, f"excess_{h}d"] = excess
            out.at[i, f"net_{h}d"] = s["side"] * excess - 2 * cost_bps[sym] / 1e4
    out["entry_ts"] = pd.to_datetime(out["entry_ts"], utc=True)
    out["entry_px"] = out["entry_px"].astype(float)
    return out


def summary(signals: pd.DataFrame, horizons=(1, 5)) -> dict:
    res = {"signals": int(len(signals))}
    for h in horizons:
        net = signals[f"net_{h}d"].dropna() if f"net_{h}d" in signals else pd.Series(dtype=float)
        n = len(net)
        res[f"{h}d"] = {"n": n, "mean_net": float(net.mean()) if n else None,
                        "hit_rate": float((net > 0).mean()) if n else None,
                        "t_stat": float(net.mean() / net.std(ddof=1) * np.sqrt(n)) if n > 2 and net.std(ddof=1) > 0 else None}
    return res


def rescore(settings, horizons=(1, 5), path=None) -> pd.DataFrame:
    """Score all stored signals (this analyst's, or those in `path`) with the latest prices and save them."""
    from trader.backtest.data import load_market

    path = path or signals_path()
    signals = read(path)
    if signals.empty:
        return signals
    syms = set(signals["symbol"])
    crypto = sorted({s for s in syms if "/" in s} | ({settings.benchmarks["crypto"]} if any("/" in s for s in syms) else set()))
    stocks = sorted({s for s in syms if "/" not in s} | ({settings.benchmarks["stocks"]} if any("/" not in s for s in syms) else set()))
    start = signals["decided_at"].min() - pd.Timedelta(days=2)
    data = load_market(crypto=crypto, stocks=stocks, start=start)
    bench = {s: settings.benchmarks["crypto" if "/" in s else "stocks"] for s in syms}
    costs = {s: settings.costs[data.venue[s]].one_way_bps for s in syms}
    scored = score_signals(signals, data.open, data.close, data.index[-1] + BAR, bench, costs, horizons)
    upsert(path, scored, key=["id"])
    return scored


# --- one collector run --------------------------------------------------------------------

def due(ns: dict, now: pd.Timestamp, every_hours: float) -> bool:
    last = ns.get("last_run")
    return last is None or now - pd.Timestamp(last) >= pd.Timedelta(hours=every_hours) - DUE_SLACK


def record_run(row: dict) -> None:
    upsert(runs_path(), pd.DataFrame([row]), key=["ts"])


def run(settings, now: pd.Timestamp, client=None) -> str:
    """Analyze new headlines if a run is due; returns a one-line status for the log."""
    cfg = settings.news or {}
    if not cfg or now < pd.Timestamp(cfg["start"], tz="UTC"):
        return "not started"
    state = alt.load_state()
    ns = state.setdefault("news", {})
    if not due(ns, now, cfg["every_hours"]):
        return f"not due (last run {ns['last_run'][:16]})"
    if client is None:
        from trader.config import ConfigError, secret
        try:
            key = secret("ANTHROPIC_API_KEY")
        except ConfigError:
            return "skipped: no ANTHROPIC_API_KEY"
        import anthropic
        client = anthropic.Anthropic(api_key=key)

    cutoff = pd.Timestamp(ns["cutoff"]) if ns.get("cutoff") else now - pd.Timedelta(hours=cfg["every_hours"])
    rows = select_headlines(read(headlines_path()), cutoff, now)
    base = {"ts": now, "model": cfg["model"], "headlines": len(rows), "flagged": 0, "signals": 0,
            "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    if rows.empty:
        record_run({**base, "status": "no new headlines"})
        ns.update(last_run=now.isoformat(), cutoff=now.isoformat())
        alt.save_state(state)
        return "no new headlines"

    spend = ns.setdefault("spend", {})
    spent = spend.get(month_key(now), 0.0)
    blocked = budget_block(spent, cfg["monthly_budget_usd"], now, cfg["model"], 1500 + 40 * len(rows))
    if blocked:
        return f"waiting: {blocked}"   # headlines stay queued (cutoff unchanged) for the next run

    result = ask_claude(client, cfg, rows, now)
    cost = cost_usd(result["model"], result["input_tokens"], result["output_tokens"])
    spend[month_key(now)] = round(spent + cost, 6)
    signals = signals_from_flags(result["flags"], rows, now, cfg["signal_threshold"])
    if len(signals):
        upsert(signals_path(), signals, key=["id"])
    record_run({**base, "model": result["model"], "flagged": len(result["flags"]), "signals": len(signals),
                "input_tokens": result["input_tokens"], "output_tokens": result["output_tokens"],
                "cost_usd": cost, "status": result["status"]})
    ns.update(last_run=now.isoformat(), cutoff=now.isoformat())
    alt.save_state(state)
    picks = ", ".join(f"{'long' if s.side > 0 else 'short'} {s.symbol}" for s in signals.itertuples()) or "no signals"
    return f"{result['status']}: {len(rows)} headlines, {len(result['flags'])} flagged, {picks} (${cost:.3f})"


# --- D1 mirror for the web app and the weekly report ---------------------------------------

SIGNAL_COLUMNS = ["id", "source", "decided_at", "symbol", "side", "score", "headlines", "entry_ts", "entry_px",
                  "ret_1d", "excess_1d", "net_1d", "ret_5d", "excess_5d", "net_5d"]
RUN_COLUMNS = ["ts", "model", "headlines", "flagged", "signals", "input_tokens", "output_tokens", "cost_usd", "status"]


def _value(v):
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT:
        return None
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return v.item() if hasattr(v, "item") else v


def mirror_to_d1(d1, now: pd.Timestamp, days: int = 10) -> int:
    """Copy recent runs and every signal (analyst and word list) decided in the last `days` to D1."""
    since = now - pd.Timedelta(days=days)
    n = 0
    runs = read(runs_path())
    if not runs.empty:
        runs = runs[runs["ts"] >= since]
        n += d1.insert_many("news_runs", RUN_COLUMNS, [tuple(_value(r[c]) for c in RUN_COLUMNS) for _, r in runs.iterrows()])
    from trader.paper.news_lexicon import signals_path as lexicon_path
    for source, path in (("claude", signals_path()), ("lexicon", lexicon_path())):
        signals = read(path)
        if signals.empty:
            continue
        signals = signals[signals["decided_at"] >= since].assign(source=source)
        for c in SIGNAL_COLUMNS:
            if c not in signals:
                signals[c] = None
        n += d1.insert_many("news_signals", SIGNAL_COLUMNS,
                            [tuple(_value(r[c]) for c in SIGNAL_COLUMNS) for _, r in signals.iterrows()])
    return n
