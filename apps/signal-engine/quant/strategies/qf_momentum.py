"""Strategy Q — Quality-Filtered Momentum on Nifty Midcap 150.

Pre-registered hypothesis: research/hypotheses/2026-05-26-quality-filtered-momentum.md

Plain-English summary
---------------------
Same as Strategy P (cross-sectional 12-1 price momentum) with one extra gate:
before ranking stocks each month, remove any company whose most recent annual EPS
was negative OR declined year-over-year.

The intuition: in a strong bull market, even terrible stocks go up, which breaks
pure price momentum (Strategy P's anti-strategy check failed with DSR 0.913).
Companies with genuinely declining earnings, however, tend to lag even in bull
markets because insiders sell, analysts downgrade, and fund mandates force exits.
Excluding them should reduce the cross-sectional "rising tide" contamination.

Signal construction (pre-registered — hypothesis §3)
-----------------------------------------------------
  Step 1 — Annual quality filter (updated each April):
    eps_growth = eps[fy] / eps[fy-1] - 1.0
    eligible   = eps[fy] > 0 AND eps_growth >= 0.0
    Filter held fixed for the 12 months following April.

  Step 2 — Monthly 12-1 momentum within eligible stocks:
    score  = close[t-21] / close[t-252] - 1.0
    long   : top 10% of ELIGIBLE ranked symbols
    buffer : stay until rank drops below top 20% of ELIGIBLE universe
    entry  : open of first trading day of next calendar month

  Vol-target overlay: same as Strategy P.
    scalar = min(1.0, 0.15 / realised_20d_vol)

Gate:
  python -m quant.research.run --strategy qf_momentum --split dev
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

# Re-use pivot / scoring utilities from cs_momentum (no duplication)
from quant.strategies.cs_momentum import (
    build_close_pivot,
    build_open_pivot,
    score_universe_from_pivot,
    simulate_portfolio as _simulate_portfolio_base,
    compute_gate_metrics,
    run_cost_stress as _run_cost_stress_base,
)

logger = logging.getLogger(__name__)

# ── Pre-registered constants (hypothesis §3 — immutable) ─────────────────────
_LOOKBACK_DAYS    = 252     # 12-month return window
_SKIP_DAYS        = 21      # skip most recent month
_TOP_PCT          = 0.10    # long top 10% of filtered eligible universe
_BUFFER_PCT       = 0.20    # stay until rank drops below top 20% of filtered universe
_VOL_TARGET       = 0.15    # 15% annualised portfolio vol target
_VOL_WINDOW       = 20      # rolling days for realised-vol estimate
_TRADING_DAYS     = 252     # annualisation constant
_EPS_GROWTH_FLOOR = 0.0     # minimum YoY EPS growth to remain eligible

# Publication lag: assume March year-end results are available by April 30.
# The filter is applied starting from the MAY signal date each year.
# In code: a stock's FY_N filter is active from May of year N to April of year N+1.
_FILTER_ACTIVE_MONTH = 5    # 5 = May (filter using FY results kicks in)

# ── Cost model (same as cs_momentum / Zerodha 2026 delivery) ─────────────────
_COST_BPS_BUY   = 1.9
_COST_BPS_SELL  = 13.5
_SLIP_BPS       = 15.0
_ROUND_TRIP_BPS = _COST_BPS_BUY + _COST_BPS_SELL + 2 * _SLIP_BPS  # ~45 bps


class MonthlyPort(NamedTuple):
    """One month's portfolio snapshot."""
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    symbols: list[str]
    n_eligible: int        # stocks passing quality filter this month
    n_scored: int          # stocks with valid price scores (subset of eligible)
    turnover: float


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Annual quality filter
# ─────────────────────────────────────────────────────────────────────────────

def load_screener_annual(path: str | None = None) -> pd.DataFrame:
    """Load Screener.in annual P&L parquet.

    Returns a DataFrame with columns: symbol, fiscal_year, eps
    (and optionally sales_cr, net_profit_cr, opm_pct).
    """
    if path is None:
        path = os.path.join(
            os.path.dirname(__file__),
            "..", "..", "data", "lake", "earnings", "screener_annual.parquet",
        )
    p = Path(path)
    if not p.exists():
        logger.warning("screener_annual.parquet not found at %s", p)
        return pd.DataFrame(columns=["symbol", "fiscal_year", "eps"])
    df = pd.read_parquet(p)
    df["symbol"] = df["symbol"].str.upper().str.strip()
    return df


