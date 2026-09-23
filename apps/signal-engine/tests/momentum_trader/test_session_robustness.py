"""The two failures that cost the 2026-09-14 session, and their fixes.

On 2026-09-14 the scanner ran a full Fargate day against a closed exchange and
then died in `prepare()` on a zero-byte parquet file. Both causes are fixed:
the calendar gained the missing holiday, the cache can no longer raise, and the
scanner now exits on its own when no data arrives — which is the only defence
that works for the holidays the hand-maintained list is still missing.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pandas as pd
import pytest
from src.config import Settings
from src.momentum_trader.scanner import (
    NO_DATA_GRACE,
    STRATEGY_WARRIOR_STRICT,
    Scanner,
    _strategy_config,
)
from src.momentum_trader.upstox import (
    read_json_cache,
    read_parquet_cache,
    write_json_cache,
    write_parquet_cache,
)
from src.news_trader.market_calendar import is_trading_day

IST = "Asia/Kolkata"


# ── the holiday that was missing ─────────────────────────────────────────────

def test_ganesh_chaturthi_2026_is_a_holiday():
    """The session this bug actually cost. 2026-09-14 was a Monday."""
    assert date(2026, 9, 14).weekday() == 0
    assert not is_trading_day(date(2026, 9, 14))


@pytest.mark.parametrize("d", [
    date(2026, 10, 2),    # Gandhi Jayanti, Friday
    date(2026, 10, 20),   # Dussehra, Tuesday
    date(2026, 11, 10),   # Diwali Balipratipada, Tuesday
    date(2026, 12, 25),   # Christmas, Friday
])
def test_confirmed_2026_weekday_holidays(d):
    assert d.weekday() < 5, "a weekend entry would prove nothing"
    assert not is_trading_day(d)


def test_ordinary_weekdays_still_trade():
    assert is_trading_day(date(2026, 9, 15))
    assert is_trading_day(date(2026, 9, 16))


# ── a cache file can no longer end the session ───────────────────────────────

def test_a_zero_byte_parquet_reads_as_absent(tmp_path):
    """The exact 2026-09-14 crash: `Parquet file size is 0 bytes`."""
    f = tmp_path / "TEST_2026-09-13.parquet"
    f.write_bytes(b"")
    assert read_parquet_cache(f) is None


def test_a_truncated_parquet_reads_as_absent(tmp_path):
    f = tmp_path / "TEST.parquet"
    f.write_bytes(b"PAR1\x00\x00garbage")
    assert read_parquet_cache(f) is None


def test_a_missing_parquet_reads_as_absent(tmp_path):
    assert read_parquet_cache(tmp_path / "nope.parquet") is None


def test_a_good_parquet_round_trips(tmp_path):
    f = tmp_path / "TEST.parquet"
    df = pd.DataFrame({"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5],
                       "volume": [10.0]},
                      index=pd.date_range(pd.Timestamp("2026-09-15 09:15", tz=IST), periods=1))
    write_parquet_cache(f, df)
    back = read_parquet_cache(f)
    assert back is not None
    # check_freq=False: parquet does not carry a DatetimeIndex's `freq`
    # attribute, which is pandas metadata rather than data.
    pd.testing.assert_frame_equal(back, df, check_freq=False)


@pytest.mark.parametrize("payload", [b"", b"{", b"null", b"[1, 2]", b"not json at all"])
def test_a_broken_sidecar_reads_as_absent(tmp_path, payload):
    """The other half of the same race — 09-11 died on `Expecting value`."""
    f = tmp_path / "TEST.range.json"
    f.write_bytes(payload)
    assert read_json_cache(f) is None


def test_a_good_sidecar_round_trips(tmp_path):
    f = tmp_path / "TEST.range.json"
    write_json_cache(f, {"start": "2026-01-01", "end": "2026-09-14"})
    assert read_json_cache(f) == {"start": "2026-01-01", "end": "2026-09-14"}


def test_writes_are_atomic_and_leave_no_temp_behind(tmp_path):
    """A concurrent reader must see the old file or the new one, never a partial.

    The write lands under a temp name and is renamed, so at no point does the
    real path hold a half-written file.
    """
    f = tmp_path / "TEST.range.json"
    write_json_cache(f, {"start": "2026-01-01", "end": "2026-06-30"})
    write_json_cache(f, {"start": "2026-01-01", "end": "2026-09-14"})
    assert json.loads(f.read_text())["end"] == "2026-09-14"
    assert list(tmp_path.iterdir()) == [f], "a .tmp file was left on the volume"


def test_a_failed_write_does_not_clobber_the_existing_cache(tmp_path):
    f = tmp_path / "TEST.parquet"
    df = pd.DataFrame({"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5],
                       "volume": [10.0]},
                      index=pd.date_range(pd.Timestamp("2026-09-15 09:15", tz=IST), periods=1))
    write_parquet_cache(f, df)
    with pytest.raises(AttributeError):
        write_parquet_cache(f, "not a dataframe")     # type: ignore[arg-type]
    assert read_parquet_cache(f) is not None, "the good file survived"
    assert list(tmp_path.iterdir()) == [f]


# ── the scanner gives up when no data arrives ────────────────────────────────

def _scanner_with(states: dict, bars: pd.DataFrame | None) -> Scanner:
    import threading

    class _Builder:
        def closed_bars(self, _key, _now):
            return bars if bars is not None else pd.DataFrame()

    settings = Settings(mt_strategy=STRATEGY_WARRIOR_STRICT)
    s = Scanner.__new__(Scanner)
    s.s = settings
    s.cfg = _strategy_config(settings)
    s.states = states
    s.builder = _Builder()
    s._state_lock = threading.RLock()
    return s


def _at(hh: int, mm: int) -> pd.Timestamp:
    return pd.Timestamp(f"2026-09-14 {hh:02d}:{mm:02d}", tz=IST)


def _one_bar() -> pd.DataFrame:
    return pd.DataFrame(
        {"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [10.0]},
        index=pd.date_range(pd.Timestamp("2026-09-14 09:15", tz=IST), periods=1),
    )


def test_a_silent_universe_after_the_grace_time_means_the_market_is_shut():
    s = _scanner_with({"NSE_EQ|A": object(), "NSE_EQ|B": object()}, bars=None)
    grace = datetime(2000, 1, 1, *NO_DATA_GRACE).time()
    assert not s._market_looks_closed(_at(grace.hour, grace.minute - 1)), "too early to judge"
    assert s._market_looks_closed(_at(grace.hour, grace.minute))


def test_one_printing_symbol_is_enough_to_keep_running():
    s = _scanner_with({"NSE_EQ|A": object()}, bars=_one_bar())
    assert not s._market_looks_closed(_at(11, 0))


def test_the_bailout_cannot_fire_before_the_grace_time():
    s = _scanner_with({"NSE_EQ|A": object()}, bars=None)
    assert not s._market_looks_closed(_at(9, 16)), "a slow feed connect is not a holiday"


def test_an_empty_universe_is_not_reported_as_a_closed_market():
    """`prepare()` already exits on an empty universe with its own message."""
    s = _scanner_with({}, bars=None)
    assert not s._market_looks_closed(_at(11, 0))


def test_market_hours_bypass_disables_the_bailout():
    s = _scanner_with({"NSE_EQ|A": object()}, bars=None)
    s.s = Settings(mt_strategy=STRATEGY_WARRIOR_STRICT, mt_bypass_market_hours=True)
    assert not s._market_looks_closed(_at(11, 0))


def test_warrior_strict_carries_the_macd_open_tolerance():
    """Kept at the frozen rule (0) after BT48; still env-overridable."""
    live = _strategy_config(Settings(mt_strategy=STRATEGY_WARRIOR_STRICT))
    assert live.macd_open_tolerance == 0.0
    tried = Settings(mt_strategy=STRATEGY_WARRIOR_STRICT, mt_macd_open_tolerance=0.05)
    assert _strategy_config(tried).macd_open_tolerance == 0.05
