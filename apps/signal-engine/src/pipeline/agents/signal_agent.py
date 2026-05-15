"""signal_agent — 9-signal weighted confluence scoring + LLM reasoning per stock.

Signal weights (total = 100):
  1. RSI < 40 (oversold)                          12 pts
  2. MACD histogram > 0 (momentum positive)       12 pts
  3. Price above EMA20 (short-term trend)          10 pts
  4. Price above EMA50 (medium-term trend)         12 pts
  5. Volume ratio > 1.5× average                  10 pts
  6. Positive news catalyst for stock or sector   14 pts
  7. Sector bullish (direction=bullish, score>60)  12 pts
  8. Market positive (Nifty up + FII buying)        8 pts
  9. LLM reasoning bonus                       0-10 pts
                                              --------
  Max                                           100 pts

Signals with base score >= min_signal_confidence (default 60) pass to order_agent.
"""

import asyncio
import logging
from typing import Any

from .base import BaseAgent
from ..state import TradingState
from ...config import Settings
from ...db.client import get_db
from ...db.repositories.trading_signals import TradingSignalsRepository
from ...db.repositories.stock_analyses import StockAnalysesRepository
from ...providers.llm_utils import call_llm_json
from ...scrapers.sector_stocks import SECTOR_STOCKS

logger = logging.getLogger(__name__)

# Reverse map: symbol → sector name
_STOCK_TO_SECTOR: dict[str, str] = {
    stock: sector
    for sector, stocks in SECTOR_STOCKS.items()
    for stock in stocks
}

_SIGNAL_DEFINITIONS: list[tuple[str, int, str]] = [
    ("rsi_oversold",      12, "RSI < 40 (oversold)"),
    ("macd_positive",     12, "MACD histogram > 0"),
    ("above_ema20",       10, "Price above EMA20"),
    ("above_ema50",       12, "Price above EMA50"),
    ("volume_elevated",   10, "Volume ratio > 1.5×"),
    ("news_positive",     14, "Positive news catalyst"),
    ("sector_bullish",    12, "Sector bullish"),
    ("market_positive",    8, "Market positive"),
    ("llm_bonus",         10, "LLM reasoning bonus"),
]
_BASE_MAX = sum(w for _, w, _ in _SIGNAL_DEFINITIONS[:-1])  # 90 without LLM
_TOTAL_MAX = 100

_LLM_SYSTEM = """\
You are a quantitative stock analyst for Indian equity markets.
Given confirmed technical signals for a stock, assess the overall bullish conviction.
Output ONLY valid JSON — no prose.
"""

_LLM_USER_TMPL = """\
Stock: {symbol}
Sector: {sector}
Confirmed signals: {signals}
Technical snapshot:
  RSI: {rsi}  MACD hist: {macd_hist}  Close: ₹{close}
  EMA20: ₹{ema20}  EMA50: ₹{ema50}  Volume ratio: {vol_ratio}×
Market: Nifty {nifty_chg:+.2f}%  VIX: {vix}  FII: ₹{fii} cr

On a scale 0-10, how strong is the bullish case?
Output JSON: {{"bonus_score": <0-10>, "reasoning": "<2 concise sentences>", "direction": "BUY"}}
"""


def _has_positive_news(
    symbol: str,
    sectors: list[dict[str, Any]],
    classified_news: list[dict[str, Any]],
) -> bool:
    """Return True if any positive article mentions this stock or its sector."""
    nse_sym = symbol.replace(".NS", "").replace(".BO", "").upper()
    stock_sector = _STOCK_TO_SECTOR.get(symbol)
    bullish_sector_names = {
        s["name"] for s in sectors if s.get("direction") == "bullish"
    }

    for art in classified_news:
        if art.get("sentiment") != "positive":
            continue
        # Direct stock mention
        affected = [s.upper() for s in (art.get("affected_stocks") or [])]
        if any(nse_sym in s for s in affected):
            return True
        # Headline contains symbol
        if nse_sym in (art.get("headline") or "").upper():
            return True
        # Sector match
        art_sectors = [s.upper() for s in (art.get("affected_sectors") or [])]
        if stock_sector and any(stock_sector.upper() in s for s in art_sectors):
            return True
        # Sector is bullish and article is about that sector area
        if stock_sector in bullish_sector_names and art_sectors:
            return True
    return False


