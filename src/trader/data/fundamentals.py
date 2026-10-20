"""Company fundamentals from SEC XBRL filings, point-in-time.

Source: the SEC's bulk companyfacts.zip (every company's reported financial facts, ~1.4 GB), with
the SEC contact header from data/sec.py. Only our universe's companies and a few concepts are kept:

    data/sec/fundamentals.parquet   cik, concept, start, end, val, filed, form
      concept: revenue, net_income, op_cash_flow, capex, gross_profit (annual, from 10-K)
               assets, liabilities, equity, long_term_debt, cash (balance sheet, 10-K and 10-Q)
               shares (diluted weighted average of the latest quarter or year; one total for all classes)
               shares_cover (cover-page count, fallback; missing for multi-class companies)
               public_float (10-K cover: $ value of shares held by the public on `end`; last resort)
      filed: the FIRST date the figure was filed. Companies repeat last year's numbers in this
             year's report; only the first filing counts, so nothing is back-dated.

`as_of(fund, date)` gives what was publicly known on a date: the latest annual figures and the
latest balance sheet filed on or before it.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from trader.data.sec import sec_path
from trader.data.store import read

BULK_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
MAX_AGE = pd.Timedelta(days=540)   # a concept whose latest figure is older than this is ignored (stopped filing?)

FLOWS = {   # annual figures (10-K), first matching tag wins per company and year
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "op_cash_flow": ["NetCashProvidedByUsedInOperatingActivities",
                     "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "gross_profit": ["GrossProfit"],
}
INSTANTS = {
    "assets": ["Assets"], "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
}
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "40-F"}
QUARTER_FORMS = ANNUAL_FORMS | {"10-Q", "10-Q/A"}


def fundamentals_path() -> Path:
    return sec_path("fundamentals.parquet")


def download_bulk(target: Path, log=print) -> Path:
    """Stream companyfacts.zip to `target` (never held in memory)."""
    from trader.data.sec import _headers
    target.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(BULK_URL, headers=_headers(), stream=True, timeout=600) as r:
        r.raise_for_status()
        tmp = target.with_suffix(".part")
        size = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=8 << 20):
                f.write(chunk)
                size += len(chunk)
        tmp.replace(target)
    log(f"  companyfacts.zip: {size / 1e9:.2f} GB")
    return target


def facts_rows(cik: int, facts: dict) -> list[dict]:
    """Rows for one company's companyfacts JSON (the `facts` object)."""
    rows = []
    gaap = facts.get("us-gaap", {})

    def take(concept, tags, annual):
        for priority, tag in enumerate(tags):      # companies switch tags over the years: collect them all
            units = gaap.get(tag, {}).get("units", {}).get("USD")
            if not units:
                continue
            for x in units:
                form = x.get("form")
                if annual:
                    if form not in ANNUAL_FORMS or not x.get("start"):
                        continue
                    days = (pd.Timestamp(x["end"]) - pd.Timestamp(x["start"])).days
                    if not 330 <= days <= 400:
                        continue
                elif form not in QUARTER_FORMS or x.get("start"):
                    continue
                rows.append({"cik": cik, "concept": concept, "tag": tag, "priority": priority, "start": x.get("start"),
                             "end": x["end"], "val": float(x["val"]), "filed": x["filed"], "form": form})

    for concept, tags in FLOWS.items():
        take(concept, tags, annual=True)
    for concept, tags in INSTANTS.items():
        take(concept, tags, annual=False)
    # share count: the diluted weighted average every 10-Q/10-K reports as one total (the cover-page
    # count is left out of this dataset for companies with several share classes, e.g. Mastercard)
    for x in gaap.get("WeightedAverageNumberOfDilutedSharesOutstanding", {}).get("units", {}).get("shares", []):
        if x.get("form") in QUARTER_FORMS and x.get("start"):
            rows.append({"cik": cik, "concept": "shares", "tag": "diluted_avg", "priority": 0, "start": x["start"],
                         "end": x["end"], "val": float(x["val"]), "filed": x["filed"], "form": x["form"]})
    for x in facts.get("dei", {}).get("EntityPublicFloat", {}).get("units", {}).get("USD", []):
        rows.append({"cik": cik, "concept": "public_float", "tag": "dei", "priority": 0, "start": None, "end": x["end"],
                     "val": float(x["val"]), "filed": x["filed"], "form": x.get("form")})
    for x in facts.get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", []):
        rows.append({"cik": cik, "concept": "shares_cover", "tag": "dei", "priority": 0, "start": None, "end": x["end"],
                     "val": float(x["val"]), "filed": x["filed"], "form": x.get("form")})
    return rows


