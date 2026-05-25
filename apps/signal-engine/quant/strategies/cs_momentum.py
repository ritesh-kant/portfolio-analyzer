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


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Momentum score per symbol per date
# ─────────────────────────────────────────────────────────────────────────────

def _momentum_score(
    ohlcv: pd.DataFrame,
    symbol: str,
    signal_date: pd.Timestamp,
    lookback: int = _LOOKBACK_DAYS,
    skip: int = _SKIP_DAYS,
) -> float | None:
    """Compute 12-1 momentum score for one symbol at one date.

    Returns None if the required price history is unavailable (e.g., IPO
    too recent, trading halt, data gap).

    The score is (price_skip_ago / price_lookback_ago) - 1.
    Both prices must exist; no interpolation.
    """
    sym_df = ohlcv[ohlcv["symbol"] == symbol].copy()
    sym_df = sym_df[sym_df["date"] <= signal_date].sort_values("date")

    # Need at least lookback+10 rows to reliably get t_start and t_skip
    if len(sym_df) < lookback + 10:
        return None

    dates = sym_df["date"].values
    closes = sym_df["close"].values

    # t_skip index: the row that is `skip` trading days before signal_date
    if len(dates) < skip + 1:
        return None
    t_skip_price = closes[-(skip + 1)]   # price `skip` days before signal_date

    # t_start index: the row that is `lookback` trading days before signal_date
    if len(dates) < lookback + 1:
        return None
    t_start_price = closes[-(lookback + 1)]

    if t_start_price <= 0 or t_skip_price <= 0:
        return None

    return float(t_skip_price / t_start_price) - 1.0


def score_universe(
    ohlcv: pd.DataFrame,
    symbols: list[str],
    signal_date: pd.Timestamp,
) -> pd.Series:
    """Return a Series of 12-1 momentum scores for all eligible symbols.

    Index = symbol, value = score (float).  Symbols with insufficient
    history are excluded (not present in the result).
    """
    scores: dict[str, float] = {}
    for sym in symbols:
        s = _momentum_score(ohlcv, sym, signal_date)
        if s is not None:
            scores[sym] = s
    return pd.Series(scores, name="momentum_score").sort_values(ascending=False)


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Monthly portfolio construction
# ─────────────────────────────────────────────────────────────────────────────

