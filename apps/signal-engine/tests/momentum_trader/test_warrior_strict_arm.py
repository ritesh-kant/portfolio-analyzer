"""End-to-end: the `warrior_strict` arm on a session built to the guide's shape.

The checklist gates are unit-tested one at a time in `test_warrior_checklist`.
What this file protects is the thing unit tests cannot see — that the eleven
rules, switched on TOGETHER, still describe a tradeable setup. A checklist that
refuses everything would pass every unit test in the suite and take zero trades
for the rest of the strategy's life, so the assertion that matters most here is
simply that a trade happens, inside the morning window, at the guide's price.
"""

from __future__ import annotations

from datetime import time

import pandas as pd
import pytest
from src.momentum_trader.catalyst import no_catalyst
from src.momentum_trader.engine import (
    DayState,
    EngineConfig,
    build_cum_volume_profile,
    resample_5m,
    run_day,
    step,
)
from src.momentum_trader.scanner import STRATEGY_WARRIOR_STRICT, _strategy_config

IST = "Asia/Kolkata"
COLS = ["open", "high", "low", "close", "volume"]


def _prior_sessions() -> pd.DataFrame:
    """Five quiet prior sessions — enough to warm EMA9/20, MACD and the 200."""
    frames = []
    for j, day in enumerate(
        ["2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-14"]
    ):
        idx = pd.date_range(pd.Timestamp(f"{day} 09:15", tz=IST), periods=375, freq="1min")
        rows, p = [], 100.0 + j * 0.5
        for _ in range(375):
            o, c = p, p + 0.001
            rows.append((o, max(o, c) + 0.05, min(o, c) - 0.05, c, 1_000.0))
            p = c
        frames.append(pd.DataFrame(rows, columns=COLS, index=idx))
    return pd.concat(frames)


def _guide_shaped_day(prev_close: float, minutes: int = 120) -> pd.DataFrame:
    """Gap up, quiet drift, a squeeze, a light-volume pullback, then the break.

    This is the transcript's own sequence: "allow a stock meeting all 5 pillars
    to surge higher on strong volume ... allow the stock to pull back for 1 to 2
    candles ... buy at the exact moment the first candle makes a new high above
    the high of the previous candle."
    """
    idx = pd.date_range(pd.Timestamp("2026-09-15 09:15", tz=IST), periods=minutes, freq="1min")
    rows, p = [], prev_close * 1.02          # opens +2%, above the 1.5% floor
    for i in range(minutes):
        if i < 26:                            # quiet opening drift
            o, c, v = p, p + 0.02, 1_500.0
        elif i < 38:                          # the squeeze, on volume
            o, c, v = p, p + 0.25, 4_000.0
        elif i in (38, 39):                   # the pullback: red, and LIGHT
            o, c, v = p, p - 0.05, 400.0
        elif i == 40:                         # the break, on heavy volume
            o, c, v = p, p + 0.50, 14_000.0
        else:
            o, c, v = p, p + 0.03, 2_000.0
        rows.append((o, max(o, c) + 0.03, min(o, c) - 0.03, c, v))
        p = c
    return pd.DataFrame(rows, columns=COLS, index=idx)


def _strict_cfg(**overrides) -> EngineConfig:
    from src.config import Settings

    cfg = _strategy_config(Settings(mt_strategy=STRATEGY_WARRIOR_STRICT))
    for k, v in overrides.items():
        setattr(cfg, k, v)
    if overrides:
        cfg.__post_init__()
    return cfg


def _replay(cfg: EngineConfig, day: pd.DataFrame | None = None) -> DayState:
    hist = _prior_sessions()
    prev_close = float(hist["close"].iloc[-1])
    today = _guide_shaped_day(prev_close) if day is None else day
    return run_day(
        "TEST", today, prev_close, build_cum_volume_profile(hist, 20), cfg, no_catalyst,
        warmup_1m=hist,
        prev_day={"high": float(hist["high"].iloc[-375:].max()),
                  "low": float(hist["low"].iloc[-375:].min()), "close": prev_close},
    )


# ── the arm trades ───────────────────────────────────────────────────────────

def test_the_full_checklist_still_takes_the_guides_own_setup():
    st = _replay(_strict_cfg())
    assert st.candidates, (
        "every guide rule on at once refused a textbook setup — the arm would "
        f"never trade. Rejections: {[r.reason for r in st.rejections][:10]}"
    )
    cand = st.candidates[0]
    assert cand.pullback_ord in (1, 2)
    assert cand.time.time() < cfg_deadline(), "entry must land inside peak hours"
    assert st.closed, "the armed buy-stop never filled"


def cfg_deadline():
    return _strict_cfg().entry_deadline


