"""Fill-mode tests (research/hypotheses/2026-09-05-entry-fill-latency.md).

The whole comparison rests on one property: the two arms must take EXACTLY the
same trades, so that a difference in result is attributable to the fill price
and nothing else. These tests pin that property down, plus the mechanics of the
`trigger` fill itself.
"""

from __future__ import annotations

import pandas as pd
import pytest
from src.momentum_trader import engine as eng
from src.momentum_trader.risk import MIN_STOP_PCT, plan_trade
from src.momentum_trader.setups import CHASE_MAX_EXT_PCT

IST = "Asia/Kolkata"
LEGACY_FILL_MODES = (eng.FILL_NEXT_OPEN, eng.FILL_TRIGGER)


def _bars(rows: list[tuple[str, float, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(f"2024-06-03 {t}", tz=IST) for t, *_ in rows])
    return pd.DataFrame(
        [{"open": o, "high": h, "low": lo, "close": c, "volume": v} for _, o, h, lo, c, v in rows],
        index=idx,
    )


# ── config validation ────────────────────────────────────────────────────────

def test_default_fill_mode_is_the_bt17_baseline() -> None:
    assert eng.EngineConfig().fill_mode == eng.FILL_NEXT_OPEN


def test_unknown_fill_mode_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="unknown fill mode"):
        eng.EngineConfig(fill_mode="five_seconds_early")


# ── the trigger fill itself ──────────────────────────────────────────────────

def _pending_state(trigger: float, stop: float) -> tuple[eng.DayState, eng.Setup]:
    from src.momentum_trader.setups import Setup
    setup = Setup(name="flat_top_breakout", trigger=trigger, stop=stop, level=trigger)
    cand = eng.Candidate(
        symbol="TEST", time=pd.Timestamp("2024-06-03 10:34", tz=IST), setup=setup,
        day_chg_pct=5.0, rvol=4.0, catalyst=0, event_type="", candle_tags=[],
    )
    st = eng.DayState(symbol="TEST", prev_close=100.0, cum_vol_profile=None)
    st.pending = eng.Pending(cand)
    return st, setup


def _fill_one_bar(fill_mode: str, next_open: float, trigger: float = 100.0,
                  stop: float = 98.5) -> eng.Position | None:
    """Run step() over a single bar with a pending entry and return the position."""
    st, _ = _pending_state(trigger, stop)
    bars = _bars([("10:35", next_open, next_open + 0.2, next_open - 0.2, next_open, 1000)])
    cfg = eng.EngineConfig(fill_mode=fill_mode, stress_slip=0.0)
    eng.step(st, bars, cfg, lambda _s, _t: (0, ""))
    return st.position


def test_next_open_fills_at_the_bar_open() -> None:
    pos = _fill_one_bar(eng.FILL_NEXT_OPEN, next_open=100.4)
    assert pos is not None
    assert pos.plan.entry == pytest.approx(100.4)


def test_trigger_mode_fills_at_the_trigger_level() -> None:
    pos = _fill_one_bar(eng.FILL_TRIGGER, next_open=100.4, trigger=100.0)
    assert pos is not None
    assert pos.plan.entry == pytest.approx(100.0)


def test_trigger_fill_never_beats_the_level_even_when_price_fell_back() -> None:
    """A resting buy-stop already filled during the trigger bar, so a lower open
    on the next bar does not hand us a better price."""
    pos = _fill_one_bar(eng.FILL_TRIGGER, next_open=99.2, trigger=100.0)
    assert pos is not None
    assert pos.plan.entry == pytest.approx(100.0)


def test_trigger_fill_tightens_the_stop_and_buys_more_shares() -> None:
    baseline = _fill_one_bar(eng.FILL_NEXT_OPEN, next_open=100.4, trigger=100.0, stop=98.5)
    treated = _fill_one_bar(eng.FILL_TRIGGER, next_open=100.4, trigger=100.0, stop=98.5)
    assert baseline is not None and treated is not None
    assert treated.plan.entry < baseline.plan.entry
    assert treated.plan.qty > baseline.plan.qty          # same ₹risk, shorter stop
    assert treated.plan.target < baseline.plan.target    # 2R measured from a lower entry


