"""Paper short-straddle logic: entry, repricing, exits, costs.

Strategy: sell ATM CE + ATM PE at signal time (after 15-min delay).
Profit when the underlying barely moves (premium decays via theta).
Loss when the stock makes a large intraday move in either direction.

All pricing is synthetic Black-Scholes (real IV logged separately via nse_chain).
The P&L accurately models theta decay and delta effects; vega is flat-IV
(the limitation of synthetic pricing; real IV from chain logs is the validation).

Cost model: F&O sell-side — STT on sell legs at entry, brokerage flat ₹20/order.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .pricing import atm_strike, greeks, price_option, year_fraction

_IST = timezone(timedelta(hours=5, minutes=30))
_RISK_FREE = 0.065

# F&O cost constants (seller perspective)
_BROKERAGE_PER_ORDER = 20.0      # ₹20 flat per order (Zerodha)
_OPT_STT_RATE = 0.001            # 0.10% on SELL-side premium
_OPT_EXCHANGE_RATE = 0.0003503   # ~0.035% per side on premium
_OPT_SEBI_RATE = 0.000001
_OPT_STAMP_RATE = 0.00003        # buy-side only (the BUY-BACK at close)
_GST_RATE = 0.18
# Fallback slippage rate, used ONLY when no real bid/ask is available for the
# leg. Expressed as a fraction of premium turnover (see calc_straddle_costs).
# This is a pure guess; the real cost is the half-spread actually crossed, which
# is wired in from the chain snapshot when present. Keep in sync with
# Settings.opt_slippage_rate (the config default overrides this constant at the
# call sites that pass it through).
_OPT_SLIPPAGE_RATE = 0.01


def nearest_monthly_expiry(from_date: date | None = None) -> date:
    """Last Tuesday of the nearest month that is ≥ 5 calendar days away.

    NSE changed single-stock monthly option expiry from last Thursday to last
    Tuesday effective late 2024. Verified against Zerodha instruments dump:
    June 2026 = 2026-06-30 (Tue), July = 2026-07-28 (Tue), Aug = 2026-08-25 (Tue).
    """
    ref = from_date or datetime.now(tz=timezone.utc).date()

    def last_tuesday(y: int, m: int) -> date:
        if m == 12:
            last = date(y + 1, 1, 1) - timedelta(days=1)
        else:
            last = date(y, m + 1, 1) - timedelta(days=1)
        offset = (last.weekday() - 1) % 7  # Tuesday = weekday 1
        return last - timedelta(days=offset)

    exp = last_tuesday(ref.year, ref.month)
    if (exp - ref).days < 5:
        m, y = (ref.month % 12) + 1, ref.year + (ref.month // 12)
        exp = last_tuesday(y, m)
    return exp


def expiry_datetime(exp_date: date) -> datetime:
    """NSE options expire at 15:30 IST on the expiry date."""
    return datetime(exp_date.year, exp_date.month, exp_date.day, 15, 30, tzinfo=_IST)


def price_straddle(
    spot: float,
    strike: float,
    t_years: float,
    iv: float,
) -> tuple[float, float, float]:
    """Returns (ce_prem, pe_prem, total_prem) at the given time."""
    ce = price_option(spot, strike, t_years, iv, "CE")
    pe = price_option(spot, strike, t_years, iv, "PE")
    return round(ce, 2), round(pe, 2), round(ce + pe, 2)


def calc_straddle_costs(
    entry_total_prem: float,
    exit_total_prem: float,
    lots: int,
    lot_size: int,
    *,
    half_spread_points: float | None = None,
    fallback_slippage_rate: float = _OPT_SLIPPAGE_RATE,
) -> dict[str, Any]:
    """Round-trip costs for a short-straddle seller (2 legs × 2 contracts = 4 orders).

    Slippage model (the dominant, previously-unrealistic term):

    * If ``half_spread_points`` is given, it is the *summed* half bid-ask spread
      of both legs in premium points — i.e. ``(ce_ask-ce_bid)/2 + (pe_ask-pe_bid)/2``
      observed from the real NSE chain. A seller crosses that spread twice over
      a round trip (sell at bid on entry, buy at ask on exit), so
      ``slippage = 2 * half_spread_points * qty``. This is the auditable, real
      cost — no premium-relative guessing.
    * If it is ``None`` (no live quote — the current production reality, since the
      NSE chain fetch fails from Lambda), fall back to a configurable fraction of
      premium turnover: ``fallback_slippage_rate * (entry_val + exit_val)``.

    The chosen path is reported back via ``slippage_source`` so every closed
    position is auditable.
    """
    qty = lots * lot_size
    # Entry = SELL CE + SELL PE (two sell-side legs → STT on both)
    entry_val = entry_total_prem * qty
    # Exit = BUY BACK CE + BUY BACK PE (buy-side → stamp on both)
    exit_val = exit_total_prem * qty

    brokerage = round(4 * _BROKERAGE_PER_ORDER, 2)          # 4 orders
    stt = round(entry_val * _OPT_STT_RATE, 2)                # sell-side at entry only
    exchange = round((entry_val + exit_val) * _OPT_EXCHANGE_RATE, 2)
    sebi = round((entry_val + exit_val) * _OPT_SEBI_RATE, 2)
    stamp = round(exit_val * _OPT_STAMP_RATE, 2)             # buy-side at exit only
    gst = round((brokerage + exchange + sebi) * _GST_RATE, 2)

    if half_spread_points is not None and half_spread_points >= 0:
        slippage = round(2.0 * half_spread_points * qty, 2)
        slippage_source = "nse_halfspread"
    else:
        slippage = round((entry_val + exit_val) * fallback_slippage_rate, 2)
        slippage_source = "fallback_rate"

    total = round(brokerage + stt + exchange + sebi + stamp + gst + slippage, 2)
    return {
        "brokerage": brokerage,
        "stt": stt,
        "exchange": exchange,
        "sebi": sebi,
        "stamp": stamp,
        "gst": gst,
        "slippage": slippage,
        "slippage_source": slippage_source,
        "total": total,
    }


def build_entry_doc(
    signal_doc: dict[str, Any],
    symbol: str,
    spot: float,
    iv: float,
    lots: int,
    lot_size: int,
    now: datetime,
    exp_date: date,
    target_pct: float,
    stop_pct: float,
    max_hold_minutes: int,
    force_close_eod: bool,
    iv_source: str = "baseline",
) -> dict[str, Any]:
    """Build the opt_paper_positions document for a new short-straddle entry.

    ``iv`` is the volatility used to price entry premiums; ``iv_source`` records
    where it came from ("nse" when a live ATM IV was available, else "baseline").
    """
    step = _strike_step_for(symbol)
    K = atm_strike(spot, step)
    exp_dt = expiry_datetime(exp_date)
    t = year_fraction(now, exp_dt)
    ce_p, pe_p, total_p = price_straddle(spot, K, t, iv)
    exposure = round(total_p * lots * lot_size, 0)
    g = greeks(spot, K, t, iv, "CE")
    return {
        "signal_id": str(signal_doc["_id"]),
        "symbol": symbol,
        "status": "open",
        "strategy": "straddle_sell",
        "signal_type": signal_doc.get("signal", "unknown"),
        "strike": K,
        "expiry": exp_date.isoformat(),
        "lot_size": lot_size,
        "lots": lots,
        "entry_at": now,
        "entry_spot": spot,
        "entry_ce_prem": ce_p,
        "entry_pe_prem": pe_p,
        "entry_total_prem": total_p,
        "entry_iv": iv,
        "entry_iv_source": iv_source,
        "entry_exposure_inr": exposure,
        "entry_delta_ce": round(g.delta, 3),
        "entry_theta_day": round(g.theta_per_day * 2, 2),  # straddle: 2 legs
        # Live state (updated by monitor)
        "current_spot": spot,
        "current_ce_prem": ce_p,
        "current_pe_prem": pe_p,
        "current_total_prem": total_p,
        "current_pnl": 0.0,
        "last_updated_at": now,
        # Config snapshot
        "target_pct": target_pct,
        "stop_pct": stop_pct,
        "max_hold_minutes_used": max_hold_minutes,
        "force_close_eod_used": force_close_eod,
    }


def _strike_step_for(symbol: str) -> float:
    from .nfo_specs import strike_step
    return strike_step(symbol)


def check_exit(
    pos: dict[str, Any],
    current_total_prem: float,
    now: datetime,
) -> str | None:
    """Return exit reason string or None if still open."""
    entry_prem = pos["entry_total_prem"]
    target_pct = pos.get("target_pct", 0.40)
    stop_pct = pos.get("stop_pct", 2.00)
    max_hold = pos.get("max_hold_minutes_used", 90)
    force_eod = pos.get("force_close_eod_used", True)

    # Target: collected enough premium decay
    if current_total_prem <= entry_prem * (1.0 - target_pct):
        return "target_hit"

    # Stop: premium expanded (we're losing — underlying moved too much)
    if current_total_prem >= entry_prem * (1.0 + stop_pct):
        return "stop_hit"

    # Time-stop. A short straddle harvests theta over the session, not in 90 min,
    # so a value <= 0 disables it entirely and lets the position run to EOD /
    # target / stop. (Day-1 evidence: a 90-min stop force-closed 7/7 trades
    # before any meaningful theta accrued — see options_paper_trading_log.)
    if max_hold > 0:
        entry_at = pos["entry_at"]
        if not entry_at.tzinfo:
            entry_at = entry_at.replace(tzinfo=timezone.utc)
        held = (now - entry_at).total_seconds() / 60
        if held >= max_hold:
            return "time_stop"

    # EOD
    if force_eod:
        now_ist = now.astimezone(_IST)
        if now_ist.hour > 15 or (now_ist.hour == 15 and now_ist.minute >= 15):
            return "eod_close"

    return None


def compute_close_update(
    pos: dict[str, Any],
    exit_spot: float,
    exit_ce_prem: float,
    exit_pe_prem: float,
    exit_reason: str,
    now: datetime,
    *,
    half_spread_points: float | None = None,
    fallback_slippage_rate: float = _OPT_SLIPPAGE_RATE,
) -> dict[str, Any]:
    """Build the $set dict for closing a position.

    ``half_spread_points`` / ``fallback_slippage_rate`` are passed straight to
    :func:`calc_straddle_costs` so slippage reflects the real crossed spread
    when a live quote was captured, and a configurable fallback otherwise.
    """
    exit_total = round(exit_ce_prem + exit_pe_prem, 2)
    lots = pos["lots"]
    ls = pos["lot_size"]
    qty = lots * ls
    # Seller P&L: received premium at entry, pay to close at exit
    gross = round((pos["entry_total_prem"] - exit_total) * qty, 2)
    costs = calc_straddle_costs(
        pos["entry_total_prem"],
        exit_total,
        lots,
        ls,
        half_spread_points=half_spread_points,
        fallback_slippage_rate=fallback_slippage_rate,
    )
    net = round(gross - costs["total"], 2)
    return {
        "status": "closed",
        "exit_at": now,
        "exit_spot": exit_spot,
        "exit_ce_prem": round(exit_ce_prem, 2),
        "exit_pe_prem": round(exit_pe_prem, 2),
        "exit_total_prem": exit_total,
        "exit_reason": exit_reason,
        "gross_pnl": gross,
        "net_pnl": net,
        "costs": costs,
        "current_spot": exit_spot,
        "current_ce_prem": round(exit_ce_prem, 2),
        "current_pe_prem": round(exit_pe_prem, 2),
        "current_total_prem": exit_total,
        "current_pnl": net,
        "last_updated_at": now,
    }
