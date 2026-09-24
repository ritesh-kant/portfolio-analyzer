"""The US arm end to end: real engine, US profile, and the live session loop.

Two layers. The first runs the REAL engine (`run_day`) on a US-shaped day and
asserts it trades — the same protection `test_warrior_strict_arm` gives the NSE
arm: a checklist that refuses everything passes every unit test and takes zero
trades forever. The second drives `USSession` minute by minute against a fake
feed, so the loop's own rules (discovery, no replay, quote fills, EOD) are
tested without touching the network.
"""

from __future__ import annotations

import dataclasses
from datetime import date, time

import pandas as pd
import pytest
from src.config import Settings
from src.momentum_trader import us_session as us_session_mod
from src.momentum_trader.catalyst import no_catalyst
from src.momentum_trader.engine import build_cum_volume_profile, resample_5m, run_day
from src.momentum_trader.ledger import chart_bars_doc
from src.momentum_trader.market import US
from src.momentum_trader.scanner import STRATEGY_WARRIOR_STRICT, _strategy_config
from src.momentum_trader.setups import opening_range_breakout
from src.momentum_trader.us_ledger import WATCH_BARS_COLLECTION, USPaperLedger
from src.momentum_trader.us_screener import USQuote
from src.momentum_trader.us_session import USSession, build_engine_config
from src.momentum_trader.us_universe import USNameFacts, USUniverseConfig

ET = "America/New_York"
COLS = ["open", "high", "low", "close", "volume"]
DAY = date(2026, 9, 23)


# ── fixtures: a US day built to the guide's shape ───────────────────────────
def prior_sessions(base: float = 5.0) -> pd.DataFrame:
    """Five quiet sessions: warm EMAs/MACD and a flat 20k/min volume profile."""
    frames = []
    for j, d in enumerate(["2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22"]):
        idx = pd.date_range(pd.Timestamp(f"{d} 09:30", tz=ET), periods=390, freq="1min")
        rows, p = [], base + j * 0.02
        for _ in range(390):
            o, c = p, p * 1.00001
            rows.append((o, max(o, c) * 1.0005, min(o, c) * 0.9995, c, 20_000.0))
            p = c
        frames.append(pd.DataFrame(rows, columns=COLS, index=idx))
    return pd.concat(frames)


def guide_day(prev: float, pause_wick: float = 0.015, minutes: int = 60) -> pd.DataFrame:
    """The guide's sequence, fitted into the US 09:30-10:00 entry window.

    09:30-09:44 opening range   09:45-09:49 a gentle break of it (inside the 1%
    chase guard)   09:50-09:52 a strong push   09:53 a doji pause with a long
    LOWER wick on light volume   09:54 the break on heavy volume   then drift.

    The long-wicked pause is what makes this a US trade: its close barely moves
    (so MACD stays open) while its low puts the stop far enough away to pay
    for per-share costs and a penny spread.
    """
    idx = pd.date_range(pd.Timestamp(f"{DAY} 09:30", tz=ET), periods=minutes, freq="1min")
    rows, p = [], prev * 1.12                                   # +12%: criterion 2
    for i in range(minutes):
        if i < 15:
            o, c, v = p, p * 1.0003, 30_000.0
            h, lo = max(o, c) * 1.0004, min(o, c) * 0.9996
        elif i < 20:
            o, c, v = p, p * 1.0015, 55_000.0
            h, lo = c * 1.0003, o * 0.9997
        elif i < 23:
            o, c, v = p, p * 1.012, 70_000.0
            h, lo = c * 1.0005, o * 0.9995
        elif i == 23:
            o, c, v = p, p * 0.9999, 12_000.0
            h, lo = o * 1.0005, o * (1 - pause_wick)
        elif i == 24:
            o, c, v = p, p * 1.006, 220_000.0
            h, lo = c * 1.0003, o * 0.9995
        else:
            o, c, v = p, p * 1.0006, 45_000.0
            h, lo = c * 1.0004, o * 0.9996
        rows.append((o, h, lo, c, v))
        p = c
    return pd.DataFrame(rows, columns=COLS, index=idx)


