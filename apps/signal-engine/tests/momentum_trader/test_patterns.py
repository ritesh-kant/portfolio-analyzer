"""Deterministic tests for the Warrior-derived setups, candle tags, indicators and risk.

Bars are built by hand so each detector is exercised on the exact geometry the
study guide describes, plus a negative case that differs in one respect.
"""

from __future__ import annotations

import pandas as pd
import pytest
from src.momentum_trader import candles, indicators, risk, setups

IST = "Asia/Kolkata"


def _bars(rows: list[tuple[float, float, float, float, float]], start: str = "2026-09-07 09:15",
          freq: str = "5min") -> pd.DataFrame:
    """rows = (open, high, low, close, volume)."""
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz=IST)
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close", "volume"])


Row = tuple[float, float, float, float, float]


def _flat(n: int, px: float = 100.0, vol: float = 1000.0) -> list[Row]:
    return [(px, px + 0.1, px - 0.1, px, vol)] * n


# ── indicators ────────────────────────────────────────────────────────────────

def test_session_vwap_resets_per_day() -> None:
    d1 = _bars([(100, 101, 99, 100, 100), (100, 102, 100, 102, 100)], start="2026-09-07 09:15")
    d2 = _bars([(200, 201, 199, 200, 100)], start="2026-09-08 09:15")
    bars = pd.concat([d1, d2])
    vw = indicators.session_vwap(bars)
    assert vw.iloc[0] == pytest.approx(100.0)
    assert vw.iloc[1] == pytest.approx((100 + 101.333333) / 2, rel=1e-4)
    assert vw.iloc[2] == pytest.approx(200.0)  # reset on the new day


def test_relative_volume_by_time_compares_same_clock_time() -> None:
    hist_rows = []
    for day in ("2026-09-01", "2026-09-02", "2026-09-03"):
        hist_rows.append(_bars([(100, 101, 99, 100, 1000)] * 4, start=f"{day} 09:15"))
    history = pd.concat(hist_rows)
    today = _bars([(100, 101, 99, 100, 2500)] * 2, start="2026-09-07 09:15")
    # today cum at 09:20 = 5000; history cum through 09:20 = 2000/day
    assert indicators.relative_volume_by_time(today, history) == pytest.approx(2.5)


def test_relative_volume_none_without_history() -> None:
    today = _bars(_flat(2))
    assert indicators.relative_volume_by_time(today, today.iloc[0:0]) is None


def test_round_levels_scale_with_price() -> None:
    assert indicators.round_levels_above(47.0) == (50.0, 50.0)
    assert indicators.round_levels_above(123.0) == (130.0, 150.0)
    assert indicators.round_levels_above(1240.0) == (1250.0, 1300.0)


def test_validate_bars_rejects_missing_columns() -> None:
    with pytest.raises(ValueError):
        indicators.validate_bars(pd.DataFrame({"open": [1.0]}))


# ── candle tags ───────────────────────────────────────────────────────────────

def test_hammer_and_inverted_hammer() -> None:
    hammer = pd.Series({"open": 100.0, "high": 100.2, "low": 97.0, "close": 100.1})
    inv = pd.Series({"open": 100.0, "high": 103.0, "low": 99.9, "close": 100.1})
    assert candles.is_hammer(hammer) and not candles.is_inverted_hammer(hammer)
    assert candles.is_inverted_hammer(inv) and not candles.is_hammer(inv)


def test_doji_variants() -> None:
    doji = pd.Series({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.05})
    dragonfly = pd.Series({"open": 100.0, "high": 100.05, "low": 98.0, "close": 100.0})
    gravestone = pd.Series({"open": 100.0, "high": 102.0, "low": 99.95, "close": 100.0})
    assert candles.is_doji(doji)
    assert candles.is_dragonfly_doji(dragonfly)
    assert candles.is_gravestone_doji(gravestone)


