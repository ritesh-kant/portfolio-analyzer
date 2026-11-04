"""Gamma exposure (GEX) levels from an option chain — the Tanuki / MenthorQ style map.

GEX estimates how much the option *writers* (dealers) must buy or sell the index
to stay hedged when it moves 1%. Per strike:

    GEX = gamma × OI (units) × spot² × 1%            (₹ per 1% move)

and the sign comes from an assumption about who is short what (``Convention``):

* ``dealer_long_calls`` — the US default: customers sell calls / buy puts, so call
  gamma counts +, put gamma −. Positive total = hedging damps moves; negative =
  hedging chases them. The price where the total crosses zero is the gamma flip
  (Tanuki's / MenthorQ's "HVL").
* ``dealer_short_all`` — closer to the Indian reality where retail mostly *buys*
  options and prop/FPI desks write both sides: every strike counts −. There is
  then no flip; only the walls and the magnitude mean anything.

Neither is observed — dealer books are not public. Treat every level as a zone.

Gamma is recomputed here with Black-Scholes from each row's IV rather than taken
from the broker: Upstox rounds gamma to 4 decimals (0.0011), far too coarse, and
the flip sweep needs gamma at hypothetical spots anyway. Sweeping keeps each
strike's IV fixed (sticky strike).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Kind = Literal["CE", "PE"]
Convention = Literal["dealer_long_calls", "dealer_short_all"]

RISK_FREE = 0.065
MIN_T_YEARS = 0.5 / (365 * 24)   # 30 minutes: stops expiry-day ATM gamma going to infinity
TRADING_DAYS = 252


@dataclass(frozen=True)
class OptionRow:
    expiry: datetime      # tz-aware expiry instant (15:30 IST for NSE)
    strike: float
    kind: Kind
    oi: float             # open interest in units of the underlying, not lots
    iv: float             # implied volatility as a decimal (0.1257, not 12.57)


@dataclass
class StrikeGex:
    strike: float
    call: float           # signed ₹ per 1% move
    put: float

    @property
    def net(self) -> float:
        return self.call + self.put


@dataclass
class GexLevels:
    spot: float
    net_gex: float                       # ₹ per 1% move, summed over every row
    flip: float | None                   # gamma flip / HVL; None if no sign change in range
    call_walls: list[float]              # C1, C2, … strongest call-gamma strikes at/above spot
    put_walls: list[float]               # P1, P2, … strongest put-gamma strikes at/below spot
    expected_move: tuple[float, float] | None   # 1-day range from ATM IV (1D Min, 1D Max)
    gamma_wall: float | None = None      # strike with the largest |net| GEX, either side of spot
    # MenthorQ GEX 1…10: the next-largest |net| strikes after Call Resistance and Put Support
    gex_levels: list[float] = field(default_factory=list)
    strikes: list[StrikeGex] = field(default_factory=list)

    @property
    def regime(self) -> str:
        return "positive gamma" if self.net_gex > 0 else "negative gamma"


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def year_fraction(now: datetime, expiry: datetime) -> float:
    return max((expiry - now).total_seconds() / (365.0 * 24 * 3600), MIN_T_YEARS)


def bs_gamma(spot: float, strike: float, t_years: float, iv: float, r: float = RISK_FREE) -> float:
    """Black-Scholes gamma (change in delta per 1-point move). Same for calls and puts."""
    if spot <= 0 or strike <= 0 or t_years <= 0 or iv <= 0:
        return 0.0
    vol_t = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / vol_t
    return _norm_pdf(d1) / (spot * vol_t)


def _sign(kind: Kind, convention: Convention) -> float:
    if convention == "dealer_short_all":
        return -1.0
    return 1.0 if kind == "CE" else -1.0


def row_gex(row: OptionRow, spot: float, now: datetime, convention: Convention) -> float:
    gamma = bs_gamma(spot, row.strike, year_fraction(now, row.expiry), row.iv)
    return _sign(row.kind, convention) * gamma * row.oi * spot * spot * 0.01


def strike_gex(
    rows: list[OptionRow], spot: float, now: datetime, convention: Convention
) -> list[StrikeGex]:
    by_strike: dict[float, StrikeGex] = {}
    for row in rows:
        s = by_strike.setdefault(row.strike, StrikeGex(row.strike, 0.0, 0.0))
        g = row_gex(row, spot, now, convention)
        if row.kind == "CE":
            s.call += g
        else:
            s.put += g
    return [by_strike[k] for k in sorted(by_strike)]


def net_gex(rows: list[OptionRow], spot: float, now: datetime, convention: Convention) -> float:
    return sum(row_gex(r, spot, now, convention) for r in rows)


def gamma_flip(
    rows: list[OptionRow],
    spot: float,
    now: datetime,
    convention: Convention,
    span: float = 0.08,
    steps: int = 161,
) -> float | None:
    """Spot where total GEX changes sign, nearest to the current spot.

    Re-prices every row at hypothetical spots across ±``span`` and linearly
    interpolates between the two grid points that bracket each zero crossing.
    """
    lo, hi = spot * (1 - span), spot * (1 + span)
    grid = [lo + (hi - lo) * i / (steps - 1) for i in range(steps)]
    totals = [net_gex(rows, s, now, convention) for s in grid]
    crossings: list[float] = []
    for i in range(steps - 1):
        s0, s1, g0, g1 = grid[i], grid[i + 1], totals[i], totals[i + 1]
        if g0 == 0:
            crossings.append(s0)
        elif g0 * g1 < 0:
            crossings.append(s0 + (s1 - s0) * (-g0) / (g1 - g0))
    return min(crossings, key=lambda c: abs(c - spot)) if crossings else None


def atm_iv(rows: list[OptionRow], spot: float, now: datetime | None = None) -> float | None:
    """Mean call/put IV at the strike nearest spot, on the nearest expiry at least a day away (else
    the nearest). A same-day option prices the overnight gap and the open into a few hours, so its
    annualised IV runs about double and would inflate a one-day range."""
    if not rows:
        return None
    expiries = [r.expiry for r in rows]
    day_out = [e for e in expiries if now is None or (e - now).total_seconds() >= 86_400]
    nearest = min(day_out or expiries)
    front = [r for r in rows if r.expiry == nearest and r.iv > 0]
    if not front:
        return None
    k = min({r.strike for r in front}, key=lambda s: abs(s - spot))
    ivs = [r.iv for r in front if r.strike == k]
    return sum(ivs) / len(ivs)


def expected_move(spot: float, iv: float | None, days: float = 1.0) -> tuple[float, float] | None:
    if not iv or iv <= 0:
        return None
    move = spot * iv * math.sqrt(days / TRADING_DAYS)
    return spot - move, spot + move


def compute_levels(
    rows: list[OptionRow],
    spot: float,
    now: datetime,
    convention: Convention = "dealer_long_calls",
    n_walls: int = 3,
    n_gex: int = 10,
) -> GexLevels:
    rows = [r for r in rows if r.oi > 0 and r.iv > 0 and r.expiry > now]
    strikes = strike_gex(rows, spot, now, convention)
    # Walls on their own side of price (Tanuki style): resistance above, support below.
    above = [s for s in strikes if s.strike >= spot]
    below = [s for s in strikes if s.strike <= spot]
    calls = sorted(above, key=lambda s: abs(s.call), reverse=True)
    puts = sorted(below, key=lambda s: abs(s.put), reverse=True)
    flip = gamma_flip(rows, spot, now, convention) if convention == "dealer_long_calls" else None
    call_walls = [s.strike for s in calls[:n_walls] if s.call != 0]
    put_walls = [s.strike for s in puts[:n_walls] if s.put != 0]
    by_net = sorted((s for s in strikes if s.net != 0), key=lambda s: abs(s.net), reverse=True)
    resistance_support = set(call_walls[:1] + put_walls[:1])
    return GexLevels(
        spot=spot,
        net_gex=sum(s.net for s in strikes),
        flip=flip,
        call_walls=call_walls,
        put_walls=put_walls,
        expected_move=expected_move(spot, atm_iv(rows, spot, now)),
        gamma_wall=by_net[0].strike if by_net else None,
        gex_levels=[s.strike for s in by_net if s.strike not in resistance_support][:n_gex],
        strikes=strikes,
    )


def group_by_expiry(rows: list[OptionRow]) -> dict[datetime, list[OptionRow]]:
    out: dict[datetime, list[OptionRow]] = defaultdict(list)
    for r in rows:
        out[r.expiry].append(r)
    return dict(sorted(out.items()))
