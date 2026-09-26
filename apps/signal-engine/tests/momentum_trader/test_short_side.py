"""Short side = the long engine on a reflected tape (short_side.py).

The central property: reflect a day the long engine trades about its previous
close and the short side must take the mirror-image trade - same bar, same
exit reason, entry/exit/stop/target reflected, the same rupees per share. Every
other test pins a place where a short is NOT just a reflection: real-price
costs (STT on the sale), the notional cap on the real sale price, SEC Rule 201,
the round-number grid and the reflection's own domain.
"""

from __future__ import annotations

from datetime import time

import pandas as pd
import pytest
from src.momentum_trader import engine, short_side
from src.momentum_trader.bars import IST
from src.momentum_trader.engine import FILL_FUTURE_TRIGGER, EngineConfig
from src.momentum_trader.indicators import reflected, round_levels_above
from src.momentum_trader.market import US
from src.momentum_trader.short_side import Reflection, ShortBook, run_day_short
from src.news_trader.trailing_sl import calc_costs

Row = tuple[float, float, float, float, float]
COLS = ["open", "high", "low", "close", "volume"]
PREV_CLOSE = 100.0

# Same +5% micro-pullback day as test_engine.py (trigger 105.6, stop 105.3).
PRELUDE: Row = (105.0, 106.5, 104.9, 105.0, 500)
TRIGGER: Row = (105.35, 105.9, 105.3, 105.8, 900)
TARGET_HIT: list[Row] = [
    (105.85, 106.0, 105.8, 105.95, 700),   # fill @ open 105.85
    (105.95, 106.7, 105.9, 106.6, 900),
    (106.6, 107.2, 106.5, 107.0, 900),     # 2R target 106.95 prints
]
STOP_HIT: list[Row] = [
    (105.85, 105.9, 105.8, 105.85, 700),
    (105.8, 105.85, 105.0, 105.1, 900),    # low through the 105.3 stop
]


def _bars(rows: list[Row], start: str = "2026-09-07 09:15") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(rows), freq="1min", tz=IST)
    return pd.DataFrame(rows, index=idx, columns=COLS)


def _mover_day(after: list[Row]) -> pd.DataFrame:
    rows: list[Row] = [PRELUDE] * 42
    rows += [
        (105.0, 105.3, 104.95, 105.25, 500),
        (105.25, 105.6, 105.2, 105.55, 500),
        (105.55, 105.6, 105.3, 105.35, 300),
        TRIGGER, *after,
    ]
    return _bars(rows)


def _falling(after: list[Row]) -> pd.DataFrame:
    """The same day, reflected: a -5% loser with a bear-flag breakdown."""
    return Reflection(PREV_CLOSE).bars(_mover_day(after))


def _profile(per_min: float = 100.0) -> pd.Series:
    times = pd.date_range("2026-01-01 09:15", "2026-01-01 15:29", freq="1min").time
    return pd.Series([per_min * (i + 1) for i in range(len(times))], index=times)


def _no_cat(_s: str, _t: pd.Timestamp) -> tuple[int, str]:
    return 0, ""


def _short(day: pd.DataFrame, cfg: EngineConfig | None = None, **kw: object) -> ShortBook:
    return run_day_short("TEST", day, PREV_CLOSE, _profile(), cfg or EngineConfig(),
                         _no_cat, **kw)  # type: ignore[arg-type]


def _long(day: pd.DataFrame, cfg: EngineConfig | None = None) -> engine.DayState:
    return engine.run_day("TEST", day, PREV_CLOSE, _profile(), cfg or EngineConfig(), _no_cat)


# ── the reflection itself ────────────────────────────────────────────────────

def test_reflection_is_its_own_inverse_and_swaps_high_and_low() -> None:
    r = Reflection(100.0)
    assert r.px(r.px(95.35)) == pytest.approx(95.35)
    day = _mover_day(TARGET_HIT)
    back = r.bars(r.bars(day))
    pd.testing.assert_frame_equal(back, day, check_exact=False)
    refl = r.bars(day)
    assert (refl["high"] >= refl["low"]).all()
    assert refl["high"].iloc[0] == pytest.approx(200.0 - day["low"].iloc[0])
    assert (refl["volume"] == day["volume"]).all()


def test_day_change_is_exactly_negated() -> None:
    falling = _falling([])
    real_chg = (falling["close"].iloc[-1] / PREV_CLOSE - 1) * 100
    assert real_chg == pytest.approx(-5.8)


# ── the central property ─────────────────────────────────────────────────────

