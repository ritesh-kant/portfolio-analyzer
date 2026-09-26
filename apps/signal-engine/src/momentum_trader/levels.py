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
4. **Volume shelves.** A pivot needs `k` lower bars on each side, so it cannot
   see supply built *inside* a fast move: every bar of a vertical run makes a
   higher high, and the one impulse bar then blinds the detector for `k` bars
   either side - which is exactly where the sellers who stopped the run are.
   A shelf is found from volume-by-price instead: a band where an unusual
   amount of the session's volume changed hands, regardless of bar shape.

Resistance is the nearest level above the current price, support the nearest
below. The exit logic uses resistance to answer "is the move about to stall?"
and support to place a stop somewhere the chart justifies.

Session VWAP is deliberately NOT in here: it moves during the day, so it is a
dynamic level handled directly in exits.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import round_levels_above, validate_bars

PIVOT_K = 3            # bars either side that must be lower/higher
CLUSTER_TOL_PCT = 0.30  # pivots within 0.30% of each other are the same level
MIN_TOUCHES = 1
NEAR_PCT = 0.35        # "price is at the level" band, in percent
# A single intraday pivot is useful context, but is not enough evidence to
# overrule a live trend.  Anchors are structural by definition; a pivot becomes
# structural only after the market has made a second, independently confirmed
# attempt at it.  This is a rule about the source of a level, not its price.
STRUCTURAL_KINDS = frozenset({"prev_day", "orb", "round", "shelf", "session_high"})

# Prior-session resistance for the fixed-target cap (BT50,
# research/hypotheses/2026-09-25-session-resistance-target.md). Both numbers
# were fixed from the GODREJIND 2026-09-25 chart before any replay ran.
SESSION_LEVEL_SESSIONS = 10   # sessions before today whose highs are known
TARGET_BUFFER_PCT = 0.15      # a capped target sits this far under its level
TICK = 0.05                   # NSE equity tick

# Volume-shelf parameters. A shelf is a local peak in the volume-by-price
# profile, so the bucket width sets what "one level" means. The default scales
# with the symbol's own 1-minute noise: a shelf narrower than a typical bar is
# not a price the market argued over, it is one bar's range.
SHELF_BUCKET_MIN = 0.05          # NSE tick; never bucket finer than this
SHELF_BUCKET_FALLBACK_PCT = 0.15  # % of price, used when no ATR is supplied
SHELF_MIN_VOLUME_MULT = 1.5      # peak must hold 1.5x the mean occupied bucket
SHELF_PEAK_WIDTH = 1             # buckets either side the peak must beat


@dataclass(frozen=True)
class Level:
    price: float
    kind: str          # pivot_high | pivot_low | prev_day | orb | round | shelf
                       # | session_high | session_pivot
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


def _shelf_bucket(bars: pd.DataFrame, atr: float | None) -> float:
    """Width of one volume-profile bucket, in rupees.

    `atr` is the symbol's average true range on the same timeframe as `bars`.
    A band narrower than one bar's typical range cannot be a level the market
    argued over, so the bucket is that range. Callers without an ATR to hand
    fall back to a fixed percentage of price.
    """
    px = float(bars["close"].iloc[-1])
    width = atr if atr and atr > 0 else px * SHELF_BUCKET_FALLBACK_PCT / 100.0
    return max(round(width, 2), SHELF_BUCKET_MIN)


def volume_by_price(bars: pd.DataFrame, bucket: float) -> pd.Series:
    """Volume traded in each price bucket, indexed by bucket centre.

    Each bar's volume is spread evenly across its high-low range, which is the
    honest thing to do with OHLCV: we know the bar traded that range and we do
    not know where inside it. The alternative - crediting a bar's whole volume
    to one price - is what makes a single impulse candle look like a level at
    its wick tip, when almost none of its volume changed hands up there.
    """
    validate_bars(bars)
    if bars.empty or bucket <= 0:
        return pd.Series(dtype=float)
    lo = float(bars["low"].min())
    hi = float(bars["high"].max())
    start = np.floor(lo / bucket) * bucket
    # Count the buckets rather than letting arange decide: a zero-range bar has
    # lo == hi, and `arange(start, hi + bucket, bucket)` then yields a single
    # edge and no bucket at all.
    n_buckets = max(int(np.ceil((hi - start) / bucket)), 1)
    edges = start + np.arange(n_buckets + 1) * bucket
    profile = np.zeros(n_buckets)
    lows = bars["low"].to_numpy(dtype=float)
    highs = bars["high"].to_numpy(dtype=float)
    vols = bars["volume"].to_numpy(dtype=float)
    for low_, high_, vol in zip(lows, highs, vols, strict=True):
        if vol <= 0:
            continue
        if high_ <= low_:                      # zero-range bar: one bucket
            j = min(int(np.searchsorted(edges, low_, "right")) - 1, len(profile) - 1)
            profile[max(j, 0)] += vol
            continue
        overlap = np.clip(np.minimum(edges[1:], high_) - np.maximum(edges[:-1], low_),
                          0.0, None)
        total = overlap.sum()
        if total > 0:
            profile += vol * overlap / total
    return pd.Series(profile, index=(edges[:-1] + edges[1:]) / 2.0)


