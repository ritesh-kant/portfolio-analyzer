"""Catalyst gate: was there a HARD corporate event for this symbol in the 24h
before the trigger? Frozen definition (hypothesis v1 §4 / v2 §0).

Source = the news-trader's `nt_signals` collection, which already carries the
classifier's `event_type`. Only *presence* of a Group-A type counts; the
classifier's bullish/bearish call is deliberately ignored.

Absence only means "no catalyst" while the feed is running. `nt_signals` stopped
on 2026-06-26 (news-trader paused), and every live trade after that was
stamped 0 — "no event" when the truth was "not looked at". So when the feed
has written nothing for FEED_ALIVE_WITHIN the answer is `None` (unknown), and
the forward screen keeps those trades out of both groups. That is wider than
LOOKBACK on purpose: the feed has never written on a Saturday or Sunday, so a
24h liveness test would call a healthy feed dead every Monday morning.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

import pandas as pd

HARD_EVENTS = frozenset({"m_and_a", "earnings", "order_win", "regulatory", "capital_action"})
LOOKBACK = timedelta(hours=24)
FEED_ALIVE_WITHIN = timedelta(days=4)   # a weekend plus a holiday


class _Collection(Protocol):
    def find_one(self, filter: dict[str, Any], sort: Any = ..., projection: Any = ...) -> Any: ...


def _naive_utc(at: pd.Timestamp) -> datetime:
    # pymongo hands back naive UTC datetimes; query with the same convention
    return (at.tz_convert("UTC").tz_localize(None) if at.tzinfo else at).to_pydatetime()


def feed_last_signal(signals: _Collection) -> datetime | None:
    """When the feed last wrote any signal (naive UTC), or None if it never has."""
    doc = signals.find_one({}, sort=[("created_at", -1)], projection={"created_at": 1})
    return doc.get("created_at") if doc else None


def hard_catalyst(signals: _Collection, symbol: str, at: pd.Timestamp) -> tuple[int | None, str]:
    """(1, event_type) if a Group-A signal names `symbol` in the prior 24h,
    (0, '') if none did while the feed was running, (None, '') if the feed
    wrote nothing at all for FEED_ALIVE_WITHIN."""
    at_utc = _naive_utc(at)
    feed_alive = signals.find_one(
        {"created_at": {"$gte": at_utc - FEED_ALIVE_WITHIN, "$lte": at_utc}},
        projection={"_id": 1},
    )
    if not feed_alive:
        return None, ""
    doc = signals.find_one(
        {
            "stocks": symbol,
            "event_type": {"$in": sorted(HARD_EVENTS)},
            "created_at": {"$gte": at_utc - LOOKBACK, "$lte": at_utc},
        },
        sort=[("created_at", -1)],
        projection={"event_type": 1},
    )
    if not doc:
        return 0, ""
    return 1, str(doc.get("event_type", ""))


def no_catalyst(_symbol: str, _at: pd.Timestamp) -> tuple[int, str]:
    """Backtest stand-in when no announcement archive is loaded."""
    return 0, ""


class DatedEventLookup:
    """Backtest catalyst from a CSV of (symbol, date, event_type) — e.g. results
    dates from StratQ. A trigger on date D or D+1 counts (results after close on D
    move the stock on D+1)."""

    def __init__(self, events: pd.DataFrame) -> None:
        self._map: dict[tuple[str, str], str] = {}
        for _, r in events.iterrows():
            d = pd.Timestamp(r["date"]).normalize()
            for offset in (0, 1):
                self._map[(str(r["symbol"]), str((d + pd.Timedelta(days=offset)).date()))] = str(
                    r.get("event_type", "earnings")
                )

    def __call__(self, symbol: str, at: pd.Timestamp) -> tuple[int, str]:
        ev = self._map.get((symbol, str(at.date())))
        return (1, ev) if ev else (0, "")
