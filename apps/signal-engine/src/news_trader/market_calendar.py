"""NSE market holiday calendar.

The holiday list is published by NSE India each year in December for the
following year. Update this file annually from:
  https://www.nseindia.com/resources/exchange-communication-holidays

Only equity-segment holidays are included. Weekends are already filtered by
_is_market_hours() in the handlers and are not listed here.

⚠ **This list is hand-maintained and is known to be incomplete.** NSE observes
14–16 weekday holidays a year; the 2026 block below carries fewer. A missing
entry costs a wasted session — on 2026-09-14 (Ganesh Chaturthi, absent until
2026-09-15) the momentum scanner ran a full Fargate day against a closed market
and crashed on a half-written cache file. A *wrong* entry is worse: it skips a
real trading day silently. So only dates confirmed against NSE's own calendar
belong here, and callers must not treat `is_trading_day() is True` as proof the
market is open — `momentum_trader.scanner` additionally exits when no symbol has
printed a bar by its grace time, which catches every holiday this file misses.
"""

from datetime import date

# fmt: off
_NSE_HOLIDAYS: frozenset[date] = frozenset([
    # --- 2025 ---
    date(2025, 2, 19),   # Chhatrapati Shivaji Maharaj Jayanti
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Id-Ul-Fitr (Ramzan Eid)
    date(2025, 4, 10),   # Shri Mahavir Jayanti
    date(2025, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5,  1),   # Maharashtra Day
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 27),   # Ganesh Chaturthi
    date(2025, 10,  2),  # Mahatma Gandhi Jayanti
    date(2025, 10, 24),  # Diwali – Laxmi Puja (Muhurat trading in evening; regular session closed)
    date(2025, 10, 27),  # Diwali – Balipratipada
    date(2025, 11,  5),  # Gurunanak Jayanti
    date(2025, 12, 25),  # Christmas

    # --- 2026 ---
    date(2026, 1, 26),   # Republic Day
    date(2026, 3,  3),   # Holi
    date(2026, 3, 20),   # Id-Ul-Fitr (Ramzan Eid) — tentative; confirm nearer the date
    date(2026, 4,  3),   # Good Friday
    date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2026, 5,  1),   # Maharashtra Day
    date(2026, 8, 15),   # Independence Day (Saturday — listed for completeness)
    date(2026, 9, 14),   # Ganesh Chaturthi (Mon) — MISSING until 2026-09-15; cost a session
    date(2026, 10,  2),  # Mahatma Gandhi Jayanti (Fri)
    date(2026, 10, 20),  # Dussehra (Tue)
    date(2026, 11,  8),  # Diwali – Laxmi Pujan (Sun; Muhurat session only, regular closed)
    date(2026, 11, 10),  # Diwali – Balipratipada (Tue)
    date(2026, 12, 25),  # Christmas (Fri)
    # Still unconfirmed for 2026 and therefore NOT listed: Holi's second day,
    # Id-Ul-Fitr / Bakri Id / Muharram (lunar, announced late), Mahavir Jayanti,
    # Guru Nanak Jayanti. NSE notifies 14 weekday holidays for 2026 and this
    # block names fewer, so the scanner's no-data bailout is load-bearing.
])
# fmt: on


def is_trading_day(d: date | None = None) -> bool:
    """Return True if *d* is a weekday that is not an NSE market holiday.

    Pass a date to test a specific day; omit (or pass None) to test today.
    """
    if d is None:
        d = date.today()
    if d.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return d not in _NSE_HOLIDAYS
