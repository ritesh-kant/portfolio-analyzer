"""Engine lifecycle tests: pending → fill at next open → stop / target / EOD exits,
RVOL gating, entry cutoff, one-trade-per-day, chase cancel; plus the bar
builder, Upstox parsing, universe filters, and the catalyst lookup."""

from __future__ import annotations

from datetime import time

import pandas as pd
import pytest
from src.momentum_trader import catalyst, engine, exits, universe
from src.momentum_trader.bars import IST, BarBuilder
from src.momentum_trader.upstox import candles_to_frame
from src.news_trader.trailing_sl import calc_costs

Row = tuple[float, float, float, float, float]
COLS = ["open", "high", "low", "close", "volume"]


def _bars(rows: list[Row], start: str | pd.Timestamp = "2026-09-07 09:15",
          freq: str = "1min") -> pd.DataFrame:
    if isinstance(start, pd.Timestamp):
        start = start.tz_localize(None).strftime("%Y-%m-%d %H:%M")
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz=IST)
    return pd.DataFrame(rows, index=idx, columns=COLS)


def _flat_profile(per_min: float = 100.0) -> pd.Series:
    """Cumulative-volume profile where every prior day traded `per_min` shares per minute."""
    times = pd.date_range("2026-01-01 09:15", "2026-01-01 15:29", freq="1min").time
    return pd.Series([per_min * (i + 1) for i in range(len(times))], index=times)


def _no_cat(_s: str, _t: pd.Timestamp) -> tuple[int, str]:
    return 0, ""


def _yes_cat(_s: str, _t: pd.Timestamp) -> tuple[int, str]:
    return 1, "order_win"


def _run(day: pd.DataFrame, prev_close: float = 100.0, profile: pd.Series | None = None,
         cfg: engine.EngineConfig | None = None, cat=_no_cat) -> engine.DayState:  # noqa: ANN001
    prof = _flat_profile() if profile is None else profile
    return engine.run_day("TEST", day, prev_close, prof, cfg or engine.EngineConfig(), cat)


# The 5-min setups must NOT fire in the prelude, so the opening range is wide
# (high 106.5 on every prelude bar — also the flat-top ceiling nobody closes
# above) while closes sit at 105, i.e. +5% on the day on 5× normal volume.
PRELUDE: Row = (105.0, 106.5, 104.9, 105.0, 500)
TRIGGER: Row = (105.35, 105.9, 105.3, 105.8, 900)   # breaks the 105.6 pause high


def _mover_day(after: list[Row], trigger: Row = TRIGGER) -> pd.DataFrame:
    """+5% mover printing a 1-min micro pullback: 2 green bars (09:57, 09:58),
    a red pause bar (09:59 → trigger 105.6 / stop 105.3), the break at 10:00."""
    rows: list[Row] = [PRELUDE] * 42
    rows += [
        (105.0, 105.3, 104.95, 105.25, 500),
        (105.25, 105.6, 105.2, 105.55, 500),
        (105.55, 105.6, 105.3, 105.35, 300),
        trigger,
        *after,
    ]
    return _bars(rows)


def test_pending_then_fill_at_next_open_then_target() -> None:
    after: list[Row] = [
        (105.85, 106.0, 105.8, 105.95, 700),   # 10:01 fill @ open 105.85
        (105.95, 106.7, 105.9, 106.6, 900),    # 10:02 target 106.95 not reached
        (106.6, 107.2, 106.5, 107.0, 900),     # 10:03 high 107.2 ≥ target → exit
    ]
    st = _run(_mover_day(after), cat=_yes_cat)
    assert [c.setup.name for c in st.candidates] == ["micro_pullback"]
    assert st.candidates[0].catalyst == 1 and st.candidates[0].event_type == "order_win"
    assert st.candidates[0].time.time() == time(10, 0)
    assert len(st.closed) == 1
    t = st.closed[0]
    assert t.entry == pytest.approx(105.85)
    assert t.exit_reason == "target"
    assert t.exit == pytest.approx(105.85 + 2 * (105.85 - 105.3))
    assert t.net_inr > 0 and t.gross_inr > t.net_inr  # costs were charged