def us_cfg():
    return build_engine_config(Settings(), DAY, USUniverseConfig())


def replay(day: pd.DataFrame, cfg=None):
    hist = prior_sessions()
    prev = float(hist["close"].iloc[-1])
    return run_day(
        "USX", day, prev, build_cum_volume_profile(hist, 20), cfg or us_cfg(), no_catalyst,
        warmup_1m=hist,
        prev_day={"high": float(hist["high"].iloc[-390:].max()),
                  "low": float(hist["low"].iloc[-390:].min()), "close": prev},
    )


# ── the arm trades, on the real engine ──────────────────────────────────────
def test_the_full_checklist_takes_the_guides_setup_on_a_us_day():
    hist = prior_sessions()
    st = replay(guide_day(float(hist["close"].iloc[-1])))
    assert st.closed, (
        "the US arm refused a textbook setup — it would never trade. "
        f"Rejections: {sorted({r.reason for r in st.rejections})}"
    )
    trade = st.closed[0]
    assert trade.entry_time.time() < time(10, 0), "entry must land inside the peak window"


def test_the_opening_range_is_what_promoted_it():
    """Regression for the 09:15 bug: the US opening range must be a live
    promotion reason, or the opening drive is never noticed."""
    hist = prior_sessions()
    st = replay(guide_day(float(hist["close"].iloc[-1])))
    assert st.attention_events and st.attention_events[0].reason == "setup:orb15"


def test_opening_range_uses_the_markets_own_open():
    hist = prior_sessions()
    bars5 = resample_5m(guide_day(float(hist["close"].iloc[-1])).iloc[:20])
    assert opening_range_breakout(bars5) is None                     # 09:15 default: empty range
    assert opening_range_breakout(bars5, US.session_start) is not None


def test_us_costs_and_us_sizing_are_what_the_engine_charged():
    """Per-share IBKR costs, not India's percentage stack; and a dollar-sized
    position under the dollar notional cap."""
    hist = prior_sessions()
    trade = replay(guide_day(float(hist["close"].iloc[-1]))).closed[0]
    assert trade.costs_inr == pytest.approx(US.round_trip_cost(trade.entry, trade.exit, trade.qty))
    assert trade.qty * trade.entry <= 5_000.0
    assert trade.costs_inr / (trade.qty * trade.entry) > 0.002        # a penny spread on ~$6


def test_a_tight_pause_is_refused_by_the_cost_gate_on_the_real_engine():
    """Same day, but the pause barely dips: the stop is too close to pay for
    a penny spread plus per-share fees. BT39's finding, as a live refusal."""
    hist = prior_sessions()
    st = replay(guide_day(float(hist["close"].iloc[-1]), pause_wick=0.006))
    assert not st.closed
    assert "cost_over_risk" in {r.reason for r in st.rejections}


# ── the config is the NSE strategy, moved, not rewritten ────────────────────
ALLOWED_TO_DIFFER = {
    "market", "risk_inr", "max_notional_inr", "entry_cutoff", "eod_close",
    "peak_hours_only", "peak_hours_end", "attention_day_chg_min", "one_trade_per_day", "exit_cfg",
}


def test_us_config_inherits_every_checklist_rule_from_the_nse_arm():
    nse = _strategy_config(Settings(mt_strategy=STRATEGY_WARRIOR_STRICT))
    us = us_cfg()
    drift = {
        f.name for f in dataclasses.fields(nse)
        if f.name not in ALLOWED_TO_DIFFER and getattr(nse, f.name) != getattr(us, f.name)
    }
    assert drift == set(), f"US arm silently differs from the NSE strategy on: {drift}"


