"""Strategy P — Cross-Sectional Momentum on Nifty Midcap 150.

Pre-registered hypothesis: research/hypotheses/2026-05-25-cross-sectional-momentum.md

Plain-English summary
---------------------
Every month, rank all 150 midcap stocks by how much they went up over the past
year (skipping the most recent month to avoid short-term reversal noise).
Buy the top 15. Rebalance at the start of the next month. Repeat.

A vol-target overlay scales down the total bet when the portfolio gets choppy,
keeping the max drawdown in a manageable range.

Signal construction (pre-registered, immutable — hypothesis §3)
---------------------------------------------------------------
  lookback : 252 trading days  (≈ 12 months)
  skip     : 21 trading days   (≈ 1 month)
  score    : close[t-skip] / close[t-lookback] - 1.0
  long     : top 10% of ranked universe (TOP_PCT = 0.10 → 15 stocks from 150)
  buffer   : stay in portfolio until rank drops below top 20% (BUFFER_PCT = 0.20)
  entry    : open of first trading day of next calendar month
  exit     : replaced at next rebalance OR dropped below buffer

Vol-target overlay (hypothesis §3):
  realized_vol = annualised std of last 20 portfolio daily returns
  scalar = min(1.0, VOL_TARGET / realized_vol)   ← no leverage ever
  weight_per_stock = scalar / n_positions         ← equal-weight within portfolio

Gate:
  python -m quant.research.run --strategy momentum --split dev
"""

from __future__ import annotations

import logging
import math
from typing import NamedTuple

import numpy as np
import pandas as pd

from quant.research.dsr import deflated_sharpe
from quant.research.holdout_lock import assert_no_holdout_access

logger = logging.getLogger(__name__)

# ── Pre-registered constants (hypothesis §3 — immutable) ─────────────────────
_LOOKBACK_DAYS = 252       # 12-month return window
_SKIP_DAYS     = 21        # skip most recent month (short-term reversal avoidance)
_TOP_PCT       = 0.10      # long top 10% of ranked universe
_BUFFER_PCT    = 0.20      # stay until rank drops below top 20%
_VOL_TARGET    = 0.15      # 15% annualised portfolio vol target
_VOL_WINDOW    = 20        # rolling days for realised-vol estimate
_TRADING_DAYS  = 252       # annualisation constant

# ── Cost model constants ─────────────────────────────────────────────────────
# Kept local so strategy is self-contained for stress-testing.
# Base values from Zerodha 2026 delivery.
_COST_BPS_BUY  = 1.9       # fees per side (bps): stamp + exchange + SEBI + GST
_COST_BPS_SELL = 13.5      # fees per side: STT + DP + exchange + SEBI + GST
_SLIP_BPS      = 15.0      # slippage per side (bps), conservative midcap estimate
_ROUND_TRIP_BPS = _COST_BPS_BUY + _COST_BPS_SELL + 2 * _SLIP_BPS  # ~45 bps


class MonthlyPort(NamedTuple):
    """One month's portfolio snapshot."""
    signal_date: pd.Timestamp    # last trading day of the month (when we score)
    entry_date: pd.Timestamp     # first trading day of next month (when we enter)
    symbols: list[str]           # ordered list of long positions
    n_eligible: int              # how many stocks had valid scores this month
    turnover: float              # fraction of portfolio replaced (0.0 – 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Universe helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_constituents(path: str | None = None) -> list[str]:
    """Load Nifty Midcap 150 symbols from CSV.

    Returns upper-cased symbol list.  If file is missing, returns empty list
    and logs a warning — the caller decides whether to abort.
    """
    import os
    if path is None:
        path = os.path.join(
            os.path.dirname(__file__),
            "..", "..", "data", "lake", "midcap150_constituents.csv",
        )
    p = __import__("pathlib").Path(path)
    if not p.exists():
        logger.warning("Midcap 150 constituent file not found: %s", p)
        return []
    df = pd.read_csv(p)
    col = "symbol" if "symbol" in df.columns else df.columns[0]
    return df[col].str.upper().str.strip().tolist()


