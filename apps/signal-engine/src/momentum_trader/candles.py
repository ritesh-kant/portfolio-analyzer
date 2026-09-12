"""Contextual recognition of all 21 patterns in the Warrior reference sheet.

The public frame APIs share ONE recognition path. A directional reversal name
requires a preceding trend; raw ``is_*`` helpers describe geometry only and
must never authorize attention. Inputs are closed, clock-aligned bars.

Rule provenance (v3, 12 Sep 2026). Each rule below was checked against the
executable TA-Lib C source (ta_CDL*.c), Bulkowski's identification guidelines
(thepatternsite.com), TradingView's indicator help pages, StockCharts
ChartSchool, and Zerodha Varsity. Where they disagree the choice is stated in
research/specs/candlestick-recognition-v2.md ("v3 revision"); nothing here is
a fitted parameter.

    term                 rule                                   source
    -------------------  -------------------------------------  -----------------
    long body            B > mean body(10)  and  B/R >= .55     TA-Lib BodyLong +
                                                                own-range policy
    short body           B < mean body(10)                      TA-Lib BodyShort
    doji                 B <= .10*R  and  B <= .10*mean R(10)   TA-Lib BodyDoji
    long shadow          shadow >= 2*B                          Bulkowski, Zerodha,
                                                                StockCharts ("2x")
    very short shadow    shadow <= .10*R (own candle)           text sources; TA-Lib
                                                                uses .10*mean R
    near / far           .20 / .60 * mean R(5)                  TA-Lib Near, Far
    equal (tweezers)     |diff| <= .05 * mean R(5)              TA-Lib Equal
    star penetration     third close beyond first-body midpoint TradingView,
                                                                Bulkowski (TA-Lib .3)
    prior trend          5 closes: >=3 of 4 steps one way and    own policy (TA-Lib
                         net move > .5 * mean R(5)               none; TV SMA50)
    strength             R(last candle) / mean R(10)             own field, added
                         DESCRIPTIVE — gates nothing              13 Sep 2026

    pattern              shape (on top of the terms above)      position / context
    -------------------  -------------------------------------  -----------------
    hammer               short body, lower >= 2B, upper <= .1R  body <= prior low
                                                                + near; down trend
    hanging_man          same shape                             body >= prior high
                                                                - near; up trend
    inverted_hammer      short body, upper >= 2B, lower <= .1R  body top <= prior
                                                                body bottom + near;
                                                                down trend
    shooting_star        same shape                             body bottom >= prior
                                                                body top - near;
                                                                up trend
    dragonfly_doji       doji, lower >= .6R, upper <= .1R       down trend, else
                                                                plain doji
    gravestone_doji      doji, upper >= .6R, lower <= .1R       up trend, else doji
    doji                 doji                                   any; neutral
    *_spinning_top       short non-doji body, both shadows > B  any; NEUTRAL
    bullish_engulfing    short red then long green; green body  down trend
                         contains red body, strictly larger;
                         equal endpoints allowed, wicks ignored
    bearish_engulfing    mirror                                 up trend
    tweezer_bottom       long red then green, lows equal        down trend
    tweezer_top          long green then red, highs equal       up trend
    morning_star         long red; short body gapping below     down trend
                         first close; long green opening above
                         star body, closing above first-body
                         midpoint
    morning_doji_star    same with a doji star (takes priority) down trend
    evening_star         mirror                                 up trend
    evening_doji_star    mirror                                 up trend
    three_white_soldiers three long green, rising closes, each   down trend
                         open > prior open and <= prior close
                         + near, upper <= .1R, each body >=
                         prior body - far (not an advance block)
    three_black_crows    mirror                                 up trend
    rising_three         long green; three short red whose      up trend
                         BODIES stay inside candle 1's high-low
                         with falling closes; long green opening
                         above last close and closing above
                         candle 1 close
    falling_three        mirror                                 down trend

No probability of profit is implied by any name.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import isfinite

import numpy as np
import pandas as pd

PATTERN_RULES_VERSION = "candles-v3-20260912"
BODY_LOOKBACK = 10
TREND_LOOKBACK = 5
NEAR_LOOKBACK = 5
DOJI_FRACTION = 0.10
SMALL_BODY_FRACTION = 0.30
LONG_BODY_FRACTION = 0.55
LONG_SHADOW_MULTIPLE = 2.0
SHORT_SHADOW_FRACTION = 0.10
DOJI_LONG_SHADOW_FRACTION = 0.60
EQUAL_RANGE_FRACTION = 0.05
NEAR_RANGE_FRACTION = 0.20
FAR_RANGE_FRACTION = 0.60
# Display-only significance threshold. A formation whose signal candle spans
# less than this multiple of the ten-candle average range is *correctly named*
# but too small to act on. NOTHING is rejected because of it — the published
# definitions (TA-Lib included) carry no size floor, and inventing one here
# would silently change what the detector labels. See BT29 §3.2: 74.7% of the
# formations found at real trade entries are below 1.0x, and 12.9% span less
# than one 0.21% round trip, so the annotation needs this number to stay honest.
STRENGTH_WEAK_BELOW = 0.75
STAR_PENETRATION = 0.50
TREND_MIN_RANGE_MOVE = 0.50
Significance vs correctness (added 13 Sep 2026, no rule changed). Every
published definition of a doji, hammer or spinning top is a *ratio* test on the
candle's own range, so a candle spanning 0.11% of price satisfies it exactly as
well as one spanning 1.1%. The names stay correct; what the ratio cannot say is
whether the candle was big enough to mean anything. ``PatternMatch.strength``
reports that separately, as the signal candle's range over the ten-candle
average range, and ``STRENGTH_WEAK_BELOW`` is the suggested display threshold.
Detection is untouched: ``PATTERN_RULES_VERSION`` is deliberately NOT bumped
because re-running any earlier census reproduces the identical set of matches.

TIMEFRAMES = {"1m": pd.Timedelta(minutes=1), "5m": pd.Timedelta(minutes=5)}

# The two spinning-top names preserve the reference sheet's colour labels.
# Their direction remains NEUTRAL: candle colour is not a directional forecast.
PATTERN_NAMES = frozenset(
    {
        "hammer",
        "inverted_hammer",
        "dragonfly_doji",
        "bullish_spinning_top",
        "bullish_engulfing",
        "tweezer_bottom",
        "morning_doji_star",
        "three_white_soldiers",
        "morning_star",
        "rising_three",
        "doji",
        "hanging_man",
        "shooting_star",
        "gravestone_doji",
        "bearish_spinning_top",
        "bearish_engulfing",
        "tweezer_top",
        "evening_doji_star",
        "three_black_crows",
        "evening_star",
        "falling_three",
    }
)


# --------------------------------------------------------------------------
# Candle anatomy
# --------------------------------------------------------------------------


def _valid(*bars: pd.Series) -> bool:
    for bar in bars:
        try:
            o, h, lo, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
        except (KeyError, ValueError, TypeError):
            return False
        if not all(isfinite(v) and v > 0 for v in (o, h, lo, c)):
            return False
        if not (lo <= min(o, c) <= max(o, c) <= h and h > lo):
            return False
    return True


def _body(bar: pd.Series) -> float:
    return abs(float(bar["close"]) - float(bar["open"]))


def _range(bar: pd.Series) -> float:
    return float(bar["high"]) - float(bar["low"])


def _body_ratio(bar: pd.Series) -> float:
    return _body(bar) / _range(bar) if _valid(bar) else 0.0


def _green(bar: pd.Series) -> bool:
    return float(bar["close"]) > float(bar["open"])


def _red(bar: pd.Series) -> bool:
    return float(bar["close"]) < float(bar["open"])


def _upper(bar: pd.Series) -> float:
    return float(bar["high"]) - max(float(bar["open"]), float(bar["close"]))


def _lower(bar: pd.Series) -> float:
    return min(float(bar["open"]), float(bar["close"])) - float(bar["low"])


def _body_top(bar: pd.Series) -> float:
    return max(float(bar["open"]), float(bar["close"]))


def _body_bottom(bar: pd.Series) -> float:
    return min(float(bar["open"]), float(bar["close"]))


def _very_short(shadow: float, bar: pd.Series) -> bool:
    """'Little or no shadow' measured against the candle's own range.

    TA-Lib measures ShadowVeryShort against a 10-candle mean range instead; the
    textual sources (Nison via StockCharts, Bulkowski, Zerodha) describe the
    shadow relative to the candle itself, which is what the eye judges.
    """
    return shadow <= SHORT_SHADOW_FRACTION * _range(bar)


# --------------------------------------------------------------------------
# Single-candle geometry (no trend, no position - the frame API adds those)
# --------------------------------------------------------------------------


def is_doji(bar: pd.Series) -> bool:
    """Open and close 'virtually equal': body within 10% of the range."""
    return _valid(bar) and _body(bar) <= DOJI_FRACTION * _range(bar)


def is_hammer(bar: pd.Series) -> bool:
    """Paper umbrella: small body at the top, lower shadow >= 2 bodies, little
    or no upper shadow. Either colour (Bulkowski, StockCharts, Zerodha)."""
    return (
        _valid(bar)
        and _body(bar) > 0
        and _body_ratio(bar) <= SMALL_BODY_FRACTION
        and _lower(bar) >= LONG_SHADOW_MULTIPLE * _body(bar)
        and _very_short(_upper(bar), bar)
    )


def is_inverted_hammer(bar: pd.Series) -> bool:
    """Inverted umbrella: small body at the bottom, upper shadow >= 2 bodies,
    little or no lower shadow. Either colour."""
    return (
        _valid(bar)
        and _body(bar) > 0
        and _body_ratio(bar) <= SMALL_BODY_FRACTION
        and _upper(bar) >= LONG_SHADOW_MULTIPLE * _body(bar)
        and _very_short(_lower(bar), bar)
    )


def is_hanging_man(bar: pd.Series) -> bool:
    """Same geometry as a hammer; context (up trend, prior high) decides."""
    return is_hammer(bar)


def is_shooting_star(bar: pd.Series) -> bool:
    """Same geometry as an inverted hammer; context decides."""
    return is_inverted_hammer(bar)


def is_dragonfly_doji(bar: pd.Series) -> bool:
    """Doji with open/close at the high: long lower shadow, no upper shadow."""
    return (
        is_doji(bar)
        and _lower(bar) >= DOJI_LONG_SHADOW_FRACTION * _range(bar)
        and _very_short(_upper(bar), bar)
    )


def is_gravestone_doji(bar: pd.Series) -> bool:
    """Doji with open/close at the low: long upper shadow, no lower shadow."""
    return (
        is_doji(bar)
        and _upper(bar) >= DOJI_LONG_SHADOW_FRACTION * _range(bar)
        and _very_short(_lower(bar), bar)
    )


def is_spinning_top(bar: pd.Series) -> bool:
    """Small non-doji body with BOTH shadows longer than the body (TA-Lib,
    Bulkowski, TradingView). Both shadows > body already implies B/R < 1/3, so
    no separate body-ratio cap is applied."""
    return (
        _valid(bar)
        and _body_ratio(bar) > DOJI_FRACTION
        and _upper(bar) > _body(bar)
        and _lower(bar) > _body(bar)
    )


# Position of a single reversal candle relative to the candle before it.
# TA-Lib tests these explicitly; the text sources describe them ("at the low
# of the move", "gaps up from the prior body"). ``near`` is TA-Lib's Near
# tolerance, .20 x mean range over the last five candles.


def hammer_at_prior_low(prev: pd.Series, bar: pd.Series, near: float) -> bool:
    """TA-Lib CDLHAMMER: body below or near the previous candle's low."""
    return _body_bottom(bar) <= float(prev["low"]) + near


