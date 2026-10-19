"""Free AI news scorer: the LLM news analyst's exact prompt and rules, run on Cloudflare Workers AI
(Llama 3.3 70B) instead of Claude. Forward-only SHADOW, no orders; rules frozen 2026-10-19.

Workers AI's free daily allowance (10,000 "neurons") covers about 20 runs; this uses 4 a day
(every `every_hours`), and on the free Workers plan a call over the allowance is refused, never billed.
Uses the Cloudflare account and API token the collector already has.

Files: data/alt/news_llama_signals.parquet; state.json["news_llama"] = {last_run, cutoff}.
"""

from __future__ import annotations

import json
import re

import pandas as pd
import requests

from trader.data import alt
from trader.data.news import headlines_path
from trader.data.store import read, upsert
from trader.paper import news_analyst as na

MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
MAX_TOKENS = 3000


def signals_path():
    return alt.alt_path("news_llama_signals")


FORMAT = ('\n\nAnswer with JSON only, no other text: {"flags": [{"id": 3, "quote": "first five words of the headline",'
          ' "symbol": "NVDA", "impact": 1, "reason": "..."}]}. "quote" must copy the start of that headline exactly.'
          ' Use an empty list if nothing qualifies.')


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def match_flags(raw: list, rows: pd.DataFrame, assets) -> list[dict]:
    """Keep flags whose quote matches the headline they point to; repair the id when the quote matches
    exactly one other headline (small models mix up ids); drop everything else."""
    titles = [_norm(t) for t in rows["title"]]
    out = []
    for f in raw:
        if not isinstance(f, dict) or f.get("symbol") not in assets or f.get("impact") not in (-2, -1, 1, 2):
            continue
        quote = _norm(f.get("quote", ""))
        if len(quote) < 8:
            continue
        i = f.get("id")
        if not (isinstance(i, int) and 0 <= i < len(rows) and titles[i].startswith(quote[:40])):
            hits = [k for k, t in enumerate(titles) if t.startswith(quote[:40])]
            if len(hits) != 1:
                continue
            i = hits[0]
        out.append({"id": i, "symbol": f["symbol"], "impact": f["impact"], "reason": str(f.get("reason", ""))[:200]})
    return out


def parse_text(result: dict) -> str | None:
    """The model's text from either Workers AI response shape."""
    if "choices" in result:
        return result["choices"][0]["message"]["content"]
    text = result.get("response")
    return text if isinstance(text, str) or text is None else json.dumps(text)


def ask_llama(cfg: dict, rows: pd.DataFrame, now: pd.Timestamp, post=requests.post) -> dict:
    from trader.config import secret

    url = f"https://api.cloudflare.com/client/v4/accounts/{secret('CLOUDFLARE_ACCOUNT_ID')}/ai/run/{cfg.get('free_model', MODEL)}"
    assets = cfg["assets"]
    system = na.SYSTEM.format(assets="\n".join(f"- {s}: {name}" for s, name in assets.items())) + FORMAT
    body = {"messages": [{"role": "system", "content": system},
                         {"role": "user", "content": na.user_message(rows, now)}],
            "max_tokens": MAX_TOKENS}
    try:
        r = post(url, headers={"Authorization": f"Bearer {secret('CLOUDFLARE_API_TOKEN')}"}, json=body, timeout=120)
    except requests.RequestException as e:            # never let the URL/headers end up in logs
        return {"status": f"request failed: {type(e).__name__}", "flags": []}
    if r.status_code != 200:
        return {"status": f"HTTP {r.status_code}", "flags": []}
    result = r.json().get("result") or {}
    text = parse_text(result)
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        raw = json.loads(m.group(0))["flags"] if m else None
    except (ValueError, KeyError, TypeError):
        raw = None
    if not isinstance(raw, list):
        return {"status": "bad json", "flags": []}
    flags = match_flags(raw, rows, assets)
    return {"status": "ok", "flags": flags, "dropped": len(raw) - len(flags), "usage": (result.get("usage") or {}).get("neurons")}


def run(settings, now: pd.Timestamp, post=requests.post) -> str:
    cfg = settings.news or {}
    if not cfg or now < pd.Timestamp(cfg["start"], tz="UTC"):
        return "not started"
    state = alt.load_state()
    ls = state.setdefault("news_llama", {})
    if not na.due(ls, now, cfg["every_hours"]):
        return f"not due (last run {ls['last_run'][:16]})"
    cutoff = pd.Timestamp(ls["cutoff"]) if ls.get("cutoff") else now - pd.Timedelta(hours=cfg["every_hours"])
    rows = na.select_headlines(read(headlines_path()), cutoff, now)
    if rows.empty:
        ls.update(last_run=now.isoformat(), cutoff=now.isoformat())
        alt.save_state(state)
        return "no new headlines"
    result = ask_llama(cfg, rows, now, post)
    if result["status"] != "ok":
        return f"failed ({result['status']}); headlines stay queued for the next run"
    signals = na.signals_from_flags(result["flags"], rows, now, cfg["signal_threshold"])
    if len(signals):
        signals["id"] = "llama-" + signals["id"]
        upsert(signals_path(), signals, key=["id"])
    ls.update(last_run=now.isoformat(), cutoff=now.isoformat())
    alt.save_state(state)
    picks = ", ".join(f"{'long' if s.side > 0 else 'short'} {s.symbol} ({s.score:+d})" for s in signals.itertuples())
    return (f"{len(rows)} headlines, {len(result['flags'])} flagged ({result['dropped']} dropped: quote didn't match), "
            f"{picks or 'no signals'}; {result.get('usage') or 0:.0f} neurons")
