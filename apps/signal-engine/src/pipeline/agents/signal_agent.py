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
from datetime import datetime, timezone
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

PROMPT_VERSION = "2.0.0"

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
You are a quantitative stock analyst for Indian equity markets (NSE/BSE).
Your job is to score the SHORT-TERM (1-5 day) bullish conviction for a stock given confirmed technical signals.

CALIBRATION — use this scale strictly:
  0-2 : Very weak. Conflicting signals or overbought. Do NOT score > 2 if RSI > 75.
  3-4 : Weak. Only 1-2 signals confirmed, no momentum.
  5-6 : Moderate. 3-4 signals confirmed, neutral market backdrop.
  7-8 : Strong. 5+ signals confirmed AND sector tailwind AND positive market.
  9-10: Exceptional. Reserved for rare cases: all 8 base signals firing + major catalyst.
       Score 9+ only when VIX < 15, Nifty trending up 5-day, and a direct stock catalyst exists.

HARD RULES (override everything else):
  - RSI > 75: cap your score at 2. The setup is overbought; mean reversion risk is high.
  - VIX > 18: subtract 2 from your raw score (market uncertainty elevated).
  - Fewer than 3 confirmed signals: cap at 4 regardless of news.

REASONING FORMAT — you must think step by step before scoring:
  1. List which signals are confirmed and their reliability.
  2. Note any red flags (overbought, high VIX, weak volume).
  3. State your raw score BEFORE applying hard rules.
  4. Apply hard rules and state your final score.

Output ONLY valid JSON — no prose outside the JSON object.
"""

_LLM_USER_TMPL = """\
Stock: {symbol}
Sector: {sector}
N confirmed signals: {n_signals} of 8

Technical snapshot:
  Close: ₹{close}  |  5-day return: {five_day_ret:+.2f}%
  RSI(14): {rsi} ({rsi_interp})
  MACD histogram: {macd_hist} ({macd_interp})
  EMA20: ₹{ema20} ({ema20_interp})  |  EMA50: ₹{ema50} ({ema50_interp})
  Volume ratio: {vol_ratio}× 20-day avg ({vol_interp})

Market context:
  Nifty today: {nifty_chg:+.2f}%  |  Nifty 5-day: {nifty_5d:+.2f}%
  VIX: {vix}  |  FII flows: {fii_display}

Confirmed signals:
  {signals}

News catalyst: {news_catalyst}

Historical context: {hist_context}