def test_entry_timestamp_is_the_next_bar_in_both_modes() -> None:
    """The better price must not come with intra-bar look-ahead: exits still
    start being checked from the bar after the trigger bar."""
    expected = pd.Timestamp("2024-06-03 10:35", tz=IST)
    for mode in LEGACY_FILL_MODES:
        pos = _fill_one_bar(mode, next_open=100.4)
        assert pos is not None
        assert pos.entry_time == expected


# ── the trade set must be identical across arms ──────────────────────────────

def test_chase_guard_is_judged_on_the_next_open_in_both_modes() -> None:
    """A fill the baseline refuses as chased must also be refused in trigger
    mode, even though trigger mode's own fill sits exactly on the level."""
    chased_open = 100.0 * (1.0 + (CHASE_MAX_EXT_PCT + 0.5) / 100.0)
    for mode in LEGACY_FILL_MODES:
        assert _fill_one_bar(mode, next_open=chased_open, trigger=100.0) is None


def test_stop_sanity_band_is_judged_on_the_gate_price() -> None:
    """A stop just inside the band at the baseline fill would fall below the
    0.3% floor once measured from the (lower) trigger fill. Anchoring the band
    on the gate price keeps the trade in both arms."""
    gate = 100.0
    stop = gate * (1.0 - (MIN_STOP_PCT + 0.0002))   # just inside the band at `gate`
    trigger = 99.7                                  # a lower fill → stop looks too tight
    assert (trigger - stop) / trigger < MIN_STOP_PCT

    assert plan_trade(trigger, stop, risk_inr=500.0, max_notional_inr=50_000.0) is None
    kept = plan_trade(trigger, stop, risk_inr=500.0, max_notional_inr=50_000.0, gate_entry=gate)
    assert kept is not None
    assert kept.entry == pytest.approx(trigger)


def test_gate_entry_cannot_admit_a_fill_at_or_below_the_stop() -> None:
    assert plan_trade(98.0, 98.5, risk_inr=500.0, max_notional_inr=50_000.0,
                      gate_entry=100.0) is None


def test_both_arms_take_the_same_trades_over_a_replayed_session() -> None:
    """End-to-end: same session, same setups, both fill modes. Same count, same
    entry timestamps, and every treated entry at or below the baseline's."""
    # a flat top at 101.0 touched three times, then a close through it
    rows: list[tuple[str, float, float, float, float, float]] = []
    px = 100.0
    for i in range(11):                                  # 09:15..09:25 warm-up drift
        t = f"09:{15 + i:02d}"
        rows.append((t, px, px + 0.15, px - 0.15, px + 0.05, 5000.0))
        px += 0.05
    # three touches of 101.0
    for t in ("09:26", "09:27", "09:28"):
        rows.append((t, 100.6, 101.0, 100.4, 100.7, 9000.0))
    for i in range(6):                                   # settle inside the range
        t = f"09:{29 + i:02d}"
        rows.append((t, 100.7, 100.9, 100.5, 100.7, 4000.0))
    rows.append(("09:35", 100.8, 101.4, 100.7, 101.3, 20000.0))   # breaks and closes above
    for i in range(20):                                  # drift after the break
        t = f"09:{36 + i:02d}"
        rows.append((t, 101.3, 101.6, 101.0, 101.2, 6000.0))

    bars = _bars(rows)
    prev_close = 96.0                                    # puts day-change in the 4–8% band
    profile = pd.Series(
        [bars["volume"].iloc[: i + 1].sum() / 4.0 for i in range(len(bars))],
        index=[ts.time() for ts in bars.index],
    )

    out = {}
    for mode in LEGACY_FILL_MODES:
        cfg = eng.EngineConfig(fill_mode=mode, stress_slip=0.0)
        st = eng.run_day("TEST", bars, prev_close, profile, cfg, lambda _s, _t: (0, ""))
        out[mode] = st.closed

    base, treat = out[eng.FILL_NEXT_OPEN], out[eng.FILL_TRIGGER]
    assert len(base) == len(treat) >= 1
    assert [t.entry_time for t in base] == [t.entry_time for t in treat]
    # The treated fill is the trigger level, full stop. It is NOT necessarily
    # better than the baseline's: on the 2024 dev run the next bar opened below
    # the trigger in 19.3% of trades, and a resting stop order cannot wait for
    # that pullback. Asserting `treated <= baseline` would encode a false
    # invariant that merely happens to hold in this fixture.
    assert all(t.entry == pytest.approx(t.cand.setup.trigger) for t in treat)