def test_stop_exit_fills_at_stop_or_gap() -> None:
    st = _run(_mover_day([
        (105.85, 105.9, 105.8, 105.85, 700),   # fill 105.85
        (105.8, 105.85, 105.0, 105.1, 900),    # low 105.0 ≤ stop 105.3 → exit at stop
    ]))
    assert st.closed[0].exit_reason == "stop" and st.closed[0].exit == pytest.approx(105.3)
    st2 = _run(_mover_day([
        (105.85, 105.9, 105.8, 105.85, 700),
        (104.9, 105.0, 104.5, 104.6, 900),     # opens below the stop → fills at the open
    ]))
    assert st2.closed[0].exit_reason == "stop" and st2.closed[0].exit == pytest.approx(104.9)


def test_cost_aware_breakeven_stop_covers_modeled_costs_and_buffer() -> None:
    entry, qty = 100.0, 100
    stop = engine._cost_aware_breakeven_stop(entry, qty, extra_ticks=1)
    costs = calc_costs(entry, stop, qty, direction="long")["total"]
    assert stop > entry
    assert (stop - entry) * qty > costs


def test_cost_aware_stop_binds_only_from_the_bar_after_1r() -> None:
    """The bar that reaches 1R must not also fill the stop that its own high armed.

    Entry 105.85, stop 105.30 (1R = 106.40), cost-aware breakeven = 106.15.
    The 10:02 bar reaches 1R and dips to 106.00, below that cost stop; raising
    the trail on the same bar would invent a fill from a high that came later.
    """
    cfg = engine.EngineConfig(cost_aware_breakeven=True)
    st = _run(_mover_day([
        (105.85, 105.9, 105.8, 105.85, 700),     # 10:01 fill at 105.85
        (105.9, 106.45, 106.0, 106.4, 900),      # 10:02 hits 1R; low 106.00 < 106.15
        (106.4, 106.45, 106.0, 106.1, 900),      # 10:03 the armed stop now binds
    ]), cfg=cfg)
    trade = st.closed[0]
    assert trade.exit_reason == "trail_stop"
    assert trade.exit == pytest.approx(106.15)
    assert trade.exit_time.strftime("%H:%M") == "10:03"


def test_cost_aware_stop_is_computed_without_the_research_stress_slip() -> None:
    """bt17's 40 bps/side stress must not move the live breakeven price."""
    assert engine._cost_aware_breakeven_stop(105.85, 472, 1) == pytest.approx(106.15)


def test_pyramid_requires_the_protective_stop() -> None:
    """Adding size on the original hard stop would double the rupee risk."""
    with pytest.raises(ValueError, match="cost_aware_breakeven"):
        engine.EngineConfig(pyramid_add_at_1r=True)
    with pytest.raises(ValueError, match="pyramid_add_at_1r"):
        engine.EngineConfig(initial_risk_fraction=0.5)


