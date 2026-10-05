"""Replay rules: every ordering decision in sim.py has a test, because each one is the
difference between a result you can trust and one that quietly flatters the strategy."""

from __future__ import annotations

import numpy as np
import pytest

from btlab.sim import ExitCfg, simulate


def mk(bars, start=600, **m5):
    """bars: list of (o, h, l, c). 1-minute bars from `start` (minute of day); 5-minute
    arrays are aggregated from them unless overridden."""
    o, h, lo, c = (np.array(x, float) for x in zip(*bars))
    n = len(bars)
    arr = {"m1_o": o, "m1_h": h, "m1_l": lo, "m1_c": c, "m1_min": np.arange(start, start + n, dtype=np.int16)}
    g = n // 5
    arr.update({
        "m5_min": np.arange(start, start + 5 * g, 5, dtype=np.int16),
        "m5_o": o[: 5 * g: 5], "m5_h": h[: 5 * g].reshape(g, 5).max(1),
        "m5_l": lo[: 5 * g].reshape(g, 5).min(1), "m5_c": c[4: 5 * g: 5],
        "m5_v": np.ones(g), "m5_ema9": np.full(g, np.nan), "m5_ema20": np.full(g, np.nan),
        "m5_macd": np.full(g, np.nan), "m5_vwap": np.full(g, np.nan), "m5_volratio": np.ones(g),
        "m5_pivlow": np.zeros(g, bool),
    })
    for k, v in m5.items():
        arr[k] = np.array(v, dtype=bool if k == "m5_pivlow" else float)
    return arr


FLAT = (100.0, 100.2, 99.9, 100.0)
# entry 100, stop 99 (risk 1), resistance target 102
ARGS = dict(k0=0, entry=100.0, stop0=99.0, target0=102.0, resistance=102.2, qty=100)


def run(bars, cfg=None, start=600, m5=None, **kw):
    return simulate(mk(bars, start=start, **(m5 or {})), **{**ARGS, **kw}, cfg=cfg or ExitCfg())


def test_target_fills_at_target():
    ex = run([FLAT, (100, 100.5, 99.9, 100.4), (100.4, 102.5, 100.3, 102.0)] + [FLAT] * 3)
    assert (ex.reason, ex.price, ex.k) == ("target", 102.0, 2)


def test_gap_up_through_target_fills_at_open():
    ex = run([FLAT, (103.0, 103.5, 102.8, 103.2)] + [FLAT] * 4)
    assert (ex.reason, ex.price) == ("target", 103.0)


def test_bar_touching_stop_and_target_is_a_stop():
    ex = run([FLAT, (100, 102.5, 98.5, 100)] + [FLAT] * 4)
    assert (ex.reason, ex.price) == ("stop", 99.0)


def test_gap_down_through_stop_fills_at_open():
    ex = run([FLAT, (98.0, 98.4, 97.5, 98.0)] + [FLAT] * 4)
    assert (ex.reason, ex.price) == ("stop", 98.0)


def test_fill_bar_can_stop_out_but_cannot_hit_target():
    ex = run([(100, 105, 100, 104)] + [FLAT] * 5)          # would be a target on any later bar
    assert ex.reason != "target"
    ex = run([(100, 100.4, 98.0, 99)] + [FLAT] * 5)
    assert (ex.reason, ex.price, ex.k) == ("stop", 99.0, 0)


def test_eod_closes_at_the_close_of_the_1514_bar():
    bars = [FLAT] * 6
    ex = run(bars, start=914 - 5)
    assert ex.reason == "eod" and ex.minute == 915 and ex.price == 100.0


def test_no_target_runs_to_eod():
    cfg = ExitCfg(target_mode="none")
    ex = run([FLAT, (100, 110, 100, 109)] + [FLAT] * 3, cfg=cfg)
    assert ex.reason == "eod"


def test_fixed_rr_target():
    cfg = ExitCfg(target_mode="rr", target_rr=3.0)
    ex = run([FLAT, (100, 103.5, 100, 103.2)] + [FLAT] * 3, cfg=cfg)
    assert (ex.reason, ex.price) == ("target", 103.0)


