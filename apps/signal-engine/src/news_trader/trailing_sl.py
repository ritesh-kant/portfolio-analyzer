"""Trailing stop-loss logic for news-trader positions.

Rules (from plan):
  - Initial SL = entry_price * (1 - SL_PCT)
  - SL = highest_price_seen * (1 - SL_PCT)
  - SL only moves UP, never down
  - Target = entry_price * (1 + TARGET_PCT)
  - Day 5 force-close at market open

Round-trip cost model: 45 bps (matches quant/execution/paper.py _ROUND_TRIP_COST_FRACTION).
"""

import math
from datetime import datetime, timezone
from typing import Literal

# Cost model — matches quant/execution/paper.py
_ROUND_TRIP_COST = 0.0045  # 45 bps

ExitReason = Literal["sl_hit", "target_hit", "day5"]


def initial_trailing_sl(entry_price: float, sl_pct: float) -> float:
    return entry_price * (1.0 - sl_pct)


def update_trailing_sl(
    current_price: float,
    highest_price: float,
    current_sl: float,
    sl_pct: float,
) -> tuple[float, float]:
    """Returns (new_highest_price, new_sl). SL never decreases."""
    new_highest = max(highest_price, current_price)
    new_sl = new_highest * (1.0 - sl_pct)
    return new_highest, max(current_sl, new_sl)


def check_exit(
    current_price: float,
    trailing_sl: float,
    target_price: float,
    entry_at: datetime,
    max_hold_days: int,
) -> ExitReason | None:
    """Returns the exit reason if any exit condition is met, else None.

    Priority: sl_hit > target_hit > day5 (matches plan priority order).
    """
    if current_price <= trailing_sl:
        return "sl_hit"
    if current_price >= target_price:
        return "target_hit"
    now = datetime.now(tz=timezone.utc)
    held_days = (now.date() - entry_at.astimezone(timezone.utc).date()).days
    if held_days >= max_hold_days:
        return "day5"
    return None


def calc_pnl(
    entry_price: float,
    exit_price: float,
    qty: int,
) -> tuple[float, float]:
    """Returns (gross_pnl_inr, net_pnl_inr) after round-trip costs."""
    gross = (exit_price - entry_price) * qty
    cost = (entry_price + exit_price) * qty * (_ROUND_TRIP_COST / 2)
    return gross, gross - cost


def calc_qty(position_size_inr: float, price: float) -> int:
    """Shares to buy for a fixed rupee position size. Minimum 1."""
    return max(1, math.floor(position_size_inr / price))