def test_engulfing() -> None:
    red = pd.Series({"open": 101.0, "high": 101.5, "low": 99.5, "close": 100.0})
    green = pd.Series({"open": 99.8, "high": 102.0, "low": 99.5, "close": 101.5})
    assert candles.is_bullish_engulfing(red, green)
    assert not candles.is_bearish_engulfing(red, green)
    red2 = pd.Series({"open": 101.6, "high": 101.7, "low": 99.0, "close": 99.5})
    assert candles.is_bearish_engulfing(green, red2)


def test_three_white_soldiers_and_morning_star() -> None:
    a = pd.Series({"open": 100.0, "high": 101.2, "low": 99.8, "close": 101.0})
    b = pd.Series({"open": 100.5, "high": 102.2, "low": 100.3, "close": 102.0})
    c = pd.Series({"open": 101.5, "high": 103.2, "low": 101.3, "close": 103.0})
    assert candles.is_three_white_soldiers(a, b, c)
    big_red = pd.Series({"open": 104.0, "high": 104.1, "low": 100.0, "close": 100.2})
    star = pd.Series({"open": 100.0, "high": 100.6, "low": 99.5, "close": 100.05})
    big_green = pd.Series({"open": 100.3, "high": 103.5, "low": 100.2, "close": 103.4})
    assert candles.is_morning_star(big_red, star, big_green)


def test_candle_tags_on_frame() -> None:
    bars = _bars([
        (104.0, 104.1, 100.0, 100.2, 1000),
        (100.0, 100.6, 99.5, 100.05, 800),
        (100.3, 103.5, 100.2, 103.4, 1500),
    ])
    tags = candles.candle_tags(bars)
    assert "morning_star" in tags
    assert candles.candle_tags(bars.iloc[0:0]) == []


def test_morning_star_rejects_small_first_or_last_body() -> None:
    # A red/indecision/green sequence alone is not enough: both impulse bodies
    # must occupy at least 55% of their candle ranges.
    weak_red = pd.Series({"open": 104.0, "high": 106.0, "low": 99.0, "close": 103.0})
    star = pd.Series({"open": 102.9, "high": 103.4, "low": 102.5, "close": 102.95})
    green = pd.Series({"open": 102.8, "high": 105.0, "low": 102.7, "close": 104.8})
    assert not candles.is_morning_star(weak_red, star, green)


def test_morning_doji_star_and_rising_three_evidence() -> None:
    morning = _bars([
        (108.0, 108.1, 106.9, 107.0, 900),
        (107.0, 107.1, 105.9, 106.0, 900),
        (106.0, 106.1, 104.9, 105.0, 900),
        (104.0, 104.1, 100.0, 100.2, 1000),
        (100.0, 100.6, 99.5, 100.05, 800),  # doji star
        (100.3, 103.5, 100.2, 103.4, 1500),
    ])
    assert candles.is_morning_doji_star(morning.iloc[-3], morning.iloc[-2], morning.iloc[-1])
    morning_match = candles.completed_pattern_matches(morning, "5m")
    assert {m.name for m in morning_match} == {"morning_doji_star"}
    assert all(m.timeframe == "5m" and m.start < m.end for m in morning_match)

    rising = _bars([
        (100.0, 105.2, 99.9, 105.0, 5000),  # impulse
        (104.8, 104.9, 103.9, 104.6, 900),
        (104.5, 104.6, 103.5, 104.3, 800),
        (104.2, 104.3, 103.3, 104.0, 700),
        (103.7, 106.0, 103.6, 105.6, 4500),  # continuation close above first high
    ])
    assert candles.is_rising_three(*[rising.iloc[i] for i in range(5)])
    matches = candles.completed_pattern_matches(rising, "5m")
    assert len(matches) == 1 and matches[0].name == "rising_three"
    assert "rising_three" in candles.candle_tags(rising)


# ── setups ────────────────────────────────────────────────────────────────────