@pytest.mark.parametrize("after,reason", [(TARGET_HIT, "target"), (STOP_HIT, "stop")])
def test_short_side_takes_the_mirror_image_of_the_long_trade(
    after: list[Row], reason: str,
) -> None:
    # A notional cap that never binds, so both sides size purely off risk.
    cfg = EngineConfig(max_notional_inr=10_000_000.0)
    long_t = _long(_mover_day(after), cfg).closed
    book = _short(_falling(after), cfg)
    assert len(long_t) == 1 and len(book.closed) == 1
    lt, st = long_t[0], book.closed[0]
    r = Reflection(PREV_CLOSE)
    assert st.side == "short" and lt.side == "long"
    assert st.exit_reason == lt.exit_reason == reason
    assert (st.entry_time, st.exit_time) == (lt.entry_time, lt.exit_time)
    assert st.entry == pytest.approx(r.px(lt.entry))
    assert st.exit == pytest.approx(r.px(lt.exit))
    assert st.cand.setup.stop == pytest.approx(r.px(lt.cand.setup.stop))
    assert st.cand.setup.stop > st.entry              # the stop sits ABOVE a short
    assert st.target == pytest.approx(r.px(lt.target))
    assert st.qty == lt.qty
    assert st.gross_inr == pytest.approx(lt.gross_inr)   # same rupees per share
    assert st.gross_pct * lt.gross_pct > 0               # both measured "in favour"
    assert st.cand.day_chg_pct == pytest.approx(-lt.cand.day_chg_pct)
    assert st.cand.setup.name == lt.cand.setup.name == "micro_pullback"


def test_short_target_is_below_entry_and_wins_when_price_falls() -> None:
    t = _short(_falling(TARGET_HIT)).closed[0]
    assert t.exit < t.entry and t.target < t.entry
    assert t.gross_inr > 0 and t.gross_pct > 0
    assert t.gross_inr == pytest.approx((t.entry - t.exit) * t.qty)


def test_a_rising_day_gives_the_short_side_nothing() -> None:
    assert _short(_mover_day(TARGET_HIT)).candidates == []


# ── where a short is NOT just a reflection ───────────────────────────────────

def test_costs_are_the_real_short_costs_with_stt_on_the_entry_sale() -> None:
    t = _short(_falling(TARGET_HIT)).closed[0]
    expect = calc_costs(t.entry, t.exit, t.qty, direction="short")
    assert t.costs_inr == pytest.approx(expect["total"])
    assert expect["stt"] == pytest.approx(round(t.entry * t.qty * 0.00025, 2))
    assert t.net_inr == pytest.approx(t.gross_inr - t.costs_inr)


def test_stress_slip_applies_to_real_turnover() -> None:
    cfg = EngineConfig(stress_slip=0.004)
    t = _short(_falling(TARGET_HIT), cfg).closed[0]
    base = calc_costs(t.entry, t.exit, t.qty, direction="short")["total"]
    assert t.costs_inr == pytest.approx(base + (t.entry + t.exit) * t.qty * 0.004)


