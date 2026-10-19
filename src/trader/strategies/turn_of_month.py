"""Turn-of-the-month: own US stocks around the change of the month, sit in cash otherwise.

"Who is forced to trade" (idea from an Instagram reel): pension contributions, payroll savings
plans and month-end rebalancing put money into the market around the turn of each month.
Documented since Ariel (1987) and Lakonishok & Smidt (1988); McConnell & Xu (2008) found most of
the US market's excess return fell in the window from the last trading day to the third.
Design fixed on 2026-10-19, before any test:

  - hold SPY, QQQ and IWM (equal weight) from near the close of the `days_before`-th last trading
    day of the month to near the close of the `days_after`-th trading day of the next month
  - with hourly bars a decision on a bar fills at the next bar's open, so "near the close" is the
    decision on the 14:30 bar (filled at the 15:30 bar's open, half an hour before the close)
  - trading days come from the NYSE holiday calendar below, which is known in advance, never from
    future bars (unplanned closures such as a national day of mourning shift the window a day)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.tseries.holiday import (AbstractHolidayCalendar, GoodFriday, Holiday, USLaborDay, USMartinLutherKingJr,
                                    USMemorialDay, USPresidentsDay, USThanksgivingDay, nearest_workday, sunday_to_monday)

from trader.backtest.data import MarketData
from trader.strategies.base import Strategy

NY = "America/New_York"


class NYSEHolidays(AbstractHolidayCalendar):
    rules = [
        Holiday("New Year's Day", month=1, day=1, observance=sunday_to_monday),   # Saturday: no weekday closure
        USMartinLutherKingJr, USPresidentsDay, GoodFriday, USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01", observance=nearest_workday),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay, USThanksgivingDay,
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    ]


def trading_days(start, end) -> pd.DatetimeIndex:
    """NYSE trading dates (naive, midnight) between start and end, from the holiday calendar."""
    holidays = NYSEHolidays().holidays(pd.Timestamp(start) - pd.Timedelta(days=10), pd.Timestamp(end) + pd.Timedelta(days=10))
    return pd.bdate_range(start, end, freq="C", holidays=holidays)


def turn_of_month_windows(days: pd.DatetimeIndex, days_before: int, days_after: int) -> list[tuple]:
    """(entry date, exit date) per month change: entry = the days_before-th last trading day of a
    month, exit = the days_after-th trading day of the next month."""
    months = pd.Series(days, index=days).groupby(days.to_period("M"))
    groups = [g.index for _, g in months]
    out = []
    for this, nxt in zip(groups[:-1], groups[1:]):
        if len(this) >= days_before and len(nxt) >= days_after:
            out.append((this[-days_before], nxt[days_after - 1]))
    return out


class TurnOfMonth(Strategy):
    name = "turn_of_month"
    default_grid = {"days_before": [1, 2], "days_after": [2, 3]}

    def __init__(self, symbols, days_before: int = 2, days_after: int = 3):
        super().__init__(symbols, days_before=days_before, days_after=days_after)

    def target_weights(self, data: MarketData) -> pd.DataFrame:
        local = data.index.tz_convert(NY)
        dates = local.tz_localize(None).normalize()
        decision = (local.hour == 14) & (local.minute == 30)
        cal = trading_days(dates[0] - pd.Timedelta(days=40), dates[-1] + pd.Timedelta(days=40))
        w = np.full(len(local), np.nan)
        w[0] = 0.0
        for entry, exit_ in turn_of_month_windows(cal, self.params["days_before"], self.params["days_after"]):
            w[decision & (dates == entry)] = 1.0 / len(self.symbols)
            w[decision & (dates == exit_)] = 0.0
        col = pd.Series(w, index=data.index).ffill()
        return pd.DataFrame({s: col for s in self.symbols}, index=data.index)
