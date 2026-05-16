"""guard_agent — deterministic kill-switch checks. No LLM involved.

Global kill-switches (block ALL stocks):
  1. India VIX > 22
  2. Nifty 50 down > 1.5% today

Per-stock kill-switches (block individual symbol):
  3. Stock on NSE ASM (Additional Surveillance Measure) list
  4. Stock on NSE GSM (Graded Surveillance Measure) list
  5. Earnings / results announcement within 5 calendar days
  6. Stock already moved > 5% today (news priced in)

Per-stock near-misses (passed but confidence penalised downstream):
  7. Earnings 6-10 days away (−5 pts confidence)

Unimplemented (Phase 6):
  8. Promoter pledging > 30% (requires quarterly SEBI filings)

Populates state.guard_result = {
  passed: [...],
  blocked: [{symbol, reason}, ...],
  near_misses: {symbol: [{type, detail, confidence_penalty}]}
}
"""

import asyncio
import logging
from typing import Any

from .base import BaseAgent
from ..state import TradingState
from ...scrapers.nse_guard import fetch_asm_gsm_symbols, fetch_earnings_within_days

logger = logging.getLogger(__name__)

_VIX_THRESHOLD = 22.0
_VIX_CAUTION = 18.0
_NIFTY_DROP_THRESHOLD = -1.5
_PRICE_MOVE_THRESHOLD = 5.0
_EARNINGS_WINDOW_DAYS = 5
_EARNINGS_NEAR_MISS_DAYS = 10   # 6-10 day window triggers a confidence penalty


def _nse_sym(yf_sym: str) -> str:
    return yf_sym.replace(".NS", "").replace(".BO", "").upper()


async def _fetch_guard_data(
    symbols: list[str],
) -> tuple[set[str], set[str], dict[str, str], dict[str, str]]:
    """Fetch ASM, GSM, 5-day earnings, and 10-day earnings concurrently."""
    asm_gsm_task = fetch_asm_gsm_symbols()
    earnings_5d_task = fetch_earnings_within_days(symbols, days=_EARNINGS_WINDOW_DAYS)
    earnings_10d_task = fetch_earnings_within_days(symbols, days=_EARNINGS_NEAR_MISS_DAYS)
    (asm, gsm), earnings_5d, earnings_10d = await asyncio.gather(
        asm_gsm_task, earnings_5d_task, earnings_10d_task
    )
    return asm, gsm, earnings_5d, earnings_10d


class GuardAgent(BaseAgent):
    name = "guard_agent"

    async def _execute(self, state: TradingState) -> TradingState:
        stocks = list(state.selected_stocks)
        if not stocks:
            return state.model_copy(
                update={"guard_result": {"passed": [], "blocked": [], "near_misses": {}}}
            )

        md = state.market_data
        blocked: list[dict[str, Any]] = []

        # ── Global kill-switches ───────────────────────────────────────────
        vix = md.get("vix")
        if vix is not None and vix > _VIX_THRESHOLD:
            reason = f"India VIX {vix:.1f} > {_VIX_THRESHOLD} — market in fear"
            logger.warning("guard_agent vix_kill_switch vix=%s", vix)
            blocked = [{"symbol": s, "reason": reason} for s in stocks]
            return state.model_copy(
                update={"guard_result": {"passed": [], "blocked": blocked, "near_misses": {}}}
            )

        if vix is not None and _VIX_CAUTION < vix <= _VIX_THRESHOLD:
            logger.info("guard_agent vix_caution vix=%.1f (%.1f–%.1f warning zone)", vix, _VIX_CAUTION, _VIX_THRESHOLD)
            state = state.model_copy(
                update={"market_data": {**state.market_data, "vix_caution": True}}
            )

        nifty_chg = md.get("nifty_change_pct")
        if nifty_chg is not None and nifty_chg < _NIFTY_DROP_THRESHOLD:
            reason = f"Nifty down {nifty_chg:.2f}% today — market selloff"
            logger.warning("guard_agent nifty_kill_switch change=%s", nifty_chg)
            blocked = [{"symbol": s, "reason": reason} for s in stocks]
            return state.model_copy(
                update={"guard_result": {"passed": [], "blocked": blocked, "near_misses": {}}}
            )

        # ── Per-stock kill-switches ────────────────────────────────────────
        asm, gsm, earnings_5d, earnings_10d = await _fetch_guard_data(stocks)

        # Warn if earnings calendar fetch failed — guard is operating blind
        if "_FETCH_FAILED" in earnings_5d:
            logger.warning(
                "guard_agent earnings_5d_calendar_unavailable — cannot block pre-earnings stocks"
            )
            earnings_5d = {}
        if "_FETCH_FAILED" in earnings_10d:
            earnings_10d = {}

        passed: list[str] = []
        near_misses: dict[str, list[dict[str, Any]]] = {}

        for sym in stocks:
            nse_sym = _nse_sym(sym)
            td = state.technical_data.get(sym, {})
            block_reason: str | None = None

            if nse_sym in asm:
                block_reason = f"Stock {nse_sym} is on NSE ASM list"
            elif nse_sym in gsm:
                block_reason = f"Stock {nse_sym} is on NSE GSM list"
            elif nse_sym in earnings_5d:
                block_reason = f"Earnings announcement on {earnings_5d[nse_sym]} (within {_EARNINGS_WINDOW_DAYS}d)"
            else:
                change_pct = td.get("change_pct_today")
                if change_pct is not None and abs(change_pct) > _PRICE_MOVE_THRESHOLD:
                    block_reason = (
                        f"Stock already moved {change_pct:+.1f}% today — news priced in"
                    )

            if block_reason:
                blocked.append({"symbol": sym, "reason": block_reason})
                logger.info("guard_agent blocked symbol=%s reason=%s", sym, block_reason)
            else:
                passed.append(sym)
                # Near-miss: earnings in 6-10 days — passed guard but reduce confidence
                stock_near_misses: list[dict[str, Any]] = []
                if nse_sym in earnings_10d and nse_sym not in earnings_5d:
                    stock_near_misses.append({
                        "type": "earnings_near",
                        "detail": f"Earnings on {earnings_10d[nse_sym]} (6-10d away)",
                        "confidence_penalty": 5,
                    })
                if stock_near_misses:
                    near_misses[sym] = stock_near_misses
                    logger.info(
                        "guard_agent near_miss symbol=%s misses=%s", sym, stock_near_misses
                    )

        logger.info("guard_agent passed=%d blocked=%d near_miss_stocks=%d",
                    len(passed), len(blocked), len(near_misses))
        return state.model_copy(
            update={"guard_result": {"passed": passed, "blocked": blocked, "near_misses": near_misses}}
        )


guard_agent = GuardAgent()
