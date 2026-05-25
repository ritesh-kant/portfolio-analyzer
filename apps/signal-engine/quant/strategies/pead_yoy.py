"""Strategy L — PEAD v3: YoY EPS Growth Signal (No Consensus Dependency).

Pre-registered hypothesis: research/hypotheses/2026-05-24-pead-yoy.md
Rules-based v1.  DSR n_trials starts at 1 (clean experiment: pead_yoy_v1).

PEAD v2 (2026-05-20) was killed because consensus estimate data (scraped from
screener.in) was unreliable — a data quality failure, not a mechanism failure.

This strategy uses only NSE quarterly filing data already in the data lake:
  - eps_reported: actual EPS for the quarter (from nse_results.parquet)
  - yoy_eps_prev: EPS from the same quarter last year (computed at ingest time)

Signal: YoY quarterly EPS growth ≥ 25%, both quarters profitable.
Entry: T+1 open.  Exit: T+5 close.  No stop-loss.  No momentum filter.

Gate criteria (hypothesis §4 — immutable):
  - Mean net return   >= 100 bps
  - Win rate          >= 52%
  - Sharpe            >= 0.5
  - DSR               >= 0.5
  - Anti-strategy     <= 0
  - Cost-stress DSR   <= 50% collapse
  - Dev events        >= 15

Run the gate check:
  python -m quant.research.run --strategy l --split dev
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant.data.promoter_pledge import is_pledge_flagged
from quant.research.holdout_lock import assert_no_holdout_access
from quant.strategies.bdm import (
    _ROUND_TRIP_COST,
    _nth_trading_day_after,
    is_election_period,
)

logger = logging.getLogger(__name__)

# Pre-registered threshold (hypothesis §3.3 + §5, immutable)
_YOY_GROWTH_THRESHOLD = 0.25   # 25% YoY EPS growth
_EXIT_DAYS = 5                  # T+1 open → T+5 close


@dataclass
class TradeRecord:
    symbol: str
    event_date: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    gross_return: float
    net_return: float
    hold_days: int
    eps_reported: float
    yoy_eps_prev: float
    yoy_growth: float
    slippage_draw: float = 0.0


def build_events(
    earnings: pd.DataFrame,
    midcap150: set[str] | list[str] | None = None,
) -> pd.DataFrame:
    """Extract YoY EPS growth signal events from earnings data.

    Parameters
    ----------
    earnings : pd.DataFrame
        From earnings_ingest.load_earnings().
    midcap150 : set or list or None
        Nifty Midcap 150 symbols. If None, uses full earnings universe.

    Returns
    -------
    DataFrame with one row per (symbol, business_date), columns:
        symbol, event_date, eps_reported, yoy_eps_prev, yoy_growth.
    """
    df = earnings.copy()
    if df.empty:
        return pd.DataFrame(columns=[
            "symbol", "event_date", "eps_reported", "yoy_eps_prev", "yoy_growth",
        ])

    df["business_date"] = pd.to_datetime(df["business_date"]).dt.date

    # Universe filter
    if midcap150 is not None:
        universe = {s.upper() for s in midcap150}
        df = df[df["symbol"].isin(universe)]

    # Quarterly results only
    if "result_type" in df.columns:
        df = df[df["result_type"] == "quarterly"]

    # Both quarters profitable
    df = df.dropna(subset=["eps_reported", "yoy_eps_prev"])
    df = df[(df["eps_reported"] > 0) & (df["yoy_eps_prev"] > 0)]

    if df.empty:
        return pd.DataFrame(columns=[
            "symbol", "event_date", "eps_reported", "yoy_eps_prev", "yoy_growth",
        ])

    # YoY growth
    df = df.copy()
    df["yoy_growth"] = (df["eps_reported"] - df["yoy_eps_prev"]) / df["yoy_eps_prev"].abs()

    # Apply threshold
    df = df[df["yoy_growth"] >= _YOY_GROWTH_THRESHOLD]

    if df.empty:
        return pd.DataFrame(columns=[
            "symbol", "event_date", "eps_reported", "yoy_eps_prev", "yoy_growth",
        ])

    # One event per (symbol, date) — take the row with the highest growth if
    # multiple quarterly results land on the same date for the same symbol
    df = (
        df.sort_values("yoy_growth", ascending=False)
        .groupby(["symbol", "business_date"])
        .first()
        .reset_index()
        .rename(columns={"business_date": "event_date"})
    )

    return df.sort_values(["event_date", "symbol"]).reset_index(drop=True)


def simulate_trades(
    events: pd.DataFrame,
    ohlcv: pd.DataFrame,
    slippage_scale: float = 1.0,
    rng: np.random.Generator | None = None,
) -> list[TradeRecord]:
    """Simulate PEAD YoY trades: T+1 open → T+5 close."""
    if events.empty or ohlcv.empty:
        return []

    events = events.copy()
    events["event_date"] = pd.to_datetime(events["event_date"])

    all_biz_dates = pd.DatetimeIndex(
        pd.to_datetime(
            sorted(ohlcv.index.get_level_values("business_date").unique())
        )
    )

    trades: list[TradeRecord] = []
    election_skipped = 0
    pledge_skipped   = 0
    no_entry_skipped = 0
    no_exit_skipped  = 0

    for _, ev in events.iterrows():
        event_ts = pd.Timestamp(ev["event_date"])
        sym = str(ev["symbol"]).upper()

        try:
            assert_no_holdout_access(event_ts)
        except ValueError:
            logger.warning("Skipping hold-out event: %s %s", sym, event_ts.date())
            continue

        if is_election_period(event_ts):
            election_skipped += 1
            continue

        if is_pledge_flagged(sym, event_ts.date()):
            pledge_skipped += 1
            continue

        # ── Entry: T+1 open ────────────────────────────────────────────────────
        entry_ts = _nth_trading_day_after(event_ts, 1, all_biz_dates)
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

        # ── Exit: T+5 close ────────────────────────────────────────────────────
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
        hold_days    = (exit_ts - entry_ts).days

        if rng is not None and slippage_scale > 1.0:
            slip_draw  = float(rng.standard_t(df=4)) * 0.0015 * slippage_scale
            total_cost = _ROUND_TRIP_COST + abs(slip_draw)
        else:
            slip_draw  = 0.0
            total_cost = _ROUND_TRIP_COST

        net_return = gross_return - total_cost

        trades.append(TradeRecord(
            symbol=sym,
            event_date=str(event_ts.date()),
            entry_date=str(entry_key),
            exit_date=str(exit_key),
            entry_price=entry_price,
            exit_price=exit_price,
            gross_return=gross_return,
            net_return=net_return,
            hold_days=hold_days,
            eps_reported=float(ev.get("eps_reported", 0.0)),
            yoy_eps_prev=float(ev.get("yoy_eps_prev", 0.0)),
            yoy_growth=float(ev.get("yoy_growth", 0.0)),
            slippage_draw=slip_draw,
        ))

    if election_skipped:
        logger.info("Election filter: skipped %d events", election_skipped)
    if pledge_skipped:
        logger.info("Pledge filter: skipped %d events", pledge_skipped)
    if no_entry_skipped:
        logger.info("No T+1 entry price: skipped %d events", no_entry_skipped)
    if no_exit_skipped:
        logger.info("No T+5 exit price: skipped %d events", no_exit_skipped)

    return trades


def compute_gate_metrics(trades: list[TradeRecord], n_trials: int = 1) -> dict:
    """Compute all 7 pre-registered gate metrics (hypothesis §4).

    Event count gate: ≥ 15.
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
    mean_bps    = float(np.mean(net_returns) * 10_000)
    win_rate    = float(np.mean(net_returns > 0))
    std_ret     = float(np.std(net_returns, ddof=1)) if len(net_returns) > 1 else 0.0
    sharpe      = float(np.mean(net_returns) / std_ret) if std_ret > 1e-10 else 0.0
    dsr         = deflated_sharpe(net_returns, n_trials=n_trials)

    fail_reasons = []
    if mean_bps < 100.0:
        fail_reasons.append(f"mean_return {mean_bps:.1f} bps < 100 bps")
    if win_rate < 0.52:
        fail_reasons.append(f"win_rate {win_rate:.2%} < 52%")
    if sharpe < 0.5:
        fail_reasons.append(f"Sharpe {sharpe:.3f} < 0.5")
    if dsr < 0.5:
        fail_reasons.append(f"DSR {dsr:.3f} < 0.5")
    if len(trades) < 15:
        fail_reasons.append(f"n_trades {len(trades)} < 15")

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
    events: pd.DataFrame,
    ohlcv: pd.DataFrame,
    n_trials: int = 1,
) -> dict:
    """Run the inverse signal (short same events) as a sanity check."""
    trades = simulate_trades(events, ohlcv)
    for t in trades:
        t.gross_return = -t.gross_return
        t.net_return   = t.gross_return - _ROUND_TRIP_COST
    metrics = compute_gate_metrics(trades, n_trials=n_trials)
    metrics["is_anti_strategy"] = True
    return metrics


def run_cost_stress(
    events: pd.DataFrame,
    ohlcv: pd.DataFrame,
    n_trials: int = 1,
    seed: int = 42,
) -> dict:
    """Re-run with 2× slippage drawn from t-dist(df=4)."""
    rng = np.random.default_rng(seed)
    trades = simulate_trades(events, ohlcv, slippage_scale=2.0, rng=rng)
    metrics = compute_gate_metrics(trades, n_trials=n_trials)
    metrics["is_cost_stress"] = True
    return metrics
