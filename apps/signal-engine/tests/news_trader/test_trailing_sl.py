"""Unit tests for trailing_sl.py — pure math, no I/O, no fixtures needed."""

import pytest

from src.news_trader.trailing_sl import (
    calc_costs,
    calc_pnl,
    calc_qty,
    check_exit,
    initial_stop,
    initial_trailing_sl,
    update_stop,
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


# ── split stop: initial_stop + update_stop ────────────────────────────────────

def test_initial_stop_is_wide():
    # 3% below entry, not the 1.5% trail width
    assert initial_stop(100.0, initial_sl_pct=0.03) == pytest.approx(97.0)


def test_split_stop_sits_at_wide_floor_before_activation():
    # Price ticks up but hasn't crossed +2% yet → stop stays at the wide 3% floor,
    # NOT trailing. This is the breathing room that the old pure-trail lacked.
    high, sl = update_stop(
        entry_price=100.0, current_price=101.0, highest_price=100.0,
        current_sl=97.0, initial_sl_pct=0.03, trail_sl_pct=0.015, trail_activate_pct=0.02,
    )
    assert high == 101.0
    assert sl == pytest.approx(97.0)  # unchanged — trailing not yet active


def test_split_stop_dip_before_activation_holds_wide_floor():
    # Entry 100, dips to 98.5 before ever going up. Old pure-trail would have a
    # stop at 98.5 and exit; split stop sits at 97.0 and survives.
    high, sl = update_stop(
        entry_price=100.0, current_price=98.5, highest_price=100.0,
        current_sl=97.0, initial_sl_pct=0.03, trail_sl_pct=0.015, trail_activate_pct=0.02,
    )
    assert high == 100.0
    assert sl == pytest.approx(97.0)  # still the wide floor — not stopped out


def test_split_stop_activates_and_locks_profit_at_threshold():
    # High reaches exactly +2% → trailing switches on, stop jumps to lock ~+0.5%.
    high, sl = update_stop(
        entry_price=100.0, current_price=102.0, highest_price=100.0,
        current_sl=97.0, initial_sl_pct=0.03, trail_sl_pct=0.015, trail_activate_pct=0.02,
    )
    assert high == 102.0
    assert sl == pytest.approx(102.0 * 0.985)  # 100.47 — now in profit


def test_split_stop_trails_up_once_active():
    high, sl = update_stop(
        entry_price=100.0, current_price=105.0, highest_price=102.0,
        current_sl=100.47, initial_sl_pct=0.03, trail_sl_pct=0.015, trail_activate_pct=0.02,
    )
    assert high == 105.0
    assert sl == pytest.approx(105.0 * 0.985)  # 103.425


def test_split_stop_never_decreases_on_dip_after_activation():
    high, sl = update_stop(
        entry_price=100.0, current_price=101.0, highest_price=105.0,
        current_sl=103.425, initial_sl_pct=0.03, trail_sl_pct=0.015, trail_activate_pct=0.02,
    )
    assert high == 105.0
    assert sl == pytest.approx(103.425)  # held


def test_split_stop_full_lifecycle():
    # buy 100 → wide floor 97; dip to 98 survives; run to 103 activates+trails;
    # pull back to 101.5 holds the trailed stop.
    entry = 100.0
    high = entry
    sl = initial_stop(entry, initial_sl_pct=0.03)
    assert sl == pytest.approx(97.0)

    for price in [99.0, 98.0, 100.5]:  # noise below +2% — floor holds
        high, sl = update_stop(entry, price, high, sl, 0.03, 0.015, 0.02)
        assert sl == pytest.approx(97.0)

    high, sl = update_stop(entry, 103.0, high, sl, 0.03, 0.015, 0.02)  # activates
    assert sl == pytest.approx(103.0 * 0.985)  # 101.455

    high, sl = update_stop(entry, 101.5, high, sl, 0.03, 0.015, 0.02)  # pullback
    assert high == 103.0
    assert sl == pytest.approx(103.0 * 0.985)  # unchanged


# ── check_exit ────────────────────────────────────────────────────────────────

def test_no_exit_when_price_between_sl_and_target():
    assert check_exit(102.0, 98.5, 108.0, held_sessions=1, max_hold_days=5) is None


def test_sl_hit_when_price_at_or_below_sl():
    assert check_exit(98.4, 98.5, 108.0, held_sessions=1, max_hold_days=5) == "sl_hit"


def test_sl_hit_exactly_at_sl():
    assert check_exit(98.5, 98.5, 108.0, held_sessions=1, max_hold_days=5) == "sl_hit"


def test_sl_hit_takes_priority_over_target():
    # Pathological: price simultaneously below SL and above target (won't happen
    # in practice but priority must hold)
    assert check_exit(98.0, 98.5, 97.0, held_sessions=1, max_hold_days=5) == "sl_hit"


def test_target_hit():
    assert check_exit(108.1, 98.5, 108.0, held_sessions=1, max_hold_days=5) == "target_hit"


def test_target_hit_exactly_at_target():
    assert check_exit(108.0, 98.5, 108.0, held_sessions=1, max_hold_days=5) == "target_hit"


def test_day5_exit_on_session_gte_max():
    assert check_exit(101.0, 98.5, 108.0, held_sessions=5, max_hold_days=5) == "day5"


def test_no_day5_on_session_4():
    assert check_exit(101.0, 98.5, 108.0, held_sessions=4, max_hold_days=5) is None


def test_day5_triggers_on_session_beyond_max():
    # Any sessions ≥ max_hold_days should trigger, not just exactly equal.
    assert check_exit(101.0, 98.5, 108.0, held_sessions=7, max_hold_days=5) == "day5"


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
