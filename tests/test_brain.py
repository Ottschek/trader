import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trader.strategies.brain import BrainRule, make_strategy, validate_spec

spec_ = importlib.util.spec_from_file_location("brain", Path(__file__).resolve().parents[1] / "scripts" / "brain.py")
brain = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(brain)

ALLOWED = ["SPY", "QQQ", "BTC/USD", "ETH/USD"]
GOOD = {"name": "Payday Flows", "thesis": "payroll money", "symbols": ["SPY"], "decide": "daily",
        "conditions": [{"feature": "day_of_week", "op": "<", "value": 2}, {"feature": "return", "hours": 120, "op": "<", "value": -0.02}]}


def test_spec_validation_cleans_good_ideas_and_explains_bad_ones():
    s = validate_spec(GOOD, ALLOWED)
    assert s["name"] == "payday_flows" and "hours" not in s["conditions"][0] and s["conditions"][1]["hours"] == 120
    bad = [({**GOOD, "symbols": ["TSLA"]}, "symbols"), ({**GOOD, "conditions": []}, "conditions"),
           ({**GOOD, "conditions": [{"feature": "astrology", "op": ">", "value": 1}]}, "unknown feature"),
           ({**GOOD, "conditions": [{"feature": "return", "hours": 99999, "op": ">", "value": 0}]}, "hours"),
           ({**GOOD, "conditions": [{"feature": "funding_annual", "hours": 24, "op": ">", "value": 0.1}]}, "coin"),
           ({**GOOD, "decide": "weekly"}, "decide"), ("not a dict", "JSON object")]
    for spec, msg in bad:
        with pytest.raises(ValueError, match=msg):
            validate_spec(spec, ALLOWED)


def test_each_idea_becomes_its_own_strategy_family():
    a = make_strategy(validate_spec(GOOD, ALLOWED))
    b = make_strategy(validate_spec({**GOOD, "name": "btc_led", "conditions": [{"feature": "btc_return", "hours": 24, "op": ">", "value": 0.05}]}, ALLOWED))
    assert issubclass(a, BrainRule) and a.name == "brain_payday_flows" and a.signal_symbols == []
    assert b.signal_symbols == ["BTC/USD"] and a.name != b.name


class Resp:
    status_code = 200

    def __init__(self, text):
        self.text = text

    def json(self):
        return {"result": {"response": self.text}}


def settings():
    return SimpleNamespace(stocks=["SPY", "QQQ"], crypto=["BTC/USD", "ETH/USD"])


def test_brain_retries_invalid_ideas_and_refuses_repeats(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    monkeypatch.setattr(brain, "graveyard", lambda n=30: "- sma: rejected")
    answers = iter(["no json", json.dumps({**GOOD, "symbols": ["NOPE"]}), json.dumps(GOOD)])
    seen = []

    def post(url, json=None, **kw):
        seen.append(json["messages"])
        return Resp(next(answers))
    spec = brain.propose(settings(), [], post=post)
    assert spec["name"] == "payday_flows"
    assert "invalid" in seen[-1][-1]["content"] and "graveyard" in seen[0][1]["content"]
    repeat = iter([json.dumps(GOOD)] * 3)
    assert brain.propose(settings(), [{"spec": spec, "status": "died"}], post=lambda url, **kw: Resp(next(repeat))) is None


def test_run_records_who_lives_and_who_dies(tmp_path, monkeypatch):
    monkeypatch.setattr(brain, "IDEAS", tmp_path / "brain_ideas.jsonl")
    monkeypatch.setattr(brain, "load_settings", settings)
    specs = iter([validate_spec(GOOD, ALLOWED), validate_spec({**GOOD, "name": "winner"}, ALLOWED)])
    monkeypatch.setattr(brain, "propose", lambda s, ideas, post=None: next(specs))
    results = iter([{"verdict": "rejected", "failed": ["Max drawdown"]}, {"verdict": "holdout_passed", "failed": []}])
    monkeypatch.setattr(brain, "test_idea", lambda spec, s, publish: next(results))
    done = brain.run(2)
    assert [d["status"] for d in done] == ["died", "survived"]
    stored = [json.loads(line) for line in (tmp_path / "brain_ideas.jsonl").read_text().splitlines()]
    assert [s["status"] for s in stored] == ["died", "survived"]
    text = brain.telegram_summary(done)
    assert "💀 payday_flows" in text and "🏆 winner" in text and "survived the locked holdout" in text
