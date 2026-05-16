"""signal_agent — 10-signal weighted confluence scoring + LLM reasoning per stock.

Signal weights (total ~100):
  1. RSI < 40 (oversold)                          12 pts
  2. MACD histogram > 0 (momentum positive)       12 pts
  3. Price above EMA20 (short-term trend)          10 pts
  4. Price above EMA50 (medium-term trend)         10 pts
  5. Volume ratio > 1.5× average                  10 pts
  6. Positive news catalyst for stock or sector   14 pts
  7. Sector bullish (direction=bullish, score>60)  12 pts
  8. Market positive (Nifty up + FII buying)        8 pts
  9. Near 75-day high (breakout proximity)          8 pts  [new]
 10. LLM reasoning bonus                       0-10 pts
                                              --------
  Max                                           106 pts → capped at 100

Signals with confidence >= min_signal_confidence (default 60) pass to order_agent.

Near-miss signals (reduce LLM bonus if present):
  - Earnings 6-10 days away (−5 pts applied by signal_agent after LLM call)
  - FII net selling discounts news catalyst weight by 30%
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
from ..sector_map import STOCK_TO_SECTOR as _STOCK_TO_SECTOR

logger = logging.getLogger(__name__)

PROMPT_VERSION = "3.0.0"

_SIGNAL_DEFINITIONS: list[tuple[str, int, str]] = [
    ("rsi_oversold",      12, "RSI < 40 (oversold)"),
    ("macd_positive",     12, "MACD histogram > 0"),
    ("above_ema20",       10, "Price above EMA20"),
    ("above_ema50",       10, "Price above EMA50"),
    ("volume_elevated",   10, "Volume ratio > 1.5×"),
    ("news_positive",     14, "Positive news catalyst"),
    ("sector_bullish",    12, "Sector bullish"),
    ("market_positive",    8, "Market positive"),
    ("near_75d_high",      8, "Near 75-day high (breakout proximity)"),
    ("llm_bonus",         10, "LLM reasoning bonus"),
]
_BASE_MAX = sum(w for _, w, _ in _SIGNAL_DEFINITIONS[:-1])  # 96 without LLM
_TOTAL_MAX = 100

_LLM_SYSTEM = """\
You are a quantitative stock analyst for Indian equity markets (NSE/BSE).
A rule-based system has already scored a stock on deterministic signals (base_score shown below).
Your job is to award a 0–10 BONUS based on the quality and reliability of those signals —
not to re-score the signals themselves.

BONUS SCALE (additive — added on top of base_score):
  0–2: Signals present but unreliable, stale, or a hard rule prevents higher.
       Use when: RSI overbought, volume spike with no news, news is vague or >12h old.
  3–4: Setup has meaningful uncertainty. Sector tailwind weak, news indirect (sector-level only).
  5–6: Clean setup. Signals consistent with each other. Direct but not major catalyst.
  7–8: High-quality setup. Stock-specific Tier-1 catalyst + 5+ signals + VIX < 18.
  9–10: Exceptional — all signals firing + breaking Tier-1 catalyst + VIX < 15 + Nifty uptrend.

HARD RULES (override your bonus):
  - RSI > 75: bonus cannot exceed 2. Mean reversion risk is acute.
  - VIX > 18: subtract 2 from your raw bonus.
  - News catalyst is sector-level only (not stock-specific): bonus cannot exceed 5.
  - Fewer than 3 confirmed signals: bonus cannot exceed 3 (base_score already low).
  - Historical outcomes show ≥2 of last 3 signals on this stock were WRONG: reduce bonus by 2.

THINK STEP BY STEP:
  Step 1: What is the strongest confirmed signal? Is it reliable or borderline?
  Step 2: Is there a direct stock-specific catalyst, or only a sector-level one?
  Step 3: Are there conflicting signals or hidden risks (overbought, high VIX, earnings near)?
  Step 4: What do the historical outcomes say about this stock's recent signal quality?
  Step 5: State raw bonus, apply hard rules, output final bonus.

Output ONLY valid JSON — no prose outside the JSON object.
"""

_LLM_USER_TMPL = """\
Stock: {symbol}
Sector: {sector} | Sector direction: {sector_direction} (sector confidence: {sector_score}/100)
Base score already computed: {base_score}/96 ({n_signals} of 9 signals confirmed)

Technical snapshot:
  Close: ₹{close}  |  5-day return: {five_day_ret:+.2f}%
  RSI(14): {rsi} ({rsi_interp})
  MACD histogram: {macd_hist} ({macd_interp})
  EMA20: ₹{ema20} ({ema20_interp})  |  EMA50: ₹{ema50} ({ema50_interp})
  BB position: {bb_pct:.2f} (0=lower band, 1=upper band)
  Volume ratio: {vol_ratio}× 20-day avg ({vol_interp})

