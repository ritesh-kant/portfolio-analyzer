"""Trailing stop-loss logic for news-trader positions.

Rules (from plan):
  - Initial SL = entry_price * (1 - SL_PCT)
  - SL = highest_price_seen * (1 - SL_PCT)
  - SL only moves UP, never down
  - Target = entry_price * (1 + TARGET_PCT)
  - Day 5 force-close at market open

Cost model: Indian equity delivery round-trip.
  Brokerage ₹20/order (Zerodha flat), STT 0.1% each side, NSE exchange ~0.00345%,
  stamp duty 0.015% buy-side, GST 18% on (brokerage + exchange), slippage 5 bps/side.
"""

import math
from typing import Literal

# Indian equity delivery cost constants
_BROKERAGE_PER_ORDER = 20.0   # ₹20 flat per order, capped at 0.03% of turnover
_BROKERAGE_CAP_RATE = 0.0003  # 0.03% cap
_STT_RATE = 0.001              # 0.1% each side (delivery)
_EXCHANGE_RATE = 0.0000345     # NSE exchange + SEBI per side (~0.00345%)
_STAMP_RATE = 0.00015          # 0.015% on buy-side only
_GST_RATE = 0.18               # 18% on (brokerage + exchange charges)
_SLIPPAGE_RATE = 0.0005        # 5 bps per side (market impact estimate)

ExitReason = Literal["sl_hit", "target_hit", "day5"]


def initial_trailing_sl(entry_price: float, sl_pct: float) -> float:
    return entry_price * (1.0 - sl_pct)


def update_trailing_sl(
    current_price: float,
    highest_price: float,
    current_sl: float,
    sl_pct: float,
) -> tuple[float, float]:
    """LEGACY pure-trailing stop. Retained for positions opened before the
    split-stop change (see update_stop). Returns (new_highest_price, new_sl).
    SL never decreases."""
    new_highest = max(highest_price, current_price)
    new_sl = new_highest * (1.0 - sl_pct)
    return new_highest, max(current_sl, new_sl)


def initial_stop(entry_price: float, initial_sl_pct: float) -> float:
    """Wide initial stop placed at entry, before trailing activates."""
    return entry_price * (1.0 - initial_sl_pct)


def update_stop(
    entry_price: float,
    current_price: float,
    highest_price: float,
    current_sl: float,
    initial_sl_pct: float,
    trail_sl_pct: float,
    trail_activate_pct: float,
) -> tuple[float, float]:
    """Split stop: wide initial stop until the trade is in profit, then a tight
    trailing stop below the high. Returns (new_highest_price, new_sl).

    - Before the high reaches entry*(1 + trail_activate_pct): stop sits at the
      wide floor entry*(1 - initial_sl_pct) — room to survive entry noise.
    - Once activated: stop trails at high*(1 - trail_sl_pct), locking in profit.
    - SL never decreases (max against current_sl), so activation can only raise it.
    """
    new_highest = max(highest_price, current_price)
    activated = new_highest >= entry_price * (1.0 + trail_activate_pct)
    if activated:
        candidate = new_highest * (1.0 - trail_sl_pct)
    else:
        candidate = entry_price * (1.0 - initial_sl_pct)
    return new_highest, max(current_sl, candidate)


def check_exit(
    current_price: float,
    trailing_sl: float,
    target_price: float,
    held_sessions: int,
    max_hold_days: int,
) -> ExitReason | None:
    """Returns the exit reason if any exit condition is met, else None.

    Priority: sl_hit > target_hit > day5.

    held_sessions must be pre-computed trading days (not calendar days) by the
    caller — see sl_monitor._trading_days_held(). Keeping this function pure
    avoids a calendar dependency and makes unit tests trivial.
    """
    if current_price <= trailing_sl:
        return "sl_hit"
    if current_price >= target_price:
        return "target_hit"
    if held_sessions >= max_hold_days:
        return "day5"
    return None


def calc_costs(
    entry_price: float,
    exit_price: float,
    qty: int,
) -> dict[str, float]:
    """Returns itemised round-trip costs (INR) for Indian equity delivery."""
    entry_val = entry_price * qty
    exit_val = exit_price * qty

    brokerage = round(
        min(_BROKERAGE_PER_ORDER, entry_val * _BROKERAGE_CAP_RATE)
        + min(_BROKERAGE_PER_ORDER, exit_val * _BROKERAGE_CAP_RATE),
        2,
    )
    stt = round((entry_val + exit_val) * _STT_RATE, 2)
    exchange = round((entry_val + exit_val) * _EXCHANGE_RATE, 2)
    stamp = round(entry_val * _STAMP_RATE, 2)
    gst = round((brokerage + exchange) * _GST_RATE, 2)
    slippage = round((entry_val + exit_val) * _SLIPPAGE_RATE, 2)
    total = round(brokerage + stt + exchange + stamp + gst + slippage, 2)

    return {
        "brokerage": brokerage,
        "stt": stt,
        "exchange": exchange,
        "stamp": stamp,
        "gst": gst,
        "slippage": slippage,
        "total": total,
    }


def calc_pnl(
    entry_price: float,
    exit_price: float,
    qty: int,
) -> tuple[float, float, dict[str, float]]:
    """Returns (gross_pnl_inr, net_pnl_inr, costs_dict) after round-trip costs."""
    gross = (exit_price - entry_price) * qty
    costs = calc_costs(entry_price, exit_price, qty)
    return gross, gross - costs["total"], costs


def calc_qty(position_size_inr: float, price: float) -> int:
    """Shares to buy for a fixed rupee position size. Minimum 1."""
    return max(1, math.floor(position_size_inr / price))
