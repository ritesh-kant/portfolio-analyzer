"""Options pricing / payoff / cost layer for synthetic option backtests.

Sits beside `replay_lib.py`. The equity harness trades the underlying's 5-min
bar directly; this module is the layer between an underlying bar and an OPTION
P&L, so the same signal cohort can be replayed as option trades.

Three things equity has that options don't, all handled here:
  1. instrument != underlying  -> select_contract() + price_option() turn a spot
     bar into a premium series (Black-Scholes), no option feed required.
  2. P&L is non-linear in spot  -> caller defines SL/TP on the PREMIUM, and
     theta bleeds every bar via the shrinking time-to-expiry.
  3. costs differ from equity    -> calc_option_costs() uses the F&O tax table
     and a deliberately WIDE spread/slippage default (single-stock weeklies).

HONESTY NOTE — this is a *synthetic* pricer. With no real option bars, every
premium is model-implied from one IV assumption. A flat-IV run deletes vega
risk and will lie. Always run the vol-crush stress (see iv_crush in the harness)
before believing any edge. This is a feasibility screen, never a hold-out.

Black-Scholes uses math.erf for the normal CDF so there is NO scipy/numpy
dependency beyond what replay_lib already pulls (pandas).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

OptType = Literal["CE", "PE"]

# --- Indian F&O (NSE / Zerodha) cost constants -----------------------------
# VERIFY against a live Zerodha contract note before trusting absolute ₹ — F&O
# STT and exchange charges were both revised in Oct 2024 and move periodically.
_BROKERAGE_PER_ORDER = 20.0     # ₹20 flat per executed order (Zerodha F&O)
_OPT_STT_RATE = 0.001           # 0.10% on SELL-side PREMIUM (raised Oct 2024 from 0.0625%);
                                #   NOTE: exercised/ITM-expiry options are taxed on INTRINSIC at 0.125% —
                                #   not modelled here because we always square off intraday.
_OPT_EXCHANGE_RATE = 0.0003503  # ~0.03503% of premium per side (NSE F&O txn charge, post-Oct-2024 slab)
_OPT_SEBI_RATE = 0.000001       # ₹10/crore = 0.0001% per side
_OPT_STAMP_RATE = 0.00003       # 0.003% on BUY-side premium
_GST_RATE = 0.18                # 18% on (brokerage + exchange + sebi)

# The cost that actually decides option viability. Single-stock weekly spreads
# are 50–200 bps; this default is intentionally pessimistic. Override per run
# and treat 2x of this as the cost-stress, mirroring replay_lib's slippage stress.
_OPT_SLIPPAGE_RATE = 0.01       # 100 bps per side on the PREMIUM (half-spread estimate)

_TRADING_DAYS_YEAR = 252.0
_RISK_FREE = 0.065              # ~India 1Y T-bill / repo neighbourhood; vega/theta are
                                #   first-order insensitive to this at intraday horizons.


def _norm_cdf(x: float) -> float:
    """Standard-normal CDF via erf — avoids a scipy dependency."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def year_fraction(now: datetime, expiry: datetime) -> float:
    """Time to expiry in years. Floored tiny-positive so an expiry-day option
    still prices (intrinsic-dominated) instead of dividing by zero."""
    seconds = (expiry - now).total_seconds()
    yrs = seconds / (365.0 * 24 * 3600)
    return max(yrs, 1e-6)


def price_option(
    spot: float,
    strike: float,
    t_years: float,
    iv: float,
    opt_type: OptType,
    r: float = _RISK_FREE,
) -> float:
    """Black-Scholes premium (no dividend). t_years from year_fraction();
    theta is captured implicitly by feeding a smaller t_years on later bars."""
    if t_years <= 0 or iv <= 0:  # degenerate -> pure intrinsic
        return _intrinsic(spot, strike, opt_type)
    vol_t = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / vol_t
    d2 = d1 - vol_t
    disc = math.exp(-r * t_years)
    if opt_type == "CE":
        return spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
    return strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def _intrinsic(spot: float, strike: float, opt_type: OptType) -> float:
    return max(0.0, spot - strike) if opt_type == "CE" else max(0.0, strike - spot)


@dataclass
class Greeks:
    delta: float
    gamma: float
    theta_per_day: float  # premium decay per CALENDAR day (negative for long)
    vega_per_volpt: float  # premium change per 1 IV percentage-point (i.e. per 0.01)


