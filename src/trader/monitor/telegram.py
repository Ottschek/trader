"""Send Telegram messages from the bot. Only ever to your own chat ID."""

from __future__ import annotations

import requests

from trader.config import secret

MAX_LEN = 4000  # Telegram's limit is 4096 characters


class TelegramError(Exception):
    pass


def _call(method: str, payload: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{secret('TELEGRAM_BOT_TOKEN')}/{method}"
    try:
        r = requests.post(url, json=payload or {}, timeout=30)
    except requests.RequestException as e:  # the URL contains the token: never print it
        raise TelegramError(f"Telegram request failed: {type(e).__name__}") from None
    try:
        body = r.json()
    except ValueError:
        raise TelegramError(f"Telegram {method} failed: HTTP {r.status_code}") from None
    if not body.get("ok"):
        raise TelegramError(f"Telegram {method} failed: {body.get('description', r.status_code)}")
    return body["result"]


def send(text: str) -> None:
    if len(text) > MAX_LEN:
        text = text[:MAX_LEN - 20] + "\n…(truncated)"
    _call("sendMessage", {"chat_id": secret("TELEGRAM_CHAT_ID"), "text": text,
                          "parse_mode": "HTML", "disable_web_page_preview": True})


def get_me() -> dict:
    return _call("getMe")


def recent_chats() -> list[dict]:
    """Chats that recently messaged the bot (used once to find your chat ID)."""
    chats = {}
    for update in _call("getUpdates"):
        msg = update.get("message") or update.get("edited_message") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = chat
    return list(chats.values())


def send_photo(path, caption: str) -> None:
    """A picture with a caption (HTML, at most 1024 characters)."""
    url = f"https://api.telegram.org/bot{secret('TELEGRAM_BOT_TOKEN')}/sendPhoto"
    try:
        with open(path, "rb") as f:
            r = requests.post(url, data={"chat_id": secret("TELEGRAM_CHAT_ID"), "caption": caption[:1024], "parse_mode": "HTML"},
                              files={"photo": f}, timeout=60)
    except requests.RequestException as e:  # the URL contains the token: never print it
        raise TelegramError(f"Telegram request failed: {type(e).__name__}") from None
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not body.get("ok"):
        raise TelegramError(f"Telegram sendPhoto failed: {body.get('description', r.status_code)}")
