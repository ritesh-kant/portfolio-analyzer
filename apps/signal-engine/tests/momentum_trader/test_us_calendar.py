"""US market calendar — computed, not hand-maintained, so it is worth testing."""

from __future__ import annotations

from datetime import date

import pytest

from src.momentum_trader import us_calendar as cal


def test_2026_full_closures_are_the_published_ten():
    assert sorted(cal.holidays(2026)) == [
        date(2026, 1, 1),    # New Year's Day
        date(2026, 1, 19),   # MLK Jr Day
        date(2026, 2, 16),   # Washington's Birthday
        date(2026, 4, 3),    # Good Friday
        date(2026, 5, 25),   # Memorial Day
        date(2026, 6, 19),   # Juneteenth
        date(2026, 7, 3),    # Independence Day observed (4th is a Saturday)
        date(2026, 9, 7),    # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas Day
    ]


@pytest.mark.parametrize(
    "year,good_friday",
    [(2024, date(2024, 3, 29)), (2025, date(2025, 4, 18)), (2026, date(2026, 4, 3))],
)
def test_good_friday_tracks_easter_across_years(year, good_friday):
    """The one US holiday that cannot be expressed as an n-th weekday."""
    assert good_friday in cal.holidays(year)


def test_saturday_holiday_observed_on_the_friday_before():
    assert cal.is_trading_day(date(2026, 7, 3)) is False
    assert cal.is_trading_day(date(2026, 7, 6)) is True


def test_sunday_holiday_observed_on_the_monday_after():
    """2022-12-25 was a Sunday; the market closed Monday the 26th."""
    assert date(2022, 12, 26) in cal.holidays(2022)


def test_juneteenth_is_not_a_holiday_before_2022():
    assert cal.is_trading_day(date(2021, 6, 18)) is True
    assert cal.is_trading_day(date(2022, 6, 20)) is False


def test_weekends_are_not_trading_days():
    assert cal.is_trading_day(date(2026, 9, 19)) is False   # Saturday
    assert cal.is_trading_day(date(2026, 9, 20)) is False   # Sunday


def test_early_closes_are_reported_separately_from_closures():
    """Holding to 16:00 on one of these marks the position at a price that
    never traded, so they are a distinct question from 'is the market open'."""
    assert cal.is_early_close(date(2026, 11, 27)) is True   # day after Thanksgiving
    assert cal.is_trading_day(date(2026, 11, 27)) is True
    assert cal.is_early_close(date(2026, 12, 24)) is True


def test_a_full_closure_is_never_also_an_early_close():
    assert cal.is_early_close(date(2026, 7, 3)) is False    # it is shut, not short
    assert cal.is_early_close(date(2026, 11, 26)) is False


def test_an_ordinary_session_is_neither():
    assert cal.is_trading_day(date(2026, 9, 17)) is True
    assert cal.is_early_close(date(2026, 9, 17)) is False
