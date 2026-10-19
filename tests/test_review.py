import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("review", Path(__file__).resolve().parents[1] / "scripts" / "review.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def carry_rows(days, sim_end, model_end, rebalances=0):
    start = {"ts": "2026-10-19T09:00:00Z", "equity": 99_500.0, "model_equity": 99_700.0}
    end = {"ts": f"2026-{10 + (19 + days > 31):02d}-{(19 + days - 1) % 31 + 1:02d}T09:00:00Z", "equity": sim_end,
           "model_equity": model_end, "funding_cum": 900.0, "fees_cum": 500.0, "rebalances": rebalances, "max_margin_use": 0.3}
    return [start, end]


def test_carry_review_compares_with_model_and_backtest_after_a_week():
    early = review.review_carry(carry_rows(3, 99_600, 99_800), 0.145)
    assert early["status"] == "too early"
    good = review.review_carry(carry_rows(30, 99_500 * 1.009, 99_700 * 1.011), 0.145)   # ~11.5% vs ~14% a year
    assert good["status"] == "matches", good["lines"]
    bad = review.review_carry(carry_rows(30, 99_500 * 1.002, 99_700 * 1.011, rebalances=9), 0.145)
    assert bad["status"] == "differs"
    assert not bad["checks"]["at least half the model"] and not bad["checks"]["at most one rebalance a week"]


def test_paper_review_flags_mismatches_tracking_and_slippage():
    rec = {"trades": [{"live_decision": "entry"}], "mismatches": [], "actual_return": 0.012, "expected_return": 0.010,
           "tracking_difference": 0.002, "avg_slippage_bps": 6.0}
    assert review.review_paper(rec, 4.0)["status"] == "matches"
    assert review.review_paper({**rec, "avg_slippage_bps": 9.0}, 4.0)["status"] == "differs"
    assert review.review_paper({**rec, "mismatches": ["2026-11-02"]}, 4.0)["status"] == "differs"
    assert review.review_paper({**rec, "trades": [{"live_decision": "no_trade"}]}, 4.0)["status"] == "too early"


def test_news_review_waits_for_enough_signals():
    sigs = [{"source": "lexicon", "net_1d": 0.01 * (-1) ** i, "net_5d": 0.0} for i in range(10)]
    r = review.review_news(sigs + [{"source": "claude", "net_1d": 1.0, "net_5d": 1.0}])
    assert r["status"] == "too early" and r["lines"][0] == "10 word-list signal(s)"
    assert review.annualize(0.01, 365) == pytest.approx(0.01)
