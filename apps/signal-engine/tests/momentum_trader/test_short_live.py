"""The short side wired into both live loops.

`test_short_side.py` proves the reflection. This file proves the scanners
drive it: an NSE short opens and covers through `Scanner._process_shorts`, the
position cap and the day's guardrails are shared with the long side, a symbol
never holds both sides, and the US session shorts a loser from its own screen
while SEC Rule 201 refuses one that has been 10% down.
"""

from __future__ import annotations

import threading

import pandas as pd
import pytest
from src.config import Settings
from src.momentum_trader.bars import IST
from src.momentum_trader.discipline import DayDiscipline, DisciplineConfig
from src.momentum_trader.engine import DayState, EngineConfig
from src.momentum_trader.market import US
from src.momentum_trader.scanner import Scanner
from src.momentum_trader.short_side import SSR_REASON, Reflection, ShortBook
from src.momentum_trader.us_ledger import USPaperLedger
from src.momentum_trader.us_scanner import USScanner
from src.momentum_trader.us_session import USSession
from src.momentum_trader.us_universe import USUniverseConfig

from .test_short_side import PREV_CLOSE, STOP_HIT, TARGET_HIT, _falling, _profile
from .test_us_session import DAY, ET, FakeDB, FakeFeed, guide_day, prior_sessions, us_cfg

KEY = "NSE_EQ|TEST"


# ── NSE scanner ──────────────────────────────────────────────────────────────

class _Bars:
    """Minimal BarBuilder: serves a fixed day up to `now`."""

    def __init__(self, day: pd.DataFrame) -> None:
        self.day = day

    def closed_bars(self, _key: str, now: pd.Timestamp) -> pd.DataFrame:
        return self.day[self.day.index <= now]

    def latest_close(self, _key: str) -> float | None:
        return None


def _nse_scanner(day: pd.DataFrame, max_positions: int = 20) -> Scanner:
    s = Scanner.__new__(Scanner)
    s.s = Settings(mt_max_positions=max_positions, mt_enable_shorts=True)
    s.cfg = EngineConfig()                  # legacy pool: replay fills, one trade/day
    s.discipline = DayDiscipline(DisciplineConfig(enabled=False))
    s._state_lock = threading.RLock()
    s.builder = _Bars(day)                  # type: ignore[assignment]
    s.states = {KEY: DayState(symbol="TEST", prev_close=PREV_CLOSE,
                              cum_vol_profile=_profile())}
    s.shorts = {KEY: ShortBook("TEST", PREV_CLOSE, _profile())}
    s._tick_entries, s._tick_rejections = [], []
    return s


def _drive(s: Scanner, day: pd.DataFrame):  # noqa: ANN202
    opened, closed, rejected = [], [], []
    for ts in day.index:
        ev, new = s._process_shorts(ts, {KEY: day[day.index <= ts]})
        opened += new
        closed += ev.closed
        rejected += ev.rejections
    return opened, closed, rejected


def test_nse_scanner_opens_and_covers_a_short() -> None:
    day = _falling(TARGET_HIT)
    s = _nse_scanner(day)
    opened, closed, _ = _drive(s, day)
    assert len(opened) == 1 and len(closed) == 1
    assert opened[0].cand.side == "short" and closed[0].side == "short"
    assert opened[0].plan.stop > opened[0].plan.entry > opened[0].plan.target
    assert closed[0].exit_reason == "target" and closed[0].gross_inr > 0
    assert s._open_count() == 0


def test_short_positions_count_against_the_shared_cap() -> None:
    day = _falling([(105.85, 105.9, 105.8, 105.85, 700)])   # fills, stays open
    s = _nse_scanner(day)
    _drive(s, day)
    assert s.shorts[KEY].has_position and s._open_count() == 1


def test_a_full_book_refuses_the_short() -> None:
    day = _falling(TARGET_HIT)
    s = _nse_scanner(day, max_positions=0)
    opened, _, rejected = _drive(s, day)
    assert opened == [] and [r.reason for r in rejected] == ["max_positions"]


def test_a_symbol_never_holds_a_long_and_a_short() -> None:
    day = _falling(TARGET_HIT)
    s = _nse_scanner(day)
    s.states[KEY].position = object()        # type: ignore[assignment]  # a long is open
    opened, _, rejected = _drive(s, day)
    assert opened == [] and rejected[0].reason == "opposite_side_open"
    assert rejected[0].side == "short"