def build_close_pivot(ohlcv: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    """Build a (date × symbol) close-price pivot — built once, used for all scoring.

    This is the key performance fix: instead of filtering the 3.6M-row DataFrame
    per symbol per month, we pivot once into a matrix and do O(1) date lookups.

    Returns a DataFrame with DatetimeIndex (sorted ascending) and symbol columns.
    Symbols with no data are absent from the columns.
    """
    logger.info("Building close-price pivot for %d symbols...", len(symbols))
    # Filter to universe symbols only to reduce memory
    mask = ohlcv["symbol"].isin(set(symbols))
    sub  = ohlcv[mask][["date", "symbol", "close"]].copy()
    sub["date"] = pd.to_datetime(sub["date"])
    # If there are duplicate (date, symbol) rows keep the last
    sub = sub.drop_duplicates(subset=["date", "symbol"], keep="last")
    pivot = sub.pivot(index="date", columns="symbol", values="close").sort_index()
    logger.info("Close pivot shape: %s", pivot.shape)
    return pivot


def build_open_pivot(ohlcv: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    """Same as build_close_pivot but for open prices (used at entry/exit)."""
    mask = ohlcv["symbol"].isin(set(symbols))
    sub  = ohlcv[mask][["date", "symbol", "open"]].copy()
    sub["date"] = pd.to_datetime(sub["date"])
    sub = sub.drop_duplicates(subset=["date", "symbol"], keep="last")
    return sub.pivot(index="date", columns="symbol", values="open").sort_index()


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Vectorised momentum scoring via pivot
# ─────────────────────────────────────────────────────────────────────────────

def score_universe_from_pivot(
    close_pivot: pd.DataFrame,
    signal_date: pd.Timestamp,
    lookback: int = _LOOKBACK_DAYS,
    skip: int = _SKIP_DAYS,
) -> pd.Series:
    """Score all symbols at once using the pre-built close pivot.

    This is O(1) in terms of DataFrame filtering — just integer-index lookups
    on the sorted pivot.

    Returns a Series (symbol → score) sorted descending.
    Symbols without enough history (IPO, halt) are excluded automatically
    (they'll have NaN at the required dates → dropped by dropna).
    """
    dates_before = close_pivot.index[close_pivot.index <= signal_date]
    n = len(dates_before)

    if n < lookback + 1:
        return pd.Series(dtype=float, name="momentum_score")

    # iloc positions counting backward from signal_date
    # -(skip+1)     → price `skip` trading days before signal_date
    # -(lookback+1) → price `lookback` trading days before signal_date
    t_skip_idx    = n - skip - 1
    t_lookback_idx = n - lookback - 1

    if t_skip_idx < 0 or t_lookback_idx < 0:
        return pd.Series(dtype=float, name="momentum_score")

    price_skip    = close_pivot.iloc[t_skip_idx]      # Series: symbol → price
    price_lookback = close_pivot.iloc[t_lookback_idx]  # Series: symbol → price

    # Vectorised score: all symbols at once
    scores = (price_skip / price_lookback) - 1.0

    # Drop symbols with NaN (missing data at either date) or non-positive prices
    scores = scores.dropna()
    scores = scores[scores.notna() & (price_skip > 0) & (price_lookback > 0)]

    return scores.sort_values(ascending=False).rename("momentum_score")


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Monthly portfolio construction
# ─────────────────────────────────────────────────────────────────────────────

def build_monthly_portfolios(
    ohlcv: pd.DataFrame,
    symbols: list[str],
    start: str,
    end: str,
    close_pivot: pd.DataFrame | None = None,
) -> list[MonthlyPort]:
    """Build the sequence of monthly portfolios from start to end.

    For each calendar month whose last trading day falls in [start, end]:
      1. Score all symbols via the close pivot → rank by 12-1 momentum
      2. Select top TOP_PCT (with BUFFER_PCT retention for existing positions)
      3. Record the entry date (first trading day of next month)
      4. Compute turnover vs prior month's portfolio

    Args:
        ohlcv:       Flat DataFrame with columns date, symbol, open, close.
        symbols:     Universe symbol list.
        start, end:  Date range for signal dates (inclusive).
        close_pivot: Optional pre-built close pivot.  If None, built here.
                     Pass it in from the caller to avoid rebuilding.

    Returns a list of MonthlyPort objects in chronological order.
    """
    if close_pivot is None:
        close_pivot = build_close_pivot(ohlcv, symbols)

    ohlcv_dates = pd.to_datetime(ohlcv["date"].drop_duplicates().sort_values())
    start_ts = pd.Timestamp(start)
    end_ts   = pd.Timestamp(end)

    # Month-end signal dates: last trading day of each calendar month in range
    all_signal_dates: list[pd.Timestamp] = []
    for period, grp in ohlcv_dates.groupby(ohlcv_dates.dt.to_period("M")):
        last_day = grp.max()
        if start_ts <= last_day <= end_ts:
            all_signal_dates.append(last_day)
    all_signal_dates.sort()

    # Fast next-trading-day lookup: sorted array of all trading days
    all_td = ohlcv_dates.sort_values().values

    def _next_open(sig: pd.Timestamp) -> pd.Timestamp:
        after = all_td[all_td > sig.to_numpy()]
        return pd.Timestamp(after[0]) if len(after) else sig

    top_n    = max(1, int(len(symbols) * _TOP_PCT))
    buffer_n = max(top_n, int(len(symbols) * _BUFFER_PCT))

    portfolios: list[MonthlyPort] = []
    prev_symbols: set[str] = set()

    for sig_date in all_signal_dates:
        scores = score_universe_from_pivot(close_pivot, sig_date)
        if scores.empty:
            logger.warning("No scores on %s — skipping month", sig_date.date())
            continue

        ranked_symbols = list(scores.index)
        n_eligible     = len(ranked_symbols)

        # Apply buffer: keep existing positions until they fall below buffer_n rank
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
            turnover=turnover,
        ))
        prev_symbols = set(final_port)

    return portfolios


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Portfolio simulation
# ─────────────────────────────────────────────────────────────────────────────

