"""Find your Telegram chat ID. First send any message to your bot in Telegram, then run:

    .venv/bin/python scripts/telegram_chat_id.py

and put the number it prints into .env as TELEGRAM_CHAT_ID.
"""

from __future__ import annotations

import sys

from trader.config import ConfigError
from trader.monitor import telegram


def main() -> int:
    try:
        bot = telegram.get_me()
        chats = telegram.recent_chats()
    except (ConfigError, telegram.TelegramError) as e:
        print(f"✗ {e}")
        return 1
    print(f"Bot: @{bot['username']}")
    if not chats:
        print(f"No messages yet. Open Telegram, send any message to @{bot['username']}, then run this again.")
        return 1
    for c in chats:
        name = c.get("username") or c.get("first_name") or c.get("title")
        print(f"  chat ID {c['id']}  ({c['type']}: {name})")
    print("Put your own chat ID into .env as TELEGRAM_CHAT_ID.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
