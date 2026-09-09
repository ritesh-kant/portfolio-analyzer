"""Paper ledger: Mongo `mt_*` collections + the spec §6 CSV log.

mt_candidates — every setup that fired on a qualifying mover (traded or not)
mt_positions  — open + closed paper positions (status: "open" | "closed")

Isolated from nt_* by prefix, same convention the options trader used.
"""

from __future__ import annotations

import csv
import logging
import os
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .engine import ENGINE_VERSION, Candidate, ClosedTrade, Position

logger = logging.getLogger(__name__)

CSV_COLUMNS = [
    "schema_version", "engine_version", "trade_id", "date", "symbol", "setup",
    "trigger_time", "trigger_px",
    "fill_px", "stop_px", "target_px",
    "qty", "day_chg_pct_at_trigger", "rvol", "catalyst", "event_type", "candle_tags",
    "catalyst_status", "catalyst_source_id", "decision_time", "fill_source",
    "next_round_level", "prev_day_gainer", "exit_time", "exit_px", "exit_reason",
    "gross_inr", "costs_inr", "net_inr", "float_filter_applied",
]

SCHEMA_VERSION = "3"


def trade_id(c: Candidate, entry_time: object) -> str:
    """Deterministic ID makes retrying an entry/exit harmless."""
    value = f"{c.symbol}|{c.time.isoformat()}|{c.setup.name}|{entry_time}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, value))


def _cand_doc(c: Candidate) -> dict[str, Any]:
    d = asdict(c)
    d["engine_version"] = ENGINE_VERSION
    d["setup"] = c.setup.name
    d["trigger_px"] = c.setup.trigger
    d["stop_px"] = c.setup.stop
    d["level"] = c.setup.level
    d["setup_meta"] = c.setup.meta
    d["time"] = c.time.to_pydatetime()
    d["candle_tags"] = list(c.candle_tags)
    for field in (
        "catalyst_published_at",
        "catalyst_ingested_at",
        "catalyst_classified_at",
        "decision_time",
    ):
        value = d.get(field)
        if value is not None:
            d[field] = value.to_pydatetime()
    return d