def test_us_config_is_the_us_market_in_dollars():
    cfg = us_cfg()
    assert cfg.market is US
    assert (cfg.risk_inr, cfg.max_notional_inr) == (50.0, 5_000.0)
    assert cfg.entry_deadline == time(15, 10)     # peak-hours rule off by default
    assert cfg.eod_close == time(15, 54)
    assert cfg.attention_day_chg_min == 10.0


def test_early_close_day_shortens_the_session():
    cfg = build_engine_config(Settings(), date(2026, 11, 27), USUniverseConfig())
    assert cfg.eod_close == time(12, 54)
    assert cfg.entry_cutoff == time(12, 10)
    assert cfg.entry_deadline == time(12, 10)


def test_peak_hours_rule_can_be_turned_back_on():
    on = Settings(mt_us_peak_hours_only=True)
    assert build_engine_config(on, DAY, USUniverseConfig()).entry_deadline == time(10, 0)
    early = build_engine_config(on, date(2026, 11, 27), USUniverseConfig())
    assert early.entry_deadline == time(10, 0)


# ── the live loop, against a fake feed ──────────────────────────────────────
class FakeFeed:
    """Serves one day minute by minute: only bars that have CLOSED by `now`."""

    settle_seconds = 0

    def __init__(self, day: pd.DataFrame, hist: pd.DataFrame, symbol: str = "USX",
                 halted: set[str] | None = None, bars_ok: bool = True):
        self.day, self.hist, self.sym = day, hist, symbol
        self.prev = float(hist["close"].iloc[-1])
        self.prev_close_of = {symbol: self.prev}
        self.exchange_of = {symbol: "NASDAQ"}
        self.quote_source = {symbol: "Nasdaq Real Time Price"}
        self._halted = halted or set()
        self.bars_ok = bars_ok

    def _closed(self, now):
        return self.day[self.day.index <= now - pd.Timedelta(seconds=60)]

    def snapshot(self, now):
        closed = self._closed(now)
        if closed.empty:
            return []
        px = float(closed["close"].iloc[-1])
        return [USQuote(self.sym, px, (px / self.prev - 1) * 100, 8.0, None, now.isoformat())]

    def bars_1m(self, symbol, now):
        return self._closed(now) if self.bars_ok else self.day.iloc[0:0]

    def facts(self, symbols):
        return {s: USNameFacts(s, float_shares=4e6, exchange="NASDAQ", as_of=str(DAY)) for s in symbols}

    def halted(self, now):
        return set(self._halted)

    def history_1m(self, symbol, now, days=29):
        return self.hist

    def daily(self, symbol, now):
        return self.hist.resample("1D").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()

    def last_price(self, symbol, now):
        """The price trading right now: the close of the minute containing `now`."""
        live = self.day[self.day.index <= now]
        return float(live["close"].iloc[-1]) if not live.empty else None


class FakeCollection:
    def __init__(self):
        self.inserted: list[dict] = []
        self.updates: list[tuple[dict, dict]] = []
        self.replaced: list[tuple[dict, dict, bool]] = []

    def insert_one(self, doc):
        self.inserted.append(doc)
        return type("R", (), {"inserted_id": len(self.inserted)})()

    def update_one(self, flt, upd):
        self.updates.append((flt, upd))

    def replace_one(self, flt, doc, upsert=False):
        self.replaced.append((flt, doc, upsert))


class FakeDB(dict):
    def __missing__(self, key):
        self[key] = FakeCollection()
        return self[key]


def session(feed, db=None) -> USSession:
    return USSession(feed, us_cfg(), USPaperLedger(db if db is not None else FakeDB(), "us_test"))


def run_minutes(sess: USSession, start: str, end: str, poll: bool = True) -> None:
    """Drive the loop as `run()` does: a cycle ~2s after each bar, polls between."""
    t = pd.Timestamp(f"{DAY} {start}", tz=ET)
    stop = pd.Timestamp(f"{DAY} {end}", tz=ET)
    while t <= stop:
        sess.cycle(t + pd.Timedelta(seconds=2))
        if poll:
            for s in (12, 22, 32, 42, 52):
                sess.poll_pending(t + pd.Timedelta(seconds=s))
        t += pd.Timedelta(minutes=1)