def volume_shelf_levels(bars: pd.DataFrame, atr: float | None = None) -> list[Level]:
    """Prices where an unusual share of the session's volume changed hands.

    A shelf is a local peak in the volume-by-price profile that holds at least
    `SHELF_MIN_VOLUME_MULT` times the mean occupied bucket. Unlike a pivot it
    does not care about bar shape, so it still sees the supply a vertical run
    left behind - the case pivots are blind to by construction.

    Look-ahead safe: the profile is built only from the bars it is given.
    """
    if bars.empty:
        return []
    bucket = _shelf_bucket(bars, atr)
    profile = volume_by_price(bars, bucket)
    if profile.empty:
        return []
    values = profile.to_numpy(dtype=float)
    occupied = values[values > 0]
    if occupied.size == 0:
        return []
    total = float(values.sum())
    threshold = float(occupied.mean()) * SHELF_MIN_VOLUME_MULT
    w = SHELF_PEAK_WIDTH
    out: list[Level] = []
    for i, value in enumerate(values):
        if value < threshold:
            continue
        window = values[max(0, i - w): i + w + 1]
        if value < window.max() - 1e-9:
            continue
        if i > 0 and abs(values[i - 1] - value) < 1e-9:
            continue                           # first bucket of a plateau only
        share = value / total if total > 0 else 0.0
        out.append(Level(price=float(profile.index[i]), kind="shelf",
                         touches=2, volume=float(value),
                         strength=1.0 + min(share * 10.0, 2.0)))
    return sorted(out, key=lambda x: x.strength, reverse=True)


def derive_levels(
    bars: pd.DataFrame,
    prev_day: dict[str, float] | None = None,
    orb: dict[str, float] | None = None,
    add_round: bool = True,
    add_shelves: bool = False,
    atr: float | None = None,
) -> list[Level]:
    """Every level worth knowing about, strongest first.

    `prev_day` takes keys high/low/close; `orb` takes high/low.

    `add_shelves` is off by default so every existing caller and every recorded
    backtest keeps the level set it was measured with. `atr` only sets the
    shelf bucket width and is ignored when shelves are off.
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
    if add_shelves:
        levels += volume_shelf_levels(bars, atr)
    return [x for x in levels if x.touches >= MIN_TOUCHES]


def session_resistance_levels(prior_1m: pd.DataFrame, prior_5m: pd.DataFrame) -> list[Level]:
    """Resistance left behind by earlier sessions, for the target cap only.

    `prior_1m` / `prior_5m` must hold only sessions strictly before today, so
    nothing here can see the current session. Two kinds come out:

    * `session_high` - each prior session's high. An anchor, like `prev_day`:
      it is structural with one touch because the whole market saw it.
    * `session_pivot` - swing pivot highs on the prior sessions' 5-minute bars,
      clustered like today's pivots. A stall in the middle of an older session
      is not any day's high, so the anchor alone misses it. Structural only
      with two or more touches, the same rule today's pivots follow.

    Highs only: these feed where a long takes profit, never an entry or a stop.
    """
    if prior_1m is None or prior_1m.empty:
        return []
    day_high = prior_1m["high"].groupby(prior_1m.index.normalize()).max()
    levels = [Level(float(h), "session_high", 1, 0.0, 1.5) for h in day_high]
    if prior_5m is not None and not prior_5m.empty:
        highs, _ = swing_pivots(prior_5m)
        levels += cluster(highs, "session_pivot")
    return levels


def buffered_target(level_price: float, buffer_pct: float) -> float:
    """The sell price for a target capped at `level_price`.

    Sellers who know a level offer in front of it, so an order resting exactly
    at the level is last in the queue. The target sits `buffer_pct` under it,
    floored to the tick. 0 returns the level untouched, which is how every run
    recorded before the buffer existed reproduces exactly.
    """
    if buffer_pct <= 0:
        return level_price
    raw = level_price * (1.0 - buffer_pct / 100.0)
    return round(math.floor(raw / TICK + 1e-9) * TICK, 2)


def resistance_target(levels: list[Level], fill: float,
                      buffer_pct: float = 0.0) -> tuple[Level, float] | None:
    """Nearest structural resistance that can still serve as a target.

    Returns the level and its buffered target. A level whose buffered target
    is not above the fill has in effect already been reached, so it is skipped
    and the next one up is used. With `buffer_pct=0` this is exactly
    `nearest_structural_resistance(levels, fill)`.
    """
    picks = [(x, buffered_target(x.price, buffer_pct)) for x in levels if is_structural(x)]
    picks = [(x, t) for x, t in picks if t > fill]
    return min(picks, key=lambda p: (p[1], p[0].price)) if picks else None


def nearest_resistance(levels: list[Level], price: float,
                       min_strength: float = 0.0) -> Level | None:
    above = [x for x in levels if x.price > price and x.strength >= min_strength]
    return min(above, key=lambda x: x.price) if above else None


def is_structural(level: Level) -> bool:
    """Whether a level has enough independent evidence to govern a trade.

    Previous-session, opening-range and round-number anchors are known before
    the decision.  A pivot needs at least two confirmed touches; the final
    pivot itself is still look-ahead safe because ``derive_levels`` only emits
    pivots after the required bars on its right have closed.
    """
    return level.kind in STRUCTURAL_KINDS or level.touches >= 2


def nearest_structural_resistance(levels: list[Level], price: float) -> Level | None:
    """Nearest resistance that can block entry or establish a rejection exit."""
    above = [x for x in levels if x.price > price and is_structural(x)]
    return min(above, key=lambda x: x.price) if above else None


def nearest_structural_support(levels: list[Level], price: float) -> Level | None:
    """Nearest support with enough evidence to govern an exit."""
    below = [x for x in levels if x.price < price and is_structural(x)]
    return max(below, key=lambda x: x.price) if below else None


def nearest_support(levels: list[Level], price: float,
                    min_strength: float = 0.0) -> Level | None:
    below = [x for x in levels if x.price < price and x.strength >= min_strength]
    return max(below, key=lambda x: x.price) if below else None
