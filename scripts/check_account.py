"""M0 check: log into the Alpaca paper account and print the (fake) balance.

Run:  .venv/bin/python scripts/check_account.py
"""

from __future__ import annotations

import sys

from trader.config import ConfigError, load_alpaca_secrets, load_settings
from trader.execution.alpaca_account import get_account_summary


def main() -> int:
    settings = load_settings()
    print(f"Settings OK: {len(settings.stocks)} stocks, {len(settings.crypto)} cryptos, "
          f"timeframe {settings.timeframe}")

    try:
        secrets = load_alpaca_secrets()
    except ConfigError as e:
        print(f"✗ {e}")
        return 1

    try:
        acct = get_account_summary(secrets)
    except Exception as e:  # network / auth errors from alpaca-py
        print(f"✗ Could not log into Alpaca paper account: {e}")
        print("  Check that the keys in .env are PAPER keys and were copied completely.")
        return 1

    print("✓ Logged into Alpaca PAPER account")
    print(f"  account:      {acct.account_number}  ({acct.status})")
    print(f"  cash:         {acct.cash:,.2f} {acct.currency}")
    print(f"  equity:       {acct.equity:,.2f} {acct.currency}")
    print(f"  buying power: {acct.buying_power:,.2f} {acct.currency}")
    print(f"  crypto:       {acct.crypto_status or 'unknown'}")
    if acct.trading_blocked:
        print("  ⚠ trading is blocked on this account")
    return 0


if __name__ == "__main__":
    sys.exit(main())