class PaperLedger:
    def __init__(self, db: Any | None, csv_path: Path | None, float_filter_applied: bool) -> None:
        self._db = db
        self._csv = csv_path
        self._ff = int(float_filter_applied)
        self._csv_trade_ids: set[str] = set()
        if db is not None:
            # Fail closed if an existing collection contains duplicate IDs.
            for collection, field in (("mt_candidates", "candidate_id"),
                                      ("mt_events", "event_id"), ("mt_positions", "trade_id")):
                db[collection].create_index(field, unique=True, partialFilterExpression={
                    field: {"$exists": True},
                })
        if self._csv:
            self._csv.parent.mkdir(parents=True, exist_ok=True)
            try:
                with self._csv.open("x", newline="") as f:
                    csv.writer(f).writerow(CSV_COLUMNS)
                    f.flush()
                    os.fsync(f.fileno())
            except FileExistsError:
                pass
            with self._csv.open(newline="") as f:
                rows = csv.DictReader(f)
                if rows.fieldnames != CSV_COLUMNS:
                    raise ValueError(
                        f"forward log schema mismatch at {self._csv}; "
                        "preserve it and start a versioned v2 log"
                    )
                self._csv_trade_ids = {row["trade_id"] for row in rows if row.get("trade_id")}

    # ── writes ────────────────────────────────────────────────────────────────

    def candidate(self, c: Candidate) -> None:
        if self._db is not None:
            try:
                candidate_id = str(uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{c.symbol}|{c.time.isoformat()}|{c.setup.name}|{c.setup.trigger}",
                ))
                self._db["mt_candidates"].update_one(
                    {"candidate_id": candidate_id},
                    {"$setOnInsert": {**_cand_doc(c), "candidate_id": candidate_id}},
                    upsert=True,
                )
            except Exception:  # noqa: BLE001
                raise RuntimeError("paper candidate persistence failed") from None

    def opened(self, p: Position) -> str | None:
        ident = trade_id(p.cand, p.entry_time)
        doc = {
            **_cand_doc(p.cand), "trade_id": ident, "status": "open",
            "entry_time": p.entry_time.to_pydatetime(),
            "entry_price": p.plan.entry, "stop": p.plan.stop,
            "target": p.plan.target if p.has_target else None,
            "qty": p.plan.qty, "risk_inr": p.plan.risk_inr, "notional_inr": p.plan.notional_inr,
            "fill_source": p.fill_source,
            "decision_time": None if p.decision_time is None else p.decision_time.to_pydatetime(),
            "fill_exchange_time": None if p.fill_exchange_time is None
            else p.fill_exchange_time.to_pydatetime(),
            "fill_receipt_time": None if p.fill_receipt_time is None
            else p.fill_receipt_time.to_pydatetime(),
            "paper": True, "float_filter_applied": self._ff,
        }
        if self._db is None:
            return ident
        try:
            self._db["mt_events"].update_one(
                {"event_id": f"{ident}:opened"},
                {"$setOnInsert": {"event_id": f"{ident}:opened", "type": "opened", **doc}},
                upsert=True,
            )
            self._db["mt_positions"].update_one(
                {"trade_id": ident}, {"$setOnInsert": doc}, upsert=True
            )
            return ident
        except Exception:  # noqa: BLE001
            raise RuntimeError("paper entry persistence failed") from None

    def closed(self, t: ClosedTrade, next_round_level: float | None = None) -> None:
        ident = trade_id(t.cand, t.entry_time)
        close_doc = {
            **_cand_doc(t.cand), "trade_id": ident, "status": "closed",
            "entry_time": t.entry_time.to_pydatetime(), "entry_price": t.entry,
            "stop": t.cand.setup.stop, "target": t.target, "qty": t.qty,
            "paper": True, "float_filter_applied": self._ff,
            "exit_time": t.exit_time.to_pydatetime(), "exit_price": t.exit,
            "exit_reason": t.exit_reason, "gross_inr": t.gross_inr,
            "costs_inr": t.costs_inr, "net_inr": t.net_inr,
            "fill_source": t.fill_source,
            "decision_time": None if t.decision_time is None else t.decision_time.to_pydatetime(),
            "fill_exchange_time": None if t.fill_exchange_time is None
            else t.fill_exchange_time.to_pydatetime(),
            "fill_receipt_time": None if t.fill_receipt_time is None
            else t.fill_receipt_time.to_pydatetime(),
        }
        if self._db is not None:
            try:
                self._db["mt_events"].update_one(
                    {"event_id": f"{ident}:closed"},
                    {"$setOnInsert": {
                        "event_id": f"{ident}:closed", "type": "closed", **close_doc,
                    }},
                    upsert=True,
                )
                self._db["mt_positions"].update_one(
                    {"trade_id": ident}, {"$set": close_doc}, upsert=True,
                )
            except Exception:  # noqa: BLE001
                raise RuntimeError("paper exit persistence failed") from None
        if self._csv and ident not in self._csv_trade_ids:
            c = t.cand
            row = [
                SCHEMA_VERSION, ENGINE_VERSION, ident, str(c.time.date()), c.symbol, c.setup.name,
                c.time.strftime("%H:%M"),
                f"{c.setup.trigger:.2f}", f"{t.entry:.2f}", f"{c.setup.stop:.2f}",
                "" if t.target is None else f"{t.target:.2f}", t.qty,
                f"{c.day_chg_pct:.2f}", f"{c.rvol:.2f}", c.catalyst, c.event_type,
                "|".join(c.candle_tags),
                c.catalyst_status, c.catalyst_source_id,
                "" if t.decision_time is None else t.decision_time.isoformat(), t.fill_source,
                "" if next_round_level is None else f"{next_round_level:.0f}",
                int(c.prev_day_gainer), t.exit_time.strftime("%H:%M"), f"{t.exit:.2f}",
                t.exit_reason, f"{t.gross_inr:.2f}", f"{t.costs_inr:.2f}", f"{t.net_inr:.2f}",
                self._ff,
            ]
            with self._csv.open("a", newline="") as f:
                csv.writer(f).writerow(row)
                f.flush()
                os.fsync(f.fileno())
            self._csv_trade_ids.add(ident)

    def session_summary(self, summary: dict[str, Any]) -> None:
        """Persist EOD facts independently of notification delivery."""
        if self._db is None:
            return
        try:
            self._db["mt_sessions"].update_one(
                {"session_id": summary["session_id"]}, {"$set": summary}, upsert=True
            )
        except Exception:  # noqa: BLE001
            raise RuntimeError("paper session persistence failed") from None

    def acquire_session(self, session_id: str, owner: str, ttl_seconds: int = 120) -> bool:
        """Non-expiring single-writer guard; no automatic takeover/restart.

        The old expiring lease allowed a paused writer to overlap its successor.
        Until durable recovery exists, a failed run retains this global guard.
        A separate daily claim prevents resetting risk by restarting a clean run.
        `ttl_seconds` is retained for call compatibility but intentionally unused.
        """
        if self._db is None:
            return False
        try:
            self._db["mt_scanner_guard"].update_one(
                {"_id": "paper", "owner": owner},
                {"$setOnInsert": {"owner": owner, "session_id": session_id,
                                  "started_at": datetime.now(UTC)}}, upsert=True,
            )
            self._db["mt_scanner_runs"].update_one(
                {"_id": session_id.split(":")[0], "owner": owner},
                {"$setOnInsert": {"owner": owner, "session_id": session_id}}, upsert=True,
            )
            return True
        except Exception:  # noqa: BLE001
            logger.error("paper session guard unavailable; reconciliation required")
            return False

    def release_session(self, session_id: str, owner: str) -> None:
        if self._db is None:
            return
        try:
            self._db["mt_scanner_guard"].delete_one(
                {"_id": "paper", "session_id": session_id, "owner": owner}
            )
        except Exception:  # noqa: BLE001
            raise RuntimeError("paper session guard release failed") from None

    def has_open_positions_for_day(self, day: datetime) -> bool:
        """Any unresolved position, including prior days, blocks a new run."""
        if self._db is None:
            return True
        try:
            return bool(self._db["mt_positions"].count_documents({
                "status": "open",
            }))
        except Exception:  # noqa: BLE001
            logger.error("open-position recovery check failed")
            return True
