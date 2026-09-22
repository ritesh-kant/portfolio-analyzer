"""Two-stage attention watchlist and future-only one-minute entry tests."""

from __future__ import annotations

import pandas as pd
import pytest
from src.momentum_trader import engine as eng
from src.momentum_trader.levels import Level

IST = "Asia/Kolkata"
COLS = ["open", "high", "low", "close", "volume"]


def _bars(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2026-09-10 09:15", periods=len(rows), freq="1min", tz=IST)
    return pd.DataFrame(rows, index=idx, columns=COLS)


def _price_volume_overlay_bars(last_volumes: list[float]) -> pd.DataFrame:
    rows = [(100.0, 100.1, 99.9, 100.0, 100.0) for _ in range(20)]
    closes = [100.1, 100.2, 100.3, 100.5]
    for close, volume in zip(closes, last_volumes, strict=True):
        rows.append((close - 0.05, close + 0.05, close - 0.1, close, volume))
    return _bars(rows)


def test_price_volume_overlay_accepts_rising_price_and_volume() -> None:
    bars = _price_volume_overlay_bars([500.0, 1_000.0, 2_000.0, 6_000.0])
    setup, reason = eng._attention_confirmation(
        bars, 2.5, require_rising_price_volume=True
    )
    assert setup is not None, reason
    assert setup.meta["price_slope_pct_per_bar"] > 0.0
    assert setup.meta["volume_slope_per_bar"] > 0.0


def test_price_volume_overlay_rejects_rising_price_on_falling_volume() -> None:
    # The last bar remains >=2.5x its prior-volume baseline, so only the new
    # four-bar direction check can reject this otherwise-valid confirmation.
    bars = _price_volume_overlay_bars([10_000.0, 8_000.0, 6_000.0, 5_000.0])
    setup, reason = eng._attention_confirmation(
        bars, 2.5, require_rising_price_volume=True
    )
    assert setup is None
    assert reason == "attention_price_volume_not_confirmed"

    unchanged, _ = eng._attention_confirmation(bars, 2.5)
    assert unchanged is not None, "the experimental overlay must remain default-off"


def test_price_volume_overlay_never_uses_prior_session_bars() -> None:
    prior = _price_volume_overlay_bars([500.0, 1_000.0, 2_000.0, 6_000.0]).iloc[-3:]
    today = prior.iloc[-1:].copy()
    today.index = pd.DatetimeIndex([pd.Timestamp("2026-09-11 09:15", tz=IST)])
    joined = pd.concat([prior, today]).sort_index()
    assert eng.price_volume_slopes(joined, 4) is None


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


def _reclaim_cfg() -> eng.EngineConfig:
    return eng.EngineConfig(
        fill_mode=eng.FILL_FUTURE_TRIGGER,
        exit_mode="trend_full",
        use_attention_entries=True,
        attention_day_chg_min=1.5,
        attention_rvol_min=1.5,
        use_structural_exit_levels=False,
        allow_false_break_reentry=True,
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


def test_closed_one_minute_doji_never_masquerades_as_five_minute_dragonfly() -> None:
    bars = _attention_prelude().iloc[:-4].copy()
    bars.iloc[-1] = [101.60, 101.61, 101.30, 101.60, 100.0]
    st = eng.DayState("TEST", prev_close=100.0, cum_vol_profile=_profile_for(bars))

    eng.step(st, bars, _cfg(), lambda _s, _t: (0, ""))

    assert bars.index[-1].minute % 5 == 0
    assert st.attention
    assert "dragonfly_doji" not in st.attention_events[-1].candle_tags
    assert "doji" in st.attention_events[-1].candle_tags
    matches = st.attention_events[-1].evidence["pattern_matches"]
    assert all(match["timeframe"] == "1m" for match in matches)
    assert st.pending is None


def test_high_volume_bullish_one_minute_confirmation_arms_order() -> None:
    st, cfg, bars = _promoted_state()
    _confirm(st, cfg, bars)

    assert len(st.candidates) == 1
    assert st.candidates[0].setup.name == eng.ATTENTION_SETUP
    assert st.pending is not None
    assert st.pending.cand.setup.trigger == pytest.approx(102.10)


def test_promotion_measurements_survive_until_confirmation() -> None:
    st, cfg, bars = _promoted_state()
    promotion = dict(st.attention_evidence)
    bars = _confirm(st, cfg, bars)
    candidate = st.pending.cand
    assert candidate.entry_evidence["promotion"] == promotion
    assert candidate.entry_evidence["promotion"]["rvol"] == pytest.approx(1.6)
    assert candidate.entry_evidence["confirmation"]["bar_start"] == bars.index[-1].isoformat()
    assert candidate.entry_evidence["trend"]["timeframe"] == "5m"
    assert pd.Timestamp(candidate.entry_evidence["trend"]["bar_start"]) < bars.index[-1]
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


def test_local_breakout_waits_when_next_timeframe_resistance_has_no_risk_room(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not buy a 1m breakout directly beneath the next 5m ceiling."""
    bars = _attention_prelude()
    confirmation = pd.DataFrame(
        [(101.70, 102.30, 101.60, 102.20, 1200.0)],
        index=pd.DatetimeIndex([bars.index[-1] + pd.Timedelta(minutes=1)]), columns=COLS,
    )
    bars = pd.concat([bars, confirmation])
    calls = 0

    def levels_for_timeframe(*_args: object, **_kwargs: object) -> list[Level]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return [Level(102.0, "pivot_high", 2, 0.0, 1.0)]
        return [Level(102.50, "pivot_high", 2, 0.0, 1.0)]

    monkeypatch.setattr(eng, "derive_levels", levels_for_timeframe)
    monkeypatch.setattr(eng, "resample_5m", lambda _bars: _bars.iloc[:5])

    setup, reason = eng._resistance_aware_attention_confirmation(bars, 2.5, None)

    assert setup is None
    assert reason == "attention_wait_next_resistance_break"


def _confirmation_bars() -> pd.DataFrame:
    """Prelude plus a confirming minute that closes through 102.0."""
    bars = _attention_prelude()
    confirmation = pd.DataFrame(
        [(101.70, 102.30, 101.60, 102.20, 1200.0)],
        index=pd.DatetimeIndex([bars.index[-1] + pd.Timedelta(minutes=1)]), columns=COLS,
    )
    return pd.concat([bars, confirmation])


def test_v2_ignores_a_round_number_as_the_next_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A price grid is not supply: v2 must not spend a refusal on a round mark."""
    bars = _confirmation_bars()

    def levels(*_args: object, **_kwargs: object) -> list[Level]:
        return [Level(102.0, "pivot_high", 2, 0.0, 1.0),
                Level(102.35, "round", 1, 0.0, 0.8)]

    monkeypatch.setattr(eng, "derive_levels", levels)
    monkeypatch.setattr(eng, "resample_5m", lambda _bars: _bars.iloc[:5])

    blocked, reason = eng._resistance_aware_attention_confirmation(bars, 2.5, None)
    assert blocked is None and reason == "attention_wait_next_resistance_break"

    setup, _ = eng._resistance_aware_attention_confirmation(bars, 2.5, None, v2=True)
    assert setup is not None, "v2 should not refuse on the round level alone"


def test_v2_does_not_merge_five_minute_levels(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the first (one-minute) level set may be consulted under v2."""
    bars = _confirmation_bars()
    calls = 0

    def levels(*_args: object, **_kwargs: object) -> list[Level]:
        nonlocal calls
        calls += 1
        # first call = 1m levels, second = the 5m merge v2 must skip
        return ([Level(102.0, "pivot_high", 2, 0.0, 1.0)] if calls == 1
                else [Level(102.50, "pivot_high", 2, 0.0, 1.0)])

    monkeypatch.setattr(eng, "derive_levels", levels)
    monkeypatch.setattr(eng, "resample_5m", lambda _bars: _bars.iloc[:5])

    setup, _ = eng._resistance_aware_attention_confirmation(bars, 2.5, None, v2=True)
    assert calls == 1, "v2 called derive_levels twice; the 5m merge is still on"
    assert setup is not None


def test_v2_refusal_ends_the_day_rather_than_freeing_it() -> None:
    """A refused setup must not hand its slot to a later, worse confirmation."""
    st = eng.DayState("TEST", prev_close=100.0, cum_vol_profile=None)
    assert st.resistance_refused is False
    st.resistance_refused = True
    cfg = eng.EngineConfig(use_attention_entries=True, require_resistance_breakout=True,
                           resistance_veto_v2=True)
    bars = _confirmation_bars()
    eng.step(st, bars, cfg, lambda _s, _t: (0, ""))
    assert st.candidates == [], "entries continued after a v2 refusal"


def test_resistance_veto_v2_is_off_by_default() -> None:
    """The shipped arm keeps the operator's rule exactly as written."""
    assert eng.EngineConfig().resistance_veto_v2 is False


def test_false_break_reclaim_allows_one_new_future_only_entry() -> None:
    """A retry requires a later high-volume close above the exact failed level."""
    bars = _attention_prelude()
    cfg = _reclaim_cfg()
    state = eng.DayState("TEST", prev_close=100.0, cum_vol_profile=_profile_for(bars))
    state.attention = True
    original = eng.Candidate(
        symbol="TEST", time=bars.index[-1], setup=eng.Setup(
            eng.ATTENTION_SETUP, trigger=102.0, stop=101.4, level=102.0,
        ), day_chg_pct=1.6, rvol=1.6, catalyst=0, event_type="", candle_tags=[],
    )
    plan = eng.plan_trade(102.0, 101.4, risk_inr=500.0, max_notional_inr=50_000.0)
    assert plan is not None
    state.position = eng.Position(
        cand=original, entry_time=bars.index[-1], plan=plan, highest=102.0,
        exit_state=eng.exits.initial_state(102.0, 101.4, bars, None),
    )
    state.traded_today = True
    eng._exit(state, bars.index[-1], 101.8, "false_break", cfg)
    assert state.false_break_reclaim is not None

    # A weak recovery cannot consume the one retry.
    weak = pd.DataFrame(
        [(101.8, 102.1, 101.6, 101.95, 100.0)],
        index=pd.DatetimeIndex([bars.index[-1] + pd.Timedelta(minutes=1)]), columns=COLS,
    )
    bars = pd.concat([bars, weak])
    state.cum_vol_profile = _profile_for(bars)
    eng.step(state, bars, cfg, lambda _s, _t: (0, ""))
    assert state.pending is None and state.false_break_reclaim is not None

    # A later green, upper-range, 2.5x-volume close through 102.00 arms one buy-stop.
    reclaim = pd.DataFrame(
        [(101.95, 102.40, 101.70, 102.32, 1200.0)],
        index=pd.DatetimeIndex([bars.index[-1] + pd.Timedelta(minutes=1)]), columns=COLS,
    )
    bars = pd.concat([bars, reclaim])
    state.cum_vol_profile = _profile_for(bars)
    eng.step(state, bars, cfg, lambda _s, _t: (0, ""))
    assert state.pending is not None
    assert state.pending.cand.setup.name == eng.ATTENTION_FALSE_BREAK_RECLAIM_SETUP
    assert state.pending.cand.setup.level == pytest.approx(102.0)
    assert state.false_break_reclaim is None and state.false_break_reclaim_attempted

    decision = state.pending.decision_time
    assert decision is not None
    assert eng.fill_pending_quote(state, decision + pd.Timedelta(seconds=1), 102.40, cfg, bars)

    # The retry can never create a third attempt after it too false-breaks.
    eng._exit(state, decision + pd.Timedelta(minutes=1), 101.9, "false_break", cfg)
    assert state.false_break_reclaim is None
