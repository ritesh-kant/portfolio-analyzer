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


class TestPublisherCorroboration:
    """Gate 1 counts DISTINCT publishers, not raw articles. Two sections of one
    outlet (publishers length 1) must not clear the medium-conf floor even when
    source_count is high."""

    def test_medium_conf_blocked_when_single_publisher_many_articles(self):
        # 3 articles merged, but all from one outlet → publishers=["ET"] → blocked.
        allow, _, reason = _apply_conviction_gates(
            _signal(confidence="medium", source_count=3, publishers=["Economic Times"]),
            _regime(),
            _BASE_SIZE,
        )
        assert allow is False
        assert "single-source" in reason
        assert "publishers=1" in reason

    def test_medium_conf_passes_with_two_distinct_publishers(self):
        allow, _, _ = _apply_conviction_gates(
            _signal(confidence="medium", source_count=2,
                    publishers=["Economic Times", "Moneycontrol"]),
            _regime(),
            _BASE_SIZE,
        )
        assert allow is True

    def test_publishers_takes_precedence_over_source_count(self):
        # source_count says 5, but only 1 distinct publisher → still blocked.
        allow, _, _ = _apply_conviction_gates(
            _signal(confidence="medium", source_count=5, publishers=["Moneycontrol"]),
            _regime(),
            _BASE_SIZE,
        )
        assert allow is False

    def test_falls_back_to_source_count_when_publishers_absent(self):
        # Legacy signal with no publishers field → use source_count.
        allow, _, _ = _apply_conviction_gates(
            _signal(confidence="medium", source_count=2),
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
        # A bearish signal (shorts off) must be rejected for direction, not for
        # the regime gates, even when the regime would also block — keeps reject
        # ordering stable for log readability.
        allow, _, reason = _apply_conviction_gates(
            _signal(signal="bearish", confidence="high"),
            _regime(nifty_change_pct=-3.0, nifty_above_ema50=False, vix=99.0),
            _BASE_SIZE,
        )
        assert allow is False
        assert "shorts disabled" in reason


class TestDirectionGate:
    """Bullish trades long; bearish trades short only when allow_shorts is on."""

    def test_bullish_allowed(self):
        allow, _, _ = _apply_conviction_gates(
            _signal(signal="bullish"), _regime(), _BASE_SIZE
        )
        assert allow is True

    def test_bearish_blocked_when_shorts_disabled(self):
        # Default allow_shorts=False preserves the long-only behavior.
        allow, size, reason = _apply_conviction_gates(
            _signal(signal="bearish", confidence="high", magnitude="major"),
            _regime(),
            _BASE_SIZE,
        )
        assert allow is False
        assert size == 0.0
        assert "shorts disabled" in reason

    def test_neutral_blocked(self):
        allow, size, reason = _apply_conviction_gates(
            _signal(signal="neutral"), _regime(), _BASE_SIZE
        )
        assert allow is False
        assert size == 0.0
        assert "non-bullish" in reason

    def test_neutral_blocked_even_with_shorts_enabled(self):
        allow, _, reason = _apply_conviction_gates(
            _signal(signal="neutral"), _regime(), _BASE_SIZE, allow_shorts=True
        )
        assert allow is False
        assert "non-bullish" in reason

    def test_missing_direction_blocked(self):
        # No direction → can't confirm a side → skip.
        allow, _, reason = _apply_conviction_gates(
            _signal(signal=None), _regime(), _BASE_SIZE
        )
        assert allow is False
        assert "non-bullish" in reason


class TestShortGates:
    """Bearish signals trade as intraday shorts when allow_shorts=True.

    Regime gates mirror per side: the melt-up that helps a long blocks a
    short, and the falling tape that blocks a long is fine for a short.
    """

    def _short_regime(self, **overrides):
        # Neutral-to-weak tape: no melt-up, below EMA50 (favourable for shorts).
        r = {"nifty_change_pct": -0.5, "nifty_above_ema50": False, "vix": 15.0}
        r.update(overrides)
        return r

    def test_bearish_high_conf_allowed(self):
        allow, size, reason = _apply_conviction_gates(
            _signal(signal="bearish", confidence="high"),
            self._short_regime(),
            _BASE_SIZE,
            allow_shorts=True,
        )
        assert allow is True
        assert size == _BASE_SIZE
        assert reason == "ok"

    def test_short_blocked_on_melt_up(self):
        # Mirror of the crash gate: Nifty up > +1.5% intraday → no shorts.
        allow, _, reason = _apply_conviction_gates(
            _signal(signal="bearish", confidence="high"),
            self._short_regime(nifty_change_pct=2.0),
            _BASE_SIZE,
            allow_shorts=True,
        )
        assert allow is False
        assert "melt-up" in reason

    def test_short_allowed_in_crash(self):
        # The crash that blocks longs is a tailwind for shorts.
        allow, _, _ = _apply_conviction_gates(
            _signal(signal="bearish", confidence="high"),
            self._short_regime(nifty_change_pct=-3.0),
            _BASE_SIZE,
            allow_shorts=True,
        )
        assert allow is True

    def test_medium_conf_short_blocked_above_ema50(self):
        # Mirror of the EMA50 gate: shorting an uptrend needs high conviction.
        allow, _, reason = _apply_conviction_gates(
            _signal(signal="bearish", confidence="medium", source_count=3),
            self._short_regime(nifty_above_ema50=True),
            _BASE_SIZE,
            allow_shorts=True,
        )
        assert allow is False
        assert "above EMA50" in reason

    def test_short_vix_gates_apply(self):
        # Vol gates are side-agnostic: extreme blocks, elevated halves.
        allow, size, _ = _apply_conviction_gates(
            _signal(signal="bearish", confidence="high"),
            self._short_regime(vix=_VIX_ELEVATED + 1),
            _BASE_SIZE,
            allow_shorts=True,
        )
        assert allow is True
        assert size == _BASE_SIZE * 0.5

        allow, _, reason = _apply_conviction_gates(
            _signal(signal="bearish", confidence="high"),
            self._short_regime(vix=_VIX_EXTREME + 1),
            _BASE_SIZE,
            allow_shorts=True,
        )
        assert allow is False
        assert "VIX extreme" in reason

    def test_short_minor_magnitude_blocked(self):
        allow, _, reason = _apply_conviction_gates(
            _signal(signal="bearish", confidence="high", magnitude="minor"),
            self._short_regime(),
            _BASE_SIZE,
            allow_shorts=True,
        )
        assert allow is False
        assert "minor magnitude" in reason
