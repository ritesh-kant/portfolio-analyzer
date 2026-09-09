"""Offline regressions for the concrete failures found in scanner review."""

from __future__ import annotations

import io
import logging
import threading
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest
from src.momentum_trader import engine, scanner, universe
from src.momentum_trader.catalyst import hard_catalyst_detail
from src.momentum_trader.exits import MODE_TREND_FULL
from src.momentum_trader.ledger import PaperLedger
from src.momentum_trader.setups import Setup
from src.news_trader import telegram


def stamp(clock: str) -> pd.Timestamp:
    return pd.Timestamp(f"2026-09-08 {clock}", tz="Asia/Kolkata")


def pending(stop: float = 99) -> engine.DayState:
    state = engine.DayState("TEST", 95, None)
    state.pending = engine.Pending(engine.Candidate(
        "TEST", stamp("09:59"), Setup("ma9_pullback", 100, stop), 5, 3, 1, "earnings", [],
        decision_time=stamp("10:00:02"),
    ))
    return state


def bars(start: str = "09:59") -> pd.DataFrame:
    return pd.DataFrame([(100, 102, 98, 100, 100)],
                        index=pd.DatetimeIndex([stamp(start)]),
                        columns=["open", "high", "low", "close", "volume"])


def config() -> engine.EngineConfig:
    return engine.EngineConfig(fill_mode=engine.FILL_OBSERVED_QUOTE, exit_mode=MODE_TREND_FULL)


def fill(state: engine.DayState, exchange: str = "10:00:03", receipt: str = "10:00:03",
         price: float = 100) -> engine.Position | None:
    return engine.fill_pending_from_quote(state, exchange_time=stamp(exchange),
        receipt_time=stamp(receipt), price=price, cfg=config(), bars_1m=bars())


@pytest.mark.parametrize("exchange,receipt", [
    ("09:00", "10:00:03"), ("10:00:02", "10:00:03"),
    ("10:00:04", "10:00:03"), ("10:00:03", "10:00:09"),
    ("10:01", "10:01"), ("14:30", "14:30"), ("15:20", "15:20"),
])
def test_stale_predecision_future_and_expired_fills_rejected(exchange, receipt):
    state = pending()
    assert fill(state, exchange, receipt) is None
    assert state.position is None


@pytest.mark.parametrize("price", [float("nan"), float("inf"), 0, -1])
def test_invalid_quote_cannot_fill(price):
    assert fill(pending(), price=price) is None


def test_entry_bar_cannot_retroactively_exit_but_fresh_stop_tick_can():
    state = pending()
    position = fill(state)
    assert position is not None
    assert not position.has_target
    engine.step(state, bars("10:00"), config(), lambda *_: (1, "earnings"),
                decision_time=stamp("10:01:02"))
    assert state.position is position
    assert not state.closed
    # A gap below the stop uses the actual observed price, not the stop level.
    engine.manage_position_from_quote(state, exchange_time=stamp("10:01:03"),
        receipt_time=stamp("10:01:03"), price=98.5, cfg=config())
    trade = state.closed[0]
    assert trade.exit_time > trade.entry_time
    assert trade.exit == 98.5
    assert trade.target is None


def test_exit_intent_waits_for_postdecision_quote():
    state = pending()
    position = fill(state)
    assert position is not None
    position.pending_exit = ("ema9_break", stamp("10:05:02"))
    for exchange in ("10:05:01", "10:05:02"):
        engine.manage_position_from_quote(state, exchange_time=stamp(exchange),
            receipt_time=stamp("10:05:03"), price=100, cfg=config())
        assert not state.closed
    engine.manage_position_from_quote(state, exchange_time=stamp("10:05:03"),
        receipt_time=stamp("10:05:03"), price=100.1, cfg=config())
    assert state.closed[0].exit_reason == "ema9_break"
    assert state.closed[0].exit == 100.1


def test_eod_requires_fresh_observation():
    state = pending()
    assert fill(state) is not None
    engine.manage_position_from_quote(state, exchange_time=stamp("15:00"),
        receipt_time=stamp("15:15"), price=100, cfg=config())
    assert not state.closed
    engine.manage_position_from_quote(state, exchange_time=stamp("15:15"),
        receipt_time=stamp("15:15"), price=100, cfg=config())
    assert state.closed[0].exit_reason == "eod_close"


def test_no_fixed_target_exit_for_trend_mode():
    state = pending()
    assert fill(state) is not None
    engine.manage_position_from_quote(state, exchange_time=stamp("10:00:04"),
        receipt_time=stamp("10:00:04"), price=110, cfg=config())
    assert not state.closed


def test_existing_position_cannot_be_overwritten():
    state = pending()
    position = fill(state)
    state.pending = pending().pending
    assert fill(state) is None
    assert state.position is position


