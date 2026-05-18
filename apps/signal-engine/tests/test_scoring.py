"""Unit tests for confluence scoring and half-Kelly position sizing.

Tests pure functions from signal_agent and order_agent — no DB, no LLM, no network.
"""

import pytest

from src.pipeline.agents.order_agent import (
    MAX_KELLY_CAP,
    REWARD_TO_RISK,
    _calc_position,
    _half_kelly,
)
from src.pipeline.agents.signal_agent import (
    _BASE_MAX,
    _TOTAL_MAX,
    _news_catalyst_score,
    _score_base,
)


# ─── _half_kelly ──────────────────────────────────────────────────────────────


class TestHalfKelly:
    def test_zero_confidence_uses_fallback_win_prob(self) -> None:
        # confidence < 60 falls back to p=0.50 → half-Kelly = 0.125 (not zero)
        assert abs(_half_kelly(0.0) - 0.125) < 1e-9

    def test_full_confidence_below_cap(self) -> None:
        # confidence=100 → p=0.60, half-Kelly = (1.2-0.4)/4 = 0.2 < MAX_KELLY_CAP
        frac = _half_kelly(100.0)
        assert 0.0 < frac < MAX_KELLY_CAP

    def test_fifty_percent_confidence(self) -> None:
        # p=0.5, q=0.5, r=2 → full_kelly=(0.5*2-0.5)/2=0.25 → half=0.125
        assert abs(_half_kelly(50.0) - 0.125) < 1e-9

    def test_all_bands_positive_kelly(self) -> None:
        # Every calibrated band has p > 1/3 (breakeven for r=2), so Kelly is always > 0
        for conf in (0.0, 60.0, 65.0, 70.0, 75.0, 80.0, 100.0):
            assert _half_kelly(conf) > 0.0, f"Expected > 0 at confidence={conf}"

    def test_high_confidence_positive_fraction(self) -> None:
        frac = _half_kelly(80.0)
        assert 0.0 < frac <= MAX_KELLY_CAP

    def test_fraction_increases_with_confidence_above_65(self) -> None:
        # From 65 upward the win-prob table is monotonically increasing
        f65 = _half_kelly(65.0)
        f70 = _half_kelly(70.0)
        f80 = _half_kelly(80.0)
        assert f65 < f70 < f80


# ─── _calc_position ───────────────────────────────────────────────────────────


class TestCalcPosition:
    def test_normal_case_returns_shares(self) -> None:
        shares, value, kf = _calc_position(
            confidence=70.0,
            portfolio_value=100_000.0,
            entry_price=500.0,
            position_size_pct=12.0,
            available_cash=100_000.0,
        )
        assert shares >= 1
        assert value == shares * 500.0
        assert 0.0 < kf <= MAX_KELLY_CAP

    def test_zero_entry_price_returns_zero(self) -> None:
        shares, value, _ = _calc_position(70.0, 100_000.0, 0.0, 12.0, 100_000.0)
        assert shares == 0
        assert value == 0.0

    def test_zero_portfolio_value_returns_zero(self) -> None:
        shares, _, _ = _calc_position(70.0, 0.0, 500.0, 12.0, 0.0)
        assert shares == 0

    def test_insufficient_cash_returns_zero(self) -> None:
        # entry_price=5000, available_cash=100 → can't buy 1 share
        shares, value, _ = _calc_position(70.0, 100_000.0, 5_000.0, 12.0, 100.0)
        assert shares == 0
        assert value == 0.0

    def test_respects_position_size_pct_cap(self) -> None:
        # 5% of 100k = 5000 max. With confidence=95 and r=2, Kelly is very high.
        _, value, _ = _calc_position(
            confidence=95.0,
            portfolio_value=100_000.0,
            entry_price=100.0,
            position_size_pct=5.0,
            available_cash=100_000.0,
        )
        assert value <= 5_000.0 + 100.0  # within cap + 1 share rounding

    def test_position_value_does_not_exceed_available_cash(self) -> None:
        _, value, _ = _calc_position(
            confidence=80.0,
            portfolio_value=100_000.0,
            entry_price=100.0,
            position_size_pct=12.0,
            available_cash=300.0,
        )
        assert value <= 300.0


# ─── _score_base ──────────────────────────────────────────────────────────────


def _md(nifty: float = -0.2, fii: float = -100.0) -> dict:
    return {"nifty_change_pct": nifty, "fii_net_crore": fii, "vix": 14.0}


def _td(**kwargs) -> dict:
    base = {
        "rsi": 50.0,
        "macd_hist": 0.0,
        "above_ema20": False,
        "above_ema50": False,
        "volume_ratio": 1.0,
        "close": 1000.0,
        "ema20": 1010.0,
        "ema50": 1020.0,
        "change_pct_today": 0.0,
    }
    base.update(kwargs)
    return base