def _pole_flag_break() -> list[tuple[float, float, float, float, float]]:
    """Textbook bull flag: 3-bar pole 100→105 on volume, 3 quiet pullback bars
    to ~103, then a bar that takes out the prior bar's high."""
    pole = [
        (100.0, 101.8, 99.9, 101.7, 5000),
        (101.7, 103.6, 101.6, 103.5, 6000),
        (103.5, 105.0, 103.4, 104.9, 7000),
    ]
    flag = [
        (104.9, 104.9, 103.9, 104.0, 2000),
        (104.0, 104.2, 103.3, 103.5, 1500),
        (103.5, 103.8, 103.1, 103.4, 1200),
    ]
    trigger = [(103.4, 104.4, 103.3, 104.3, 4000)]  # high 104.4 > prev high 103.8
    return _flat(9) + pole + flag + trigger


def test_bull_flag_fires_on_new_high_after_quiet_pullback() -> None:
    s = setups.bull_flag(_bars(_pole_flag_break()))
    assert s is not None and s.name == "bull_flag"
    assert s.trigger == pytest.approx(103.8)   # previous bar's high
    assert s.stop == pytest.approx(103.1)      # flag low
    assert s.level == pytest.approx(105.0)     # pole high
    assert s.meta["pole_pct"] > setups.POLE_MIN_PCT


def test_bull_flag_rejects_when_flag_volume_does_not_dry_up() -> None:
    rows = _pole_flag_break()
    rows[-4:-1] = [(o, h, lo, c, 9000.0) for (o, h, lo, c, _) in rows[-4:-1]]
    assert setups.bull_flag(_bars(rows)) is None


def test_bull_flag_rejects_deep_retrace() -> None:
    rows = _pole_flag_break()
    rows[-3] = (104.0, 104.2, 101.0, 101.5, 1500)  # gives back >50% of the pole
    assert setups.bull_flag(_bars(rows)) is None


def test_bull_flag_rejects_when_no_new_high() -> None:
    rows = _pole_flag_break()
    rows[-1] = (103.4, 103.7, 103.2, 103.6, 4000)  # high 103.7 < prev high 103.8
    assert setups.bull_flag(_bars(rows)) is None


def test_flat_top_breakout_requires_close_through_level() -> None:
    base = _flat(6, px=100.0)
    tops = [
        (100.0, 102.0, 99.8, 101.5, 1000),
        (101.5, 101.9, 100.8, 101.0, 900),
        (101.0, 102.0, 100.9, 101.6, 950),
        (101.6, 101.8, 100.7, 101.2, 800),
        (101.2, 101.95, 100.9, 101.5, 900),
        (101.5, 101.9, 101.0, 101.4, 850),
    ]
    wick_only = [(101.4, 102.4, 101.2, 101.9, 3000)]  # pokes above 102 but closes below
    close_through = [(101.4, 102.6, 101.3, 102.5, 3000)]
    assert setups.flat_top_breakout(_bars(base + tops + wick_only)) is None
    s = setups.flat_top_breakout(_bars(base + tops + close_through))
    assert s is not None and s.name == "flat_top_breakout"
    assert s.trigger == pytest.approx(102.0)
    assert s.stop == pytest.approx(100.7)
    assert s.meta["touches"] >= setups.FLAT_TOP_MIN_TOUCHES


def test_ma_pullback_fires_after_touch_of_ema9_in_uptrend() -> None:
    # steady uptrend so EMA9 > EMA20, then a 2-bar dip onto EMA9, then a new high
    rows: list[tuple[float, float, float, float, float]] = []
    px = 100.0
    for _ in range(26):
        rows.append((px, px + 0.5, px - 0.1, px + 0.4, 1000))
        px += 0.4
    bars = _bars(rows)
    e9 = indicators.ema(bars["close"], 9).iloc[-1]
    # pullback bars: lows touch EMA9, closes stay above EMA20
    dip1 = (px, px + 0.1, float(e9) - 0.01, float(e9) + 0.15, 700)
    dip2 = (float(e9) + 0.15, float(e9) + 0.3, float(e9) - 0.02, float(e9) + 0.2, 600)
    brk = (float(e9) + 0.2, float(e9) + 0.6, float(e9) + 0.15, float(e9) + 0.55, 1800)
    s = setups.ma_pullback(_bars(rows + [dip1, dip2, brk]))
    assert s is not None and s.name == "ma9_pullback"
    assert s.trigger == pytest.approx(dip2[1])
    assert s.stop == pytest.approx(min(dip1[2], dip2[2]))


