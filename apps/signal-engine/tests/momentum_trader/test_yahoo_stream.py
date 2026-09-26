"""Yahoo push stream: bars, fills, gaps, and how the feed and session use it.
No network — the socket is a fake and the clock is a variable."""

from __future__ import annotations

import threading

import pandas as pd
import pytest
from src.momentum_trader.yahoo_feed import YahooFeed
from src.momentum_trader.yahoo_stream import HEARTBEAT, YahooStream, compare_bars

ET = "America/New_York"
T0 = pd.Timestamp("2026-09-25 10:30:00", tz=ET).timestamp()   # a minute boundary


def msg(sym: str, price: float, t: float, dv: float | None = None, hours: int = 1) -> dict:
    out = {"id": sym, "price": price, "time": str(int(t) * 1000), "market_hours": hours}
    if dv is not None:
        out["day_volume"] = str(int(dv))
    return out


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def stream_at(t: float, **kw) -> tuple[YahooStream, Clock]:
    clock = Clock(t)
    s = YahooStream(ws_factory=lambda: None, clock=clock, **kw)
    s._connected = True
    return s, clock


def feed_in(s: YahooStream, clock: Clock, rx: float, m: dict) -> None:
    clock.t = rx
    s.on_message(m)


def et_min(sec_after_t0: float) -> pd.Timestamp:
    return pd.Timestamp(T0 + sec_after_t0, unit="s", tz="UTC").tz_convert(ET)


# ── bars ────────────────────────────────────────────────────────────────────
def test_first_message_is_only_a_baseline_and_its_minute_is_never_served():
    s, c = stream_at(T0 + 20)
    feed_in(s, c, T0 + 20, msg("ABC", 5.00, T0 + 19, 1_000))      # subscribed mid-minute
    feed_in(s, c, T0 + 40, msg("ABC", 5.05, T0 + 39, 1_500))      # rest of 10:30
    feed_in(s, c, T0 + 61, msg("ABC", 5.10, T0 + 60, 2_000))      # 10:31 opens
    feed_in(s, c, T0 + 90, msg("ABC", 5.02, T0 + 89, 2_600))
    bars = s.closed_bars("ABC", T0 + 125)
    assert list(bars.index) == [et_min(60)]                     # 10:30 is incomplete
    b = bars.iloc[0]
    assert (b.open, b.high, b.low, b.close) == (5.10, 5.10, 5.02, 5.02)


def test_a_minute_is_served_only_after_it_closes_plus_settle():
    s, c = stream_at(T0 - 30, settle_seconds=3.0)
    feed_in(s, c, T0 - 30, msg("ABC", 5.0, T0 - 31, 100))
    feed_in(s, c, T0 + 10, msg("ABC", 5.1, T0 + 9, 200))
    assert s.closed_bars("ABC", T0 + 62).empty          # closed 2 s ago: not final
    assert len(s.closed_bars("ABC", T0 + 63)) == 1


def test_volume_only_messages_add_volume_but_are_not_trades():
    """Measured: day_volume keeps rising while `time` and `price` sit still
    (odd lots, non-last-sale prints). They are volume, not a new price."""
    s, c = stream_at(T0 - 30)
    feed_in(s, c, T0 - 30, msg("ABC", 5.0, T0 - 31, 1_000))
    feed_in(s, c, T0 + 5, msg("ABC", 5.2, T0 + 4, 1_100))
    for k, dv in enumerate([1_150, 1_300, 1_320]):
        feed_in(s, c, T0 + 20 + k, msg("ABC", 5.2, T0 + 4, dv))   # same time + price
    assert len(s.drain("ABC")) == 1
    bar = s.closed_bars("ABC", T0 + 70).iloc[0]
    assert bar.volume == 320 and bar.high == 5.2