def hanging_man_at_prior_high(prev: pd.Series, bar: pd.Series, near: float) -> bool:
    """TA-Lib CDLHANGINGMAN: body above or near the previous candle's high."""
    return _body_bottom(bar) >= float(prev["high"]) - near


def inverted_hammer_below_prior_body(prev: pd.Series, bar: pd.Series, near: float) -> bool:
    """TA-Lib CDLINVERTEDHAMMER requires a real-body gap DOWN. Intraday bars
    rarely gap, so the gap is relaxed by the Near tolerance: the body must sit
    at or below the previous body, not inside it."""
    return _body_top(bar) <= _body_bottom(prev) + near


def shooting_star_above_prior_body(prev: pd.Series, bar: pd.Series, near: float) -> bool:
    """TA-Lib CDLSHOOTINGSTAR requires a real-body gap UP; same relaxation."""
    return _body_bottom(bar) >= _body_top(prev) - near


# --------------------------------------------------------------------------
# Two-candle geometry
# --------------------------------------------------------------------------


def is_bullish_engulfing(prev: pd.Series, cur: pd.Series) -> bool:
    """Green body contains the prior red body and is strictly larger. Shared
    endpoints are allowed (TA-Lib's 80-strength case); wicks are ignored
    (TradingView, Bulkowski: 'ignore the shadows')."""
    return (
        _valid(prev, cur)
        and _red(prev)
        and _green(cur)
        and float(cur["open"]) <= float(prev["close"])
        and float(cur["close"]) >= float(prev["open"])
        and _body(cur) > _body(prev)
    )