def test_ma_pullback_rejects_without_touch() -> None:
    rows: list[tuple[float, float, float, float, float]] = []
    px = 100.0
    for _ in range(30):
        rows.append((px, px + 0.5, px + 0.05, px + 0.4, 1000))  # never dips to the EMA
        px += 0.4
    assert setups.ma_pullback(_bars(rows)) is None


def test_vwap_reclaim() -> None:
    rows = [
        (100.0, 100.5, 99.0, 99.2, 3000),   # sells off, closes below VWAP
        (99.2, 99.6, 98.5, 98.8, 2500),
        (98.8, 99.0, 98.2, 98.6, 2000),
        (98.6, 99.9, 98.5, 99.8, 4000),     # reclaims above VWAP
        (99.8, 100.3, 99.6, 100.2, 3000),   # holds
        (100.2, 100.4, 99.7, 99.9, 1500),   # pullback holds above VWAP
        (99.9, 100.8, 99.8, 100.7, 3500),   # new high → trigger
    ]
    s = setups.vwap_reclaim(_bars(rows))
    assert s is not None and s.name == "vwap_reclaim"
    assert s.trigger == pytest.approx(100.4)
    assert s.stop == pytest.approx(99.6)


def test_orb15_first_break_only() -> None:
    orb = [
        (100.0, 101.0, 99.5, 100.5, 5000),
        (100.5, 101.2, 100.0, 100.8, 4000),
        (100.8, 101.1, 100.4, 100.6, 3000),
    ]
    inside = [(100.6, 101.0, 100.3, 100.9, 2000)]
    brk = [(100.9, 101.6, 100.8, 101.5, 4500)]
    bars = _bars(orb + inside + brk)  # 09:15..09:35, breakout bar at 09:35
    s = setups.opening_range_breakout(bars)
    assert s is not None and s.name == "orb15"
    assert s.trigger == pytest.approx(101.2)
    assert s.stop == pytest.approx(99.5)
    # a second close above the level later is NOT the first break
    later = _bars(orb + inside + brk + [(101.5, 101.9, 101.3, 101.8, 3000)])
    assert setups.opening_range_breakout(later) is None
    # still inside the range → nothing
    assert setups.opening_range_breakout(_bars(orb)) is None


def test_red_to_green() -> None:
    prev_close = 100.0
    rows = [
        (98.5, 99.0, 98.0, 98.8, 3000),
        (98.8, 99.8, 98.6, 99.7, 2500),
        (99.7, 100.6, 99.5, 100.4, 4000),
    ]
    s = setups.red_to_green(_bars(rows), prev_close)
    assert s is not None and s.trigger == pytest.approx(100.0) and s.stop == pytest.approx(98.0)
    # opened above prev close → not red-to-green
    green_open = _bars([(100.5, 101, 100.2, 100.8, 1), (100.8, 101.5, 100.6, 101.2, 1)])
    assert setups.red_to_green(green_open, prev_close) is None


def test_micro_pullback_on_1min() -> None:
    rows = [
        (100.0, 100.4, 99.9, 100.3, 500),     # green run
        (100.3, 100.7, 100.2, 100.6, 600),
        (100.6, 100.7, 100.3, 100.4, 300),    # red pause
        (100.4, 100.9, 100.35, 100.85, 900),  # breaks pause high
    ]
    s = setups.micro_pullback(_bars(rows, freq="1min"))
    assert s is not None and s.trigger == pytest.approx(100.7) and s.stop == pytest.approx(100.3)
    rows[2] = (100.6, 100.9, 100.5, 100.85, 300)  # pause bar is a strong green → not a pullback
    assert setups.micro_pullback(_bars(rows, freq="1min")) is None


