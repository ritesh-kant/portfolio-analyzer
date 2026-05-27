"""NSE market holiday calendar.

The holiday list is published by NSE India each year in December for the
following year. Update this file annually from:
  https://www.nseindia.com/resources/exchange-communication-holidays

Only equity-segment holidays are included. Weekends are already filtered by
_is_market_hours() in the handlers and are not listed here.
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
    date(2026, 10,  2),  # Mahatma Gandhi Jayanti
    date(2026, 11, 14),  # Diwali – Laxmi Puja (tentative; confirm when NSE publishes)
    date(2026, 12, 25),  # Christmas
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
