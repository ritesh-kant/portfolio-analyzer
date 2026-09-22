"""The bar builder only ever hands out bars of the session being traded.

Found on 2026-09-22 from a RHIM review chart: every stored trade's bar series
began with the previous session's last trade and the pre-open auction print,
because the first FULL-mode snapshot carries its own `ltt`. Those two rows then
travelled into `engine.step()` with the real session.
"""

from __future__ import annotations

import pandas as pd
from src.momentum_trader.bars import IST, BarBuilder

KEY = "NSE_EQ|INE743M01012"


def _ms(ts: str) -> int:
    return int(pd.Timestamp(ts, tz=IST).timestamp() * 1000)


def _builder_with_the_real_2026_09_22_prologue() -> BarBuilder:
    """The exact shape of the stored RHIM trade: a stale snapshot, a pre-open
    print, then the session."""
    b = BarBuilder()
    b.on_tick(KEY, _ms("2026-09-21 15:56"), 367.00, 1_000_000)
    b.on_tick(KEY, _ms("2026-09-21 15:56"), 367.00, 1_015_666)
    b.on_tick(KEY, _ms("2026-09-22 09:09"), 392.60, 1_015_666)
    b.on_tick(KEY, _ms("2026-09-22 09:15"), 389.30, 1_385_391)
    b.on_tick(KEY, _ms("2026-09-22 09:16"), 391.00, 1_400_000)
    return b


def test_yesterdays_last_trade_never_reaches_the_engine() -> None:
    bars = _builder_with_the_real_2026_09_22_prologue().closed_bars(
        KEY, pd.Timestamp("2026-09-22 09:17", tz=IST)
    )
    assert list(bars.index) == [
        pd.Timestamp("2026-09-22 09:15", tz=IST),
        pd.Timestamp("2026-09-22 09:16", tz=IST),
    ]
    assert float(bars["low"].min()) == 389.30, "the 367.00 bar would be the day's low"


def test_the_pre_open_auction_print_is_not_a_bar() -> None:
    bars = _builder_with_the_real_2026_09_22_prologue().closed_bars(
        KEY, pd.Timestamp("2026-09-22 09:17", tz=IST)
    )
    assert pd.Timestamp("2026-09-22 09:09", tz=IST) not in bars.index


def test_the_unfinished_minute_is_still_excluded() -> None:
    """The upper bound this filter must not weaken."""
    bars = _builder_with_the_real_2026_09_22_prologue().closed_bars(
        KEY, pd.Timestamp("2026-09-22 09:16:30", tz=IST)
    )
    assert list(bars.index) == [pd.Timestamp("2026-09-22 09:15", tz=IST)]


def test_the_opening_bar_itself_is_kept() -> None:
    """09:15 is the first bar of the session, not a boundary to skip."""
    b = BarBuilder()
    b.on_tick(KEY, _ms("2026-09-22 09:15"), 389.30, 100.0)
    bars = b.closed_bars(KEY, pd.Timestamp("2026-09-22 09:16", tz=IST))
    assert list(bars.index) == [pd.Timestamp("2026-09-22 09:15", tz=IST)]


def test_a_rest_seed_of_a_prior_session_cannot_leak_in() -> None:
    """`seed()` is fed whatever `intraday_1m` returns; the cut is downstream."""
    b = BarBuilder()
    seed = pd.DataFrame(
        {"open": [370.0, 389.3], "high": [371.0, 392.6], "low": [369.0, 388.0],
         "close": [370.5, 391.0], "volume": [5000.0, 369725.0]},
        index=pd.DatetimeIndex([
            pd.Timestamp("2026-09-21 15:29", tz=IST),
            pd.Timestamp("2026-09-22 09:15", tz=IST),
        ]),
    )
    b.seed(KEY, seed)
    bars = b.closed_bars(KEY, pd.Timestamp("2026-09-22 09:16", tz=IST))
    assert list(bars.index) == [pd.Timestamp("2026-09-22 09:15", tz=IST)]


def test_a_quiet_symbol_returns_an_empty_frame_not_yesterdays_bar() -> None:
    """`_market_looks_closed` reads emptiness — a stale bar must not mask a
    dead feed as a live one."""
    b = BarBuilder()
    b.on_tick(KEY, _ms("2026-09-21 15:56"), 367.00, 1_000_000)
    bars = b.closed_bars(KEY, pd.Timestamp("2026-09-22 09:46", tz=IST))
    assert bars.empty
    assert list(bars.columns) == ["open", "high", "low", "close", "volume"]
