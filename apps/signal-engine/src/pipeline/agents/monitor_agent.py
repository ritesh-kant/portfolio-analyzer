"""monitor_agent — thesis-break detection; closes paper positions at stop-loss or target.

Runs independently of the main LangGraph pipeline, triggered every 30 minutes
during NSE market hours (09:15–15:30 IST, Monday–Friday).

For each OPEN paper order it:
  1. Fetches the latest price via yfinance (period="1d", interval="1m").
  2. Checks stop-loss breach: current_price ≤ stop_loss  → close as STOPPED.
  3. Checks target hit:       current_price ≥ target     → close as CLOSED (profit).
  4. Writes exit data back to paper_orders and updates virtual_portfolio.

Returns a summary dict for the /pipeline/monitor response.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import yfinance as yf

from ...db.client import get_db
from ...db.repositories.paper_orders import PaperOrdersRepository
from ...db.repositories.virtual_portfolio import VirtualPortfolioRepository
from ...db.repositories.agent_logs import AgentLogsRepository

logger = logging.getLogger(__name__)

_AGENT_NAME = "monitor_agent"


async def _fetch_price(symbol: str) -> float | None:
    """Fetch latest trade price for a symbol. Returns None on failure."""
    loop = asyncio.get_event_loop()
    try:
        def _download() -> float | None:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1d", interval="1m")
            if hist.empty:
                return None
            return float(hist["Close"].iloc[-1])

        return await loop.run_in_executor(None, _download)
    except Exception as exc:
        logger.warning("monitor price_fetch_failed symbol=%s error=%s", symbol, exc)
        return None


async def _close_position(
    orders_repo: PaperOrdersRepository,
    portfolio_repo: VirtualPortfolioRepository,
    order: dict[str, Any],
    exit_price: float,
    reason: str,
) -> None:
    """Write exit data to paper_orders and release cash back to virtual_portfolio."""
    order_id = order["_id"]  # raw ObjectId — matches MongoDB's _id index
    entry_price = float(order["entry_price"])
    shares = int(order["shares"])
    position_value = float(order["position_value"])

    exit_value = round(shares * exit_price, 2)
    pnl = exit_value - position_value
    return_pct = round((pnl / position_value) * 100, 4) if position_value else 0.0
    was_correct = pnl > 0

    await orders_repo.close_order(order_id, exit_price, return_pct, was_correct)
    logger.info(
        "monitor closed symbol=%s reason=%s entry=%.2f exit=%.2f pnl=%.2f (%.2f%%)",
        order["symbol"], reason, entry_price, exit_price, pnl, return_pct,
    )

    # Return cash and update portfolio stats
    await portfolio_repo.close_position(exit_value, position_value, was_correct)


async def run_monitor() -> dict[str, Any]:
    """Scan all open positions, trigger stops/targets. Returns a summary."""
    db = get_db()
    orders_repo = PaperOrdersRepository(db)
    portfolio_repo = VirtualPortfolioRepository(db)
    logs_repo = AgentLogsRepository(db)

    open_orders = await orders_repo.get_open_orders()
    if not open_orders:
        logger.info("monitor no open positions")
        return {"checked": 0, "closed": 0, "errors": []}

    symbols = list({o["symbol"] for o in open_orders})
    prices = dict(
        zip(
            symbols,
            await asyncio.gather(*[_fetch_price(s) for s in symbols]),
        )
    )

    closed = 0
    errors: list[str] = []

    for order in open_orders:
        symbol = order["symbol"]
        current_price = prices.get(symbol)

        if current_price is None:
            errors.append(f"{symbol}: price fetch failed")
            continue

        stop_loss = float(order.get("stop_loss", 0))
        target = float(order.get("target", float("inf")))
        run_id = order.get("run_id", "monitor")

        try:
            if stop_loss > 0 and current_price <= stop_loss:
                await _close_position(orders_repo, portfolio_repo, order, current_price, "stop_loss")
                await logs_repo.log(run_id, _AGENT_NAME, "info", f"stop_loss triggered {symbol} @ {current_price}")
                closed += 1

            elif current_price >= target:
                await _close_position(orders_repo, portfolio_repo, order, current_price, "target_hit")
                await logs_repo.log(run_id, _AGENT_NAME, "info", f"target_hit {symbol} @ {current_price}")
                closed += 1

        except Exception as exc:
            err = f"{symbol}: {exc!s}"
            errors.append(err)
            logger.error("monitor error %s", err)

    summary = {
        "checked": len(open_orders),
        "closed": closed,
        "errors": errors,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    logger.info("monitor summary checked=%d closed=%d errors=%d", len(open_orders), closed, len(errors))
    return summary
