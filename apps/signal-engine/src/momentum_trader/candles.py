"""Candlestick pattern TAGS (Warrior "Candlestick Pattern Reference" sheet).

These are observational tags only. They are recorded on every captured trade so
the forward test can report whether any of them add information, but none of
them is an entry gate. Single-bar shapes on their own have no documented edge;
the spec (research/specs/warrior-patterns-nse.md §3) explains why they are
tags, not triggers.

All functions take the bar frame (see indicators.REQUIRED_COLS) and evaluate the
LAST closed bar (and the 1–2 bars before it for multi-bar shapes).
"""

from __future__ import annotations

import pandas as pd

# Geometry thresholds, expressed as fractions of the bar's full range.
_DOJI_BODY = 0.10       # body ≤ 10% of range → doji
_SMALL_BODY = 0.30      # body ≤ 30% of range → "small body" (spinning top / star)
_LONG_WICK = 2.0        # wick ≥ 2× body → "long wick" (hammer family)
_SHORT_WICK = 0.10      # wick ≤ 10% of range → "no wick"
_TWEEZER_TOL = 0.001    # highs/lows within 0.1% → equal


def _parts(bar: pd.Series) -> tuple[float, float, float, float, float]:
    o, h = float(bar["open"]), float(bar["high"])
    lo, c = float(bar["low"]), float(bar["close"])
    rng = max(h - lo, 1e-12)
    return o, h, lo, c, rng


def _body(bar: pd.Series) -> float:
    return abs(float(bar["close"]) - float(bar["open"]))


def _is_green(bar: pd.Series) -> bool:
    return float(bar["close"]) > float(bar["open"])


def _is_red(bar: pd.Series) -> bool:
    return float(bar["close"]) < float(bar["open"])


def _upper_wick(bar: pd.Series) -> float:
    return float(bar["high"]) - max(float(bar["open"]), float(bar["close"]))


def _lower_wick(bar: pd.Series) -> float:
    return min(float(bar["open"]), float(bar["close"])) - float(bar["low"])


# ── single bar ────────────────────────────────────────────────────────────────

def is_doji(bar: pd.Series) -> bool:
    _, _, _, _, rng = _parts(bar)
    return _body(bar) <= _DOJI_BODY * rng


def is_hammer(bar: pd.Series) -> bool:
    """Small body at the top, long lower wick, little upper wick. Bullish after a drop."""
    _, _, _, _, rng = _parts(bar)
    body = _body(bar)
    return (
        body <= _SMALL_BODY * rng
        and _lower_wick(bar) >= _LONG_WICK * max(body, 1e-12)
        and _upper_wick(bar) <= _SHORT_WICK * rng
    )


def is_inverted_hammer(bar: pd.Series) -> bool:
    """Small body at the bottom, long upper wick. Bullish after a drop (mirror of hammer)."""
    _, _, _, _, rng = _parts(bar)
    body = _body(bar)
    return (
        body <= _SMALL_BODY * rng
        and _upper_wick(bar) >= _LONG_WICK * max(body, 1e-12)
        and _lower_wick(bar) <= _SHORT_WICK * rng
    )


def is_shooting_star(bar: pd.Series) -> bool:
    """Same shape as inverted hammer; bearish when it appears after a run-up.
    Context (prior trend) is applied by the caller; the shape test is identical."""
    return is_inverted_hammer(bar)


def is_hanging_man(bar: pd.Series) -> bool:
    """Same shape as hammer; bearish when it appears after a run-up."""
    return is_hammer(bar)


def is_dragonfly_doji(bar: pd.Series) -> bool:
    _, _, _, _, rng = _parts(bar)
    return is_doji(bar) and _lower_wick(bar) >= 0.6 * rng and _upper_wick(bar) <= _SHORT_WICK * rng


def is_gravestone_doji(bar: pd.Series) -> bool:
    _, _, _, _, rng = _parts(bar)
    return is_doji(bar) and _upper_wick(bar) >= 0.6 * rng and _lower_wick(bar) <= _SHORT_WICK * rng


def is_spinning_top(bar: pd.Series) -> bool:
    """Small body, wicks on both sides. Indecision."""
    _, _, _, _, rng = _parts(bar)
    body = _body(bar)
    return (
        _DOJI_BODY * rng < body <= _SMALL_BODY * rng
        and _upper_wick(bar) >= body
        and _lower_wick(bar) >= body
    )


# ── two bar ───────────────────────────────────────────────────────────────────

def is_bullish_engulfing(prev: pd.Series, cur: pd.Series) -> bool:
    """Red bar followed by a green bar whose body covers the red body."""
    return (
        _is_red(prev)
        and _is_green(cur)
        and float(cur["open"]) <= float(prev["close"])
        and float(cur["close"]) >= float(prev["open"])
        and _body(cur) > _body(prev)
    )


