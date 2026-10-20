"""Send a report picture to Telegram and archive it for the web app's Reports page
(picture in R2 under telegram/<id>.png, index row in D1 table telegram_reports)."""

from __future__ import annotations

import secrets

import pandas as pd

DASHBOARD = "https://trader-web-6t6.pages.dev"


def new_id() -> str:
    return secrets.token_hex(6)


def deliver(path, caption: str, kind: str, title: str, report_id: str, settings, archive: bool = True) -> None:
    from trader.monitor import telegram
    link = f'\n<a href="{DASHBOARD}/#/reports">All reports</a>' if archive else ""
    telegram.send_photo(path, caption[:1024 - len(link)] + link)
    if not archive:
        return
    from trader.data import r2
    from trader.monitor.d1 import D1
    with open(path, "rb") as f:
        r2.client().put_object(Bucket=settings.cloud.r2_bucket, Key=f"telegram/{report_id}.png", Body=f.read(), ContentType="image/png")
    D1(settings.cloud.d1_database_id).insert_many(
        "telegram_reports", ["id", "created_at", "kind", "title", "caption"],
        [(report_id, pd.Timestamp.now(tz="UTC").isoformat(), kind, title, caption)])