def test_halted_day_cancels_an_armed_short() -> None:
    day = _falling(TARGET_HIT)
    s = _nse_scanner(day)
    s.discipline = DayDiscipline(DisciplineConfig(enabled=True))
    for _ in range(3):                       # three straight losses halt the day
        s.discipline.record(-100.0)
    assert s.discipline.halted
    opened, _, rejected = _drive(s, day)
    assert opened == [] and rejected[0].reason.startswith("halted:")


def test_eod_sweep_covers_a_short_that_stopped_printing() -> None:
    day = _falling([(105.85, 105.9, 105.8, 105.85, 700)])
    s = _nse_scanner(day)
    _drive(s, day)
    assert s.shorts[KEY].has_position
    swept = s._eod_sweep(pd.Timestamp("2026-09-07 15:16", tz=IST), {KEY: day})
    assert len(swept) == 1 and swept[0].side == "short"
    assert swept[0].exit_reason == "eod_sweep" and not s.shorts[KEY].has_position


def test_stop_out_is_a_loss_on_the_short() -> None:
    day = _falling(STOP_HIT)
    _, closed, _ = _drive(_nse_scanner(day), day)
    assert closed[0].exit_reason == "stop" and closed[0].gross_inr < 0
    assert closed[0].exit > closed[0].entry


# ── US session ───────────────────────────────────────────────────────────────

def _loser_day(gap_pct: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The guide's day, moved to open `gap_pct` up, then reflected about the
    previous close: a loser opening `gap_pct` DOWN with the same shape."""
    hist = prior_sessions()
    prev = float(hist["close"].iloc[-1])
    rising = guide_day(prev * (1 + gap_pct / 100.0) / 1.12, minutes=60)
    return Reflection(prev).bars(rising), hist


def _us_session(feed: FakeFeed, db: FakeDB) -> USSession:
    # The guide's push-pause-break adds ~6% after the open, so the fixture
    # opens -3.5% to break near -9%, inside SEC Rule 201's -10% line.
    short_cfg = USUniverseConfig(side="short", day_chg_min_pct=3.0)
    return USSession(feed, us_cfg(), USPaperLedger(db, "us_test"),
                     short_scanner=USScanner(feed, short_cfg, US))


def _run(sess: USSession, start: str, end: str) -> None:
    t = pd.Timestamp(f"{DAY} {start}", tz=ET)
    stop = pd.Timestamp(f"{DAY} {end}", tz=ET)
    while t <= stop:
        sess.cycle(t + pd.Timedelta(seconds=2))
        for sec in (12, 22, 32, 42, 52):
            sess.poll_pending(t + pd.Timedelta(seconds=sec))
        t += pd.Timedelta(minutes=1)


def test_us_session_shorts_a_loser_from_its_own_screen() -> None:
    day, hist = _loser_day(3.5)
    db = FakeDB()
    sess = _us_session(FakeFeed(day, hist), db)
    _run(sess, "09:31", "10:29")
    events = db[US.candidates_collection].inserted
    opened = db[US.positions_collection].inserted
    assert opened, f"no US short. Rejections: {[d['reason'] for d in events if d['kind'] == 'rejection']}"
    doc = opened[0]
    assert doc["side"] == "short" and doc["locate_verified"] is False
    assert doc["stop"] > doc["entry_price"] > doc["target"]
    assert doc["risk_usd"] == pytest.approx(50.0, rel=0.05)
    assert "USX" in sess.short_watch and "USX" not in sess.watch   # the long screen passed on it


def test_ssr_refuses_a_us_short_on_a_stock_already_10pct_down() -> None:
    day, hist = _loser_day(12.0)                      # opens -12%: SSR from the first print
    db = FakeDB()
    sess = _us_session(FakeFeed(day, hist), db)
    _run(sess, "09:31", "10:29")
    assert not db[US.positions_collection].inserted
    reasons = [d["reason"] for d in db[US.candidates_collection].inserted
               if d["kind"] == "rejection"]
    assert SSR_REASON in reasons


def test_us_watchlist_reports_the_losers_screen_separately() -> None:
    day, hist = _loser_day(3.5)
    db = FakeDB()
    sess = _us_session(FakeFeed(day, hist), db)
    _run(sess, "09:31", "09:40")
    doc = db[US.watchlist_collection].replaced[-1][1]
    assert doc["short"]["passed"] == 1 and doc["short"]["names"][0]["side"] == "short"
    assert doc["passed"] == 0                         # the long funnel is unchanged


def test_shorts_off_leaves_the_us_session_long_only() -> None:
    day, hist = _loser_day(3.5)
    db = FakeDB()
    sess = USSession(FakeFeed(day, hist), us_cfg(), USPaperLedger(db, "us_test"))
    _run(sess, "09:31", "10:29")
    assert sess.short_scanner is None and not db[US.positions_collection].inserted