class TestScoreBase:
    def test_zero_when_no_signals_fired(self) -> None:
        score, triggered, *_ = _score_base("RELIANCE.NS", _td(), _md(), [], [])
        assert score == 0
        assert triggered == []

    def test_rsi_below_40_adds_12(self) -> None:
        score, triggered, *_ = _score_base("RELIANCE.NS", _td(rsi=39.9), _md(), [], [])
        assert score == 12
        assert any("RSI" in t for t in triggered)

    def test_rsi_exactly_40_not_triggered(self) -> None:
        # condition is strict: rsi < 40
        score, *_ = _score_base("RELIANCE.NS", _td(rsi=40.0), _md(), [], [])
        assert score == 0

    def test_macd_positive_adds_12(self) -> None:
        score, triggered, *_ = _score_base("RELIANCE.NS", _td(macd_hist=0.001), _md(), [], [])
        assert score == 12
        assert any("MACD" in t for t in triggered)

    def test_macd_zero_not_triggered(self) -> None:
        score, *_ = _score_base("RELIANCE.NS", _td(macd_hist=0.0), _md(), [], [])
        assert score == 0

    def test_above_ema20_adds_10(self) -> None:
        score, *_ = _score_base("RELIANCE.NS", _td(above_ema20=True), _md(), [], [])
        assert score == 10

    def test_above_ema50_adds_12(self) -> None:
        score, *_ = _score_base("RELIANCE.NS", _td(above_ema50=True), _md(), [], [])
        assert score == 12

    def test_volume_above_1_5x_adds_10(self) -> None:
        score, triggered, *_ = _score_base("RELIANCE.NS", _td(volume_ratio=1.51), _md(), [], [])
        assert score == 10
        assert any("Volume" in t for t in triggered)

    def test_volume_exactly_1_5x_not_triggered(self) -> None:
        # condition: volume_ratio > 1.5 (strict)
        score, *_ = _score_base("RELIANCE.NS", _td(volume_ratio=1.5), _md(), [], [])
        assert score == 0

    def test_market_nifty_up_and_fii_buy_adds_8(self) -> None:
        score, triggered, *_ = _score_base("RELIANCE.NS", _td(), _md(nifty=0.5, fii=500.0), [], [])
        assert score == 8
        assert any("FII" in t for t in triggered)

    def test_market_nifty_up_fii_sell_adds_4(self) -> None:
        score, *_ = _score_base("RELIANCE.NS", _td(), _md(nifty=0.5, fii=-200.0), [], [])
        assert score == 4

    def test_market_nifty_down_adds_nothing(self) -> None:
        score, *_ = _score_base("RELIANCE.NS", _td(), _md(nifty=-0.5, fii=500.0), [], [])
        assert score == 0

    def test_all_base_signals_except_news_and_sector(self) -> None:
        # rsi(12) + macd(12) + ema20(10) + ema50(12) + vol(10) + market(8) = 64
        td = _td(rsi=35.0, macd_hist=0.05, above_ema20=True, above_ema50=True, volume_ratio=2.0)
        score, *_ = _score_base("RELIANCE.NS", td, _md(nifty=0.5, fii=500.0), [], [])
        assert score == 64

    def test_score_bounded_by_base_max(self) -> None:
        td = _td(rsi=35.0, macd_hist=0.05, above_ema20=True, above_ema50=True, volume_ratio=2.0)
        news = [{"sentiment": "positive", "affected_stocks": ["RELIANCE"], "affected_sectors": [], "headline": "x"}]
        score, *_ = _score_base("RELIANCE.NS", td, _md(nifty=0.5, fii=500.0), [], news)
        assert score <= _BASE_MAX  # 90 max without LLM bonus

    def test_total_confidence_bounded_by_total_max(self) -> None:
        assert _TOTAL_MAX == 100


# ─── _news_catalyst_score ─────────────────────────────────────────────────────


class TestHasPositiveNews:
    def _pos_news(self, stocks: list, sectors: list, headline: str = "test") -> list:
        return [{"sentiment": "positive", "affected_stocks": stocks, "affected_sectors": sectors, "headline": headline}]

    def _has_news(self, symbol: str, news: list) -> bool:
        pts, _, _ = _news_catalyst_score(symbol, news)
        return pts > 0

    def test_direct_stock_mention_returns_true(self) -> None:
        news = self._pos_news(["RELIANCE"], [])
        assert self._has_news("RELIANCE.NS", news) is True

    def test_bo_suffix_stripped_correctly(self) -> None:
        news = self._pos_news(["RELIANCE"], [])
        assert self._has_news("RELIANCE.BO", news) is True

    def test_symbol_in_headline_returns_true(self) -> None:
        news = [{"sentiment": "positive", "affected_stocks": [], "affected_sectors": [], "headline": "INFY announces buyback"}]
        assert self._has_news("INFY.NS", news) is True

    def test_negative_sentiment_ignored(self) -> None:
        news = [{"sentiment": "negative", "affected_stocks": ["RELIANCE"], "affected_sectors": [], "headline": "x"}]
        assert self._has_news("RELIANCE.NS", news) is False

    def test_neutral_sentiment_ignored(self) -> None:
        news = [{"sentiment": "neutral", "affected_stocks": ["RELIANCE"], "affected_sectors": [], "headline": "x"}]
        assert self._has_news("RELIANCE.NS", news) is False

    def test_unrelated_stock_returns_false(self) -> None:
        news = self._pos_news(["TCS"], [])
        assert self._has_news("RELIANCE.NS", news) is False

    def test_empty_news_returns_false(self) -> None:
        assert self._has_news("RELIANCE.NS", []) is False

    def test_case_insensitive_matching(self) -> None:
        news = self._pos_news(["reliance"], [])
        assert self._has_news("RELIANCE.NS", news) is True