@pytest.fixture
def hist():
    return prior_sessions()


def test_session_trades_the_guide_day_end_to_end(hist):
    db = FakeDB()
    feed = FakeFeed(guide_day(float(hist["close"].iloc[-1]), minutes=60), hist)
    sess = session(feed, db)
    run_minutes(sess, "09:31", "10:29")
    opened = db[US.positions_collection].inserted
    assert opened, f"no US entry. Rejections: {[d['reason'] for d in db[US.candidates_collection].inserted if d['kind'] == 'rejection']}"
    doc = opened[0]
    assert doc["market"] == "US" and doc["currency"] == "USD" and doc["paper"] is True
    assert doc["risk_usd"] == pytest.approx(50.0, rel=0.02)
    assert 0 < doc["cost_over_risk"] <= 0.25
    assert doc["float_shares"] == 4e6 and doc["screen_flags"]["float_filter_applied"] == 1
    assert doc["entry_time"].time() < time(10, 1)


def test_a_stock_found_late_is_never_entered_in_the_past(hist, monkeypatch):
    """Discovered at 10:12 with bars back to 09:30: the engine must only ever
    see windows ending AFTER discovery. Replaying earlier bars would enter a
    stock the screen had not found yet."""
    seen: list[pd.Timestamp] = []
    real_step = us_session_mod.step

    def spy(st, window, *a, **k):
        seen.append(window.index[-1])
        return real_step(st, window, *a, **k)

    monkeypatch.setattr(us_session_mod, "step", spy)
    feed = FakeFeed(guide_day(float(hist["close"].iloc[-1]), minutes=90), hist)
    sess = session(feed)
    found_at = pd.Timestamp(f"{DAY} 10:12:02", tz=ET)
    sess.cycle(found_at)                                   # discovery
    assert "USX" in sess.watch and not seen                # nothing replayed
    sess.cycle(found_at + pd.Timedelta(minutes=1))
    assert seen and min(seen) >= found_at - pd.Timedelta(seconds=60)


def test_discovered_without_bars_still_never_replays(hist, monkeypatch):
    seen: list[pd.Timestamp] = []
    monkeypatch.setattr(us_session_mod, "step",
                        lambda st, w, *a, **k: seen.append(w.index[-1]))
    feed = FakeFeed(guide_day(float(hist["close"].iloc[-1]), minutes=90), hist, bars_ok=False)
    sess = session(feed)
    found_at = pd.Timestamp(f"{DAY} 10:12:02", tz=ET)
    sess.cycle(found_at)
    feed.bars_ok = True                                    # the feed recovers
    sess.cycle(found_at + pd.Timedelta(minutes=1))
    assert seen and min(seen) > found_at - pd.Timedelta(seconds=60)


def test_discovery_builds_the_volume_profile_and_warmup(hist):
    feed = FakeFeed(guide_day(float(hist["close"].iloc[-1])), hist)
    sess = session(feed)
    w = sess.discover("USX", pd.Timestamp(f"{DAY} 09:40", tz=ET))
    assert w is not None and w.has_profile
    assert w.state.prev_close == feed.prev
    assert w.state.warmup_1m is not None and w.state.warmup_5m is not None


def test_no_history_means_watched_but_never_entered_and_says_so(hist):
    """The engine's attention path refuses every entry without a profile."""
    feed = FakeFeed(guide_day(float(hist["close"].iloc[-1])), hist)
    feed.history_1m = lambda *a, **k: hist.iloc[0:0]
    w = session(feed).discover("USX", pd.Timestamp(f"{DAY} 09:40", tz=ET))
    assert w is not None and not w.has_profile


