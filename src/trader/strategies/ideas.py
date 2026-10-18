"""M4 crazy ideas that only need data we already collect."""

from __future__ import annotations

import numpy as np
import pandas as pd

from trader.backtest.data import MarketData
from trader.data.sessions import NY
from trader.strategies.base import Strategy
from trader.strategies.helpers import has_history, hold_after

WEEKEND = pd.Timedelta(hours=40)   # longer than any weekday overnight break (16.5 h)


class WeekendGap(Strategy):
    """Bitcoin trades all weekend, stocks don't. At 08:00 New York on the first trading
    morning after a weekend or holiday, measure BTC's move since the last stock close and
    trade crypto-linked stocks for that session (in at the 09:30 open, out at the next open).

    mode "follow": buy after BTC rose more than `threshold` (the move continues).
    mode "fade":   buy after BTC fell more than `threshold` (the stocks overreact and recover).
    The 08:00-09:00 BTC bar closes before the 09:30 open, so nothing here peeks ahead.
    """
    name = "weekend_gap"
    signal_symbols = ["BTC/USD"]
    default_grid = {"threshold": [0.03, 0.06], "mode": ["follow", "fade"]}

    def __init__(self, symbols, threshold: float = 0.03, mode: str = "follow"):
        if mode not in ("follow", "fade"):
            raise ValueError("mode must be 'follow' or 'fade'")
        super().__init__(symbols, threshold=threshold, mode=mode)

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        position = self._positions(data, self.params["threshold"], self.params["mode"] == "follow")
        w = position / len(self.symbols)
        return pd.DataFrame({s: w for s in self.symbols}, index=data.index)

    def _positions(self, data: MarketData, thr: float, follow: bool) -> np.ndarray:
        """1.0 on rows where we want to hold the stocks, else 0.0 (entry/exit logic above)."""
        idx = data.index
        local = idx.tz_convert(NY)
        stock_bar = data.tradable[self.symbols].any(axis=1).to_numpy()
        btc_vals = data.close["BTC/USD"].to_numpy()

        position = np.zeros(len(idx))
        holding = False
        last_stock_time = None
        btc_at_close = np.nan
        last_btc = np.nan
        for i, t in enumerate(idx):
            if not np.isnan(btc_vals[i]):
                last_btc = btc_vals[i]
            if stock_bar[i]:
                last_stock_time = t
                btc_at_close = last_btc       # BTC close of the hour ending at this stock bar's close
                if holding and local[i].hour >= 15:
                    holding = False           # decided at the session's last bar -> out at next open
            elif (not holding and last_stock_time is not None and local[i].hour == 8 and local[i].minute == 0
                  and local[i].weekday() < 5 and t - last_stock_time > WEEKEND
                  and not np.isnan(btc_at_close) and not np.isnan(btc_vals[i])):
                move = btc_vals[i] / btc_at_close - 1
                holding = move > thr if follow else move < -thr
            position[i] = 1.0 if holding else 0.0
        return position


class WeekendGapSized(WeekendGap):
    """Refined weekend gap (from the M4 diagnosis): follow only, and size each stock by its
    own risk. The bitcoin miners (MARA, RIOT) swing 2-3x more than COIN/MSTR and caused most
    of the drawdown, so each position aims for `target_vol` daily volatility (from the past
    30 days, frozen at entry), capped at an equal share of the account."""
    name = "weekend_gap_sized"
    default_grid = {"threshold": [0.03, 0.06], "target_vol": [0.02, 0.04]}

    def __init__(self, symbols, threshold: float = 0.03, target_vol: float = 0.02):
        Strategy.__init__(self, symbols, threshold=threshold, target_vol=target_vol)

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        position = self._positions(data, self.params["threshold"], follow=True)
        held = pd.Series(position > 0, index=data.index)
        entry = held & ~held.shift(1, fill_value=False)
        out = {}
        for s in self.symbols:
            size, _ = self.size_series(data, s)
            at_entry = size.reindex(data.index).ffill().where(entry).ffill()  # frozen for each trade
            out[s] = (position * at_entry.fillna(0.0).to_numpy())
        return pd.DataFrame(out, index=data.index)

    def size_series(self, data: MarketData, symbol: str) -> tuple[pd.Series, pd.Series]:
        """Position size (fraction of equity) and daily volatility on each of the stock's bars.
        Shared with paper trading, so live sizes use exactly the backtested formula."""
        c = data.close[symbol].dropna()
        daily_vol = np.log(c).diff().rolling("30D").std() * np.sqrt(7)   # 7 regular bars a day
        daily_vol = daily_vol.where(has_history(c, pd.Timedelta(days=30)))
        size = (self.params["target_vol"] / daily_vol).clip(upper=1.0) / len(self.symbols)
        return size, daily_vol