Apply the calibration scale. Think step by step. Then output:
{{"bonus_score": <0-10>, "reasoning": "<2 concise sentences max>", "direction": "BUY"}}
"""


def _news_age_decay(article: dict[str, Any]) -> float:
    """Return a multiplier (0.5–1.0) based on article age. Older = less credit."""
    published = article.get("published_at")
    if not isinstance(published, datetime):
        return 1.0
    age_hours = (datetime.now(timezone.utc) - published).total_seconds() / 3600
    if age_hours <= 4:
        return 1.0
    if age_hours <= 12:
        return 0.75
    return 0.5


def _news_catalyst_score(
    symbol: str,
    classified_news: list[dict[str, Any]],
) -> tuple[int, str]:
    """Return (pts, description) for the strongest positive news catalyst found.

    Tier A (up to 14 pts): article directly names this stock.
    Tier B  (up to 8 pts): article explicitly tags the stock's sector.
    Both tiers apply age decay: articles > 4h get 75%, > 12h get 50% of full points.
    """
    nse_sym = symbol.replace(".NS", "").replace(".BO", "").upper()
    stock_sector = _STOCK_TO_SECTOR.get(symbol)

    # Tier A: direct stock-specific catalyst
    for art in classified_news:
        if art.get("sentiment") != "positive":
            continue
        affected = [s.upper() for s in (art.get("affected_stocks") or [])]
        if any(nse_sym in s for s in affected) or nse_sym in (art.get("headline") or "").upper():
            summary = art.get("summary") or art["headline"][:60]
            pts = round(14 * _news_age_decay(art))
            return pts, summary

    # Tier B: sector-tagged article (reduced weight — indirect catalyst)
    if stock_sector:
        for art in classified_news:
            if art.get("sentiment") != "positive":
                continue
            art_sectors = [s.upper() for s in (art.get("affected_sectors") or [])]
            if any(stock_sector.upper() in s for s in art_sectors):
                summary = art.get("summary") or art["headline"][:60]
                pts = round(8 * _news_age_decay(art))
                return pts, summary

    return 0, ""


def _score_base(
    symbol: str,
    td: dict[str, Any],
    market_data: dict[str, Any],
    sectors: list[dict[str, Any]],
    classified_news: list[dict[str, Any]],
) -> tuple[int, list[str], list[str]]:
    """Compute base score, triggered signals, and near-miss weak signals."""
    score = 0
    triggered: list[str] = []
    weak: list[str] = []  # signals that nearly fired but didn't

    # 1. RSI
    rsi = td.get("rsi", 50.0)
    if rsi < 40:
        score += 12
        triggered.append(f"RSI {rsi:.1f} < 40 (oversold)")
    elif rsi > 75:
        triggered.append(f"⚠ RSI {rsi:.1f} > 75 (overbought — elevated reversal risk)")
    elif 40 <= rsi <= 50:
        weak.append(f"RSI {rsi:.1f} (near oversold, threshold 40)")

    # 2. MACD histogram positive
    macd_hist = td.get("macd_hist", 0)
    if macd_hist > 0:
        score += 12
        triggered.append(f"MACD hist {macd_hist:.4f} > 0")
    elif -0.05 <= macd_hist <= 0:
        weak.append(f"MACD hist {macd_hist:.4f} (near zero, bearish by thin margin)")

    # 3 & 4. EMA trend
    close = td.get("close", 0)
    ema20 = td.get("ema20", close)
    ema50 = td.get("ema50", close)
    if td.get("above_ema20"):
        score += 10
        triggered.append(f"Price ₹{close:.0f} > EMA20 ₹{ema20:.0f}")
    elif close > 0 and ema20 > 0 and (ema20 - close) / ema20 < 0.01:
        weak.append(f"Price ₹{close:.0f} within 1% below EMA20 ₹{ema20:.0f}")
    if td.get("above_ema50"):
        score += 12
        triggered.append(f"Price ₹{close:.0f} > EMA50 ₹{ema50:.0f}")
    elif close > 0 and ema50 > 0 and (ema50 - close) / ema50 < 0.015:
        weak.append(f"Price ₹{close:.0f} within 1.5% below EMA50 ₹{ema50:.0f}")

    # 5. Volume elevated
    vol_ratio = td.get("volume_ratio", 1.0)
    if vol_ratio > 1.5:
        score += 10
        triggered.append(f"Volume {vol_ratio:.1f}× average")
    elif 1.2 <= vol_ratio <= 1.5:
        weak.append(f"Volume {vol_ratio:.1f}× (near elevated threshold 1.5×)")

    # 6. News catalyst
    news_pts, news_desc = _news_catalyst_score(symbol, classified_news)
    if news_pts > 0:
        score += news_pts
        triggered.append(f"News catalyst ({news_pts}pts): {news_desc}")

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
    else:
        near_sector = next(
            (s for s in sectors
             if s.get("name") == stock_sector
             and s.get("direction") == "bullish"
             and 40 <= s.get("score", 0) <= 60),
            None,
        )
        if near_sector:
            weak.append(
                f"Sector {stock_sector} mildly bullish (score {near_sector['score']}, threshold 60)"
            )

    # 8. Market positive — treat missing FII as neutral, not as zero
    nifty_chg = market_data.get("nifty_change_pct", 0) or 0
    fii_raw = market_data.get("fii_net_crore")
    if nifty_chg > 0 and fii_raw is not None and fii_raw > 0:
        score += 8
        triggered.append(f"Market positive (Nifty {nifty_chg:+.2f}%, FII ₹{fii_raw:.0f}cr)")
    elif nifty_chg > 0 and fii_raw is None:
        triggered.append(f"Nifty {nifty_chg:+.2f}% (FII data unavailable — market signal neutral)")
    elif nifty_chg > 0:
        score += 4
        triggered.append(f"Nifty {nifty_chg:+.2f}% (FII net selling ₹{fii_raw:.0f}cr)")
    elif -0.5 <= nifty_chg <= 0:
        weak.append(f"Nifty {nifty_chg:+.2f}% (flat, market not positive)")

    return score, triggered, weak


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
    fii_raw = market_data.get("fii_net_crore")

    rsi = td.get("rsi", 50.0)
    macd_hist = td.get("macd_hist", 0.0)
    close = td.get("close", 0.0)
    ema20 = td.get("ema20", close)
    ema50 = td.get("ema50", close)
    vol_ratio = td.get("volume_ratio", 1.0)

    rsi_interp = (
        "oversold" if rsi < 40
        else "overbought — hard cap applies" if rsi > 75
        else "neutral"
    )
    macd_interp = "bullish momentum" if macd_hist > 0 else "bearish momentum"
    ema20_interp = "above — short-term uptrend" if close > ema20 else "below — short-term downtrend"
    ema50_interp = "above — medium-term uptrend" if close > ema50 else "below — medium-term downtrend"
    vol_interp = "elevated — strong participation" if vol_ratio > 1.5 else "average or low"

    news_catalyst = next(
        (s for s in triggered if s.startswith("News catalyst")),
        "none identified",
    )
    n_signals = sum(
        1 for s in triggered if not s.startswith("⚠") and not s.startswith("Nifty")
    )

    vix = market_data.get("vix")
    nifty_5d = market_data.get("nifty_5d_return") or 0.0
    hist_context = "No prior outcome data available."

    prompt = _LLM_USER_TMPL.format(
        symbol=symbol,
        sector=stock_sector,
        n_signals=n_signals,
        close=close,
        five_day_ret=td.get("five_day_ret", 0.0),
        rsi=rsi,
        rsi_interp=rsi_interp,
        macd_hist=macd_hist,
        macd_interp=macd_interp,
        ema20=ema20,
        ema20_interp=ema20_interp,
        ema50=ema50,
        ema50_interp=ema50_interp,
        vol_ratio=vol_ratio,
        vol_interp=vol_interp,
        nifty_chg=market_data.get("nifty_change_pct") or 0.0,
        nifty_5d=nifty_5d,
        vix=vix if vix is not None else "N/A",
        fii_display=f"₹{fii_raw:.0f}cr" if fii_raw is not None else "unavailable",
        signals="; ".join(triggered) or "none",
        news_catalyst=news_catalyst,
        hist_context=hist_context,
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

            # Skip stale data (flagged by technical_agent on market holidays)
            if td.get("stale"):
                logger.warning(
                    "signal_agent stale_data symbol=%s last_date=%s — skipping",
                    symbol, td.get("last_data_date"),
                )
                continue

            base_score, triggered, weak_signals = _score_base(
                symbol, td, state.market_data, state.sectors, state.classified_news
            )

            bonus = 0
            reasoning = ""
            # Guardrail: require base_score ≥ 44 (~4 signals) before calling LLM.
            # Prevents the LLM from single-handedly elevating weak setups to tradeable.
            if state.llm is not None and triggered and base_score >= 44:
                bonus, reasoning = await _llm_bonus(
                    state.llm, symbol, td, state.market_data, state.sectors, triggered
                )
            elif state.llm is not None and base_score < 44:
                logger.info(
                    "signal_agent llm_bonus_skipped symbol=%s base_score=%d (min 44 required)",
                    symbol, base_score,
                )

            confidence = min(base_score + bonus, _TOTAL_MAX)

            # Hard cap: RSI > 75 (overbought)
            if td.get("rsi", 50.0) > 75:
                confidence = min(confidence, 68)

            # Soft cap: VIX in caution zone (18–22) — reduce by 5 pts
            if state.market_data.get("vix_caution"):
                confidence = max(0, confidence - 5)

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
                "prompt_version": PROMPT_VERSION,
                "weak_signals": weak_signals,
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