def is_bearish_engulfing(prev: pd.Series, cur: pd.Series) -> bool:
    return (
        _valid(prev, cur)
        and _green(prev)
        and _red(cur)
        and float(cur["open"]) >= float(prev["close"])
        and float(cur["close"]) <= float(prev["open"])
        and _body(cur) > _body(prev)
    )


def is_tweezer_bottom(prev: pd.Series, cur: pd.Series, tolerance: float = 0.0) -> bool:
    """Red then green with matching lows. Bulkowski accepts any colours; the
    TradingView definition (red then green) is used because it is the one that
    carries a directional reading."""
    return (
        _valid(prev, cur)
        and _red(prev)
        and _green(cur)
        and abs(float(prev["low"]) - float(cur["low"])) <= tolerance
    )


def is_tweezer_top(prev: pd.Series, cur: pd.Series, tolerance: float = 0.0) -> bool:
    return (
        _valid(prev, cur)
        and _green(prev)
        and _red(cur)
        and abs(float(prev["high"]) - float(cur["high"])) <= tolerance
    )


# --------------------------------------------------------------------------
# Three-candle geometry
# --------------------------------------------------------------------------


def is_morning_star(a: pd.Series, b: pd.Series, c: pd.Series) -> bool:
    """Classical body-gap star. Gap tests are on real bodies, never on the
    high-low range (TA-Lib, Bulkowski 'ignore the shadows'). Penetration is
    the first-body midpoint (TradingView, Bulkowski); TA-Lib defaults to 30%."""
    return (
        _valid(a, b, c)
        and _red(a)
        and _green(c)
        and _body_ratio(a) >= LONG_BODY_FRACTION
        and _body_ratio(c) >= LONG_BODY_FRACTION
        and _body_ratio(b) <= SMALL_BODY_FRACTION
        and _body(b) < min(_body(a), _body(c))
        and _body_top(b) < float(a["close"])
        and float(c["open"]) > _body_top(b)
        and float(c["close"]) > float(a["close"]) + STAR_PENETRATION * _body(a)
    )


