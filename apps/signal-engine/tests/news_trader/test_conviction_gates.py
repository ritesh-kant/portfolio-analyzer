"""Unit tests for trade-decision conviction & regime gates.

Pure function tests — no DB, no network, no yfinance. Each test isolates
one gate so a regression on rule X doesn't get masked by rule Y firing first.
"""

from handlers.trade_decision import (
    _MEDIUM_CONF_MIN_SOURCES,
    _NIFTY_CRASH_PCT,
    _VIX_ELEVATED,
    _VIX_EXTREME,
    _apply_conviction_gates,
)
from src.news_trader.nifty500 import NIFTY_500, is_liquid

_BASE_SIZE = 50_000.0


def _signal(**overrides):
    s = {
        "_id": "sig-xxx",
        "signal": "bullish",
        "confidence": "high",
        "magnitude": "major",   # default to major so existing gate tests don't hit the magnitude gate
        "source_count": 3,
        "stocks": ["HDFCBANK"],
    }
    s.update(overrides)
    return s


def _regime(**overrides):
    r = {
        "nifty_change_pct": 0.5,
        "nifty_above_ema50": True,
        "vix": 15.0,
    }
    r.update(overrides)
    return r


class TestSourceCountGate:
    def test_high_conf_single_source_allowed(self):
        allow, size, _ = _apply_conviction_gates(
            _signal(confidence="high", source_count=1), _regime(), _BASE_SIZE
        )
        assert allow is True
        assert size == _BASE_SIZE

    def test_medium_conf_single_source_blocked(self):
        allow, size, reason = _apply_conviction_gates(
            _signal(confidence="medium", source_count=1), _regime(), _BASE_SIZE
        )
        assert allow is False
        assert size == 0.0
        assert "medium-conf single-source" in reason

    def test_medium_conf_min_sources_passes(self):
        allow, _, _ = _apply_conviction_gates(
            _signal(confidence="medium", source_count=_MEDIUM_CONF_MIN_SOURCES),
            _regime(),
            _BASE_SIZE,
        )
        assert allow is True


class TestNiftyCrashGate:
    def test_bullish_blocked_when_nifty_crashing(self):
        allow, _, reason = _apply_conviction_gates(
            _signal(signal="bullish"),
            _regime(nifty_change_pct=_NIFTY_CRASH_PCT - 0.1),
            _BASE_SIZE,
        )
        assert allow is False
        assert "nifty crash" in reason

    def test_bullish_allowed_at_crash_boundary(self):
        # Threshold is strict less-than — exactly at -1.5% is allowed.
        allow, _, _ = _apply_conviction_gates(
            _signal(signal="bullish"),
            _regime(nifty_change_pct=_NIFTY_CRASH_PCT),
            _BASE_SIZE,
        )
        assert allow is True

    def test_missing_nifty_change_does_not_block(self):
        # Failed regime fetch → gate degrades to a no-op, not a block.
        allow, _, _ = _apply_conviction_gates(
            _signal(), _regime(nifty_change_pct=None), _BASE_SIZE
        )
        assert allow is True


class TestEma50Gate:
    def test_medium_conf_blocked_below_ema50(self):
        allow, _, reason = _apply_conviction_gates(
            _signal(confidence="medium", source_count=3),
            _regime(nifty_above_ema50=False),
            _BASE_SIZE,
        )
        assert allow is False
        assert "EMA50" in reason

    def test_high_conf_passes_below_ema50(self):
        # High-conviction signals bypass the EMA50 trend filter.
        allow, _, _ = _apply_conviction_gates(
            _signal(confidence="high"),
            _regime(nifty_above_ema50=False),
            _BASE_SIZE,
        )
        assert allow is True


class TestVixGate:
    def test_calm_vix_full_size(self):
        allow, size, _ = _apply_conviction_gates(
            _signal(), _regime(vix=14.0), _BASE_SIZE
        )
        assert allow is True
        assert size == _BASE_SIZE

    def test_elevated_vix_halves_size(self):
        allow, size, _ = _apply_conviction_gates(
            _signal(), _regime(vix=_VIX_ELEVATED + 1), _BASE_SIZE
        )
        assert allow is True
        assert size == _BASE_SIZE * 0.5

    def test_extreme_vix_blocks_entry(self):
        allow, size, reason = _apply_conviction_gates(
            _signal(), _regime(vix=_VIX_EXTREME + 1), _BASE_SIZE
        )
        assert allow is False
        assert size == 0.0
        assert "VIX extreme" in reason

    def test_missing_vix_full_size(self):
        # No data → no scaling. Failing-open is intentional.
        allow, size, _ = _apply_conviction_gates(
            _signal(), _regime(vix=None), _BASE_SIZE
        )
        assert allow is True
        assert size == _BASE_SIZE


