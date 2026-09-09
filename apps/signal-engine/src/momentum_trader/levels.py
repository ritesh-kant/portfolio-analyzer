"""Support and resistance, derived from the bars rather than drawn by hand.

A level is a price the market has already argued over. We find those prices
mechanically:

1. **Swing pivots.** A bar is a pivot high if its high is the highest in a
   window of `k` bars on each side. It means buyers pushed to that price and
   failed. Pivot lows are the mirror: sellers pushed down and failed.
2. **Clustering.** Pivots rarely repeat to the paisa, so pivots within a
   tolerance of each other are merged into one level. The more times a price
   was tested, and the more volume traded there, the stronger the level.
3. **Anchors.** Prices that matter for reasons other than pivots get added:
   yesterday's high, low and close, today's opening-range high and low, and the
   round-rupee grid. These are where other traders place orders.

Resistance is the nearest level above the current price, support the nearest
below. The exit logic uses resistance to answer "is the move about to stall?"
and support to place a stop somewhere the chart justifies.

Session VWAP is deliberately NOT in here: it moves during the day, so it is a
dynamic level handled directly in exits.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .indicators import round_levels_above, validate_bars

PIVOT_K = 3            # bars either side that must be lower/higher
CLUSTER_TOL_PCT = 0.30  # pivots within 0.30% of each other are the same level
MIN_TOUCHES = 1
NEAR_PCT = 0.35        # "price is at the level" band, in percent


@dataclass(frozen=True)
class Level:
    price: float
    kind: str          # pivot_high | pivot_low | prev_day | orb | round
    touches: int
    volume: float
    strength: float    # touches, plus a small bonus for volume traded there

    def is_near(self, price: float, tol_pct: float = NEAR_PCT) -> bool:
        return abs(price - self.price) / max(self.price, 1e-9) * 100.0 <= tol_pct


def swing_pivot_positions(bars: pd.DataFrame, k: int = PIVOT_K) -> tuple[list[int], list[int]]:
    """(pivot_high_positions, pivot_low_positions) as integer row offsets.

    The single definition of what a pivot is. `swing_pivots` returns the same
    bars as (price, volume) pairs; callers that need timestamps use this.

    A pivot is only confirmed once `k` bars have printed after it, so this never
    looks ahead: the caller passes bars up to now, and the last `k` bars can
    never produce a pivot.
    """
    validate_bars(bars)
    highs: list[int] = []
    lows: list[int] = []
    if len(bars) < 2 * k + 1:
        return highs, lows
    h, low_ = bars["high"].to_numpy(), bars["low"].to_numpy()
    for i in range(k, len(bars) - k):
        window_h = h[i - k: i + k + 1]
        window_l = low_[i - k: i + k + 1]
        if h[i] == window_h.max() and (window_h.argmax() == k):
            highs.append(i)
        if low_[i] == window_l.min() and (window_l.argmin() == k):
            lows.append(i)
    return highs, lows


def swing_pivots(bars: pd.DataFrame, k: int = PIVOT_K) -> tuple[list[tuple[float, float]],
                                                               list[tuple[float, float]]]:
    """(pivot_highs, pivot_lows) as (price, volume) pairs."""
    hi, lo = swing_pivot_positions(bars, k)
    h, low_, v = bars["high"].to_numpy(), bars["low"].to_numpy(), bars["volume"].to_numpy()
    return ([(float(h[i]), float(v[i])) for i in hi],
            [(float(low_[i]), float(v[i])) for i in lo])


def cluster(points: list[tuple[float, float]], kind: str,
            tol_pct: float = CLUSTER_TOL_PCT) -> list[Level]:
    """Merge nearby prices into single levels, strongest first."""
    if not points:
        return []
    pts = sorted(points, key=lambda x: x[0])
    groups: list[list[tuple[float, float]]] = [[pts[0]]]
    for price, vol in pts[1:]:
        anchor = groups[-1][0][0]
        if abs(price - anchor) / max(anchor, 1e-9) * 100.0 <= tol_pct:
            groups[-1].append((price, vol))
        else:
            groups.append([(price, vol)])
    out: list[Level] = []
    for g in groups:
        vol_sum = sum(v for _, v in g)
        # volume-weighted centre: the price where most of the arguing happened
        wsum = sum(p * v for p, v in g)
        price = wsum / vol_sum if vol_sum > 0 else sum(p for p, _ in g) / len(g)
        out.append(Level(price=price, kind=kind, touches=len(g), volume=vol_sum,
                         strength=float(len(g)) + min(vol_sum / 1e6, 1.0)))
    return sorted(out, key=lambda x: x.strength, reverse=True)


def derive_levels(
    bars: pd.DataFrame,
    prev_day: dict[str, float] | None = None,
    orb: dict[str, float] | None = None,
    add_round: bool = True,
) -> list[Level]:
    """Every level worth knowing about, strongest first.

    `prev_day` takes keys high/low/close; `orb` takes high/low.
    """
    if bars.empty:
        return []
    highs, lows = swing_pivots(bars)
    levels = cluster(highs, "pivot_high") + cluster(lows, "pivot_low")
    if prev_day:
        for key in ("high", "low", "close"):
            if prev_day.get(key):
                levels.append(Level(float(prev_day[key]), "prev_day", 1, 0.0, 1.5))
    if orb:
        for key in ("high", "low"):
            if orb.get(key):
                levels.append(Level(float(orb[key]), "orb", 1, 0.0, 1.5))
    if add_round:
        px = float(bars["close"].iloc[-1])
        minor, major = round_levels_above(px)
        levels.append(Level(minor, "round", 1, 0.0, 0.8))
        levels.append(Level(major, "round", 1, 0.0, 1.0))
    return [x for x in levels if x.touches >= MIN_TOUCHES]


def nearest_resistance(levels: list[Level], price: float,
                       min_strength: float = 0.0) -> Level | None:
    above = [x for x in levels if x.price > price and x.strength >= min_strength]
    return min(above, key=lambda x: x.price) if above else None


def nearest_support(levels: list[Level], price: float,
                    min_strength: float = 0.0) -> Level | None:
    below = [x for x in levels if x.price < price and x.strength >= min_strength]
    return max(below, key=lambda x: x.price) if below else None
