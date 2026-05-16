"""metrics — performance measurement for the walk-forward backtest.

Computes the standard set of metrics a professional risk manager would
want to see before approving a strategy for live deployment:

    cagr                  Compound Annual Growth Rate
    sharpe_ratio          Annualised Sharpe (daily returns, RF = 6.5% Indian 10Y Gsec)
    sortino_ratio         Annualised Sortino (downside deviation only)
    max_drawdown_pct      Peak-to-trough drawdown on the equity curve
    max_drawdown_duration Days from peak to recovery (or end of period)
    win_rate              Fraction of closed trades that hit target
    profit_factor         Gross wins / gross losses (should be > 1.5)
    avg_hold_days         Mean days held per closed trade
    avg_win_pct           Mean return % on winning trades
    avg_loss_pct          Mean return % on losing trades (negative)
    total_trades          Total closed trades in the period
    annualised_turnover   Position value traded / avg portfolio value, annualised
    vs_nifty_cagr         Nifty 50 buy-and-hold CAGR over the same period
    alpha_vs_nifty        Strategy CAGR minus Nifty CAGR (excess return)

Decision gate (from the plan):
    Sharpe ≥ 1.0  AND  max_drawdown < 25%  AND  win_rate ≥ 50%
    AND  profit_factor ≥ 1.5  AND  CAGR > vs_nifty_cagr
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from backtest.order_simulator import ClosedTrade

# Risk-free rate: Indian 10-year G-Sec approximate (annualised)
RISK_FREE_RATE_ANNUAL = 0.065
TRADING_DAYS_PER_YEAR = 252

# Decision-gate thresholds (from Phase 1 plan)
GATE_MIN_SHARPE = 1.0
GATE_MAX_DRAWDOWN_PCT = 25.0
GATE_MIN_WIN_RATE = 0.50
GATE_MIN_PROFIT_FACTOR = 1.5


@dataclass
class FoldMetrics:
    """Performance metrics for a single walk-forward fold."""
    fold_id: int
    start_date: str
    end_date: str

    # Equity curve
    initial_value: float = 0.0
    final_value: float = 0.0
    cagr: float = 0.0

    # Risk-adjusted returns
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0

    # Drawdown
    max_drawdown_pct: float = 0.0
    max_drawdown_duration: int = 0   # calendar days peak → trough or recovery

    # Trade statistics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_hold_days: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0

    # Versus Nifty
    vs_nifty_cagr: float = 0.0
    alpha_vs_nifty: float = 0.0

    # Turnover
    annualised_turnover: float = 0.0

    # Raw data kept for aggregation
    daily_values: list[float] = field(default_factory=list)
    daily_dates: list[str] = field(default_factory=list)
    trades: list["ClosedTrade"] = field(default_factory=list)

    @property
    def passes_gate(self) -> bool:
        return (
            self.sharpe_ratio >= GATE_MIN_SHARPE
            and self.max_drawdown_pct <= GATE_MAX_DRAWDOWN_PCT
            and self.win_rate >= GATE_MIN_WIN_RATE
            and self.profit_factor >= GATE_MIN_PROFIT_FACTOR
            and self.alpha_vs_nifty > 0
        )


@dataclass
class AggregateMetrics:
    """Performance metrics aggregated across all walk-forward folds."""
    total_folds: int = 0
    folds_passing_gate: int = 0

    # Means and medians across folds
    mean_cagr: float = 0.0
    median_cagr: float = 0.0
    mean_sharpe: float = 0.0
    median_sharpe: float = 0.0
    mean_sortino: float = 0.0
    mean_max_drawdown: float = 0.0
    worst_drawdown: float = 0.0
    mean_win_rate: float = 0.0
    mean_profit_factor: float = 0.0
    mean_alpha: float = 0.0

    # Combined equity across all folds (concatenated daily values)
    overall_cagr: float = 0.0
    overall_sharpe: float = 0.0
    overall_sortino: float = 0.0
    overall_max_drawdown: float = 0.0
    overall_win_rate: float = 0.0
    overall_profit_factor: float = 0.0
    overall_alpha: float = 0.0
    total_trades: int = 0

    # Benchmark comparison over the full period
    vs_nifty_cagr: float = 0.0

    fold_results: list[FoldMetrics] = field(default_factory=list)

    @property
    def passes_gate(self) -> bool:
        """Overall pass/fail for the Phase 1 decision gate."""
        return (
            self.overall_sharpe >= GATE_MIN_SHARPE
            and self.overall_max_drawdown <= GATE_MAX_DRAWDOWN_PCT
            and self.overall_win_rate >= GATE_MIN_WIN_RATE
            and self.overall_profit_factor >= GATE_MIN_PROFIT_FACTOR
            and self.overall_alpha > 0
        )


# ── Core metric functions ──────────────────────────────────────────────────────

def compute_cagr(initial: float, final: float, calendar_days: int) -> float:
    """CAGR = (final/initial)^(365/days) − 1.  Returns 0 on invalid input."""
    if initial <= 0 or final <= 0 or calendar_days <= 0:
        return 0.0
    years = calendar_days / 365.0
    if years < 1 / 365:
        return 0.0
    try:
        return (final / initial) ** (1.0 / years) - 1.0
    except (ZeroDivisionError, ValueError):
        return 0.0


def compute_daily_returns(daily_values: list[float]) -> np.ndarray:
    """Convert equity curve to daily % returns.  Returns empty array if < 2 values."""
    if len(daily_values) < 2:
        return np.array([])
    arr = np.array(daily_values, dtype=float)
    # pct_change equivalent — avoid division by zero
    with np.errstate(invalid="ignore", divide="ignore"):
        rets = np.diff(arr) / arr[:-1]
    return np.where(np.isfinite(rets), rets, 0.0)


def compute_sharpe(daily_returns: np.ndarray) -> float:
    """Annualised Sharpe ratio.

    Sharpe = (mean_daily_return − rf_daily) / std_daily_return × sqrt(252)
    RF daily = 6.5% / 252
    Returns 0.0 if std is zero or fewer than 2 data points.
    """
    if len(daily_returns) < 2:
        return 0.0
    rf_daily = RISK_FREE_RATE_ANNUAL / TRADING_DAYS_PER_YEAR
    excess = daily_returns - rf_daily
    std = np.std(daily_returns, ddof=1)
    if std < 1e-10:
        return 0.0
    return float(np.mean(excess) / std * math.sqrt(TRADING_DAYS_PER_YEAR))


def compute_sortino(daily_returns: np.ndarray) -> float:
    """Annualised Sortino ratio.

    Sortino = (mean_daily_return − rf_daily) / downside_std × sqrt(252)
    Downside std uses only returns below 0 (MAR = 0).
    Returns 0.0 if no downside returns or fewer than 2 points.
    """
    if len(daily_returns) < 2:
        return 0.0
    rf_daily = RISK_FREE_RATE_ANNUAL / TRADING_DAYS_PER_YEAR
    mean_excess = np.mean(daily_returns) - rf_daily
    downside = daily_returns[daily_returns < 0]
    if len(downside) == 0:
        # No negative days — Sortino is theoretically infinite; cap for display
        return 10.0
    downside_std = math.sqrt(np.mean(downside ** 2))
    if downside_std < 1e-10:
        return 0.0
    return float(mean_excess / downside_std * math.sqrt(TRADING_DAYS_PER_YEAR))


def compute_max_drawdown(daily_values: list[float]) -> tuple[float, int]:
    """Peak-to-trough max drawdown and its duration in calendar days.

    Returns:
        (max_drawdown_pct, duration_days)
        max_drawdown_pct is a positive percentage (e.g. 15.2 means −15.2%).
        duration_days is the number of days from the peak to trough (not recovery).
    """
    if len(daily_values) < 2:
        return 0.0, 0

    arr = np.array(daily_values, dtype=float)
    peak = arr[0]
    max_dd = 0.0
    peak_idx = 0
    trough_idx = 0
    cur_peak_idx = 0

    for i, val in enumerate(arr):
        if val > peak:
            peak = val
            cur_peak_idx = i
        dd = (peak - val) / peak * 100 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            peak_idx = cur_peak_idx
            trough_idx = i

    duration = trough_idx - peak_idx   # in trading days
    return round(max_dd, 4), int(duration)


def compute_trade_stats(
    trades: list["ClosedTrade"],
    avg_portfolio_value: float,
    calendar_days: int,
) -> dict:
    """Compute win rate, profit factor, hold days, turnover from closed trades.

    Returns a dict with: win_rate, profit_factor, avg_hold_days,
    avg_win_pct, avg_loss_pct, total_trades, winning_trades, losing_trades,
    annualised_turnover.
    """
    if not trades:
        return {
            "win_rate": 0.0, "profit_factor": 0.0, "avg_hold_days": 0.0,
            "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
            "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
            "annualised_turnover": 0.0,
        }

    winners = [t for t in trades if t.was_correct]
    losers  = [t for t in trades if not t.was_correct]

    win_rate = len(winners) / len(trades)

    gross_win  = sum(t.pnl for t in winners) if winners else 0.0
    gross_loss = abs(sum(t.pnl for t in losers)) if losers else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 1e-6 else (10.0 if gross_win > 0 else 0.0)

    # Hold days: use days_held field (incremented by engine each sim day)
    hold_days_list = []
    for t in trades:
        try:
            entry = pd.Timestamp(t.entry_date)
            exit_ = pd.Timestamp(t.exit_date)
            hold_days_list.append((exit_ - entry).days)
        except Exception:
            pass
    avg_hold_days = float(np.mean(hold_days_list)) if hold_days_list else 0.0

    avg_win_pct  = float(np.mean([t.return_pct for t in winners])) if winners else 0.0
    avg_loss_pct = float(np.mean([t.return_pct for t in losers]))  if losers  else 0.0

    # Annualised turnover = total_position_value_traded / avg_portfolio × (365/calendar_days)
    total_traded = sum(t.position_value for t in trades)
    if avg_portfolio_value > 0 and calendar_days > 0:
        annualised_turnover = (total_traded / avg_portfolio_value) * (365 / calendar_days)
    else:
        annualised_turnover = 0.0

    return {
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "avg_hold_days": round(avg_hold_days, 2),
        "avg_win_pct": round(avg_win_pct, 4),
        "avg_loss_pct": round(avg_loss_pct, 4),
        "total_trades": len(trades),
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "annualised_turnover": round(annualised_turnover, 4),
    }


def compute_nifty_cagr(
    market_df: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> float:
    """Buy-and-hold CAGR for Nifty 50 over the same period as the backtest fold."""
    try:
        col = "nifty_close"
        window = market_df.loc[start_date:end_date, col].dropna()
        if len(window) < 2:
            return 0.0
        calendar_days = (
            pd.Timestamp(end_date) - pd.Timestamp(start_date)
        ).days
        return compute_cagr(float(window.iloc[0]), float(window.iloc[-1]), calendar_days)
    except Exception:
        return 0.0


# ── Fold-level metric assembly ─────────────────────────────────────────────────

def compute_fold_metrics(
    fold_id: int,
    start_date: str,
    end_date: str,
    daily_values: list[float],
    daily_dates: list[str],
    trades: list["ClosedTrade"],
    market_df: pd.DataFrame,
) -> FoldMetrics:
    """Assemble all metrics for a single walk-forward fold.

    Args:
        fold_id:      Sequential fold number (1-based).
        start_date:   ISO date of fold start.
        end_date:     ISO date of fold end.
        daily_values: Equity curve (one float per sim trading day).
        daily_dates:  ISO date strings parallel to daily_values.
        trades:       All closed trades in this fold.
        market_df:    Full market DataFrame (nifty_close, vix, etc.)
    """
    m = FoldMetrics(
        fold_id=fold_id,
        start_date=start_date,
        end_date=end_date,
        daily_values=daily_values,
        daily_dates=daily_dates,
        trades=trades,
    )

    if not daily_values:
        return m

    m.initial_value = daily_values[0]
    m.final_value   = daily_values[-1]

    calendar_days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days
    m.cagr = compute_cagr(m.initial_value, m.final_value, calendar_days)

    rets = compute_daily_returns(daily_values)
    m.sharpe_ratio  = compute_sharpe(rets)
    m.sortino_ratio = compute_sortino(rets)

    m.max_drawdown_pct, m.max_drawdown_duration = compute_max_drawdown(daily_values)

    avg_portfolio = float(np.mean(daily_values)) if daily_values else m.initial_value
    stats = compute_trade_stats(trades, avg_portfolio, calendar_days)
    m.total_trades        = stats["total_trades"]
    m.winning_trades      = stats["winning_trades"]
    m.losing_trades       = stats["losing_trades"]
    m.win_rate            = stats["win_rate"]
    m.profit_factor       = stats["profit_factor"]
    m.avg_hold_days       = stats["avg_hold_days"]
    m.avg_win_pct         = stats["avg_win_pct"]
    m.avg_loss_pct        = stats["avg_loss_pct"]
    m.annualised_turnover = stats["annualised_turnover"]

    m.vs_nifty_cagr = compute_nifty_cagr(market_df, start_date, end_date)
    m.alpha_vs_nifty = m.cagr - m.vs_nifty_cagr

    return m


# ── Aggregate across all folds ─────────────────────────────────────────────────

def aggregate_fold_metrics(
    folds: list[FoldMetrics],
    market_df: pd.DataFrame,
    overall_start: str,
    overall_end: str,
) -> AggregateMetrics:
    """Combine per-fold metrics into overall walk-forward performance.

    The "overall" metrics are computed on the concatenated equity curve
    (folds stitched in time order), not as an average of fold metrics.
    This gives the true compound performance an investor would have experienced.
    """
    agg = AggregateMetrics(total_folds=len(folds), fold_results=folds)

    if not folds:
        return agg

    agg.folds_passing_gate = sum(1 for f in folds if f.passes_gate)

    # Per-fold averages
    agg.mean_cagr        = float(np.mean([f.cagr for f in folds]))
    agg.median_cagr      = float(np.median([f.cagr for f in folds]))
    agg.mean_sharpe      = float(np.mean([f.sharpe_ratio for f in folds]))
    agg.median_sharpe    = float(np.median([f.sharpe_ratio for f in folds]))
    agg.mean_sortino     = float(np.mean([f.sortino_ratio for f in folds]))
    agg.mean_max_drawdown = float(np.mean([f.max_drawdown_pct for f in folds]))
    agg.worst_drawdown   = float(np.max([f.max_drawdown_pct for f in folds]))
    agg.mean_win_rate    = float(np.mean([f.win_rate for f in folds if f.total_trades > 0]))
    agg.mean_profit_factor = float(np.mean([f.profit_factor for f in folds if f.total_trades > 0]))
    agg.mean_alpha       = float(np.mean([f.alpha_vs_nifty for f in folds]))
    agg.total_trades     = sum(f.total_trades for f in folds)

    # Concatenated equity curve → overall metrics
    all_values: list[float] = []
    for f in sorted(folds, key=lambda x: x.start_date):
        all_values.extend(f.daily_values)

    if all_values:
        overall_calendar_days = (
            pd.Timestamp(overall_end) - pd.Timestamp(overall_start)
        ).days
        agg.overall_cagr = compute_cagr(all_values[0], all_values[-1], overall_calendar_days)
        all_rets = compute_daily_returns(all_values)
        agg.overall_sharpe   = compute_sharpe(all_rets)
        agg.overall_sortino  = compute_sortino(all_rets)
        agg.overall_max_drawdown, _ = compute_max_drawdown(all_values)

    # Overall trade stats (all folds combined)
    all_trades = [t for f in folds for t in f.trades]
    if all_trades:
        overall_calendar_days = max(1, (
            pd.Timestamp(overall_end) - pd.Timestamp(overall_start)
        ).days)
        avg_pv = float(np.mean(all_values)) if all_values else 1.0
        tstats = compute_trade_stats(all_trades, avg_pv, overall_calendar_days)
        agg.overall_win_rate      = tstats["win_rate"]
        agg.overall_profit_factor = tstats["profit_factor"]

    agg.vs_nifty_cagr  = compute_nifty_cagr(market_df, overall_start, overall_end)
    agg.overall_alpha  = agg.overall_cagr - agg.vs_nifty_cagr

    return agg
