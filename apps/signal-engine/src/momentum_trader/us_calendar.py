"""NYSE / NASDAQ market holiday calendar.

Same shape and same warning as `news_trader.market_calendar`, and separate from
it for the same reason the universes are separate: the two exchanges share no
holidays at all, so one combined list would be a list of dates on which SOME
market is shut, which is not a question any caller asks.

US equity holidays are rule-based (third Monday in January, last Monday in May,
and so on) rather than lunar, so unlike the NSE list this one can be computed
and does not need hand-maintaining every December. Good Friday is the exception
— it tracks Easter — so it is computed with the anonymous Gregorian algorithm
rather than hard-coded.

⚠️ Two cases this file does NOT cover, matching the NSE module's honesty about
its own gaps:
  * **Early closes.** The day after Thanksgiving, Christmas Eve and 3 July close
    at 13:00 ET, not 16:00. `is_early_close` reports them; a caller that ignores
    it will hold a position past the close and mark it at a stale price.
  * **Unscheduled closures** (weather, national days of mourning). Nothing
    predicts these. As on NSE, the real backstop is the scanner's data-driven
    check: zero bars across the whole universe by the grace time means the
    market is shut, whatever this file claims.
"""

from __future__ import annotations

from datetime import date, timedelta


def _easter(year: int) -> date:
    """Anonymous Gregorian computus. Good Friday is two days earlier."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    wd = (32 + 2 * e + 2 * i - h - k) % 7          # the algorithm's `L`
    m = (a + 11 * h + 22 * wd) // 451
    month, day = divmod(h + wd - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """`n`-th `weekday` (Mon=0) of a month, e.g. third Monday in January."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Last `weekday` of a month, e.g. last Monday in May (Memorial Day)."""
    last = date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """A fixed-date holiday falling on a weekend is observed on the nearest
    weekday: Saturday -> Friday before, Sunday -> Monday after."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def holidays(year: int) -> frozenset[date]:
    """Full-closure NYSE/NASDAQ holidays for `year`."""
    return frozenset(
        {
            _observed(date(year, 1, 1)),               # New Year's Day
            _nth_weekday(year, 1, 0, 3),               # MLK Jr Day
            _nth_weekday(year, 2, 0, 3),               # Washington's Birthday
            _easter(year) - timedelta(days=2),         # Good Friday
            _last_weekday(year, 5, 0),                 # Memorial Day
            _observed(date(year, 6, 19)),              # Juneteenth (from 2022)
            _observed(date(year, 7, 4)),               # Independence Day
            _nth_weekday(year, 9, 0, 1),               # Labor Day
            _nth_weekday(year, 11, 3, 4),              # Thanksgiving
            _observed(date(year, 12, 25)),             # Christmas Day
        }
    )


def is_trading_day(d: date | None = None) -> bool:
    """True if `d` is a weekday on which the US equity market holds a full or
    shortened session. Mirrors `news_trader.market_calendar.is_trading_day`."""
    if d is None:
        d = date.today()
    if d.weekday() >= 5:
        return False
    if d.year < 2022 and d == _observed(date(d.year, 6, 19)):
        return True                                    # Juneteenth pre-dates 2022
    return d not in holidays(d.year)


def is_early_close(d: date | None = None) -> bool:
    """True on a 13:00 ET close. Callers must shift their EOD exit — holding to
    16:00 on one of these marks the position at a price that never traded."""
    if d is None:
        d = date.today()
    if not is_trading_day(d):
        return False
    thanksgiving = _nth_weekday(d.year, 11, 3, 4)
    candidates = {
        thanksgiving + timedelta(days=1),              # Black Friday
        date(d.year, 7, 3),                            # day before Independence Day
        date(d.year, 12, 24),                          # Christmas Eve
    }
    return d in candidates and d.weekday() < 5