def is_morning_doji_star(a: pd.Series, b: pd.Series, c: pd.Series) -> bool:
    return is_doji(b) and is_morning_star(a, b, c)


def is_evening_star(a: pd.Series, b: pd.Series, c: pd.Series) -> bool:
    return (
        _valid(a, b, c)
        and _green(a)
        and _red(c)
        and _body_ratio(a) >= LONG_BODY_FRACTION
        and _body_ratio(c) >= LONG_BODY_FRACTION
        and _body_ratio(b) <= SMALL_BODY_FRACTION
        and _body(b) < min(_body(a), _body(c))
        and _body_bottom(b) > float(a["close"])
        and float(c["open"]) < _body_bottom(b)
        and float(c["close"]) < float(a["close"]) - STAR_PENETRATION * _body(a)
    )


def is_evening_doji_star(a: pd.Series, b: pd.Series, c: pd.Series) -> bool:
    return is_doji(b) and is_evening_star(a, b, c)


def is_three_white_soldiers(
    a: pd.Series, b: pd.Series, c: pd.Series, near: float = 0.0, far: float = 0.0
) -> bool:
    """TA-Lib CDL3WHITESOLDIERS: three green candles, strictly higher closes,
    each open above the prior open and no higher than the prior close + near,
    very short upper shadows, and each body not far shorter than the one
    before (which would be an Advance Block, a different pattern)."""
    return (
        _valid(a, b, c)
        and all(
            _green(x) and _body_ratio(x) >= LONG_BODY_FRACTION and _very_short(_upper(x), x)
            for x in (a, b, c)
        )
        and float(a["close"]) < float(b["close"]) < float(c["close"])
        and float(a["open"]) < float(b["open"]) <= float(a["close"]) + near
        and float(b["open"]) < float(c["open"]) <= float(b["close"]) + near
        and _body(b) >= _body(a) - far
        and _body(c) >= _body(b) - far
    )