def test_pyramid_adds_a_second_tranche_on_the_bar_that_lifts_the_stop() -> None:
    """Same bar as the cost stop, filled at that bar's OPEN, equal rupee risk.

    Entry 105.85, hard stop 105.30 (1R = 106.40), cost stop 106.15. The 10:02
    bar reaches 1R; the add is made at 10:03's open, risking risk_inr against
    the 106.15 stop the whole position now shares.
    """
    cfg = engine.EngineConfig(cost_aware_breakeven=True, pyramid_add_at_1r=True)
    st = _run(_mover_day([
        (105.85, 105.9, 105.8, 105.85, 700),     # 10:01 fill at 105.85
        (105.9, 106.45, 106.3, 106.4, 900),      # 10:02 reaches 1R
        (106.5, 106.6, 106.4, 106.5, 900),       # 10:03 stop binds, add at 106.50
        (106.5, 106.6, 106.0, 106.1, 900),       # 10:04 both tranches stop out
    ]), cfg=cfg)
    t = st.closed[0]
    assert t.add_time is not None and t.add_time.strftime("%H:%M") == "10:03"
    assert t.add_entry == pytest.approx(106.50)
    # equal-risk sizing, under the same notional cap the first tranche used
    assert t.add_qty == min(int(cfg.risk_inr // (106.50 - 106.15)),
                            int(cfg.max_notional_inr // 106.50))
    assert t.exit == pytest.approx(106.15)
    # the add-on lost its own risk; the first tranche kept a small gain
    assert t.add_net_inr < 0.0
    assert t.net_inr == pytest.approx(t.base_net_inr + t.add_net_inr)
    assert t.total_qty == t.qty + t.add_qty


def test_no_add_when_the_bar_opens_at_or_below_the_shared_stop() -> None:
    """A gap that opens into the stop leaves no room to risk anything."""
    cfg = engine.EngineConfig(cost_aware_breakeven=True, pyramid_add_at_1r=True)
    st = _run(_mover_day([
        (105.85, 105.9, 105.8, 105.85, 700),
        (105.9, 106.45, 106.3, 106.4, 900),      # reaches 1R
        (106.0, 106.1, 105.5, 105.6, 900),       # opens 106.00 < cost stop 106.15
    ]), cfg=cfg)
    t = st.closed[0]
    assert t.add_qty == 0 and t.add_net_inr == 0.0
    assert t.gross_pct == pytest.approx((t.exit / t.entry - 1.0) * 100.0)


def test_half_size_start_ends_at_about_one_full_position() -> None:
    """Two half tranches ~ one of today's positions, not two."""
    full = engine.EngineConfig(cost_aware_breakeven=True)
    half = engine.EngineConfig(cost_aware_breakeven=True, pyramid_add_at_1r=True,
                               initial_risk_fraction=0.5)
    day = _mover_day([
        (105.85, 105.9, 105.8, 105.85, 700),
        (105.9, 106.45, 106.3, 106.4, 900),
        (106.5, 106.6, 106.4, 106.5, 900),
        (106.5, 106.6, 106.0, 106.1, 900),
    ])
    a, b = _run(day, cfg=full).closed[0], _run(day, cfg=half).closed[0]
    assert b.qty == pytest.approx(a.qty // 2, abs=1)
    assert b.total_qty == pytest.approx(a.qty, abs=2)


def test_pyramid_off_leaves_every_closed_trade_field_untouched() -> None:
    cfg = engine.EngineConfig(cost_aware_breakeven=True)
    st = _run(_mover_day([
        (105.85, 105.9, 105.8, 105.85, 700),
        (105.9, 106.45, 106.3, 106.4, 900),
        (106.5, 106.6, 106.0, 106.1, 900),
    ]), cfg=cfg)
    t = st.closed[0]
    assert (t.add_qty, t.add_entry, t.add_time, t.add_net_inr) == (0, 0.0, None, 0.0)
    assert t.avg_entry == t.entry and t.total_qty == t.qty
    assert t.net_pct == pytest.approx(t.net_inr / (t.entry * t.qty) * 100.0)


def test_false_break_never_counts_a_close_from_before_the_fill() -> None:
    """Both confirming closes must belong to bars that closed after entry.

    The guard used to check only `index[-1]`, so on the 5-minute path (where
    `entry_floor` is floored to the bucket) a pattern made entirely of
    pre-entry closes could exit the trade.
    """
    bars = _bars([
        (101.0, 102.0, 100.5, 101.0, 1),   # breakout bar
        (101.0, 101.2, 98.5, 99.0, 1),     # first close below
        (99.0, 99.2, 97.5, 98.0, 1),       # second close below
    ])
    cfg = engine.EngineConfig()
    # filled on the last bar: the earlier close pre-dates the fill → no exit
    assert not engine._false_break_fired(cfg, bars, 100.0, bars.index[-1])
    # filled on the middle bar: both confirming closes are at/after the fill
    assert engine._false_break_fired(cfg, bars, 100.0, bars.index[-2])
    # the one-close rule only ever uses the last bar, so it is unaffected
    legacy = engine.EngineConfig(
        use_structural_exit_levels=False,
        legacy_single_close_false_break=True,
    )
    assert engine._false_break_fired(legacy, bars, 100.0, bars.index[-1])


def test_legacy_false_break_flags_are_independently_selectable() -> None:
    """Each half of the 2026-09-18 exit change can be replayed on its own."""
    bars = _bars([
        (100.0, 102.5, 99.9, 102.2, 1),
        (102.2, 102.3, 100.5, 101.0, 1),
    ])
    floor = bars.index[0]
    single = engine.EngineConfig(
        use_structural_exit_levels=False,
        legacy_single_close_false_break=True,
    )
    assert engine._false_break_fired(single, bars, 102.0, floor)
    # the default two-close rule needs a third bar, so one close is not enough
    assert not engine._false_break_fired(engine.EngineConfig(), bars, 102.0, floor)
    # ordering is a separate switch and does not change the predicate
    ordering = engine.EngineConfig(
        use_structural_exit_levels=False,
        legacy_false_break_before_stop=True,
    )
    assert not engine._false_break_fired(ordering, bars, 102.0, floor)


def test_legacy_false_break_matches_single_close_condition() -> None:
    bars = _bars([
        (100.0, 102.0, 99.5, 101.5, 1000),
        (101.5, 101.8, 100.0, 100.5, 1000),
    ])
    assert engine._legacy_false_break(bars, level=101.0)
    assert not engine._legacy_false_break(bars, level=100.0)


def test_stop_fill_precedes_confirmed_false_break() -> None:
    """A bar that confirms a false break cannot replace an intrabar stop fill."""
    st = _run(_mover_day([
        (105.85, 105.9, 105.8, 105.85, 700),   # 10:01 fill at 105.85
        (105.8, 105.85, 105.4, 105.5, 900),    # first close below level 105.6
        (105.5, 105.55, 105.0, 105.1, 900),    # second close, but low crosses stop 105.3
    ]))
    assert st.closed[0].exit_reason == "stop"
    assert st.closed[0].exit == pytest.approx(105.3)


def _structural_position(
    support: float = 100.5, hard_stop: float = 99.0,
) -> tuple[engine.DayState, engine.EngineConfig]:
    cfg = engine.EngineConfig(use_structural_exit_levels=True)
    state = engine.DayState("TEST", prev_close=100.0, cum_vol_profile=None)
    setup = engine.Setup("micro_pullback", trigger=101.0, stop=hard_stop)
    cand = engine.Candidate(
        symbol="TEST", time=pd.Timestamp("2026-09-07 10:00", tz=IST), setup=setup,
        day_chg_pct=5.0, rvol=3.0, catalyst=0, event_type="", candle_tags=[],
    )
    plan = engine.plan_trade(101.0, hard_stop, risk_inr=500.0, max_notional_inr=50_000.0)
    assert plan is not None
    exit_state = exits.initial_state(entry=101.0, hard_stop=hard_stop)
    exit_state.structural_support = exits.Level(
        support, "prev_day", touches=1, volume=0.0, strength=1.5
    )
    state.position = engine.Position(
        cand=cand, entry_time=cand.time, plan=plan, highest=101.0, exit_state=exit_state,
    )
    return state, cfg


def test_structural_support_break_exits_on_completed_one_minute_close() -> None:
    state, cfg = _structural_position()
    bars = _bars([(100.8, 100.9, 100.4, 100.45, 1_000)], start="2026-09-07 10:01")
    engine.step(state, bars, cfg, _no_cat)
    assert state.closed[0].exit_reason == "support_break"
    assert state.closed[0].exit == pytest.approx(100.45)


def test_hard_stop_precedes_structural_support_break() -> None:
    state, cfg = _structural_position()
    bars = _bars([(100.8, 100.9, 98.8, 100.4, 1_000)], start="2026-09-07 10:01")
    engine.step(state, bars, cfg, _no_cat)
    assert state.closed[0].exit_reason == "stop"
    assert state.closed[0].exit == pytest.approx(99.0)


def test_support_at_or_below_hard_stop_does_not_create_another_exit() -> None:
    state, cfg = _structural_position(support=99.0)
    bars = _bars([(100.8, 100.9, 99.2, 99.5, 1_000)], start="2026-09-07 10:01")
    engine.step(state, bars, cfg, _no_cat)
    assert state.position is not None
    assert state.closed == []


def test_structural_exit_caps_fixed_target_at_entry_known_resistance() -> None:
    cfg = engine.EngineConfig(use_structural_exit_levels=True)
    state = engine.DayState(
        "TEST",
        prev_close=100.0,
        cum_vol_profile=None,
        prev_day={"high": 102.0, "low": 99.5, "close": 100.5},
    )
    setup = engine.Setup("micro_pullback", trigger=101.0, stop=99.0)
    cand = engine.Candidate(
        symbol="TEST", time=pd.Timestamp("2026-09-07 10:00", tz=IST), setup=setup,
        day_chg_pct=5.0, rvol=3.0, catalyst=0, event_type="", candle_tags=[],
    )
    plan = engine.plan_trade(101.0, 99.0, risk_inr=500.0, max_notional_inr=50_000.0)
    assert plan is not None and plan.target == pytest.approx(105.0)
    bars = _bars([(100.0, 101.1, 99.8, 100.8, 1_000)] * 8, start="2026-09-07 09:53",
                 freq="1min")
    engine._open_position(state, cand, bars.index[-1], 101.0, plan, cfg, bars, None)
    assert state.position is not None
    assert state.position.plan.target == pytest.approx(102.0)
    assert state.position.plan.reward_inr == pytest.approx(
        (102.0 - 101.0) * state.position.plan.qty
    )
    assert state.position.target_source == "structural_resistance:prev_day"


def test_structural_exit_rejects_false_break_reclaim_combination() -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        engine.EngineConfig(use_structural_exit_levels=True, allow_false_break_reentry=True)


def test_open_position_closes_at_data_end() -> None:
    st = _run(_mover_day([(105.85, 105.9, 105.5, 105.55, 700)]))
    assert st.closed[0].exit_reason == "eod_close"


def test_no_entry_without_rvol_or_outside_band() -> None:
    day = _mover_day([(105.85, 105.9, 105.8, 105.85, 700)])
    # profile says 500/min is normal → RVOL ≈ 1 → no entry
    assert _run(day, profile=_flat_profile(500.0)).candidates == []
    # prev close 95 → up ~10.5% → outside the 4–8% band
    assert _run(day, prev_close=95.0).candidates == []


def test_entry_cutoff() -> None:
    day = _mover_day([(105.85, 105.9, 105.8, 105.85, 700)])
    late = day.copy()
    late.index = pd.date_range("2026-09-07 13:50", periods=len(late), freq="1min", tz=IST)
    assert _run(late, cfg=engine.EngineConfig(entry_cutoff=time(14, 30))).candidates == []


def test_one_trade_per_day() -> None:
    first = _mover_day([(105.85, 105.9, 105.8, 105.85, 700), (105.8, 105.85, 105.0, 105.1, 900)])
    repeat = _bars([
        (105.0, 105.3, 104.95, 105.25, 500), (105.25, 105.6, 105.2, 105.55, 500),
        (105.55, 105.6, 105.3, 105.35, 300), TRIGGER, (105.85, 105.9, 105.8, 105.85, 700),
    ], start=first.index[-1] + pd.Timedelta(minutes=1))
    st = _run(pd.concat([first, repeat]))
    assert len(st.closed) == 1 and len(st.candidates) == 1


def test_eod_close_at_1515() -> None:
    rows = _mover_day([(105.85, 105.9, 105.8, 105.85, 700)])
    end = pd.Timestamp("2026-09-07 15:20", tz=IST)
    n_extra = int((end - rows.index[-1]) / pd.Timedelta(minutes=1))
    extra = _bars([(105.85, 105.9, 105.8, 105.85, 100)] * n_extra,
                  start=rows.index[-1] + pd.Timedelta(minutes=1))
    st = _run(pd.concat([rows, extra]))
    assert st.closed[0].exit_reason == "eod_close"
    assert st.closed[0].exit_time.time() == time(15, 14)


def test_chased_fill_is_cancelled() -> None:
    st = _run(_mover_day([(107.0, 107.2, 106.9, 107.1, 700)]))  # opens 1.3% above 105.6
    assert len(st.candidates) == 1 and st.closed == [] and not st.traded_today


def test_build_cum_volume_profile() -> None:
    d1 = _bars([(1, 1, 1, 1, 100), (1, 1, 1, 1, 100)], start="2026-09-01 09:15")
    d2 = _bars([(1, 1, 1, 1, 300), (1, 1, 1, 1, 100)], start="2026-09-02 09:15")
    prof = engine.build_cum_volume_profile(pd.concat([d1, d2]))
    assert prof.loc[time(9, 15)] == pytest.approx(200.0)   # mean(100, 300)
    assert prof.loc[time(9, 16)] == pytest.approx(300.0)   # mean(200, 400)


def test_resample_5m_drops_open_bucket() -> None:
    rows = [(100 + i, 100.5 + i, 99.5 + i, 100.2 + i, 10) for i in range(7)]  # 09:15..09:21
    b5 = engine.resample_5m(_bars(rows))
    assert len(b5) == 1                                  # 09:20 bucket still open
    assert b5["open"].iloc[0] == 100 and b5["close"].iloc[0] == pytest.approx(104.2)
    assert b5["volume"].iloc[0] == 50


# ── bar builder ───────────────────────────────────────────────────────────────

def _ms(s: str) -> int:
    return int(pd.Timestamp(s, tz=IST).timestamp() * 1000)


def test_bar_builder_uses_vtt_diff_for_volume() -> None:
    bb = BarBuilder()
    bb.on_tick("K", _ms("2026-09-07 10:00:05"), 100.0, 1000)
    bb.on_tick("K", _ms("2026-09-07 10:00:40"), 100.5, 1300)
    bb.on_tick("K", _ms("2026-09-07 10:01:02"), 100.2, 1450)
    bars = bb.closed_bars("K", pd.Timestamp("2026-09-07 10:01:30", tz=IST))
    assert len(bars) == 1
    b = bars.iloc[0]
    assert (b["open"], b["high"], b["low"], b["close"]) == (100.0, 100.5, 100.0, 100.5)
    assert b["volume"] == 300      # 1300 − 1000; the 10:01 tick belongs to the next bar
    later = bb.closed_bars("K", pd.Timestamp("2026-09-07 10:02:30", tz=IST))
    assert later["volume"].iloc[-1] == 150


def test_bar_builder_seed_then_live() -> None:
    bb = BarBuilder()
    seed = _bars([(100, 101, 99, 100.5, 400), (100.5, 101.5, 100, 101, 600)],
                 start="2026-09-07 09:15")
    bb.seed("K", seed)
    bb.on_tick("K", _ms("2026-09-07 09:17:10"), 101.2, 1100)   # vtt continues from 1000
    bars = bb.closed_bars("K", pd.Timestamp("2026-09-07 09:18:00", tz=IST))
    assert len(bars) == 3 and bars["volume"].tolist() == [400, 600, 100]


# ── upstox parsing ────────────────────────────────────────────────────────────

def test_candles_to_frame_sorts_and_localises() -> None:
    raw = [
        ["2026-09-05T09:16:00+05:30", 101, 102, 100, 101.5, 500, 0],
        ["2026-09-05T09:15:00+05:30", 100, 101, 99, 100.5, 400, 0],
    ]
    df = candles_to_frame(raw)
    assert list(df.index.strftime("%H:%M")) == ["09:15", "09:16"]
    assert str(df.index.tz) == IST
    assert df["volume"].tolist() == [400.0, 500.0]
    assert candles_to_frame([]).empty


# ── universe ──────────────────────────────────────────────────────────────────

def test_universe_static_filters() -> None:
    nf = universe.NameFacts
    assert universe.passes_static(nf("A", 1200.0, 62.0, 10.0, "EQ", "")) == (True, "ok")
    assert universe.passes_static(None) == (True, "no_facts")
    assert not universe.passes_static(nf("B", 1200.0, 62.0, 5.0, "EQ", ""))[0]     # 5% band
    assert not universe.passes_static(nf("C", 1200.0, 30.0, 10.0, "EQ", ""))[0]    # promoter
    assert not universe.passes_static(nf("D", 9000.0, 62.0, 10.0, "EQ", ""))[0]    # too big
    assert not universe.passes_static(nf("E", 1200.0, 62.0, 10.0, "EQ", "ASM"))[0]  # surveillance
    assert not universe.passes_static(nf("F", 1200.0, 62.0, 10.0, "BE", ""))[0]    # T2T


def test_universe_dynamic_filters() -> None:
    assert universe.passes_dynamic(150.0, 10.0) == (True, "ok")
    assert universe.passes_dynamic(30.0, 10.0)[1] == "price"
    assert universe.passes_dynamic(150.0, 200.0)[1] == "turnover"
    assert universe.passes_dynamic(150.0, None) == (True, "ok")


# ── catalyst ──────────────────────────────────────────────────────────────────

class _FakeSignals:
    """Minimal pymongo-like collection: naive-UTC created_at, as pymongo returns."""

    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs

    def find_one(self, filter: dict, sort=None, projection=None):  # noqa: ANN001
        lo, hi = filter["created_at"]["$gte"], filter["created_at"]["$lte"]
        hits = [d for d in self.docs
                if ("stocks" not in filter or filter["stocks"] in d["stocks"])
                and ("event_type" not in filter or d["event_type"] in filter["event_type"]["$in"])
                and lo <= d["created_at"] <= hi]
        hits.sort(key=lambda d: d["created_at"], reverse=True)
        return hits[0] if hits else None


def test_hard_catalyst_window_and_taxonomy() -> None:
    at = pd.Timestamp("2026-09-07 10:00", tz=IST)
    now_utc = at.tz_convert("UTC").tz_localize(None).to_pydatetime()
    h = pd.Timedelta(hours=1)
    docs = [
        {"stocks": ["ABC"], "event_type": "order_win", "created_at": now_utc - 3 * h},
        {"stocks": ["XYZ"], "event_type": "generic_pr", "created_at": now_utc - 1 * h},
        {"stocks": ["OLD"], "event_type": "earnings", "created_at": now_utc - 30 * h},
    ]
    sig = _FakeSignals(docs)
    assert catalyst.hard_catalyst(sig, "ABC", at) == (1, "order_win")
    assert catalyst.hard_catalyst(sig, "XYZ", at) == (0, "")     # Group B does not count
    assert catalyst.hard_catalyst(sig, "OLD", at) == (0, "")     # outside 24h
    assert catalyst.hard_catalyst(sig, "NONE", at) == (0, "")


def test_dated_event_lookup_counts_d_and_d_plus_1() -> None:
    ev = pd.DataFrame({"symbol": ["ABC"], "date": ["2026-09-07"], "event_type": ["earnings"]})
    lk = catalyst.DatedEventLookup(ev)
    assert lk("ABC", pd.Timestamp("2026-09-07 10:00", tz=IST)) == (1, "earnings")
    assert lk("ABC", pd.Timestamp("2026-09-08 10:00", tz=IST)) == (1, "earnings")
    assert lk("ABC", pd.Timestamp("2026-09-09 10:00", tz=IST)) == (0, "")


# ── breakeven_at_r override (hypothesis 2026-09-20-breakeven-at-1p5r) ─────────

def test_breakeven_at_r_defaults_to_the_frozen_one_r() -> None:
    """Every arm ever run must replay unchanged when the flag is untouched."""
    for mode in ("trend_min", "trend_full", "trend_resistance_state"):
        cfg = engine.EngineConfig(exit_mode=mode)
        assert cfg.exit_cfg.breakeven_at_r == exits.BREAKEVEN_AT_R == 1.0
        assert cfg.exit_cfg.arm_at_r == exits.ARM_AT_R == 0.5


def test_breakeven_at_r_reaches_the_exit_config() -> None:
    """`exit_cfg` is built in __post_init__, so a value that never reaches the
    constructor is a silent no-op. This is the test for that trap."""
    cfg = engine.EngineConfig(exit_mode="trend_resistance_state", breakeven_at_r=1.5)
    assert cfg.exit_cfg.breakeven_at_r == 1.5
    # the arming threshold is a SEPARATE frozen number and must not move with it
    assert cfg.exit_cfg.arm_at_r == 0.5


def test_breakeven_at_r_is_rejected_where_it_has_no_meaning() -> None:
    with pytest.raises(ValueError, match="fixed_2r"):
        engine.EngineConfig(exit_mode="fixed_2r", breakeven_at_r=1.5)
    with pytest.raises(ValueError, match="breakeven_at_r must be positive"):
        engine.EngineConfig(exit_mode="trend_full", breakeven_at_r=0.0)
    with pytest.raises(ValueError, match="cost_aware_breakeven"):
        engine.EngineConfig(exit_mode="trend_full", breakeven_at_r=1.5,
                            cost_aware_breakeven=True)
    # fixed_2r keeps its deliberate "no breakeven lock at all"
    assert engine.EngineConfig(exit_mode="fixed_2r").exit_cfg.breakeven_at_r == float("inf")


def test_stop_lifts_to_entry_only_after_the_configured_r() -> None:
    """Entry 100, hard stop 98 → 1R = 102, 1.5R = 103.

    At a 102.50 high the default arm is already protected at entry and the
    1.5R arm is still on its original stop. That difference IS the experiment.
    """
    for r, protected_at_102_5 in ((None, True), (1.5, False)):
        cfg = engine.EngineConfig(exit_mode="trend_full", breakeven_at_r=r)
        st = exits.initial_state(entry=100.0, hard_stop=98.0)
        exits.update_high(st, 102.5, cfg.exit_cfg)
        exits.apply_pending_breakeven(st, cfg.exit_cfg)   # i.e. the following bar
        assert st.armed is True                      # arming is unchanged at 0.5R
        assert (st.stop == 100.0) is protected_at_102_5
        assert st.stop == (100.0 if protected_at_102_5 else 98.0)
        # push through 1.5R and the 1.5R arm protects too — later, not never
        exits.update_high(st, 103.0, cfg.exit_cfg)
        exits.apply_pending_breakeven(st, cfg.exit_cfg)
        assert st.stop == 100.0


def test_deferred_breakeven_cannot_fill_on_the_bar_that_armed_it() -> None:
    """Entry 100, hard stop 98 → 1R = 102. One bar runs 101 → 102.5 → 99.5.

    Same-bar (the legacy default): the high arms the lift to 100 and the low
    fills it, booking an exit at the entry price from an order that could not
    have existed inside the candle. Deferred: that bar cannot stop out at all;
    protection binds on the NEXT bar.
    """
    bar = pd.Series({"open": 101.0, "high": 102.5, "low": 99.5, "close": 100.2})

    legacy = engine.EngineConfig(exit_mode="trend_full",
                                 legacy_same_bar_breakeven=True).exit_cfg
    st = exits.initial_state(entry=100.0, hard_stop=98.0)
    exits.apply_pending_breakeven(st, legacy)          # no-op when not deferring
    exits.update_high(st, float(bar["high"]), legacy)
    assert st.stop == 100.0
    assert exits.check_stop(st, bar) is not None       # the artifact

    fixed = engine.EngineConfig(exit_mode="trend_full").exit_cfg  # the DEFAULT
    st = exits.initial_state(entry=100.0, hard_stop=98.0)
    exits.apply_pending_breakeven(st, fixed)
    exits.update_high(st, float(bar["high"]), fixed)
    assert st.breakeven_pending is True
    assert st.stop == 98.0                             # still the hard stop
    assert exits.check_stop(st, bar) is None           # 99.5 > 98 → survives
    # next bar: the lift binds before that bar's low is tested
    exits.apply_pending_breakeven(st, fixed)
    assert st.stop == 100.0 and st.breakeven_pending is False
    assert exits.check_stop(st, pd.Series(
        {"open": 100.4, "high": 100.6, "low": 99.8, "close": 99.9})) is not None


def test_the_breakeven_lift_is_deferred_by_default_everywhere() -> None:
    """The fix ships ON. Live builds EngineConfig directly (scanner._strategy_config),
    so no deployment branch can silently miss it."""
    for mode in ("trend_min", "trend_full", "trend_resistance_state"):
        assert engine.EngineConfig(exit_mode=mode).exit_cfg.legacy_same_bar_breakeven is False
    from src.momentum_trader.scanner import _strategy_config, Settings  # noqa: PLC0415
    for name in ("attention_1m_merged", "attention_1m", "baseline"):
        cfg = _strategy_config(Settings(mt_strategy=name))
        assert cfg.exit_cfg.legacy_same_bar_breakeven is False, name


def test_no_trailing_stops_leaves_only_the_entry_stop() -> None:
    """Entry 100, hard stop 98. However far price runs, the stop stays at 98."""
    cfg = engine.EngineConfig(exit_mode="trend_resistance_state",
                              no_trailing_stops=True).exit_cfg
    assert cfg.use_swing_trail is False
    assert cfg.breakeven_at_r == float("inf")
    assert cfg.use_macd_fade is True          # trend exits untouched
    st = exits.initial_state(entry=100.0, hard_stop=98.0)
    for high in (101.0, 102.0, 105.0, 110.0):
        exits.update_high(st, high, cfg)
        exits.apply_pending_breakeven(st, cfg)
    assert st.stop == 98.0 and st.trail == 98.0
    # Arming at 0.5R is a SEPARATE frozen rule and must still fire — the trend
    # exits depend on it, and this option removes moving stops, not arming.
    assert st.armed is True
    assert st.highest == 110.0


def test_no_trend_exits_removes_all_five_indicator_exits() -> None:
    cfg = engine.EngineConfig(exit_mode="trend_resistance_state",
                              no_trailing_stops=True, no_trend_exits=True).exit_cfg
    for f in ("use_ema_fast_break", "use_ema_slow_break", "use_macd_fade",
              "use_resistance_reject", "use_volume_climax"):
        assert getattr(cfg, f) is False, f


def test_no_trailing_stops_refuses_meaningless_combinations() -> None:
    with pytest.raises(ValueError, match="breakeven_at_r is meaningless"):
        engine.EngineConfig(no_trailing_stops=True, breakeven_at_r=1.5)
    with pytest.raises(ValueError, match="cost_aware_breakeven is a trailing stop"):
        engine.EngineConfig(no_trailing_stops=True, cost_aware_breakeven=True)