class FundingContrarian(Strategy):
    """Perpetual futures funding as a crowd gauge: when funding turns unusually negative
    (z-score below -`z` against the past 30 days), shorts are crowded and squeezes are
    likely. Buy the coin spot and hold for `hold_hours`. Needs Hyperliquid funding data
    (from May 2023), so it needs shorter walk-forward windows than the default."""
    name = "funding_contrarian"
    needs_funding = True
    default_grid = {"z": [1.5, 2.5], "hold_hours": [24, 72]}

    def __init__(self, symbols, z: float = 2.0, hold_hours: int = 24):
        super().__init__(symbols, z=z, hold_hours=hold_hours)

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        if data.funding is None:
            raise ValueError("FundingContrarian needs data loaded with funding=True")
        w = 1.0 / len(self.symbols)
        out = {}
        for s in self.symbols:
            if s not in data.funding:
                out[s] = pd.Series(0.0, index=data.index)
                continue
            f = data.funding[s].dropna()
            if f.empty:
                out[s] = pd.Series(0.0, index=data.index)
                continue
            mean, std = f.rolling("30D").mean(), f.rolling("30D").std()
            ready = pd.Series(f.index - f.index[0] >= pd.Timedelta(days=30), index=f.index)
            entry = ((f - mean) / std < -self.params["z"]) & ready
            held = hold_after(entry, pd.Timedelta(hours=self.params["hold_hours"]))
            # only hold while the coin actually has spot bars to trade
            out[s] = held.reindex(data.index).ffill().fillna(False).astype(float) * w
        return pd.DataFrame(out).reindex(data.index)


CRYPTO_BASKET = ["BTC/USD", "ETH/USD", "SOL/USD", "LTC/USD", "DOGE/USD", "AVAX/USD", "LINK/USD", "UNI/USD", "AAVE/USD", "BCH/USD"]
CRYPTO_STOCKS = ["COIN", "MSTR", "MARA", "RIOT"]


class ComboSleeves(Strategy):
    """Three weak, unrelated signals as one portfolio, a third of the account each:
      - weekend gap (sized) on crypto stocks: trades Monday sessions after big BTC weekends
      - regime filter on SPY: in the stock market above its moving average
      - trend on the crypto basket: in coins with a positive recent return
    They trade different markets at different times, so their swings partly offset.
    The walk-forward picks each sleeve's setting per fold from a small grid."""
    name = "combo_sleeves"
    signal_symbols = ["BTC/USD"]
    default_grid = {"weekend_threshold": [0.03, 0.06], "regime_days": [100, 200], "momentum_days": [7, 30]}

    def __init__(self, symbols=None, weekend_threshold: float = 0.06, regime_days: int = 100, momentum_days: int = 7):
        symbols = list(symbols) if symbols else CRYPTO_STOCKS + ["SPY"] + CRYPTO_BASKET
        super().__init__(symbols, weekend_threshold=weekend_threshold, regime_days=regime_days,
                         momentum_days=momentum_days)

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        from trader.strategies.baselines import RegimeFilter, TsMomentum

        third = 1.0 / 3.0
        sleeves = [
            WeekendGapSized(CRYPTO_STOCKS, threshold=self.params["weekend_threshold"], target_vol=0.02),
            RegimeFilter(["SPY"], sma_days=self.params["regime_days"]),
            TsMomentum(CRYPTO_BASKET, lookback_days=self.params["momentum_days"]),
        ]
        total = pd.DataFrame(0.0, index=data.index, columns=self.symbols)
        for sleeve in sleeves:
            w = sleeve.target_weights(data).reindex(data.index).ffill().fillna(0.0)
            total[w.columns] += third * w
        return total