def build_quality_filter(
    annual: pd.DataFrame,
    fiscal_year: int,
    eps_growth_floor: float = _EPS_GROWTH_FLOOR,
) -> frozenset[str]:
    """Return the set of eligible symbols for the 12 months after April of fiscal_year.

    Eligible = profitable (EPS > 0) AND EPS growth >= eps_growth_floor.

    Args:
        annual:          Output of load_screener_annual().
        fiscal_year:     The fiscal year whose March results we're using.
                         E.g. 2023 → uses EPS from FY ending March 2023.
        eps_growth_floor: Minimum YoY EPS growth (default 0.0 = no decline).

    Returns frozenset of eligible symbol strings.
    """
    # Pivot to wide format: symbol × fiscal_year → eps
    eps_wide = (
        annual[["symbol", "fiscal_year", "eps"]]
        .dropna(subset=["eps"])
        .set_index(["symbol", "fiscal_year"])["eps"]
        .unstack("fiscal_year")
    )

    if fiscal_year not in eps_wide.columns or (fiscal_year - 1) not in eps_wide.columns:
        logger.warning(
            "EPS data for FY%d or FY%d not found — returning empty filter",
            fiscal_year, fiscal_year - 1,
        )
        return frozenset()

    eps_cur  = eps_wide[fiscal_year]
    eps_prev = eps_wide[fiscal_year - 1]
    growth   = (eps_cur / eps_prev.replace(0, float("nan"))) - 1.0

    # Eligible: currently profitable AND not declining
    eligible_mask = (eps_cur > 0) & (growth >= eps_growth_floor)
    eligible = frozenset(eps_cur[eligible_mask].index.tolist())

    n_total   = len(eps_cur.dropna())
    n_eligible = len(eligible)
    logger.info(
        "Quality filter FY%d: %d/%d eligible (%.0f%% pass)",
        fiscal_year, n_eligible, n_total,
        100.0 * n_eligible / max(n_total, 1),
    )
    return eligible


def get_eligible_symbols_for_date(
    signal_date: pd.Timestamp,
    annual: pd.DataFrame,
    universe: list[str],
    eps_growth_floor: float = _EPS_GROWTH_FLOOR,
) -> frozenset[str]:
    """Return the quality-eligible symbols for a given signal date.

    The filter is based on the most recent fiscal year whose results should be
    available by the signal date (assuming March year-end + publication by April 30).

    Rule (pre-registered §3):
      - Signal date in May–April (year Y to year Y+1):
        use FY results from March of year Y (the one just reported in April Y).
      - Signal date before May: use FY from March of year Y-1.

    Only symbols in `universe` are considered.
    """
    year = signal_date.year
    month = signal_date.month

    # After April 30: FY ending March of this year is available
    # Before May: FY ending March of last year is available
    fiscal_year = year if month >= _FILTER_ACTIVE_MONTH else year - 1

    eligible = build_quality_filter(annual, fiscal_year, eps_growth_floor)

    # Intersect with universe
    universe_set = frozenset(s.upper() for s in universe)
    return eligible & universe_set


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Monthly portfolio construction (quality-filtered)
# ─────────────────────────────────────────────────────────────────────────────