def is_three_black_crows(
    a: pd.Series, b: pd.Series, c: pd.Series, near: float = 0.0, far: float = 0.0
) -> bool:
    """Mirror of the soldiers. TA-Lib additionally wants a white candle before
    the first crow; here the separately measured up trend plays that role."""
    return (
        _valid(a, b, c)
        and all(
            _red(x) and _body_ratio(x) >= LONG_BODY_FRACTION and _very_short(_lower(x), x)
            for x in (a, b, c)
        )
        and float(a["close"]) > float(b["close"]) > float(c["close"])
        and float(a["close"]) - near <= float(b["open"]) < float(a["open"])
        and float(b["close"]) - near <= float(c["open"]) < float(b["open"])
        and _body(b) >= _body(a) - far
        and _body(c) >= _body(b) - far
    )


# --------------------------------------------------------------------------
# Five-candle geometry
# --------------------------------------------------------------------------


def _body_inside_range(x: pd.Series, first: pd.Series) -> bool:
    """Three Methods containment is on the small candles' REAL BODIES within
    the first candle's high-low range (TA-Lib, Bulkowski, TradingView). Wicks
    may poke outside."""
    return float(first["low"]) <= _body_bottom(x) and _body_top(x) <= float(first["high"])


def is_rising_three(a: pd.Series, b: pd.Series, c: pd.Series, d: pd.Series, e: pd.Series) -> bool:
    return (
        _valid(a, b, c, d, e)
        and _green(a)
        and _green(e)
        and min(_body_ratio(a), _body_ratio(e)) >= LONG_BODY_FRACTION
        and all(
            _red(x) and _body(x) < min(_body(a), _body(e)) and _body_inside_range(x, a)
            for x in (b, c, d)
        )
        and float(b["close"]) > float(c["close"]) > float(d["close"])
        and float(e["open"]) > float(d["close"])
        and float(e["close"]) > float(a["close"])
    )


def is_falling_three(a: pd.Series, b: pd.Series, c: pd.Series, d: pd.Series, e: pd.Series) -> bool:
    return (
        _valid(a, b, c, d, e)
        and _red(a)
        and _red(e)
        and min(_body_ratio(a), _body_ratio(e)) >= LONG_BODY_FRACTION
        and all(
            _green(x) and _body(x) < min(_body(a), _body(e)) and _body_inside_range(x, a)
            for x in (b, c, d)
        )
        and float(b["close"]) < float(c["close"]) < float(d["close"])
        and float(e["open"]) < float(d["close"])
        and float(e["close"]) < float(a["close"])
    )


# --------------------------------------------------------------------------
# Frame API: context + evidence
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PatternMatch:
    name: str
    timeframe: str
    start: str
    end: str
    confirmation: float
    invalidation: float
    direction: str = "bullish"
    kind: str = "reversal"
    prior_trend: str = "unknown"
    # Range of the formation's LAST (confirming) candle divided by the mean
    # range of the ten candles before the formation. 1.0 = an ordinary candle
    # for this stock at this time of day; 0.3 = a candle a third the usual size
    # that happens to satisfy the same ratio test. Descriptive only.
    strength: float = 0.0
    formed_at: str = ""
    # "formed" is deliberately different from a later trade confirmation.
    status: str = "formed"
    rules_version: str = PATTERN_RULES_VERSION
    evidence: dict[str, object] = field(default_factory=dict)

    def document(self) -> dict[str, object]:
        return asdict(self)


def _trend(prior: pd.DataFrame) -> tuple[str, dict[str, object]]:
    """Explicit local-trend policy, measured strictly BEFORE the formation."""
    window = prior.iloc[-TREND_LOOKBACK:]
    closes = window["close"].to_numpy(dtype=float)
    delta = np.diff(closes)
    net = float(closes[-1] - closes[0])
    avg_range = float((window["high"] - window["low"]).mean())
    direction = "sideways"
    if net > TREND_MIN_RANGE_MOVE * avg_range and int((delta > 0).sum()) >= 3:
        direction = "up"
    elif net < -TREND_MIN_RANGE_MOVE * avg_range and int((delta < 0).sum()) >= 3:
        direction = "down"
    return direction, {
        "trend_start": window.index[0].isoformat(),
        "trend_end": window.index[-1].isoformat(),
        "trend_closes": closes.tolist(),
        "trend_net_move": net,
        "trend_mean_range": avg_range,
    }


