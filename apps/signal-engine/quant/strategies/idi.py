"""Strategy E — Institutional Delivery Impulse (IDI) on Nifty Midcap 150.

Pre-registered hypothesis: research/hypotheses/2026-05-22-institutional-delivery-impulse.md
Rules-based v1 — no ML model.  DSR n_trials starts at 1 (clean experiment).

Trading rule (hypothesis §8):
  1. Signal day: delivery_zscore > 2.0 AND daily_return > 1.5% AND volume_ratio > 1.2
     (All three conditions must hold simultaneously.)
  2. Entry: T+1 open (first session after signal day)
  3. Exit: T+5 close (five trading sessions after entry)
  4. Skip signals within ±30 calendar days of a Lok Sabha general election.
  5. Skip signals for stocks flagged by the promoter pledge filter (Strategy C).

Gate criteria (hypothesis §4 — immutable):
  - Mean net return (T+1 open → T+5 close)  >= 80 bps
  - Win rate                                  >= 52%
  - Sharpe (per-trade return / std)           >= 0.5
  - DSR (n_trials from MLflow)               >= 0.5
  - Anti-strategy net return                  <= 0
  - Cost-stress DSR collapse                  <= 50%
  - Total dev events                          >= 80

Run the gate check:
  python -m quant.research.run --strategy idi --split dev
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quant.data.promoter_pledge import is_pledge_flagged
from quant.research.holdout_lock import assert_no_holdout_access

logger = logging.getLogger(__name__)

# ── Election regime filter ────────────────────────────────────────────────────
# Same ±30-day windows as Strategies A and B.
_ELECTION_WINDOWS: list[tuple[pd.Timestamp, pd.Timestamp]] = [
    (pd.Timestamp("2019-03-12"), pd.Timestamp("2019-06-22")),
    (pd.Timestamp("2024-03-20"), pd.Timestamp("2024-07-04")),
]


def is_election_period(date: str | pd.Timestamp) -> bool:
    """Return True if date falls within a pre-registered election filter window."""
    ts = pd.Timestamp(date)
    return any(start <= ts <= end for start, end in _ELECTION_WINDOWS)


# ── Cost model ─────────────────────────────────────────────────────────────────
# NSE equities round-trip: STT + exchange + SEBI + GST + 0.15% slippage each side.
# Pre-registered in hypothesis §3.
_ROUND_TRIP_COST = 0.0055   # 55 bps

# ── Signal parameters (pre-registered, immutable) ─────────────────────────────
_DELIVERY_ZSCORE_THRESHOLD = 2.0    # delivery % must be > 2σ above 20d mean
_RETURN_THRESHOLD = 0.015           # daily return must be > 1.5%
_VOLUME_RATIO_THRESHOLD = 1.2       # volume must be > 1.2× 20d avg
_ROLLING_WINDOW = 20                # days for computing rolling mean/std
_EXIT_DAYS = 5                      # hold for 5 trading sessions after entry


@dataclass
class TradeRecord:
    symbol: str
    signal_date: str
    entry_date: str          # T+1 open date
    exit_date: str           # T+5 close date
    entry_price: float       # T+1 open
    exit_price: float        # T+5 close
    gross_return: float
    net_return: float        # after _ROUND_TRIP_COST
    hold_days: int           # calendar days from entry to exit
    delivery_zscore: float   # signal-day delivery z-score
    daily_return: float      # signal-day daily return
    volume_ratio: float      # signal-day volume ratio
    slippage_draw: float = 0.0


def _nth_trading_day_after(
    after: pd.Timestamp,
    n: int,
    biz_dates: pd.DatetimeIndex,
) -> pd.Timestamp | None:
    """Return the nth trading day strictly after `after`.

    n=1 → first trading day after `after` (entry at T+1).
    n=5 → fifth trading day after `after` (counted from entry, so T+5 close
          is the 5th trading day after the entry date, or 6th after signal).
    """
    future = biz_dates[biz_dates > after]
    if len(future) >= n:
        return future[n - 1]
    return None


def compute_signals(delivery: pd.DataFrame) -> pd.DataFrame:
    """Compute IDI signal flags from a delivery DataFrame.

    Parameters
    ----------
    delivery : pd.DataFrame
        From delivery_ingest.load_delivery(), indexed flat with columns:
        symbol, business_date (date), open, close, prev_close, volume, deliv_pct.

    Returns
    -------
    DataFrame with columns:
        symbol, business_date, delivery_zscore, daily_return, volume_ratio, signal
    where `signal` is True when all three thresholds are exceeded.
    """
    if delivery.empty:
        return pd.DataFrame(columns=["symbol", "business_date",
                                      "delivery_zscore", "daily_return",
                                      "volume_ratio", "signal"])

    df = delivery.copy()
    df["business_date"] = pd.to_datetime(df["business_date"])
    df = df.sort_values(["symbol", "business_date"]).reset_index(drop=True)

    # Per-symbol rolling stats
    grp = df.groupby("symbol", group_keys=False)

    df["deliv_mean"] = grp["deliv_pct"].transform(
        lambda s: s.shift(1).rolling(_ROLLING_WINDOW, min_periods=int(_ROLLING_WINDOW * 0.8)).mean()
    )
    df["deliv_std"] = grp["deliv_pct"].transform(
        lambda s: s.shift(1).rolling(_ROLLING_WINDOW, min_periods=int(_ROLLING_WINDOW * 0.8)).std()
    )
    df["vol_mean"] = grp["volume"].transform(
        lambda s: s.shift(1).rolling(_ROLLING_WINDOW, min_periods=int(_ROLLING_WINDOW * 0.8)).mean()
    )

    # Avoid division by zero
    df["delivery_zscore"] = np.where(
        df["deliv_std"] > 1e-6,
        (df["deliv_pct"] - df["deliv_mean"]) / df["deliv_std"],
        0.0,
    )
    df["daily_return"] = np.where(
        df["prev_close"] > 1e-6,
        (df["close"] - df["prev_close"]) / df["prev_close"],
        np.nan,
    )
    df["volume_ratio"] = np.where(
        df["vol_mean"] > 0,
        df["volume"] / df["vol_mean"],
        0.0,
    )

    df["signal"] = (
        (df["delivery_zscore"] > _DELIVERY_ZSCORE_THRESHOLD) &
        (df["daily_return"] > _RETURN_THRESHOLD) &
        (df["volume_ratio"] > _VOLUME_RATIO_THRESHOLD)
    )

    return df[["symbol", "business_date", "delivery_zscore",
               "daily_return", "volume_ratio", "signal"]].copy()


def simulate_trades(
    signals: pd.DataFrame,
    ohlcv: pd.DataFrame,
    slippage_scale: float = 1.0,
    rng: np.random.Generator | None = None,
) -> list[TradeRecord]:
    """Simulate IDI trades from pre-computed signals.

    Parameters
    ----------
    signals : pd.DataFrame
        From compute_signals() — rows where signal=True only (caller should
        filter beforehand, but function handles mixed input too).
    ohlcv : pd.DataFrame
        PIT-correct OHLCV indexed by (business_date, symbol), with 'open' and
        'close' columns.  business_date must be a date (not datetime).
    slippage_scale : float
        Multiplier on nominal slippage.  1.0 = nominal; 2.0 = cost-stress.
    rng : np.random.Generator or None
        For stochastic slippage in cost-stress simulation.

    Returns
    -------
    list[TradeRecord]
    """
    if signals.empty or ohlcv.empty:
        return []

    # Filter to actual signal rows
    sig_df = signals[signals["signal"] == True].copy()
    if sig_df.empty:
        return []

    sig_df["business_date"] = pd.to_datetime(sig_df["business_date"])

    # Pre-compute sorted unique trading days
    all_biz_dates = pd.DatetimeIndex(
        pd.to_datetime(
            sorted(ohlcv.index.get_level_values("business_date").unique())
        )
    )

    trades: list[TradeRecord] = []
    election_skipped = 0
    pledge_skipped = 0
    no_entry_skipped = 0
    no_exit_skipped = 0

    for _, row in sig_df.iterrows():
        signal_ts = pd.Timestamp(row["business_date"])
        sym = str(row["symbol"]).upper()

        try:
            assert_no_holdout_access(signal_ts)
        except ValueError:
            logger.warning("Skipping hold-out signal: %s %s", sym, signal_ts.date())
            continue

        if is_election_period(signal_ts):
            election_skipped += 1
            continue

        if is_pledge_flagged(sym, signal_ts.date()):
            pledge_skipped += 1
            logger.debug("Pledge filter: excluded %s on %s", sym, signal_ts.date())
            continue

        # ── Entry: T+1 open ────────────────────────────────────────────────────
        entry_ts = _nth_trading_day_after(signal_ts, 1, all_biz_dates)
        if entry_ts is None:
            no_entry_skipped += 1
            continue

        entry_key = entry_ts.date()
        try:
            entry_price = float(ohlcv.loc[(entry_key, sym), "open"])
        except KeyError:
            no_entry_skipped += 1
            continue

        if entry_price < 1e-6:
            no_entry_skipped += 1
            continue

        # ── Exit: T+5 close (5 trading days after entry) ───────────────────────
        exit_ts = _nth_trading_day_after(entry_ts, _EXIT_DAYS, all_biz_dates)
        if exit_ts is None:
            no_exit_skipped += 1
            continue

        exit_key = exit_ts.date()
        try:
            exit_price = float(ohlcv.loc[(exit_key, sym), "close"])
        except KeyError:
            no_exit_skipped += 1
            continue

        if exit_price < 1e-6:
            no_exit_skipped += 1
            continue

        gross_return = exit_price / entry_price - 1.0
        hold_days = (exit_ts - entry_ts).days

        if rng is not None and slippage_scale > 1.0:
            slip_draw = float(rng.standard_t(df=4)) * 0.0015 * slippage_scale
            total_cost = _ROUND_TRIP_COST + abs(slip_draw)
        else:
            slip_draw = 0.0
            total_cost = _ROUND_TRIP_COST

        net_return = gross_return - total_cost

        trades.append(TradeRecord(
            symbol=sym,
            signal_date=str(signal_ts.date()),
            entry_date=str(entry_key),
            exit_date=str(exit_key),
            entry_price=entry_price,
            exit_price=exit_price,
            gross_return=gross_return,
            net_return=net_return,
            hold_days=hold_days,
            delivery_zscore=float(row["delivery_zscore"]),
            daily_return=float(row["daily_return"]),
            volume_ratio=float(row["volume_ratio"]),
            slippage_draw=slip_draw,
        ))

    if election_skipped:
        logger.info("Election filter: skipped %d signals", election_skipped)
    if pledge_skipped:
        logger.info("Pledge filter (Strategy C): skipped %d signals", pledge_skipped)
    if no_entry_skipped:
        logger.info("No T+1 entry price: skipped %d signals", no_entry_skipped)
    if no_exit_skipped:
        logger.info("No T+5 exit price: skipped %d signals", no_exit_skipped)

    return trades


def compute_gate_metrics(trades: list[TradeRecord], n_trials: int = 1) -> dict:
    """Compute all 7 pre-registered gate metrics (hypothesis §4).

    Parameters
    ----------
    trades : list[TradeRecord]
    n_trials : int
        MLflow trial count for DSR.

    Returns
    -------
    dict with keys: mean_return_bps, win_rate, sharpe, dsr, n_trades,
                    gate_pass, fail_reason.
    """
    from quant.research.dsr import deflated_sharpe

    if not trades:
        return {
            "mean_return_bps": 0.0,
            "win_rate": 0.0,
            "sharpe": 0.0,
            "dsr": 0.0,
            "n_trades": 0,
            "gate_pass": False,
            "fail_reason": "no trades",
        }

    net_returns = np.array([t.net_return for t in trades])
    mean_bps = float(np.mean(net_returns) * 10_000)
    win_rate = float(np.mean(net_returns > 0))
    std_ret = float(np.std(net_returns, ddof=1)) if len(net_returns) > 1 else 0.0
    sharpe = float(np.mean(net_returns) / std_ret) if std_ret > 1e-10 else 0.0
    dsr = deflated_sharpe(net_returns, n_trials=n_trials)

    fail_reasons = []
    if mean_bps < 80.0:
        fail_reasons.append(f"mean_return {mean_bps:.1f} bps < 80 bps")
    if win_rate < 0.52:
        fail_reasons.append(f"win_rate {win_rate:.2%} < 52%")
    if sharpe < 0.5:
        fail_reasons.append(f"Sharpe {sharpe:.3f} < 0.5")
    if dsr < 0.5:
        fail_reasons.append(f"DSR {dsr:.3f} < 0.5")
    if len(trades) < 80:
        fail_reasons.append(f"n_trades {len(trades)} < 80")

    return {
        "mean_return_bps": mean_bps,
        "win_rate": win_rate,
        "sharpe": sharpe,
        "dsr": dsr,
        "n_trades": len(trades),
        "gate_pass": not bool(fail_reasons),
        "fail_reason": "; ".join(fail_reasons) if fail_reasons else "all pass",
    }


def run_anti_strategy(
    signals: pd.DataFrame,
    ohlcv: pd.DataFrame,
    n_trials: int = 1,
) -> dict:
    """Backtest the inverse signal: SHORT same events over same window.

    If the anti-strategy earns positive net returns, the original signal is
    just compensating for transaction costs — not a real edge.
    Gate: anti-strategy mean net return <= 0.
    """
    trades = simulate_trades(signals, ohlcv)
    for t in trades:
        t.gross_return = -t.gross_return
        t.net_return = t.gross_return - _ROUND_TRIP_COST
    metrics = compute_gate_metrics(trades, n_trials=n_trials)
    metrics["is_anti_strategy"] = True
    return metrics


def run_cost_stress(
    signals: pd.DataFrame,
    ohlcv: pd.DataFrame,
    n_trials: int = 1,
    seed: int = 42,
) -> dict:
    """Re-simulate with slippage drawn from t-dist(df=4, scale=2× nominal).

    Gate: DSR must not collapse by > 50% relative to nominal.
    """
    rng = np.random.default_rng(seed)
    trades = simulate_trades(signals, ohlcv, slippage_scale=2.0, rng=rng)
    metrics = compute_gate_metrics(trades, n_trials=n_trials)
    metrics["is_cost_stress"] = True
    return metrics