def is_bearish_engulfing(prev: pd.Series, cur: pd.Series) -> bool:
    return (
        _is_green(prev)
        and _is_red(cur)
        and float(cur["open"]) >= float(prev["close"])
        and float(cur["close"]) <= float(prev["open"])
        and _body(cur) > _body(prev)
    )


def is_tweezer_bottom(prev: pd.Series, cur: pd.Series) -> bool:
    """Two bars with (near-)equal lows, first red, second green."""
    lo_p, lo_c = float(prev["low"]), float(cur["low"])
    return _is_red(prev) and _is_green(cur) and abs(lo_p - lo_c) <= _TWEEZER_TOL * lo_p


def is_tweezer_top(prev: pd.Series, cur: pd.Series) -> bool:
    hi_p, hi_c = float(prev["high"]), float(cur["high"])
    return _is_green(prev) and _is_red(cur) and abs(hi_p - hi_c) <= _TWEEZER_TOL * hi_p


# ── three bar ─────────────────────────────────────────────────────────────────

def is_morning_star(a: pd.Series, b: pd.Series, c: pd.Series) -> bool:
    """Big red, small-bodied star (gapped or not), big green closing into the red body."""
    _, _, _, _, rng_b = _parts(b)
    return (
        _is_red(a)
        and _body(b) <= _SMALL_BODY * rng_b
        and _is_green(c)
        and float(c["close"]) > (float(a["open"]) + float(a["close"])) / 2.0
    )


def is_evening_star(a: pd.Series, b: pd.Series, c: pd.Series) -> bool:
    _, _, _, _, rng_b = _parts(b)
    return (
        _is_green(a)
        and _body(b) <= _SMALL_BODY * rng_b
        and _is_red(c)
        and float(c["close"]) < (float(a["open"]) + float(a["close"])) / 2.0
    )


def is_three_white_soldiers(a: pd.Series, b: pd.Series, c: pd.Series) -> bool:
    """Three consecutive green bars, each closing higher, each opening inside the prior body."""
    bars = (a, b, c)
    if not all(_is_green(x) for x in bars):
        return False
    closes_up = float(a["close"]) < float(b["close"]) < float(c["close"])
    opens_inside = (
        float(a["open"]) <= float(b["open"]) <= float(a["close"])
        and float(b["open"]) <= float(c["open"]) <= float(b["close"])
    )
    return closes_up and opens_inside


def is_three_black_crows(a: pd.Series, b: pd.Series, c: pd.Series) -> bool:
    bars = (a, b, c)
    if not all(_is_red(x) for x in bars):
        return False
    closes_down = float(a["close"]) > float(b["close"]) > float(c["close"])
    opens_inside = (
        float(a["close"]) <= float(b["open"]) <= float(a["open"])
        and float(b["close"]) <= float(c["open"]) <= float(b["open"])
    )
    return closes_down and opens_inside


# ── tag the latest bar ────────────────────────────────────────────────────────

def candle_tags(bars: pd.DataFrame) -> list[str]:
    """Return every candlestick tag that fires on the last closed bar.

    Order is stable so the list can be joined into a CSV field. Rising/Falling
    Three (5-bar continuation shapes) are deliberately omitted: they are the
    bull-flag / bear-flag setups already covered in setups.py.
    """
    if len(bars) == 0:
        return []
    cur = bars.iloc[-1]
    tags: list[str] = []

    single = (
        ("doji", is_doji), ("dragonfly_doji", is_dragonfly_doji),
        ("gravestone_doji", is_gravestone_doji), ("hammer", is_hammer),
        ("inverted_hammer", is_inverted_hammer), ("spinning_top", is_spinning_top),
    )
    for name, fn in single:
        if fn(cur):
            tags.append(name)

    if len(bars) >= 2:
        prev = bars.iloc[-2]
        two = (
            ("bullish_engulfing", is_bullish_engulfing),
            ("bearish_engulfing", is_bearish_engulfing),
            ("tweezer_bottom", is_tweezer_bottom),
            ("tweezer_top", is_tweezer_top),
        )
        for name, fn2 in two:
            if fn2(prev, cur):
                tags.append(name)

    if len(bars) >= 3:
        a, b = bars.iloc[-3], bars.iloc[-2]
        three = (
            ("morning_star", is_morning_star), ("evening_star", is_evening_star),
            ("three_white_soldiers", is_three_white_soldiers),
            ("three_black_crows", is_three_black_crows),
        )
        for name, fn3 in three:
            if fn3(a, b, cur):
                tags.append(name)
    return tags