def test_a_halted_stock_does_not_fill(hist):
    feed = FakeFeed(guide_day(float(hist["close"].iloc[-1]), minutes=60), hist)
    db = FakeDB()
    sess = session(feed, db)
    run_minutes(sess, "09:31", "09:54")                    # up to the arming bar
    feed._halted = {"USX"}                                 # LULD pause before any fill
    run_minutes(sess, "09:55", "09:59")
    assert not db[US.positions_collection].inserted


def test_end_of_day_sweep_closes_an_open_position_in_dollars(hist):
    db = FakeDB()
    feed = FakeFeed(guide_day(float(hist["close"].iloc[-1]), minutes=60), hist)
    sess = session(feed, db)
    run_minutes(sess, "09:31", "10:29")
    assert db[US.positions_collection].inserted
    # The fixture stops printing at 10:29, so only the wall-clock sweep can close it.
    sess.cycle(pd.Timestamp(f"{DAY} 15:56:02", tz=ET))
    closes = db[US.positions_collection].updates
    assert closes, "the sweep did not close a position that stopped printing"
    fields = closes[-1][1]["$set"]
    assert fields["status"] == "closed" and "net_usd" in fields and "net_inr" not in fields
    assert fields["exit_reason"] in ("eod_sweep", "eod_close")


def test_watchlist_is_one_document_per_session_replaced_each_cycle(hist):
    db = FakeDB()
    feed = FakeFeed(guide_day(float(hist["close"].iloc[-1]), minutes=60), hist)
    sess = session(feed, db)
    run_minutes(sess, "09:31", "09:33", poll=False)
    writes = db[US.watchlist_collection].replaced
    assert len(writes) == 3
    flt, doc, upsert = writes[-1]
    assert upsert and flt == {"market": "US", "date": str(DAY)}
    assert doc["passed"] == 1 and doc["names"][0]["symbol"] == "USX"


def test_watched_names_bars_are_saved_once_per_new_bar(hist):
    db = FakeDB()
    feed = FakeFeed(guide_day(float(hist["close"].iloc[-1]), minutes=60), hist)
    sess = session(feed, db)
    run_minutes(sess, "09:31", "09:33", poll=False)
    writes = db[WATCH_BARS_COLLECTION].replaced
    assert writes, "a watched name's bars were never saved"
    flt, doc, upsert = writes[-1]
    assert upsert and flt == {"market": "US", "date": str(DAY), "symbol": "USX"}
    assert doc["interval"] == "1m"
    assert doc["bars"] == chart_bars_doc(sess.bars["USX"])
    # A cycle with no new bar must not rewrite the document.
    before = len(writes)
    sess._write_watchlist(pd.Timestamp(f"{DAY} 09:33:30", tz=ET))
    assert len(writes) == before


def test_session_refuses_an_nse_config():
    with pytest.raises(ValueError, match="market=US"):
        USSession(FakeFeed(prior_sessions().iloc[:10], prior_sessions()),
                  _strategy_config(Settings()), USPaperLedger(None, "x"))


def test_holiday_exits_without_touching_the_network():
    thanksgiving = pd.Timestamp("2026-11-26 09:20", tz=ET)
    assert us_session_mod.run(Settings(), dry_run=True, clock=lambda: thanksgiving) == 0



def test_telegram_says_when_a_stock_joins_the_watchlist_and_when_it_gets_attention(hist):
    sent: list[str] = []
    feed = FakeFeed(guide_day(float(hist["close"].iloc[-1]), minutes=60), hist)
    sess = USSession(feed, us_cfg(), USPaperLedger(FakeDB(), "us_test"), notify=sent.append)
    run_minutes(sess, "09:31", "10:29")
    added = [m for m in sent if "added to watchlist" in m]
    assert len(added) == 1 and "<b>USX</b>" in added[0], sent    # once, not every cycle
    assert "float 4.0M" in added[0] and "RVOL 8.0x" in added[0]
    first_attention = next(i for i, m in enumerate(sent) if m.startswith("👀"))
    assert sent.index(added[0]) < first_attention
    assert any(m.startswith("🟢 <b>ENTER USX") for m in sent)
