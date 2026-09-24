"""Proof that segregating the two markets did not change the Indian one.

Every NSE assertion here reads the constant out of the module that LIVE code
uses and compares it to the profile. If someone edits `market.NSE` to suit a US
need, or "generalises" a constant in `engine.py`, these fail. That is the whole
contract: the profile is a description of the running system, not a new
configuration for it.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from src.momentum_trader import bars, engine, market, scanner, setups
from src.momentum_trader.market import NSE, US


# ── the seam must match the live NSE constants ──────────────────────────────
def test_nse_session_clock_matches_the_scanner():
    """`scanner.SESSION_START` is the first BAR, and `scanner.SESSION_END` is
    when the process exits (15:35, five minutes after the 15:30 close) — the
    scanner's two names are not symmetric. The profile separates them, so the
    mapping is asserted rather than assumed."""
    assert (NSE.session_start.hour, NSE.session_start.minute) == scanner.SESSION_START
    assert NSE.process_end == scanner.SESSION_END
    assert NSE.eod_sweep == scanner.EOD_SWEEP


def test_nse_process_start_has_no_code_constant_to_match():
    """09:05 is set by the EventBridge cron in infrastructure/ecs-scanner.yml
    (`cron(35 3 ? * MON-FRI *)` = 03:35 UTC), not by any Python constant. It is
    recorded here so the US arm has somewhere to state its own wake time."""
    assert NSE.process_start == (9, 5)
    assert NSE.process_start < (NSE.session_start.hour, NSE.session_start.minute)
    assert US.process_start < (US.session_start.hour, US.session_start.minute)


def test_nse_entry_and_exit_times_match_the_engine():
    assert NSE.entry_cutoff == engine.ENTRY_CUTOFF
    assert NSE.eod_close == engine.EOD_CLOSE
    assert NSE.peak_hours_end == engine.PEAK_HOURS_END


def test_nse_session_open_matches_setups():
    assert NSE.session_start == setups.SESSION_OPEN


def test_nse_orb_window_matches_setups():
    assert NSE.orb_minutes == setups.ORB_MINUTES


def test_nse_timezone_matches_the_bar_builder():
    assert NSE.timezone == bars.IST


def test_nse_cost_function_is_the_live_indian_one():
    """Not an equivalent reimplementation — the same function the engine calls."""
    from src.news_trader.trailing_sl import calc_costs

    expected = calc_costs(100.0, 101.0, 500, direction="long")["total"]
    assert NSE.round_trip_cost(100.0, 101.0, 500) == pytest.approx(expected)


def test_nse_risk_planner_is_the_live_indian_one():
    from src.momentum_trader.risk import plan_trade

    assert NSE.plan_trade is plan_trade


def test_nse_collections_are_the_ones_already_in_use():
    assert NSE.positions_collection == "mt_positions"
    assert NSE.candidates_collection == "mt_candidates"


# ── the two profiles must actually differ where it matters ──────────────────
@pytest.mark.parametrize(
    "field",
    [
        "timezone", "currency_code", "tick_size", "halt_style",
        "entry_cutoff", "eod_close", "session_start", "session_end",
        "positions_collection", "candidates_collection", "watchlist_collection",
    ],
)
def test_every_conflicting_field_differs(field):
    """A field that is equal in both profiles is either genuinely shared or an
    un-migrated NSE default. These are the ones that must not be shared."""
    assert getattr(NSE, field) != getattr(US, field)


def test_cost_models_are_different_functions_not_a_currency_swap():
    """India bills a percentage of turnover, the US bills per share, so the
    same trade at the same NUMBER costs a different fraction in each."""
    inr = NSE.round_trip_cost(100.0, 100.0, 500) / (100.0 * 500)
    usd = US.round_trip_cost(100.0, 100.0, 500) / (100.0 * 500)
    assert inr != pytest.approx(usd)


def test_money_suffix_keeps_the_two_ledgers_unmixable():
    assert NSE.money_suffix == "inr"
    assert US.money_suffix == "usd"


def test_halt_styles_are_opposite_in_kind():
    """Not cosmetic: a circuit band is an EXCLUSION (no fill is possible), an
    LULD pause is a STATE (the position still exists and still carries risk)."""
    assert NSE.halt_style == "circuit_band"
    assert US.halt_style == "luld"


# ── US-specific values that have no NSE counterpart ─────────────────────────
def test_us_records_the_guides_premarket_window_but_does_not_trade_it():
    """The one constant NSE could not translate at all. Recorded literally,
    acted on only behind an explicit decision."""
    assert US.peak_hours_start == time(7, 0)
    assert US.trades_premarket is False
    assert NSE.peak_hours_start is None


def test_us_entry_cutoff_keeps_the_same_margin_before_the_close_as_nse():
    def minutes(t: time) -> int:
        return t.hour * 60 + t.minute

    assert minutes(NSE.eod_close) - minutes(NSE.entry_cutoff) == 44
    assert minutes(US.eod_close) - minutes(US.entry_cutoff) == 44


def test_us_sweep_is_after_its_bar_driven_close():
    assert US.eod_sweep > (US.eod_close.hour, US.eod_close.minute)
    assert NSE.eod_sweep > (NSE.eod_close.hour, NSE.eod_close.minute)


# ── calendars ───────────────────────────────────────────────────────────────
def test_profiles_carry_their_own_calendar():
    """Diwali is an NSE holiday and a normal US session; Thanksgiving is the
    reverse. One shared calendar could not express either."""
    thanksgiving_2026 = date(2026, 11, 26)
    assert US.is_trading_day(thanksgiving_2026) is False
    assert NSE.is_trading_day(thanksgiving_2026) is True


def test_lookup_rejects_an_unknown_market_rather_than_defaulting():
    """A typo must not quietly trade Indian rules on US names."""
    assert market.profile("us") is US
    assert market.profile("NSE") is NSE
    with pytest.raises(ValueError, match="unknown market"):
        market.profile("NYSE")