def first_filed(df: pd.DataFrame) -> pd.DataFrame:
    """Keep each figure (company, concept, period) only as of the first time it was filed, under any
    of the concept's tags (ties on the same day: the preferred tag)."""
    df = df.sort_values(["filed", "priority"], kind="stable")
    return df.drop_duplicates(["cik", "concept", "start", "end"], keep="first").reset_index(drop=True)


def extract(zip_path: Path, ciks: set[int], log=print) -> pd.DataFrame:
    rows = []
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        for k, cik in enumerate(sorted(ciks)):
            name = f"CIK{cik:010d}.json"
            if name not in names:
                continue
            rows += facts_rows(cik, json.loads(z.read(name)).get("facts", {}))
            if (k + 1) % 500 == 0:
                log(f"  facts: {k + 1}/{len(ciks)} companies")
    df = pd.DataFrame(rows)
    for c in ("start", "end", "filed"):
        df[c] = pd.to_datetime(df[c])
    return first_filed(df)


def update_fundamentals(universe: pd.DataFrame, zip_path: Path, log=print, download: bool = True) -> int:
    if download or not zip_path.exists():
        download_bulk(zip_path, log)
    df = extract(zip_path, set(universe["cik"].astype(int)), log)
    path = fundamentals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    log(f"  fundamentals: {len(df):,} figures for {df['cik'].nunique():,} companies")
    return len(df)


def as_of(fund: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
    """One row per company: what was public on `date` (filed on or before it).
    Annual flows: the latest fiscal year (and the one before, `_prev`, for growth).
    Balance sheet and shares: the latest reported."""
    date = date.tz_localize(None) if date.tzinfo else date
    known = fund[fund["filed"] <= date]
    fresh = date - MAX_AGE           # a concept whose LATEST figure was filed before this is stale (dropped);
    out = {}                         # older figures still count as history (last year's revenue, for growth)
    flows = known[known["concept"].isin(FLOWS)].sort_values(["cik", "concept", "end"])
    if len(flows):
        last = flows.groupby(["cik", "concept"]).tail(2)
        latest_filed = last.groupby(["cik", "concept"])["filed"].transform("max")
        last = last[latest_filed > fresh]
        cur = last.groupby(["cik", "concept"]).tail(1).pivot(index="cik", columns="concept", values="val")
        prev = last.groupby(["cik", "concept"]).head(1).pivot(index="cik", columns="concept", values="val")
        prev_end = last.groupby(["cik", "concept"]).head(1).pivot(index="cik", columns="concept", values="end")
        cur_end = last.groupby(["cik", "concept"]).tail(1).pivot(index="cik", columns="concept", values="end")
        prev = prev.where(prev_end < cur_end)          # only a genuinely earlier year counts as "previous"
        out.update({c: cur[c] for c in cur})
        out.update({f"{c}_prev": prev[c] for c in prev})
        if "revenue" in cur_end:
            out["fiscal_year_end"] = cur_end["revenue"]
    inst = known[~known["concept"].isin(FLOWS)].sort_values(["cik", "concept", "end", "filed"])
    inst = inst[inst["filed"] > fresh]
    if len(inst):
        latest = inst.groupby(["cik", "concept"]).tail(1).pivot(index="cik", columns="concept", values="val")
        out.update({c: latest[c] for c in latest})
        ends = inst.groupby(["cik", "concept"]).tail(1).pivot(index="cik", columns="concept", values="end")
        if "public_float" in ends:
            out["public_float_date"] = ends["public_float"]
        if "shares_cover" in latest:
            out["shares"] = out["shares"].combine_first(latest["shares_cover"]) if "shares" in out else latest["shares_cover"]
    return pd.DataFrame(out)


def load_fundamentals() -> pd.DataFrame:
    df = read(fundamentals_path())
    if df.empty:
        raise ValueError("no fundamentals stored: run scripts/fundamentals.py first")
    return df


def ratios(snap: pd.DataFrame, market_cap: pd.Series) -> pd.DataFrame:
    """Quality and value ratios from an as_of() snapshot and market caps (both indexed by cik)."""
    s = snap.reindex(market_cap.index)
    g = lambda c: s[c] if c in s else pd.Series(np.nan, index=s.index)  # noqa: E731
    fcf = g("op_cash_flow") - g("capex").fillna(0)
    out = pd.DataFrame({
        "roa": g("net_income") / g("assets"),
        "cash_roa": g("op_cash_flow") / g("assets"),
        "accruals": (g("net_income") - g("op_cash_flow")) / g("assets"),
        "leverage": g("liabilities") / g("assets"),
        "earnings_yield": g("net_income") / market_cap,
        "fcf_yield": fcf / market_cap,
        "revenue_growth": g("revenue") / g("revenue_prev") - 1,
    }, index=market_cap.index)
    return out.replace([np.inf, -np.inf], np.nan)
