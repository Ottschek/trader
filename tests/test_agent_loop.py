import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd

from trader.backtest.data import MarketData

spec = importlib.util.spec_from_file_location("agent_loop", Path(__file__).resolve().parents[1] / "scripts" / "agent_loop.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


def synthetic(days=400):
    idx = pd.date_range("2016-01-01", periods=days * 24, freq="h", tz="UTC")
    rng = np.random.default_rng(0)
    cols = {s: 100 * np.exp(np.cumsum(rng.normal(0.0001, 0.01, len(idx)))) for s in agent.SYMBOLS}
    close = pd.DataFrame(cols, index=idx)
    r = close.pct_change().fillna(0)
    return MarketData(close=close, open=close, volume=close * 0 + 1, gap=r * 0, intra=r, tradable=close.notna(),
                      venue={s: "alpaca_crypto" for s in agent.SYMBOLS}, raw_close=close)


class Resp:
    status_code = 200

    def __init__(self, text):
        self.text = text

    def json(self):
        return {"result": {"response": self.text, "usage": {"neurons": 50}}}


def test_clamp_keeps_rules_inside_the_rule_language():
    g = agent.clamp({"use_mom": 1, "mom_days": 999, "mom_min": -5, "use_sma": "1", "sma_days": 50.4, "sma_ratio": 1.0,
                     "use_vol": 0, "vol_max": 0.05})
    assert g["mom_days"] == 180 and g["mom_min"] == -0.2 and g["sma_days"] == 50 and g["use_sma"] == 1
    assert agent.clamp({"use_mom": 0, "use_sma": 0, "use_vol": 0, "mom_days": 30, "mom_min": 0, "sma_days": 100,
                        "sma_ratio": 1, "vol_max": 0.05}) is None
    assert agent.clamp({"use_mom": 1}) is None


def test_loop_counts_every_evaluated_rule_and_keeps_only_improvements(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "load_market", lambda **kw: synthetic())
    monkeypatch.setattr(agent, "AGENT_POPULATION", tmp_path / "agent_loop.json")
    monkeypatch.setattr(agent, "ROUNDS", 3)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    answers = iter([json.dumps({"rule": dict(agent.START_RULE, mom_days=60), "why": "slower"}),
                    "sorry, no json here",
                    json.dumps({"rule": dict(agent.START_RULE, use_vol=1, vol_max=0.02), "why": "avoid chaos"})])
    prompts = []

    def post(url, json=None, **kw):
        prompts.append(json["messages"][1]["content"])
        return Resp(next(answers))
    out = agent.run_loop(post=post)
    assert out["evaluated"] == 3                                   # start + 2 valid proposals (the bad answer isn't a rule)
    assert [t.get("error") for t in out["transcript"]].count("no valid rule") == 1
    kept = [t for t in out["transcript"] if t.get("kept")]
    assert out["survivors"][0]["in_sample_sharpe"] == max(t["in_sample_sharpe"] for t in kept)
    assert "worst losing trades" in prompts[0]
    assert json.loads((tmp_path / "agent_loop.json").read_text())["evaluated"] == 3
