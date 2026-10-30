"""Orphaned `open` paper positions get closed by replaying the day's bars.

The scanner is the only writer of an exit; if it sleeps or dies, the row stays
open. `reconcile.replay_position` runs the stored position through the engine's
own `step()` so the result is the one the live loop would have produced.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from src.momentum_trader import engine as eng
from src.momentum_trader import reconcile as rec

IST = "Asia/Kolkata"
DAY = "2026-10-29"


def _bars(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(f"{DAY} {t}", tz=IST) for t, *_ in rows])
    return pd.DataFrame(
        [{"open": o, "high": h, "low": low, "close": c, "volume": 10_000.0}
         for _t, o, h, low, c in rows], index=idx)


def _doc(**over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "symbol": "ABC", "status": "open", "strategy": "warrior_strict",
        "setup": "attention_1m_confirmation", "trigger_px": 105.0, "stop_px": 104.0,
        "level": 105.0, "setup_meta": {}, "time": pd.Timestamp(f"{DAY} 10:00", tz=IST),
        "entry_time": pd.Timestamp(f"{DAY} 10:00", tz=IST).to_pydatetime(),
        "day_chg_pct": 5.0, "rvol": 3.0, "catalyst": 0, "event_type": "",
        "candle_tags": [], "entry_price": 105.0, "stop": 104.0, "target": 107.0,
        "target_source": "fixed_2r", "qty": 100, "risk_inr": 100.0, "notional_inr": 10_500.0,
        "checkpoint": None, "checkpoint_stop": None,
        "structural_support": None, "structural_resistance": None,
    }
    doc.update(over)
    return doc


def _cfg() -> eng.EngineConfig:
    return eng.EngineConfig(stress_slip=0.0)


def test_orphan_that_hit_its_target_is_closed_at_the_target() -> None:
    bars = _bars([
        ("09:59", 104.9, 105.1, 104.8, 105.0),
        ("10:00", 105.0, 105.5, 104.9, 105.4),
        ("10:01", 105.4, 106.0, 105.3, 105.9),
        ("10:02", 105.9, 107.4, 105.8, 107.2),   # high 107.4 >= target 107.0
    ])
    trade, window = rec.replay_position(_doc(), bars, _cfg())
    assert trade is not None
    assert trade.exit_reason == "target"
    assert trade.exit == 107.0
    assert trade.gross_inr == (107.0 - 105.0) * 100
    assert trade.net_inr < trade.gross_inr            # real MIS costs
    assert window.index[-1] == pd.Timestamp(f"{DAY} 10:02", tz=IST)


def test_orphan_that_hit_its_stop_is_closed_at_the_stop() -> None:
    bars = _bars([
        ("10:00", 105.0, 105.2, 104.9, 105.1),
        ("10:01", 105.1, 105.2, 103.5, 103.8),   # low through the 104.0 stop
        ("10:02", 103.8, 108.0, 103.7, 107.9),   # later rally must not matter
    ])
    trade, _ = rec.replay_position(_doc(), bars, _cfg())
    assert trade is not None
    assert trade.exit_reason == "stop"
    assert trade.exit == 104.0
    assert trade.net_inr < 0


def test_nothing_hit_closes_at_the_session_end_not_overnight() -> None:
    bars = _bars([
        ("10:00", 105.0, 105.3, 104.9, 105.2),
        ("10:01", 105.2, 105.4, 105.0, 105.3),
    ])
    trade, _ = rec.replay_position(_doc(), bars, _cfg())
    assert trade is not None
    assert trade.exit_reason == "eod_sweep"
    assert trade.exit == 105.3


def test_checkpoint_fields_survive_the_round_trip() -> None:
    pos = rec.position_from_doc(
        _doc(checkpoint=106.0, checkpoint_stop=105.5,
             structural_support=104.5, structural_support_kind="pivot_low",
             structural_resistance=108.0, structural_resistance_kind="round"),
        _cfg(), _bars([("10:00", 105.0, 105.2, 104.9, 105.1)]))
    assert (pos.checkpoint, pos.checkpoint_stop) == (106.0, 105.5)
    assert pos.exit_state.structural_support is not None
    assert pos.exit_state.structural_support.price == 104.5
    assert pos.exit_state.structural_resistance is not None
    assert pos.exit_state.structural_resistance.kind == "round"
    assert pos.plan.target == 107.0 and pos.plan.qty == 100


def test_naive_mongo_datetimes_are_read_as_utc() -> None:
    naive_utc = datetime(2026, 10, 29, 4, 30)          # 10:00 IST
    assert rec._ist(naive_utc) == pd.Timestamp(f"{DAY} 10:00", tz=IST)


class _Coll:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def find(self, q: dict[str, Any]) -> list[dict[str, Any]]:
        return [d for d in self._docs
                if d["status"] == q["status"] and d["strategy"] == q["strategy"]]


def test_todays_position_is_left_to_the_live_scanner_until_the_close() -> None:
    db = {"mt_positions": _Coll([_doc()])}
    during = pd.Timestamp(f"{DAY} 12:00", tz=IST)
    after = pd.Timestamp(f"{DAY} 15:40", tz=IST)
    tomorrow = pd.Timestamp("2026-10-30 09:00", tz=IST)
    assert rec.find_orphans(db, "warrior_strict", during) == []
    assert len(rec.find_orphans(db, "warrior_strict", after)) == 1
    assert len(rec.find_orphans(db, "warrior_strict", tomorrow)) == 1


def test_shorts_and_other_strategies_are_not_touched() -> None:
    db = {"mt_positions": _Coll([
        _doc(strategy="warrior_strict_short", side="short"),
        _doc(strategy="baseline"),
    ])}
    tomorrow = pd.Timestamp("2026-10-30 09:00", tz=IST)
    assert rec.find_orphans(db, "warrior_strict", tomorrow) == []