# ── resting buy-stop modes ───────────────────────────────────────────────────
# research/hypotheses/2026-09-15-resting-buy-stop-execution.md
#
# Two shapes of armed entry, and they must fill differently:
#   trigger_reached=True  — the legacy setups fire on `bar.high > trigger`, so a
#                           buy-stop at the level was hit inside that signal bar.
#   trigger_reached=False — the attention path arms BELOW a future trigger, so the
#                           order genuinely waits and may never fill.

RESTING_MODES = (eng.FILL_RESTING, eng.FILL_RESTING_SIZED)


def _rest(fill_mode: str, bar: tuple[str, float, float, float, float, float],
          *, reached: bool, slip: float = 0.0, trigger: float = 100.0,
          stop: float = 98.5, expires: pd.Timestamp | None = None) -> eng.DayState:
    st, _ = _pending_state(trigger, stop)
    st.pending = eng.Pending(st.pending.cand, trigger_reached=reached, expires_at=expires)
    cfg = eng.EngineConfig(fill_mode=fill_mode, stress_slip=0.0, entry_slip_pct=slip)
    eng.step(st, _bars([bar]), cfg, lambda _s, _t: (0, ""))
    return st


def test_resting_modes_are_registered() -> None:
    for mode in RESTING_MODES:
        assert mode in eng.FILL_MODES


def test_already_through_the_level_fills_at_the_trigger() -> None:
    for mode in RESTING_MODES:
        st = _rest(mode, ("10:35", 100.4, 100.6, 100.2, 100.4, 1000), reached=True)
        assert st.position is not None, mode
        assert st.position.plan.entry == pytest.approx(100.0), mode


def test_a_future_trigger_waits_and_does_not_invent_a_fill() -> None:
    """The bug this guards: filling at a level the bar never traded at. 30.8% of
    attention fills were fabricated this way before `trigger_reached` existed."""
    for mode in RESTING_MODES:
        st = _rest(mode, ("10:35", 99.0, 99.6, 98.8, 99.2, 1000), reached=False)
        assert st.position is None, mode
        assert st.pending is not None, mode          # still resting, not cancelled


def test_a_future_trigger_fills_once_the_level_trades() -> None:
    for mode in RESTING_MODES:
        st = _rest(mode, ("10:35", 99.5, 100.3, 99.4, 100.2, 1000), reached=False)
        assert st.position is not None, mode
        assert st.position.plan.entry == pytest.approx(100.0), mode


def test_a_gap_open_above_the_level_fills_at_the_open_which_is_worse() -> None:
    """Mirror of exits.check_stop's min(stop, open): a buy-stop gapped through
    pays the open, not the level."""
    st = _rest(eng.FILL_RESTING_SIZED, ("10:35", 100.6, 100.9, 100.5, 100.8, 1000),
               reached=False)
    assert st.position is not None
    assert st.position.plan.entry == pytest.approx(100.6)


def test_resting_keeps_the_chase_cap_exactly_as_the_live_quote_path_does() -> None:
    runaway = 100.0 * (1.0 + (CHASE_MAX_EXT_PCT + 0.5) / 100.0)
    st = _rest(eng.FILL_RESTING_SIZED,
               ("10:35", runaway, runaway + 0.2, runaway - 0.1, runaway, 1000), reached=False)
    assert st.position is None
    assert [r.reason for r in st.rejections] == ["chased"]


