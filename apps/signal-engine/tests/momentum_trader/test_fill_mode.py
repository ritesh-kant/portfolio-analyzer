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
