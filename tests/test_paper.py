import tomllib

import pandas as pd

from trader.config import PROJECT_ROOT, load_settings
from trader.paper import plan


def worker_vars() -> dict:
    return tomllib.loads((PROJECT_ROOT / "cloudflare" / "scheduler" / "wrangler.toml").read_text())["vars"]


def test_worker_settings_match_the_frozen_experiment():
    cfg, v = plan.load_config(), worker_vars()
    exp, risk = cfg["experiment"], cfg["risk"]
    assert v["PAPER_EXPERIMENT"] == exp["id"]
    assert v["PAPER_SYMBOLS"].split(",") == exp["symbols"]
    assert float(v["PAPER_THRESHOLD"]) == exp["params"]["threshold"]
    assert v["PAPER_START"] == str(exp["start"])
    assert float(v["PAPER_WEEKEND_GAP_HOURS"]) == cfg["execution"]["weekend_gap_hours"]
    assert float(v["RISK_MAX_POSITION"]) == risk["max_position_pct"]
    assert float(v["RISK_MAX_DAILY_LOSS"]) == risk["max_daily_loss_pct"]
    assert float(v["RISK_KILL_DRAWDOWN"]) == risk["kill_switch_drawdown_pct"]


def test_paper_risk_is_never_looser_than_the_global_limits():
    r, g = plan.load_config()["risk"], load_settings().risk
    assert r["max_position_pct"] <= g.max_position_pct
    assert r["max_daily_loss_pct"] <= g.max_daily_loss_pct
    assert r["kill_switch_drawdown_pct"] <= g.kill_switch_drawdown_pct


def test_experiment_is_labeled_and_uses_pre_holdout_params():
    exp = plan.load_config()["experiment"]
    assert "SHADOW" in exp["label"] and "not a validated candidate" in exp["label"]
    assert pd.Timestamp(exp["start"]) > pd.Timestamp(load_settings().validation.holdout_start)
