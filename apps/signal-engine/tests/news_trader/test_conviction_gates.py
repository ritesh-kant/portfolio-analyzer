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

_BASE_SIZE = 50_000.0


def _signal(**overrides):
    s = {
        "_id": "sig-xxx",
        "signal": "bullish",
        "confidence": "high",
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

    def test_bearish_signal_skips_nifty_direction_gates(self):
        # The Nifty-down and EMA50 gates only apply to bullish entries today
        # (since trade_decision opens longs regardless of direction, gating
        # bearish on a falling Nifty would be backwards).
        allow, _, _ = _apply_conviction_gates(
            _signal(signal="bearish", confidence="high"),
            _regime(nifty_change_pct=-3.0, nifty_above_ema50=False),
            _BASE_SIZE,
        )
        assert allow is True