Market context:
  Nifty regime: {nifty_regime}
  Nifty today: {nifty_chg:+.2f}%  |  Nifty 5-day: {nifty_5d:+.2f}%  |  30-day: {nifty_30d:+.1f}%
  VIX: {vix}  |  FII flows: {fii_display}

Confirmed signals:
  {signals}

Near-miss signals (didn't fire, borderline):
  {weak_signals_str}

News catalyst: {news_catalyst}

Historical outcomes on this stock: {hist_context}

Apply the bonus scale. Think through the 5 steps. Then output:
{{"bonus_score": <0-10>, "reasoning": "<3 sentences: (1) strongest signal and reliability, (2) key risk or conflict, (3) what would break the thesis>", "direction": "BUY"}}
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
    fii_net_crore: float | None = None,
) -> tuple[int, str]:
    """Return (pts, description) for the strongest positive news catalyst found.

    Tier A (up to 14 pts): article directly names this stock.
    Tier B  (up to 8 pts): article explicitly tags the stock's sector.
    Both tiers apply age decay: articles > 4h get 75%, > 12h get 50% of full points.

    FII discount: if FII is net selling > 300 Cr on the same day as positive news,
    reduce points by 30% — smart money distributing against the headline is a warning.
    """
    nse_sym = symbol.replace(".NS", "").replace(".BO", "").upper()
    stock_sector = _STOCK_TO_SECTOR.get(symbol)

    pts, summary, is_tier_a = 0, "", False

    # Tier A: direct stock-specific catalyst
    for art in classified_news:
        if art.get("sentiment") != "positive":
            continue
        affected = [s.upper() for s in (art.get("affected_stocks") or [])]
        if any(nse_sym in s for s in affected) or nse_sym in (art.get("headline") or "").upper():
            summary = art.get("summary") or art["headline"][:60]
            pts = round(14 * _news_age_decay(art))
            is_tier_a = True
            break

    # Tier B: sector-tagged article (reduced weight — indirect catalyst)
    if not is_tier_a and stock_sector:
        for art in classified_news:
            if art.get("sentiment") != "positive":
                continue
            art_sectors = [s.upper() for s in (art.get("affected_sectors") or [])]
            if any(stock_sector.upper() in s for s in art_sectors):
                summary = art.get("summary") or art["headline"][:60]
                pts = round(8 * _news_age_decay(art))
                break

    if pts > 0 and fii_net_crore is not None and fii_net_crore < -300:
        discounted = round(pts * 0.70)
        summary = f"{summary} [FII net selling ₹{fii_net_crore:.0f}Cr — catalyst discounted]"
        pts = discounted

    return pts, summary


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

    # 6. News catalyst (FII net selling discounts points by 30% when > ₹300 Cr selling)
    fii_raw = market_data.get("fii_net_crore")
    news_pts, news_desc = _news_catalyst_score(symbol, classified_news, fii_net_crore=fii_raw)
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

    # 9b. PCR signal — options market positioning
    pcr = td.get("pcr")
    if pcr is not None:
        if pcr < 0.7:
            score += 6
            triggered.append(f"PCR {pcr:.2f} < 0.7 (options market positioned for rise)")
        elif pcr > 1.3:
            score = max(0, score - 6)
            triggered.append(f"⚠ PCR {pcr:.2f} > 1.3 (options market positioned for fall)")
        elif pcr <= 0.85:
            triggered.append(f"PCR {pcr:.2f} (mild bullish options positioning)")

    # 9. Near 75-day high — breakout proximity signal
    pct_from_high = td.get("pct_from_high", -99.0)
    pct_from_low  = td.get("pct_from_low", 99.0)
    if -5.0 <= pct_from_high <= 0.0:
        score += 8
        triggered.append(
            f"Near 75d high (₹{td.get('high_75d', 0):.0f}, {pct_from_high:+.1f}% away) — breakout setup"
        )
    elif 0.0 < pct_from_low <= 10.0:
        weak.append(
            f"Near 75d low ({pct_from_low:+.1f}% above ₹{td.get('low_75d', 0):.0f}) — check for downtrend"
        )

    # 8. Market positive — treat missing FII as neutral, not as zero
    nifty_chg = market_data.get("nifty_change_pct", 0) or 0
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


