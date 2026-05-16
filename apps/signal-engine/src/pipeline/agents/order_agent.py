"""order_agent — half-Kelly position sizing; writes paper_orders + virtual_portfolio.

Half-Kelly formula:
  p  = _conservative_win_prob(confidence)  (conservative table — not raw confidence/100)
  q  = 1 - p
  r  = REWARD_TO_RISK                      (reward-to-risk ratio, default 2.0)
  f  = (p*r - q) / r                       (full Kelly fraction)
  f½ = max(0, f/2)                         (half-Kelly — safer, less drawdown)

Win probability uses a static conservative table until paper-trading history
accumulates enough closed trades for empirical calibration via recalibrate_weights.py.

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
from ..sector_map import STOCK_TO_SECTOR
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

# Conservative win-probability table — replaces circular p = confidence/100.
# Intentionally below LLM confidence until empirical calibration data exists.
# Update these values using recalibrate_weights.py after 150+ closed trades.
# Bands are checked from highest to lowest; first match wins.
_WIN_PROB_TABLE: list[tuple[float, float]] = [
    (80.0, 0.60),
    (75.0, 0.57),
    (70.0, 0.54),
    (65.0, 0.52),
    (60.0, 0.50),
]


def _conservative_win_prob(confidence: float) -> float:
    for min_conf, win_prob in _WIN_PROB_TABLE:
        if confidence >= min_conf:
            return win_prob
    return 0.50  # below min threshold — Kelly will return 0 at r=2


def _half_kelly(confidence: float, r: float = REWARD_TO_RISK) -> float:
    p = _conservative_win_prob(confidence)
    q = 1.0 - p
    full_kelly = (p * r - q) / r
    half = max(0.0, full_kelly / 2.0)
    return min(half, MAX_KELLY_CAP)


def _confidence_scale(confidence: float) -> float:
    """Graduated position scale by confidence band.

    Prevents full-size positions at marginal confidence (60-65%) where the
    system's actual win rate may not yet justify the Kelly fraction implied.
    """
    if confidence < 65:
        return 0.50
    if confidence < 70:
        return 0.65
    if confidence < 75:
        return 0.80
    if confidence < 80:
        return 0.90
    return 1.00


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
    scale = _confidence_scale(confidence)
    max_pct_value = portfolio_value * (position_size_pct / 100.0) * scale
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

        # Daily reset: zero the circuit-breaker counter at the start of each trading day
        today = datetime.now(timezone.utc).date().isoformat()
        if portfolio.get("daily_pnl_date", "") != today:
            await portfolio_repo.update_totals({"daily_pnl": 0.0, "daily_pnl_date": today})
            portfolio["daily_pnl"] = 0.0
            portfolio["daily_pnl_date"] = today

        # Circuit breaker: daily loss limit
        daily_loss_pct = await portfolio_repo.get_daily_loss_pct()
        if daily_loss_pct <= -settings.daily_loss_limit_pct:
            logger.warning(
                "order_agent CIRCUIT_BREAKER daily_loss=%.2f%% limit=%.2f%% — halting new orders",
                daily_loss_pct, settings.daily_loss_limit_pct,
            )
            return state

        # Circuit breaker: absolute portfolio floor
        total_value_now = float(portfolio.get("total_value", initial_capital))
        floor_value = initial_capital * (settings.portfolio_floor_pct / 100.0)
        if total_value_now < floor_value:
            logger.warning(
                "order_agent CIRCUIT_BREAKER PORTFOLIO_FLOOR total=%.0f floor=%.0f (%.0f%% of ₹%.0f) — halting",
                total_value_now, floor_value, settings.portfolio_floor_pct, initial_capital,
            )
            return state

        placed_orders: list[dict[str, Any]] = []

        # Fetch sector counts once — updated in-memory after each placement
        sector_counts = await orders_repo.get_open_sector_counts(STOCK_TO_SECTOR)
        max_sector_pos = settings.max_sector_positions

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

            # Sector concentration limit
            symbol_sector = STOCK_TO_SECTOR.get(symbol, "Unknown")
            if sector_counts.get(symbol_sector, 0) >= max_sector_pos:
                logger.info(
                    "order_agent sector_limit symbol=%s sector=%s count=%d limit=%d — skipping",
                    symbol, symbol_sector, sector_counts.get(symbol_sector, 0), max_sector_pos,
                )
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
                "sector": symbol_sector,
            }

            # Write to MongoDB
            await orders_repo.insert_order(order.copy())
            await portfolio_repo.apply_order(position_value)
            await signals_repo.mark_order_placed(state.run_id, symbol)

            # Refresh portfolio state for next iteration
            portfolio["cash"] = cash - position_value
            portfolio["invested"] = float(portfolio.get("invested", 0)) + position_value
            portfolio["open_positions"] = open_positions + 1
            sector_counts[symbol_sector] = sector_counts.get(symbol_sector, 0) + 1

            placed_orders.append(order)
            logger.info(
                "order_agent placed symbol=%s shares=%d value=₹%.0f confidence=%d kelly=%.3f",
                symbol, shares, position_value, confidence, kelly_frac,
            )

        logger.info("order_agent placed_total=%d", len(placed_orders))
        return state.model_copy(update={"orders": placed_orders})


order_agent = OrderAgent()