def test_volume_before_a_minutes_first_trade_is_held_for_that_minute():
    """Opening a bar at the previous close because shares printed first made
    every stream open wrong (DELL, 2026-09-25: 570.765 vs REST 570.957)."""
    s, c = stream_at(T0 - 30)
    feed_in(s, c, T0 - 30, msg("ABC", 5.00, T0 - 31, 1_000))
    feed_in(s, c, T0 + 2, msg("ABC", 5.00, T0 - 31, 1_400))       # volume, no sale yet
    feed_in(s, c, T0 + 8, msg("ABC", 5.07, T0 + 7, 1_500))        # the minute's first sale
    bar = s.closed_bars("ABC", T0 + 70).iloc[0]
    assert (bar.open, bar.low, bar.volume) == (5.07, 5.07, 500)


def test_a_minute_with_volume_and_no_new_sale_is_a_flat_bar():
    s, c = stream_at(T0 - 30)
    feed_in(s, c, T0 - 30, msg("ABC", 5.00, T0 - 31, 1_000))
    feed_in(s, c, T0 + 5, msg("ABC", 5.10, T0 + 4, 1_100))        # 10:30 trades
    feed_in(s, c, T0 + 75, msg("ABC", 5.10, T0 + 4, 1_900))       # 10:31: volume only
    bars = s.closed_bars("ABC", T0 + 130)
    assert list(bars.index) == [et_min(0), et_min(60)]
    flat = bars.iloc[1]
    assert (flat.open, flat.high, flat.low, flat.close, flat.volume) == (5.1, 5.1, 5.1, 5.1, 800)


def test_out_of_order_and_extended_hours_prints_are_ignored():
    s, c = stream_at(T0 - 30)
    feed_in(s, c, T0 - 30, msg("ABC", 5.0, T0 - 31, 100))
    feed_in(s, c, T0 + 61, msg("ABC", 5.3, T0 + 60, 200))         # 10:31
    feed_in(s, c, T0 + 62, msg("ABC", 9.9, T0 + 30, 300))         # late print for 10:30
    feed_in(s, c, T0 + 63, msg("ABC", 0.1, T0 + 62, 400, hours=2))   # post-market
    assert [t.price for t in s.drain("ABC")] == [5.3]
    assert s.closed_bars("ABC", T0 + 200)["high"].max() == 5.3


def test_a_disconnect_leaves_the_minutes_it_spanned_out():
    s, c = stream_at(T0 - 30)
    feed_in(s, c, T0 - 30, msg("ABC", 5.0, T0 - 31, 100))
    feed_in(s, c, T0 + 10, msg("ABC", 5.1, T0 + 9, 200))          # 10:30 ok
    feed_in(s, c, T0 + 70, msg("ABC", 5.2, T0 + 69, 300))         # 10:31 ...
    c.t = T0 + 80
    s._on_drop()                                                  # ... dropped mid-minute
    assert not s.healthy() and s.closed_bars("ABC", T0 + 200).empty
    s._connected = True
    feed_in(s, c, T0 + 150, msg("ABC", 5.4, T0 + 149, 900))       # back in 10:32
    feed_in(s, c, T0 + 185, msg("ABC", 5.5, T0 + 184, 1_000))     # 10:33
    bars = s.closed_bars("ABC", T0 + 250)
    assert list(bars.index) == [et_min(0), et_min(180)]           # 10:31, 10:32 gone
    assert bars.iloc[1].volume == 100                             # baseline reset, no gap volume


def test_healthy_needs_a_recent_message():
    s, c = stream_at(T0, stale_after=10)
    assert not s.healthy()
    feed_in(s, c, T0, msg(HEARTBEAT, 700.0, T0 - 1, 1))
    assert s.healthy()
    c.t = T0 + 11
    assert not s.healthy()


def test_drain_returns_every_trade_in_order_at_its_trade_time_then_empties():
    s, c = stream_at(T0 - 30)
    feed_in(s, c, T0 - 30, msg("ABC", 5.0, T0 - 31))
    feed_in(s, c, T0 + 2, msg("ABC", 5.1, T0 + 1))
    feed_in(s, c, T0 + 3, msg("ABC", 4.9, T0 + 2))
    trades = s.drain("ABC")
    assert [(t.at, t.price) for t in trades] == [(et_min(1), 5.1), (et_min(2), 4.9)]
    assert s.drain("ABC") == []


