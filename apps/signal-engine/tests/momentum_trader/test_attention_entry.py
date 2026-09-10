"""Two-stage attention watchlist and future-only one-minute entry tests."""

from __future__ import annotations

import pandas as pd
import pytest
from src.momentum_trader import engine as eng

IST = "Asia/Kolkata"
COLS = ["open", "high", "low", "close", "volume"]


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2026-09-10 09:15", periods=len(rows), freq="1min", tz=IST)
    return pd.DataFrame(rows, index=idx, columns=COLS)


def _attention_prelude() -> pd.DataFrame:
    """Twenty-five rising 5-minute buckets ending in a doji above EMA/VWAP."""
    rows: list[tuple[float, float, float, float, float]] = []
    px = 100.0
    for bucket in range(24):
        close = 100.05 + bucket * 0.065
        for minute in range(5):
            o = px
            c = close if minute == 4 else o + (close - o) / max(1, 5 - minute)
            rows.append((o, max(o, c) + 0.04, min(o, c) - 0.03, c, 100.0))
            px = c
    # Final completed five-minute candle is a doji, while its last one-minute
    # bar is red so the following minute can form a bullish engulfing pattern.
    rows.extend([
        (101.55, 101.66, 101.52, 101.61, 100.0),
        (101.61, 101.68, 101.57, 101.64, 100.0),
        (101.64, 101.69, 101.59, 101.62, 100.0),
        (101.62, 101.68, 101.58, 101.64, 100.0),
        (101.64, 101.70, 101.57, 101.60, 100.0),
    ])
    return _bars(rows)


def _profile_for(bars: pd.DataFrame, rvol: float = 1.6) -> pd.Series:
    cumulative = bars["volume"].cumsum() / rvol
    return pd.Series(cumulative.to_numpy(), index=bars.index.time)


def _cfg() -> eng.EngineConfig:
    return eng.EngineConfig(
        fill_mode=eng.FILL_FUTURE_TRIGGER,
        exit_mode="trend_full",
        use_attention_entries=True,
        attention_day_chg_min=1.5,
        attention_rvol_min=1.5,
    )


def _resistance_cfg() -> eng.EngineConfig:
    return eng.EngineConfig(
        fill_mode=eng.FILL_FUTURE_TRIGGER,
        exit_mode="trend_resistance_state",
        use_attention_entries=True,
        attention_day_chg_min=1.5,
        attention_rvol_min=1.5,
        require_resistance_breakout=True,
    )


def _promoted_state() -> tuple[eng.DayState, eng.EngineConfig, pd.DataFrame]:
    bars = _attention_prelude()
    st = eng.DayState("TEST", prev_close=100.0, cum_vol_profile=_profile_for(bars))
    cfg = _cfg()
    eng.step(st, bars, cfg, lambda _s, _t: (0, ""))
    return st, cfg, bars


def _confirm(st: eng.DayState, cfg: eng.EngineConfig, bars: pd.DataFrame) -> pd.DataFrame:
    confirmation = pd.DataFrame(
        [(101.56, 102.10, 101.54, 102.06, 1200.0)],
        index=pd.DatetimeIndex([bars.index[-1] + pd.Timedelta(minutes=1)]),
        columns=COLS,
    )
    bars = pd.concat([bars, confirmation])
    st.cum_vol_profile = _profile_for(bars)
    eng.step(st, bars, cfg, lambda _s, _t: (0, ""))
    return bars


def test_soft_thresholds_promote_but_do_not_enter() -> None:
    st, _, _ = _promoted_state()

    assert st.attention
    assert len(st.attention_events) == 1
    assert st.attention_events[0].day_chg_pct == pytest.approx(1.6)
    assert st.attention_events[0].rvol == pytest.approx(1.6)
    assert st.position is None and st.pending is None


def test_evolving_five_minute_dragonfly_can_promote_before_bucket_close() -> None:
    bars = _attention_prelude().iloc[:-4].copy()
    bars.iloc[-1] = [101.60, 101.61, 101.30, 101.60, 100.0]
    st = eng.DayState("TEST", prev_close=100.0, cum_vol_profile=_profile_for(bars))

    eng.step(st, bars, _cfg(), lambda _s, _t: (0, ""))

    assert bars.index[-1].minute % 5 == 0
    assert st.attention
    assert "dragonfly_doji" in st.attention_events[-1].candle_tags
    assert st.pending is None


def test_high_volume_bullish_one_minute_confirmation_arms_order() -> None:
    st, cfg, bars = _promoted_state()
    _confirm(st, cfg, bars)

    assert len(st.candidates) == 1
    assert st.candidates[0].setup.name == eng.ATTENTION_SETUP
    assert st.pending is not None
    assert st.pending.cand.setup.trigger == pytest.approx(102.10)
    assert st.position is None


def test_strong_one_minute_breakout_need_not_form_a_second_named_pattern() -> None:
    st, cfg, bars = _promoted_state()
    breakout = pd.DataFrame(
        [(101.80, 102.10, 101.75, 102.04, 1200.0)],
        index=pd.DatetimeIndex([bars.index[-1] + pd.Timedelta(minutes=1)]),
        columns=COLS,
    )
    bars = pd.concat([bars, breakout])
    st.cum_vol_profile = _profile_for(bars)
    assert eng.candle_tags(bars) == []

    eng.step(st, bars, cfg, lambda _s, _t: (0, ""))

    assert st.pending is not None
    assert st.pending.cand.setup.trigger == pytest.approx(102.10)


