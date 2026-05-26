"""Strategy S — Low-Volatility + Quality Filter on Nifty Midcap 150.

Pre-registered hypothesis: research/hypotheses/2026-05-26-low-volatility.md

Plain-English summary
---------------------
Every month, measure how much each stock's daily price moved over the past year.
Buy the 20% that moved the least (lowest realized volatility), provided they also
have healthy earnings. Rebalance monthly.

Unlike momentum (which broke in the 2024-26 correction), low-volatility stocks
tend to lose less in market downturns — their low daily swings also mean they
drop less when the market falls. The quality filter (EPS > 0, no YoY EPS decline)
prevents "dead money" traps — stocks that are calm only because nobody trades them.

Signal construction (pre-registered — hypothesis §3)
-----------------------------------------------------
  Step 1: EPS quality filter (annual, updated each May)
    eligible = eps[fy] > 0 AND eps_growth_yoy >= 0.0

  Step 2: Realized volatility for each eligible symbol
    vol = std(daily_returns_252d, ddof=1) * sqrt(252)   # annualised
    Min 200 observations required (gap tolerance ≤ 52 missing days)

  Step 3: Long bottom 20% by vol (LOWEST volatility)
    Buffer: stay until rank > 30% (wider than momentum to reduce churn)
    Entry: open of first day of next calendar month

  Anti-portfolio: top 20% by vol (HIGHEST volatility), same quality filter

Gate:
  python -m quant.research.run --strategy low_vol --split dev
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd

from quant.research.dsr import deflated_sharpe
from quant.research.holdout_lock import assert_no_holdout_access

# Re-use shared utilities
from quant.strategies.cs_momentum import (
    build_close_pivot,
    build_open_pivot,
    compute_gate_metrics,
)
from quant.strategies.qf_momentum import (
    build_quality_filter,
    load_screener_annual,
)

logger = logging.getLogger(__name__)

# ── Pre-registered constants (hypothesis §3 — immutable) ─────────────────────
_VOL_LOOKBACK     = 252    # trading days for realized vol estimate
_MIN_OBS          = 200    # minimum daily returns required
_TOP_PCT          = 0.20   # long BOTTOM 20% by vol (lowest volatility)
_BUFFER_PCT       = 0.30   # exit when rank rises above 30%
_EPS_GROWTH_FLOOR = 0.0    # no declining EPS
_FILTER_ACTIVE_MONTH = 5   # May onwards, using March year-end results
_TRADING_DAYS     = 252    # annualisation constant

# ── Cost model (Zerodha 2026 delivery, same as Q/R) ──────────────────────────
_COST_BPS_BUY   = 1.9
_COST_BPS_SELL  = 13.5
_SLIP_BPS       = 15.0
_ROUND_TRIP_BPS = _COST_BPS_BUY + _COST_BPS_SELL + 2 * _SLIP_BPS  # ~45 bps


class MonthlyPort(NamedTuple):
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    symbols: list[str]
    n_eligible: int   # stocks passing quality filter
    n_scored: int     # stocks with enough price history
    turnover: float


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Volatility scoring
# ─────────────────────────────────────────────────────────────────────────────

def score_vol_from_pivot(
    close_pivot: pd.DataFrame,
    signal_date: pd.Timestamp,
    lookback: int = _VOL_LOOKBACK,
    min_obs: int = _MIN_OBS,
) -> pd.Series:
    """Compute annualized realized vol for all symbols at signal_date.

    Returns a Series (symbol → annualized vol) sorted ASCENDING (low vol first).
    Symbols with insufficient history are excluded automatically.

    This is vectorised: one call scores all symbols simultaneously.
    """
    dates_before = close_pivot.index[close_pivot.index <= signal_date]
    n = len(dates_before)

    if n < lookback + 2:
        return pd.Series(dtype=float, name="realized_vol")

    # Take the last `lookback + 1` price rows → `lookback` daily returns
    price_window = close_pivot.iloc[n - lookback - 1 : n]  # shape: (lookback+1, symbols)
    daily_rets = price_window.pct_change().iloc[1:]          # shape: (lookback, symbols)

    # Count valid (non-NaN) observations per symbol
    obs_count = daily_rets.notna().sum()
    valid_cols = obs_count[obs_count >= min_obs].index

    if len(valid_cols) == 0:
        return pd.Series(dtype=float, name="realized_vol")

    vols = daily_rets[valid_cols].std(ddof=1) * math.sqrt(_TRADING_DAYS)
    vols = vols.dropna()
    vols = vols[vols > 0]

    return vols.sort_values(ascending=True).rename("realized_vol")


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Monthly portfolio construction
# ─────────────────────────────────────────────────────────────────────────────

def _get_month_end_dates(
    ohlcv_dates: pd.Series,
    start: str,
    end: str,
) -> list[pd.Timestamp]:
    start_ts = pd.Timestamp(start)
    end_ts   = pd.Timestamp(end)
    signal_dates = []
    for _, grp in ohlcv_dates.groupby(ohlcv_dates.dt.to_period("M")):
        last_day = grp.max()
        if start_ts <= last_day <= end_ts:
            signal_dates.append(last_day)
    return sorted(signal_dates)


def build_monthly_portfolios(
    ohlcv: pd.DataFrame,
    symbols: list[str],
    annual_earnings: pd.DataFrame,
    start: str,
    end: str,
    close_pivot: pd.DataFrame | None = None,
    select_high_vol: bool = False,
) -> list[MonthlyPort]:
    """Build low-vol (or high-vol anti) monthly portfolios.

    Args:
        select_high_vol: If True, selects the TOP 20% by vol (anti-portfolio).
    """
    if close_pivot is None:
        close_pivot = build_close_pivot(ohlcv, symbols)

    ohlcv_dates = pd.to_datetime(ohlcv["date"].drop_duplicates().sort_values())
    signal_dates = _get_month_end_dates(ohlcv_dates, start, end)
    all_td = ohlcv_dates.sort_values().values

    def _next_open(sig: pd.Timestamp) -> pd.Timestamp:
        after = all_td[all_td > sig.to_numpy()]
        return pd.Timestamp(after[0]) if len(after) else sig

    portfolios: list[MonthlyPort] = []
    prev_symbols: set[str] = set()

    _cached_fy: int | None = None
    _cached_eligible: frozenset = frozenset()

    for sig_date in signal_dates:
        yr = sig_date.year
        mo = sig_date.month
        fiscal_year = yr if mo >= _FILTER_ACTIVE_MONTH else yr - 1

        if fiscal_year != _cached_fy:
            _cached_eligible = build_quality_filter(
                annual_earnings, fiscal_year, _EPS_GROWTH_FLOOR
            )
            _cached_fy = fiscal_year

        universe_set = frozenset(s.upper() for s in symbols)
        eligible_set = _cached_eligible & universe_set
        n_eligible = len(eligible_set)

        if n_eligible < 5:
            logger.warning("%s: only %d eligible after quality filter — skip", sig_date.date(), n_eligible)
            continue

        # Score all eligible symbols by volatility
        vols = score_vol_from_pivot(close_pivot, sig_date)
        vols = vols[vols.index.isin(eligible_set)]

        if vols.empty:
            logger.warning("%s: no vol scores in eligible universe", sig_date.date())
            continue

        n_scored = len(vols)
        n_select = max(1, int(n_eligible * _TOP_PCT))
        n_buffer = max(n_select, int(n_eligible * _BUFFER_PCT))

        # Sorted ascending → bottom = lowest vol (our long), top = highest vol (anti)
        if select_high_vol:
            # Anti-portfolio: worst (highest) volatility stocks — no buffer rule
            selected = list(vols.index[-n_select:])
            turnover = (
                len(prev_symbols - set(selected)) / max(len(prev_symbols), 1)
                if prev_symbols else 1.0
            )
        else:
            # Main portfolio: lowest vol with buffer retention
            top_set = set(vols.index[:n_select])           # lowest vol
            buffer_set = set(vols.index[:n_buffer])        # low-vol buffer zone
            retained = {s for s in prev_symbols if s in buffer_set}
            new_entries = top_set - retained
            selected = list(retained | new_entries)

            if len(selected) > n_select:
                # Sort by vol ascending (keep lowest vol when trimming)
                vol_map = vols.to_dict()
                selected.sort(key=lambda s: vol_map.get(s, 999.0))
                selected = selected[:n_select]

            turnover = (
                len(prev_symbols - set(selected)) / max(len(prev_symbols), 1)
                if prev_symbols else 1.0
            )

        portfolios.append(MonthlyPort(
            signal_date=sig_date,
            entry_date=_next_open(sig_date),
            symbols=selected,
            n_eligible=n_eligible,
            n_scored=n_scored,
            turnover=turnover,
        ))
        prev_symbols = set(selected)

        logger.debug(
            "%s: eligible=%d scored=%d select=%d | %s",
            sig_date.date(), n_eligible, n_scored, n_select,
            "HIGH-VOL" if select_high_vol else "LOW-VOL",
        )

    return portfolios


def build_anti_portfolios(
    ohlcv: pd.DataFrame,
    symbols: list[str],
    annual_earnings: pd.DataFrame,
    start: str,
    end: str,
    close_pivot: pd.DataFrame | None = None,
) -> list[MonthlyPort]:
    """High-vol anti-portfolio (top 20% by vol, same quality filter)."""
    return build_monthly_portfolios(
        ohlcv, symbols, annual_earnings, start, end,
        close_pivot=close_pivot, select_high_vol=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Simulation (reuse cs_momentum engine)
# ─────────────────────────────────────────────────────────────────────────────

def simulate_portfolio(
    portfolios: list[MonthlyPort],
    ohlcv: pd.DataFrame,
    cost_bps_round_trip: float = _ROUND_TRIP_BPS,
    costs_enabled: bool = True,
    open_pivot: pd.DataFrame | None = None,
    close_pivot: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Simulate monthly returns. No vol-target overlay — low-vol handles its own risk."""
    from quant.strategies.cs_momentum import (
        MonthlyPort as CsPort,
        simulate_portfolio as cs_simulate,
    )
    cs_ports = [
        CsPort(
            signal_date=p.signal_date,
            entry_date=p.entry_date,
            symbols=p.symbols,
            n_eligible=p.n_eligible,
            turnover=p.turnover,
        )
        for p in portfolios
    ]
    # vol_target=99.0 disables vol scaling (scalar always = 1.0 since no portfolio
    # will ever hit 99× annualized vol)
    return cs_simulate(
        cs_ports, ohlcv,
        vol_target=99.0,
        cost_bps_round_trip=cost_bps_round_trip,
        costs_enabled=costs_enabled,
        open_pivot=open_pivot,
        close_pivot=close_pivot,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Cost-stress test
# ─────────────────────────────────────────────────────────────────────────────

def run_cost_stress(
    portfolios: list[MonthlyPort],
    ohlcv: pd.DataFrame,
    n_trials: int = 1,
    n_stress_runs: int = 200,
    rng_seed: int = 42,
    open_pivot: pd.DataFrame | None = None,
    close_pivot: pd.DataFrame | None = None,
) -> dict:
    """200-run t-dist slippage stress test."""
    from scipy.stats import t as t_dist  # type: ignore[import]

    rng = np.random.default_rng(rng_seed)
    all_symbols = list({s for p in portfolios for s in p.symbols})
    if open_pivot is None:
        open_pivot = build_open_pivot(ohlcv, all_symbols)
    if close_pivot is None:
        close_pivot = build_close_pivot(ohlcv, all_symbols)

    stressed_dsrs: list[float] = []
    for _ in range(n_stress_runs):
        slip_mult = float(max(0.0, t_dist.rvs(df=4, scale=2.0, random_state=rng)))
        stressed_bps = (_COST_BPS_BUY + _COST_BPS_SELL) + 2 * _SLIP_BPS * slip_mult
        result_df = simulate_portfolio(
            portfolios, ohlcv,
            cost_bps_round_trip=stressed_bps,
            costs_enabled=True,
            open_pivot=open_pivot,
            close_pivot=close_pivot,
        )
        metrics = compute_gate_metrics(result_df, n_trials=n_trials)
        if metrics["dsr"] is not None:
            stressed_dsrs.append(metrics["dsr"])

    if not stressed_dsrs:
        return {"dsr_median": 0.0, "dsr_p10": 0.0, "dsr_p90": 0.0}
    return {
        "dsr_median": float(np.median(stressed_dsrs)),
        "dsr_p10":    float(np.percentile(stressed_dsrs, 10)),
        "dsr_p90":    float(np.percentile(stressed_dsrs, 90)),
    }
