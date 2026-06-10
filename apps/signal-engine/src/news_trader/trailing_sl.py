"""Trailing stop-loss logic for news-trader positions.

Rules (from plan):
  - Initial SL = entry_price * (1 - SL_PCT)
  - SL = highest_price_seen * (1 - SL_PCT)
  - SL only moves UP, never down
  - Target = entry_price * (1 + TARGET_PCT)
  - EOD force-close at 15:15 IST; day-5 backstop

Cost model: Indian equity INTRADAY (MIS) round-trip on NSE/Zerodha.
  All positions are now same-day (EOD force-close + 90-min time-stop), so
  intraday rates apply — NOT delivery rates. Key differences vs delivery:
    STT:   0.025% sell-side only  (delivery was 0.1% each side — 8× more)
    Stamp: 0.003% buy-side        (delivery was 0.015% — 5× more)
  Brokerage ₹20/order (Zerodha flat, same as delivery), NSE exchange ~0.00345%/side,
  GST 18% on (brokerage + exchange), slippage 5 bps/side.
"""

import math
from typing import Literal

# Indian equity intraday (MIS) cost constants — NSE/Zerodha
_BROKERAGE_PER_ORDER = 20.0   # ₹20 flat per order, capped at 0.03% of turnover (Zerodha flat)
_BROKERAGE_CAP_RATE = 0.0003  # 0.03% cap
_STT_RATE = 0.00025            # 0.025% sell-side only (intraday MIS; delivery is 0.1% each side)
_EXCHANGE_RATE = 0.0000297     # NSE exchange transaction charge per side — slab-based since Oct 2024;
                               #   Zerodha passes through at ~0.00297% for equity intraday/delivery
_SEBI_RATE = 0.000001          # SEBI turnover fee: ₹10/crore = 0.0001% per side (revised Jul 2024
                               #   from ₹5/crore); shown separately on Zerodha contract notes
_STAMP_RATE = 0.00003          # 0.003% buy-side only (intraday; delivery is 0.015%)
_GST_RATE = 0.18               # 18% on (brokerage + exchange charges); NOT on STT/stamp/SEBI
_SLIPPAGE_RATE = 0.0005        # 5 bps per side (market impact estimate)

ExitReason = Literal["sl_hit", "target_hit", "time_stop", "eod_close", "day5"]


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


def initial_stop(entry_price: float, initial_sl_pct: float, direction: str = "long") -> float:
    """Wide initial stop placed at entry, before trailing activates.

    Long: below entry. Short: above entry (loss direction is up)."""
    if direction == "short":
        return entry_price * (1.0 + initial_sl_pct)
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


def update_stop_short(
    entry_price: float,
    current_price: float,
    lowest_price: float,
    current_sl: float,
    initial_sl_pct: float,
    trail_sl_pct: float,
    trail_activate_pct: float,
) -> tuple[float, float]:
    """Split stop for SHORT positions — mirror of update_stop.

    Favourable direction is DOWN, so the stop sits ABOVE price and only ever
    moves down. Returns (new_lowest_price, new_sl).

    - Before the low reaches entry*(1 - trail_activate_pct): stop sits at the
      wide ceiling entry*(1 + initial_sl_pct).
    - Once activated: stop trails at low*(1 + trail_sl_pct), locking in profit.
    - SL never increases (min against current_sl).
    """
    new_lowest = min(lowest_price, current_price)
    activated = new_lowest <= entry_price * (1.0 - trail_activate_pct)
    if activated:
        candidate = new_lowest * (1.0 + trail_sl_pct)
    else:
        candidate = entry_price * (1.0 + initial_sl_pct)
    return new_lowest, min(current_sl, candidate)


def check_exit(
    current_price: float,
    trailing_sl: float,
    target_price: float,
    held_sessions: int,
    max_hold_days: int,
    held_minutes: float | None = None,
    max_hold_minutes: int | None = None,
    eod_close: bool = False,
    direction: str = "long",
) -> ExitReason | None:
    """Returns the exit reason if any exit condition is met, else None.

    Priority: sl_hit > target_hit > time_stop > eod_close > day5.

    - sl_hit / target_hit: price-based, take priority over time exits.
      For shorts the comparisons invert: SL is above price, target below.
    - time_stop: fires when held_minutes >= max_hold_minutes (intraday elapsed time).
      Captures the ~97-min avg MFE peak; both must be supplied.
    - eod_close: caller passes True when wall-clock IST >= 15:15 and
      nt_force_close_eod is enabled.
    - day5: calendar-session backstop (rarely fires when EOD-close is on).

    held_sessions must be pre-computed trading days by the caller —
    see sl_monitor._trading_days_held().
    """
    if direction == "short":
        if current_price >= trailing_sl:
            return "sl_hit"
        if current_price <= target_price:
            return "target_hit"
    else:
        if current_price <= trailing_sl:
            return "sl_hit"
        if current_price >= target_price:
            return "target_hit"
    if held_minutes is not None and max_hold_minutes is not None and held_minutes >= max_hold_minutes:
        return "time_stop"
    if eod_close:
        return "eod_close"
    if held_sessions >= max_hold_days:
        return "day5"
    return None


def calc_costs(
    entry_price: float,
    exit_price: float,
    qty: int,
    direction: str = "long",
) -> dict[str, float]:
    """Returns itemised round-trip costs (INR) for Indian equity intraday (MIS).

    STT applies to the SELL leg, stamp duty to the BUY leg. For a long the
    sell leg is the exit; for a short it's the entry (sell first, buy back)."""
    entry_val = entry_price * qty
    exit_val = exit_price * qty
    sell_val = entry_val if direction == "short" else exit_val
    buy_val = exit_val if direction == "short" else entry_val

    brokerage = round(
        min(_BROKERAGE_PER_ORDER, entry_val * _BROKERAGE_CAP_RATE)
        + min(_BROKERAGE_PER_ORDER, exit_val * _BROKERAGE_CAP_RATE),
        2,
    )
    stt = round(sell_val * _STT_RATE, 2)  # intraday: sell-side only
    exchange = round((entry_val + exit_val) * _EXCHANGE_RATE, 2)
    sebi = round((entry_val + exit_val) * _SEBI_RATE, 2)
    stamp = round(buy_val * _STAMP_RATE, 2)
    gst = round((brokerage + exchange) * _GST_RATE, 2)  # GST not levied on STT/stamp/SEBI
    slippage = round((entry_val + exit_val) * _SLIPPAGE_RATE, 2)
    total = round(brokerage + stt + exchange + sebi + stamp + gst + slippage, 2)

    return {
        "brokerage": brokerage,
        "stt": stt,
        "exchange": exchange,
        "sebi": sebi,
        "stamp": stamp,
        "gst": gst,
        "slippage": slippage,
        "total": total,
    }


def calc_pnl(
    entry_price: float,
    exit_price: float,
    qty: int,
    direction: str = "long",
) -> tuple[float, float, dict[str, float]]:
    """Returns (gross_pnl_inr, net_pnl_inr, costs_dict) after round-trip costs.

    Shorts profit when price falls: gross = (entry - exit) * qty."""
    if direction == "short":
        gross = (entry_price - exit_price) * qty
    else:
        gross = (exit_price - entry_price) * qty
    costs = calc_costs(entry_price, exit_price, qty, direction=direction)
    return gross, gross - costs["total"], costs


def calc_qty(position_size_inr: float, price: float) -> int:
    """Shares to buy for a fixed rupee position size. Minimum 1."""
    return max(1, math.floor(position_size_inr / price))
