"""signal_replay — deterministic replay of signal_agent._score_base() for the backtest.

This module is the scoring heart of the backtest. It must produce the same
confidence score and triggered-signals list as the production signal_agent for
identical inputs. The only intentional differences are:

    1. LLM bonus is always 0 (no live LLM call during backtest). This is
       conservative — real production performance should be equal or better
       when the LLM bonus is active.

    2. News score is 0 in news-neutral mode (default). Historical news data
       is not freely available; running without news measures the purely
       technical + macro signal quality as a lower bound.

    3. Guard checks are inlined (no async NSE scraping). The caller passes
       pre-loaded earnings data; ASM/GSM lists are approximated as empty
       (backtest limitation noted in the report).

Parity guarantee:
    test_backtest_parity.py::TestSignalReplayParity feeds identical inputs to
    both this module and production signal_agent._score_base() and asserts
    identical (score, triggered_signals) output.

EXACT VALUES FROM PRODUCTION CODE (src/pipeline/agents/signal_agent.py):
    RSI oversold   < 40  → +12 pts
    MACD hist      > 0   → +12 pts
    EMA20 above         → +10 pts
    EMA50 above         → +12 pts   ← NOTE: code says 12, not 10 as in docs
    Volume ratio   > 1.5 → +10 pts
    News Tier A direct  → +14 pts (×0.75 if >4h, ×0.5 if >12h; backtest: 0)
    News Tier B sector  → +8 pts max (backtest: 0)
    FII discount        → −30% of news pts when FII < −300 Cr
    Sector bullish(>60) → +12 pts
    PCR < 0.7 bullish   → +6 pts   (backtest: 0 when pcr=None)
    PCR > 1.3 bearish   → −6 pts   (backtest: 0 when pcr=None)
    Near 75d high       → +8 pts   (−5% ≤ pct_from_high ≤ 0%)
    Market positive     → +8 pts (Nifty > 0 AND fii > 0)
                        → +4 pts (Nifty > 0 AND fii ≤ 0 but known)
    Hard caps:
        RSI > 75         → confidence capped at 68
        VIX 18–22        → −5 pts (vix_caution)
        Earnings 6–10d   → −5 pts (near-miss penalty from guard)
    LLM bonus: always 0 in backtest
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Signal weights (match production signal_agent._SIGNAL_DEFINITIONS + _score_base) ──
# Use actual code values, not the docstring table (EMA50 is 12 in code, 10 in docs).
_W_RSI_OVERSOLD  = 12
_W_MACD_POSITIVE = 12
_W_EMA20_ABOVE   = 10
_W_EMA50_ABOVE   = 12   # actual code: score += 12 (line ~222 of signal_agent.py)
_W_VOLUME_ELEV   = 10
_W_NEWS_TIER_A   = 14
_W_NEWS_TIER_B   =  8
_W_SECTOR_BULL   = 12
_W_PCR_BULL      =  6
_W_PCR_BEAR      =  6   # subtracted
_W_NEAR_HIGH     =  8
_W_MARKET_POS    =  8
_W_MARKET_POS_NOFII = 4  # Nifty positive but FII data unavailable or negative

_TOTAL_MAX = 100
_RSI_OVERBOUGHT_CAP = 68   # hard confidence cap when RSI > 75
_VIX_CAUTION_PENALTY = 5   # pts subtracted when VIX 18–22
_EARNINGS_NEAR_MISS_PENALTY = 5  # pts subtracted for earnings 6–10d away


def _score_base(
    symbol: str,
    td: dict[str, Any],
    market_data: dict[str, Any],
    sectors: list[dict[str, Any]],
    stock_to_sector: dict[str, str],
    *,
    news_mode: bool = False,  # False = news-neutral backtest; True = include news scoring
    classified_news: list[dict[str, Any]] | None = None,
) -> tuple[int, list[str], list[str]]:
    """Compute base score, triggered signals, and near-miss weak signals.

    This function is a direct port of signal_agent._score_base(). The logic
    and thresholds are identical. The only structural difference is that
    it is a plain synchronous function (no async, no DB, no LLM).

    Args:
        symbol:         NSE symbol, e.g. "RELIANCE.NS"
        td:             Technical indicator dict from backtest/indicators.py
        market_data:    Dict with keys: nifty_change_pct, fii_net_crore (or None),
                        vix (or None), vix_caution (bool), nifty_above_ema50 (bool).
        sectors:        List of sector dicts: {name, direction, score}.
                        Use [] for news-neutral mode (sector signals still work
                        if stock_to_sector resolves the symbol's sector).
        stock_to_sector: {yf_symbol: sector_name} — same dict as production.
        news_mode:      If False (default), news score is 0 (backtest conservative mode).
                        If True, classified_news must be provided.
        classified_news: Required when news_mode=True.

    Returns:
        (base_score, triggered_signals, weak_signals)
        Caller applies LLM bonus (always 0 in backtest), hard caps, and VIX penalty
        via compute_signal_score() below.
    """
    score = 0
    triggered: list[str] = []
    weak: list[str] = []

    # 1. RSI
    rsi = td.get("rsi", 50.0)
    if rsi < 40:
        score += _W_RSI_OVERSOLD
        triggered.append(f"RSI {rsi:.1f} < 40 (oversold)")
    elif rsi > 75:
        triggered.append(f"⚠ RSI {rsi:.1f} > 75 (overbought — elevated reversal risk)")
    elif 40 <= rsi <= 50:
        weak.append(f"RSI {rsi:.1f} (near oversold, threshold 40)")

    # 2. MACD histogram positive
    macd_hist = td.get("macd_hist", 0)
    if macd_hist > 0:
        score += _W_MACD_POSITIVE
        triggered.append(f"MACD hist {macd_hist:.4f} > 0")
    elif -0.05 <= macd_hist <= 0:
        weak.append(f"MACD hist {macd_hist:.4f} (near zero, bearish by thin margin)")

    # 3 & 4. EMA trend
    close = td.get("close", 0)
    ema20 = td.get("ema20", close)
    ema50 = td.get("ema50", close)
    if td.get("above_ema20"):
        score += _W_EMA20_ABOVE
        triggered.append(f"Price ₹{close:.0f} > EMA20 ₹{ema20:.0f}")
    elif close > 0 and ema20 > 0 and (ema20 - close) / ema20 < 0.01:
        weak.append(f"Price ₹{close:.0f} within 1% below EMA20 ₹{ema20:.0f}")
    if td.get("above_ema50"):
        score += _W_EMA50_ABOVE
        triggered.append(f"Price ₹{close:.0f} > EMA50 ₹{ema50:.0f}")
    elif close > 0 and ema50 > 0 and (ema50 - close) / ema50 < 0.015:
        weak.append(f"Price ₹{close:.0f} within 1.5% below EMA50 ₹{ema50:.0f}")

    # 5. Volume elevated
    vol_ratio = td.get("volume_ratio", 1.0)
    if vol_ratio > 1.5:
        score += _W_VOLUME_ELEV
        triggered.append(f"Volume {vol_ratio:.1f}× average")
    elif 1.2 <= vol_ratio <= 1.5:
        weak.append(f"Volume {vol_ratio:.1f}× (near elevated threshold 1.5×)")

    # 6. News catalyst
    fii_raw = market_data.get("fii_net_crore")
    if news_mode and classified_news:
        news_pts, news_desc = _news_catalyst_score(
            symbol, classified_news, stock_to_sector, fii_net_crore=fii_raw
        )
        if news_pts > 0:
            score += news_pts
            triggered.append(f"News catalyst ({news_pts}pts): {news_desc}")
    # else: news-neutral mode — score += 0

    # 7. Sector bullish
    stock_sector = stock_to_sector.get(symbol)
    sector_entry = next(
        (s for s in sectors
         if s.get("name") == stock_sector
         and s.get("direction") == "bullish"
         and s.get("score", 0) > 60),
        None,
    )
    if sector_entry:
        score += _W_SECTOR_BULL
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
            score += _W_PCR_BULL
            triggered.append(f"PCR {pcr:.2f} < 0.7 (options market positioned for rise)")
        elif pcr > 1.3:
            score = max(0, score - _W_PCR_BEAR)
            triggered.append(f"⚠ PCR {pcr:.2f} > 1.3 (options market positioned for fall)")
        elif pcr <= 0.85:
            triggered.append(f"PCR {pcr:.2f} (mild bullish options positioning)")
    # else: backtest — pcr=None → 0 pts (neutral)

    # 9. Near 75-day high — breakout proximity signal
    pct_from_high = td.get("pct_from_high", -99.0)
    pct_from_low  = td.get("pct_from_low", 99.0)
    if -5.0 <= pct_from_high <= 0.0:
        score += _W_NEAR_HIGH
        triggered.append(
            f"Near 75d high (₹{td.get('high_75d', 0):.0f}, {pct_from_high:+.1f}% away) — breakout setup"
        )
    elif 0.0 < pct_from_low <= 10.0:
        weak.append(
            f"Near 75d low ({pct_from_low:+.1f}% above ₹{td.get('low_75d', 0):.0f}) — check for downtrend"
        )

    # 8. Market positive — treat missing FII as neutral, not zero
    nifty_chg = market_data.get("nifty_change_pct", 0) or 0
    if nifty_chg > 0 and fii_raw is not None and fii_raw > 0:
        score += _W_MARKET_POS
        triggered.append(f"Market positive (Nifty {nifty_chg:+.2f}%, FII ₹{fii_raw:.0f}cr)")
    elif nifty_chg > 0 and fii_raw is None:
        triggered.append(f"Nifty {nifty_chg:+.2f}% (FII data unavailable — market signal neutral)")
    elif nifty_chg > 0:
        score += _W_MARKET_POS_NOFII
        triggered.append(f"Nifty {nifty_chg:+.2f}% (FII net selling ₹{fii_raw:.0f}cr)")
    elif -0.5 <= nifty_chg <= 0:
        weak.append(f"Nifty {nifty_chg:+.2f}% (flat, market not positive)")

    return score, triggered, weak


def _news_catalyst_score(
    symbol: str,
    classified_news: list[dict[str, Any]],
    stock_to_sector: dict[str, str],
    fii_net_crore: float | None = None,
) -> tuple[int, str]:
    """Port of signal_agent._news_catalyst_score().

    Only used when news_mode=True. In backtest news-neutral mode (default)
    this function is never called.
    """
    nse_sym = symbol.replace(".NS", "").replace(".BO", "").upper()
    stock_sector = stock_to_sector.get(symbol)

    pts, summary, is_tier_a = 0, "", False

    # Tier A: direct stock-specific catalyst (no age decay in backtest — historical articles
    # are treated as fresh at the time they were published; caller handles age filtering)
    for art in classified_news:
        if art.get("sentiment") != "positive":
            continue
        affected = [s.upper() for s in (art.get("affected_stocks") or [])]
        if any(nse_sym in s for s in affected) or nse_sym in (art.get("headline") or "").upper():
            summary = art.get("summary") or str(art.get("headline", ""))[:60]
            pts = _W_NEWS_TIER_A
            is_tier_a = True
            break

    # Tier B: sector-tagged article
    if not is_tier_a and stock_sector:
        for art in classified_news:
            if art.get("sentiment") != "positive":
                continue
            art_sectors = [s.upper() for s in (art.get("affected_sectors") or [])]
            if any(stock_sector.upper() in s for s in art_sectors):
                summary = art.get("summary") or str(art.get("headline", ""))[:60]
                pts = _W_NEWS_TIER_B
                break

    if pts > 0 and fii_net_crore is not None and fii_net_crore < -300:
        discounted = round(pts * 0.70)
        summary = f"{summary} [FII net selling ₹{fii_net_crore:.0f}Cr — catalyst discounted]"
        pts = discounted

    return pts, summary


def compute_signal_score(
    symbol: str,
    td: dict[str, Any],
    market_data: dict[str, Any],
    sectors: list[dict[str, Any]],
    stock_to_sector: dict[str, str],
    *,
    earnings_days_away: int | None = None,
    news_mode: bool = False,
    classified_news: list[dict[str, Any]] | None = None,
) -> tuple[int, list[str], list[str]]:
    """Compute the final confidence score for a symbol on a given backtest day.

    Applies the full production pipeline:
        1. _score_base() — deterministic 9-signal scoring
        2. LLM bonus = 0 (always; backtest conservative mode)
        3. Hard cap: RSI > 75 → confidence ≤ 68
        4. VIX caution zone (18–22) → −5 pts
        5. Earnings near-miss (6–10d) → −5 pts per near-miss

    Args:
        symbol:            NSE yfinance symbol.
        td:                Indicator dict from backtest/indicators.compute_indicators().
        market_data:       Dict with nifty_change_pct, fii_net_crore, vix, vix_caution.
        sectors:           List of {name, direction, score} sector dicts.
        stock_to_sector:   Symbol → sector name mapping.
        earnings_days_away: Days until next earnings announcement (None = unknown/none).
        news_mode:         Include news scoring (default False for backtest).
        classified_news:   Required when news_mode=True.

    Returns:
        (confidence, triggered_signals, weak_signals)
        confidence is the final score passed to order_simulator.size_position().
    """
    base_score, triggered, weak = _score_base(
        symbol, td, market_data, sectors, stock_to_sector,
        news_mode=news_mode,
        classified_news=classified_news,
    )

    # LLM bonus: always 0 in backtest
    bonus = 0
    confidence = min(base_score + bonus, _TOTAL_MAX)

    # Hard cap: RSI > 75 (overbought)
    if td.get("rsi", 50.0) > 75:
        confidence = min(confidence, _RSI_OVERBOUGHT_CAP)

    # Soft cap: VIX in caution zone (18–22) — reduce by 5 pts
    if market_data.get("vix_caution"):
        confidence = max(0, confidence - _VIX_CAUTION_PENALTY)

    # Near-miss guard: earnings in 6-10 days
    if (
        earnings_days_away is not None
        and 6 <= earnings_days_away <= 10
    ):
        confidence = max(0, confidence - _EARNINGS_NEAR_MISS_PENALTY)
        weak.append(
            f"Earnings ~{earnings_days_away}d away (−{_EARNINGS_NEAR_MISS_PENALTY} pts)"
        )

    return confidence, triggered, weak


def apply_guard(
    symbol: str,
    td: dict[str, Any],
    market_data: dict[str, Any],
    earnings_days_away: int | None,
) -> tuple[bool, str | None]:
    """Replicate guard_agent kill-switch logic for a single symbol.

    Returns (passed, block_reason). block_reason is None when passed=True.

    Global kill-switches (checked by caller before iterating stocks):
        VIX > 22, Nifty down > 1.5% — handled in engine.py per day.

    Per-stock kill-switches (checked here):
        - Earnings within 5 days
        - Stock moved > 5% today

    ASM/GSM is treated as absent in backtest (data not historically available).
    This is a documented conservatism — real guard blocks slightly more stocks.
    """
    # Earnings within 5 calendar days
    if earnings_days_away is not None and earnings_days_away <= 5:
        return False, f"Earnings ~{earnings_days_away}d away (guard kill-switch)"

    # Stock already moved > 5% today
    change = td.get("change_pct_today")
    if change is not None and abs(change) > 5.0:
        return False, f"Stock moved {change:+.1f}% today — news priced in"

    return True, None