def test_breakeven_lift_never_applies_on_the_bar_that_arms_it():
    cfg = ExitCfg(target_mode="none", breakeven=True, breakeven_r=1.0)
    # bar 1 runs to +1R and trades back under entry: the SAME bar must not stop us at 100 ...
    calm = (100.1, 100.3, 100.05, 100.2)
    ex = run([FLAT, (100.2, 101.2, 99.95, 100.1), calm, calm, calm, calm], cfg=cfg)
    assert ex.reason == "eod"
    # ... but the next bar, with the stop now at entry, does stop us there
    ex = run([FLAT, (100.2, 101.2, 100.0, 100.1), (100.1, 100.2, 99.8, 99.9), calm, calm, calm], cfg=cfg)
    assert (ex.reason, ex.price, ex.k) == ("breakeven_stop", 100.0, 2)


def test_ema9_exit_needs_the_trade_to_be_armed_first():
    # 5m bar 0 closes under its EMA9. Not armed (never up half a risk): stay in.
    ema9 = [101.0, 101.0]
    cfg = ExitCfg(target_mode="none", ema9_exit=True)
    ex = run([FLAT] * 10, cfg=cfg, m5={"m5_ema9": ema9})
    assert ex.reason == "eod"
    # up 0.6R on bar 1, then bar 4 closes the first 5m bar under EMA9 -> sold at that close
    bars = [FLAT, (100, 100.7, 100, 100.5), FLAT, FLAT, (100, 100.1, 99.5, 99.8)] + [FLAT] * 5
    ex = run(bars, cfg=cfg, m5={"m5_ema9": ema9})
    assert (ex.reason, ex.price, ex.minute) == ("ema9_break", 99.8, 605)


def test_swing_trail_lifts_the_stop_under_a_confirmed_pivot_low():
    n = 45
    up = (101.0, 101.4, 100.9, 101.2)
    bars = [FLAT] + [up] * (n - 1)
    bars[36] = (101.2, 101.2, 100.2, 100.4)                      # falls through the lifted stop
    arr = mk(bars)
    g = len(arr["m5_min"])
    flags = np.zeros(g, bool)
    flags[3] = True                                              # pivot low at 5m bar 3 ...
    lows = np.full(g, 101.0)
    lows[3] = 100.6
    arr["m5_pivlow"], arr["m5_l"] = flags, lows                  # ... confirmed by bar 6 (K=3 bars after)
    ex = simulate(arr, cfg=ExitCfg(target_mode="none", swing_trail=True), **ARGS)
    assert (ex.reason, ex.k) == ("trail_stop", 36)
    assert ex.price == pytest.approx(100.45)                     # 100.6 less 0.10% = 100.4994, floored to the tick
    # a pivot that is NOT yet confirmed must not lift anything
    flags2 = np.zeros(g, bool)
    flags2[6] = True
    arr["m5_pivlow"] = flags2
    ex = simulate(arr, cfg=ExitCfg(target_mode="none", swing_trail=True), **ARGS)
    assert ex.reason != "trail_stop" or ex.k > 36


def test_checkpoint_lifts_stop_under_resistance_and_target_stays_2r():
    cfg = ExitCfg(target_mode="checkpoint")
    # price reaches the checkpoint 101.5, then pulls back through the lifted stop (101.5*0.9985=101.3)
    ex = run([FLAT, (100, 101.6, 100.2, 101.4), (101.4, 101.5, 101.0, 101.1), FLAT, FLAT, FLAT],
             cfg=cfg, target0=101.5)
    assert (ex.reason, ex.k) == ("checkpoint_stop", 2)
    assert ex.price == pytest.approx(101.3, abs=0.06)


def test_stale_exit():
    cfg = ExitCfg(target_mode="none", stale_exit=True, stale_minutes=3, stale_r=0.5)
    ex = run([FLAT] * 8, cfg=cfg)
    assert ex.reason == "stale"


def test_mfe_mae_are_in_r():
    ex = run([FLAT, (100, 101.0, 99.5, 100.5), (100.5, 100.6, 98.9, 99.0)] + [FLAT] * 3)
    assert ex.reason == "stop" and ex.mfe_r == pytest.approx(1.0) and ex.mae_r == pytest.approx(-1.1)
