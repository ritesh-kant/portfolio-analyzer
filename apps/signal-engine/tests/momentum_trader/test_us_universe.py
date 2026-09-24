"""The five Warrior criteria, and the flags that say which ones actually ran."""

from __future__ import annotations

import pytest

from src.momentum_trader.us_universe import (
    USNameFacts,
    USUniverseConfig,
    passes_intraday,
    passes_static,
    screen_is_complete,
)

CFG = USUniverseConfig()


# ── criterion 5: supply ─────────────────────────────────────────────────────
def test_float_under_10m_passes():
    r = passes_static(USNameFacts("AAAA", float_shares=4_000_000, exchange="NASDAQ"), CFG)
    assert r.ok and r.flags["float_filter_applied"] == 1


def test_float_at_or_over_10m_rejected():
    r = passes_static(USNameFacts("BBBB", float_shares=10_000_000, exchange="NASDAQ"), CFG)
    assert not r.ok and r.reason == "float"


def test_missing_float_skips_the_filter_and_says_so():
    """The name survives, but the row records that criterion 5 never ran."""
    r = passes_static(USNameFacts("CCCC", float_shares=None, exchange="NYSE"), CFG)
    assert r.ok
    assert r.flags["float_filter_applied"] == 0


def test_otc_excluded():
    r = passes_static(USNameFacts("DDDD", float_shares=1_000_000, exchange="OTC"), CFG)
    assert not r.ok and r.reason.startswith("exchange")


# ── criterion 4: price band ─────────────────────────────────────────────────
@pytest.mark.parametrize("price,ok", [(0.99, False), (1.00, True), (20.00, True), (20.01, False)])
def test_price_band_edges(price, ok):
    assert passes_intraday(price, 15.0, 8.0, CFG).ok is ok


# ── criterion 2: up 10% ─────────────────────────────────────────────────────
@pytest.mark.parametrize("chg,ok", [(9.99, False), (10.0, True), (45.0, True)])
def test_day_change_floor_is_one_sided(chg, ok):
    """No upper bound: a stock up 45% is more interesting to this screen, not
    less. The NSE arm's +8% ceiling existed only because of circuit bands."""
    assert passes_intraday(5.0, chg, 8.0, CFG).ok is ok


# ── criterion 1: relative volume ────────────────────────────────────────────
def test_rvol_below_threshold_rejected():
    assert not passes_intraday(5.0, 15.0, 4.9, CFG).ok


def test_time_of_day_measure_uses_its_own_threshold():
    """3x time-of-day is the stated equivalent of 5x naive, so a 4.0 reading
    passes on the former and fails on the latter."""
    tod = USUniverseConfig(rvol_is_time_of_day=True)
    assert passes_intraday(5.0, 15.0, 4.0, tod).ok
    assert not passes_intraday(5.0, 15.0, 4.0, CFG).ok


def test_missing_rvol_skips_the_filter():
    r = passes_intraday(5.0, 15.0, None, CFG)
    assert r.ok and r.flags["rvol_filter_applied"] == 0


# ── criterion 3: catalyst ───────────────────────────────────────────────────
def test_catalyst_off_by_default_so_no_feed_is_not_a_blocker():
    assert passes_intraday(5.0, 15.0, 8.0, CFG, has_catalyst=None).ok


def test_catalyst_required_but_unknown_rejects_rather_than_assumes():
    cfg = USUniverseConfig(require_catalyst=True)
    r = passes_intraday(5.0, 15.0, 8.0, cfg, has_catalyst=None)
    assert not r.ok and r.reason == "catalyst_unavailable"


def test_catalyst_required_and_present():
    cfg = USUniverseConfig(require_catalyst=True)
    r = passes_intraday(5.0, 15.0, 8.0, cfg, has_catalyst=True)
    assert r.ok and r.flags["catalyst_filter_applied"] == 1


# ── the completeness flag ───────────────────────────────────────────────────
def test_screen_is_complete_only_when_all_five_ran():
    cfg = USUniverseConfig(require_catalyst=True)
    s = passes_static(USNameFacts("EEEE", float_shares=3e6, exchange="NASDAQ"), cfg)
    i = passes_intraday(5.0, 15.0, 8.0, cfg, has_catalyst=True)
    assert screen_is_complete(s, i)


def test_screen_incomplete_without_float():
    """This is the NSE arm's situation: a passing row that did not test supply."""
    s = passes_static(USNameFacts("FFFF", float_shares=None, exchange="NASDAQ"), CFG)
    i = passes_intraday(5.0, 15.0, 8.0, CFG)
    assert not screen_is_complete(s, i)
