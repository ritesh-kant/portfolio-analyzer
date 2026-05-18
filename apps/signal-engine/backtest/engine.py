"""engine — walk-forward backtest driver.

Orchestrates the full simulation loop:

    for each fold (3-month out-of-sample window, 1-month step):
        for each trading day in the fold:
            1. Guard: skip day if global kill-switches fire (VIX > 22 or Nifty < −1.5%)
            2. Exits: for each open position, check stop/target/max-age
            3. Entries: score every stock in universe; open qualifying positions
            4. Mark-to-market: snapshot equity curve
        → compute FoldMetrics

    → aggregate across all folds → AggregateMetrics

Walk-forward parameters (defaults match the Phase 1 plan):
    test_window_months : 3     — out-of-sample window per fold
    step_months        : 1     — how far forward the window advances each fold
    universe           : all symbols from SECTOR_STOCKS (Nifty ~100 liquid names)
    initial_capital    : ₹10,00,000 (₹10 lakh) — reset each fold

Why reset capital each fold rather than chain:
    Walk-forward validates the *strategy's* signal quality, not the compounding
    of one lucky fold into the next. Each fold is an independent out-of-sample
    test. Chaining would make a lucky early fold inflate all later metrics.
    For a chained equity curve (what an investor would have experienced),
    report.py concatenates the fold equity curves in order.

Data requirements:
    The engine reads pre-loaded data from data_loader.load_all(). Historical
    OHLCV must cover the test window PLUS 90 calendar days of lookback so that
    indicators can be computed on the first day of each fold.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

from backtest.data_loader import load_all
from backtest.indicators import compute_indicators_batch, derive_sector_scores
from backtest.metrics import (
    AggregateMetrics,
    FoldMetrics,
    aggregate_fold_metrics,
    compute_fold_metrics,
)
from backtest.order_simulator import (
    TP1_FRACTION,
    Portfolio,
    check_circuit_breakers,
    clone_portfolio,
    close_position,
    make_portfolio,
    open_position,
    reset_daily_pnl,
    should_close,
    update_trail,
)
from backtest.signal_replay import apply_guard, compute_signal_score

logger = logging.getLogger(__name__)

# Global guard thresholds — must match guard_agent.py constants
# VIX threshold: 18 was too aggressive (caused 9 consecutive zero-trade folds in 2022).
# 20 blocks the worst panic spikes while keeping the engine active in normal chop.
_VIX_KILL_THRESHOLD   = 20.0
_NIFTY_KILL_THRESHOLD = -1.5   # % daily change


@dataclass
class BacktestConfig:
    """All tunable parameters for a backtest run."""
    start_date: str               # ISO "YYYY-MM-DD" — start of first fold
    end_date: str                 # ISO "YYYY-MM-DD" — end of last fold
    initial_capital: float = 1_000_000.0
    test_window_months: int = 3   # out-of-sample window length
    step_months: int = 1          # how far the window steps forward per fold
    lookback_days: int = 90       # calendar days of OHLCV needed before fold start
    min_signal_confidence: float = 60.0
    news_mode: bool = False       # False = news-neutral (conservative baseline)
    refresh_data: bool = False    # True = re-download even if cache exists
    symbols: list[str] = field(default_factory=list)   # populated by engine from SECTOR_STOCKS


@dataclass
class DayResult:
    """Summary of one simulated trading day."""
    date: str
    portfolio_value: float
    new_positions_opened: int
    positions_closed: int
    circuit_breaker_fired: bool
    global_guard_fired: bool


# ── Fold date generator ────────────────────────────────────────────────────────

def generate_folds(
    start_date: str,
    end_date: str,
    test_window_months: int = 3,
    step_months: int = 1,
) -> list[tuple[str, str]]:
    """Return list of (fold_start, fold_end) ISO date tuples.

    Example: start=2021-01-01, end=2024-12-31, window=3, step=1
        → (2021-01-01, 2021-03-31), (2021-02-01, 2021-04-30), ...
        Each tuple is the out-of-sample test window.
    """
    folds: list[tuple[str, str]] = []
    current = pd.Timestamp(start_date)
    end_ts  = pd.Timestamp(end_date)

    while True:
        fold_end = current + pd.DateOffset(months=test_window_months) - pd.Timedelta(days=1)
        if fold_end > end_ts:
            fold_end = end_ts
        folds.append((current.date().isoformat(), fold_end.date().isoformat()))
        if fold_end >= end_ts:
            break
        current = current + pd.DateOffset(months=step_months)
        if current > end_ts:
            break

    return folds


def _trading_days_in_range(
    market_df: pd.DataFrame,
    start: str,
    end: str,
) -> list[pd.Timestamp]:
    """Return sorted list of trading days (market index) within [start, end]."""
    window = market_df.loc[start:end]
    return list(window.index)


def _days_until_earnings(
    symbol: str,
    sim_date: pd.Timestamp,
    earnings_calendar: dict[str, list[str]],
) -> int | None:
    """Return days until the next upcoming earnings announcement, or None."""
    dates = earnings_calendar.get(symbol, [])
    upcoming = [
        (pd.Timestamp(d) - sim_date).days
        for d in dates
        if (pd.Timestamp(d) - sim_date).days >= 0
    ]
    return min(upcoming) if upcoming else None


# ── Single-day simulation step ─────────────────────────────────────────────────

def _simulate_day(
    sim_date: pd.Timestamp,
    date_str: str,
    portfolio: Portfolio,
    stocks_ohlcv: dict[str, pd.DataFrame],
    market_df: pd.DataFrame,
    fii_df: pd.DataFrame,
    earnings_calendar: dict[str, list[str]],
    stock_to_sector: dict[str, str],
    sector_stocks: dict[str, list[str]],
    config: BacktestConfig,
    cooled_off_until: dict[str, pd.Timestamp],   # W2.4: per-symbol cooloff state (mutated in-place)
) -> DayResult:
    """Run one complete trading day in the simulation.

    Order of operations (matches production pipeline):
        1. Reset daily P&L counter if date changed
        2. Build market_data dict for this day
        3. Global guard checks (VIX > 18, Nifty < −1.5%, EMA50/200) — skip entries if fired
        4. Increment days_held + update MAE/MFE on all open positions
        5. Exit loop: check each open position for stop/target/max-age
           → on STOP exit, add symbol to cooled_off_until (W2.4)
        6. Entry loop (if guard not fired and circuit-breakers ok):
           a. Compute indicators for all symbols
           b. Derive sector scores, select top-2 bullish sectors (W2.5)
           c. Per-stock guard: earnings, intraday move, cooloff (W2.4)
           d. Adaptive confidence threshold based on VIX (W2.3)
           e. Score with compute_signal_score
           f. Open position with ATR stops and signal attribution (W2.2, W1.2)
        7. Mark-to-market equity snapshot
    """
    reset_daily_pnl(portfolio, date_str)

    # ── Build market_data for this day ────────────────────────────────────────
    mkt_row = market_df.loc[sim_date] if sim_date in market_df.index else None

    nifty_chg      = float(mkt_row["nifty_change_pct"])  if mkt_row is not None and pd.notna(mkt_row.get("nifty_change_pct"))  else 0.0
    vix            = float(mkt_row["vix"])                if mkt_row is not None and pd.notna(mkt_row.get("vix"))               else None
    vix_caution    = bool(mkt_row["vix_caution"])         if mkt_row is not None and pd.notna(mkt_row.get("vix_caution"))       else False
    nifty_above50  = mkt_row.get("nifty_above_ema50")     if mkt_row is not None else None
    nifty_above200 = mkt_row.get("nifty_above_ema200")    if mkt_row is not None else None
    nifty_5d       = float(mkt_row["nifty_5d_return"])    if mkt_row is not None and pd.notna(mkt_row.get("nifty_5d_return"))   else 0.0
    nifty_30d      = float(mkt_row["nifty_30d_return"])   if mkt_row is not None and pd.notna(mkt_row.get("nifty_30d_return"))  else 0.0
    nifty_20d_vol  = float(mkt_row["nifty_20d_vol"])      if mkt_row is not None and pd.notna(mkt_row.get("nifty_20d_vol"))     else None
    nifty_60d_vol  = float(mkt_row["nifty_60d_vol"])      if mkt_row is not None and pd.notna(mkt_row.get("nifty_60d_vol"))     else None

    fii_row = fii_df.loc[sim_date] if sim_date in fii_df.index else None
    fii_net = float(fii_row["fii_net_crore"]) if fii_row is not None and pd.notna(fii_row.get("fii_net_crore")) else None

    market_data: dict[str, Any] = {
        "nifty_change_pct":  nifty_chg,
        "vix":               vix,
        "vix_caution":       vix_caution,
        "nifty_above_ema50": nifty_above50,
        "nifty_5d_return":   nifty_5d,
        "nifty_30d_return":  nifty_30d,
        "fii_net_crore":     fii_net,
    }

    # ── Global guard ──────────────────────────────────────────────────────────
    # VIX threshold tightened from 22 → 18 (W2.1): mean-reversion setups misfire
    # in Indian chop markets above VIX 18. The previous caution zone (18–22) merely
    # penalised −5 pts; now VIX ≥ 18 fully blocks new entries.
    global_guard_fired = False
    if vix is not None and vix > _VIX_KILL_THRESHOLD:
        logger.debug("engine global_guard VIX=%.1f > %.1f date=%s", vix, _VIX_KILL_THRESHOLD, date_str)
        global_guard_fired = True
    if nifty_chg < _NIFTY_KILL_THRESHOLD:
        logger.debug("engine global_guard nifty=%.2f%% date=%s", nifty_chg, date_str)
        global_guard_fired = True
    # Three-layer regime filter (W2.1):
    #   Layer 1 (slow):  Nifty > EMA50  — full bear market guard.
    #   Layer 2 (ultra): Nifty > EMA200 — extended downtrend / structural bear guard.
    #   Layer 3 (fast):  Nifty 5d > 0   — early correction / topping guard.
    if nifty_above50 is not None and not nifty_above50:
        logger.debug("engine regime_filter Nifty below EMA50 date=%s", date_str)
        global_guard_fired = True
    if nifty_above200 is not None and not nifty_above200:
        logger.debug("engine regime_filter Nifty below EMA200 date=%s", date_str)
        global_guard_fired = True
    if nifty_5d <= 0:
        logger.debug("engine regime_filter nifty_5d=%.2f%% ≤ 0 date=%s", nifty_5d, date_str)
        global_guard_fired = True

    # W2.1 Chop gate: high recent vol vs baseline → halve max open positions
    from backtest.order_simulator import MAX_POSITIONS as _BASE_MAX_POS
    effective_max_positions = _BASE_MAX_POS
    if (
        nifty_20d_vol is not None and nifty_60d_vol is not None
        and nifty_60d_vol > 0
        and nifty_20d_vol > 1.5 * nifty_60d_vol
    ):
        effective_max_positions = max(1, _BASE_MAX_POS // 2)
        logger.debug(
            "engine chop_gate 20d_vol=%.1f%% > 1.5×60d_vol=%.1f%% — max_pos=%d date=%s",
            nifty_20d_vol, nifty_60d_vol, effective_max_positions, date_str,
        )

    # ── Increment days_held + update MAE/MFE ─────────────────────────────────
    # We need current prices before exits so we can update excursion data first.
    current_prices: dict[str, float] = {}
    for sym, ohlcv in stocks_ohlcv.items():
        row = ohlcv.loc[ohlcv.index <= sim_date]
        if not row.empty:
            current_prices[sym] = float(row["close"].iloc[-1])

    for pos in portfolio.open_positions:
        pos.days_held += 1
        # W1.2: track MFE and MAE using today's close price
        price = current_prices.get(pos.symbol)
        if price is not None and pos.entry_price > 0:
            ret_pct = (price - pos.entry_price) / pos.entry_price * 100
            pos.mfe_pct = max(pos.mfe_pct, ret_pct)
            pos.mae_pct = max(pos.mae_pct, -ret_pct)   # stored as positive loss %
            # Chandelier high-water mark + trailing stop, ratcheting up only.
            update_trail(pos, price)

    # ── Exit loop ─────────────────────────────────────────────────────────────
    # Exit ladder:
    #   TP1      — partial close (TP1_FRACTION), move stop to breakeven, position
    #              continues with chandelier trail on remainder.
    #   TRAIL    — remainder hit trailing stop after TP1; full close.
    #   STOP     — pre-TP1 initial stop hit; full close + cooloff.
    #   MAX_AGE  — held past MAX_HOLD_DAYS; full close.
    positions_closed = 0
    for pos in list(portfolio.open_positions):   # iterate over copy
        price = current_prices.get(pos.symbol)
        if price is None:
            continue
        reason = should_close(pos, price)
        if not reason:
            continue
        if reason == "TP1" and pos.atr_at_entry > 0 and not pos.tp1_taken:
            shares_to_sell = max(1, int(pos.shares * TP1_FRACTION))
            if shares_to_sell >= pos.shares:
                # Position too small to split — full close.
                close_position(portfolio, pos, date_str, price, "TP1")
                positions_closed += 1
            else:
                close_position(
                    portfolio, pos, date_str, price, "TP1",
                    shares_to_close=shares_to_sell,
                )
                # Move stop to breakeven on remainder; trail seeded at entry.
                pos.tp1_taken = True
                pos.original_stop = pos.entry_price
                pos.trailing_stop = max(pos.trailing_stop, pos.entry_price)
                logger.debug(
                    "engine tp1 symbol=%s sold=%d remaining=%d new_stop=%.2f",
                    pos.symbol, shares_to_sell, pos.shares, pos.trailing_stop,
                )
        else:
            close_position(portfolio, pos, date_str, price, reason)
            positions_closed += 1
            # W2.4: cool off the symbol for 15 trading days after a STOP exit.
            # TRAIL exits intentionally do NOT cooloff — those are trend-end exits,
            # often profitable, where re-entry on the next signal is acceptable.
            if reason == "STOP":
                release = sim_date + pd.offsets.BDay(15)
                cooled_off_until[pos.symbol] = release
                logger.debug("engine cooloff symbol=%s until=%s", pos.symbol, release.date())

    # ── MTM for circuit-breaker check ─────────────────────────────────────────
    mtm_value = portfolio.snapshot_equity(date_str, current_prices)

    # ── Entry loop ────────────────────────────────────────────────────────────
    new_positions = 0
    can_trade = True   # initialise so DayResult can reference it below
    if not global_guard_fired:
        can_trade, cb_reason = check_circuit_breakers(portfolio, date_str, mtm_value)
        if not can_trade:
            logger.debug("engine circuit_breaker date=%s reason=%s", date_str, cb_reason)
        else:
            # Compute indicators for all symbols as of sim_date
            indicators = compute_indicators_batch(stocks_ohlcv, sim_date)

            # Derive sector scores — historical proxy for production sector_agent.
            # A sector is "bullish" when ≥ 60% of its stocks are above EMA50.
            all_sectors = derive_sector_scores(indicators, sector_stocks)

            # W2.5: restrict to top-3 bullish sectors by score.
            # Top-2 was too restrictive (cut too many folds to zero trades).
            # Top-3 still concentrates in best-performing sectors while allowing enough volume.
            bullish_sorted = sorted(
                [s for s in all_sectors if s.get("direction") == "bullish"],
                key=lambda x: x.get("score", 0),
                reverse=True,
            )
            day_sectors = bullish_sorted[:3]

            # W2.3: adaptive confidence threshold.
            # Low-vol markets (VIX < 14) → baseline threshold.
            # Rising VIX (14–20) → linearly tighten up to +10 pts.
            # VIX ≥ 20 is already blocked by the global guard.
            if vix is not None and vix > 14:
                vix_tighten = min((vix - 14) * (10.0 / 6.0), 10.0)
            else:
                vix_tighten = 0.0

            # Additional +5 pts if ≥ 3 of the last 20 closed trades were STOPs
            recent_trades = portfolio.closed_trades[-20:]
            consecutive_stop_penalty = 5.0 if sum(
                1 for t in recent_trades if t.exit_reason == "STOP"
            ) >= 3 else 0.0

            effective_threshold = config.min_signal_confidence + vix_tighten + consecutive_stop_penalty

            for symbol, td in indicators.items():
                if portfolio.open_position_count >= effective_max_positions:
                    break

                # W2.4: skip if in cooloff period after a STOP exit
                if symbol in cooled_off_until and sim_date <= cooled_off_until[symbol]:
                    logger.debug("engine cooloff_skip symbol=%s date=%s", symbol, date_str)
                    continue

                # Per-stock guard (earnings, intraday move)
                earnings_days = _days_until_earnings(symbol, sim_date, earnings_calendar)
                passed, _ = apply_guard(symbol, td, market_data, earnings_days)
                if not passed:
                    continue

                sector = stock_to_sector.get(symbol, "Unknown")

                confidence, triggered, _ = compute_signal_score(
                    symbol, td, market_data, day_sectors, stock_to_sector,
                    earnings_days_away=earnings_days,
                    news_mode=config.news_mode,
                )

                if confidence < effective_threshold:
                    continue

                entry_price = td.get("close", 0.0)
                if entry_price <= 0:
                    continue

                # W1.2: build signal attribution snapshot
                stock_sector_entry = next(
                    (s for s in all_sectors if s.get("name") == sector), {}
                )
                signal_details = {
                    "triggered_signals":         triggered,
                    "signal_score_raw":          confidence,
                    "vix_at_entry":              vix or 0.0,
                    "nifty_above_ema50_at_entry": bool(nifty_above50),
                    "fii_net_cr_at_entry":        fii_net or 0.0,
                    "sector_score_at_entry":      int(stock_sector_entry.get("score", 0)),
                    "had_tier_a_news":            False,  # news_mode=False in backtest
                }

                # W2.2: pass ATR for adaptive stop/target sizing
                atr = td.get("atr14")

                pos = open_position(
                    portfolio, symbol, sector, date_str, entry_price, int(confidence),
                    max_positions=effective_max_positions,
                    signal_details=signal_details,
                    atr=atr,
                )
                if pos:
                    new_positions += 1
                    logger.debug(
                        "engine opened symbol=%s conf=%d entry=%.2f atr=%.2f date=%s",
                        symbol, confidence, entry_price, atr or 0.0, date_str,
                    )

    return DayResult(
        date=date_str,
        portfolio_value=mtm_value,
        new_positions_opened=new_positions,
        positions_closed=positions_closed,
        circuit_breaker_fired=(not global_guard_fired and not can_trade) if not global_guard_fired else False,
        global_guard_fired=global_guard_fired,
    )


# ── Single fold simulation ─────────────────────────────────────────────────────

async def run_fold(
    fold_id: int,
    fold_start: str,
    fold_end: str,
    data: dict[str, Any],
    config: BacktestConfig,
    stock_to_sector: dict[str, str],
    sector_stocks: dict[str, list[str]],
) -> FoldMetrics:
    """Simulate one walk-forward fold and return its metrics.

    Args:
        fold_id:        1-based fold number.
        fold_start:     ISO start date of the out-of-sample window.
        fold_end:       ISO end date of the out-of-sample window.
        data:           Output of data_loader.load_all() — stocks, market, fii, earnings.
        config:         Backtest configuration.
        stock_to_sector: Production symbol → sector mapping.
    """
    logger.info(
        "engine fold=%d start=%s end=%s capital=₹%.0f",
        fold_id, fold_start, fold_end, config.initial_capital,
    )

    market_df = data["market"]
    fii_df    = data["fii"]
    stocks    = data["stocks"]
    earnings  = data["earnings"]

    # Fresh portfolio and cooloff state for this fold
    portfolio = make_portfolio(config.initial_capital)
    cooled_off_until: dict[str, pd.Timestamp] = {}   # W2.4: symbol → release date

    trading_days = _trading_days_in_range(market_df, fold_start, fold_end)
    logger.info("engine fold=%d trading_days=%d", fold_id, len(trading_days))

    day_results: list[DayResult] = []

    for sim_date in trading_days:
        date_str = sim_date.date().isoformat()
        result = _simulate_day(
            sim_date=sim_date,
            date_str=date_str,
            portfolio=portfolio,
            stocks_ohlcv=stocks,
            market_df=market_df,
            fii_df=fii_df,
            earnings_calendar=earnings,
            stock_to_sector=stock_to_sector,
            sector_stocks=sector_stocks,
            config=config,
            cooled_off_until=cooled_off_until,
        )
        day_results.append(result)

    # Force-close any remaining open positions at last day's price
    last_day = trading_days[-1] if trading_days else None
    if last_day:
        last_date_str = last_day.date().isoformat()
        current_prices: dict[str, float] = {}
        for sym, ohlcv in stocks.items():
            row = ohlcv.loc[ohlcv.index <= last_day]
            if not row.empty:
                current_prices[sym] = float(row["close"].iloc[-1])

        for pos in list(portfolio.open_positions):
            price = current_prices.get(pos.symbol, pos.entry_price)
            close_position(portfolio, pos, last_date_str, price, "FOLD_END")
            logger.debug("engine fold_end_close symbol=%s", pos.symbol)

    fold_metrics = compute_fold_metrics(
        fold_id=fold_id,
        start_date=fold_start,
        end_date=fold_end,
        daily_values=portfolio.daily_values,
        daily_dates=portfolio.daily_dates,
        trades=portfolio.closed_trades,
        market_df=market_df,
    )

    logger.info(
        "engine fold=%d done trades=%d win_rate=%.1f%% sharpe=%.2f "
        "cagr=%.1f%% max_dd=%.1f%% alpha=%.1f%%",
        fold_id,
        fold_metrics.total_trades,
        fold_metrics.win_rate * 100,
        fold_metrics.sharpe_ratio,
        fold_metrics.cagr * 100,
        fold_metrics.max_drawdown_pct,
        fold_metrics.alpha_vs_nifty * 100,
    )
    return fold_metrics


# ── Full walk-forward run ──────────────────────────────────────────────────────

async def run_backtest(config: BacktestConfig) -> AggregateMetrics:
    """Run the full walk-forward backtest and return aggregated metrics.

    Steps:
        1. Load all historical data (with caching)
        2. Generate fold date windows
        3. Run each fold sequentially
        4. Aggregate fold metrics
        5. Return AggregateMetrics with pass/fail verdict

    Args:
        config: BacktestConfig with start/end dates, capital, symbols, etc.

    Returns:
        AggregateMetrics — use report.py to display or save to CSV.
    """
    from src.scrapers.sector_stocks import SECTOR_STOCKS
    from src.pipeline.sector_map import STOCK_TO_SECTOR

    # Default universe: all symbols from SECTOR_STOCKS
    if not config.symbols:
        config.symbols = [s for syms in SECTOR_STOCKS.values() for s in syms]

    logger.info(
        "engine backtest start=%s end=%s universe=%d symbols capital=₹%.0f",
        config.start_date, config.end_date, len(config.symbols), config.initial_capital,
    )

    # Data load: fetch enough history for the first fold's indicator lookback
    data_start = (
        pd.Timestamp(config.start_date) - pd.Timedelta(days=config.lookback_days + 30)
    ).date().isoformat()

    data = await load_all(
        config.symbols,
        data_start,
        config.end_date,
        refresh=config.refresh_data,
    )

    folds = generate_folds(
        config.start_date,
        config.end_date,
        config.test_window_months,
        config.step_months,
    )
    logger.info("engine generated %d folds", len(folds))

    fold_results: list[FoldMetrics] = []
    for i, (fold_start, fold_end) in enumerate(folds, start=1):
        fold_metrics = await run_fold(
            fold_id=i,
            fold_start=fold_start,
            fold_end=fold_end,
            data=data,
            config=config,
            stock_to_sector=STOCK_TO_SECTOR,
            sector_stocks=SECTOR_STOCKS,
        )
        fold_results.append(fold_metrics)

    agg = aggregate_fold_metrics(
        fold_results,
        data["market"],
        config.start_date,
        config.end_date,
    )

    logger.info(
        "engine backtest complete folds=%d/%d passing overall_sharpe=%.2f "
        "overall_cagr=%.1f%% overall_dd=%.1f%% overall_win_rate=%.1f%% "
        "overall_alpha=%.1f%% GATE=%s",
        agg.folds_passing_gate,
        agg.total_folds,
        agg.overall_sharpe,
        agg.overall_cagr * 100,
        agg.overall_max_drawdown,
        agg.overall_win_rate * 100,
        agg.overall_alpha * 100,
        "PASS" if agg.passes_gate else "FAIL",
    )

    return agg