async def _fetch_hist_context(signals_repo: TradingSignalsRepository, symbol: str) -> str:
    """Return the last 3 closed outcomes for this stock as a calibration string for the LLM.

    Queries trading_signals for documents where was_correct exists (i.e. monitor_agent
    has already back-filled the outcome). Returns a compact one-liner per signal so the
    LLM can see whether this stock has a recent track record of correct or incorrect calls.
    """
    try:
        cursor = signals_repo._col.find(
            {"symbol": symbol, "was_correct": {"$exists": True}},
            projection={
                "date": 1,
                "confidence": 1,
                "was_correct": 1,
                "actual_return_pct": 1,
                "direction": 1,
            },
            sort=[("createdAt", -1)],
            limit=3,
        )
        past: list[dict[str, Any]] = await cursor.to_list(length=3)
        if not past:
            return "No prior closed signals for this stock."
        lines = []
        for s in past:
            tick = "✓" if s.get("was_correct") else "✗"
            ret = s.get("actual_return_pct", 0.0)
            lines.append(
                f"{tick} {s.get('date', '?')}: {s.get('direction', 'BUY')} "
                f"conf={s.get('confidence', '?')}% → {ret:+.2f}%"
            )
        return "Prior signals on this stock: " + " | ".join(lines)
    except Exception as exc:
        logger.warning("hist_context_fetch_failed symbol=%s error=%s", symbol, exc)
        return "Prior signal data temporarily unavailable."


async def _llm_bonus(
    llm: Any,
    signals_repo: TradingSignalsRepository,
    symbol: str,
    base_score: int,
    td: dict[str, Any],
    market_data: dict[str, Any],
    sectors: list[dict[str, Any]],
    triggered: list[str],
    weak_signals: list[str],
) -> tuple[int, str]:
    """Ask the LLM for a 0-10 bonus and reasoning. Returns (bonus, reasoning)."""
    stock_sector = _STOCK_TO_SECTOR.get(symbol, "Unknown")
    fii_raw = market_data.get("fii_net_crore")

    rsi = td.get("rsi", 50.0)
    macd_hist = td.get("macd_hist", 0.0)
    close = td.get("close", 0.0)
    ema20 = td.get("ema20", close)
    ema50 = td.get("ema50", close)
    vol_ratio = td.get("volume_ratio", 1.0)
    bb_pct = td.get("bb_pct", 0.5)

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

    # Sector context for the LLM
    sector_entry = next((s for s in sectors if s.get("name") == stock_sector), None)
    sector_score = sector_entry.get("score", 0) if sector_entry else 0
    sector_direction = sector_entry.get("direction", "unknown") if sector_entry else "unknown"

    # Nifty regime
    nifty_above_ema50 = market_data.get("nifty_above_ema50")
    if nifty_above_ema50 is True:
        nifty_regime = "BULL (Nifty above 50-EMA)"
    elif nifty_above_ema50 is False:
        nifty_regime = "BEAR (Nifty below 50-EMA)"
    else:
        nifty_regime = "unknown"

    vix = market_data.get("vix")
    nifty_5d = market_data.get("nifty_5d_return") or 0.0
    nifty_30d = market_data.get("nifty_30d_return") or 0.0
    hist_context = await _fetch_hist_context(signals_repo, symbol)

    prompt = _LLM_USER_TMPL.format(
        symbol=symbol,
        sector=stock_sector,
        sector_direction=sector_direction,
        sector_score=sector_score,
        base_score=base_score,
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
        bb_pct=bb_pct,
        vol_ratio=vol_ratio,
        vol_interp=vol_interp,
        nifty_regime=nifty_regime,
        nifty_chg=market_data.get("nifty_change_pct") or 0.0,
        nifty_5d=nifty_5d,
        nifty_30d=nifty_30d,
        vix=vix if vix is not None else "N/A",
        fii_display=f"₹{fii_raw:.0f}cr" if fii_raw is not None else "unavailable",
        signals="; ".join(triggered) or "none",
        weak_signals_str="; ".join(weak_signals) if weak_signals else "none",
        news_catalyst=news_catalyst,
        hist_context=hist_context,
    )
    result = await call_llm_json(llm, _LLM_SYSTEM, prompt)
    if isinstance(result, dict):
        bonus = max(0, min(10, int(result.get("bonus_score", 0))))
        # Python-side guard: LLM hard rule enforcement
        n_clean = sum(1 for s in triggered if not s.startswith("⚠") and not s.startswith("Nifty"))
        if n_clean < 3 and bonus > 3:
            logger.info("signal_agent llm_bonus_capped symbol=%s bonus=%d→3 (only %d signals)", symbol, bonus, n_clean)
            bonus = 3
        reasoning = str(result.get("reasoning", ""))[:600]
        return bonus, reasoning
    logger.warning("signal_agent llm_bonus_failed symbol=%s — using 0", symbol)
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
                    state.llm, signals_repo, symbol, base_score,
                    td, state.market_data, state.sectors, triggered, weak_signals,
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

            # Near-miss guard penalties from guard_agent (e.g. earnings in 6-10 days)
            guard_near_misses = state.guard_result.get("near_misses", {}).get(symbol, [])
            for nm in guard_near_misses:
                penalty = nm.get("confidence_penalty", 0)
                confidence = max(0, confidence - penalty)
                weak_signals.append(f"{nm['type']}: {nm['detail']} (−{penalty} pts)")

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