def build_monthly_portfolios(
    ohlcv: pd.DataFrame,
    symbols: list[str],
    annual_earnings: pd.DataFrame,
    start: str,
    end: str,
    close_pivot: pd.DataFrame | None = None,
) -> list[MonthlyPort]:
    """Build quality-filtered monthly portfolios from start to end.

    For each calendar month:
      1. Determine eligible symbols (EPS quality filter)
      2. Score eligible symbols by 12-1 price momentum
      3. Select top TOP_PCT with BUFFER_PCT retention
      4. Record entry date and turnover

    Args:
        ohlcv:            Flat OHLCV DataFrame (date, symbol, open, close).
        symbols:          Full Midcap 150 universe.
        annual_earnings:  Output of load_screener_annual().
        start, end:       Date range for signal dates (inclusive).
        close_pivot:      Pre-built close-price pivot (optional; built if not provided).

    Returns list of MonthlyPort in chronological order.
    """
    if close_pivot is None:
        close_pivot = build_close_pivot(ohlcv, symbols)

    ohlcv_dates = pd.to_datetime(ohlcv["date"].drop_duplicates().sort_values())
    start_ts    = pd.Timestamp(start)
    end_ts      = pd.Timestamp(end)

    # Month-end signal dates in [start, end]
    all_signal_dates: list[pd.Timestamp] = []
    for _, grp in ohlcv_dates.groupby(ohlcv_dates.dt.to_period("M")):
        last_day = grp.max()
        if start_ts <= last_day <= end_ts:
            all_signal_dates.append(last_day)
    all_signal_dates.sort()

    all_td = ohlcv_dates.sort_values().values

    def _next_open(sig: pd.Timestamp) -> pd.Timestamp:
        after = all_td[all_td > sig.to_numpy()]
        return pd.Timestamp(after[0]) if len(after) else sig

    portfolios: list[MonthlyPort] = []
    prev_symbols: set[str] = set()

    # Cache quality filter — recompute only when fiscal year changes
    _cached_fy: int | None = None
    _cached_eligible: frozenset[str] = frozenset()

    for sig_date in all_signal_dates:
        # Determine fiscal year for this signal date
        yr = sig_date.year
        mo = sig_date.month
        fiscal_year = yr if mo >= _FILTER_ACTIVE_MONTH else yr - 1

        if fiscal_year != _cached_fy:
            _cached_eligible = build_quality_filter(
                annual_earnings, fiscal_year, _EPS_GROWTH_FLOOR
            )
            _cached_fy = fiscal_year

        # Intersect quality filter with universe
        universe_set = frozenset(s.upper() for s in symbols)
        eligible_this_month = list(_cached_eligible & universe_set)
        n_eligible = len(eligible_this_month)

        if n_eligible < 5:
            logger.warning(
                "%s: only %d eligible stocks after quality filter — skipping",
                sig_date.date(), n_eligible,
            )
            continue

        # Score eligible symbols by price momentum
        scores = score_universe_from_pivot(close_pivot, sig_date)
        # Restrict to eligible universe
        eligible_set = set(eligible_this_month)
        scores = scores[scores.index.isin(eligible_set)]

        if scores.empty:
            logger.warning("No price scores within eligible universe on %s", sig_date.date())
            continue

        n_scored   = len(scores)
        top_n      = max(1, int(n_eligible * _TOP_PCT))
        buffer_n   = max(top_n, int(n_eligible * _BUFFER_PCT))

        ranked_symbols = list(scores.index)

        # Buffer rule: retain existing positions if still in top buffer_n
        top_symbols_set = set(ranked_symbols[:top_n])
        retained        = {s for s in prev_symbols if s in set(ranked_symbols[:buffer_n])}
        new_entries     = top_symbols_set - retained

        final_port = list(retained | new_entries)
        if len(final_port) > top_n:
            rank_map = {s: i for i, s in enumerate(ranked_symbols)}
            final_port.sort(key=lambda s: rank_map.get(s, 9999))
            final_port = final_port[:top_n]

        turnover = (
            len(prev_symbols - set(final_port)) / max(len(prev_symbols), 1)
            if prev_symbols else 1.0
        )

        portfolios.append(MonthlyPort(
            signal_date=sig_date,
            entry_date=_next_open(sig_date),
            symbols=final_port,
            n_eligible=n_eligible,
            n_scored=n_scored,
            turnover=turnover,
        ))
        prev_symbols = set(final_port)

        logger.debug(
            "%s: eligible=%d scored=%d top=%d positions=%s",
            sig_date.date(), n_eligible, n_scored, top_n, final_port,
        )

    return portfolios


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Anti-strategy (bottom of filtered universe)
# ─────────────────────────────────────────────────────────────────────────────

