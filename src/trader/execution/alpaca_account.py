"""Connection to the Alpaca paper-trading account."""

from __future__ import annotations

from dataclasses import dataclass

from alpaca.trading.client import TradingClient

from trader.config import AlpacaSecrets


@dataclass(frozen=True)
class AccountSummary:
    account_number: str
    status: str
    currency: str
    cash: float
    equity: float
    buying_power: float
    crypto_status: str | None
    trading_blocked: bool


def connect(secrets: AlpacaSecrets) -> TradingClient:
    # paper=True is hard-coded: this project never talks to the live endpoint.
    return TradingClient(secrets.api_key, secrets.secret_key, paper=True)


def summarize(account) -> AccountSummary:
    """Turn Alpaca's account object into the few numbers we care about."""
    crypto_status = getattr(account, "crypto_status", None)
    return AccountSummary(
        account_number=str(account.account_number),
        status=str(getattr(account.status, "value", account.status)),
        currency=str(account.currency),
        cash=float(account.cash),
        equity=float(account.equity),
        buying_power=float(account.buying_power),
        crypto_status=str(getattr(crypto_status, "value", crypto_status)) if crypto_status else None,
        trading_blocked=bool(account.trading_blocked),
    )


def get_account_summary(secrets: AlpacaSecrets) -> AccountSummary:
    return summarize(connect(secrets).get_account())
