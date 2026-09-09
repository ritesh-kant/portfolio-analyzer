"""Catalyst gate: was there a HARD corporate event for this symbol in the 24h
before the trigger? Frozen definition (hypothesis v1 §4 / v2 §0).

Source = the news-trader's `nt_signals` collection, which already carries the
classifier's `event_type`. Only *presence* of a Group-A type counts; the
classifier's bullish/bearish call is deliberately ignored.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Protocol

import pandas as pd

from .engine import CatalystResult

HARD_EVENTS = frozenset({"m_and_a", "earnings", "order_win", "regulatory", "capital_action"})
LOOKBACK = timedelta(hours=24)


class _Collection(Protocol):
    def find_one(self, filter: dict[str, Any], sort: Any = ..., projection: Any = ...) -> Any: ...


def hard_catalyst_detail(signals: _Collection, symbol: str, at: pd.Timestamp) -> CatalystResult:
    """Return a point-in-time label and non-sensitive provenance metadata."""
    # pymongo hands back naive UTC datetimes; query with the same convention
    at_utc = (at.tz_convert("UTC").tz_localize(None) if at.tzinfo else at).to_pydatetime()
    doc = signals.find_one(
        {
            "stocks": symbol,
            "event_type": {"$in": sorted(HARD_EVENTS)},
            "created_at": {"$gte": at_utc - LOOKBACK, "$lte": at_utc},
        },
        sort=[("created_at", -1)],
        projection={"event_type": 1, "published_at": 1, "created_at": 1, "classified_at": 1},
    )
    if not doc:
        # Connectivity proves neither ingestion coverage nor classifier health.
        # Until a coverage watermark exists, an empty query is not a control label.
        return CatalystResult(status="unknown")

    def _timestamp(value: object) -> pd.Timestamp | None:
        if value is None:
            return None
        stamp = pd.Timestamp(value)
        return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp

    return CatalystResult(
        status="present",
        event_type=str(doc.get("event_type", "")),
        source_id=str(doc.get("_id", "")),
        published_at=_timestamp(doc.get("published_at")),
        ingested_at=None,
        classified_at=_timestamp(doc.get("classified_at") or doc.get("created_at")),
    )


def hard_catalyst(signals: _Collection, symbol: str, at: pd.Timestamp) -> tuple[int, str]:
    """Legacy tuple API retained for historical backtests and existing callers."""
    result = hard_catalyst_detail(signals, symbol, at)
    return result.value, result.event_type


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
