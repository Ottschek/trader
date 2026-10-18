from types import SimpleNamespace

import pytest

from trader.config import ConfigError, load_alpaca_secrets, load_settings
from trader.execution.alpaca_account import summarize


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    return monkeypatch


def test_settings_load():
    s = load_settings()
    assert s.timeframe == "1h"
    assert len(s.stocks) == 20
    assert len(s.crypto) == 10
    assert s.benchmarks["stocks"] in s.stocks
    assert s.benchmarks["crypto"] in s.crypto


def test_risk_limits_match_plan():
    r = load_settings().risk
    assert r.max_position_pct == 0.10
    assert r.max_daily_loss_pct == 0.03
    assert r.kill_switch_drawdown_pct == 0.15


def test_costs_are_never_zero():
    # A backtest without costs lies. Every venue must charge something.
    for venue, c in load_settings().costs.items():
        assert c.one_way_bps > 0, venue


def test_bad_risk_limit_rejected(tmp_path):
    bad = tmp_path / "s.yaml"
    bad.write_text(
        "timeframe: 1h\nhistory_start: '2019-01-01'\n"
        "universe: {stocks: [SPY], crypto: [BTC/USD]}\n"
        "benchmarks: {stocks: SPY, crypto: BTC/USD}\n"
        "risk: {max_position_pct: 10, max_daily_loss_pct: 0.03, kill_switch_drawdown_pct: 0.15}\n"
        "costs: {x: {commission_bps: 0, spread_bps: 1, slippage_bps: 1}}\n"
    )
    with pytest.raises(ConfigError, match="max_position_pct"):
        load_settings(bad)


def test_missing_keys_give_clear_error(clean_env):
    with pytest.raises(ConfigError, match="not found"):
        load_alpaca_secrets(env_file=None)


def test_live_keys_refused(clean_env):
    clean_env.setenv("ALPACA_API_KEY", "AKLIVEKEY123")
    clean_env.setenv("ALPACA_SECRET_KEY", "secret")
    with pytest.raises(ConfigError, match="paper"):
        load_alpaca_secrets(env_file=None)


def test_paper_keys_accepted_and_never_printed(clean_env):
    clean_env.setenv("ALPACA_API_KEY", "PKTESTKEY123")
    clean_env.setenv("ALPACA_SECRET_KEY", "supersecret")
    secrets = load_alpaca_secrets(env_file=None)
    assert secrets.api_key == "PKTESTKEY123"
    assert "supersecret" not in repr(secrets)
    assert "PKTESTKEY123" not in repr(secrets)


def test_account_summary_parses_alpaca_strings():
    fake = SimpleNamespace(
        account_number="PA123", status=SimpleNamespace(value="ACTIVE"), currency="USD",
        cash="100000", equity="100000.5", buying_power="200000",
        crypto_status=SimpleNamespace(value="ACTIVE"), trading_blocked=False,
    )
    s = summarize(fake)
    assert s.status == "ACTIVE"
    assert s.equity == 100000.5
    assert s.crypto_status == "ACTIVE"
