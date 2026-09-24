"""hard_catalyst's three answers: event, no event, and feed down (unknown).

A small in-memory stand-in for the `nt_signals` collection that honours the
filter shapes hard_catalyst sends (`created_at` range, `stocks`, `event_type`).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from src.momentum_trader.catalyst import feed_last_signal, hard_catalyst

AT = pd.Timestamp("2026-09-23 10:00", tz="Asia/Kolkata")
AT_UTC = datetime(2026, 9, 23, 4, 30)  # naive UTC, as pymongo returns it


class FakeSignals:
    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs

    def find_one(self, filter, sort=None, projection=None):
        def ok(d):
            rng = filter.get("created_at")
            if rng and not (rng["$gte"] <= d["created_at"] <= rng["$lte"]):
                return False
            if "stocks" in filter and filter["stocks"] not in d.get("stocks", []):
                return False
            if "event_type" in filter and d.get("event_type") not in filter["event_type"]["$in"]:
                return False
            return True

        hits = sorted((d for d in self.docs if ok(d)), key=lambda d: d["created_at"], reverse=True)
        return hits[0] if hits else None


def _sig(hours_before: float, stocks=("OTHER",), event_type="other") -> dict:
    return {"created_at": AT_UTC - timedelta(hours=hours_before), "stocks": list(stocks),
            "event_type": event_type}


def test_group_a_event_for_the_symbol_is_a_catalyst():
    signals = FakeSignals([_sig(2, stocks=["IKS"], event_type="order_win")])
    assert hard_catalyst(signals, "IKS", AT) == (1, "order_win")


def test_live_feed_without_an_event_for_the_symbol_is_no_catalyst():
    signals = FakeSignals([_sig(2), _sig(5, stocks=["IKS"], event_type="other")])
    assert hard_catalyst(signals, "IKS", AT) == (0, "")


def test_feed_silent_for_the_whole_window_is_unknown_not_zero():
    # The 2026-06-26 case: the last signal is months old.
    signals = FakeSignals([_sig(24 * 89, stocks=["IKS"], event_type="order_win")])
    assert hard_catalyst(signals, "IKS", AT) == (None, "")


def test_monday_morning_after_a_quiet_weekend_is_still_a_live_feed():
    # Last write Friday 15:00 IST, entry Monday 09:30 IST: no signal in the
    # 24h event window, but the feed is healthy — the feed never runs weekends.
    monday = pd.Timestamp("2026-09-28 09:30", tz="Asia/Kolkata")
    friday = datetime(2026, 9, 25, 9, 30)  # 15:00 IST, naive UTC
    signals = FakeSignals([{"created_at": friday, "stocks": ["OTHER"], "event_type": "other"}])
    assert hard_catalyst(signals, "IKS", monday) == (0, "")


def test_empty_feed_is_unknown():
    assert hard_catalyst(FakeSignals([]), "IKS", AT) == (None, "")


def test_feed_last_signal_reports_the_newest_write():
    signals = FakeSignals([_sig(30), _sig(3)])
    assert feed_last_signal(signals) == AT_UTC - timedelta(hours=3)
    assert feed_last_signal(FakeSignals([])) is None
