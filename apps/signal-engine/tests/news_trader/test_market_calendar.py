"""Tests for NSE market holiday calendar."""

from datetime import date

from src.news_trader.market_calendar import is_trading_day


def test_saturday_is_not_trading_day():
    assert not is_trading_day(date(2025, 5, 3))  # Saturday


def test_sunday_is_not_trading_day():
    assert not is_trading_day(date(2025, 5, 4))  # Sunday


def test_regular_weekday_is_trading_day():
    assert is_trading_day(date(2025, 5, 5))  # Monday, no holiday


def test_independence_day_2025_is_not_trading_day():
    assert not is_trading_day(date(2025, 8, 15))  # Friday


def test_diwali_laxmi_puja_2025_is_not_trading_day():
    assert not is_trading_day(date(2025, 10, 24))  # Friday


def test_diwali_balipratipada_2025_is_not_trading_day():
    assert not is_trading_day(date(2025, 10, 27))  # Monday


def test_good_friday_2025_is_not_trading_day():
    assert not is_trading_day(date(2025, 4, 18))  # Friday


def test_republic_day_2026_is_not_trading_day():
    assert not is_trading_day(date(2026, 1, 26))  # Monday


def test_day_after_holiday_is_trading_day():
    # Day after Diwali Balipratipada (Oct 27) should be a trading day
    assert is_trading_day(date(2025, 10, 28))  # Tuesday


def test_weekend_is_still_blocked_on_holiday_bypass():
    # NT_BYPASS_MARKET_HOLIDAY must never make a Saturday a trading day.
    # is_trading_day itself doesn't take a bypass flag — the handlers apply
    # the bypass *after* the weekday check, so Saturday stays False regardless.
    assert not is_trading_day(date(2025, 10, 25))  # Saturday (day after Diwali)
