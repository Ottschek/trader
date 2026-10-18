"""Point the Telegram bot at the Cloudflare Worker, so /status, /stop and /resume work.

Run:  .venv/bin/python scripts/set_telegram_webhook.py https://trader-scheduler.<subdomain>.workers.dev
      .venv/bin/python scripts/set_telegram_webhook.py --info
Uses TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET from .env (never printed).
"""

from __future__ import annotations

import sys

from trader.config import secret
from trader.monitor.telegram import TelegramError, _call


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    try:
        if sys.argv[1] == "--info":
            info = _call("getWebhookInfo")
            url = info.get("url", "")
            print(f"webhook: {url.split('/telegram/')[0] + '/telegram/[secret]' if url else 'none'}")
            print(f"pending updates: {info.get('pending_update_count')}, last error: {info.get('last_error_message', 'none')}")
            return 0
        hook = secret("TELEGRAM_WEBHOOK_SECRET")
        _call("setWebhook", {"url": f"{sys.argv[1].rstrip('/')}/telegram/{hook}", "secret_token": hook,
                             "allowed_updates": ["message"], "drop_pending_updates": True})
        _call("setMyCommands", {"commands": [
            {"command": "status", "description": "Paper account, positions, halt state"},
            {"command": "stop", "description": "Close strategy positions and halt"},
            {"command": "resume", "description": "Clear a halt"},
        ]})
        print("✓ Webhook set; commands registered.")
        return 0
    except TelegramError as e:
        print(f"✗ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
