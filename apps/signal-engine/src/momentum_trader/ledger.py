"""Paper ledger: Mongo `mt_*` collections + the spec §6 CSV log.

mt_candidates — every setup that fired on a qualifying mover (traded or not)
mt_positions  — open + closed paper positions (status: "open" | "closed")

Isolated from nt_* by prefix, same convention the options trader used.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from .engine import AttentionEvent, Candidate, ClosedTrade, Position, Rejection

logger = logging.getLogger(__name__)

CSV_COLUMNS = [
    "date", "symbol", "setup", "trigger_time", "trigger_px", "fill_px", "stop_px", "target_px",
    "qty", "day_chg_pct_at_trigger", "rvol", "catalyst", "event_type", "candle_tags",
    "next_round_level", "prev_day_gainer", "exit_time", "exit_px", "exit_reason",
    "gross_inr", "costs_inr", "net_inr", "float_filter_applied",
]


def chart_bars_doc(bars: pd.DataFrame) -> list[dict[str, Any]]:
    """Make the closed 1-minute bars portable for the trade-review UI.

    The scanner is the only component that has market-data access.  Capturing
    the raw bars when a paper position closes means the web app can reproduce
    the chart later without a browser token, a broker API call, or a mutable
    cache.  Indicators deliberately remain derived values in the UI so their
    formulas stay visible and can be changed without rewriting stored trades.
    """
    required = ("open", "high", "low", "close", "volume")
    if bars.empty or any(col not in bars.columns for col in required):
        return []
    return [
        {
            "time": pd.Timestamp(at).isoformat(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        }
        for at, row in bars.iterrows()
    ]


def _cand_doc(c: Candidate) -> dict[str, Any]:
    d = asdict(c)
    d["setup"] = c.setup.name
    d["trigger_px"] = c.setup.trigger
    d["stop_px"] = c.setup.stop
    d["level"] = c.setup.level
    d["setup_meta"] = c.setup.meta
    d["time"] = c.time.to_pydatetime()
    d["candle_tags"] = list(c.candle_tags)
    return d


class PaperLedger:
    def __init__(self, db: Any | None, csv_path: Path | None, float_filter_applied: bool) -> None:
        self._db = db
        self._csv = csv_path
        self._ff = int(float_filter_applied)
        if self._csv and not self._csv.exists():
            self._csv.parent.mkdir(parents=True, exist_ok=True)
            with self._csv.open("w", newline="") as f:
                csv.writer(f).writerow(CSV_COLUMNS)

    # ── writes ────────────────────────────────────────────────────────────────

    def candidate(self, c: Candidate) -> None:
        if self._db is not None:
            try:
                self._db["mt_candidates"].insert_one(_cand_doc(c))
            except Exception:  # noqa: BLE001
                logger.exception("mt_candidates insert failed")

    def attention(self, event: AttentionEvent) -> None:
        if self._db is None:
            return
        try:
            self._db["mt_attention"].insert_one({
                "symbol": event.symbol,
                "time": event.time.to_pydatetime(),
                "day_chg_pct": event.day_chg_pct,
                "rvol": event.rvol,
                "reason": event.reason,
                "candle_tags": list(event.candle_tags),
            })
        except Exception:  # noqa: BLE001
            logger.exception("mt_attention insert failed")

    def rejected(self, rejection: Rejection) -> None:
        if self._db is None:
            return
        try:
            self._db["mt_rejections"].insert_one({
                "symbol": rejection.symbol,
                "time": rejection.time.to_pydatetime(),
                "reason": rejection.reason,
                "setup": rejection.setup,
                "trigger": rejection.trigger,
                "observed_price": rejection.observed_price,
            })
        except Exception:  # noqa: BLE001
            logger.exception("mt_rejections insert failed")

    def opened(self, p: Position) -> str | None:
        doc = {
            **_cand_doc(p.cand), "status": "open", "entry_time": p.entry_time.to_pydatetime(),
            "entry_price": p.plan.entry, "stop": p.plan.stop, "target": p.plan.target,
            "qty": p.plan.qty, "risk_inr": p.plan.risk_inr, "notional_inr": p.plan.notional_inr,
            "paper": True, "float_filter_applied": self._ff,
        }
        if self._db is None:
            return None
        try:
            return str(self._db["mt_positions"].insert_one(doc).inserted_id)
        except Exception:  # noqa: BLE001
            logger.exception("mt_positions insert failed")
            return None

    def closed(
        self,
        t: ClosedTrade,
        next_round_level: float | None = None,
        chart_bars: pd.DataFrame | None = None,
    ) -> None:
        if self._db is not None:
            try:
                self._db["mt_positions"].update_one(
                    {"symbol": t.cand.symbol, "status": "open",
                     "entry_time": t.entry_time.to_pydatetime()},
                    {"$set": {
                        "status": "closed", "exit_time": t.exit_time.to_pydatetime(),
                        "exit_price": t.exit, "exit_reason": t.exit_reason,
                        "gross_inr": t.gross_inr, "costs_inr": t.costs_inr, "net_inr": t.net_inr,
                        # Raw one-minute session bars are sufficient to render
                        # candles and derive EMA/VWAP/MACD/volume in the review UI.
                        "chart": {
                            "interval": "1m",
                            "bars": chart_bars_doc(chart_bars)
                            if chart_bars is not None else [],
                        },
                    }},
                )
            except Exception:  # noqa: BLE001
                logger.exception("mt_positions close failed")
        if self._csv:
            c = t.cand
            row = [
                str(c.time.date()), c.symbol, c.setup.name, c.time.strftime("%H:%M"),
                f"{c.setup.trigger:.2f}", f"{t.entry:.2f}", f"{c.setup.stop:.2f}",
                f"{t.entry + (t.entry - c.setup.stop) * 2:.2f}", t.qty,
                f"{c.day_chg_pct:.2f}", f"{c.rvol:.2f}", c.catalyst, c.event_type,
                "|".join(c.candle_tags),
                "" if next_round_level is None else f"{next_round_level:.0f}",
                int(c.prev_day_gainer), t.exit_time.strftime("%H:%M"), f"{t.exit:.2f}",
                t.exit_reason, f"{t.gross_inr:.2f}", f"{t.costs_inr:.2f}", f"{t.net_inr:.2f}",
                self._ff,
            ]
            with self._csv.open("a", newline="") as f:
                csv.writer(f).writerow(row)
