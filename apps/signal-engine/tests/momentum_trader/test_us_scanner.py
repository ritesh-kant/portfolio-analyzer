"""The US session loop: its own clock, its own calendar, and halts as a state."""

from __future__ import annotations

import pandas as pd
import pytest

from src.momentum_trader.market import US
from src.momentum_trader.us_scanner import (
    EARLY_CLOSE_EOD,
    HALT_COOLDOWN,
    HaltState,
    USScanner,
    session_times,
)
from src.momentum_trader.us_screener import USQuote
from src.momentum_trader.us_universe import USNameFacts

ET = "America/New_York"


def et(day: str, hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"{day} {hhmm}", tz=ET)


class FakeFeed:
    """Everything the loop needs, and nothing that reaches a network."""

    def __init__(self, quotes=None, facts=None, halted=None):
        self._quotes = quotes if quotes is not None else [
            USQuote("TIGHT", 5.0, 15.0, 8.0),
            USQuote("LOOSE", 5.0, 15.0, 8.0),
        ]
        self._facts = facts if facts is not None else {
            "TIGHT": USNameFacts("TIGHT", float_shares=4e6, exchange="NASDAQ"),
            "LOOSE": USNameFacts("LOOSE", float_shares=8e7, exchange="NASDAQ"),
        }
        self._halted = halted or set()

    def snapshot(self, now):
        return self._quotes

    def bars_1m(self, symbol, now):
        return pd.DataFrame()

    def facts(self, symbols):
        return self._facts

    def halted(self, now):
        return set(self._halted)


# ── the clock is the US one, not a translated NSE one ───────────────────────
def test_screens_during_the_us_session():
    result = USScanner(FakeFeed()).step(et("2026-09-17", "10:05"))
    assert result.session_open
    assert result.tradeable == ["TIGHT"]        # LOOSE fails the float criterion


def test_does_not_screen_before_the_open():
    """09:20 ET is inside the guide's stated 07:00 window but before the
    regular session, and `trades_premarket` is False for v1."""
    result = USScanner(FakeFeed()).step(et("2026-09-17", "09:20"))
    assert not result.session_open
    assert result.tradeable == []


def test_does_not_screen_on_a_us_holiday_that_is_an_nse_trading_day():
    thanksgiving = USScanner(FakeFeed()).step(et("2026-11-26", "10:05"))
    assert not thanksgiving.session_open


def test_entry_cutoff_blocks_new_entries_but_still_reports_the_name():
    """Blocked, with a reason — not silently dropped from the funnel."""
    result = USScanner(FakeFeed()).step(et("2026-09-17", "15:30"))
    assert result.tradeable == []
    assert result.blocked["TIGHT"] == "entry_cutoff"


# ── early closes ────────────────────────────────────────────────────────────
def test_early_close_shortens_the_session():
    cutoff, eod, sweep = session_times(pd.Timestamp("2026-11-27").date())
    assert eod == EARLY_CLOSE_EOD
    assert (cutoff.hour, cutoff.minute) == (12, 10)
    assert sweep == (12, 56)


def test_early_close_day_is_over_at_one_pm():
    """Holding to 16:00 on Black Friday marks the position at a price that
    never traded."""
    result = USScanner(FakeFeed()).step(et("2026-11-27", "13:30"))
    assert result.eod
    assert not result.session_open


def test_normal_day_keeps_the_full_session():
    cutoff, eod, sweep = session_times(pd.Timestamp("2026-09-17").date())
    assert (cutoff, eod, sweep) == (US.entry_cutoff, US.eod_close, US.eod_sweep)


# ── halts: a state, not an exclusion ────────────────────────────────────────
def test_a_halted_name_is_not_entered():
    feed = FakeFeed(halted={"TIGHT"})
    result = USScanner(feed).step(et("2026-09-17", "10:05"))
    assert result.tradeable == []
    assert result.blocked["TIGHT"] == "halted"


def test_no_entry_during_the_cooldown_after_a_resume():
    """The reopening auction is price discovery, not a breakout, and a resting
    buy-stop across it fills at whatever the auction clears at."""
    feed = FakeFeed(halted={"TIGHT"})
    scanner = USScanner(feed)
    scanner.step(et("2026-09-17", "10:05"))
    feed._halted = set()
    result = scanner.step(et("2026-09-17", "10:08"))
    assert result.blocked["TIGHT"] == "halt_cooldown"


def test_entry_allowed_once_the_cooldown_has_passed():
    feed = FakeFeed(halted={"TIGHT"})
    scanner = USScanner(feed)
    scanner.step(et("2026-09-17", "10:05"))
    feed._halted = set()
    scanner.step(et("2026-09-17", "10:06"))
    later = et("2026-09-17", "10:06") + HALT_COOLDOWN
    assert USScanner and scanner.step(later).tradeable == ["TIGHT"]


def test_a_position_open_when_its_symbol_halts_is_flagged():
    """Its stop was not protecting it while trading was paused, so its realised
    loss is not bounded by the plan. The forward log has to be able to exclude
    these rather than pool a risk the strategy never chose to take."""
    scanner = USScanner(FakeFeed(halted={"TIGHT"}))
    scanner.step(et("2026-09-17", "10:05"), open_symbols={"TIGHT"})
    assert "TIGHT" in scanner.halts.held_through_halt


def test_a_halt_with_no_position_is_not_flagged_as_held_through():
    scanner = USScanner(FakeFeed(halted={"TIGHT"}))
    scanner.step(et("2026-09-17", "10:05"), open_symbols=set())
    assert scanner.halts.held_through_halt == set()


def test_repeat_halts_are_counted():
    """Low-float runners halt repeatedly; once is noise, five times is the
    character of the name."""
    state = HaltState()
    now = et("2026-09-17", "10:00")
    for i in range(3):
        state.update(now, {"TIGHT"}, set())
        state.update(now + pd.Timedelta(minutes=i + 1), set(), set())
    assert state.halt_count["TIGHT"] == 3


# ── the stored document ─────────────────────────────────────────────────────
def test_watchlist_document_records_halts_and_the_short_session():
    scanner = USScanner(FakeFeed(halted={"LOOSE"}))
    scanner.step(et("2026-11-27", "12:00"))
    doc = scanner.watchlist_document(et("2026-11-27", "12:00"))
    assert doc["market"] == "US"
    assert doc["early_close"] is True
    assert doc["halted"] == ["LOOSE"]


def test_no_document_before_the_first_screen():
    assert USScanner(FakeFeed()).watchlist_document(et("2026-09-17", "09:00")) is None