def test_micro_pullback_rejects_zero_range_pause_bar() -> None:
    """A pause bar where high == low (one price traded that minute) would give
    trigger == stop — a stop distance of exactly zero, which `plan_trade` does
    not catch because it judges the band against the fill, not the trigger.
    Real case: KAJARIACER 2024-11-25 11:35, trigger == stop == 1217.35."""
    rows = [
        (100.0, 100.4, 99.9, 100.3, 500),      # green run
        (100.3, 100.7, 100.2, 100.6, 600),
        (100.7, 100.7, 100.7, 100.7, 100),     # zero-range pause: high == low
        (100.7, 101.1, 100.65, 101.0, 900),    # breaks the pause high
    ]
    assert setups.micro_pullback(_bars(rows, freq="1min")) is None
    # one tick of range is enough — the guard rejects only the degenerate bar
    rows[2] = (100.7, 100.7, 100.65, 100.7, 100)
    s = setups.micro_pullback(_bars(rows, freq="1min"))
    assert s is not None
    assert s.trigger == pytest.approx(100.7) and s.stop == pytest.approx(100.65)


def test_setups_never_emit_a_stop_at_or_above_the_trigger() -> None:
    """The guard is applied by every detector, not just micro_pullback: each one
    derives its stop from a bar low, so any all-zero-range window is degenerate."""
    zero_range: list[Row] = [(100.0, 100.0, 100.0, 100.0, 100.0)] * 30
    found = setups.scan_setups(_bars(zero_range),
                               bars_1m=_bars(zero_range, freq="1min"),
                               prev_close=101.0)
    for st in found:
        assert st.stop < st.trigger
    assert setups._has_stop_room(100.0, 100.0) is False
    assert setups._has_stop_room(100.0, 100.5) is False
    assert setups._has_stop_room(100.0, 99.95) is True


def test_false_break_detects_close_back_below_level() -> None:
    bars = _bars([(100.0, 102.5, 99.9, 102.2, 1), (102.2, 102.3, 100.5, 101.0, 1)])
    assert setups.false_break(bars, level=102.0)
    assert not setups.false_break(bars, level=100.0)


def test_scan_setups_returns_all_that_fire() -> None:
    bars = _bars(_pole_flag_break())
    found = setups.scan_setups(bars)
    assert any(s.name == "bull_flag" for s in found)
    assert setups.scan_setups(_bars(_flat(30))) == []


# ── risk ──────────────────────────────────────────────────────────────────────

def test_plan_trade_sizes_by_risk_and_2to1() -> None:
    p = risk.plan_trade(entry=200.0, stop=198.0, risk_inr=1000.0, max_notional_inr=5_00_000.0)
    assert p is not None
    assert p.qty == 500                      # ₹1000 / ₹2 per share
    assert p.target == pytest.approx(204.0)  # 2 × ₹2 above entry
    assert p.rr == pytest.approx(2.0)
    assert p.notional_inr == pytest.approx(100_000.0)


def test_plan_trade_notional_cap_binds() -> None:
    p = risk.plan_trade(entry=200.0, stop=199.0, risk_inr=5000.0, max_notional_inr=50_000.0)
    assert p is not None and p.qty == 250 and p.risk_inr == pytest.approx(250.0)


def test_plan_trade_rejects_insane_stops() -> None:
    def plan(stop: float) -> risk.TradePlan | None:
        return risk.plan_trade(entry=200.0, stop=stop, risk_inr=1000.0, max_notional_inr=1e6)

    assert plan(199.9) is None  # too tight
    assert plan(180.0) is None  # too wide
    assert plan(201.0) is None  # inverted


def test_target_for_requires_long_geometry() -> None:
    with pytest.raises(ValueError):
        risk.target_for(100.0, 101.0)
