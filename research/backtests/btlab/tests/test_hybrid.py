"""The hybrid trigger (`entry_mode="hybrid"`): the nine patterns plus doji / spinning tops after a down-move."""

from __future__ import annotations

import pandas as pd

from btlab import base, hybrid, runner
from btlab.tests.test_runner import KEY, cand, day

RULE = base.BaseRule(entry_mode="hybrid")


def session(closes_open_high_low, start="2026-03-02 09:15"):
    idx = pd.date_range(start, periods=len(closes_open_high_low), freq="5min", tz="Asia/Kolkata")
    o, h, lo, c = zip(*closes_open_high_low)
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": 1000.0}, index=idx)


def falling_then(last):
    """Fifteen candles that step down by 1 (range 2), then `last`."""
    rows = []
    px = 100.0
    for _ in range(15):
        rows.append((px, px + 0.3, px - 1.5, px - 1.0))
        px -= 1.0
    return session(rows + [last(px)])


def test_a_doji_after_a_fall_is_an_indecision_entry():
    bars = falling_then(lambda px: (px, px + 1.0, px - 1.0, px + 0.02))
    got = hybrid.indecision_matches(bars, "5m")
    assert [m.name for m in got] == ["indecision_doji"]
    assert got[0].direction == "bullish"
    assert got[0].confirmation == bars["high"].iloc[-1] and got[0].invalidation == bars["low"].iloc[-1]


def test_a_doji_after_a_rise_is_not():
    rows, px = [], 100.0
    for _ in range(15):
        rows.append((px, px + 1.5, px - 0.3, px + 1.0))
        px += 1.0
    bars = session(rows + [(px, px + 1.0, px - 1.0, px + 0.02)])
    assert hybrid.indecision_matches(bars, "5m") == []


def test_the_nine_bullish_patterns_are_not_indecision_entries():
    bars = falling_then(lambda px: (px - 0.2, px + 0.05, px - 3.0, px))      # a hammer, not an indecision candle
    assert all(m.name not in hybrid.INDECISION_PATTERNS for m in hybrid.indecision_matches(bars, "5m"))


def test_indecision_has_its_own_cache_key_and_default_is_unchanged():
    assert RULE.key() != base.BaseRule().key()
    assert RULE.key() != base.BaseRule(entry_mode="market").key()


def test_pool_keeps_the_chosen_patterns_and_every_indecision_candidate():
    df = pd.DataFrame([cand(pattern="hammer"), cand(pattern="bullish_engulfing"),
                       cand(pattern="indecision_doji"), cand(pattern="indecision_spinning_top")])
    cfg = runner.RunConfig(years=[2026], rule=RULE, patterns=["hammer"])
    assert sorted(runner.pool(df, cfg)["pattern"]) == ["hammer", "indecision_doji", "indecision_spinning_top"]


def test_the_base_pool_is_untouched_by_indecision_rows():
    df = pd.DataFrame([cand(pattern="hammer"), cand(pattern="indecision_doji")])
    cfg = runner.RunConfig(years=[2026], rule=base.BaseRule(), patterns=["hammer"])
    assert list(runner.pool(df, cfg)["pattern"]) == ["hammer"]


def test_indecision_candidate_runs_through_the_engine():
    tr = runner.run(runner.RunConfig(years=[2026], rule=RULE),
                    pd.DataFrame([cand(pattern="indecision_doji")]), {KEY: day()})
    assert len(tr) == 1 and tr["setup"].iloc[0] == "indecision_doji"