def _score_base(
    symbol: str,
    td: dict[str, Any],
    market_data: dict[str, Any],
    sectors: list[dict[str, Any]],
    classified_news: list[dict[str, Any]],
) -> tuple[int, list[str]]:
    """Compute base (non-LLM) score and list of triggered signal descriptions."""
    score = 0
    triggered: list[str] = []

    # 1. RSI oversold
    rsi = td.get("rsi", 50.0)
    if rsi < 40:
        score += 12
        triggered.append(f"RSI {rsi:.1f} < 40 (oversold)")

    # 2. MACD histogram positive
    if td.get("macd_hist", 0) > 0:
        score += 12
        triggered.append(f"MACD hist {td.get('macd_hist', 0):.4f} > 0")

    # 3 & 4. EMA trend
    if td.get("above_ema20"):
        score += 10
        triggered.append(f"Price ₹{td.get('close',0):.0f} > EMA20 ₹{td.get('ema20',0):.0f}")
    if td.get("above_ema50"):
        score += 12
        triggered.append(f"Price ₹{td.get('close',0):.0f} > EMA50 ₹{td.get('ema50',0):.0f}")

    # 5. Volume elevated
    vol_ratio = td.get("volume_ratio", 1.0)
    if vol_ratio > 1.5:
        score += 10
        triggered.append(f"Volume {vol_ratio:.1f}× average")

    # 6. Positive news
    if _has_positive_news(symbol, sectors, classified_news):
        score += 14
        triggered.append("Positive news catalyst")

    # 7. Sector bullish
    stock_sector = _STOCK_TO_SECTOR.get(symbol)
    sector_entry = next(
        (s for s in sectors
         if s.get("name") == stock_sector
         and s.get("direction") == "bullish"
         and s.get("score", 0) > 60),
        None,
    )
    if sector_entry:
        score += 12
        triggered.append(
            f"Sector {stock_sector} bullish (score {sector_entry['score']})"
        )

    # 8. Market positive
    nifty_chg = market_data.get("nifty_change_pct", 0) or 0
    fii = market_data.get("fii_net_crore") or 0
    if nifty_chg > 0 and fii > 0:
        score += 8
        triggered.append(f"Market positive (Nifty {nifty_chg:+.2f}%, FII ₹{fii:.0f}cr)")
    elif nifty_chg > 0:
        score += 4
        triggered.append(f"Nifty {nifty_chg:+.2f}%")

    return score, triggered


async def _llm_bonus(
    llm: Any,
    symbol: str,
    td: dict[str, Any],
    market_data: dict[str, Any],
    sectors: list[dict[str, Any]],
    triggered: list[str],
) -> tuple[int, str]:
    """Ask the LLM for a 0-10 bonus and 2-sentence reasoning. Returns (bonus, reasoning)."""
    stock_sector = _STOCK_TO_SECTOR.get(symbol, "Unknown")
    prompt = _LLM_USER_TMPL.format(
        symbol=symbol,
        sector=stock_sector,
        signals="; ".join(triggered) or "none",
        rsi=td.get("rsi", 50),
        macd_hist=td.get("macd_hist", 0),
        close=td.get("close", 0),
        ema20=td.get("ema20", 0),
        ema50=td.get("ema50", 0),
        vol_ratio=td.get("volume_ratio", 1),
        nifty_chg=market_data.get("nifty_change_pct") or 0,
        vix=market_data.get("vix") or "N/A",
        fii=market_data.get("fii_net_crore") or 0,
    )
    result = await call_llm_json(llm, _LLM_SYSTEM, prompt)
    if isinstance(result, dict):
        bonus = max(0, min(10, int(result.get("bonus_score", 0))))
        reasoning = str(result.get("reasoning", ""))[:400]
        return bonus, reasoning
    return 0, ""


class SignalAgent(BaseAgent):
    name = "signal_agent"

    async def _execute(self, state: TradingState) -> TradingState:
        passed_symbols = state.guard_result.get("passed", [])
        if not passed_symbols:
            logger.info("signal_agent no passed symbols — skipping")
            return state

        settings = Settings()
        min_confidence = settings.min_signal_confidence
        db = get_db()
        signals_repo = TradingSignalsRepository(db)
        analyses_repo = StockAnalysesRepository(db)

        # Score each passed symbol concurrently (LLM calls serialised below)
        all_signals: list[dict[str, Any]] = []

        for symbol in passed_symbols:
            td = state.technical_data.get(symbol, {})
            if not td:
                logger.warning("signal_agent no technical_data for %s — skipping", symbol)
                continue

            base_score, triggered = _score_base(
                symbol, td, state.market_data, state.sectors, state.classified_news
            )

            bonus = 0
            reasoning = ""
            if state.llm is not None and triggered:
                bonus, reasoning = await _llm_bonus(
                    state.llm, symbol, td, state.market_data, state.sectors, triggered
                )

            confidence = min(base_score + bonus, _TOTAL_MAX)

            signal: dict[str, Any] = {
                "run_id": state.run_id,
                "date": state.date,
                "symbol": symbol,
                "direction": "BUY",
                "confidence": confidence,
                "base_score": base_score,
                "llm_bonus": bonus,
                "triggered_signals": triggered,
                "reasoning": reasoning,
                "entry_price": td.get("close", 0),
                "rsi": td.get("rsi"),
                "macd_hist": td.get("macd_hist"),
                "above_ema20": td.get("above_ema20"),
                "above_ema50": td.get("above_ema50"),
                "volume_ratio": td.get("volume_ratio"),
                "meets_threshold": confidence >= min_confidence,
            }
            all_signals.append(signal)

            # Persist signal + analysis
            await signals_repo.insert_signal(signal.copy())
            await analyses_repo.upsert(state.run_id, symbol, {
                "run_id": state.run_id,
                "symbol": symbol,
                "confidence": confidence,
                "direction": "BUY",
                "triggered_signals": triggered,
                "reasoning": reasoning,
                "technical_data": td,
                "market_data": state.market_data,
            })

            logger.info(
                "signal_agent scored symbol=%s confidence=%d triggered=%d meets=%s",
                symbol, confidence, len(triggered), confidence >= min_confidence,
            )

        actionable = [s for s in all_signals if s["meets_threshold"]]
        logger.info(
            "signal_agent total=%d actionable=%d",
            len(all_signals), len(actionable),
        )
        return state.model_copy(update={"signals": all_signals})


signal_agent = SignalAgent()
