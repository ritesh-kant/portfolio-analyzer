"""Unit tests for _trading_days_held in sl_monitor.

Tests the calendar-day → trading-session conversion that gates the day5
force-close. The original code used calendar days, which caused inconsistent
hold durations depending on which weekday the trade was entered.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from handlers.sl_monitor import _trading_days_held


def _dt(days_ago: int) -> datetime:
    return datetime.now(tz=timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)


class TestTradingDaysHeld:

    def test_entry_today_is_zero(self):
        assert _trading_days_held(_dt(0)) == 0

    def test_entry_future_is_zero(self):
        entry = datetime.now(tz=timezone.utc) + timedelta(days=1)
        assert _trading_days_held(entry) == 0

    def test_one_weekday_ago(self):
        # Use a Monday (weekday=0) as "today" and entry Tuesday the week before.
        # Mock is_trading_day to always return True so we only test weekday logic.
        with patch("handlers.sl_monitor.is_trading_day", return_value=True):
            # 1 calendar day, both weekdays → 1 trading session
            entry = _dt(1)
            # Skip if today is Monday (the "1 day ago" would be Sunday, a weekend)
            if entry.weekday() < 5:
                result = _trading_days_held(entry)
                assert result == 1

    def test_weekend_does_not_count(self):
        # Force a scenario: entry was last Friday (5 calendar days = Mon now).
        # With a real Monday "today" and Friday entry: 1 trading day elapsed.
        # Easier to test via mock: mark Saturday and Sunday as not is_trading_day,
        # weekday filter already handles them.
        with patch("handlers.sl_monitor.is_trading_day", return_value=True):
            # 7 calendar days crossing one full weekend → 5 trading sessions
            # (Mon Tue Wed Thu Fri Sat Sun → Mon is +7 days)
            # We can't guarantee which day "today" is, so set a fixed reference.
            now = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)  # Monday
            entry = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)  # prev Monday

            with patch("handlers.sl_monitor.datetime") as mock_dt:
                mock_dt.now.return_value = now
                mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
                result = _trading_days_held(entry)

            # Mon 18 → Mon 25: Tue 19, Wed 20, Thu 21, Fri 22, Mon 25 = 5 trading days
            # (Sat 23, Sun 24 skipped by weekday filter)
            assert result == 5

    def test_holiday_not_counted(self):
        # Entry 3 calendar days ago; the day in between is a holiday.
        # Only 1 trading day should be counted (not 2).
        now = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)   # Wednesday
        entry = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc) # Monday

        holiday = datetime(2026, 5, 19, tzinfo=timezone.utc).date()  # Tuesday

        def _is_trading(d):
            return d != holiday

        with patch("handlers.sl_monitor.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            with patch("handlers.sl_monitor.is_trading_day", side_effect=_is_trading):
                result = _trading_days_held(entry)

        # Mon entry → Tue (holiday, skipped) → Wed = 1 trading day
        assert result == 1

    def test_consistent_across_entry_days(self):
        """A Monday entry and a Wednesday entry, both checked 5 trading sessions
        later, should both trigger day5 — regardless of calendar days elapsed."""
        with patch("handlers.sl_monitor.is_trading_day", return_value=True):
            # Monday entry, check the following Monday (7 calendar days, 5 trading)
            mon_entry = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
            mon_check = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)

            # Wednesday entry, check the following Wednesday (7 calendar days, 5 trading)
            wed_entry = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
            wed_check = datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc)

            with patch("handlers.sl_monitor.datetime") as mock_dt:
                mock_dt.now.return_value = mon_check
                mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
                mon_sessions = _trading_days_held(mon_entry)

            with patch("handlers.sl_monitor.datetime") as mock_dt:
                mock_dt.now.return_value = wed_check
                mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
                wed_sessions = _trading_days_held(wed_entry)

            assert mon_sessions == wed_sessions == 5
