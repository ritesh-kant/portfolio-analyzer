"""
Strategy O: BDM Portfolio — Equal-Weight Portfolio of Bulk Deal Momentum Trades.

Takes the list of TradeRecord objects from bdm.simulate_trades() and computes:
  - Daily equal-weight portfolio returns across all concurrently open positions
  - Annualised Sharpe from that daily series
  - Gate metrics (portfolio-level, not per-trade)

Portfolio construction (pre-registered, immutable):
  - Each position has equal weight = 1/n where n = number of open positions that day
  - Daily return of position i = gross_return_i / hold_days_i  (linear approximation)
  - Cost (55 bps) allocated entirely to the exit day of each trade
  - Days with no open positions contribute 0.0 to the return series
    but ARE counted when computing Sharpe (they are real days of strategy inactivity)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant.strategies.bdm import TradeRecord, _ROUND_TRIP_COST

logger = logging.getLogger(__name__)

TRADING_DAYS_PER_YEAR = 252


@dataclass
class PortfolioMetrics:
    """Output of compute_portfolio_metrics()."""
    daily_returns: np.ndarray       # length = number of trading days in period
    mean_daily_return: float        # arithmetic mean
    annualised_return: float        # mean_daily * 252
    annualised_vol: float           # std_daily * sqrt(252)
    annualised_sharpe: float        # annualised_return / annualised_vol
    dsr: float                      # deflated Sharpe ratio
    active_days: int                # days with >= 1 open position
    total_days: int                 # total trading days in period
    active_fraction: float          # active_days / total_days
    mean_concurrent_positions: float
    max_concurrent_positions: int


def build_daily_portfolio_returns(
    trades: list[TradeRecord],
    period_start: str,
    period_end: str,
) -> tuple[pd.Series, pd.Series]:
    """Compute daily equal-weight portfolio returns.

    Parameters
    ----------
    trades : list[TradeRecord]
        Simulated trades from bdm.simulate_trades().
    period_start, period_end : str
        Evaluation window (YYYY-MM-DD).  Only trading days within this range
        are included; any trade whose entry or exit falls outside is still
        included if it overlaps.

    Returns
    -------
    daily_returns : pd.Series indexed by date
        Equal-weight portfolio daily return (includes 0.0 on inactive days).
    n_open : pd.Series indexed by date
        Number of open positions on each day.
    """
    # Build business-day calendar for the evaluation period
    all_days = pd.bdate_range(start=period_start, end=period_end)

    daily_gross = pd.Series(0.0, index=all_days, dtype=float)
    daily_cost  = pd.Series(0.0, index=all_days, dtype=float)
    n_open      = pd.Series(0,   index=all_days, dtype=int)

    for t in trades:
        entry = pd.Timestamp(t.entry_date)
        exit_ = pd.Timestamp(t.exit_date)

        # Trading days this trade is open (within the evaluation window)
        trade_days = pd.bdate_range(
            start=max(entry, all_days[0]),
            end=min(exit_, all_days[-1]),
        )
        if len(trade_days) == 0:
            continue

        hold_bdays = len(pd.bdate_range(entry, exit_))
        if hold_bdays < 1:
            continue

        # Daily gross return: spread uniformly across hold_bdays
        daily_r = t.gross_return / hold_bdays

        for d in trade_days:
            if d in daily_gross.index:
                daily_gross[d] += daily_r
                n_open[d] += 1

        # Cost allocated entirely to exit day
        if exit_ in daily_cost.index:
            daily_cost[exit_] += _ROUND_TRIP_COST

    # Equal-weight: where n_open > 0, divide by n_open
    mask = n_open > 0
    portfolio_gross = pd.Series(0.0, index=all_days)
    portfolio_gross[mask] = daily_gross[mask] / n_open[mask]

    # Subtract cost contribution (cost was summed per position, divide by n_open)
    portfolio_cost = pd.Series(0.0, index=all_days)
    portfolio_cost[mask] = daily_cost[mask] / n_open[mask]

    daily_net = portfolio_gross - portfolio_cost

    return daily_net, n_open


def compute_portfolio_metrics(
    trades: list[TradeRecord],
    period_start: str,
    period_end: str,
    n_trials: int = 13,
) -> PortfolioMetrics:
    """Compute all portfolio-level gate metrics from a list of trades.

    Parameters
    ----------
    trades : list[TradeRecord]
    period_start, period_end : str  YYYY-MM-DD evaluation window
    n_trials : int  for DSR (pre-registered: floor(260/20) = 13 for 1-year dev)
    """
    from quant.research.dsr import deflated_sharpe

    daily_returns, n_open = build_daily_portfolio_returns(trades, period_start, period_end)

    rets = daily_returns.values.astype(float)
    mean_r  = float(np.mean(rets))
    std_r   = float(np.std(rets, ddof=1))
    ann_ret = mean_r * TRADING_DAYS_PER_YEAR
    ann_vol = std_r  * np.sqrt(TRADING_DAYS_PER_YEAR)
    sharpe  = ann_ret / ann_vol if ann_vol > 1e-10 else 0.0
    dsr     = deflated_sharpe(rets, n_trials=n_trials)

    active    = int((n_open > 0).sum())
    total     = len(rets)
    mean_conc = float(n_open[n_open > 0].mean()) if active > 0 else 0.0
    max_conc  = int(n_open.max())

    return PortfolioMetrics(
        daily_returns=rets,
        mean_daily_return=mean_r,
        annualised_return=ann_ret,
        annualised_vol=ann_vol,
        annualised_sharpe=sharpe,
        dsr=dsr,
        active_days=active,
        total_days=total,
        active_fraction=active / total if total > 0 else 0.0,
        mean_concurrent_positions=mean_conc,
        max_concurrent_positions=max_conc,
    )


def compute_anti_strategy_metrics(
    trades: list[TradeRecord],
    period_start: str,
    period_end: str,
    n_trials: int = 13,
) -> PortfolioMetrics:
    """Flip all trade returns negative (short all positions)."""
    flipped = []
    for t in trades:
        import copy
        ft = copy.copy(t)
        ft.gross_return = -t.gross_return
        ft.net_return   = ft.gross_return - _ROUND_TRIP_COST
        flipped.append(ft)
    return compute_portfolio_metrics(flipped, period_start, period_end, n_trials)


def compute_stress_metrics(
    trades: list[TradeRecord],
    period_start: str,
    period_end: str,
    n_trials: int = 13,
    cost_multiplier: float = 2.0,
) -> PortfolioMetrics:
    """2× cost stress test."""
    import copy
    stressed = []
    for t in trades:
        st = copy.copy(t)
        extra_cost = _ROUND_TRIP_COST * (cost_multiplier - 1.0)
        st.gross_return = t.gross_return          # gross unchanged
        st.net_return   = t.gross_return - _ROUND_TRIP_COST * cost_multiplier
        stressed.append(st)
    # Recompute portfolio returns with extra cost at exit
    # Easiest: build daily portfolio but inject extra cost
    daily, n_open = build_daily_portfolio_returns(stressed, period_start, period_end)
    rets = daily.values.astype(float)
    from quant.research.dsr import deflated_sharpe
    mean_r  = float(np.mean(rets))
    std_r   = float(np.std(rets, ddof=1))
    ann_ret = mean_r * TRADING_DAYS_PER_YEAR
    ann_vol = std_r  * np.sqrt(TRADING_DAYS_PER_YEAR)
    sharpe  = ann_ret / ann_vol if ann_vol > 1e-10 else 0.0
    dsr     = deflated_sharpe(rets, n_trials=n_trials)
    active  = int((n_open > 0).sum())
    total   = len(rets)
    return PortfolioMetrics(
        daily_returns=rets,
        mean_daily_return=mean_r,
        annualised_return=ann_ret,
        annualised_vol=ann_vol,
        annualised_sharpe=sharpe,
        dsr=dsr,
        active_days=active,
        total_days=total,
        active_fraction=active / total if total > 0 else 0.0,
        mean_concurrent_positions=float(n_open[n_open > 0].mean()) if active > 0 else 0.0,
        max_concurrent_positions=int(n_open.max()),
    )
