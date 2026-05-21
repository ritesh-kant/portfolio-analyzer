"""Strategy B — Index Reconstitution Arbitrage on NSE Nifty indices.

Pre-registered hypothesis: research/hypotheses/2026-05-21-index-recon-arb.md
Rules-based v1 — no ML model.  DSR n_trials starts at 1 (clean experiment).

Trading rule (hypothesis §7):
  1. Buy at open on announcement_date + 1 (T+1 — first full session after
     NSE announces the reconstitution, which is done post-market or intra-day).
  2. Sell at close on effective_date (the session when passive funds must
     execute to comply with their mandate).
  3. Skip events within ±30 calendar days of a Lok Sabha general election.

Gate criteria (hypothesis §4 — immutable):
  - Mean net return (T+1 open → eff_date close) >= 100 bps
  - Win rate                                     >= 50%
  - Sharpe (per-trade return / std)               >= 0.5
  - DSR (n_trials from MLflow)                    >= 0.5
  - Anti-strategy net return                      <= 0
  - Cost-stress DSR collapse                      <= 50%
  - Total dev events                              >= 15

Run the gate check:
  python -m quant.research.run --strategy index_recon --split dev
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quant.research.holdout_lock import assert_no_holdout_access

logger = logging.getLogger(__name__)

# ── Election regime filter (hypothesis §7, same ±30-day rule as PEAD-v2) ─────
# Skip any announcement_date within ±30 calendar days of a Lok Sabha general
# election (first phase date).
_ELECTION_WINDOWS: list[tuple[pd.Timestamp, pd.Timestamp]] = [
    (pd.Timestamp("2019-03-12"), pd.Timestamp("2019-06-22")),  # 2019: first phase 2019-04-11 ± 30d
    (pd.Timestamp("2024-03-20"), pd.Timestamp("2024-07-04")),  # 2024: first phase 2024-04-19 ± 30d
]


def is_election_period(date: str | pd.Timestamp) -> bool:
    """Return True if date falls within a pre-registered election filter window."""
    ts = pd.Timestamp(date)
    return any(start <= ts <= end for start, end in _ELECTION_WINDOWS)


# ── Cost model ─────────────────────────────────────────────────────────────────
# NSE equities round-trip: STT + exchange + SEBI + GST + 0.20% slippage each side
# Slightly higher than PEAD because Midcap 150 names are less liquid near announcement.
# Pre-registered in hypothesis §3.
_ROUND_TRIP_COST = 0.0055   # 55 bps


@dataclass
class TradeRecord:
    symbol: str
    index_name: str
    announcement_date: str
    effective_date: str
    entry_date: str          # T+1 open date (first trading day after announcement)
    entry_price: float       # T+1 open
    exit_price: float        # effective_date close
    gross_return: float
    net_return: float        # after _ROUND_TRIP_COST
    hold_days: int           # calendar days from announcement to effective
    slippage_draw: float = 0.0


def _next_trading_day(
    after: pd.Timestamp,
    ohlcv_dates: pd.DatetimeIndex,
) -> pd.Timestamp | None:
    """Return the first trading day strictly after `after`."""
    future = ohlcv_dates[ohlcv_dates > after]
    return future[0] if len(future) > 0 else None


def simulate_trades(
    events: pd.DataFrame,
    ohlcv: pd.DataFrame,
    slippage_scale: float = 1.0,
    rng: np.random.Generator | None = None,
) -> list[TradeRecord]:
    """Simulate index recon arbitrage trades from a set of events.

    Parameters
    ----------
    events : pd.DataFrame
        From nse_index_changes.load_events() — inclusion events only.
        Columns: announcement_date, effective_date, index_name, symbol.
    ohlcv : pd.DataFrame
        PIT-correct OHLCV from pit_loader, indexed by (business_date, symbol).
    slippage_scale : float
        Multiplier on nominal slippage (1.0 = nominal; 2.0 = cost-stress).
    rng : np.random.Generator | None
        For stochastic slippage draws in cost-stress simulation.

    Returns
    -------
    list[TradeRecord]
    """
    if events.empty or ohlcv.empty:
        return []

    # Pre-compute the sorted unique trading days once
    all_biz_dates = pd.DatetimeIndex(
        sorted(ohlcv.index.get_level_values("business_date").unique())
    )

    trades: list[TradeRecord] = []
    election_skipped = 0
    no_entry_skipped = 0
    no_exit_skipped = 0

    for _, ev in events.iterrows():
        ann_ts = pd.Timestamp(ev["announcement_date"])
        eff_ts = pd.Timestamp(ev["effective_date"])
        sym = str(ev["symbol"]).upper()

        try:
            assert_no_holdout_access(ann_ts)
        except ValueError:
            logger.warning("Skipping hold-out event: %s %s", sym, ann_ts.date())
            continue

        if is_election_period(ann_ts):
            election_skipped += 1
            continue

        # ── Entry: T+1 open ────────────────────────────────────────────────────
        entry_ts = _next_trading_day(ann_ts, all_biz_dates)
        if entry_ts is None:
            no_entry_skipped += 1
            continue

        try:
            entry_price = float(ohlcv.loc[(entry_ts, sym), "open"])
        except KeyError:
            no_entry_skipped += 1
            continue

        if entry_price < 1e-6:
            no_entry_skipped += 1
            continue

        # ── Exit: effective_date close ─────────────────────────────────────────
        # If the effective_date is not a trading day, use the nearest prior day.
        if eff_ts in all_biz_dates:
            exit_ts = eff_ts
        else:
            prior = all_biz_dates[all_biz_dates < eff_ts]
            if len(prior) == 0:
                no_exit_skipped += 1
                continue
            exit_ts = prior[-1]

        try:
            exit_price = float(ohlcv.loc[(exit_ts, sym), "close"])
        except KeyError:
            no_exit_skipped += 1
            continue

        if exit_price < 1e-6:
            no_exit_skipped += 1
            continue

        gross_return = exit_price / entry_price - 1.0
        hold_days = (eff_ts - ann_ts).days

        if rng is not None and slippage_scale > 1.0:
            slip_draw = float(rng.standard_t(df=4)) * 0.002 * slippage_scale
            total_cost = _ROUND_TRIP_COST + abs(slip_draw)
        else:
            slip_draw = 0.0
            total_cost = _ROUND_TRIP_COST

        net_return = gross_return - total_cost

        trades.append(TradeRecord(
            symbol=sym,
            index_name=str(ev["index_name"]),
            announcement_date=str(ann_ts.date()),
            effective_date=str(eff_ts.date()),
            entry_date=str(entry_ts.date()),
            entry_price=entry_price,
            exit_price=exit_price,
            gross_return=gross_return,
            net_return=net_return,
            hold_days=hold_days,
            slippage_draw=slip_draw,
        ))

    if election_skipped:
        logger.info("Election filter: skipped %d events", election_skipped)
    if no_entry_skipped:
        logger.info("No T+1 entry price: skipped %d events", no_entry_skipped)
    if no_exit_skipped:
        logger.info("No effective-date exit price: skipped %d events", no_exit_skipped)

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
    if mean_bps < 100.0:
        fail_reasons.append(f"mean_return {mean_bps:.1f} bps < 100 bps")
    if win_rate < 0.5:
        fail_reasons.append(f"win_rate {win_rate:.2%} < 50%")
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
    """Backtest the inverse signal: SHORT inclusions over the same window.

    If the anti-strategy earns positive net returns, the original signal is
    just compensating for transaction costs — not a real edge.
    Gate: anti-strategy mean net return <= 0.
    """
    trades = simulate_trades(events, ohlcv)
    for t in trades:
        t.gross_return = -t.gross_return
        t.net_return = t.gross_return - _ROUND_TRIP_COST
    metrics = compute_gate_metrics(trades, n_trials=n_trials)
    metrics["is_anti_strategy"] = True
    return metrics


def run_cost_stress(
    events: pd.DataFrame,
    ohlcv: pd.DataFrame,
    n_trials: int = 1,
    seed: int = 42,
) -> dict:
    """Re-simulate with slippage drawn from t-dist(df=4, scale=2× nominal).

    Gate: DSR must not collapse by > 50% relative to nominal.
    """
    rng = np.random.default_rng(seed)
    trades = simulate_trades(events, ohlcv, slippage_scale=2.0, rng=rng)
    metrics = compute_gate_metrics(trades, n_trials=n_trials)
    metrics["is_cost_stress"] = True
    return metrics
