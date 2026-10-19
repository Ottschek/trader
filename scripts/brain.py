"""The brain: the free AI (Llama 3.3 70B on Cloudflare Workers AI) invents strategies; the six
tests decide. Ideas that fail die (graveyard); ideas that pass all six get their one look at the
locked holdout right away, and a survivor of that becomes a candidate (Telegram message).

Honest bookkeeping: every tested idea is a recorded trial, which raises the bar (deflated Sharpe)
for every later idea, so the brain runs slowly on purpose (a couple of ideas a week). Before each
idea it sees the graveyard (what died and why) and the ideas it already tried.

Ideas are stored in ledger/brain_ideas.jsonl (outside the code folders, so writing one doesn't
make the code "dirty" and the holdout stays usable). The strategy code is strategies/brain.py.

Run:  .venv/bin/python scripts/brain.py --ideas 1             # local data
      .venv/bin/python scripts/brain.py --ideas 2 --cloud     # what the weekly workflow runs
"""

from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from trader.config import PROJECT_ROOT, load_settings
from trader.ledger import ledger
from trader.paper.news_llama import MODEL, WorkersAIError, chat, extract_json
from trader.strategies.brain import FEATURES, MAX_CONDITIONS, make_strategy, validate_spec

IDEAS = PROJECT_ROOT / "ledger" / "brain_ideas.jsonl"
ATTEMPTS = 3

SYSTEM = """You are a quant researcher inventing ONE trading strategy at a time. A strict test harness
judges it on years of hourly data it has never seen: out-of-sample Sharpe >= 1, beat buy-and-hold,
max drawdown no worse than -20%, beat random timing, and a deflated Sharpe that accounts for the 2,000+
ideas already tried. Almost every idea fails. What killed most of them: trading costs (frequent
trading), deep drawdowns, and timing no better than random.

Good ideas start from a REASON: who is forced to trade (index funds, payroll money, liquidations,
funding payments, hedgers) and why that moves prices predictably. Prefer few trades.

You can only use this rule language. The strategy holds an equal share of each of its symbols while
ALL conditions are true, and is in cash otherwise. "decide": "daily" (re-decided once a day, cheaper)
or "hourly".
Features (each condition: {{"feature", "hours" if it has a window, "op": ">" or "<", "value"}}):
{features}
At most {max_conditions} conditions. Symbols: {symbols}.

Answer with JSON only:
{{"name": "short_snake_case", "thesis": "who is forced to trade and why it should work",
  "symbols": ["SPY"], "decide": "daily",
  "conditions": [{{"feature": "return", "hours": 120, "op": "<", "value": -0.03}}]}}"""


def load_ideas() -> list[dict]:
    return [json.loads(line) for line in IDEAS.read_text().splitlines()] if IDEAS.exists() else []


def save_ideas(ideas: list[dict]) -> None:
    IDEAS.parent.mkdir(exist_ok=True)
    IDEAS.write_text("".join(json.dumps(i) + "\n" for i in ideas))


def graveyard(n: int = 30) -> str:
    lines = []
    for e in ledger.entries()[-n:]:
        failed = [g["label"] for g in e["gates"] if not g["passed"]]
        o = e.get("oos", {})
        lines.append(f"- {e['strategy']}: {e['verdict']}"
                     + (f", failed {', '.join(failed)}" if failed else "")
                     + f" (Sharpe {o.get('sharpe', 0):.2f}, drawdown {o.get('max_drawdown', 0):.0%}, costs {o.get('cost_per_year', 0):.1%}/yr)")
    return "\n".join(lines)


def tried(ideas: list[dict]) -> str:
    return "\n".join(f"- {i['spec']['name']}: {json.dumps(i['spec']['conditions'])} on {i['spec']['symbols']} -> {i['status']}"
                     for i in ideas) or "- none yet"


def signature(spec: dict) -> str:
    return json.dumps({"s": sorted(spec["symbols"]), "c": sorted(json.dumps(c, sort_keys=True) for c in spec["conditions"]),
                       "d": spec["decide"]}, sort_keys=True)


