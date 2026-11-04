"""Tests for the gamma-exposure (GEX) level calculator."""

import math
from datetime import datetime, timedelta, timezone

import pytest
from src.options_trader.gex import (
    OptionRow,
    bs_gamma,
    compute_levels,
    expected_move,
    gamma_flip,
    net_gex,
)
from src.options_trader.pricing import greeks

_IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 4, 10, 0, tzinfo=_IST)
EXPIRY = datetime(2026, 11, 10, 15, 30, tzinfo=_IST)


def _row(strike: float, kind: str, oi: float = 1_000_000, iv: float = 0.13,
         expiry: datetime = EXPIRY) -> OptionRow:
    return OptionRow(expiry=expiry, strike=strike, kind=kind, oi=oi, iv=iv)  # type: ignore[arg-type]


# ── gamma ────────────────────────────────────────────────────────────────────

def test_gamma_is_the_derivative_of_delta():
    t, iv, k = 6 / 365, 0.13, 24000.0
    h = 0.5
    up = greeks(24000 + h, k, t, iv, "CE").delta
    dn = greeks(24000 - h, k, t, iv, "CE").delta
    assert bs_gamma(24000, k, t, iv) == pytest.approx((up - dn) / (2 * h), rel=1e-4)


def test_gamma_is_zero_for_degenerate_inputs():
    assert bs_gamma(24000, 24000, 0.0, 0.13) == 0.0
    assert bs_gamma(24000, 24000, 0.01, 0.0) == 0.0


# ── sign convention ──────────────────────────────────────────────────────────

def test_calls_positive_puts_negative_under_us_convention():
    levels = compute_levels([_row(24000, "CE"), _row(24000, "PE")], 24000, NOW)
    (s,) = levels.strikes
    assert s.call > 0 > s.put
    assert s.net == pytest.approx(0.0, abs=1e-6)   # same strike, OI and IV cancel


def test_short_all_convention_is_negative_and_has_no_flip():
    rows = [_row(24500, "CE"), _row(23500, "PE")]
    levels = compute_levels(rows, 24000, NOW, convention="dealer_short_all")
    assert levels.net_gex < 0
    assert levels.flip is None


def test_gex_units_are_rupees_per_one_percent_move():
    row = _row(24000, "CE", oi=65)
    t = (EXPIRY - NOW).total_seconds() / (365 * 24 * 3600)
    want = bs_gamma(24000, 24000, t, 0.13) * 65 * 24000**2 * 0.01
    assert net_gex([row], 24000, NOW, "dealer_long_calls") == pytest.approx(want)


# ── flip ─────────────────────────────────────────────────────────────────────

def test_flip_sits_between_put_and_call_concentrations():
    rows = [_row(24500, "CE"), _row(23500, "PE")]
    flip = gamma_flip(rows, 24000, NOW, "dealer_long_calls")
    assert flip is not None and 23500 < flip < 24500
    below = net_gex(rows, flip - 50, NOW, "dealer_long_calls")
    above = net_gex(rows, flip + 50, NOW, "dealer_long_calls")
    assert below < 0 < above


def test_no_flip_when_every_strike_has_the_same_sign():
    assert gamma_flip([_row(24000, "CE")], 24000, NOW, "dealer_long_calls") is None


# ── walls and range ──────────────────────────────────────────────────────────

def test_walls_rank_strikes_by_size():
    rows = [
        _row(24500, "CE", oi=3_000_000), _row(24300, "CE", oi=1_000_000),
        _row(23500, "PE", oi=2_000_000), _row(23800, "PE", oi=500_000),
    ]
    levels = compute_levels(rows, 24000, NOW, n_walls=2)
    assert levels.call_walls == [24500, 24300]
    assert set(levels.put_walls) == {23500, 23800}


def test_walls_stay_on_their_own_side_of_spot():
    rows = [_row(23900, "CE", oi=9_000_000), _row(24100, "CE"),
            _row(24100, "PE", oi=9_000_000), _row(23900, "PE")]
    levels = compute_levels(rows, 24000, NOW)
    assert levels.call_walls == [24100]
    assert levels.put_walls == [23900]


def test_expected_move_uses_trading_day_volatility():
    lo, hi = expected_move(24000, 0.1584)  # type: ignore[misc]
    move = 24000 * 0.1584 / math.sqrt(252)
    assert (lo, hi) == pytest.approx((24000 - move, 24000 + move))
    assert expected_move(24000, None) is None


def test_expired_and_empty_rows_are_ignored():
    rows = [
        _row(24000, "CE"),
        _row(24000, "PE", expiry=NOW - timedelta(days=1)),
        _row(24100, "PE", oi=0),
        _row(24200, "PE", iv=0),
    ]
    levels = compute_levels(rows, 24000, NOW)
    assert [s.strike for s in levels.strikes] == [24000]
    assert levels.put_walls == []


def test_gamma_wall_and_gex_levels_skip_resistance_and_support():
    rows = [
        _row(24500, "CE", oi=3_000_000), _row(24300, "CE", oi=1_000_000),
        _row(23500, "PE", oi=2_000_000), _row(23800, "PE", oi=500_000),
    ]
    levels = compute_levels(rows, 24000, NOW)
    ranked = sorted(levels.strikes, key=lambda s: abs(s.net), reverse=True)
    assert levels.gamma_wall == ranked[0].strike
    cr, ps = levels.call_walls[0], levels.put_walls[0]
    assert cr not in levels.gex_levels and ps not in levels.gex_levels
    assert levels.gex_levels == [s.strike for s in ranked if s.strike not in (cr, ps)]


def test_one_day_range_skips_a_same_day_expiry():
    today = NOW.replace(hour=15, minute=30)
    rows = [
        _row(24000, "CE", iv=0.30, expiry=today), _row(24000, "PE", iv=0.30, expiry=today),
        _row(24000, "CE", iv=0.15), _row(24000, "PE", iv=0.15),
    ]
    lo, hi = compute_levels(rows, 24000, NOW).expected_move  # type: ignore[misc]
    assert (hi - lo) / 2 == pytest.approx(24000 * 0.15 / math.sqrt(252))