def test_capital_admission_uses_quote_not_trigger():
    state = pending(stop=98)
    worker = object.__new__(scanner.Scanner)
    worker.cfg = config()
    worker.states = {"TEST": state}
    worker.s = SimpleNamespace(mt_max_positions=5, mt_total_capital_inr=30000,
                               mt_daily_loss_limit_inr=2500)
    assert worker._admit_pending(state) == (True, "ok")  # 25,000 at trigger
    assert worker._admit_pending(state, price=99) == (False, "capital_limit")  # 49,500


def test_daily_loss_budget_includes_exit_costs():
    state = pending()
    worker = object.__new__(scanner.Scanner)
    worker.cfg = config()
    worker.states = {"TEST": state}
    worker.s = SimpleNamespace(mt_max_positions=5, mt_total_capital_inr=100000,
                               mt_daily_loss_limit_inr=500)
    assert worker._admit_pending(state, price=100) == (False, "daily_loss_limit")


def test_stale_bar_does_not_generate_live_candidate(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("expired bar reached setup detection")

    monkeypatch.setattr(engine, "scan_setups", unexpected)
    state = engine.DayState("TEST", 95, pd.Series([10], index=[stamp("09:59").time()]))
    engine.step(state, bars(), config(), lambda *_: (1, "earnings"),
                decision_time=stamp("10:05:02"))
    assert not state.candidates


def test_scanner_does_not_process_preopen_bars(monkeypatch):
    state = engine.DayState("TEST", 95, None)
    worker = object.__new__(scanner.Scanner)
    worker._ledger = SimpleNamespace(acquire_session=lambda *_: True)
    worker._session_id = "test"
    worker._lease_owner = "owner"
    worker._state_lock = threading.RLock()
    worker._stop = threading.Event()
    worker.builder = SimpleNamespace(closed_bars=lambda *_: bars("09:10"))
    worker.states = {"TEST": state}
    called = []
    monkeypatch.setattr(scanner, "step", lambda *args, **kwargs: called.append(True))
    worker._process(stamp("09:16"))
    assert not called


def test_cutoff_rejects_even_unexpired_candidate():
    state = pending()
    state.pending.cand.time = stamp("14:29")
    state.pending.cand.decision_time = stamp("14:30:00")
    assert fill(state, "14:30:01", "14:30:01") is None
    assert state.pending is None


def test_telegram_httpx_logs_redact_token_without_network(monkeypatch):
    token = "synthetic-only-credential"
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    client_logger = logging.getLogger("httpx")
    old_level = client_logger.level
    client_logger.setLevel(logging.INFO)
    client_logger.addHandler(handler)
    real_client = httpx.Client
    monkeypatch.setattr(telegram.httpx, "Client", lambda **kw: real_client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, request=req)), **kw))
    try:
        telegram._send(token, "fake-chat", "offline test")
    finally:
        client_logger.removeHandler(handler)
        client_logger.setLevel(old_level)
    assert token not in output.getvalue()
    assert "[REDACTED]" in output.getvalue()


def test_blank_surveillance_passes_but_missing_column_does_not(tmp_path):
    path = tmp_path / "facts.csv"
    path.write_text("symbol,free_float_mcap_cr,promoter_pct,band_pct,series,surveillance\n"
                    "TEST,1000,60,10,EQ,\n")
    assert universe.passes_static(universe.load_facts(path)["TEST"], require_facts=True)[0]
    path.write_text("symbol,free_float_mcap_cr,promoter_pct,band_pct,series\n"
                    "TEST,1000,60,10,EQ\n")
    assert not universe.passes_static(universe.load_facts(path)["TEST"], require_facts=True)[0]


def test_empty_catalyst_query_is_not_verified_absence():
    signals = SimpleNamespace(find_one=lambda *a, **kw: None)
    assert hard_catalyst_detail(signals, "TEST", stamp("10:00")).status == "unknown"


def test_classifier_creation_time_is_not_mislabeled_as_ingestion():
    signals = SimpleNamespace(find_one=lambda *a, **kw: {
        "_id": "test", "event_type": "earnings", "created_at": stamp("09:00"),
    })
    result = hard_catalyst_detail(signals, "TEST", stamp("10:00"))
    assert result.classified_at == stamp("09:00")
    assert result.ingested_at is None


class Collection:
    def __init__(self):
        self.docs = {}
        self.fail = False

    def create_index(self, *args, **kwargs):
        pass

    def update_one(self, query, update, **kwargs):
        if self.fail:
            raise OSError("synthetic DB failure")
        key = next(iter(query.values()))
        if key in self.docs:
            doc = self.docs[key]
            if any(doc.get(k) != v for k, v in query.items()):
                raise ValueError("duplicate key")
            doc.update(update.get("$set", {}))
        else:
            self.docs[key] = {**query, **update.get("$setOnInsert", {}), **update.get("$set", {})}

    def count_documents(self, query):
        return sum(all(d.get(k) == v for k, v in query.items()) for d in self.docs.values())

    def delete_one(self, query):
        for key, doc in list(self.docs.items()):
            if all(doc.get(k) == v for k, v in query.items()):
                del self.docs[key]