def propose(settings, ideas: list[dict], post=None) -> dict | None:
    allowed = list(settings.stocks) + list(settings.crypto)
    features = "\n".join(f"- {k}: {d}" + (f"; hours {h[0]}-{h[1]}" if h else "") + f"; value {v[0]} to {v[1]}"
                         for k, (d, h, v) in FEATURES.items())
    system = SYSTEM.format(features=features, max_conditions=MAX_CONDITIONS, symbols=", ".join(allowed))
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": f"Recent results (the graveyard):\n{graveyard()}\n\nYour earlier ideas:\n{tried(ideas)}\n\n"
                                            "Invent one NEW idea that avoids what killed these."}]
    seen = {signature(i["spec"]) for i in ideas}
    kwargs = {"post": post} if post else {}
    for attempt in range(ATTEMPTS):
        try:
            text, _ = chat(messages, 600, MODEL, **kwargs)
        except WorkersAIError as e:
            print(f"  brain call failed: {e}")
            return None
        try:
            spec = validate_spec(extract_json(text), allowed)
            if signature(spec) in seen:
                raise ValueError("that exact rule was already tested; invent a different one")
            return spec
        except ValueError as e:
            print(f"  attempt {attempt + 1}: invalid idea ({e})")
            messages += [{"role": "assistant", "content": text[:2000]},
                         {"role": "user", "content": f"That idea is invalid: {e}. Answer with a corrected JSON idea only."}]
    return None


def test_idea(spec: dict, settings, publish: bool) -> dict:
    from trader.ledger import sync
    from trader.validation import report_html
    from trader.validation.holdout import HoldoutLocked
    from trader.validation.pipeline import validate

    cls = make_strategy(spec)
    card = validate(cls, spec["symbols"], settings)
    result = {"research_card": card.id, "verdict": card.verdict, "failed": [g.label for g in card.gates if not g.passed],
              "oos_sharpe": card.oos.get("sharpe") if isinstance(card.oos, dict) else None}
    if publish:
        print("  " + sync.publish_card(card, report_html.write(card)))
    if card.passed_research:
        try:
            hcard = validate(cls, spec["symbols"], settings, holdout=True)
            result.update(holdout_card=hcard.id, verdict=hcard.verdict)
            if publish:
                print("  " + sync.publish_card(hcard, report_html.write(hcard)))
        except HoldoutLocked as e:
            result["holdout_error"] = str(e)
    return result


def run(n: int, publish: bool = False) -> list[dict]:
    settings = load_settings()
    ideas = load_ideas()
    done = []
    for k in range(n):
        print(f"Brain idea {k + 1}/{n}")
        spec = propose(settings, ideas)
        if spec is None:
            print("  no valid idea this time")
            continue
        print(f"  {spec['name']}: {spec['thesis'][:120]}")
        entry = {"created_at": pd.Timestamp.now(tz="UTC").isoformat(), "model": MODEL, "spec": spec, "status": "testing"}
        ideas.append(entry)
        save_ideas(ideas)
        try:
            entry.update(test_idea(spec, settings, publish))
            entry["status"] = {"holdout_passed": "survived", "candidate": "candidate"}.get(entry["verdict"], "died")
        except Exception as e:  # noqa: BLE001 - one broken idea must not stop the brain
            entry.update(status="error", error=f"{type(e).__name__}: {e}"[:300])
        save_ideas(ideas)
        print(f"  -> {entry['status'].upper()}" + (f" (failed: {', '.join(entry.get('failed', []))})" if entry.get("failed") else ""))
        done.append(entry)
    return done


def telegram_summary(done: list[dict]) -> str:
    icon = {"survived": "🏆", "candidate": "🌱", "died": "💀", "error": "⚠️"}
    lines = ["🧠 <b>Brain report</b>"]
    for e in done:
        s = e["spec"]
        why = f" · failed: {', '.join(e.get('failed', []))}" if e.get("failed") else ""
        lines.append(f"{icon.get(e['status'], '')} {s['name']} ({', '.join(s['symbols'][:4])}): {e['status']}{why}")
    if any(e["status"] == "survived" for e in done):
        lines.append("A brain idea survived the locked holdout: see the Strategy lab.")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ideas", type=int, default=1)
    p.add_argument("--cloud", action="store_true", help="pull price data from R2 first; publish cards; Telegram summary")
    args = p.parse_args()
    if args.cloud:
        from trader.data import r2
        print(f"Pulling price data: {r2.pull(load_settings().cloud.r2_bucket, prefixes=('bars/', 'funding/', 'corporate_actions/'))} files")
    done = run(args.ideas, publish=args.cloud)
    if args.cloud and done:
        from trader.monitor import telegram
        telegram.send(telegram_summary(done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
