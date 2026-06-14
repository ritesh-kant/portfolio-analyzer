"""Black-Scholes option pricing for the options-trader module.

Self-contained copy (no import from research/backtests) so it works inside
the Lambda Docker image. Keep in sync with research/backtests/options_lib.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

OptType = Literal["CE", "PE"]
_RISK_FREE = 0.065


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def year_fraction(now: datetime, expiry: datetime) -> float:
    seconds = (expiry - now).total_seconds()
    return max(seconds / (365.0 * 24 * 3600), 1e-6)


def price_option(
    spot: float,
    strike: float,
    t_years: float,
    iv: float,
    opt_type: OptType,
    r: float = _RISK_FREE,
) -> float:
    if t_years <= 0 or iv <= 0:
        return max(0.0, spot - strike) if opt_type == "CE" else max(0.0, strike - spot)
    vol_t = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / vol_t
    d2 = d1 - vol_t
    disc = math.exp(-r * t_years)
    if opt_type == "CE":
        return spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
    return strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


@dataclass
class Greeks:
    delta: float
    theta_per_day: float


def greeks(
    spot: float, strike: float, t_years: float, iv: float, opt_type: OptType
) -> Greeks:
    if t_years <= 0 or iv <= 0:
        intr = price_option(spot, strike, 0.0, 0.0, opt_type)
        d = 1.0 if intr > 0 and opt_type == "CE" else (-1.0 if intr > 0 else 0.0)
        return Greeks(delta=d, theta_per_day=0.0)
    vol_t = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (_RISK_FREE + 0.5 * iv * iv) * t_years) / vol_t
    d2 = d1 - vol_t
    pdf = _norm_pdf(d1)
    disc = math.exp(-_RISK_FREE * t_years)
    delta = _norm_cdf(d1) if opt_type == "CE" else _norm_cdf(d1) - 1.0
    term1 = -(spot * pdf * iv) / (2.0 * math.sqrt(t_years))
    if opt_type == "CE":
        theta_yr = term1 - _RISK_FREE * strike * disc * _norm_cdf(d2)
    else:
        theta_yr = term1 + _RISK_FREE * strike * disc * _norm_cdf(-d2)
    return Greeks(delta=delta, theta_per_day=theta_yr / 365.0)


def atm_strike(spot: float, step: float) -> float:
    return round(spot / step) * step