def build_anti_portfolios(
    ohlcv: pd.DataFrame,
    symbols: list[str],
    annual_earnings: pd.DataFrame,
    start: str,
    end: str,
    close_pivot: pd.DataFrame | None = None,
) -> list[MonthlyPort]:
    """Same as build_monthly_portfolios but picks BOTTOM decile of eligible universe.

    Anti-strategy hypothesis: within the quality-filtered universe, the worst
    price-momentum stocks should NOT also be profitable. If they are → the signal
    is still just market beta within the filtered universe → KILL.
    """
    if close_pivot is None:
        close_pivot = build_close_pivot(ohlcv, symbols)

    ohlcv_dates = pd.to_datetime(ohlcv["date"].drop_duplicates().sort_values())
    start_ts    = pd.Timestamp(start)
    end_ts      = pd.Timestamp(end)

    all_signal_dates: list[pd.Timestamp] = []
    for _, grp in ohlcv_dates.groupby(ohlcv_dates.dt.to_period("M")):
        last_day = grp.max()
        if start_ts <= last_day <= end_ts:
            all_signal_dates.append(last_day)
    all_signal_dates.sort()

    all_td = ohlcv_dates.sort_values().values

    def _next(sig: pd.Timestamp) -> pd.Timestamp:
        after = all_td[all_td > sig.to_numpy()]
        return pd.Timestamp(after[0]) if len(after) else sig

    portfolios: list[MonthlyPort] = []
    prev_symbols: set[str] = set()

    _cached_fy: int | None = None
    _cached_eligible: frozenset[str] = frozenset()

    for sig_date in all_signal_dates:
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
            continue

        scores = score_universe_from_pivot(close_pivot, sig_date)
        scores = scores[scores.index.isin(eligible_set)]
        if scores.empty:
            continue

        top_n  = max(1, int(n_eligible * _TOP_PCT))
        # Bottom decile: last top_n in descending scores
        worst  = list(scores.index[-top_n:])

        turnover = (
            len(prev_symbols - set(worst)) / max(len(prev_symbols), 1)
            if prev_symbols else 1.0
        )
        portfolios.append(MonthlyPort(
            signal_date=sig_date,
            entry_date=_next(sig_date),
            symbols=worst,
            n_eligible=n_eligible,
            n_scored=len(scores),
            turnover=turnover,
        ))
        prev_symbols = set(worst)

    return portfolios


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Simulation (re-use cs_momentum simulate_portfolio)
# ─────────────────────────────────────────────────────────────────────────────

def simulate_portfolio(
    portfolios: list[MonthlyPort],
    ohlcv: pd.DataFrame,
    vol_target: float = _VOL_TARGET,
    cost_bps_round_trip: float = _ROUND_TRIP_BPS,
    costs_enabled: bool = True,
    open_pivot: pd.DataFrame | None = None,
    close_pivot: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Thin wrapper: convert QF MonthlyPort list to cs_momentum format and simulate.

    qf_momentum.MonthlyPort has an extra n_scored field but is otherwise identical
    in the fields that simulate_portfolio uses (entry_date, symbols, turnover).
    """
    from quant.strategies.cs_momentum import MonthlyPort as CsPort

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
    return _simulate_portfolio_base(
        cs_ports, ohlcv,
        vol_target=vol_target,
        cost_bps_round_trip=cost_bps_round_trip,
        costs_enabled=costs_enabled,
        open_pivot=open_pivot,
        close_pivot=close_pivot,
    )


def run_cost_stress(
    portfolios: list[MonthlyPort],
    ohlcv: pd.DataFrame,
    n_trials: int = 1,
    n_stress_runs: int = 200,
    rng_seed: int = 42,
    open_pivot: pd.DataFrame | None = None,
    close_pivot: pd.DataFrame | None = None,
) -> dict:
    """200-run t-dist slippage stress test. Same logic as cs_momentum."""
    from quant.strategies.cs_momentum import MonthlyPort as CsPort

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
    return _run_cost_stress_base(
        cs_ports, ohlcv,
        n_trials=n_trials,
        n_stress_runs=n_stress_runs,
        rng_seed=rng_seed,
        open_pivot=open_pivot,
        close_pivot=close_pivot,
    )
