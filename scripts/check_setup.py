"""Check that every key in .env works, without ever printing a key.

Run:  .venv/bin/python scripts/check_setup.py
"""

from __future__ import annotations

import sys

from trader.config import load_alpaca_secrets, load_settings


def check(name: str, fn) -> bool:
    try:
        detail = fn()
        print(f"✓ {name}: {detail}")
        return True
    except Exception as e:
        print(f"✗ {name}: {type(e).__name__}: {e}")
        return False


def main() -> int:
    cloud = load_settings().cloud

    def alpaca():
        from trader.execution.alpaca_account import get_account_summary
        a = get_account_summary(load_alpaca_secrets())
        return f"paper account {a.status}, equity {a.equity:,.2f} {a.currency}"

    def r2():
        from trader.data import r2 as r2mod
        objects = r2mod.remote_index(r2mod.client(), cloud.r2_bucket)
        return f"bucket {cloud.r2_bucket} reachable, {len(objects)} files"

    def d1():
        from trader.monitor.d1 import D1
        tables = D1(cloud.d1_database_id).query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return "tables: " + ", ".join(t["name"] for t in tables if not t["name"].startswith("_"))

    def telegram():
        from trader.monitor import telegram as tg
        bot = tg.get_me()
        tg.send("👋 Trader bot connected. Alerts will arrive here.")
        return f"@{bot['username']} sent you a test message"

    results = [check("Alpaca", alpaca), check("R2", r2), check("D1", d1), check("Telegram", telegram)]
    print("\nAll good." if all(results) else "\nFix the ✗ lines above (check the matching values in .env).")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
