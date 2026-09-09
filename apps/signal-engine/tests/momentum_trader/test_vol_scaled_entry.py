"""Recorded-only fields for the volatility-scaled-entry hypothesis
(research/hypotheses/2026-09-06-volatility-scaled-entry.md).

The whole design rests on ONE property: `atr_pct` and `macd_hist` are recorded
but never gate anything, so the derived arms C/C'/D are exact SUBSETS of the run
and the anti-test is valid. If either field ever starts changing which trades are
taken, that inference silently breaks — so it is pinned here.
"""

from __future__ import annotations

import pandas as pd
import pytest
from src.momentum_trader import engine as eng
from src.momentum_trader.exits import MACD_SLOW

from tests.momentum_trader.test_multi_entry import _session

IST = "Asia/Kolkata"


def _run(daily_atr_pct: float | None, day_chg_min: float = 4.0) -> eng.DayState:
    bars, prev_close, profile = _session()
    cfg = eng.EngineConfig(stress_slip=0.0, day_chg_min=day_chg_min)
    return eng.run_day("T", bars, prev_close, profile, cfg, lambda _s, _t: (0, ""),
                       daily_atr_pct=daily_atr_pct)


# ── the load-bearing property ────────────────────────────────────────────────

def test_atr_pct_never_changes_which_trades_are_taken() -> None:
    """Same day, wildly different volatility readings — identical trade set."""
    quiet = _run(daily_atr_pct=0.5)
    wild = _run(daily_atr_pct=99.0)
    absent = _run(daily_atr_pct=None)
    keys = [sorted((t.entry_time, t.entry, t.exit_reason) for t in s.closed)
            for s in (quiet, wild, absent)]
    assert keys[0] == keys[1] == keys[2]
    assert len(quiet.closed) > 0, "fixture must produce trades or this proves nothing"


def test_atr_pct_is_recorded_verbatim_on_every_candidate() -> None:
    st = _run(daily_atr_pct=3.75)
    assert st.candidates, "fixture must produce candidates"
    assert all(c.atr_pct == 3.75 for c in st.candidates)


def test_macd_hist_is_recorded_on_every_candidate() -> None:
    st = _run(daily_atr_pct=3.0)
    assert st.candidates
    # a value or None (pre-warm-up), but the attribute must always be present
    assert all(c.macd_hist is None or isinstance(c.macd_hist, float)
               for c in st.candidates)


def test_defaults_are_none_so_older_backtests_are_unchanged() -> None:
    from src.momentum_trader.setups import Setup
    c = eng.Candidate(symbol="T", time=pd.Timestamp("2024-06-03 10:00", tz=IST),
                      setup=Setup("micro_pullback", 100.0, 99.0), day_chg_pct=5.0,
                      rvol=4.0, catalyst=0, event_type="", candle_tags=[])
    assert (c.atr_pct, c.macd_hist) == (None, None)


# ── _macd_hist_now ───────────────────────────────────────────────────────────

def _rising_5m(n: int, start: float = 100.0, step: float = 0.2) -> pd.DataFrame:
    idx = pd.date_range(pd.Timestamp("2024-06-03 09:15", tz=IST), periods=n, freq="5min")
    px = [start + i * step for i in range(n)]
    return pd.DataFrame({"open": px, "high": [p + 0.1 for p in px],
                         "low": [p - 0.1 for p in px], "close": px,
                         "volume": [1000.0] * n}, index=idx)


def test_macd_hist_is_none_on_an_empty_frame() -> None:
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    assert eng._macd_hist_now(empty, None) is None


def test_macd_hist_is_none_before_the_slow_ema_has_warmed_up() -> None:
    assert eng._macd_hist_now(_rising_5m(MACD_SLOW - 5), None) is None


def test_macd_hist_is_positive_on_a_steady_advance() -> None:
    """A clean uptrend is exactly the 'indicator is showing a signal' case."""
    v = eng._macd_hist_now(_rising_5m(60), None)
    assert v is not None and v > 0


def test_macd_hist_turns_negative_once_the_advance_rolls_over() -> None:
    up = _rising_5m(60)
    down = _rising_5m(25, start=float(up["close"].iloc[-1]), step=-0.4)
    down.index = pd.date_range(up.index[-1] + pd.Timedelta(minutes=5),
                               periods=len(down), freq="5min")
    v = eng._macd_hist_now(pd.concat([up, down]), None)
    assert v is not None and v < 0


def test_warmup_bars_let_macd_read_early_in_the_session() -> None:
    """With three prior sessions fed in, a 09:40 entry has a MACD value rather
    than the None it would get from five of today's bars alone."""
    warm = _rising_5m(80)
    today = _rising_5m(5, start=float(warm["close"].iloc[-1]))
    today.index = pd.date_range(warm.index[-1] + pd.Timedelta(minutes=5),
                                periods=len(today), freq="5min")
    assert eng._macd_hist_now(today, None) is None
    assert eng._macd_hist_now(today, warm) is not None


# ── the floor override (arm B) ───────────────────────────────────────────────

def test_day_chg_min_override_is_accepted_and_widens_the_pool() -> None:
    strict, wide = _run(3.0, day_chg_min=4.0), _run(3.0, day_chg_min=2.0)
    assert len(wide.candidates) >= len(strict.candidates)


@pytest.mark.parametrize("floor", [2.0, 4.0])
def test_the_ceiling_is_untouched_by_the_floor_override(floor: float) -> None:
    cfg = eng.EngineConfig(day_chg_min=floor)
    assert (cfg.day_chg_min, cfg.day_chg_max) == (floor, eng.DAY_CHG_MAX_PCT)


def test_engine_default_floor_is_still_four_percent() -> None:
    """Arm A must remain the shipped rule until a hypothesis says otherwise."""
    assert eng.EngineConfig().day_chg_min == 4.0