def build_monthly_portfolios(
    ohlcv: pd.DataFrame,
    symbols: list[str],
    start: str,
    end: str,
) -> list[MonthlyPort]:
    """Build the sequence of monthly portfolios from start to end.

    For each calendar month whose last trading day falls in [start, end]:
      1. Score all symbols → rank by 12-1 momentum
      2. Select top TOP_PCT (with BUFFER_PCT retention for existing positions)
      3. Record the entry date (first trading day of next month)
      4. Compute turnover vs prior month's portfolio

    Returns a list of MonthlyPort objects in chronological order.
    """
    dates_df = ohlcv[["date"]].drop_duplicates().sort_values("date")
    trading_days = dates_df["date"].dt.to_pydatetime()

    start_ts = pd.Timestamp(start)
    end_ts   = pd.Timestamp(end)

    # Find month-end signal dates (last trading day of each month)
    all_signal_dates: list[pd.Timestamp] = []
    month_groups = dates_df.copy()
    month_groups["ym"] = pd.to_datetime(month_groups["date"]).dt.to_period("M")
    for _, grp in month_groups.groupby("ym"):
        last_day = pd.Timestamp(grp["date"].max())
        if start_ts <= last_day <= end_ts:
            all_signal_dates.append(last_day)

    all_signal_dates.sort()

    # Need the next month's first trading day for each signal date
    all_td = sorted(pd.Timestamp(d) for d in trading_days)

    def _next_month_open(sig_date: pd.Timestamp) -> pd.Timestamp:
        """Return first trading day after sig_date."""
        for d in all_td:
            if d > sig_date:
                return d
        return sig_date  # fallback (shouldn't happen)

    portfolios: list[MonthlyPort] = []
    prev_symbols: set[str] = set()
    top_n = max(1, int(len(symbols) * _TOP_PCT))
    buffer_n = max(top_n, int(len(symbols) * _BUFFER_PCT))

    for sig_date in all_signal_dates:
        scores = score_universe(ohlcv, symbols, sig_date)
        if scores.empty:
            logger.warning("No scores on %s — skipping month", sig_date.date())
            continue

        n_eligible = len(scores)
        ranked_symbols = list(scores.index)

        # Apply buffer: keep existing positions until they drop below buffer_n
        top_symbols_set = set(ranked_symbols[:top_n])
        retained = {s for s in prev_symbols if s in set(ranked_symbols[:buffer_n])}
        new_entries = top_symbols_set - retained

        # Final portfolio: retained + new entries up to top_n
        final_port = list(retained | new_entries)
        # If somehow over top_n after retention, trim lowest-ranked
        if len(final_port) > top_n:
            rank_map = {s: i for i, s in enumerate(ranked_symbols)}
            final_port.sort(key=lambda s: rank_map.get(s, 9999))
            final_port = final_port[:top_n]

        # Compute turnover
        if prev_symbols:
            dropped = prev_symbols - set(final_port)
            turnover = len(dropped) / max(len(prev_symbols), 1)
        else:
            turnover = 1.0  # first month: full deployment

        entry_date = _next_month_open(sig_date)

        portfolios.append(MonthlyPort(
            signal_date=sig_date,
            entry_date=entry_date,
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
) -> pd.DataFrame:
    """Simulate monthly portfolio returns with vol-target overlay.

    For each monthly portfolio:
      - Entry: open of entry_date
      - Exit: open of next portfolio's entry_date (or last available close)
      - Gross return: equal-weight average of constituent returns
      - Cost deduction: turnover × cost_bps_round_trip / 10000
      - Vol-target scalar: applied to the PRIOR month's realised vol
        (we only know last month's vol at the time of the new entry)

    Returns a DataFrame with columns:
      date, gross_return, cost, net_return, scalar, turnover, n_positions
    """
    if not portfolios:
        return pd.DataFrame()

    # Build a fast (symbol, date) → price lookup dict for open/close
    logger.info("Building price lookup (this may take a moment)...")
    ohlcv_indexed = ohlcv.copy()
    ohlcv_indexed["date"] = pd.to_datetime(ohlcv_indexed["date"])
    # pivot open and close for speed: date × symbol
    open_pivot = (
        ohlcv_indexed.pivot_table(index="date", columns="symbol", values="open")
    )
    close_pivot = (
        ohlcv_indexed.pivot_table(index="date", columns="symbol", values="close")
    )

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
) -> list[MonthlyPort]:
    """Same as build_monthly_portfolios but selects BOTTOM decile instead of top.

    If this also makes money → the momentum signal is just a broad long bias,
    not a cross-sectional signal → KILL the strategy.
    """
    # Temporarily monkey-patch top/buffer pct to select bottom decile
    # (we rank ascending and select "top" of that = actual bottom of momentum)
    dates_df = ohlcv[["date"]].drop_duplicates().sort_values("date")
    start_ts = pd.Timestamp(start)
    end_ts   = pd.Timestamp(end)

    month_groups = dates_df.copy()
    month_groups["ym"] = pd.to_datetime(month_groups["date"]).dt.to_period("M")
    all_signal_dates: list[pd.Timestamp] = []
    for _, grp in month_groups.groupby("ym"):
        last_day = pd.Timestamp(grp["date"].max())
        if start_ts <= last_day <= end_ts:
            all_signal_dates.append(last_day)
    all_signal_dates.sort()

    all_td = sorted(pd.Timestamp(d) for d in ohlcv["date"].drop_duplicates().values)

    def _next(sig: pd.Timestamp) -> pd.Timestamp:
        for d in all_td:
            if d > sig:
                return d
        return sig

    top_n = max(1, int(len(symbols) * _TOP_PCT))
    portfolios: list[MonthlyPort] = []

    for i, sig_date in enumerate(all_signal_dates):
        scores = score_universe(ohlcv, symbols, sig_date)
        if scores.empty:
            continue
        # Bottom decile: last `top_n` in descending score → worst performers
        worst = list(scores.index[-top_n:])
        entry_date = _next(sig_date)
        turnover = 1.0 if i == 0 else float(
            len(set(worst) - set(portfolios[-1].symbols)) / max(len(portfolios[-1].symbols), 1)
        )
        portfolios.append(MonthlyPort(
            signal_date=sig_date,
            entry_date=entry_date,
            symbols=worst,
            n_eligible=len(scores),
            turnover=turnover,
        ))

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
) -> dict:
    """Re-simulate with slippage drawn from t-dist(df=4, scale=2×nominal).

    Per plan §3.1 rule 5: strategy is killed if median DSR collapses > 50%
    under this stress model.

    Returns dict with keys: dsr_median, dsr_p10, dsr_p90, collapse_threshold
    """
    rng = np.random.default_rng(rng_seed)
    from scipy.stats import t as t_dist  # type: ignore[import]

    stressed_dsrs: list[float] = []
    for _ in range(n_stress_runs):
        # Draw slippage multiplier: t(df=4) with scale=2, floor at 0
        slip_mult = float(max(0.0, t_dist.rvs(df=4, scale=2.0, random_state=rng)))
        stressed_bps = (_COST_BPS_BUY + _COST_BPS_SELL) + 2 * _SLIP_BPS * slip_mult
        result_df = simulate_portfolio(
            portfolios, ohlcv,
            cost_bps_round_trip=stressed_bps,
            costs_enabled=True,
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