def completed_pattern_matches(
    bars: pd.DataFrame,
    timeframe: str,
    *,
    closed_through: pd.Timestamp | None = None,
) -> list[PatternMatch]:
    """Recognize formations ending at the latest fully CLOSED candle.

    Requires ten consecutive valid prior candles for each formation's adaptive
    scale. Never bridges a missing minute, session boundary, duplicate, or an
    invalid OHLC/zero-volume bar. ``closed_through`` can additionally bound a
    caller's full frame; without it the caller promises the frame is closed.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unsupported candlestick timeframe: {timeframe}")
    if bars.empty:
        return []
    if not isinstance(bars.index, pd.DatetimeIndex):
        raise ValueError("candlestick bars require a DatetimeIndex")
    if not bars.index.is_monotonic_increasing or not bars.index.is_unique:
        return []
    if not {"open", "high", "low", "close", "volume"}.issubset(bars.columns):
        return []
    duration = TIMEFRAMES[timeframe]
    if closed_through is not None:
        bars = bars[bars.index + duration <= pd.Timestamp(closed_through)]
    if bars.empty:
        return []
    # At most ten baseline candles plus the longest five-candle pattern.
    bars = bars.iloc[-(BODY_LOOKBACK + 5) :]
    matches: list[PatternMatch] = []
    for size in (5, 3, 2, 1):
        if len(bars) < BODY_LOOKBACK + size:
            continue
        window = bars.iloc[-(BODY_LOOKBACK + size) :]
        if ((window.index[1:] - window.index[:-1]) != duration).any():
            continue
        if (window.index != window.index.floor(f"{int(duration.total_seconds() / 60)}min")).any():
            continue
        if len(set(window.index.date)) != 1:
            continue
        try:
            window = window[["open", "high", "low", "close", "volume"]].astype(float)
        except (TypeError, ValueError, OverflowError):
            continue
        values = window.to_numpy()
        o, h, lo, c, v = values.T
        if (
            not np.isfinite(values).all()
            or (values <= 0).any()
            or (h <= lo).any()
            or (lo > np.minimum(o, c)).any()
            or (h < np.maximum(o, c)).any()
        ):
            continue
        prior, formation = window.iloc[:BODY_LOOKBACK], window.iloc[BODY_LOOKBACK:]
        trend, trend_evidence = _trend(prior)
        mean_body = float((prior["close"] - prior["open"]).abs().mean())
        mean_range = float((prior["high"] - prior["low"]).mean())
        recent_range = float((prior["high"] - prior["low"]).iloc[-NEAR_LOOKBACK:].mean())
        equal_tolerance = EQUAL_RANGE_FRACTION * recent_range
        near = NEAR_RANGE_FRACTION * recent_range
        far = FAR_RANGE_FRACTION * recent_range
        prev_bar = prior.iloc[-1]
        rows = [formation.iloc[i] for i in range(size)]

        def long(x: pd.Series, baseline: float = mean_body) -> bool:
            return _body(x) > baseline and _body_ratio(x) >= LONG_BODY_FRACTION

        def short(x: pd.Series, baseline: float = mean_body) -> bool:
            return _body(x) < baseline

        def doji(x: pd.Series, baseline: float = mean_range) -> bool:
            return is_doji(x) and _body(x) <= DOJI_FRACTION * baseline

        detected: list[tuple[str, str, str]] = []
        if size == 5:
            a, b, c, d, e = rows
            scale_ok = long(a) and long(e) and all(short(x) for x in (b, c, d))
            if scale_ok and trend == "up" and is_rising_three(a, b, c, d, e):
                detected.append(("rising_three", "bullish", "continuation"))
            if scale_ok and trend == "down" and is_falling_three(a, b, c, d, e):
                detected.append(("falling_three", "bearish", "continuation"))
        elif size == 3:
            a, b, c = rows
            scale_ok = long(a) and short(b) and long(c)
            if scale_ok and trend == "down" and is_morning_star(a, b, c):
                name = "morning_doji_star" if doji(b) else "morning_star"
                detected.append((name, "bullish", "reversal"))
            if scale_ok and trend == "up" and is_evening_star(a, b, c):
                name = "evening_doji_star" if doji(b) else "evening_star"
                detected.append((name, "bearish", "reversal"))
            if all(long(x) for x in rows):
                if trend == "down" and is_three_white_soldiers(a, b, c, near, far):
                    detected.append(("three_white_soldiers", "bullish", "reversal"))
                if trend == "up" and is_three_black_crows(a, b, c, near, far):
                    detected.append(("three_black_crows", "bearish", "reversal"))
        elif size == 2:
            a, b = rows
            if short(a) and long(b):
                if trend == "down" and is_bullish_engulfing(a, b):
                    detected.append(("bullish_engulfing", "bullish", "reversal"))
                if trend == "up" and is_bearish_engulfing(a, b):
                    detected.append(("bearish_engulfing", "bearish", "reversal"))
            if long(a):
                if trend == "down" and is_tweezer_bottom(a, b, equal_tolerance):
                    detected.append(("tweezer_bottom", "bullish", "reversal"))
                if trend == "up" and is_tweezer_top(a, b, equal_tolerance):
                    detected.append(("tweezer_top", "bearish", "reversal"))
        else:
            bar = rows[0]
            if doji(bar):
                if is_dragonfly_doji(bar) and trend == "down":
                    detected.append(("dragonfly_doji", "bullish", "reversal"))
                elif is_gravestone_doji(bar) and trend == "up":
                    detected.append(("gravestone_doji", "bearish", "reversal"))
                else:
                    detected.append(("doji", "neutral", "indecision"))
            elif short(bar):
                if is_hammer(bar):
                    if trend == "down" and hammer_at_prior_low(prev_bar, bar, near):
                        detected.append(("hammer", "bullish", "reversal"))
                    elif trend == "up" and hanging_man_at_prior_high(prev_bar, bar, near):
                        detected.append(("hanging_man", "bearish", "reversal"))
                elif is_inverted_hammer(bar):
                    if trend == "down" and inverted_hammer_below_prior_body(prev_bar, bar, near):
                        detected.append(("inverted_hammer", "bullish", "reversal"))
                    elif trend == "up" and shooting_star_above_prior_body(prev_bar, bar, near):
                        detected.append(("shooting_star", "bearish", "reversal"))
                elif is_spinning_top(bar):
                    detected.append(
                        (
                            "bullish_spinning_top" if _green(bar) else "bearish_spinning_top",
                            "neutral",
                            "indecision",
                        )
                    )

        evidence = {
            **trend_evidence,
            "mean_prior_body": mean_body,
            "mean_prior_range": mean_range,
            "signal_candle_range": float(
                formation["high"].iloc[-1] - formation["low"].iloc[-1]
            ),
            "equal_tolerance": equal_tolerance,
            "near_tolerance": near,
            "far_tolerance": far,
            "baseline_start": prior.index[0].isoformat(),
            "baseline_end": prior.index[-1].isoformat(),
            "body_lookback": BODY_LOOKBACK,
            "trend_lookback": TREND_LOOKBACK,
            "gap_policy": "strict_body_gaps",
            "shadow_policy": "own_range",
            "candles": [
                {
                    "time": at.isoformat(),
                    **{k: float(row[k]) for k in ("open", "high", "low", "close", "volume")},
                }
                for at, row in formation.iterrows()
            ],
        }
        high, low = float(formation["high"].max()), float(formation["low"].min())
        # The last candle is the confirming candle of every formation here, so
        # one definition of "how big is this?" works across all 21 patterns.
        signal_range = float(formation["high"].iloc[-1] - formation["low"].iloc[-1])
        strength = (signal_range / mean_range) if mean_range > 0 else 0.0
        for name, direction, kind in detected:
            matches.append(
                PatternMatch(
                    name=name,
                    timeframe=timeframe,
                    start=formation.index[0].isoformat(),
                    end=formation.index[-1].isoformat(),
                    formed_at=(formation.index[-1] + duration).isoformat(),
                    confirmation=low if direction == "bearish" else high,
                    invalidation=high if direction == "bearish" else low,
                    direction=direction,
                    kind=kind,
                    prior_trend=trend,
                    strength=round(strength, 3),
                    evidence=evidence,
                )
            )
    return matches


def candle_tags(bars: pd.DataFrame, timeframe: str | None = None) -> list[str]:
    """Names from the same contextual detector used by persistence and the UI."""
    if bars.empty:
        return []
    if timeframe is None:
        # Compatibility for callers already supplying regular closed frames.
        # Never infer a timeframe from one candle or from arbitrary row grouping.
        if not isinstance(bars.index, pd.DatetimeIndex) or len(bars) < 2:
            return []
        step = bars.index[-1] - bars.index[-2]
        timeframe = next((name for name, duration in TIMEFRAMES.items() if duration == step), None)
        if timeframe is None:
            return []
    return [match.name for match in completed_pattern_matches(bars, timeframe)]
