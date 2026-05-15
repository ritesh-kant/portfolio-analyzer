"""Shared pytest fixtures for the signal-engine test suite."""

import pytest


@pytest.fixture
def market_data_positive() -> dict:
    return {"nifty_change_pct": 0.8, "fii_net_crore": 1200.0, "vix": 13.5}


@pytest.fixture
def market_data_bearish() -> dict:
    return {"nifty_change_pct": -0.5, "fii_net_crore": -800.0, "vix": 18.0}


@pytest.fixture
def clean_technical_data() -> dict:
    """Neutral technical data — no signals triggered."""
    return {
        "rsi": 50.0,
        "macd_hist": 0.0,
        "above_ema20": False,
        "above_ema50": False,
        "volume_ratio": 1.0,
        "close": 1000.0,
        "ema20": 1010.0,
        "ema50": 1020.0,
        "change_pct_today": 0.5,
    }


@pytest.fixture
def bullish_technical_data() -> dict:
    """All non-LLM bullish signals triggered."""
    return {
        "rsi": 35.0,
        "macd_hist": 0.05,
        "above_ema20": True,
        "above_ema50": True,
        "volume_ratio": 2.5,
        "close": 2500.0,
        "ema20": 2480.0,
        "ema50": 2450.0,
        "change_pct_today": 1.2,
    }