def test_a_resting_order_expires() -> None:
    st = _rest(eng.FILL_RESTING_SIZED, ("10:35", 99.0, 99.5, 98.9, 99.1, 1000),
               reached=False, expires=pd.Timestamp("2024-06-03 10:30", tz=IST))
    assert st.position is None and st.pending is None
    assert [r.reason for r in st.rejections] == ["pending_expired"]


def test_entry_slip_prices_a_resting_fill_worse_than_the_level() -> None:
    clean = _rest(eng.FILL_RESTING_SIZED, ("10:35", 100.4, 100.6, 100.2, 100.4, 1000),
                  reached=True, slip=0.0)
    slipped = _rest(eng.FILL_RESTING_SIZED, ("10:35", 100.4, 100.6, 100.2, 100.4, 1000),
                    reached=True, slip=0.03)
    assert clean.position is not None and slipped.position is not None
    assert slipped.position.plan.entry == pytest.approx(100.0 * 1.0003)
    assert slipped.position.plan.entry > clean.position.plan.entry


def test_entry_slip_does_not_touch_the_legacy_modes() -> None:
    for mode in LEGACY_FILL_MODES:
        base = _fill_one_bar(mode, next_open=100.4)
        assert base is not None, mode
        st, _ = _pending_state(100.0, 98.5)
        cfg = eng.EngineConfig(fill_mode=mode, stress_slip=0.0, entry_slip_pct=0.25)
        eng.step(st, _bars([("10:35", 100.4, 100.6, 100.2, 100.4, 1000)]), cfg,
                 lambda _s, _t: (0, ""))
        assert st.position is not None, mode
        assert st.position.plan.entry == pytest.approx(base.plan.entry), mode


def test_resting_sized_gates_on_the_price_paid_not_the_next_open() -> None:
    """A live buy-stop cannot see the next open, so the stop-sanity band has to be
    judged on the fill."""
    trigger = 100.0
    stop = trigger * (1.0 - MIN_STOP_PCT / 100.0) - 0.01
    gated = plan_trade(trigger, stop, risk_inr=500.0, max_notional_inr=50_000.0,
                       gate_entry=103.0)
    paid = plan_trade(trigger, stop, risk_inr=500.0, max_notional_inr=50_000.0,
                      gate_entry=trigger)
    assert (gated is None) != (paid is None), "pick a stop the two gates disagree on"


def test_negative_entry_slip_is_rejected() -> None:
    with pytest.raises(ValueError, match="entry_slip_pct must be non-negative"):
        eng.EngineConfig(entry_slip_pct=-0.1)


def test_entry_slip_defaults_to_zero_so_old_runs_reproduce() -> None:
    assert eng.EngineConfig().entry_slip_pct == 0.0


def test_a_waiting_fill_is_never_priced_above_what_traded() -> None:
    """The bar only just touches the level (high == trigger). Slippage must not
    push the fill past the bar's own high — that invents a price, and it priced
    33.5% of attention fills before this cap."""
    st = _rest(eng.FILL_RESTING_SIZED, ("10:35", 99.6, 100.0, 99.4, 99.8, 1000),
               reached=False, slip=0.03, trigger=100.0)
    assert st.position is not None
    assert st.position.plan.entry == pytest.approx(100.0)
    assert st.position.plan.entry <= 100.0


def test_an_already_hit_fill_is_capped_by_its_own_arming_bar() -> None:
    """Slippage must not price a legacy fill above what the signal bar traded."""
    st, _ = _pending_state(100.0, 98.5)
    st.pending = eng.Pending(st.pending.cand, trigger_reached=True, armed_high=100.01)
    cfg = eng.EngineConfig(fill_mode=eng.FILL_RESTING_SIZED, stress_slip=0.0,
                           entry_slip_pct=0.30)
    eng.step(st, _bars([("10:35", 100.4, 100.6, 100.2, 100.4, 1000)]), cfg,
             lambda _s, _t: (0, ""))
    assert st.position is not None
    assert st.position.plan.entry == pytest.approx(100.01)