# ── the socket thread ───────────────────────────────────────────────────────
class FakeWS:
    def __init__(self, script: list[dict], done: threading.Event) -> None:
        self.script, self.done = script, done
        self.subscribed: list[list[str]] = []
        self.closed = False

    def subscribe(self, symbols):
        self.subscribed.append(list(symbols))

    def listen(self, handler):
        for m in self.script:
            handler(m)
        self.done.set()
        raise ConnectionError("server closed")

    def close(self):
        self.closed = True


class LiveWS(FakeWS):
    """A socket that stays up until closed. `listen` runs only after the stream
    has counted the connect (and every earlier drop), so `done` marks a moment
    when those counts are final."""

    def __init__(self, done: threading.Event) -> None:
        super().__init__([], done)
        self._closed = threading.Event()

    def listen(self, handler):
        self.done.set()
        self._closed.wait(5)

    def close(self):
        self.closed = True
        self._closed.set()


def test_run_subscribes_heartbeat_and_reconnects_after_a_drop(monkeypatch):
    monkeypatch.setattr("src.momentum_trader.yahoo_stream.RECONNECT_BACKOFF", (0.01,))
    # Stop only once the stream is back up after the second drop: stopping
    # while a drop is still unwinding would (rightly) not count it as one.
    live = threading.Event()
    sockets = [FakeWS([msg("ABC", 5.0, T0)], threading.Event()),
               FakeWS([], threading.Event()), LiveWS(live)]
    made: list[FakeWS] = []

    def factory():
        ws = sockets[len(made)]
        made.append(ws)
        return ws

    s = YahooStream(ws_factory=factory)
    s.subscribe(["abc"])
    s.start()
    assert live.wait(2)
    s.stop()
    assert [ws.subscribed[0] for ws in made] == [["ABC", HEARTBEAT]] * 3   # full resubscribe
    assert s.connects == 3
    assert s.drops == 2                                      # our own stop is not a drop
    assert made[2].closed
    assert s.has_seen("ABC")


def test_subscribing_on_a_live_socket_sends_only_the_new_names():
    s, _ = stream_at(T0)
    ws = FakeWS([], threading.Event())
    s._ws = ws
    s.subscribe(["ABC", "SPY"])
    s.subscribe(["ABC"])
    assert ws.subscribed == [["ABC"]]


# ── the feed ────────────────────────────────────────────────────────────────
def rest_frame(minutes: list[str], price: float = 5.0) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(f"2026-09-25 {m}", tz=ET) for m in minutes])
    return pd.DataFrame({"Open": price, "High": price, "Low": price, "Close": price,
                         "Volume": 100.0}, index=idx)


def streamed_feed(stream_bars: bool = True):
    s, c = stream_at(T0 - 30)
    feed_in(s, c, T0 - 30, msg(HEARTBEAT, 700.0, T0 - 31, 1))
    feed_in(s, c, T0 - 30, msg("ABC", 5.0, T0 - 31, 100))
    feed_in(s, c, T0 + 10, msg("ABC", 5.1, T0 + 9, 200))          # 10:30
    feed_in(s, c, T0 + 70, msg("ABC", 5.2, T0 + 69, 300))         # 10:31
    feed_in(s, c, T0 + 124, msg(HEARTBEAT, 700.1, T0 + 123, 2))
    # REST at 10:32:05 has settled only through 10:30 (20 s rule)
    f = YahooFeed(history_fn=lambda _s: rest_frame(["10:29", "10:30", "10:31"]),
                  stream=s, stream_bars=stream_bars)
    return f, s, c


def test_rest_wins_what_it_has_settled_and_the_stream_adds_the_newest_minute():
    f, _, _ = streamed_feed()
    now = et_min(125)                                              # 10:32:05
    bars = f.bars_1m("ABC", now)
    assert list(bars.index.strftime("%H:%M")) == ["10:29", "10:30", "10:31"]
    assert bars.loc[et_min(0), "close"] == 5.0                     # REST's 10:30
    assert bars.loc[et_min(60), "close"] == 5.2                    # stream's 10:31
    assert f.ready_seconds() == 3.0
    assert f.describe()["stream_minutes_used"] == 1


