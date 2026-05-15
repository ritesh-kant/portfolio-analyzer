"""guard_agent — deterministic kill-switch checks. No LLM involved.

Global kill-switches (block ALL stocks):
  1. India VIX > 22
  2. Nifty 50 down > 1.5% today

Per-stock kill-switches (block individual symbol):
  3. Stock on NSE ASM (Additional Surveillance Measure) list
  4. Stock on NSE GSM (Graded Surveillance Measure) list
  5. Earnings / results announcement within 5 calendar days
  6. Stock already moved > 5% today (news priced in)

Unimplemented (Phase 6):
  7. Promoter pledging > 30% (requires quarterly SEBI filings)

Populates state.guard_result = { passed: [...], blocked: [{symbol, reason}, ...] }
"""

import asyncio
import logging
from typing import Any

from .base import BaseAgent
from ..state import TradingState
from ...scrapers.nse_guard import fetch_asm_gsm_symbols, fetch_earnings_within_days

logger = logging.getLogger(__name__)

_VIX_THRESHOLD = 22.0
_NIFTY_DROP_THRESHOLD = -1.5   # percent
_PRICE_MOVE_THRESHOLD = 5.0    # percent (absolute)
_EARNINGS_WINDOW_DAYS = 5


def _nse_sym(yf_sym: str) -> str:
    """Strip .NS suffix to get the bare NSE symbol."""
    return yf_sym.replace(".NS", "").replace(".BO", "").upper()


async def _fetch_guard_data(symbols: list[str]) -> tuple[set[str], set[str], dict[str, str]]:
    """Fetch ASM, GSM, and earnings data concurrently."""
    asm_gsm_task = fetch_asm_gsm_symbols()
    earnings_task = fetch_earnings_within_days(symbols, days=_EARNINGS_WINDOW_DAYS)
    (asm, gsm), earnings = await asyncio.gather(asm_gsm_task, earnings_task)
    return asm, gsm, earnings


class GuardAgent(BaseAgent):
    name = "guard_agent"

    async def _execute(self, state: TradingState) -> TradingState:
        stocks = list(state.selected_stocks)
        if not stocks:
            return state.model_copy(
                update={"guard_result": {"passed": [], "blocked": []}}
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
                update={"guard_result": {"passed": [], "blocked": blocked}}
            )

        nifty_chg = md.get("nifty_change_pct")
        if nifty_chg is not None and nifty_chg < _NIFTY_DROP_THRESHOLD:
            reason = f"Nifty down {nifty_chg:.2f}% today — market selloff"
            logger.warning("guard_agent nifty_kill_switch change=%s", nifty_chg)
            blocked = [{"symbol": s, "reason": reason} for s in stocks]
            return state.model_copy(
                update={"guard_result": {"passed": [], "blocked": blocked}}
            )

        # ── Per-stock kill-switches ────────────────────────────────────────
        asm, gsm, earnings = await _fetch_guard_data(stocks)

        passed: list[str] = []
        for sym in stocks:
            nse_sym = _nse_sym(sym)
            td = state.technical_data.get(sym, {})
            block_reason: str | None = None

            if nse_sym in asm:
                block_reason = f"Stock {nse_sym} is on NSE ASM list"
            elif nse_sym in gsm:
                block_reason = f"Stock {nse_sym} is on NSE GSM list"
            elif nse_sym in earnings:
                block_reason = f"Earnings announcement on {earnings[nse_sym]} (within {_EARNINGS_WINDOW_DAYS}d)"
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

        logger.info(
            "guard_agent passed=%d blocked=%d",
            len(passed),
            len(blocked),
        )
        return state.model_copy(
            update={"guard_result": {"passed": passed, "blocked": blocked}}
        )


guard_agent = GuardAgent()