def greeks(
    spot: float,
    strike: float,
    t_years: float,
    iv: float,
    opt_type: OptType,
    r: float = _RISK_FREE,
) -> Greeks:
    """First-order Greeks. Diagnostic only — used to sanity-check that a trade's
    P&L came from delta (direction) and not an accidental vega/theta artifact."""
    if t_years <= 0 or iv <= 0:
        intr = _intrinsic(spot, strike, opt_type)
        d = 1.0 if (intr > 0 and opt_type == "CE") else (-1.0 if intr > 0 else 0.0)
        return Greeks(delta=d, gamma=0.0, theta_per_day=0.0, vega_per_volpt=0.0)
    vol_t = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / vol_t
    d2 = d1 - vol_t
    pdf = _norm_pdf(d1)
    disc = math.exp(-r * t_years)
    delta = _norm_cdf(d1) if opt_type == "CE" else _norm_cdf(d1) - 1.0
    gamma = pdf / (spot * vol_t)
    # theta (per year) -> per calendar day; sign is negative for long holders
    term1 = -(spot * pdf * iv) / (2.0 * math.sqrt(t_years))
    if opt_type == "CE":
        theta_yr = term1 - r * strike * disc * _norm_cdf(d2)
    else:
        theta_yr = term1 + r * strike * disc * _norm_cdf(-d2)
    vega_per_volpt = spot * pdf * math.sqrt(t_years) * 0.01
    return Greeks(
        delta=delta,
        gamma=gamma,
        theta_per_day=theta_yr / 365.0,
        vega_per_volpt=vega_per_volpt,
    )


def select_contract(
    spot: float,
    opt_type: OptType,
    strike_step: float,
    moneyness: int = 0,
) -> float:
    """Pick a strike from spot. moneyness in strike-steps:
      0  -> ATM (nearest step)
      +1 -> 1 step OTM, -1 -> 1 step ITM (sign is relative to the option type).
    strike_step is the contract's strike interval (per-symbol; e.g. ₹20/₹50/₹100).
    Expiry selection is the caller's job (weekly vs monthly is a registered choice)."""
    atm = round(spot / strike_step) * strike_step
    shift = moneyness * strike_step
    return atm + shift if opt_type == "CE" else atm - shift


def calc_option_costs(
    entry_prem: float,
    exit_prem: float,
    lots: int,
    lot_size: int,
    slippage_rate: float = _OPT_SLIPPAGE_RATE,
) -> dict[str, float]:
    """Round-trip F&O costs (INR) for an intraday option buy->sell (long premium).

    Premium turnover = premium * lots * lot_size. STT is sell-side premium only;
    stamp is buy-side. Slippage applies to BOTH legs on the premium and is the
    dominant term for single-stock weeklies — that is the point."""
    qty = lots * lot_size
    entry_val = entry_prem * qty
    exit_val = exit_prem * qty

    brokerage = round(2 * _BROKERAGE_PER_ORDER, 2)  # flat, both legs
    stt = round(exit_val * _OPT_STT_RATE, 2)         # sell leg = exit for a long option
    exchange = round((entry_val + exit_val) * _OPT_EXCHANGE_RATE, 2)
    sebi = round((entry_val + exit_val) * _OPT_SEBI_RATE, 2)
    stamp = round(entry_val * _OPT_STAMP_RATE, 2)    # buy leg = entry
    gst = round((brokerage + exchange + sebi) * _GST_RATE, 2)
    slippage = round((entry_val + exit_val) * slippage_rate, 2)
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


def calc_option_pnl(
    entry_prem: float,
    exit_prem: float,
    lots: int,
    lot_size: int,
    slippage_rate: float = _OPT_SLIPPAGE_RATE,
) -> tuple[float, float, dict[str, float]]:
    """(gross, net, costs) for a long-premium round trip. Long option: profit
    when premium rises, gross = (exit - entry) * lots * lot_size."""
    qty = lots * lot_size
    gross = (exit_prem - entry_prem) * qty
    costs = calc_option_costs(entry_prem, exit_prem, lots, lot_size, slippage_rate)
    return gross, gross - costs["total"], costs


def lots_for_budget(premium: float, lot_size: int, budget_inr: float) -> int:
    """Whole lots affordable for a premium budget. Minimum 1 lot (the smallest
    tradeable unit) — note this can exceed budget for a high-premium contract,
    which the caller should flag rather than silently size to zero."""
    per_lot = premium * lot_size
    return max(1, math.floor(budget_inr / per_lot)) if per_lot > 0 else 1


# --- contract-spec lookup (lot size + strike step) -------------------------
# Data lives in nfo_specs.py, auto-generated from the Zerodha instruments dump.
# Imported lazily so options_lib stays usable (pricing/greeks) even if the
# spec file hasn't been generated yet.
try:
    from nfo_specs import NFO_SPECS, NOT_FO
except ImportError:  # pragma: no cover - spec file not generated
    NFO_SPECS, NOT_FO = {}, set()


def has_options(symbol: str) -> bool:
    """True if the symbol has tradeable single-stock options. The options
    strategy MUST filter on this — ~23% of the news cohort is cash-only."""
    return symbol in NFO_SPECS


def lot_size(symbol: str) -> int:
    """NFO lot size for a symbol. Raises if the symbol has no options, so a
    missing spec fails loudly instead of silently mispricing a trade."""
    if symbol not in NFO_SPECS:
        raise KeyError(f"{symbol} has no single-stock options (NOT_FO or unlisted)")
    return NFO_SPECS[symbol][0]


def strike_step(symbol: str) -> float:
    """NFO strike interval for a symbol (modal listed-strike gap)."""
    if symbol not in NFO_SPECS:
        raise KeyError(f"{symbol} has no single-stock options (NOT_FO or unlisted)")
    return NFO_SPECS[symbol][1]
