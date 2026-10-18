"""Load stored bars into the aligned tables a backtest needs.

Timing model (used everywhere in backtest/):
    A strategy looks at bar t after it closed and decides target weights.
    Those trades fill at the OPEN of the asset's next bar. Returns are split into
        gap   = previous close -> this open   (earned by the old position)
        intra = this open -> this close       (earned by the new position)
    so nothing ever trades at a price it couldn't have known.

Corporate actions are applied *forward* inside the gap return of the first bar on or
after the ex-date. The adjusted price series is built forward from the first real
price, so no future split or dividend leaks into earlier prices.

Crypto: signals use Coinbase (deep history); fills use Alpaca returns where Alpaca has
the bar and the bar before it, otherwise Coinbase (Alpaca starts 2021 and has gaps;
the two differ by a median of ~1 bps).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from trader.data.sessions import NY
from trader.data.store import bars_path, corporate_actions_path, funding_path, read

SPIKE_THRESHOLD = {"crypto": 0.25, "stock": 0.15}


@dataclass
class MarketData:
    close: pd.DataFrame      # adjusted close for signals; NaN where the asset has no bar
    open: pd.DataFrame       # adjusted open for signals
    volume: pd.DataFrame
    gap: pd.DataFrame        # execution returns, previous close -> open (0 where no bar)
    intra: pd.DataFrame      # execution returns, open -> close (0 where no bar)
    tradable: pd.DataFrame   # True where the asset has a bar (can trade at its open)
    venue: dict[str, str]    # symbol -> cost venue in settings.costs
    cleaned: dict[str, int] = field(default_factory=dict)  # bad ticks flattened per symbol
    funding: pd.DataFrame | None = None  # Hyperliquid hourly funding rate per crypto symbol (if loaded)

    @property
    def index(self) -> pd.DatetimeIndex:
        return self.close.index

    @property
    def symbols(self) -> list[str]:
        return list(self.close.columns)

    def until(self, ts) -> "MarketData":
        """The data as it looked at time `ts` (inclusive). Used by the lookahead check."""
        cut = lambda df: df.loc[:ts]  # noqa: E731
        return MarketData(cut(self.close), cut(self.open), cut(self.volume), cut(self.gap),
                          cut(self.intra), cut(self.tradable), self.venue, self.cleaned,
                          cut(self.funding) if self.funding is not None else None)


# --- per-asset preparation -----------------------------------------------------

def flatten_spikes(bars: pd.DataFrame, threshold: float) -> tuple[pd.DataFrame, int]:
    """Replace spike-and-revert bad ticks (a huge 1-bar move that immediately reverses)
    with a flat bar at the previous close. Uses the next bar to spot them, which is a
    deliberate, conservative exception: bad ticks would otherwise hand fake profits to
    any strategy that buys dips."""
    if len(bars) < 3:
        return bars, 0
    r = np.log(bars["close"]).diff()
    bad = (r.abs() > threshold) & (r.shift(-1).abs() > threshold) & (np.sign(r) != np.sign(r.shift(-1)))
    if not bad.any():
        return bars, 0
    out = bars.copy()
    prev_close = out["close"].shift(1)
    for col in ("open", "high", "low", "close"):
        out.loc[bad, col] = prev_close[bad]
    return out, int(bad.sum())


def asset_returns(bars: pd.DataFrame, actions: pd.DataFrame | None = None) -> pd.DataFrame:
    """gap/intra returns on the asset's own bar sequence, with corporate actions applied
    to the gap of the first bar on or after each ex-date."""
    b = bars.sort_values("ts").set_index("ts")
    adj_open = b["open"].copy()
    dividend = pd.Series(0.0, index=b.index)
    if actions is not None and not actions.empty:
        bar_dates = b.index.tz_convert(NY).normalize().tz_localize(None)
        for _, a in actions.iterrows():
            first = np.flatnonzero(bar_dates >= pd.Timestamp(a["ex_date"]).normalize())
            if not len(first) or first[0] == 0:
                continue  # ex-date before our first bar or after the last
            i = first[0]
            if a["type"] in ("forward_splits", "reverse_splits") and a["old_rate"]:
                adj_open.iloc[i] *= a["new_rate"] / a["old_rate"]
            elif a["type"] == "stock_dividends" and a["rate"]:
                adj_open.iloc[i] *= 1 + a["rate"]
            elif a["type"] == "cash_dividends" and a["rate"]:
                dividend.iloc[i] += a["rate"]
    gap = (adj_open + dividend) / b["close"].shift(1) - 1
    gap.iloc[0] = 0.0
    intra = b["close"] / b["open"] - 1
    return pd.DataFrame({"gap": gap, "intra": intra, "open": b["open"], "close": b["close"],
                         "volume": b["volume"]})


def adjusted_prices(r: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Forward-adjusted close/open: start at the first real price and compound returns."""
    growth = (1 + r["gap"]) * (1 + r["intra"])
    close = r["close"].iloc[0] / (1 + r["intra"].iloc[0]) * growth.cumprod()
    open_ = close.shift(1) * (1 + r["gap"])
    open_.iloc[0] = r["open"].iloc[0]
    return close, open_


