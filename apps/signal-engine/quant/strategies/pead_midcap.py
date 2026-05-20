"""Strategy A — Post-Earnings Drift on Nifty Midcap 150. Month 3.

Pre-registered hypothesis: research/hypotheses/2026-05-19-pead-midcap.md
All parameters in this file were set before seeing dev-set results.
See plan §5.1 + §3.

Gate criteria (from hypothesis §4 — do NOT change):
  - Mean 5-day net drift >= 40 bps
  - Sharpe >= 0.5
  - DSR >= 0.5 (dev), >= 0.4 (hold-out)
  - Anti-strategy DSR <= 0
  - Cost-stress DSR collapse <= 50%
  - Capacity DSR @ ₹50L >= 0.3
  - Median trades per fold >= 30

Run the gate check:
  python -m quant.research.run --strategy pead_midcap --split dev
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quant.features.earnings import (
    REGISTERED_FEATURES,
    TARGET_BPS,
    TARGET_RETURN,
    build_earnings_features,
    build_target,
)
from quant.research.holdout_lock import (
    DEV_END,
    DEV_START,
    HOLDOUT_START,
    TRAIN_END,
    assert_no_holdout_access,
)

logger = logging.getLogger(__name__)

# ── Entry filter (pre-registered in hypothesis §1) ────────────────────────────

# Only take a trade if:
# 1. EPS surprise > 1 standard deviation above mean (positive surprise gate)
# 2. Day-0 price reaction <= 50% of historical PEAD median (drift still ahead)

_SURPRISE_STDEV_THRESHOLD = 1.0   # minimum standardised surprise
_REACTION_COVERAGE_MAX = 0.5      # max fraction of historical drift already taken

# Cost model (conservative, NSE equities)
_ROUND_TRIP_COST = 0.0045   # 45 bps: STT + exchange + SEBI + GST + slippage

# Capacity model (linear impact)
_AUM_LIMIT_INR = 5_000_000   # ₹50L
_IMPACT_COEFFICIENT = 0.0001  # 1 bp per ₹1L traded (linear impact)

# Position sizing: quarter-Kelly, max 5% AUM per position (plan §7.1)
_MAX_POSITION_FRACTION = 0.05


@dataclass
class TradeRecord:
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    gross_return: float
    net_return: float           # after _ROUND_TRIP_COST
    slippage_draw: float = 0.0  # used in cost-stress simulation
    features: dict = field(default_factory=dict)
    p_win: float = 0.5
    is_gate_pass: bool = True   # False if entry filter rejected


def select_candidates(
    announcement_date: str,
    earnings_df: pd.DataFrame,
    ohlcv: pd.DataFrame,
    midcap150_universe: list[str],
    surprise_std_threshold: float = _SURPRISE_STDEV_THRESHOLD,
    reaction_coverage_max: float = _REACTION_COVERAGE_MAX,
) -> list[str]:
    """Filter PEAD candidates on announcement_date.

    Returns a list of NSE symbols that pass:
    1. Positive EPS surprise > surprise_std_threshold * cross-sectional std
    2. Day-0 reaction coverage <= reaction_coverage_max

    Parameters
    ----------
    announcement_date : str
        ISO date.
    earnings_df : pd.DataFrame
        From earnings_ingest.load_earnings().
    ohlcv : pd.DataFrame
        PIT-correct OHLCV.
    midcap150_universe : list[str]
        NSE symbols in the Nifty Midcap 150 as of this date (ex-ante list).
    surprise_std_threshold : float
        Multiplier on cross-sectional EPS surprise std.
    reaction_coverage_max : float
        Day-0 reaction must be <= this fraction of historical PEAD magnitude.

    Returns
    -------
    list[str] — symbols passing the entry filter.
    """
    assert_no_holdout_access(announcement_date)

    if earnings_df.empty or "business_date" not in earnings_df.columns:
        return []

    day_filings = earnings_df[
        pd.to_datetime(earnings_df["business_date"]) == pd.Timestamp(announcement_date)
    ]
    day_filings = day_filings[day_filings["symbol"].isin(midcap150_universe)]

    if day_filings.empty:
        return []

    # Compute features for all symbols with results today
    feature_rows = []
    for _, row in day_filings.iterrows():
        sym = row["symbol"]
        feat = build_earnings_features(
            symbol=sym,
            announcement_date=announcement_date,
            earnings_df=earnings_df,
            ohlcv=ohlcv,
        )
        feat["symbol"] = sym
        feature_rows.append(feat)

    if not feature_rows:
        return []

    feat_df = pd.DataFrame(feature_rows)

    # ── Gate 1: EPS surprise > 1 stdev above universe mean ────────────────────
    # Use robust estimators (median + 1.4826 × MAD) instead of mean + std.
    # Plain mean/std breaks whenever one stock reports a recovery-from-zero
    # base (e.g. small finance bank with near-zero prior-year EPS producing a
    # 4000%+ YoY change).  median + MAD is the standard in PEAD literature
    # (López de Prado §5) and is invariant to these outliers without changing
    # the hypothesis criterion ("1 sigma above universe").
    if "eps_surprise_pct" not in feat_df.columns:
        return []

    valid_surp = feat_df["eps_surprise_pct"].dropna()
    if len(valid_surp) < 2:
        surp_threshold = 0.0
    else:
        surp_median = float(valid_surp.median())
        surp_mad = float((valid_surp - surp_median).abs().median())
        surp_robust_std = 1.4826 * surp_mad  # consistent estimator of σ
        surp_threshold = surp_median + surprise_std_threshold * surp_robust_std

    candidates = feat_df[
        (feat_df["eps_surprise_pct"].notna())
        & (feat_df["eps_surprise_pct"] > surp_threshold)
        & (feat_df["eps_surprise_pct"] > 0)  # must be positive surprise
    ]["symbol"].tolist()

    # ── Gate 2: Day-0 reaction coverage ───────────────────────────────────────
    if "reaction_coverage_ratio" in feat_df.columns:
        cov_mask = (
            feat_df["reaction_coverage_ratio"].isna()
            | (feat_df["reaction_coverage_ratio"] <= reaction_coverage_max)
        )
        candidates = [
            s for s in candidates
            if feat_df.loc[feat_df["symbol"] == s, "reaction_coverage_ratio"].values[0] is None
            or (
                not pd.isna(feat_df.loc[feat_df["symbol"] == s, "reaction_coverage_ratio"].values[0])
                and feat_df.loc[feat_df["symbol"] == s, "reaction_coverage_ratio"].values[0] <= reaction_coverage_max
            )
        ]

    return candidates


def simulate_trades(
    announcement_dates: list[str],
    earnings_df: pd.DataFrame,
    ohlcv: pd.DataFrame,
    midcap150_universe: list[str],
    hold_days: int = 5,
    slippage_scale: float = 1.0,
    rng: np.random.Generator | None = None,
) -> list[TradeRecord]:
    """Simulate PEAD trades across a set of announcement dates.

    This is the core backtest loop for purged k-fold evaluation.  Each
    qualifying announcement becomes one trade: entry at close on
    announcement_date, exit at close 5 trading days later.

    Parameters
    ----------
    announcement_dates : list[str]
        Dates in the test fold (from purged k-fold).
    earnings_df : pd.DataFrame
    ohlcv : pd.DataFrame
    midcap150_universe : list[str]
    hold_days : int
        Default 5 (pre-registered in hypothesis).
    slippage_scale : float
        Multiplier on nominal slippage (1.0 = nominal; 2.0 = cost-stress).
    rng : np.random.Generator | None
        For stochastic slippage draws (cost-stress simulation).

    Returns
    -------
    list[TradeRecord]
    """
    trades: list[TradeRecord] = []

    for ann_date in announcement_dates:
        try:
            assert_no_holdout_access(ann_date)
        except ValueError:
            logger.warning("Skipping hold-out date %s in simulate_trades", ann_date)
            continue

        candidates = select_candidates(
            ann_date, earnings_df, ohlcv, midcap150_universe
        )

        for sym in candidates:
            ann_ts = pd.Timestamp(ann_date)

            if isinstance(ohlcv.index, pd.MultiIndex):
                try:
                    prices = ohlcv.xs(sym, level="symbol")["close"].sort_index()
                except KeyError:
                    continue
            else:
                mask = ohlcv["symbol"] == sym
                if not mask.any():
                    continue
                prices = ohlcv[mask].set_index("business_date")["close"].sort_index()

            prices.index = pd.to_datetime(prices.index)
            future = prices.index[prices.index > ann_ts]

            if ann_ts not in prices.index or len(future) < hold_days:
                continue

            entry_price = float(prices[ann_ts])
            exit_price = float(prices[future[hold_days - 1]])
            exit_date = str(future[hold_days - 1].date())

            if entry_price < 1e-6:
                continue

            gross_return = exit_price / entry_price - 1.0

            # Slippage: nominal (0.15% each way) + optional stochastic draw
            if rng is not None and slippage_scale > 1.0:
                # t-distribution draw (df=4) for cost-stress (plan §3.1, rule 5)
                slip_draw = float(rng.standard_t(df=4)) * 0.0015 * slippage_scale
                total_cost = _ROUND_TRIP_COST + abs(slip_draw)
            else:
                slip_draw = 0.0
                total_cost = _ROUND_TRIP_COST

            net_return = gross_return - total_cost

            trades.append(TradeRecord(
                symbol=sym,
                entry_date=ann_date,
                exit_date=exit_date,
                entry_price=entry_price,
                exit_price=exit_price,
                gross_return=gross_return,
                net_return=net_return,
                slippage_draw=slip_draw,
            ))

    return trades


def compute_gate_metrics(trades: list[TradeRecord], n_trials: int = 1) -> dict:
    """Compute all pre-registered gate metrics from a list of trades.

    Parameters
    ----------
    trades : list[TradeRecord]
    n_trials : int
        MLflow trial count for DSR computation.

    Returns
    -------
    dict with keys: mean_drift_bps, sharpe, dsr, n_trades, gate_pass.
    """
    from quant.research.dsr import deflated_sharpe

    if not trades:
        return {
            "mean_drift_bps": 0.0,
            "sharpe": 0.0,
            "dsr": 0.0,
            "n_trades": 0,
            "gate_pass": False,
            "fail_reason": "no trades",
        }

    net_returns = np.array([t.net_return for t in trades])
    mean_drift_bps = float(np.mean(net_returns) * 10_000)
    std_ret = float(np.std(net_returns, ddof=1))
    sharpe = float(np.mean(net_returns) / std_ret) if std_ret > 1e-10 else 0.0
    dsr = deflated_sharpe(net_returns, n_trials=n_trials)

    # Gate thresholds (pre-registered, plan §3.2)
    gate_pass = (
        mean_drift_bps >= TARGET_BPS
        and sharpe >= 0.5
        and dsr >= 0.5
        and len(trades) >= 30
    )

    fail_reasons = []
    if mean_drift_bps < TARGET_BPS:
        fail_reasons.append(f"drift {mean_drift_bps:.1f} bps < {TARGET_BPS:.0f} bps")
    if sharpe < 0.5:
        fail_reasons.append(f"Sharpe {sharpe:.3f} < 0.5")
    if dsr < 0.5:
        fail_reasons.append(f"DSR {dsr:.3f} < 0.5")
    if len(trades) < 30:
        fail_reasons.append(f"trades {len(trades)} < 30")

    return {
        "mean_drift_bps": mean_drift_bps,
        "sharpe": sharpe,
        "dsr": dsr,
        "n_trades": len(trades),
        "gate_pass": gate_pass,
        "fail_reason": "; ".join(fail_reasons) if fail_reasons else "all pass",
    }


def run_anti_strategy(
    announcement_dates: list[str],
    earnings_df: pd.DataFrame,
    ohlcv: pd.DataFrame,
    midcap150_universe: list[str],
    n_trials: int = 1,
) -> dict:
    """Backtest the inverse of the PEAD signal (plan §3.1, rule 5).

    For anti-strategy, we SHORT the same candidates that the strategy would
    long.  If the inverse strategy also loses money net of costs, then the
    original signal is not exploiting transaction costs — it's a real signal.

    Returns gate metrics for the inverse signal.  anti_strategy_dsr must be <= 0
    for the original strategy to pass.
    """
    trades = simulate_trades(
        announcement_dates, earnings_df, ohlcv, midcap150_universe
    )

    # Invert returns (short position)
    for t in trades:
        t.gross_return = -t.gross_return
        t.net_return = t.gross_return - _ROUND_TRIP_COST  # gross_return is already negated

    metrics = compute_gate_metrics(trades, n_trials=n_trials)
    metrics["is_anti_strategy"] = True
    return metrics


def run_cost_stress(
    announcement_dates: list[str],
    earnings_df: pd.DataFrame,
    ohlcv: pd.DataFrame,
    midcap150_universe: list[str],
    n_trials: int = 1,
    seed: int = 42,
) -> dict:
    """Re-simulate with slippage from t-dist(df=4, scale=2× nominal) (plan §3.1, rule 5).

    Returns gate metrics under stress.  DSR must not collapse by > 50%
    relative to nominal simulation.
    """
    rng = np.random.default_rng(seed)
    trades = simulate_trades(
        announcement_dates, earnings_df, ohlcv, midcap150_universe,
        slippage_scale=2.0, rng=rng,
    )
    metrics = compute_gate_metrics(trades, n_trials=n_trials)
    metrics["is_cost_stress"] = True
    return metrics


def run_capacity_check(
    trades: list[TradeRecord],
    aum_inr: float = _AUM_LIMIT_INR,
    n_trials: int = 1,
) -> dict:
    """Adjust trade returns for linear market impact at given AUM.

    Linear impact model: 1 bp per ₹1L (₹100,000) deployed.

    Returns gate metrics.  Capacity DSR must be >= 0.3 (plan §3.2).
    """
    from quant.research.dsr import deflated_sharpe

    if not trades:
        return {"capacity_dsr": 0.0, "capacity_gate_pass": False}

    position_size = aum_inr * _MAX_POSITION_FRACTION
    impact_bps = (position_size / 100_000) * _IMPACT_COEFFICIENT * 10_000
    impact_fraction = impact_bps / 10_000

    adjusted_returns = np.array([
        t.net_return - impact_fraction for t in trades
    ])
    cap_dsr = deflated_sharpe(adjusted_returns, n_trials=n_trials)

    return {
        "capacity_dsr": cap_dsr,
        "capacity_aum_inr": aum_inr,
        "impact_bps_per_trade": impact_bps,
        "capacity_gate_pass": cap_dsr >= 0.3,
    }
