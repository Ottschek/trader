"""Minimal client for Cloudflare D1's HTTP API."""

from __future__ import annotations

import requests

from trader.config import secret

MAX_PARAMS = 90  # D1 allows at most 100 bound parameters per statement


class D1Error(Exception):
    pass


class D1:
    def __init__(self, database_id: str, account_id: str | None = None, token: str | None = None):
        account_id = account_id or secret("CLOUDFLARE_ACCOUNT_ID")
        self._url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query"
        self._headers = {"Authorization": f"Bearer {token or secret('CLOUDFLARE_API_TOKEN')}"}

    def query(self, sql: str, params: list | None = None) -> list[dict]:
        try:
            r = requests.post(self._url, headers=self._headers, json={"sql": sql, "params": params or []}, timeout=30)
        except requests.RequestException as e:  # never let the URL/headers end up in logs
            raise D1Error(f"D1 request failed: {type(e).__name__}") from None
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code != 200 or not body.get("success"):
            errors = "; ".join(e.get("message", "") for e in body.get("errors", [])) or f"HTTP {r.status_code}"
            raise D1Error(f"D1 query failed: {errors}")
        return body["result"][0]["results"]

    def insert_many(self, table: str, columns: list[str], rows: list[tuple]) -> int:
        """INSERT OR REPLACE in chunks that stay under D1's parameter limit."""
        per_chunk = max(1, MAX_PARAMS // len(columns))
        placeholder = "(" + ",".join("?" * len(columns)) + ")"
        for i in range(0, len(rows), per_chunk):
            chunk = rows[i:i + per_chunk]
            sql = (f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES "
                   + ",".join([placeholder] * len(chunk)))
            self.query(sql, [v for row in chunk for v in row])
        return len(rows)