class Database(dict):
    def __missing__(self, key):
        self[key] = Collection()
        return self[key]


def test_guard_never_expires_or_resets_daily_risk():
    db = Database()
    ledger = PaperLedger(db, None, True)
    assert ledger.acquire_session("2026-09-08:baseline", "A", ttl_seconds=-1)
    assert ledger.acquire_session("2026-09-08:baseline", "A")
    assert not ledger.acquire_session("2026-09-08:baseline", "B")
    assert not ledger.acquire_session("2026-09-09:baseline", "B")
    ledger.release_session("2026-09-08:baseline", "B")
    assert "paper" in db["mt_scanner_guard"].docs
    ledger.release_session("2026-09-08:baseline", "A")
    # Even after a clean release, changing strategy cannot reset daily losses.
    assert not ledger.acquire_session("2026-09-08:other", "B")


def test_prior_day_unresolved_position_blocks_new_run():
    db = Database()
    db["mt_positions"].docs["old"] = {"status": "open", "time": stamp("09:20")}
    assert PaperLedger(db, None, True).has_open_positions_for_day(
        pd.Timestamp("2026-09-09").to_pydatetime())


def test_mongo_failure_propagates_and_no_csv_success_is_written(tmp_path):
    db = Database()
    path = tmp_path / "ledger.csv"
    ledger = PaperLedger(db, path, True)
    state = pending()
    position = fill(state)
    assert position is not None
    db["mt_positions"].fail = True
    with pytest.raises(RuntimeError, match="entry persistence"):
        ledger.opened(position)
    engine.manage_position_from_quote(state, exchange_time=stamp("10:00:04"),
        receipt_time=stamp("10:00:04"), price=98, cfg=config())
    with pytest.raises(RuntimeError, match="exit persistence"):
        ledger.closed(state.closed[0])
    assert len(path.read_text().splitlines()) == 1


def test_callback_persistence_failure_stops_scanner_before_alert(monkeypatch):
    worker = object.__new__(scanner.Scanner)
    worker.cfg = config()
    worker._stop = threading.Event()
    worker._state_lock = threading.RLock()
    worker._fatal_error = False
    worker.states = {"TEST": pending()}
    worker.s = SimpleNamespace(mt_max_positions=5, mt_total_capital_inr=100000,
                               mt_daily_loss_limit_inr=2500)
    worker.builder = SimpleNamespace(on_tick=lambda *_: None, closed_bars=lambda *_: bars())
    db = Database()
    worker._ledger = PaperLedger(db, None, True)
    db["mt_positions"].fail = True
    alerts = []
    worker._tg = alerts.append
    monkeypatch.setattr(scanner, "_now", lambda: stamp("10:00:03"))
    worker._on_tick("TEST", int(stamp("10:00:03").timestamp() * 1000), 100, 100)
    assert worker._stop.is_set()
    assert worker._fatal_error
    assert not alerts


def test_open_ledger_has_no_target_in_trend_mode():
    db = Database()
    position = fill(pending())
    assert position is not None
    PaperLedger(db, None, True).opened(position)
    assert next(iter(db["mt_positions"].docs.values()))["target"] is None


@pytest.mark.parametrize("failure", [
    "unresolved", "summary", "stream", "interrupt", "feed", "none",
])
def test_run_reports_failures_and_retains_guard(monkeypatch, failure):
    worker = object.__new__(scanner.Scanner)
    worker.s = SimpleNamespace(mt_bypass_market_hours=False, mt_strategy="baseline")
    worker._stop = threading.Event()
    worker._state_lock = threading.RLock()
    worker._fatal_error = False
    worker._feed_status = "error" if failure == "feed" else "open"
    worker._session_id = "2026-09-08:baseline"
    worker._lease_owner = "A"
    worker._tg = lambda *_: None
    state = pending()
    state.pending = None
    worker.states = {"TEST": state}
    if failure == "unresolved":
        state.position = fill(pending())
    db = Database()
    worker._ledger = PaperLedger(db, None, True)
    if failure == "summary":
        db["mt_sessions"].fail = True
    worker.prepare = lambda *_: ["TEST"]
    worker.builder = SimpleNamespace(seed=lambda *_: None)

    def stream(*args, **kwargs):
        if failure == "stream":
            raise OSError("synthetic stream failure")
        if failure == "interrupt":
            worker._stop.set()
        return SimpleNamespace(disconnect=lambda: None)

    worker.client = SimpleNamespace(token="synthetic", intraday_1m=lambda *_: bars(), stream=stream)
    monkeypatch.setattr(scanner, "_now", lambda: stamp("15:35"))
    monkeypatch.setattr(scanner, "is_trading_day", lambda *_: True)
    monkeypatch.setattr(scanner.signal, "signal", lambda *_: None)
    assert worker.run() == (0 if failure == "none" else 7)
    assert ("paper" in db["mt_scanner_guard"].docs) == (failure != "none")
