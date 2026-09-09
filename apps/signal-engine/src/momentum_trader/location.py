"""Where the entry price sits relative to prices other traders care about.

Three measurements, all read at the trigger bar and all RECORDED ONLY — nothing
in the engine gates on them. See
research/hypotheses/2026-09-06-entry-location.md.

  round_head_pct    how far the trigger is below the next round-rupee mark.
                    The operator's observation: "check if the current price is
                    near a whole rupee or half rupee, because i have seen high
                    sell on these levels." Order books really do cluster at
                    round ticks, so a break with a round number just overhead
                    has resting sell interest in the way.
  resist_head_pct   clear air above: distance to the nearest derived resistance.
  support_drop_pct  distance down to the nearest derived support.

`resist_head_pct` and `support_drop_pct` reuse `levels.derive_levels`, so they
are the same support/resistance the exit rules already use. No new definition
of a level is introduced here.
"""

from __future__ import annotations

import math

import pandas as pd

from .levels import Level, derive_levels, nearest_resistance, nearest_support

# The operator specified the grid as "whole rupee or half rupee", so the marks
# are every ₹0.50. This is a stated spec, not a fitted parameter: it is not
# searched over, and the gate derived from it (§2 of the hypothesis) is a
# half-interval split with no threshold of its own.
ROUND_STEP = 0.50


def round_head_pct(price: float, step: float = ROUND_STEP) -> float | None:
    """Percent distance from `price` up to the next ₹`step` mark.

    A price sitting exactly on a mark returns 0.0 — it has the mark directly
    overhead. A price just above one returns nearly the full interval, because
    the next mark is a whole step away.
    """
    if price <= 0 or step <= 0:
        return None
    nxt = math.floor(price / step) * step + step
    return (nxt - price) / price * 100.0


def dist_to_round_pct(price: float, step: float = ROUND_STEP) -> float | None:
    """Percent to the NEAREST ₹`step` mark, in either direction.

    This is the operator's claim as stated — "price near a whole rupee or half
    rupee ... high sell on these levels". Resting orders cluster AT the mark, so
    a price sitting on ₹250.00 is inside that congestion; the directional
    `round_head_pct` scores it 0.2% *below* the next mark and would wave it
    through, which is the wrong reading of the claim.

    Ranges from 0.0 (exactly on a mark) to half an interval (at the midpoint,
    the furthest any price can be from every mark).

    Amended 2026-09-06 BEFORE the run and before any 2026 number was computed;
    see §2 of the hypothesis. `round_head_pct` is kept as a second, directional
    reading and is reported exploratorily.
    """
    if price <= 0 or step <= 0:
        return None
    below = price - math.floor(price / step) * step
    above = step - below
    return min(below, above) / price * 100.0


def head_and_drop(
    levels: list[Level], price: float
) -> tuple[float | None, float | None]:
    """(percent up to nearest resistance, percent down to nearest support).

    None on either side means no derived level in that direction, which is
    itself informative — it is recorded rather than silently treated as "far".
    """
    if price <= 0:
        return None, None
    res = nearest_resistance(levels, price)
    sup = nearest_support(levels, price)
    head = (res.price - price) / price * 100.0 if res is not None else None
    drop = (price - sup.price) / price * 100.0 if sup is not None else None
    return head, drop


def measure(
    bars_tf: pd.DataFrame,
    price: float,
    prev_day: dict[str, float] | None = None,
) -> tuple[float | None, float | None, float | None, float | None]:
    """(dist_to_round_pct, round_head_pct, resist_head_pct, support_drop_pct).

    Reads only `bars_tf`, which the caller has already sliced to closed bars up
    to and including the trigger bar, so this cannot see the future.
    """
    dr, rh = dist_to_round_pct(price), round_head_pct(price)
    if bars_tf.empty:
        return dr, rh, None, None
    levels = derive_levels(bars_tf, prev_day)
    head, drop = head_and_drop(levels, price)
    return dr, rh, head, drop