def test_quantity_uses_the_real_sale_price_under_the_notional_cap() -> None:
    cfg = EngineConfig(max_notional_inr=10_000.0)     # binds: ~95 x 105 shares
    t = _short(_falling(TARGET_HIT), cfg).closed[0]
    assert t.qty == int(10_000.0 // t.entry)
    long_t = _long(_mover_day(TARGET_HIT), cfg).closed[0]
    assert t.qty > long_t.qty          # the real sale price (~94) < the mirror's (~106)


def test_management_overlays_that_price_costs_internally_are_refused() -> None:
    with pytest.raises(ValueError):
        _short(_falling(TARGET_HIT), EngineConfig(cost_aware_breakeven=True))


def test_round_levels_are_the_real_grid_under_reflection() -> None:
    # Real price 312 sits on the 10/50 grid: marks below are 310 and 300.
    k = 320.0
    with reflected(k):
        minor, major = round_levels_above(2 * k - 312.0)
    assert (2 * k - minor, 2 * k - major) == pytest.approx((310.0, 300.0))
    # Outside a reflection nothing changed.
    assert round_levels_above(312.0) == (320.0, 350.0)
    # A price exactly on a mark: the next mark strictly below.
    with reflected(k):
        minor, _ = round_levels_above(2 * k - 300.0)
    assert 2 * k - minor == pytest.approx(290.0)


def test_exit_levels_sit_on_the_correct_side_of_a_short() -> None:
    # A short's stop-side level is ABOVE the entry and its target-side level
    # BELOW - the reflection of a long's support/resistance.
    cfg = EngineConfig(max_notional_inr=10_000_000.0)
    lt = _long(_mover_day(TARGET_HIT), cfg).closed[0]
    st = _short(_falling(TARGET_HIT), cfg).closed[0]
    r = Reflection(PREV_CLOSE)
    assert st.structural_resistance == pytest.approx(r.opt(lt.structural_support))
    assert st.structural_support == pytest.approx(r.opt(lt.structural_resistance))
    if st.structural_resistance is not None:
        assert st.structural_resistance > st.entry
    if st.structural_support is not None:
        assert st.structural_support < st.entry
    assert short_side._flip_kind("pivot_low") == "pivot_high"
    assert short_side._flip_target_source("structural_resistance:pivot_high") == \
        "structural_support:pivot_low"
    assert short_side._REASON_FLIP["support_break"] == "resistance_break"


# ── SEC Rule 201 (US) ────────────────────────────────────────────────────────

def _us_cfg() -> EngineConfig:
    # Same engine, US money. Wide stops/penny ticks are not what's under test.
    return EngineConfig(market=US, risk_inr=50.0, max_notional_inr=100_000.0)


def test_ssr_refuses_a_breakdown_short_once_price_has_been_10pct_down() -> None:
    day = _falling(TARGET_HIT)
    # One early bar wicks to -10.5%: SSR is on for the rest of the day.
    day.iloc[5, day.columns.get_loc("low")] = 89.5
    book = _short(day, EngineConfig(), ssr_rule=True)
    assert book.closed == []
    assert [r.reason for r in book.rejections] == [short_side.SSR_REASON]
    assert book.rejections[0].side == "short"
    # Without the rule (NSE has no SSR) the same day trades.
    assert len(_short(day, EngineConfig()).closed) == 1


def test_ssr_carried_from_the_prior_day_blocks_from_the_open() -> None:
    book = _short(_falling(TARGET_HIT), EngineConfig(), ssr_rule=True, ssr_carried=True)
    assert book.closed == [] and book.rejections[0].reason == "ssr_active"
    assert short_side.ssr_carried_from(prior_day_low=89.0, prior_prev_close=100.0)
    assert not short_side.ssr_carried_from(prior_day_low=91.0, prior_prev_close=100.0)


def test_us_short_costs_use_the_us_model() -> None:
    cost = short_side.short_round_trip_cost(US, 5.00, 4.80, 1000)
    assert cost == pytest.approx(US.round_trip_cost(5.00, 4.80, 1000))


# ── the live driver ──────────────────────────────────────────────────────────

def test_live_quote_fills_the_sell_stop_only_at_or_below_the_trigger() -> None:
    cfg = EngineConfig(fill_mode=FILL_FUTURE_TRIGGER)
    day = _falling([])
    book = ShortBook("TEST", PREV_CLOSE, _profile())
    for i in range(1, len(day) + 1):
        book.step(day.iloc[:i], cfg, _no_cat, allow_replay_fill=False)
    assert book.has_pending
    trigger = book.pending_trigger
    assert trigger == pytest.approx(200.0 - 105.6)            # the pause bar's LOW
    after = day.index[-1] + pd.Timedelta(seconds=70)
    ev = book.fill_quote(after, trigger + 0.05, cfg, day)      # above the level: waits
    assert ev.opened is None and book.has_pending
    ev = book.fill_quote(after + pd.Timedelta(seconds=1), trigger - 0.05, cfg, day)
    assert ev.opened is not None
    p = ev.opened
    assert p.cand.side == "short"
    assert p.plan.entry == pytest.approx(trigger - 0.05)
    assert p.plan.stop > p.plan.entry > p.plan.target
    assert p.plan.risk_inr == pytest.approx((p.plan.stop - p.plan.entry) * p.plan.qty)
    t = book.force_close(after + pd.Timedelta(minutes=5), p.plan.entry - 0.30, cfg)
    assert t is not None and t.gross_inr == pytest.approx(0.30 * p.plan.qty)
    assert book.position is None


def test_cancel_pending_logs_a_real_rejection() -> None:
    cfg = EngineConfig(fill_mode=FILL_FUTURE_TRIGGER)
    day = _falling([])
    book = ShortBook("TEST", PREV_CLOSE, _profile())
    for i in range(1, len(day) + 1):
        book.step(day.iloc[:i], cfg, _no_cat, allow_replay_fill=False)
    rej = book.cancel_pending(day.index[-1], "max_positions", 94.2)
    assert rej is not None and rej.side == "short" and not book.has_pending
    assert rej.trigger == pytest.approx(200.0 - 105.6)


def test_a_stock_that_doubles_leaves_the_reflection_domain_flat() -> None:
    day = _falling([(105.85, 105.9, 105.8, 105.85, 700)])  # short fills ~94.15, stays open
    spike = _bars([(150.0, 201.0, 150.0, 199.0, 900)],
                  start=(day.index[-1] + pd.Timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M"))
    book = _short(pd.concat([day, spike]))
    assert book.out_of_domain
    assert len(book.closed) == 1 and book.closed[0].exit_reason == "reflection_domain"
    assert book.closed[0].exit == pytest.approx(199.0)     # marked at the real close


def test_entry_cutoff_applies_to_shorts() -> None:
    late = _falling(TARGET_HIT)
    late.index = pd.date_range("2026-09-07 13:50", periods=len(late), freq="1min", tz=IST)
    assert _short(late, EngineConfig(entry_cutoff=time(14, 30))).candidates == []
