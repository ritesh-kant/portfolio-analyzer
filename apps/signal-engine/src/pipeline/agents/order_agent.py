"""order_agent — half-Kelly position sizing; writes paper_orders + virtual_portfolio.

Half-Kelly formula:
  p  = confidence / 100              (estimated win probability)
  q  = 1 - p
  r  = REWARD_TO_RISK                (reward-to-risk ratio, default 2.0)
  f  = (p*r - q) / r                 (full Kelly fraction)
  f½ = max(0, f/2)                   (half-Kelly — safer, less drawdown)

Position value = portfolio_total_value × f½
  capped at position_size_pct% of portfolio (config: 12%)
  and capped at available cash.

Stop-loss: entry × (1 - STOP_PCT)   default 5%
Target:    entry × (1 + TARGET_PCT)  default 10%

Only signals meeting min_signal_confidence threshold are traded.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from .base import BaseAgent
from ..state import TradingState
from ...config import Settings
from ...db.client import get_db
from ...db.repositories.paper_orders import PaperOrdersRepository
from ...db.repositories.virtual_portfolio import VirtualPortfolioRepository
from ...db.repositories.trading_signals import TradingSignalsRepository

logger = logging.getLogger(__name__)

REWARD_TO_RISK = 2.0   # 10% target / 5% stop
STOP_PCT = 0.05
TARGET_PCT = 0.10
MAX_KELLY_CAP = 0.25   # never risk more than 25% on a single trade


def _half_kelly(confidence: float, r: float = REWARD_TO_RISK) -> float:
    p = confidence / 100.0
    q = 1.0 - p
    full_kelly = (p * r - q) / r
    half = max(0.0, full_kelly / 2.0)
    return min(half, MAX_KELLY_CAP)


def _calc_position(
    confidence: float,
    portfolio_value: float,
    entry_price: float,
    position_size_pct: float,
    available_cash: float,
) -> tuple[int, float, float]:
    """Return (shares, position_value, kelly_fraction) or (0, 0.0, 0.0) if not feasible."""
    if entry_price <= 0 or portfolio_value <= 0:
        return 0, 0.0, 0.0

    kf = _half_kelly(confidence)
    max_pct_value = portfolio_value * (position_size_pct / 100.0)
    target_value = min(portfolio_value * kf, max_pct_value, available_cash)

    shares = int(target_value // entry_price)
    if shares < 1:
        return 0, 0.0, kf

    actual_value = shares * entry_price
    return shares, actual_value, kf


class OrderAgent(BaseAgent):
    name = "order_agent"

    async def _execute(self, state: TradingState) -> TradingState:
        settings = Settings()
        min_confidence = settings.min_signal_confidence
        position_size_pct = settings.position_size_pct
        initial_capital = settings.virtual_portfolio_initial

        actionable = [
            s for s in state.signals
            if s.get("meets_threshold") and s.get("entry_price", 0) > 0
        ]
        if not actionable:
            logger.info("order_agent no actionable signals — skipping")
            return state

        db = get_db()
        portfolio_repo = VirtualPortfolioRepository(db)
        orders_repo = PaperOrdersRepository(db)
        signals_repo = TradingSignalsRepository(db)

        # Ensure portfolio document exists
        await portfolio_repo.ensure_exists(initial_capital)
        portfolio = await portfolio_repo.get()
        if portfolio is None:
            logger.error("order_agent portfolio missing after ensure_exists")
            return state

        placed_orders: list[dict[str, Any]] = []
        today = datetime.now(timezone.utc).date().isoformat()

        for signal in actionable:
            symbol = signal["symbol"]
            confidence = signal["confidence"]
            entry_price = signal["entry_price"]

            cash = float(portfolio.get("cash", 0))
            total_value = float(portfolio.get("total_value", initial_capital))
            open_positions = int(portfolio.get("open_positions", 0))

            # Respect max_positions cap
            if open_positions >= settings.max_positions:
                logger.info(
                    "order_agent max_positions=%d reached — skipping %s",
                    settings.max_positions, symbol,
                )
                continue

            # Skip if an open position for this symbol already exists
            if await orders_repo.has_open_position(symbol):
                logger.info("order_agent duplicate_position symbol=%s already OPEN — skipping", symbol)
                continue

            shares, position_value, kelly_frac = _calc_position(
                confidence, total_value, entry_price, position_size_pct, cash
            )
            if shares < 1:
                logger.info(
                    "order_agent insufficient_cash symbol=%s entry=%.2f cash=%.2f",
                    symbol, entry_price, cash,
                )
                continue

            stop_loss = round(entry_price * (1 - STOP_PCT), 2)
            target = round(entry_price * (1 + TARGET_PCT), 2)

            order: dict[str, Any] = {
                "run_id": state.run_id,
                "symbol": symbol,
                "direction": "BUY",
                "mode": settings.trading_mode,
                "entry_price": round(entry_price, 2),
                "shares": shares,
                "position_value": round(position_value, 2),
                "confidence": confidence,
                "kelly_fraction": round(kelly_frac, 4),
                "stop_loss": stop_loss,
                "target": target,
                "date": state.date or today,
                "status": "OPEN",
                "reasoning": signal.get("reasoning", ""),
            }

            # Write to MongoDB
            await orders_repo.insert_order(order.copy())
            await portfolio_repo.apply_order(position_value)
            await signals_repo.mark_order_placed(state.run_id, symbol)

            # Refresh portfolio state for next iteration
            portfolio["cash"] = cash - position_value
            portfolio["invested"] = float(portfolio.get("invested", 0)) + position_value
            portfolio["open_positions"] = open_positions + 1

            placed_orders.append(order)
            logger.info(
                "order_agent placed symbol=%s shares=%d value=₹%.0f confidence=%d kelly=%.3f",
                symbol, shares, position_value, confidence, kelly_frac,
            )

        logger.info("order_agent placed_total=%d", len(placed_orders))
        return state.model_copy(update={"orders": placed_orders})


order_agent = OrderAgent()
