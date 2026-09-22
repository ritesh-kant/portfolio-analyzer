"""The levels a trade was governed by are written down with the trade.

`exits.initial_state` fixes a structural resistance and support at entry and
the exit rules watch them for the life of the position, but until 2026-09-22
the ledger dropped both: a review could only see the recorded-only location
metrics, which are measured on a different frame and gate nothing. A reviewer
looking at a chart therefore could not tell which level any rule was watching.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from src.momentum_trader import exits
from src.momentum_trader.ledger import PaperLedger, _structural_doc
from src.momentum_trader.levels import Level

from tests.momentum_trader.test_multi_entry import _session

IST = "Asia/Kolkata"


class _Collection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []
        self.updates: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def insert_one(self, doc: dict[str, Any]) -> Any:
        self.docs.append(doc)
        return type("R", (), {"inserted_id": "id-1"})()

    def update_one(self, where: dict[str, Any], update: dict[str, Any]) -> Any:
        self.updates.append((where, update))
        return type("R", (), {"modified_count": 1})()


class _Db:
    def __init__(self) -> None:
        self.cols: dict[str, _Collection] = {}

    def __getitem__(self, name: str) -> _Collection:
        return self.cols.setdefault(name, _Collection())


def test_structural_doc_flattens_a_level_and_tolerates_none() -> None:
    doc = _structural_doc(Level(391.55, "pivot_high", 2, 0.0, 2.0),
                          Level(378.25, "prev_day", 1, 0.0, 1.5))
    assert doc == {
        "structural_resistance": 391.55, "structural_resistance_kind": "pivot_high",
        "structural_support": 378.25, "structural_support_kind": "prev_day",
    }
    assert _structural_doc(None, None) == {
        "structural_resistance": None, "structural_resistance_kind": "",
        "structural_support": None, "structural_support_kind": "",
    }


def _one_open_position() -> Any:
    from src.momentum_trader import engine as eng

    bars, prev_close, profile = _session()
    cfg = eng.EngineConfig(stress_slip=0.0)
    st = eng.run_day("T", bars, prev_close, profile, cfg, lambda _s, _t: (0, ""))
    assert st.closed, "fixture must produce a trade"
    return st.closed[0]


def test_an_open_position_records_the_levels_its_exit_rules_hold() -> None:
    db = _Db()
    ledger = PaperLedger(db, None, False, "test")
    trade = _one_open_position()
    position = type("P", (), {
        "cand": trade.cand,
        "entry_time": trade.entry_time,
        "plan": type("Plan", (), {"entry": trade.entry, "stop": trade.cand.setup.stop,
                                  "target": None, "qty": trade.qty, "risk_inr": 500.0,
                                  "notional_inr": 50_000.0})(),
        "exit_state": exits.initial_state(
            entry=trade.entry, hard_stop=trade.cand.setup.stop,
            bars_tf=pd.DataFrame(), with_levels=False,
        ),
    })()
    position.exit_state.structural_resistance = Level(391.55, "pivot_high", 2, 0.0, 2.0)
    position.exit_state.structural_support = Level(378.25, "prev_day", 1, 0.0, 1.5)

    ledger.opened(position)

    doc = db["mt_positions"].docs[0]
    assert doc["structural_resistance"] == 391.55
    assert doc["structural_resistance_kind"] == "pivot_high"
    assert doc["structural_support"] == 378.25
    assert doc["structural_support_kind"] == "prev_day"
    # and the location metrics still travel with their own anchor
    assert doc["level_anchor_px"] == trade.cand.setup.trigger


def test_the_closed_row_repeats_them_so_a_restart_cannot_lose_them() -> None:
    """`closed()` matches on symbol+entry_time, so a position opened by an
    earlier process is closed by this one; the levels must be written on both
    paths or a restarted session loses them."""
    db = _Db()
    ledger = PaperLedger(db, None, False, "test")
    trade = _one_open_position()

    ledger.closed(trade, None, pd.DataFrame())

    _where, update = db["mt_positions"].updates[0]
    written = update["$set"]
    assert written["structural_resistance"] == trade.structural_resistance
    assert written["structural_resistance_kind"] == trade.structural_resistance_kind
    assert written["structural_support"] == trade.structural_support
    assert written["structural_support_kind"] == trade.structural_support_kind
    assert written["status"] == "closed"
