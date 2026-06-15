"""Tests for the short-straddle cost model, exit clock, and slippage sourcing.

Focus: the day-1 failure modes documented in options_paper_trading_log —
  * slippage was a flat 1% guess that dominated P&L; it must now be the real
    crossed half-spread when a live quote exists, and a *configurable* fallback
    otherwise, with the source recorded for audit;
  * a 90-min time-stop force-closed every theta trade — it must be disable-able.
"""

from datetime import datetime, timedelta, timezone

import handlers.opt_position_monitor as monitor
from src.options_trader.paper_straddle import (
    build_entry_doc,
    calc_straddle_costs,
    check_exit,
    compute_close_update,
)

_UTC = timezone.utc


# ── Slippage: fallback rate path ─────────────────────────────────────────────

def test_fallback_slippage_matches_rate_and_is_labelled():
    # entry+exit turnover = (10 + 10) * 100 = 2000; 1% => 20.0
    costs = calc_straddle_costs(10.0, 10.0, lots=1, lot_size=100)
    assert costs["slippage"] == 20.0
    assert costs["slippage_source"] == "fallback_rate"


def test_fallback_rate_is_configurable():
    full = calc_straddle_costs(10.0, 10.0, 1, 100, fallback_slippage_rate=0.01)
    half = calc_straddle_costs(10.0, 10.0, 1, 100, fallback_slippage_rate=0.005)
    assert half["slippage"] == round(full["slippage"] / 2, 2)
    assert half["slippage_source"] == "fallback_rate"


# ── Slippage: real half-spread path ──────────────────────────────────────────

def test_real_half_spread_overrides_rate():
    # half_spread_points = 0.05 (both legs summed); crossed twice over the round
    # trip: 2 * 0.05 * (1*100) = 10.0 — independent of the premium level.
    costs = calc_straddle_costs(
        10.0, 10.0, lots=1, lot_size=100, half_spread_points=0.05
    )
    assert costs["slippage"] == 10.0
    assert costs["slippage_source"] == "nse_halfspread"


def test_real_half_spread_zero_is_valid():
    costs = calc_straddle_costs(10.0, 10.0, 1, 100, half_spread_points=0.0)
    assert costs["slippage"] == 0.0
    assert costs["slippage_source"] == "nse_halfspread"


def test_total_includes_slippage():
    costs = calc_straddle_costs(10.0, 10.0, 1, 100, half_spread_points=0.05)
    expected = round(
        costs["brokerage"] + costs["stt"] + costs["exchange"]
        + costs["sebi"] + costs["stamp"] + costs["gst"] + costs["slippage"],
        2,
    )
    assert costs["total"] == expected


# ── compute_close_update threads the slippage source through ─────────────────

def _open_pos(**over):
    pos = {
        "entry_total_prem": 10.0,
        "lots": 1,
        "lot_size": 100,
        "entry_at": datetime(2026, 6, 15, 4, 0, tzinfo=_UTC),
        "target_pct": 0.40,
        "stop_pct": 2.00,
        "max_hold_minutes_used": 0,
        "force_close_eod_used": True,
    }
    pos.update(over)
    return pos

def test_close_update_uses_real_half_spread():
    now = datetime(2026, 6, 15, 9, 0, tzinfo=_UTC)
    upd = compute_close_update(
        _open_pos(), 100.0, 5.0, 5.0, "eod_close", now, half_spread_points=0.05
    )
    assert upd["costs"]["slippage_source"] == "nse_halfspread"
    assert upd["costs"]["slippage"] == 10.0
    # gross = (10 - 10) * 100 = 0; net = -total
    assert upd["gross_pnl"] == 0.0
    assert upd["net_pnl"] == round(-upd["costs"]["total"], 2)


def test_close_update_falls_back_when_no_quote():
    now = datetime(2026, 6, 15, 9, 0, tzinfo=_UTC)
    upd = compute_close_update(
        _open_pos(), 100.0, 5.0, 5.0, "eod_close", now,
        half_spread_points=None, fallback_slippage_rate=0.005,
    )
    assert upd["costs"]["slippage_source"] == "fallback_rate"


# ── Exit clock: disable-able time-stop ───────────────────────────────────────

def test_time_stop_disabled_when_max_hold_zero():
    pos = _open_pos(max_hold_minutes_used=0)
    # 5 hours later, premium flat (no target/stop), well before 15:15 IST
    now = pos["entry_at"] + timedelta(hours=5)  # 09:00 UTC = 14:30 IST
    assert check_exit(pos, 10.0, now) is None


def test_time_stop_fires_when_enabled():
    pos = _open_pos(max_hold_minutes_used=90)
    now = pos["entry_at"] + timedelta(minutes=91)
    assert check_exit(pos, 10.0, now) == "time_stop"


def test_target_still_fires_with_time_stop_disabled():
    pos = _open_pos(max_hold_minutes_used=0)
    now = pos["entry_at"] + timedelta(hours=2)
    # premium decayed past 40% target (10 -> 5.9 <= 6.0)
    assert check_exit(pos, 5.9, now) == "target_hit"


# ── build_entry_doc records the IV source ────────────────────────────────────

def test_entry_doc_records_iv_source():
    sig = {"_id": "abc", "signal": "bullish"}
    now = datetime(2026, 6, 15, 4, 0, tzinfo=_UTC)
    exp = (now + timedelta(days=15)).date()
    doc = build_entry_doc(
        signal_doc=sig, symbol="IOC", spot=146.0, iv=0.30, lots=1, lot_size=4875,
        now=now, exp_date=exp, target_pct=0.4, stop_pct=2.0,
        max_hold_minutes=0, force_close_eod=True, iv_source="nse",
    )
    assert doc["entry_iv_source"] == "nse"
    assert doc["entry_iv"] == 0.30


def test_entry_doc_iv_source_defaults_to_baseline():
    sig = {"_id": "abc", "signal": "bullish"}
    now = datetime(2026, 6, 15, 4, 0, tzinfo=_UTC)
    exp = (now + timedelta(days=15)).date()
    doc = build_entry_doc(
        signal_doc=sig, symbol="IOC", spot=146.0, iv=0.30, lots=1, lot_size=4875,
        now=now, exp_date=exp, target_pct=0.4, stop_pct=2.0,
        max_hold_minutes=0, force_close_eod=True,
    )
    assert doc["entry_iv_source"] == "baseline"


# ── monitor._half_spread_points guards bad markets ───────────────────────────

def test_half_spread_points_valid():
    q = {"ce_bid": 0.80, "ce_ask": 1.00, "pe_bid": 0.90, "pe_ask": 1.00}
    assert monitor._half_spread_points(q) == 0.15


def test_half_spread_points_none_quote():
    assert monitor._half_spread_points(None) is None


def test_half_spread_points_crossed_market():
    # ask < bid (crossed) -> not a usable market
    q = {"ce_bid": 1.00, "ce_ask": 0.80, "pe_bid": 0.90, "pe_ask": 1.00}
    assert monitor._half_spread_points(q) is None


def test_half_spread_points_missing_field():
    assert monitor._half_spread_points({"ce_bid": 0.8, "ce_ask": 1.0}) is None
