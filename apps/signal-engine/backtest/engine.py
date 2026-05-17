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
    Portfolio,
    check_circuit_breakers,
    clone_portfolio,
    close_position,
    make_portfolio,
    open_position,
    reset_daily_pnl,
    should_close,
)
from backtest.signal_replay import apply_guard, compute_signal_score

logger = logging.getLogger(__name__)

# Global guard thresholds — must match guard_agent.py constants
_VIX_KILL_THRESHOLD   = 22.0
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
) -> DayResult:
    """Run one complete trading day in the simulation.

    Order of operations (matches production pipeline):
        1. Reset daily P&L counter if date changed
        2. Build market_data dict for this day
        3. Global guard checks (VIX > 22, Nifty < −1.5%) — skip entries if fired
        4. Increment days_held on all open positions
        5. Exit loop: check each open position for stop/target/max-age
        6. Entry loop (if guard not fired and circuit-breakers ok):
           a. Compute indicators for all symbols
           b. Per-stock guard (earnings, intraday move)
           c. Score with compute_signal_score
           d. Open position if confidence ≥ threshold
        7. Mark-to-market equity snapshot
    """
    reset_daily_pnl(portfolio, date_str)

    # ── Build market_data for this day ────────────────────────────────────────
    mkt_row = market_df.loc[sim_date] if sim_date in market_df.index else None

    nifty_chg   = float(mkt_row["nifty_change_pct"]) if mkt_row is not None and pd.notna(mkt_row.get("nifty_change_pct")) else 0.0
    vix         = float(mkt_row["vix"])               if mkt_row is not None and pd.notna(mkt_row.get("vix"))              else None
    vix_caution = bool(mkt_row["vix_caution"])        if mkt_row is not None and pd.notna(mkt_row.get("vix_caution"))      else False
    nifty_above = mkt_row.get("nifty_above_ema50")    if mkt_row is not None else None
    nifty_5d    = float(mkt_row["nifty_5d_return"])   if mkt_row is not None and pd.notna(mkt_row.get("nifty_5d_return"))  else 0.0
    nifty_30d   = float(mkt_row["nifty_30d_return"])  if mkt_row is not None and pd.notna(mkt_row.get("nifty_30d_return")) else 0.0

    fii_row = fii_df.loc[sim_date] if sim_date in fii_df.index else None
    fii_net = float(fii_row["fii_net_crore"]) if fii_row is not None and pd.notna(fii_row.get("fii_net_crore")) else None

    market_data: dict[str, Any] = {
        "nifty_change_pct":  nifty_chg,
        "vix":               vix,
        "vix_caution":       vix_caution,
        "nifty_above_ema50": nifty_above,
        "nifty_5d_return":   nifty_5d,
        "nifty_30d_return":  nifty_30d,
        "fii_net_crore":     fii_net,
    }

    # ── Global guard ──────────────────────────────────────────────────────────
    global_guard_fired = False
    if vix is not None and vix > _VIX_KILL_THRESHOLD:
        logger.debug("engine global_guard VIX=%.1f > %.1f date=%s", vix, _VIX_KILL_THRESHOLD, date_str)
        global_guard_fired = True
    if nifty_chg < _NIFTY_KILL_THRESHOLD:
        logger.debug("engine global_guard nifty=%.2f%% date=%s", nifty_chg, date_str)
        global_guard_fired = True

    # ── Increment days_held ───────────────────────────────────────────────────
    for pos in portfolio.open_positions:
        pos.days_held += 1

    # ── Current prices for exit checks and MTM ────────────────────────────────
    current_prices: dict[str, float] = {}
    for sym, ohlcv in stocks_ohlcv.items():
        row = ohlcv.loc[ohlcv.index <= sim_date]
        if not row.empty:
            current_prices[sym] = float(row["close"].iloc[-1])

    # ── Exit loop ─────────────────────────────────────────────────────────────
    positions_closed = 0
    for pos in list(portfolio.open_positions):   # iterate over copy
        price = current_prices.get(pos.symbol)
        if price is None:
            continue
        reason = should_close(pos, price)
        if reason:
            close_position(portfolio, pos, date_str, price, reason)
            positions_closed += 1

    # ── MTM for circuit-breaker check ─────────────────────────────────────────
    mtm_value = portfolio.snapshot_equity(date_str, current_prices)

    # ── Entry loop ────────────────────────────────────────────────────────────
    new_positions = 0
    if not global_guard_fired:
        can_trade, cb_reason = check_circuit_breakers(portfolio, date_str, mtm_value)
        if not can_trade:
            logger.debug("engine circuit_breaker date=%s reason=%s", date_str, cb_reason)
        else:
            # Compute indicators for all symbols as of sim_date
            indicators = compute_indicators_batch(stocks_ohlcv, sim_date)

            # Derive sector scores from the pre-computed EMA50 flags — this is
            # the historical proxy for production's sector_agent. A sector is
            # "bullish" (score ≥ 60) when ≥ 60% of its stocks are above EMA50.
            day_sectors = derive_sector_scores(indicators, sector_stocks)

            for symbol, td in indicators.items():
                if portfolio.open_position_count >= 8:
                    break

                # Per-stock guard
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

                if confidence < config.min_signal_confidence:
                    continue

                entry_price = td.get("close", 0.0)
                if entry_price <= 0:
                    continue

                pos = open_position(
                    portfolio, symbol, sector, date_str, entry_price, int(confidence)
                )
                if pos:
                    new_positions += 1
                    logger.debug(
                        "engine opened symbol=%s conf=%d entry=%.2f date=%s",
                        symbol, confidence, entry_price, date_str,
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

    # Fresh portfolio for this fold
    portfolio = make_portfolio(config.initial_capital)

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