def test_promotion_happens_in_the_morning_not_at_the_end_of_the_window():
    """The warm-up fix, stated as the behaviour it exists to produce.

    Cold, the 5-minute EMAs need 20 completed bars of TODAY, so the earliest
    possible promotion is the bar starting 10:54 — measured on the live log,
    the first attention event on 2026-09-10 and 2026-09-11 was 10:44 and
    nothing whatsoever happened before it. Warm, the same session promotes
    inside the first twenty minutes.
    """
    warm = _replay(_strict_cfg())
    assert warm.attention_events
    first_warm = warm.attention_events[0].time
    assert first_warm.hour == 9, f"warm promotion at {first_warm.time()}, expected 09:xx"

    cold = _replay(_strict_cfg(warm_context=False))
    first_cold = cold.attention_events[0].time if cold.attention_events else None
    assert first_cold is None or first_cold.time() >= pd.Timestamp("10:54").time()
    # And by then the setup is long over, so the cold arm takes no trade at all
    # while the warm one does.
    assert not cold.closed
    assert warm.closed


def test_entry_is_the_pullback_high_and_the_stop_is_the_pullback_low():
    st = _replay(_strict_cfg())
    setup = st.candidates[0].setup
    day = _guide_shaped_day(float(_prior_sessions()["close"].iloc[-1]))
    pullback = day.iloc[38:40]
    assert setup.trigger == pytest.approx(float(pullback["high"].max()), abs=0.01)
    assert setup.stop == pytest.approx(float(pullback["low"].min()), abs=0.01)


def test_the_two_r_target_is_live_in_the_trend_mode():
    st = _replay(_strict_cfg())
    assert st.closed[0].exit_reason == "target", (
        "the trend mode had no target before `use_fixed_target`; this trade "
        f"exited via {st.closed[0].exit_reason}"
    )
    trade = st.closed[0]
    plan_risk = trade.entry - trade.cand.setup.stop
    assert trade.exit - trade.entry == pytest.approx(2.0 * plan_risk, rel=0.02)


# ── the arm refuses ──────────────────────────────────────────────────────────

def test_the_same_setup_outside_the_peak_window_is_refused():
    """Identical bars, identical everything — only the deadline moves.

    Moving the deadline rather than the day isolates the clock: shifting the
    session instead would also change the time-of-day RVOL the setup is
    compared against, and the refusal could no longer be attributed.
    """
    traded = _replay(_strict_cfg())
    assert traded.candidates, "control arm must trade for this test to mean anything"
    entered_at = traded.candidates[0].time.time()

    closed_early = _strict_cfg(peak_hours_end=time(9, 45))
    assert closed_early.entry_deadline < entered_at
    st = _replay(closed_early)
    assert not st.candidates, "the entry is past the deadline and must be refused"
    assert not st.closed


def test_a_squeeze_with_no_pullback_is_refused():
    hist = _prior_sessions()
    prev_close = float(hist["close"].iloc[-1])
    day = _guide_shaped_day(prev_close)
    # Replace the two pullback bars with more green — the "10 to 15 green
    # candles in a row" the transcript says never to chase.
    for i in (38, 39):
        o = float(day["open"].iloc[i])
        c = o + 0.25
        day.iloc[i] = [o, c + 0.03, o - 0.03, c, 4_000.0]
    st = _replay(_strict_cfg(), day=day)
    assert not st.closed
    assert any(r.reason == "attention_no_micro_pullback" for r in st.rejections)


def test_a_heavy_volume_pullback_is_refused():
    hist = _prior_sessions()
    prev_close = float(hist["close"].iloc[-1])
    day = _guide_shaped_day(prev_close)
    for i in (38, 39):                      # same shape, sellers in size
        row = day.iloc[i].tolist()
        row[4] = 9_000.0
        day.iloc[i] = row
    st = _replay(_strict_cfg(), day=day)
    assert not st.closed
    assert any(r.reason == "attention_heavy_pullback_volume" for r in st.rejections)


# ── the merged arm is untouched ──────────────────────────────────────────────

def test_the_previous_arm_is_byte_for_byte_unaffected():
    """Every switch added on 2026-09-15 is off for `attention_1m_merged`."""
    from src.config import Settings

    merged = _strategy_config(Settings(mt_strategy="attention_1m_merged"))
    assert not merged.require_micro_pullback
    assert not merged.require_light_pullback_volume
    assert not merged.require_macd_positive_open
    assert not merged.peak_hours_only
    assert not merged.warm_context
    assert not merged.use_fixed_target
    assert merged.vol_baseline_min_bars is None
    assert merged.allowed_pullback_ordinals == ()
    assert merged.entry_deadline == EngineConfig().entry_cutoff


def test_warmup_bars_alone_do_not_change_a_merged_replay():
    """The scanner now holds warm-up bars for EVERY arm. They must only reach
    the attention context when `warm_context` asks for them."""
    from src.config import Settings

    cfg = _strategy_config(Settings(mt_strategy="attention_1m_merged"))
    hist = _prior_sessions()
    prev_close = float(hist["close"].iloc[-1])
    today = _guide_shaped_day(prev_close)
    profile = build_cum_volume_profile(hist, 20)

    def run(warm: pd.DataFrame | None) -> list[str]:
        state = DayState(symbol="TEST", prev_close=prev_close, cum_vol_profile=profile,
                         warmup_1m=warm,
                         warmup_5m=resample_5m(warm) if warm is not None else None)
        for i in range(1, len(today) + 1):
            step(state, today.iloc[:i], cfg, no_catalyst)
        return [e.reason for e in state.attention_events]

    assert run(hist) == run(None)
