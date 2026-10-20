"""Quality + value + insiders: own profitable, cash-generating, modestly indebted companies that are
cheap relative to their earnings and cash flow, with a bonus when insiders are buying. Layer 1 of the
"research companies, invest only with valid reasons" idea; layer 2 (paper/research_desk.py) has the
free AI write and fact-check a memo for each pick.

Design fixed on 2026-10-20, before any test:
  - decide on the last NYSE trading day of each month (calendar known in advance), fill next open
  - use only figures filed with the SEC on or before that day (data/fundamentals.py, first filing)
  - eligible: 20-day average dollar volume >= $50M, unadjusted price >= $5, market cap >= $2B,
    positive latest-year net income and free cash flow (no buying on hope)
  - score = quality + value + 0.5 x insider, where quality = mean percentile of return on assets,
    cash return on assets, low accruals, low leverage; value = mean percentile of earnings yield and
    free-cash-flow yield; insider = 1 if 2+ insiders bought >= $100k in total in the last 90 days
  - hold the `top_n` best, equal weight; large-cap costs (7 bps each way)
  - market cap = unadjusted month-end close x latest filed share count, corrected for splits since
    that filing (via the unadjusted/adjusted price ratio)

Bias warning: the universe is today's SEC company list (largest ~1,500), so companies that
disappeared since 2016 are missing: survivorship bias flatters the results.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trader.backtest.data import MarketData
from trader.data.daily import alpaca_symbol, monthly_raw_path
from trader.data.store import read
from trader.strategies.base import Strategy
from trader.strategies.turn_of_month import trading_days

NY = "America/New_York"
MIN_DOLLAR_VOLUME = 50e6
MIN_PRICE = 5.0
MIN_MARKET_CAP = 2e9


def universe_map() -> pd.DataFrame:
    """cik -> Alpaca symbol for the fundamentals universe."""
    from trader.strategies.earnings import earnings_universe
    u = earnings_universe(1500)
    return pd.DataFrame({"cik": u["cik"].astype(int), "symbol": u["symbol"].map(alpaca_symbol)})


def month_end_decisions(index: pd.DatetimeIndex) -> list[int]:
    """Rows of `index` (daily bars) that are the last NYSE trading day of their month."""
    dates = index.tz_convert(NY).tz_localize(None).normalize()
    cal = trading_days(dates[0], dates[-1] + pd.Timedelta(days=40))
    last = set(pd.Series(cal, index=cal).groupby(cal.to_period("M")).max())
    return [i for i, d in enumerate(dates) if d in last]


def insider_flags(events: pd.DataFrame, date: pd.Timestamp, days: int = 90) -> set[str]:
    """Symbols where 2+ distinct insiders bought >= $100k in total, filed in the last `days`."""
    recent = events[(events["filing_date"] <= date) & (events["filing_date"] > date - pd.Timedelta(days=days))]
    g = recent.groupby("symbol").agg(insiders=("owner_cik", "nunique"), value=("value", "sum"))
    return set(g.index[(g["insiders"] >= 2) & (g["value"] >= 100_000)])


def pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True)


def score_snapshot(r: pd.DataFrame, insiders: pd.Series) -> pd.Series:
    """Composite score for the eligible companies in `r` (output of fundamentals.ratios)."""
    quality = pd.concat([pct_rank(r["roa"]), pct_rank(r["cash_roa"]), pct_rank(-r["accruals"]),
                         pct_rank(-r["leverage"])], axis=1).mean(axis=1)
    value = pd.concat([pct_rank(r["earnings_yield"]), pct_rank(r["fcf_yield"])], axis=1).mean(axis=1)
    return quality + value + 0.5 * insiders.reindex(r.index).fillna(0)


class QualityValue(Strategy):
    name = "quality_value"
    bars = "daily"
    cost_venue = "alpaca_largecaps"
    default_grid = {"top_n": [20, 40]}

    def __init__(self, symbols, top_n: int = 20, fundamentals: pd.DataFrame | None = None,
                 monthly_raw: pd.DataFrame | None = None, events: pd.DataFrame | None = None,
                 cik_map: pd.DataFrame | None = None):
        super().__init__(symbols, top_n=top_n)
        self._fund, self._raw, self._events, self._map = fundamentals, monthly_raw, events, cik_map

    @classmethod
    def default_universe(cls) -> list[str]:
        from trader.data.daily import daily_path
        have = set(read(daily_path())["symbol"].unique())
        return sorted(set(universe_map()["symbol"]) & have)

    def _inputs(self):
        if self._fund is None:
            from trader.data.fundamentals import load_fundamentals
            self._fund = load_fundamentals()
        if self._raw is None:
            self._raw = read(monthly_raw_path())
        if self._events is None:
            from trader.strategies.insider import load_events
            self._events = load_events()
        if self._map is None:
            self._map = universe_map()
        return self._fund, self._raw, self._events, self._map

    def picks(self, data: MarketData, row: int) -> pd.DataFrame:
        """The scored, eligible companies at decision row `row` (best first)."""
        from trader.data.fundamentals import as_of, ratios
        fund, raw, events, cmap = self._inputs()
        date = data.index[row].tz_convert(NY).tz_localize(None).normalize()
        cols = [s for s in self.symbols if s in data.close.columns]
        cmap = cmap[cmap["symbol"].isin(cols)].drop_duplicates("symbol").drop_duplicates("cik")
        snap = as_of(fund, date)
        cmap = cmap[cmap["cik"].isin(snap.index)]
        if cmap.empty:
            return pd.DataFrame()
        snap = snap.loc[cmap["cik"]].set_axis(cmap["symbol"].to_numpy())
        symbols = snap.index
        # 20-day average dollar volume (split-adjusted price x volume: splits cancel out)
        dollar = (data.close[symbols] * data.volume[symbols]).where(data.tradable[symbols])
        adv = dollar.iloc[max(0, row - 19):row + 1].mean()
        # unadjusted close of this month (its last trading day is the decision day)
        months = self._raw_months(raw, symbols)
        price = months.get(date.to_period("M"), pd.Series(dtype=float)).reindex(symbols)
        shares = snap["shares"] if "shares" in snap else pd.Series(np.nan, index=symbols)
        mcap = price * shares * self._split_factor(data, symbols, row, fund, cmap, months)
        if "public_float" in snap:      # multi-class companies with no total share count (Visa, Alphabet)
            mcap = mcap.combine_first(self._float_now(data, snap, row))
        r = ratios(snap, mcap)
        ok = ((adv >= MIN_DOLLAR_VOLUME) & (price >= MIN_PRICE) & (mcap >= MIN_MARKET_CAP)
              & (r["earnings_yield"] > 0) & (r["fcf_yield"] > 0)
              & r[["roa", "cash_roa", "accruals", "leverage"]].notna().all(axis=1))
        r = r[ok]
        if r.empty:
            return r
        ins = pd.Series(1.0, index=sorted(insider_flags(events, date) & set(r.index)))
        out = r.assign(score=score_snapshot(r, ins), insider=ins.reindex(r.index).fillna(0.0),
                       market_cap=mcap[r.index], price=price[r.index])
        return out.sort_values("score", ascending=False, kind="stable")

    def _float_now(self, data, snap, row) -> pd.Series:
        """Public float scaled by the (split-adjusted) price change since its date."""
        out = pd.Series(np.nan, index=snap.index)
        for s in snap.index:
            pf, when = snap.at[s, "public_float"], snap.get("public_float_date", pd.Series(dtype=object)).get(s)
            if pd.isna(pf) or pd.isna(when):
                continue
            px = data.close[s]
            then = px.loc[:pd.Timestamp(when).tz_localize(NY).tz_convert("UTC") + pd.Timedelta(days=1)].dropna()
            if then.empty or pd.isna(px.iat[row]):
                continue
            out[s] = pf * px.iat[row] / then.iloc[-1]
        return out

    def _raw_months(self, raw: pd.DataFrame, symbols) -> dict:
        """{month period: unadjusted month close per symbol}, cached."""
        if getattr(self, "_months", None) is None:
            r = raw.assign(month=raw["ts"].dt.tz_convert(NY).dt.tz_localize(None).dt.to_period("M"))
            self._months = {m: g.set_index("symbol")["close"] for m, g in r.groupby("month")}
        return self._months

    def _split_factor(self, data, symbols, row, fund, cmap, months) -> pd.Series:
        """Splits since the share count was filed: (unadjusted / adjusted month close) at the filing
        month divided by the same ratio now. Adjusted prices are divided by every later split, so
        the ratio jumps exactly at a split (dividends move it only slightly)."""
        date = data.index[row].tz_convert(NY).tz_localize(None)
        shares = fund[(fund["concept"] == "shares") & (fund["filed"] <= date)]
        filed = shares.sort_values("filed").groupby("cik")["filed"].last()
        sym_filed = filed.reindex(cmap.set_index("symbol")["cik"]).set_axis(cmap["symbol"].to_numpy())
        adj = data.close[list(symbols)]
        adj_month = adj.groupby(adj.index.tz_convert(NY).tz_localize(None).to_period("M")).last()
        now = date.to_period("M")
        out = pd.Series(1.0, index=symbols)
        for s in symbols:
            f = sym_filed.get(s)
            if pd.isna(f):
                continue
            then = pd.Timestamp(f).to_period("M")
            try:
                k = (months[then][s] / adj_month.at[then, s]) / (months[now][s] / adj_month.at[now, s])
            except (KeyError, ZeroDivisionError):
                continue
            if np.isfinite(k) and k > 0:
                out[s] = round(k, 2)
        return out

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        cols = [s for s in self.symbols if s in data.close.columns and s != "SPY"]
        w = np.full((len(data.index), len(cols)), np.nan)
        w[0] = 0.0
        col = {s: i for i, s in enumerate(cols)}
        for row in month_end_decisions(data.index):
            p = self.picks(data, row)
            target = np.zeros(len(cols))
            chosen = [s for s in p.index[:self.params["top_n"]] if s in col]
            for s in chosen:
                target[col[s]] = 1.0 / self.params["top_n"]
            w[row] = target
        return pd.DataFrame(w, index=data.index, columns=cols).ffill()
