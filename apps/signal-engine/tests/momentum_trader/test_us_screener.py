"""The screen funnel: what passed, what did not, and which criteria ran."""

from __future__ import annotations

from src.momentum_trader.us_screener import USQuote, screen, watchlist_doc
from src.momentum_trader.us_universe import USNameFacts, USUniverseConfig

FACTS = {
    "TIGHT": USNameFacts("TIGHT", float_shares=4_000_000, exchange="NASDAQ"),
    "LOOSE": USNameFacts("LOOSE", float_shares=80_000_000, exchange="NASDAQ"),
    "PINK": USNameFacts("PINK", float_shares=1_000_000, exchange="OTC"),
}


def q(symbol, price=5.0, chg=15.0, rvol=8.0, catalyst=None):
    return USQuote(symbol, price, chg, rvol, catalyst, observed_at="2026-09-17T14:05:00Z")


def test_a_qualifying_name_passes_all_five_when_data_is_present():
    s = screen([q("TIGHT", catalyst=True)], FACTS, USUniverseConfig(require_catalyst=True))
    assert s.passed == 1 and s.complete == 1
    assert s.missing_criteria == []


def test_float_rejection_short_circuits_before_intraday_work():
    s = screen([q("LOOSE")], FACTS)
    assert s.passed == 0
    assert s.rejected_by == {"float": 1}
    assert s.rows[0].flags.get("rvol_filter_applied") is None


def test_otc_excluded():
    s = screen([q("PINK")], FACTS)
    assert s.rejected_by == {"exchange:OTC": 1}


def test_funnel_counts_every_rejection_reason_separately():
    """The point of the funnel: 'nothing qualified' must decompose into which
    criterion did the rejecting."""
    s = screen(
        [
            q("TIGHT", price=25.0),        # outside $1-$20
            q("TIGHT2", chg=4.0),          # not up 10%
            q("TIGHT3", rvol=2.0),         # volume too thin
            q("LOOSE"),                    # float too large
        ],
        FACTS,
    )
    assert s.considered == 4 and s.passed == 0
    assert s.rejected_by == {"price": 1, "day_chg": 1, "rvol": 1, "float": 1}


def test_passing_row_with_no_float_data_is_not_a_complete_screen():
    """The NSE arm's exact situation: rows that passed a screen missing its
    supply criterion. They must not be counted as passing the guide's screen."""
    s = screen([q("UNKNOWN")], facts={})
    assert s.passed == 1
    assert s.complete == 0
    assert "5. float under 10M" in s.missing_criteria


def test_missing_criteria_names_every_filter_that_never_ran():
    s = screen([USQuote("UNKNOWN", 5.0, 15.0, rvol=None)], facts={})
    assert "5. float under 10M" in s.missing_criteria
    assert "1. 5x relative volume" in s.missing_criteria
    assert "3. news catalyst" in s.missing_criteria


def test_missing_criteria_empty_when_nothing_passed():
    """No passing rows means no claim to make about which screen ran."""
    s = screen([q("LOOSE")], FACTS)
    assert s.missing_criteria == []


def test_watchlist_doc_keeps_rejections_not_just_survivors():
    s = screen([q("TIGHT"), q("LOOSE")], FACTS)
    doc = watchlist_doc(s, "2026-09-17")
    assert doc["considered"] == 2 and doc["passed"] == 1
    assert len(doc["names"]) == 2
    assert {n["symbol"] for n in doc["names"]} == {"TIGHT", "LOOSE"}
    assert doc["rejected_by"] == {"float": 1}


def test_empty_snapshot_is_not_an_error():
    s = screen([], FACTS)
    assert s.considered == 0 and s.passed == 0 and s.missing_criteria == []
