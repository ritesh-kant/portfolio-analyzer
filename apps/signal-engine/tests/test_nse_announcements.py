"""NSE announcement parsing against the payload shape NSE actually returns.

The fixture rows are copied from a live `api/corporate-announcements` response
(2026-09-24). Before this test existed every row parsed to `published_at=None`
— `sort_date` has a space, not a `T` — so `news_context` dropped all of them.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from src.momentum_trader.news_context import select_matches
from src.scrapers import nse

ABDL_ROWS = [
    {
        "symbol": "ABDL",
        "desc": "General Updates",
        "an_dt": "24-Sep-2026 17:44:16",
        "sort_date": "2026-09-24 17:44:16",
        "exchdisstime": "24-Sep-2026 17:44:17",
        "attchmntText": "Disclosure under Regulation 29(2) of the SEBI SAST Regulations",
        "attchmntFile": "https://nsearchives.nseindia.com/corporate/ABDINDIA_24092026174408_Reg_29_2__SAST.pdf",
        "sm_name": "Allied Blenders and Distillers Limited",
    },
]


def test_sort_date_parses_as_ist_and_returns_utc():
    [a] = nse._normalise(ABDL_ROWS)
    assert a["published_at"] == datetime(2026, 9, 24, 12, 14, 16, tzinfo=timezone.utc)


def test_an_dt_is_the_fallback_when_sort_date_is_missing():
    row = {k: v for k, v in ABDL_ROWS[0].items() if k != "sort_date"}
    [a] = nse._normalise([row])
    assert a["published_at"] == datetime(2026, 9, 24, 12, 14, 16, tzinfo=timezone.utc)


def test_filing_text_and_attachment_link_are_kept():
    [a] = nse._normalise(ABDL_ROWS)
    assert a["headline"] == "ABDL: General Updates"
    assert "Regulation 29(2)" in a["raw_text"]
    assert a["url"].endswith("_SAST.pdf")
    assert a["nse_symbol"] == "ABDL"


def test_parsed_filing_survives_the_news_context_window():
    # A trade entered 30 min after the filing must see it; one entered
    # 30 min before must not.
    [a] = nse._normalise(ABDL_ROWS)
    filed = a["published_at"]
    assert len(select_matches([a], "ABDL", filed + timedelta(minutes=30))) == 1
    assert select_matches([a], "ABDL", filed - timedelta(minutes=30)) == []


def test_symbol_fetch_asks_nse_for_that_symbol_over_ist_dates(monkeypatch):
    seen: dict[str, str] = {}

    async def fake_get(params):
        seen.update(params)
        return ABDL_ROWS

    monkeypatch.setattr(nse, "_get", fake_get)
    # 20:00 UTC on the 23rd is already the 24th in IST.
    start = datetime(2026, 9, 23, 20, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 24, 12, 30, tzinfo=timezone.utc)
    got = asyncio.run(nse.fetch_nse_symbol_announcements("abdl", start, end))
    assert seen["symbol"] == "ABDL"
    assert seen["from_date"] == "24-09-2026"
    assert seen["to_date"] == "24-09-2026"
    assert len(got) == 1


def test_news_context_text_is_the_filing_body_without_the_headline():
    [a] = nse._normalise(ABDL_ROWS)
    [m] = select_matches([a], "ABDL", a["published_at"] + timedelta(minutes=1))
    assert m["text"] == "Disclosure under Regulation 29(2) of the SEBI SAST Regulations"