class TestMagnitudeGate:
    def test_minor_blocked_regardless_of_confidence(self):
        for conf in ("high", "medium"):
            allow, size, reason = _apply_conviction_gates(
                _signal(confidence=conf, source_count=5, magnitude="minor"),
                _regime(),
                _BASE_SIZE,
            )
            assert allow is False, f"minor should be blocked for confidence={conf}"
            assert size == 0.0
            assert "minor magnitude" in reason

    def test_moderate_passes(self):
        allow, _, _ = _apply_conviction_gates(
            _signal(magnitude="moderate"), _regime(), _BASE_SIZE
        )
        assert allow is True

    def test_major_passes(self):
        allow, _, _ = _apply_conviction_gates(
            _signal(magnitude="major"), _regime(), _BASE_SIZE
        )
        assert allow is True

    def test_missing_magnitude_passes(self):
        # Graceful degradation: if LLM omits magnitude, don't block.
        allow, _, _ = _apply_conviction_gates(
            _signal(magnitude=None), _regime(), _BASE_SIZE
        )
        assert allow is True

    def test_magnitude_checked_before_regime(self):
        # minor should be rejected for magnitude reason, not VIX, even if
        # both would fail — ensures stable reject ordering for log readability.
        allow, _, reason = _apply_conviction_gates(
            _signal(magnitude="minor"), _regime(vix=99.0), _BASE_SIZE
        )
        assert allow is False
        assert "minor magnitude" in reason


class TestNifty500Universe:
    def test_known_nifty50_stocks_are_in_universe(self):
        for sym in ("RELIANCE", "HDFCBANK", "INFY", "TCS", "SBIN"):
            assert sym in NIFTY_500, f"{sym} missing from universe"

    def test_is_liquid_helper(self):
        assert is_liquid("RELIANCE") is True
        assert is_liquid("reliance") is True  # case-insensitive
        assert is_liquid("XYZGARBAGE") is False

    def test_universe_minimum_size(self):
        # Guard against accidental truncation of the universe file.
        assert len(NIFTY_500) >= 400, f"Universe too small: {len(NIFTY_500)} symbols"


class TestGateInteractions:
    def test_source_count_rejects_before_regime_evaluated(self):
        # Medium-conf single source should be rejected for sources reason,
        # not VIX, even when VIX would also block.
        allow, _, reason = _apply_conviction_gates(
            _signal(confidence="medium", source_count=1),
            _regime(vix=99.0),
            _BASE_SIZE,
        )
        assert allow is False
        assert "single-source" in reason

    def test_direction_rejected_before_other_gates(self):
        # A bearish signal must be rejected for direction, not for the regime
        # gates, even when the regime would also block — keeps reject ordering
        # stable for log readability.
        allow, _, reason = _apply_conviction_gates(
            _signal(signal="bearish", confidence="high"),
            _regime(nifty_change_pct=-3.0, nifty_above_ema50=False, vix=99.0),
            _BASE_SIZE,
        )
        assert allow is False
        assert "non-bullish" in reason


class TestDirectionGate:
    """Long-only system: only bullish signals may trade."""

    def test_bullish_allowed(self):
        allow, _, _ = _apply_conviction_gates(
            _signal(signal="bullish"), _regime(), _BASE_SIZE
        )
        assert allow is True

    def test_bearish_blocked(self):
        allow, size, reason = _apply_conviction_gates(
            _signal(signal="bearish", confidence="high", magnitude="major"),
            _regime(),
            _BASE_SIZE,
        )
        assert allow is False
        assert size == 0.0
        assert "non-bullish" in reason

    def test_neutral_blocked(self):
        allow, size, reason = _apply_conviction_gates(
            _signal(signal="neutral"), _regime(), _BASE_SIZE
        )
        assert allow is False
        assert size == 0.0
        assert "non-bullish" in reason

    def test_missing_direction_blocked(self):
        # No direction → can't confirm it's a long opportunity → skip.
        allow, _, reason = _apply_conviction_gates(
            _signal(signal=None), _regime(), _BASE_SIZE
        )
        assert allow is False
        assert "non-bullish" in reason
