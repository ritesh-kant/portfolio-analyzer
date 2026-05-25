"""Strategy I — Block Deal Momentum (BlockDM) on Nifty Midcap 150.

Pre-registered hypothesis: research/hypotheses/2026-05-23-block-deal-momentum.md
Rules-based v1.  DSR n_trials starts at 1 (clean experiment).

Strategies F/G/H used NSE bulk deals — post-mortem showed bulk deals are dominated
by algorithmic traders (~94%), not institutional investors.  Block deals are
pre-negotiated off-market transactions (≥ ₹10 crore, NSE block window 8:45-9 AM)
that are ALWAYS institutional.

Trading rule (hypothesis §5):
  1. Signal: NSE block deal BUY in a Nifty Midcap 150 stock.
  2. Momentum filter: stock close on signal day T must be > 50-day EMA.
  3. Entry: T+1 open.  Exit: T+20 close.
  4. Skip events within ±30 calendar days of a Lok Sabha general election.
  5. Skip events for stocks flagged by the promoter pledge filter (Strategy C).
  6. Aggregate multiple block deals for the same (symbol, date): one event.

Gate criteria (hypothesis §4 — immutable):
  - Mean net return (T+1 open → T+20 close)  >= 100 bps
  - Win rate                                   >= 52%
  - Sharpe (per-trade return / std)            >= 0.5
  - DSR (n_trials from MLflow)                >= 0.5
  - Anti-strategy net return                   <= 0
  - Cost-stress DSR collapse                   <= 50%
  - Total dev events (after all filters)       >= 15

Run the gate check:
  python -m quant.research.run --strategy i --split dev
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant.data.promoter_pledge import is_pledge_flagged
from quant.research.holdout_lock import assert_no_holdout_access
from quant.strategies.bdm import (
    _EXIT_DAYS,
    _ROUND_TRIP_COST,
    _nth_trading_day_after,
    is_election_period,
)
from quant.strategies.bdm_momentum import compute_ema50

logger = logging.getLogger(__name__)


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
    deal_value_cr: float
    client_name: str
    ema50: float
    close_t: float
    slippage_draw: float = 0.0


def build_events(
    block_deals: pd.DataFrame,
    midcap150: set[str] | list[str] | None = None,
) -> pd.DataFrame:
    """Extract and aggregate block deal signal events.

    Parameters
    ----------
    block_deals : pd.DataFrame
        From block_deals.load_block_deals(side='BUY').
    midcap150 : set or list or None

    Returns
    -------
    DataFrame with one row per (symbol, business_date), columns:
        symbol, event_date, total_value_cr, client_names.
    """
    df = block_deals.copy()
    if df.empty:
        return pd.DataFrame(columns=["symbol", "event_date",
                                      "total_value_cr", "client_names"])

    df["business_date"] = pd.to_datetime(df["business_date"]).dt.date

    if midcap150 is not None:
        universe = {s.upper() for s in midcap150}
        df = df[df["symbol"].isin(universe)]

    if df.empty:
        return pd.DataFrame(columns=["symbol", "event_date",
                                      "total_value_cr", "client_names"])

    df = df[df["side"] == "BUY"]

    agg = (
        df.groupby(["symbol", "business_date"])
        .agg(
            total_value_cr=("value_cr", "sum"),
            client_names=("client_name", lambda x: " | ".join(x.unique())),
        )
        .reset_index()
        .rename(columns={"business_date": "event_date"})
    )

    return agg.sort_values(["event_date", "symbol"]).reset_index(drop=True)


def simulate_trades(
    events: pd.DataFrame,
    ohlcv: pd.DataFrame,
    slippage_scale: float = 1.0,
    rng: np.random.Generator | None = None,
    ema50: pd.Series | None = None,
) -> list[TradeRecord]:
    """Simulate Block Deal Momentum trades."""
    if events.empty or ohlcv.empty:
        return []

    if ema50 is None:
        ema50 = compute_ema50(ohlcv)

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
    momentum_skipped = 0
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

        # ── Momentum filter ────────────────────────────────────────────────────
        event_date = event_ts.date()
        try:
            close_t = float(ohlcv.loc[(event_date, sym), "close"])
            ema_t   = float(ema50.loc[(event_date, sym)])
        except KeyError:
            momentum_skipped += 1
            continue

        if np.isnan(ema_t) or close_t <= ema_t:
            momentum_skipped += 1
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

        # ── Exit: T+20 close ───────────────────────────────────────────────────
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
            deal_value_cr=float(ev.get("total_value_cr", 0.0)),
            client_name=str(ev.get("client_names", "")),
            ema50=ema_t,
            close_t=close_t,
            slippage_draw=slip_draw,
        ))

    if election_skipped:
        logger.info("Election filter: skipped %d events", election_skipped)
    if pledge_skipped:
        logger.info("Pledge filter: skipped %d events", pledge_skipped)
    if momentum_skipped:
        logger.info("Momentum filter (close ≤ EMA50): skipped %d events", momentum_skipped)
    if no_entry_skipped:
        logger.info("No T+1 entry price: skipped %d events", no_entry_skipped)
    if no_exit_skipped:
        logger.info("No T+20 exit price: skipped %d events", no_exit_skipped)

    return trades


def compute_gate_metrics(trades: list[TradeRecord], n_trials: int = 1) -> dict:
    """Compute all 7 pre-registered gate metrics (hypothesis §4).

    Event count gate for Strategy I is ≥ 15 (pre-registered).
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
    if len(trades) < 15:          # ← 15 for I (pre-registered)
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
    ema50: pd.Series | None = None,
) -> dict:
    trades = simulate_trades(events, ohlcv, ema50=ema50)
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
    ema50: pd.Series | None = None,
) -> dict:
    rng = np.random.default_rng(seed)
    trades = simulate_trades(events, ohlcv, slippage_scale=2.0, rng=rng, ema50=ema50)
    metrics = compute_gate_metrics(trades, n_trials=n_trials)
    metrics["is_cost_stress"] = True
    return metrics