# --- loading ---------------------------------------------------------------------

def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _window(df: pd.DataFrame, start, end) -> pd.DataFrame:
    if df.empty:
        return df
    if start is not None:
        df = df[df["ts"] >= _utc(start)]
    if end is not None:
        df = df[df["ts"] <= _utc(end)]
    return df


def load_market(crypto=(), stocks=(), start=None, end=None, stock_session="regular",
                clean=True, funding=False) -> MarketData:
    """Build aligned tables for the given symbols. `stock_session`: "regular" or "all".
    `funding=True` also loads Hyperliquid funding rates for the crypto symbols. A rate
    stamped at hour h was settled at h, so it is known by the close of the bar starting at h."""
    cols: dict[str, dict[str, pd.Series]] = {k: {} for k in ("close", "open", "volume", "gap", "intra")}
    venue, cleaned = {}, {}

    def add(symbol, sig: pd.DataFrame, exe: pd.DataFrame):
        c, o = adjusted_prices(sig)
        cols["close"][symbol], cols["open"][symbol], cols["volume"][symbol] = c, o, sig["volume"]
        cols["gap"][symbol], cols["intra"][symbol] = exe["gap"], exe["intra"]

    for s in crypto:
        cb = _window(read(bars_path("coinbase", s)), start, end)
        if cb.empty:
            raise ValueError(f"no Coinbase data for {s}")
        cb, cleaned[s] = flatten_spikes(cb.sort_values("ts"), SPIKE_THRESHOLD["crypto"]) if clean else (cb, 0)
        sig = asset_returns(cb)
        exe = sig[["gap", "intra"]].copy()
        al = _window(read(bars_path("alpaca_crypto", s)), start, end)
        if not al.empty:
            al = flatten_spikes(al.sort_values("ts"), SPIKE_THRESHOLD["crypto"])[0] if clean else al
            ar = asset_returns(al)
            # Alpaca return only where Alpaca has this bar and the one an hour before it
            consecutive = ar.index.to_series().diff() == pd.Timedelta(hours=1)
            use = ar.index[consecutive.values].intersection(exe.index)
            exe.loc[use, ["gap", "intra"]] = ar.loc[use, ["gap", "intra"]].values
        add(s, sig, exe)
        venue[s] = "alpaca_crypto"

    actions = read(corporate_actions_path())
    for s in stocks:
        st = _window(read(bars_path("alpaca_stocks", s)), start, end)
        if st.empty:
            raise ValueError(f"no Alpaca stock data for {s}")
        if stock_session != "all":
            st = st[st["session"] == stock_session]
        st, cleaned[s] = flatten_spikes(st.sort_values("ts"), SPIKE_THRESHOLD["stock"]) if clean else (st, 0)
        acts = actions[actions["symbol"] == s] if not actions.empty else None
        r = asset_returns(st, acts)
        add(s, r, r)
        venue[s] = "alpaca_stocks"

    frames = {k: pd.DataFrame(v).sort_index() for k, v in cols.items()}
    tradable = frames["close"].notna()
    funding_frame = None
    if funding:
        rates = {}
        for s in crypto:
            f = _window(read(funding_path(s.split("/")[0])), start, end)
            if not f.empty:
                rates[s] = f.drop_duplicates("ts").set_index("ts")["funding_rate"]
        funding_frame = pd.DataFrame(rates).reindex(frames["close"].index)
    return MarketData(
        close=frames["close"], open=frames["open"], volume=frames["volume"],
        gap=frames["gap"].fillna(0.0), intra=frames["intra"].fillna(0.0),
        tradable=tradable, venue=venue, cleaned=cleaned, funding=funding_frame,
    )