def test_without_stream_bars_or_a_healthy_stream_it_is_rest_only():
    f, _, _ = streamed_feed(stream_bars=False)
    assert list(f.bars_1m("ABC", et_min(125)).index.strftime("%H:%M")) == ["10:29", "10:30"]
    assert f.ready_seconds() == f.settle_seconds
    f, s, c = streamed_feed()
    c.t = T0 + 200                                                 # stream silent 76 s
    assert not s.healthy()
    assert list(f.bars_1m("ABC", et_min(125)).index.strftime("%H:%M")) == ["10:29", "10:30"]
    assert f.ready_seconds() == f.settle_seconds


def test_fresh_prices_come_from_the_stream_when_it_is_healthy():
    f, _, _ = streamed_feed()
    f.quote_fn = lambda _s: pytest.fail("REST must not be polled while streaming")
    assert f.fresh_prices("ABC", et_min(125)) == [(et_min(9), 5.1), (et_min(69), 5.2)]


def test_fresh_prices_fall_back_to_a_rate_limited_rest_quote():
    f, s, c = streamed_feed()
    calls = []
    f.quote_fn = lambda sym: calls.append(sym) or 5.3
    # a name the stream has sent nothing for
    assert f.fresh_prices("NEW", et_min(125)) == [(et_min(125), 5.3)]
    assert f.fresh_prices("NEW", et_min(126)) == []                # < rest_poll_seconds
    # a dead stream
    c.t = T0 + 500
    assert f.fresh_prices("ABC", et_min(500)) == [(et_min(500), 5.3)]
    assert calls == ["NEW", "ABC"]


def test_delayed_quotes_stay_blocked_on_the_stream_too():
    f, _, _ = streamed_feed()
    f.quote_source["ABC"] = "Delayed Quote"
    assert f.fresh_prices("ABC", et_min(125)) == []


def test_watch_subscribes_and_no_stream_is_a_no_op():
    f, s, _ = streamed_feed()
    f.watch(["XYZ"])
    assert "XYZ" in s.symbols
    YahooFeed().watch(["XYZ"])                                     # no stream: fine


# ── measurement ─────────────────────────────────────────────────────────────
def test_compare_bars_counts_matches_low_highs_and_colour_flips():
    idx = pd.date_range("2026-09-25 10:30", periods=4, freq="1min", tz=ET)
    rest = pd.DataFrame({"open": [5.0, 5.1, 5.2, 5.3], "high": [5.2, 5.3, 5.4, 5.5],
                         "low": [4.9, 5.0, 5.1, 5.2], "close": [5.1, 5.2, 5.3, 5.2],
                         "volume": [100.0] * 4}, index=idx)
    stream = rest.copy()
    stream.loc[idx[1], "high"] = 5.25                              # missed the wick
    stream.loc[idx[3], "open"] = 5.1                               # red → green
    c = compare_bars(stream, rest)
    assert c["minutes_compared"] == 4
    assert c["high_match_pct"] == 75.0 and c["high_too_low_pct"] == 25.0
    assert c["colour_flip_pct"] == 25.0 and c["volume_ratio_median"] == 1.0


def test_stored_bars_round_trip_into_a_frame():
    from src.momentum_trader.ledger import chart_bars_doc
    from src.momentum_trader.yahoo_stream import bars_from_doc

    idx = pd.date_range("2026-09-25 10:30", periods=3, freq="1min", tz=ET)
    df = pd.DataFrame({"open": [1.0, 2, 3], "high": [1.5, 2.5, 3.5], "low": [0.5, 1.5, 2.5],
                       "close": [1.2, 2.2, 3.2], "volume": [10.0, 20, 30]}, index=idx)
    back = bars_from_doc(chart_bars_doc(df))
    pd.testing.assert_frame_equal(back, df, check_freq=False)
    assert bars_from_doc([]).empty
