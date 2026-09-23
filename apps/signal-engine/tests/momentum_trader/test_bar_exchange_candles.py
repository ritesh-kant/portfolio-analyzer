"""The exchange's own 1-minute candle beats the snapshot-built bar.

Found on 2026-09-23 from an IKS review: FULL-mode messages are periodic
snapshots, so bars built from their `ltp` miss the wicks and open late. On the
official candles the day's 09:34 entry never forms.
"""

from __future__ import annotations

import pandas as pd
from src.momentum_trader.bars import IST, BarBuilder
from src.momentum_trader.upstox import _route_candles

KEY = "NSE_EQ|INE115Q01022"  # IKS


def _ms(ts: str) -> int:
    return int(pd.Timestamp(ts, tz=IST).timestamp() * 1000)


def _snapshot_0933(b: BarBuilder) -> None:
    """The live 09:33 IKS bar: open 1923.0, close 1923.2 — a doji pause."""
    b.on_tick(KEY, _ms("2026-09-23 09:32:59"), 1922.7, 100_000)
    b.on_tick(KEY, _ms("2026-09-23 09:33:05"), 1923.0, 100_500)
    b.on_tick(KEY, _ms("2026-09-23 09:33:30"), 1921.4, 101_500)
    b.on_tick(KEY, _ms("2026-09-23 09:33:40"), 1924.4, 102_500)
    b.on_tick(KEY, _ms("2026-09-23 09:33:58"), 1923.2, 103_416)


def test_the_exchange_candle_replaces_the_snapshot_bar() -> None:
    b = BarBuilder()
    _snapshot_0933(b)
    b.on_candle(KEY, _ms("2026-09-23 09:33"), 1922.5, 1925.0, 1920.3, 1923.2, 3008)
    bar = b.closed_bars(KEY, pd.Timestamp("2026-09-23 09:34:02", tz=IST)).iloc[-1]
    assert tuple(bar) == (1922.5, 1925.0, 1920.3, 1923.2, 3008.0)
    # the official body is 15% of the range: a green push, not a doji pause
    assert (bar["close"] - bar["open"]) / (bar["high"] - bar["low"]) > 0.10


def test_the_latest_candle_for_a_minute_wins() -> None:
    b = BarBuilder()
    b.on_candle(KEY, _ms("2026-09-23 09:33"), 1922.5, 1923.0, 1922.0, 1922.8, 900)
    b.on_candle(KEY, _ms("2026-09-23 09:33"), 1922.5, 1925.0, 1920.3, 1923.2, 3008)
    bars = b.closed_bars(KEY, pd.Timestamp("2026-09-23 09:34:02", tz=IST))
    assert len(bars) == 1
    assert float(bars["high"].iloc[0]) == 1925.0


def test_minutes_without_a_candle_fall_back_to_the_snapshot_bar() -> None:
    b = BarBuilder()
    b.on_tick(KEY, _ms("2026-09-23 09:32:10"), 1923.0, 100_000)
    b.on_tick(KEY, _ms("2026-09-23 09:32:50"), 1922.7, 100_000)
    _snapshot_0933(b)
    b.on_candle(KEY, _ms("2026-09-23 09:33"), 1922.5, 1925.0, 1920.3, 1923.2, 3008)
    bars = b.closed_bars(KEY, pd.Timestamp("2026-09-23 09:34:02", tz=IST))
    assert list(bars.index) == [pd.Timestamp("2026-09-23 09:32", tz=IST),
                                pd.Timestamp("2026-09-23 09:33", tz=IST)]
    assert float(bars["close"].iloc[0]) == 1922.7
    assert b.candle_coverage(KEY, pd.Timestamp("2026-09-23 09:34:02", tz=IST)) == (1, 2)


def test_candles_obey_the_session_window() -> None:
    """The first snapshot carries yesterday's 15:29 candle; the forming minute is unfinished."""
    b = BarBuilder()
    b.on_candle(KEY, _ms("2026-09-22 15:29"), 1878.0, 1880.0, 1877.0, 1879.0, 2000)
    b.on_candle(KEY, _ms("2026-09-23 09:15"), 1881.2, 1887.6, 1874.8, 1880.5, 3201)
    b.on_candle(KEY, _ms("2026-09-23 09:16"), 1881.9, 1884.4, 1875.0, 1876.5, 1200)
    bars = b.closed_bars(KEY, pd.Timestamp("2026-09-23 09:16:30", tz=IST))
    assert list(bars.index) == [pd.Timestamp("2026-09-23 09:15", tz=IST)]


def test_the_feed_parser_routes_only_i1_candles() -> None:
    """Shape recorded from the live V3 feed on 2026-09-23 (int64s arrive as strings)."""
    market_ff = {"marketOHLC": {"ohlc": [
        {"interval": "1d", "open": 1881.2, "high": 1970.0, "low": 1871.0,
         "close": 1937.8, "vol": "1011998", "ts": "1790101800000"},
        {"interval": "I1", "open": 1922.2, "high": 1927.8, "low": 1921.0,
         "close": 1927.8, "vol": "2244", "ts": "1790157540000"},
        {"interval": "I1", "open": "bad"},
    ]}}
    got: list[tuple] = []
    _route_candles(KEY, market_ff, lambda *a: got.append(a))
    assert got == [(KEY, 1790157540000, 1922.2, 1927.8, 1921.0, 1927.8, 2244.0)]
    assert pd.Timestamp(got[0][1], unit="ms", tz="UTC").tz_convert(IST) == \
        pd.Timestamp("2026-09-23 15:29", tz=IST)


def test_a_message_without_market_ohlc_is_harmless() -> None:
    got: list[tuple] = []
    _route_candles(KEY, {"ltpc": {"ltp": 1.0}}, lambda *a: got.append(a))
    assert got == []
