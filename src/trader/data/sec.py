"""SEC EDGAR data: insider purchases (Form 4) and earnings release filings (8-K item 2.02).

The SEC requires automated requests to identify themselves with a contact email; it comes
from SEC_CONTACT_EMAIL (.env / GitHub Secrets), never from the public code. Requests are
throttled well under the SEC's 10-per-second limit.

Stored under data/sec/:
    insider_purchases.parquet  open-market purchases (Form 4, code P) by company insiders
    earnings_8k.parquet        8-K filings with item 2.02 (results of operations), with the exact
                               acceptance time, which is when the market learns the results
    state.json                 which quarterly insider files have been processed
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

from trader.config import secret
from trader.data.store import data_dir, upsert

INSIDER_URL = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{y}q{q}_form345.zip"
MIN_INTERVAL = 0.15   # seconds between requests (~6/s)
_last_request = 0.0


def sec_path(name: str) -> Path:
    return data_dir() / "sec" / name


def _headers() -> dict:
    return {"User-Agent": f"trader-research {secret('SEC_CONTACT_EMAIL')}", "Accept-Encoding": "gzip, deflate"}


def sec_get(url: str, timeout: int = 120) -> requests.Response:
    global _last_request
    for attempt in range(4):
        wait = MIN_INTERVAL - (time.time() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.time()
        r = requests.get(url, headers=_headers(), timeout=timeout)
        if r.status_code in (429, 503):
            time.sleep(10 * (attempt + 1))
            continue
        return r
    return r


# --- insider purchases ------------------------------------------------------------------

def parse_insider_zip(content: bytes) -> pd.DataFrame:
    """Open-market purchases (Form 4, transaction code P, acquired) from one quarterly file."""
    z = zipfile.ZipFile(io.BytesIO(content))
    read = lambda name, cols: pd.read_csv(z.open(name), sep="\t", usecols=cols, dtype=str, low_memory=False)  # noqa: E731
    sub = read("SUBMISSION.tsv", ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK", "ISSUERNAME",
                                  "ISSUERTRADINGSYMBOL"])
    trans = read("NONDERIV_TRANS.tsv", ["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
                                        "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD"])
    owners = read("REPORTINGOWNER.tsv", ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNER_RELATIONSHIP"])
    sub = sub[sub["DOCUMENT_TYPE"] == "4"]
    trans = trans[(trans["TRANS_CODE"] == "P") & (trans["TRANS_ACQUIRED_DISP_CD"] == "A")]
    df = trans.merge(sub, on="ACCESSION_NUMBER").merge(owners.drop_duplicates("ACCESSION_NUMBER"), on="ACCESSION_NUMBER")
    df["shares"] = pd.to_numeric(df["TRANS_SHARES"], errors="coerce")
    df["price"] = pd.to_numeric(df["TRANS_PRICEPERSHARE"], errors="coerce")
    df = df[(df["shares"] > 0) & (df["price"] > 0)]
    out = pd.DataFrame({
        "accession": df["ACCESSION_NUMBER"],
        "filing_date": pd.to_datetime(df["FILING_DATE"], format="%d-%b-%Y", errors="coerce"),
        "trans_date": pd.to_datetime(df["TRANS_DATE"], format="%d-%b-%Y", errors="coerce"),
        "issuer_cik": df["ISSUERCIK"],
        "issuer": df["ISSUERNAME"],
        "symbol": df["ISSUERTRADINGSYMBOL"].fillna("").str.strip().str.upper(),
        "owner_cik": df["RPTOWNERCIK"],
        "relationship": df["RPTOWNER_RELATIONSHIP"],
        "shares": df["shares"],
        "price": df["price"],
    })
    out["value"] = out["shares"] * out["price"]
    valid = out["filing_date"].notna() & out["symbol"].str.fullmatch(r"[A-Z][A-Z.\-]{0,6}")
    return out[valid].reset_index(drop=True)


def _load_state() -> dict:
    p = sec_path("state.json")
    return json.loads(p.read_text()) if p.exists() else {"insider_quarters": []}


def _save_state(state: dict) -> None:
    p = sec_path("state.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=1, sort_keys=True))


def quarters(start_year: int, today: pd.Timestamp) -> list[tuple[int, int]]:
    out = []
    for y in range(start_year, today.year + 1):
        for q in range(1, 5):
            quarter_end = pd.Timestamp(year=y, month=3 * q, day=1) + pd.offsets.MonthEnd(0)
            if quarter_end < today.tz_localize(None):
                out.append((y, q))
    return out


def update_insider_purchases(today: pd.Timestamp, start_year: int = 2016, log=print) -> int:
    """Process every finished quarter not yet done (the SEC publishes each quarter shortly after it ends)."""
    state = _load_state()
    done = set(state["insider_quarters"])
    added = 0
    for y, q in quarters(start_year, today):
        key = f"{y}q{q}"
        if key in done:
            continue
        r = sec_get(INSIDER_URL.format(y=y, q=q))
        if r.status_code == 404:
            log(f"  insider {key}: not published yet")
            continue
        r.raise_for_status()
        rows = parse_insider_zip(r.content)
        added += upsert(sec_path("insider_purchases.parquet"), rows, key=["accession", "owner_cik", "trans_date", "shares", "price"])
        state["insider_quarters"] = sorted(done | {key})
        done.add(key)
        _save_state(state)
        log(f"  insider {key}: {len(rows):,} purchases")
    return added


# --- 8-K earnings releases ----------------------------------------------------------------

def company_tickers() -> pd.DataFrame:
    """SEC's list of current companies: cik, ticker, name."""
    data = sec_get("https://www.sec.gov/files/company_tickers.json").json()
    return pd.DataFrame([{"cik": int(v["cik_str"]), "symbol": v["ticker"].upper(), "name": v["title"]} for v in data.values()])


