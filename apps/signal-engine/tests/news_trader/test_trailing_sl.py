"""Unit tests for trailing_sl.py — pure math, no I/O, no fixtures needed."""

from datetime import datetime, timedelta, timezone

import pytest

from src.news_trader.trailing_sl import (
    calc_costs,
    calc_pnl,
    calc_qty,
    check_exit,
    initial_trailing_sl,
    update_trailing_sl,
)


# ── initial SL ────────────────────────────────────────────────────────────────

def test_initial_sl_is_1pct5_below_entry():
    assert initial_trailing_sl(100.0, sl_pct=0.015) == pytest.approx(98.5)


# ── update_trailing_sl ────────────────────────────────────────────────────────

def test_sl_raises_when_price_makes_new_high():
    highest, sl = update_trailing_sl(
        current_price=105.0, highest_price=100.0, current_sl=98.5, sl_pct=0.015
    )
    assert highest == 105.0
    assert sl == pytest.approx(105.0 * 0.985)


def test_sl_never_decreases_on_price_dip():
    # Price drops back below previous high — SL must stay put
    highest, sl = update_trailing_sl(
        current_price=95.0, highest_price=105.0, current_sl=103.425, sl_pct=0.015
    )
    assert highest == 105.0          # highest unchanged
    assert sl == pytest.approx(103.425)  # SL unchanged


def test_sl_matches_plan_example():
    # Plan: buy ₹100, SL ₹98.50.  Price → ₹102: SL → ₹100.47 (≈100.50)
    _, sl = update_trailing_sl(
        current_price=102.0, highest_price=100.0, current_sl=98.5, sl_pct=0.015
    )
    assert sl == pytest.approx(102.0 * 0.985)  # 100.47


def test_sl_staircase_three_steps():
    # Replicate full plan example
    entry, sl_pct = 100.0, 0.015
    sl = initial_trailing_sl(entry, sl_pct)
    high = entry

    for price in [102.0, 104.0, 106.0]:
        high, sl = update_trailing_sl(price, high, sl, sl_pct)

    assert high == 106.0
    assert sl == pytest.approx(106.0 * 0.985)   # 104.41


# ── check_exit ────────────────────────────────────────────────────────────────

def _entry_at(days_ago: int = 0) -> datetime:
    return datetime.now(tz=timezone.utc) - timedelta(days=days_ago)


def test_no_exit_when_price_between_sl_and_target():
    result = check_exit(
        current_price=102.0,
        trailing_sl=98.5,
        target_price=108.0,
        entry_at=_entry_at(1),
        max_hold_days=5,
    )
    assert result is None


def test_sl_hit_when_price_at_or_below_sl():
    result = check_exit(
        current_price=98.4,
        trailing_sl=98.5,
        target_price=108.0,
        entry_at=_entry_at(1),
        max_hold_days=5,
    )
    assert result == "sl_hit"


def test_sl_hit_takes_priority_over_target():
    # Pathological: price simultaneously below SL and above target (won't happen
    # in practice but priority must hold)
    result = check_exit(
        current_price=98.0,
        trailing_sl=98.5,
        target_price=97.0,
        entry_at=_entry_at(1),
        max_hold_days=5,
    )
    assert result == "sl_hit"


def test_target_hit():
    result = check_exit(
        current_price=108.1,
        trailing_sl=98.5,
        target_price=108.0,
        entry_at=_entry_at(1),
        max_hold_days=5,
    )
    assert result == "target_hit"


def test_day5_exit_on_hold_day_gte_max():
    result = check_exit(
        current_price=101.0,
        trailing_sl=98.5,
        target_price=108.0,
        entry_at=_entry_at(5),
        max_hold_days=5,
    )
    assert result == "day5"


def test_no_day5_on_day_4():
    result = check_exit(
        current_price=101.0,
        trailing_sl=98.5,
        target_price=108.0,
        entry_at=_entry_at(4),
        max_hold_days=5,
    )
    assert result is None


# ── calc_costs ────────────────────────────────────────────────────────────────

def test_calc_costs_keys():
    costs = calc_costs(entry_price=100.0, exit_price=108.0, qty=500)
    for key in ("brokerage", "stt", "exchange", "stamp", "gst", "slippage", "total"):
        assert key in costs

def test_calc_costs_total_equals_sum():
    costs = calc_costs(100.0, 108.0, 500)
    components = costs["brokerage"] + costs["stt"] + costs["exchange"] + costs["stamp"] + costs["gst"] + costs["slippage"]
    assert costs["total"] == pytest.approx(components, abs=0.02)

def test_calc_costs_stt_dominates():
    # STT = 0.1% each side on ₹50k + ₹54k = ₹104; should be the biggest line
    costs = calc_costs(100.0, 108.0, 500)
    assert costs["stt"] > costs["brokerage"]
    assert costs["stt"] > costs["slippage"]


# ── calc_pnl ──────────────────────────────────────────────────────────────────

def test_profitable_trade_net_less_than_gross():
    gross, net, costs = calc_pnl(entry_price=100.0, exit_price=108.0, qty=500)
    assert gross == pytest.approx(4000.0)   # (108-100) * 500
    assert net < gross                      # costs deducted
    assert net == pytest.approx(gross - costs["total"])


def test_losing_trade_net_worse_than_gross():
    gross, net, _costs = calc_pnl(entry_price=100.0, exit_price=98.0, qty=500)
    assert gross == pytest.approx(-1000.0)
    assert net < gross   # costs make it even worse


def test_breakeven_requires_clearing_cost():
    # Entry = exit → gross = 0, net negative (cost not recovered)
    gross, net, _costs = calc_pnl(100.0, 100.0, 500)
    assert gross == 0.0
    assert net < 0.0


# ── calc_qty ──────────────────────────────────────────────────────────────────

def test_qty_floors_to_integer():
    # ₹50,000 / ₹333 = 150.15... → 150
    assert calc_qty(50_000.0, 333.0) == 150


def test_qty_minimum_is_1_for_expensive_stock():
    # ₹50,000 / ₹100,000 = 0.5 → clamped to 1
    assert calc_qty(50_000.0, 100_000.0) == 1


def test_qty_exact_division():
    assert calc_qty(50_000.0, 500.0) == 100
