"""EDGAR annotation: pure filtering only, no network (same convention as test_news_context.py)."""

from __future__ import annotations

from datetime import datetime, timezone

from src.momentum_trader.us_news_context import MAX_ITEMS, recent_news, select_filings

# Wed 2026-09-23 10:00 ET = 14:00Z. Window opens Tue 09-22 00:00 ET.
AT = datetime(2026, 9, 23, 14, 0, tzinfo=timezone.utc)


def _recent(rows):
    return {
        "form": [r[0] for r in rows], "acceptanceDateTime": [r[1] for r in rows],
        "accessionNumber": [f"0001-26-{i:06d}" for i in range(len(rows))],
        "items": [r[2] if len(r) > 2 else "" for r in rows],
    }


def test_window_and_ordering():
    rows = [
        ("8-K", "2026-09-23T12:00:00.000Z", "7.01,9.01"),   # in
        ("8-K", "2026-09-22T05:00:00.000Z", "2.02"),         # in (Tue 01:00 ET)
        ("8-K", "2026-09-22T03:00:00.000Z", "8.01"),         # before window
        ("8-K", "2026-09-23T15:00:00.000Z", "8.01"),         # after entry
    ]
    out = select_filings(_recent(rows), 123, AT)
    assert [i["published_at"][:13] for i in out] == ["2026-09-23T12", "2026-09-22T05"]
    assert out[0]["headline"] == "7.01 Reg FD disclosure"


def test_monday_window_opens_friday():
    monday = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
    out = select_filings(_recent([("8-K", "2026-09-25T10:00:00.000Z", "8.01")]), 1, monday)
    assert len(out) == 1


def test_insider_forms_skipped_and_dilution_flagged():
    rows = [("4", "2026-09-23T12:00:00.000Z"), ("424B5", "2026-09-23T12:01:00.000Z"),
            ("8-K", "2026-09-23T12:02:00.000Z", "3.02")]
    out = select_filings(_recent(rows), 1, AT)
    assert [i["kind"] for i in out] == ["offering", "offering"]


def test_capped():
    rows = [("8-K", f"2026-09-23T12:{m:02d}:00.000Z", "8.01") for m in range(MAX_ITEMS + 5)]
    assert len(select_filings(_recent(rows), 1, AT)) == MAX_ITEMS


def test_no_user_agent_returns_empty(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    assert recent_news("ABCD", AT) == []