def parse_8k_earnings(filings: dict, symbol: str) -> pd.DataFrame:
    """8-K filings with item 2.02 from a filings block (the 'recent' part of a company's
    submissions JSON, or one of its older history pages, which have the same columns)."""
    df = pd.DataFrame({k: filings.get(k, []) for k in ("form", "items", "acceptanceDateTime", "accessionNumber")})
    if df.empty:
        return pd.DataFrame(columns=["symbol", "accepted", "accession"])
    df = df[(df["form"] == "8-K") & df["items"].fillna("").str.split(",").map(lambda xs: "2.02" in [x.strip() for x in xs])]
    return pd.DataFrame({"symbol": symbol, "accepted": pd.to_datetime(df["acceptanceDateTime"], utc=True),
                         "accession": df["accessionNumber"]}).reset_index(drop=True)


def update_earnings_8k(companies: pd.DataFrame, since: str = "2016-01-01", log=print) -> int:
    """companies: rows with cik and symbol. Fetches older history pages back to `since`.
    Resumable: progress is saved every 100 companies, and companies already done are skipped;
    a network error skips that company (it's retried on the next run)."""
    state = _load_state()
    done = set(state.get("earnings_done", []))
    todo = companies[~companies["cik"].astype(int).isin(done)]
    frames, failed, added = [], 0, 0

    def flush():
        nonlocal frames, added
        if frames:
            added += upsert(sec_path("earnings_8k.parquet"), pd.concat(frames, ignore_index=True), key=["symbol", "accession"])
            frames = []
        state["earnings_done"] = sorted(done)
        _save_state(state)

    for i, row in enumerate(todo.itertuples(index=False)):
        try:
            r = sec_get(f"https://data.sec.gov/submissions/CIK{int(row.cik):010d}.json", timeout=60)
            if r.status_code != 200:
                failed += 1
                continue
            sub = r.json()
            parts = [parse_8k_earnings(sub.get("filings", {}).get("recent", {}), row.symbol)]
            for page in sub.get("filings", {}).get("files", []):   # older history, if any reaches back far enough
                if page.get("filingTo", "9999") < since:
                    continue
                pr = sec_get(f"https://data.sec.gov/submissions/{page['name']}", timeout=60)
                if pr.status_code != 200:
                    raise requests.ConnectionError(f"history page {page['name']}: HTTP {pr.status_code}")
                parts.append(parse_8k_earnings(pr.json(), row.symbol))
        except requests.RequestException:
            failed += 1
            continue
        frames.extend(parts)
        done.add(int(row.cik))
        if (i + 1) % 100 == 0:
            flush()
            log(f"  8-K: {i + 1}/{len(todo)} companies this run")
    flush()
    log(f"  8-K: +{added} earnings filings; {len(done)} companies done, {failed} failed (rerun to retry)")
    return added