def test_attention_records_why_a_watched_minute_did_not_enter() -> None:
    st, cfg, bars = _promoted_state()
    quiet_red = pd.DataFrame(
        [(101.60, 101.64, 101.48, 101.50, 100.0)],
        index=pd.DatetimeIndex([bars.index[-1] + pd.Timedelta(minutes=1)]),
        columns=COLS,
    )
    bars = pd.concat([bars, quiet_red])
    st.cum_vol_profile = _profile_for(bars)

    eng.step(st, bars, cfg, lambda _s, _t: (0, ""))

    assert st.pending is None
    assert st.rejections[-1].reason == "attention_red_or_flat"


def test_future_buy_stop_waits_below_trigger_then_fills_on_later_quote() -> None:
    st, cfg, bars = _promoted_state()
    bars = _confirm(st, cfg, bars)
    assert st.pending is not None
    decision = st.pending.decision_time
    assert decision is not None

    before = decision - pd.Timedelta(milliseconds=1)
    assert eng.fill_pending_quote(st, before, 102.20, cfg, bars) is None
    assert eng.fill_pending_quote(st, decision + pd.Timedelta(seconds=1), 101.90, cfg, bars) is None
    assert st.pending is not None and st.position is None

    pos = eng.fill_pending_quote(
        st, decision + pd.Timedelta(seconds=2), 102.10, cfg, bars
    )
    assert pos is not None
    assert pos.plan.entry == pytest.approx(102.10)
    assert pos.entry_time > decision


def test_replay_does_not_treat_a_high_only_touch_as_an_executable_fill() -> None:
    st, cfg, bars = _promoted_state()
    bars = _confirm(st, cfg, bars)
    assert st.pending is not None and st.pending.decision_time is not None
    first = pd.DataFrame(
        [(102.00, 102.20, 101.80, 101.95, 200.0)],
        index=pd.DatetimeIndex([st.pending.decision_time]),
        columns=COLS,
    )
    bars = pd.concat([bars, first])

    eng.step(st, bars, cfg, lambda _s, _t: (0, ""))

    assert st.position is None and st.pending is not None

    second = pd.DataFrame(
        [(102.00, 102.30, 101.95, 102.15, 200.0)],
        index=pd.DatetimeIndex([bars.index[-1] + pd.Timedelta(minutes=1)]),
        columns=COLS,
    )
    bars = pd.concat([bars, second])
    eng.step(st, bars, cfg, lambda _s, _t: (0, ""))

    assert st.position is not None
    assert st.position.plan.entry == pytest.approx(102.15)
    assert st.position.entry_time > st.position.cand.time


def test_partial_entry_minute_does_not_use_price_action_from_before_fill() -> None:
    st, cfg, bars = _promoted_state()
    bars = _confirm(st, cfg, bars)
    assert st.pending is not None and st.pending.decision_time is not None
    decision = st.pending.decision_time
    pos = eng.fill_pending_quote(
        st, decision + pd.Timedelta(seconds=30), 102.10, cfg, bars
    )
    assert pos is not None
    partial_entry_bar = pd.DataFrame(
        [(102.00, 103.00, 101.80, 102.20, 500.0)],
        index=pd.DatetimeIndex([decision]),
        columns=COLS,
    )
    bars = pd.concat([bars, partial_entry_bar])

    eng.step(st, bars, cfg, lambda _s, _t: (0, ""))

    assert st.position is not None
    assert st.position.highest == pytest.approx(102.10)


def test_chased_future_quote_is_rejected_with_reason() -> None:
    st, cfg, bars = _promoted_state()
    bars = _confirm(st, cfg, bars)
    assert st.pending is not None and st.pending.decision_time is not None
    when = st.pending.decision_time + pd.Timedelta(seconds=1)

    assert eng.fill_pending_quote(st, when, 103.20, cfg, bars) is None
    assert st.pending is None
    assert st.rejections[-1].reason == "chased"


def test_resistance_aware_confirmation_waits_for_a_nearby_structural_ceiling() -> None:
    bars = _attention_prelude()
    # The current high-volume confirmation is valid in isolation, but a
    # previous-session ceiling sits less than its initial risk above the trigger.
    confirmation = pd.DataFrame(
        [(101.60, 102.10, 101.54, 102.06, 1200.0)],
        index=pd.DatetimeIndex([bars.index[-1] + pd.Timedelta(minutes=1)]), columns=COLS,
    )
    bars = pd.concat([bars, confirmation])
    setup, reason = eng._resistance_aware_attention_confirmation(
        bars, 2.5, {"high": 102.50, "low": 99.0, "close": 100.0}
    )
    assert setup is None and reason == "attention_wait_resistance_break"


def test_resistance_aware_confirmation_uses_the_crossed_ceiling_as_false_break_level() -> None:
    bars = _attention_prelude()
    # This volume-backed candle closes through the pre-existing prior-day high.
    confirmation = pd.DataFrame(
        [(101.70, 102.30, 101.60, 102.20, 1200.0)],
        index=pd.DatetimeIndex([bars.index[-1] + pd.Timedelta(minutes=1)]), columns=COLS,
    )
    bars = pd.concat([bars, confirmation])
    setup, reason = eng._resistance_aware_attention_confirmation(
        bars, 2.5, {"high": 102.0, "low": 99.0, "close": 100.0}
    )
    assert setup is not None and reason == "confirmed_resistance_breakout"
    assert setup.level == pytest.approx(102.0)
