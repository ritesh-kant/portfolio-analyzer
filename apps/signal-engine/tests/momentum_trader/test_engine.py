"""Engine lifecycle tests: pending → fill at next open → stop / target / EOD exits,
RVOL gating, entry cutoff, one-trade-per-day, chase cancel; plus the bar
builder, Upstox parsing, universe filters, and the catalyst lookup."""

from __future__ import annotations

from datetime import time

import pandas as pd
import pytest
from src.momentum_trader import catalyst, engine, universe
from src.momentum_trader.bars import IST, BarBuilder
from src.momentum_trader.upstox import candles_to_frame

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
    assert universe.passes_static(None, require_facts=True) == (False, "missing_facts")


def test_universe_dynamic_filters() -> None:
    assert universe.passes_dynamic(150.0, 10.0) == (True, "ok")
    assert universe.passes_dynamic(30.0, 10.0)[1] == "price"
    assert universe.passes_dynamic(150.0, 200.0)[1] == "turnover"
    assert universe.passes_dynamic(150.0, None) == (True, "ok")
    assert universe.passes_dynamic(150.0, None, require_turnover=True) == (
        False,
        "missing_turnover",
    )


# ── catalyst ──────────────────────────────────────────────────────────────────

class _FakeSignals:
    """Minimal pymongo-like collection: naive-UTC created_at, as pymongo returns."""

    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs

    def find_one(self, filter: dict, sort=None, projection=None):  # noqa: ANN001
        lo, hi = filter["created_at"]["$gte"], filter["created_at"]["$lte"]
        hits = [d for d in self.docs
                if filter["stocks"] in d["stocks"]
                and d["event_type"] in filter["event_type"]["$in"]
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
