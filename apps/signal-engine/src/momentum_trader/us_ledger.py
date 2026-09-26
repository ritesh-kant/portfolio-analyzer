"""Paper ledger for the US arm: `mt_us_*` collections, money in DOLLARS.

Parallel to `ledger.PaperLedger` and deliberately not a flag on it. The engine's
money fields are named `*_inr` but hold the MARKET's currency (see
`EngineConfig.market`); this writer is where that is resolved, by storing them
as `*_usd` in collections the NSE page never reads. A US row can therefore never
be summed into a rupee total, by a query or by a dashboard.

mt_us_positions  — open + closed paper positions (read by /mt/us/trades)
mt_us_watchlist  — one document per session: the whole screen funnel
mt_us_candidates — attention promotions, setups and refused entries, by `kind`
mt_us_watch_bars — the session's 1-minute bars for every watched name (charts)
mt_us_stream_bars — the same names' bars as built from the push stream alone,
                    kept to measure the stream against REST (`yahoo_stream compare-db`)

Every write swallows its own failure after logging it: a Mongo outage must not
stop the session from managing positions it already holds. The CSV-free design
is intentional — the forward log IS these collections.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

import pandas as pd

from .engine import AttentionEvent, Candidate, ClosedTrade, Position, Rejection
from .ledger import _cand_doc, _structural_doc, chart_bars_doc
from .market import US, MarketProfile
from .us_screener import USScreenRow

logger = logging.getLogger(__name__)

WATCH_BARS_COLLECTION = "mt_us_watch_bars"
STREAM_BARS_COLLECTION = "mt_us_stream_bars"


def _position_filter(symbol: str, entry_time: pd.Timestamp, strategy: str) -> dict[str, Any]:
    return {"symbol": symbol, "status": "open",
            "entry_time": entry_time.to_pydatetime(), "strategy": strategy}


class USPaperLedger:
    def __init__(self, db: Any | None, strategy: str, profile: MarketProfile = US) -> None:
        self._db = db
        self._strategy = strategy
        self._p = profile

    def _write(self, collection: str, op: str, *args: Any, **kw: Any) -> Any:
        if self._db is None:
            return None
        try:
            return getattr(self._db[collection], op)(*args, **kw)
        except Exception:  # noqa: BLE001 — see module docstring
            logger.exception("%s %s failed", collection, op)
            return None

    # ── positions ───────────────────────────────────────────────────────────
    def opened(
        self,
        p: Position,
        row: USScreenRow | None,
        exchange: str = "",
        quote_source: str = "",
    ) -> str | None:
        """An entry, with the screen row that admitted it and what it cost to
        take. `cost_over_risk` is stored because it sets the win rate this
        trade needed to break even: (1 + C/R) / 3 at a 2:1 target."""
        cost = self._p.round_trip_cost(p.plan.entry, p.plan.entry, p.plan.qty)
        risk = p.plan.risk_inr                      # dollars; see EngineConfig.market
        doc = {
            **_cand_doc(p.cand),
            "market": self._p.code, "currency": self._p.currency_code,
            "status": "open", "paper": True, "strategy": self._strategy,
            "entry_time": p.entry_time.to_pydatetime(),
            "entry_price": p.plan.entry, "stop": p.plan.stop, "target": p.plan.target,
            "qty": p.plan.qty,
            "risk_usd": risk, "notional_usd": p.plan.notional_inr,
            "cost_usd_modelled": cost,
            "cost_over_risk": cost / risk if risk > 0 else None,
            "float_shares": row.float_shares if row else None,
            "screen_flags": dict(row.flags) if row else {},
            "screen_complete": bool(row.screen_complete) if row else False,
            "exchange": exchange,
            "quote_source": quote_source,
            **_structural_doc(p.exit_state.structural_resistance,
                              p.exit_state.structural_support),
        }
        result = self._write(self._p.positions_collection, "insert_one", doc)
        return str(result.inserted_id) if result is not None else None

    def closed(
        self,
        t: ClosedTrade,
        chart_bars: pd.DataFrame | None = None,
        held_through_halt: bool = False,
    ) -> None:
        """Close the row `opened` wrote. `held_through_halt` marks a trade whose
        stop could not protect it while trading was paused; its loss is not
        bounded by the plan, so the forward log must be readable without it."""
        self._write(
            self._p.positions_collection, "update_one",
            _position_filter(t.cand.symbol, t.entry_time, self._strategy),
            {"$set": {
                "status": "closed",
                "exit_time": t.exit_time.to_pydatetime(),
                "exit_price": t.exit, "exit_reason": t.exit_reason,
                "gross_usd": t.gross_inr, "costs_usd": t.costs_inr, "net_usd": t.net_inr,
                "held_through_halt": held_through_halt,
                "structural_resistance": t.structural_resistance,
                "structural_resistance_kind": t.structural_resistance_kind,
                "structural_support": t.structural_support,
                "structural_support_kind": t.structural_support_kind,
                "chart": {"interval": "1m", "bars": chart_bars_doc(chart_bars)}
                if chart_bars is not None else None,
            }},
        )

    # ── the audit trail ─────────────────────────────────────────────────────
    def attention(self, event: AttentionEvent) -> None:
        doc = asdict(event)
        doc["time"] = event.time.to_pydatetime()
        self._event("attention", doc)

    def candidate(self, c: Candidate) -> None:
        self._event("candidate", _cand_doc(c))

    def rejected(self, rejection: Rejection) -> None:
        doc = asdict(rejection)
        doc["time"] = rejection.time.to_pydatetime()
        self._event("rejection", doc)

    def _event(self, kind: str, doc: dict[str, Any]) -> None:
        self._write(self._p.candidates_collection, "insert_one",
                    {**doc, "kind": kind, "market": self._p.code, "strategy": self._strategy})

    # ── the watched names' candles ──────────────────────────────────────────
    def watch_bars(self, session_date: str, symbol: str, bars: pd.DataFrame) -> None:
        """The bars the engine is reading for a watched name, one document per
        (session, symbol), replaced as the day grows. Closed trades already keep
        theirs; this is what lets the watchlist page chart a name that was only
        watched. The web API cannot fetch Yahoo itself — it answers a plain
        server request with HTTP 429 — so the session is the only reader."""
        self._write(
            WATCH_BARS_COLLECTION, "replace_one",
            {"market": self._p.code, "date": session_date, "symbol": symbol},
            {"market": self._p.code, "date": session_date, "symbol": symbol,
             "interval": "1m", "bars": chart_bars_doc(bars)},
            upsert=True,
        )

    def stream_bars(self, session_date: str, symbol: str, bars: pd.DataFrame) -> None:
        """The bars the push stream built for a watched name, apart from REST.
        The engine decides on the stream's newest minute; this is the record of
        what it saw, for comparing with the REST bars in `watch_bars`."""
        self._write(
            STREAM_BARS_COLLECTION, "replace_one",
            {"market": self._p.code, "date": session_date, "symbol": symbol},
            {"market": self._p.code, "date": session_date, "symbol": symbol,
             "interval": "1m", "source": "yahoo_websocket", "bars": chart_bars_doc(bars)},
            upsert=True,
        )

    # ── the session's screen funnel ─────────────────────────────────────────
    def watchlist(self, doc: dict[str, Any]) -> None:
        """One document per session, replaced every cycle, so /mt/us/watchlist
        always shows the day's funnel as it stands."""
        self._write(
            self._p.watchlist_collection, "replace_one",
            {"market": self._p.code, "date": doc["date"]}, doc, upsert=True,
        )
