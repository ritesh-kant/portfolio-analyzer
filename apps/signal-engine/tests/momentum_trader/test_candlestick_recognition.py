"""Source-derived fixtures plus adversarial cases for the 21-pattern contract.

These test structural validity, not investment performance. Each directional
fixture is also tested against a reversed preceding trend. SAREGAMA is a real
negative fixture, never used to tune numerical thresholds.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest
from src.momentum_trader import candles as c
from src.momentum_trader import engine as eng
from src.momentum_trader.ledger import PaperLedger

Row = tuple[float, float, float, float, float]
COLS = ["open", "high", "low", "close", "volume"]


def mirror(rows: list[Row]) -> list[Row]:
    return [(200 - o, 200 - lo, 200 - h, 200 - close, v) for o, h, lo, close, v in rows]


def frame(rows: list[Row], trend: str = "down", timeframe: str = "5m") -> pd.DataFrame:
    anchor = rows[0][0]
    baseline = []
    for i in range(10):
        close = anchor + (10 - i) * 0.8 * (1 if trend == "down" else -1)
        if trend == "sideways":
            close = anchor + (i % 2) * 0.1
        open_ = close + (0.3 if trend == "down" else -0.3)
        baseline.append((open_, max(open_, close) + 0.2, min(open_, close) - 0.2, close, 1000))
    return pd.DataFrame(
        baseline + rows,
        columns=COLS,
        index=pd.date_range(
            "2026-09-11 09:15",
            periods=10 + len(rows),
            freq="1min" if timeframe == "1m" else "5min",
            tz="Asia/Kolkata",
        ),
    )


STAR = [(100, 100.2, 96.8, 97, 1000), (96.5, 97, 96, 96.65, 700), (96.8, 99.7, 96.7, 99.5, 1200)]
DOJI_STAR = [STAR[0], (96.5, 97, 96, 96.5, 700), STAR[2]]
SOLDIERS = [
    (100, 101.3, 99.9, 101.2, 1000),
    (100.6, 102.3, 100.5, 102.2, 1000),
    (101.6, 103.3, 101.5, 103.2, 1000),
]
METHODS = [
    (100, 104.2, 99.8, 104, 1500),
    (103.9, 104, 103.5, 103.7, 700),
    (103.6, 103.8, 103.2, 103.4, 600),
    (103.4, 103.5, 102.9, 103.2, 650),
    (103.3, 104.7, 103.2, 104.6, 1700),
]
ENGULF = [(100, 100.2, 99.6, 99.8, 800), (99.7, 101.5, 99.5, 101.4, 1500)]
TWEEZER = [(100, 100.1, 97.9, 98, 1000), (98, 100, 97.9, 99.8, 1200)]
HAMMER = [(100, 100.25, 98.9, 100.2, 1000)]
INVERTED = [(100, 101.3, 99.95, 100.2, 1000)]
DRAGONFLY = [(100, 100.02, 98.9, 100, 1000)]
SPIN = [(100, 100.7, 99.5, 100.2, 1000)]
CASES = [
    ("hammer", HAMMER, "down", "bullish"),
    ("inverted_hammer", INVERTED, "down", "bullish"),
    ("dragonfly_doji", DRAGONFLY, "down", "bullish"),
    ("bullish_spinning_top", SPIN, "sideways", "neutral"),
    ("bullish_engulfing", ENGULF, "down", "bullish"),
    ("tweezer_bottom", TWEEZER, "down", "bullish"),
    ("morning_doji_star", DOJI_STAR, "down", "bullish"),
    ("three_white_soldiers", SOLDIERS, "down", "bullish"),
    ("morning_star", STAR, "down", "bullish"),
    ("rising_three", METHODS, "up", "bullish"),
    ("doji", [(100, 101, 99, 100, 1000)], "sideways", "neutral"),
    ("hanging_man", HAMMER, "up", "bearish"),
    ("shooting_star", INVERTED, "up", "bearish"),
    ("gravestone_doji", mirror(DRAGONFLY), "up", "bearish"),
    ("bearish_spinning_top", mirror(SPIN), "sideways", "neutral"),
    ("bearish_engulfing", mirror(ENGULF), "up", "bearish"),
    ("tweezer_top", mirror(TWEEZER), "up", "bearish"),
    ("evening_doji_star", mirror(DOJI_STAR), "up", "bearish"),
    ("three_black_crows", mirror(SOLDIERS), "up", "bearish"),
    ("evening_star", mirror(STAR), "up", "bearish"),
    ("falling_three", mirror(METHODS), "down", "bearish"),
]


def names(bars: pd.DataFrame, tf: str = "5m") -> set[str]:
    return {m.name for m in c.completed_pattern_matches(bars, tf)}


def test_every_pdf_pattern_has_a_positive_fixture() -> None:
    assert {name for name, *_ in CASES} == c.PATTERN_NAMES
    assert len(CASES) == 21


@pytest.mark.parametrize("timeframe", ["1m", "5m"])
@pytest.mark.parametrize("name,rows,trend,direction", CASES, ids=[x[0] for x in CASES])
def test_recognition_evidence_and_tags_agree(name, rows, trend, direction, timeframe) -> None:
    bars = frame(rows, trend, timeframe)
    matches = c.completed_pattern_matches(bars, timeframe)
    match = next((m for m in matches if m.name == name), None)
    assert match is not None, [m.name for m in matches]
    assert match.direction == direction
    assert match.prior_trend == trend
    assert match.start == bars.index[-len(rows)].isoformat()
    assert match.end == bars.index[-1].isoformat()
    assert pd.Timestamp(match.formed_at) == bars.index[-1] + c.TIMEFRAMES[timeframe]
    assert len(match.evidence["candles"]) == len(rows)
    assert pd.Timestamp(match.evidence["trend_end"]) < pd.Timestamp(match.start)
    assert pd.Timestamp(match.evidence["baseline_end"]) < pd.Timestamp(match.start)
    assert match.status == "formed"
    assert match.rules_version == c.PATTERN_RULES_VERSION
    assert set(c.candle_tags(bars, timeframe)) == {m.name for m in matches}
    json.dumps(match.document(), allow_nan=False)


@pytest.mark.parametrize("name,rows,trend,direction", [x for x in CASES if x[3] != "neutral"])
def test_wrong_or_absent_trend_cannot_produce_a_directional_name(name, rows, trend, direction):
    for wrong in ("up" if trend == "down" else "down", "sideways"):
        assert name not in names(frame(rows, wrong))


@pytest.mark.parametrize("name,rows,trend,direction", CASES)
def test_insufficient_history_never_guesses(name, rows, trend, direction):
    assert name not in names(frame(rows, trend).iloc[1:])


@pytest.mark.parametrize("name,rows,trend,direction", CASES)
def test_affine_price_scale_does_not_change_patterns(name, rows, trend, direction):
    bars = frame(rows, trend)
    original = names(bars)
    for factor, shift in ((10, 0), (1, 500), (0.1, 0)):
        other = bars.copy()
        other[["open", "high", "low", "close"]] = (
            other[["open", "high", "low", "close"]] * factor + shift
        )
        assert names(other) == original


@pytest.mark.parametrize(
    "mutation", ["nan", "inf", "invalid_ohlc", "zero_range", "zero_volume", "negative"]
)
def test_corrupt_formation_never_gets_a_label(mutation):
    bars = frame(STAR)
    if mutation == "nan":
        bars.iloc[-1, 3] = float("nan")
    elif mutation == "inf":
        bars.iloc[-1, 1] = float("inf")
    elif mutation == "invalid_ohlc":
        bars.iloc[-1, 1] = 1
    elif mutation == "zero_range":
        bars.iloc[-1, :4] = 100
    elif mutation == "zero_volume":
        bars.iloc[-1, 4] = 0
    else:
        bars.iloc[-1, 2] = -1
    assert names(bars) == set()


def test_missing_duplicate_unsorted_cross_session_and_misaligned_bars_reject():
    bars = frame(STAR)
    assert "morning_star" not in names(bars.drop(bars.index[-2]))
    assert names(pd.concat([bars, bars.iloc[-1:]])) == set()
    assert names(bars.iloc[::-1]) == set()
    late = bars.copy()
    late.index = late.index[:-1].append(pd.DatetimeIndex([late.index[-1] + pd.Timedelta(days=1)]))
    assert names(late) == set()
    late.index = bars.index + pd.Timedelta(minutes=1)
    assert names(late) == set()


def test_prefix_invariance_and_last_bar_must_close():
    bars = frame(STAR)
    formed = bars.index[-1] + pd.Timedelta(minutes=5)
    assert "morning_star" not in {
        m.name
        for m in c.completed_pattern_matches(
            bars, "5m", closed_through=formed - pd.Timedelta(milliseconds=1)
        )
    }
    snapshot = [m.document() for m in c.completed_pattern_matches(bars, "5m")]
    future = bars.iloc[-1:].copy()
    future.index += pd.Timedelta(minutes=5)
    future[["open", "high", "low", "close"]] *= 10
    full = pd.concat([bars, future])
    assert [
        m.document() for m in c.completed_pattern_matches(full, "5m", closed_through=formed)
    ] == snapshot


def test_stars_require_body_gap_and_midpoint_penetration():
    for bullish in (True, False):
        rows = list(STAR)
        # Small middle candle sits inside the first body: not a star position.
        rows[1] = (97.1, 97.5, 96.5, 97.25, 700)
        if not bullish:
            rows = mirror(rows)
        name = "morning_star" if bullish else "evening_star"
        assert name not in names(frame(rows, "down" if bullish else "up"))
    rows = list(STAR)
    rows[-1] = (96.8, 98.6, 96.7, 98.5, 1200)  # exactly the midpoint
    assert "morning_star" not in names(frame(rows))
    rows[-1] = (96.6, 99.7, 96.5, 99.5, 1200)  # third open has no gap
    assert "morning_star" not in names(frame(rows))


def test_large_own_body_ratio_is_not_enough_for_a_long_candle():
    bars = frame(STAR)
    # Entire candidate is tiny relative to the preceding candles.
    bars.iloc[-3:, :4] = 100 + (bars.iloc[-3:, :4] - 100) * 0.01
    assert "morning_star" not in names(bars)


def test_engulfing_is_about_bodies_and_not_wicks():
    bars = frame(ENGULF)
    assert "bullish_engulfing" in names(bars)
    bars.iloc[-2, 1:3] = [110, 90]  # wicks need not be engulfed
    assert "bullish_engulfing" in names(bars)
    bars.iloc[-1, 0] = 99.9  # wick still reaches lower, body no longer engulfs
    assert "bullish_engulfing" not in names(bars)


def test_tweezer_tolerance_scales_with_range_not_price():
    bars = frame(TWEEZER)
    bars.iloc[-1, 2] += 0.03  # 5% of recent .7 range = .035
    assert "tweezer_bottom" in names(bars)
    bars.iloc[-1, 2] -= 0.10
    assert "tweezer_bottom" not in names(bars)


def test_three_methods_containment_is_on_bodies_not_wicks():
    # TA-Lib, Bulkowski and TradingView all keep the small candles' REAL BODIES
    # inside candle 1's high-low; a wick poking out does not break the pattern.
    bars = frame(METHODS, "up")
    bars.iloc[-3, 2] = 99  # middle-candle wick below the first candle low
    assert "rising_three" in names(bars)
    bars = frame(METHODS, "up")
    bars.iloc[-3, 0] = 104.6  # middle-candle body opens above the first high
    bars.iloc[-3, 1] = 104.7
    assert "rising_three" not in names(bars)
    bars = frame(METHODS, "up")
    bars.iloc[-1, 3] = 104  # no continuation beyond first close
    assert "rising_three" not in names(bars)


def test_three_soldiers_rejects_large_upper_wicks():
    bars = frame(SOLDIERS)
    bars.iloc[-2, 1] = 106
    assert "three_white_soldiers" not in names(bars)


def test_three_soldiers_open_may_sit_slightly_above_prior_close_but_not_gap():
    # TA-Lib: open <= prior close + Near (.20 x mean range of the last five).
    # Second soldier closes at 102.2; keep the third body long enough that the
    # Far (advance-block) test is not what decides the outcome.
    bars = frame(SOLDIERS)
    near = 0.2 * 0.7
    bars.iloc[-1, [0, 1, 3]] = [102.2 + near * 0.5, 103.6, 103.5]
    assert "three_white_soldiers" in names(bars)
    bars.iloc[-1, [0, 1, 3]] = [102.2 + near * 1.5, 103.8, 103.7]
    assert "three_white_soldiers" not in names(bars)


def test_three_soldiers_reject_advance_block_shrinking_bodies():
    # A third soldier far shorter than the second is an Advance Block, not
    # Three White Soldiers (TA-Lib Far test).
    bars = frame(SOLDIERS)
    bars.iloc[-1, [0, 1, 3]] = [102.3, 102.9, 102.8]  # body .5 vs prior 1.6, far=.42
    assert "three_white_soldiers" not in names(bars)


def test_hammer_family_requires_position_against_the_prior_candle():
    # frame() anchors the baseline to the formation, so shift ONLY the
    # formation candle after building the frame.
    def shifted(rows: list[Row], trend: str, by: float) -> pd.DataFrame:
        bars = frame(rows, trend)
        bars.iloc[-1, :4] += by
        return bars

    # Hammer body must reach the prior candle's low (TA-Lib CDLHAMMER).
    assert "hammer" in names(frame(HAMMER, "down"))
    assert "hammer" not in names(shifted(HAMMER, "down", +1.5))
    # Hanging man body must reach the prior candle's high (CDLHANGINGMAN).
    assert "hanging_man" in names(frame(HAMMER, "up"))
    assert "hanging_man" not in names(shifted(HAMMER, "up", -1.5))
    # Inverted hammer body at/below the prior body; shooting star at/above it.
    assert "inverted_hammer" in names(frame(INVERTED, "down"))
    assert "inverted_hammer" not in names(shifted(INVERTED, "down", +1.5))
    assert "shooting_star" in names(frame(INVERTED, "up"))
    assert "shooting_star" not in names(shifted(INVERTED, "up", -1.5))


def test_spinning_top_needs_both_shadows_longer_than_body_and_no_body_cap():
    # Body/range of .32 (above v2's .30 cap) with both shadows > body is still
    # a spinning top: body .28, shadows .30 each, range .88.
    bars = frame([(100, 100.58, 99.70, 100.28, 1000)], "sideways")
    assert "bullish_spinning_top" in names(bars)
    # One shadow shorter than the body disqualifies it.
    bars = frame([(100, 100.25, 99.4, 100.2, 1000)], "sideways")
    assert not names(bars) & {"bullish_spinning_top", "bearish_spinning_top"}


def matches(bars: pd.DataFrame, tf: str = "5m") -> dict[str, c.PatternMatch]:
    return {m.name: m for m in c.completed_pattern_matches(bars, tf)}


def test_strength_is_the_signal_candle_against_the_ten_candle_baseline():
    # frame()'s baseline candles each span 0.8 + 0.4 = 1.2, so a formation
    # candle spanning 1.2 scores exactly 1.0 and one spanning 0.3 scores 0.25.
    bars = frame([(100, 100.58, 99.70, 100.28, 1000)], "sideways")
    got = matches(bars)["bullish_spinning_top"]
    mean_range = got.evidence["mean_prior_range"]
    assert got.evidence["signal_candle_range"] == pytest.approx(0.88)
    assert got.strength == pytest.approx(0.88 / mean_range, rel=1e-3)


def test_strength_scales_with_the_candle_and_not_with_the_price_level():
    # Same shape, same baseline: a candle a quarter the size scores a quarter.
    big = matches(frame([(100, 100.58, 99.70, 100.28, 1000)], "sideways"))
    small = matches(frame([(100, 100.145, 99.925, 100.07, 1000)], "sideways"))
    assert "bullish_spinning_top" in big and "bullish_spinning_top" in small
    ratio = small["bullish_spinning_top"].strength / big["bullish_spinning_top"].strength
    assert ratio == pytest.approx(0.25, rel=0.05)
    # An affine price change must not move it (the audit relies on this).
    shifted = frame([(500, 500.58, 499.70, 500.28, 1000)], "sideways")
    assert matches(shifted)["bullish_spinning_top"].strength == pytest.approx(
        big["bullish_spinning_top"].strength, rel=1e-6
    )


def test_strength_is_descriptive_and_never_suppresses_a_detection():
    # A formation far below the display threshold is still labelled. Nothing in
    # the published definitions carries a size floor; inventing one silently
    # would change what the detector reports. BT29 3.2.
    small = matches(frame([(100, 100.145, 99.925, 100.07, 1000)], "sideways"))
    top = small["bullish_spinning_top"]
    assert top.strength < c.STRENGTH_WEAK_BELOW
    assert top.status == "formed"
    assert "bullish_spinning_top" in c.candle_tags(
        frame([(100, 100.145, 99.925, 100.07, 1000)], "sideways")
    )


def test_strength_travels_in_the_stored_document():
    got = matches(frame(STAR))["morning_star"]
    doc = got.document()
    assert doc["strength"] == got.strength > 0
    assert json.loads(json.dumps(doc))["strength"] == doc["strength"]


def test_zero_range_is_not_a_doji():
    assert not c.is_doji(pd.Series(dict(open=100, high=100, low=100, close=100)))


def test_non_numeric_data_fails_closed_without_crashing_scanner():
    bars = frame(STAR).astype(object)
    bars.iloc[-1, 3] = "bad feed value"
    assert names(bars) == set()


def test_numeric_string_feed_has_the_same_result():
    bars = frame(STAR)
    assert names(bars.astype(str)) == names(bars)


def test_saregama_missing_1126_minute_cannot_form_a_complete_1125_bucket():
    # Exact stored chart rows, read-only mt_positions snapshot 2026-09-11.
    bars = pd.DataFrame(
        [
            (512.85, 513.25, 512.85, 512.85, 722),
            (513.00, 513.00, 512.85, 512.95, 758),
            (513.00, 513.00, 512.40, 512.40, 1028),
            (512.05, 513.00, 512.05, 512.10, 2640),
        ],
        columns=COLS,
        index=pd.DatetimeIndex(
            [
                "2026-09-11 11:25",
                "2026-09-11 11:27",
                "2026-09-11 11:28",
                "2026-09-11 11:29",
            ],
            tz="Asia/Kolkata",
        ),
    )
    assert eng.resample_5m(bars).empty


def test_saregama_actual_sequence_cannot_be_promoted_as_morning_star():
    # Stored 2026-09-11 OHLC, 11:10–11:35 IST. Context is rising and the middle
    # real body overlaps the first. Both independently disqualify the label.
    rows = [
        (506.55, 507.95, 506.55, 507.95, 5652),
        (507.95, 509, 507.75, 508.70, 4515),
        (508.75, 512.85, 508.75, 512.55, 22384),
        (512.85, 513.25, 512.05, 512.10, 5148),
        (512.25, 512.80, 512.20, 512.35, 3701),
        (512.55, 514.15, 512.25, 514.15, 15806),
    ]
    bars = frame(rows, "up")
    assert "morning_star" not in c.candle_tags(bars, "5m")
    reason, tags, matches = eng._promotion_reason(bars, bars.iloc[:0])
    assert "morning_star" not in tags
    assert all(m["name"] != "morning_star" for m in matches)
    assert reason is None or "morning_star" not in reason


def test_bearish_shape_cannot_promote_a_long_via_bullish_alias():
    bars = frame(INVERTED, "up")
    assert "shooting_star" in names(bars)
    reason, tags, _ = eng._promotion_reason(bars, bars.iloc[:0])
    assert "inverted_hammer" not in tags
    assert reason is None


def test_clock_aggregation_drops_partial_and_missing_minutes():
    bars = frame(STAR, timeframe="1m")
    full = eng.resample_5m(bars)
    assert list(full.index.minute) == [15, 20]
    missing = eng.resample_5m(bars.drop(bars.index[1]))
    assert list(missing.index.minute) == [20]
    assert missing.iloc[0].to_dict() == full.iloc[1].to_dict()


def test_attention_ledger_preserves_exact_evidence():
    from unittest.mock import MagicMock

    db = MagicMock()
    evidence = {
        "pattern_rules_version": c.PATTERN_RULES_VERSION,
        "rvol": 1.6,
        "pattern_matches": [m.document() for m in c.completed_pattern_matches(frame(STAR), "5m")],
    }
    event = eng.AttentionEvent(
        "TEST",
        pd.Timestamp("2026-09-11 11:42", tz="Asia/Kolkata"),
        1.6,
        1.6,
        "pattern:morning_star:5m",
        ("morning_star",),
        evidence,
    )
    PaperLedger(db, None, False).attention(event)
    assert db["mt_attention"].insert_one.call_args.args[0]["evidence"] == evidence