def _get_price(ohlcv: pd.DataFrame, symbol: str, date: pd.Timestamp, col: str) -> float | None:
    """Look up a single price safely."""
    rows = ohlcv[(ohlcv["symbol"] == symbol) & (ohlcv["date"] == date)]
    if rows.empty:
        return None
    val = rows[col].iloc[0]
    return float(val) if pd.notna(val) and val > 0 else None


def simulate_portfolio(
    portfolios: list[MonthlyPort],
    ohlcv: pd.DataFrame,
    vol_target: float = _VOL_TARGET,
    cost_bps_round_trip: float = _ROUND_TRIP_BPS,
    costs_enabled: bool = True,
    open_pivot: pd.DataFrame | None = None,
    close_pivot: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Simulate monthly portfolio returns with vol-target overlay.

    For each monthly portfolio:
      - Entry: open of entry_date
      - Exit: open of next portfolio's entry_date (or last available close)
      - Gross return: equal-weight average of constituent returns
      - Cost deduction: turnover × cost_bps_round_trip / 10000
      - Vol-target scalar: applied to the PRIOR month's realised vol

    Args:
        portfolios:          Output of build_monthly_portfolios().
        ohlcv:               Flat OHLCV DataFrame (used only if pivots not provided).
        open_pivot:          Pre-built date×symbol open price pivot (recommended).
        close_pivot:         Pre-built date×symbol close price pivot (fallback exit).

    Returns a DataFrame with columns:
      date, gross_return, cost, net_return, scalar, turnover, n_positions
    """
    if not portfolios:
        return pd.DataFrame()

    # Build pivots if not provided (expensive — pass them in when calling repeatedly)
    all_symbols = list({s for p in portfolios for s in p.symbols})
    if open_pivot is None:
        logger.info("Building open-price pivot...")
        open_pivot = build_open_pivot(ohlcv, all_symbols)
    if close_pivot is None:
        logger.info("Building close-price pivot...")
        close_pivot = build_close_pivot(ohlcv, all_symbols)

    records: list[dict] = []
    daily_returns_history: list[float] = []  # rolling 20-day buffer for vol estimate

    for i, port in enumerate(portfolios):
        entry_date = port.entry_date
        exit_date  = (
            portfolios[i + 1].entry_date
            if i + 1 < len(portfolios)
            else pd.Timestamp("2099-01-01")
        )

        # Get entry prices (open on entry_date)
        entry_prices: dict[str, float] = {}
        for sym in port.symbols:
            try:
                p = open_pivot.loc[entry_date, sym] if entry_date in open_pivot.index else None
            except KeyError:
                p = None
            if p is not None and not math.isnan(p) and p > 0:
                entry_prices[sym] = float(p)

        if not entry_prices:
            logger.warning("No entry prices on %s — skipping month", entry_date.date())
            continue

        # Get exit prices (open on exit_date, fallback to last available close)
        constituent_returns: list[float] = []
        for sym, ep in entry_prices.items():
            xp = None
            if exit_date in open_pivot.index:
                v = open_pivot.loc[exit_date, sym]
                if pd.notna(v) and v > 0:
                    xp = float(v)
            if xp is None:
                # Fallback: use last close before exit_date
                sym_closes = close_pivot[sym].dropna() if sym in close_pivot.columns else pd.Series(dtype=float)
                before_exit = sym_closes[sym_closes.index < exit_date]
                if not before_exit.empty and before_exit.iloc[-1] > 0:
                    xp = float(before_exit.iloc[-1])
            if xp is not None and ep > 0:
                r = (xp / ep) - 1.0
                constituent_returns.append(r)

        if not constituent_returns:
            continue

        gross_return = float(np.mean(constituent_returns))

        # Cost deduction (proportional to turnover)
        cost_fraction = (
            port.turnover * cost_bps_round_trip / 10_000.0
            if costs_enabled else 0.0
        )
        net_return_pre_scalar = gross_return - cost_fraction

        # Vol-target scalar (based on PRIOR period realised vol)
        if len(daily_returns_history) >= _VOL_WINDOW:
            recent = np.array(daily_returns_history[-_VOL_WINDOW:])
            realised_vol = float(np.std(recent, ddof=1)) * math.sqrt(_TRADING_DAYS)
            scalar = min(1.0, vol_target / realised_vol) if realised_vol > 1e-6 else 1.0
        else:
            scalar = 1.0  # not enough history yet; full exposure

        net_return = net_return_pre_scalar * scalar

        # Store this month's daily approximation for rolling vol estimate
        # (Monthly return → rough daily = r / 21)
        daily_approx = net_return_pre_scalar / 21.0
        daily_returns_history.extend([daily_approx] * 21)

        records.append({
            "date":        entry_date,
            "gross_return": gross_return,
            "cost":        cost_fraction,
            "net_return":  net_return,
            "scalar":      scalar,
            "turnover":    port.turnover,
            "n_positions": len(entry_prices),
        })

    return pd.DataFrame(records).set_index("date")


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Gate metrics
# ─────────────────────────────────────────────────────────────────────────────

def compute_gate_metrics(
    returns_df: pd.DataFrame,
    benchmark_df: pd.DataFrame | None = None,
    n_trials: int = 1,
) -> dict:
    """Compute all 8 gate criteria from a monthly-returns DataFrame.

    Args:
        returns_df:   Output of simulate_portfolio().  Must have 'net_return' column.
        benchmark_df: Optional DataFrame with 'date' index and 'benchmark_return'
                      column (monthly returns of Nifty Midcap 150 TRI proxy).
                      If None, alpha is reported as None.
        n_trials:     MLflow trial count for DSR deflation.

    Returns a dict with keys matching the 8 gate criteria.
    """
    if returns_df.empty or "net_return" not in returns_df.columns:
        return {k: None for k in [
            "n_months", "mean_monthly_return", "annualised_return",
            "sharpe", "dsr", "max_drawdown", "excess_return_vs_benchmark",
            "n_trials",
        ]}

    rets = returns_df["net_return"].dropna().values
    n = len(rets)
    mean_monthly = float(np.mean(rets))
    std_monthly  = float(np.std(rets, ddof=1)) if n > 1 else 0.0

    # Annualised Sharpe (using monthly returns)
    sharpe = (mean_monthly / std_monthly * math.sqrt(12)) if std_monthly > 1e-8 else 0.0

    # DSR — deflated Sharpe ratio accounting for multiple trials.
    # deflated_sharpe(returns_array, n_trials) — passes raw monthly returns.
    dsr = deflated_sharpe(rets, n_trials=n_trials)

    # Annualised return (geometric)
    ann_return = (1.0 + mean_monthly) ** 12 - 1.0

    # Max drawdown from equity curve
    equity = np.cumprod(1.0 + rets)
    peak = np.maximum.accumulate(equity)
    drawdowns = (equity - peak) / peak
    max_dd = float(np.min(drawdowns))  # most negative value

    # Excess return vs benchmark
    excess_return = None
    if benchmark_df is not None and not benchmark_df.empty:
        bench = benchmark_df.reindex(returns_df.index)["benchmark_return"].dropna()
        aligned_net = returns_df["net_return"].reindex(bench.index).dropna()
        if len(aligned_net) > 0:
            ann_bench = (1.0 + float(bench.mean())) ** 12 - 1.0
            ann_strat = (1.0 + float(aligned_net.mean())) ** 12 - 1.0
            excess_return = ann_strat - ann_bench

    return {
        "n_months":                  n,
        "mean_monthly_return":       mean_monthly,
        "annualised_return":         ann_return,
        "sharpe":                    sharpe,
        "dsr":                       dsr,
        "max_drawdown":              max_dd,
        "excess_return_vs_benchmark": excess_return,
        "n_trials":                  n_trials,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Anti-strategy check (bottom decile)
# ─────────────────────────────────────────────────────────────────────────────

def build_anti_portfolios(
    ohlcv: pd.DataFrame,
    symbols: list[str],
    start: str,
    end: str,
    close_pivot: pd.DataFrame | None = None,
) -> list[MonthlyPort]:
    """Same as build_monthly_portfolios but selects BOTTOM decile instead of top.

    If this also makes money → the momentum signal is just a broad long bias,
    not a cross-sectional signal → KILL the strategy.
    """
    if close_pivot is None:
        close_pivot = build_close_pivot(ohlcv, symbols)

    ohlcv_dates = pd.to_datetime(ohlcv["date"].drop_duplicates().sort_values())
    start_ts = pd.Timestamp(start)
    end_ts   = pd.Timestamp(end)

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

    top_n = max(1, int(len(symbols) * _TOP_PCT))
    portfolios: list[MonthlyPort] = []
    prev_symbols: set[str] = set()

    for sig_date in all_signal_dates:
        scores = score_universe_from_pivot(close_pivot, sig_date)
        if scores.empty:
            continue
        # Bottom decile: last `top_n` entries in descending scores = worst performers
        worst = list(scores.index[-top_n:])
        turnover = (
            len(prev_symbols - set(worst)) / max(len(prev_symbols), 1)
            if prev_symbols else 1.0
        )
        portfolios.append(MonthlyPort(
            signal_date=sig_date,
            entry_date=_next(sig_date),
            symbols=worst,
            n_eligible=len(scores),
            turnover=turnover,
        ))
        prev_symbols = set(worst)

    return portfolios


# ─────────────────────────────────────────────────────────────────────────────
# 7.  Cost-stress test (2× slippage via t-distribution)
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
    """Re-simulate with slippage drawn from t-dist(df=4, scale=2×nominal).

    Per plan §3.1 rule 5: strategy is killed if median DSR collapses > 50%
    under this stress model.

    Pass open_pivot and close_pivot to avoid rebuilding them 200 times.
    Returns dict with keys: dsr_median, dsr_p10, dsr_p90
    """
    rng = np.random.default_rng(rng_seed)
    from scipy.stats import t as t_dist  # type: ignore[import]

    # Build pivots once if not provided
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
