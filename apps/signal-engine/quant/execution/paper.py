"""Paper-trade execution adapter — Month 4.

Public API (mirrors the eventual Kite Connect live adapter):
    place_order(symbol, side, qty, price, order_type, strategy_id) -> order_id
    cancel_order(order_id) -> bool
    get_order(order_id) -> Order
    get_positions() -> list[Position]
    get_orders(status=None) -> list[Order]
    close_position(symbol, strategy_id) -> TradeResult

State is persisted to a JSON file (data/paper/orders.json) so it survives
process restarts.  All timestamps are UTC ISO strings.

Post-trade hook
---------------
After close_position(), the adapter fires the post-mortem hook by calling
quant.agents.post_mortem.run() with the TradeResult.  This keeps the
post-mortem loop running automatically without manual wiring.

Usage
-----
    from quant.execution.paper import PaperBroker

    broker = PaperBroker()
    order_id = broker.place_order("PIIND", "BUY", qty=10, price=1500.0)
    positions = broker.get_positions()
    result = broker.close_position("PIIND", strategy_id="pead_midcap")

Design constraints (plan §4, Month 4)
--------------------------------------
- Same interface as the future kite.py live adapter.
- PnL tracks net of cost model from pead_midcap._ROUND_TRIP_COST.
- No partial fills — paper orders always fill fully at the stated price.
- Slippage model: 0 (paper is best-case; the live adapter adds realistic
  slippage).  The gap between paper and live PnL is tracked as "tracking
  error" (plan §12, Month 5 verification).
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

OrderSide = Literal["BUY", "SELL"]
OrderStatus = Literal["OPEN", "FILLED", "CANCELLED"]
OrderType = Literal["MARKET", "LIMIT"]

# Indian equity round-trip cost model (same as pead_midcap._ROUND_TRIP_COST)
_ROUND_TRIP_COST_FRACTION = 0.0045  # 45 bps: STT + exchange + SEBI + GST + slippage


def _now_utc() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class Order:
    order_id: str
    symbol: str
    side: OrderSide
    qty: int
    price: float          # fill price (paper: always equals requested price)
    order_type: OrderType
    strategy_id: str
    status: OrderStatus
    created_at: str
    filled_at: str | None = None
    cancelled_at: str | None = None
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Order":
        return cls(**d)


@dataclass
class Position:
    symbol: str
    strategy_id: str
    qty: int
    entry_price: float
    entry_date: str       # ISO date (YYYY-MM-DD) of the first fill
    entry_order_id: str
    current_price: float = 0.0  # updated by mark_to_market()
    unrealised_pnl: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        return cls(**d)


@dataclass
class TradeResult:
    """Outcome of a closed paper trade — passed to the post-mortem agent."""
    symbol: str
    strategy_id: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    qty: int
    gross_return: float    # (exit - entry) / entry
    net_return: float      # gross_return - round_trip_cost
    holding_days: int
    entry_order_id: str
    exit_order_id: str


class PaperBroker:
    """In-memory paper broker with JSON persistence.

    Parameters
    ----------
    state_file : str | Path | None
        Path to the JSON state file.  Defaults to data/paper/orders.json.
        Pass None to use in-memory only (useful for tests).
    """

    def __init__(self, state_file: str | Path | None = None) -> None:
        if state_file is None:
            base = os.environ.get("QUANT_DATA_DIR", "data")
            state_file = Path(base) / "paper" / "orders.json"

        self._state_file = Path(state_file) if state_file else None
        self._orders: dict[str, Order] = {}
        self._positions: dict[str, Position] = {}  # key: f"{symbol}:{strategy_id}"

        if self._state_file is not None:
            self._load()

    # ── Public API ─────────────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: int,
        price: float,
        order_type: OrderType = "MARKET",
        strategy_id: str = "default",
        notes: str = "",
    ) -> str:
        """Place a paper order and immediately fill it.

        Parameters
        ----------
        symbol : str
            NSE symbol.
        side : "BUY" | "SELL"
        qty : int
            Number of shares.
        price : float
            Fill price (paper: no slippage).
        order_type : "MARKET" | "LIMIT"
        strategy_id : str
            Strategy that originated this order.
        notes : str
            Optional human note.

        Returns
        -------
        str — order_id (UUID4).
        """
        order_id = str(uuid.uuid4())
        now = _now_utc()

        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            order_type=order_type,
            strategy_id=strategy_id,
            status="FILLED",
            created_at=now,
            filled_at=now,
            notes=notes,
        )
        self._orders[order_id] = order

        # Update position
        pos_key = f"{symbol}:{strategy_id}"
        if side == "BUY":
            if pos_key in self._positions:
                # Average up: weighted average entry price
                existing = self._positions[pos_key]
                total_qty = existing.qty + qty
                avg_price = (existing.entry_price * existing.qty + price * qty) / total_qty
                existing.entry_price = avg_price
                existing.qty = total_qty
            else:
                entry_date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
                self._positions[pos_key] = Position(
                    symbol=symbol,
                    strategy_id=strategy_id,
                    qty=qty,
                    entry_price=price,
                    entry_date=entry_date,
                    entry_order_id=order_id,
                    current_price=price,
                )
        elif side == "SELL":
            if pos_key in self._positions:
                existing = self._positions[pos_key]
                existing.qty -= qty
                if existing.qty <= 0:
                    del self._positions[pos_key]
            else:
                logger.warning(
                    "SELL order for %s:%s but no open position — creating short",
                    symbol, strategy_id,
                )

        logger.info(
            "paper_order order_id=%s symbol=%s side=%s qty=%d price=%.2f strategy=%s",
            order_id, symbol, side, qty, price, strategy_id,
        )
        self._save()
        return order_id

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order (no-op for already-filled paper orders)."""
        if order_id not in self._orders:
            logger.warning("cancel_order: order %s not found", order_id)
            return False
        order = self._orders[order_id]
        if order.status == "FILLED":
            logger.warning("cancel_order: order %s already filled", order_id)
            return False
        order.status = "CANCELLED"
        order.cancelled_at = _now_utc()
        self._save()
        return True

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def get_orders(
        self,
        status: OrderStatus | None = None,
        strategy_id: str | None = None,
    ) -> list[Order]:
        orders = list(self._orders.values())
        if status is not None:
            orders = [o for o in orders if o.status == status]
        if strategy_id is not None:
            orders = [o for o in orders if o.strategy_id == strategy_id]
        return sorted(orders, key=lambda o: o.created_at)

    def get_positions(
        self,
        strategy_id: str | None = None,
    ) -> list[Position]:
        positions = list(self._positions.values())
        if strategy_id is not None:
            positions = [p for p in positions if p.strategy_id == strategy_id]
        return positions

    def mark_to_market(self, symbol: str, current_price: float) -> None:
        """Update unrealised PnL for all open positions in symbol."""
        for pos in self._positions.values():
            if pos.symbol == symbol:
                pos.current_price = current_price
                pos.unrealised_pnl = (current_price - pos.entry_price) / pos.entry_price * pos.qty
        self._save()

    def close_position(
        self,
        symbol: str,
        strategy_id: str,
        exit_price: float,
        exit_date: str | None = None,
        trigger_postmortem: bool = True,
    ) -> TradeResult | None:
        """Close an open position and optionally fire the post-mortem agent.

        Parameters
        ----------
        symbol : str
        strategy_id : str
        exit_price : float
            Current market price for the paper exit.
        exit_date : str | None
            ISO date string.  Defaults to today.
        trigger_postmortem : bool
            If True, calls quant.agents.post_mortem.run() after closing.

        Returns
        -------
        TradeResult | None — None if no open position found.
        """
        pos_key = f"{symbol}:{strategy_id}"
        pos = self._positions.get(pos_key)
        if pos is None:
            logger.warning("close_position: no open position for %s:%s", symbol, strategy_id)
            return None

        if exit_date is None:
            exit_date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

        # Place the closing SELL order
        exit_order_id = self.place_order(
            symbol=symbol,
            side="SELL",
            qty=pos.qty,
            price=exit_price,
            order_type="MARKET",
            strategy_id=strategy_id,
            notes=f"auto-close position opened {pos.entry_date}",
        )

        gross_return = (exit_price - pos.entry_price) / pos.entry_price
        net_return = gross_return - _ROUND_TRIP_COST_FRACTION

        try:
            from datetime import date
            entry_dt = date.fromisoformat(pos.entry_date)
            exit_dt = date.fromisoformat(exit_date)
            holding_days = (exit_dt - entry_dt).days
        except Exception:
            holding_days = 0

        result = TradeResult(
            symbol=symbol,
            strategy_id=strategy_id,
            entry_date=pos.entry_date,
            exit_date=exit_date,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            qty=pos.qty,
            gross_return=gross_return,
            net_return=net_return,
            holding_days=holding_days,
            entry_order_id=pos.entry_order_id,
            exit_order_id=exit_order_id,
        )

        logger.info(
            "position_closed symbol=%s strategy=%s entry=%.2f exit=%.2f "
            "gross_return=%.2f%% net_return=%.2f%% holding_days=%d",
            symbol, strategy_id, pos.entry_price, exit_price,
            gross_return * 100, net_return * 100, holding_days,
        )

        if trigger_postmortem:
            self._fire_postmortem(result)

        return result

    def get_daily_pnl(self, date: str) -> float:
        """Sum of net_return × entry_price × qty for all trades closed on date."""
        total = 0.0
        for order in self._orders.values():
            if order.status == "FILLED" and order.side == "SELL" and order.filled_at:
                fill_date = order.filled_at[:10]
                if fill_date == date:
                    # Find the matching position entry to compute PnL
                    pass  # TODO: pair BUY/SELL orders for daily P&L
        return total

    # ── Persistence ────────────────────────────────────────────────────────────

    def _save(self) -> None:
        if self._state_file is None:
            return
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "orders": {k: v.to_dict() for k, v in self._orders.items()},
            "positions": {k: v.to_dict() for k, v in self._positions.items()},
        }
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, default=str))
        tmp.replace(self._state_file)

    def _load(self) -> None:
        if self._state_file is None or not self._state_file.exists():
            return
        try:
            state = json.loads(self._state_file.read_text())
            self._orders = {k: Order.from_dict(v) for k, v in state.get("orders", {}).items()}
            self._positions = {
                k: Position.from_dict(v) for k, v in state.get("positions", {}).items()
            }
            logger.info(
                "paper_broker loaded orders=%d positions=%d from %s",
                len(self._orders), len(self._positions), self._state_file,
            )
        except Exception as exc:
            logger.error("Failed to load paper broker state: %s", exc)

    # ── Post-mortem integration ────────────────────────────────────────────────

    def _fire_postmortem(self, result: TradeResult) -> None:
        """Non-blocking post-mortem trigger — logs on failure, never raises."""
        try:
            from quant.agents.post_mortem import run as postmortem_run
            postmortem_run(
                symbol=result.symbol,
                strategy_id=result.strategy_id,
                entry_date=result.entry_date,
                exit_date=result.exit_date,
                entry_price=result.entry_price,
                exit_price=result.exit_price,
                net_return=result.net_return,
                holding_days=result.holding_days,
            )
        except Exception as exc:
            logger.warning("Post-mortem agent failed (non-fatal): %s", exc)
