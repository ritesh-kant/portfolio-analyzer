"""Checks against the real bar cache: no look-ahead, and an independent replay.

These need the 1-minute cache (`.cache_upstox/`) and a built 2026 candidate table;
they skip themselves otherwise. Run `pnpm bt:dashboard`, open /lab and Apply once
(or call `base.build_year(2026, BaseRule())`) to build it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from btlab import base, runner

YEAR = 2026


@pytest.fixture(scope="module", params=["NSE", "US"])
def rule(request):
    return base.BaseRule.for_market(request.param)


@pytest.fixture(scope="module")
def built(rule):
    got = base.load_year(YEAR, rule)
    if got is None or got[0].empty:
        pytest.skip(f"2026 {rule.market} base data is not built")
    return got


FEATURES = ["pattern", "trigger", "stop", "target", "support", "resistance", "day_chg_pct", "rvol",
            "close5", "ema9_5", "ema20_5", "ema200_5", "vwap5", "macd5", "macd1", "vol_ratio5",
            "pullback_ord", "f1_uptrend", "f3_surge", "pv_rising", "strength", "dist_round_pct"]


def _scrambled(original: pd.DataFrame, date: str, cut: int, rng, prices: bool = True) -> pd.DataFrame:
    """The same bars, but every minute of `date` from `cut` on is replaced by random noise
    (prices=False: only the volumes are scrambled, so every fill still happens)."""
    df = original.copy()
    mask = (df.index.normalize() == pd.Timestamp(date, tz=df.index.tz)) & \
           ((df.index.hour * 60 + df.index.minute) >= cut)
    if not prices:
        df.loc[mask, "volume"] = df.loc[mask, "volume"].to_numpy() * rng.uniform(0.2, 6, mask.sum())
        return df
    o = df.loc[mask, "open"].to_numpy() * rng.uniform(0.85, 1.15, mask.sum())
    c = df.loc[mask, "close"].to_numpy() * rng.uniform(0.85, 1.15, mask.sum())
    df.loc[mask, "open"], df.loc[mask, "close"] = o, c
    # the day-level pre-filter reads the day's high, so never LOWER it: that would drop the day
    # for a reason that has nothing to do with look-ahead
    df.loc[mask, "high"] = np.maximum(np.maximum(o, c) * 1.01, original.loc[mask, "high"].to_numpy())
    df.loc[mask, "low"] = np.minimum(o, c) * 0.99
    df.loc[mask, "volume"] = df.loc[mask, "volume"].to_numpy() * rng.uniform(0.2, 6, mask.sum())
    return df


def _same(a, b) -> bool:
    return (a == b) or (pd.isna(a) and pd.isna(b)) or abs(a - b) < 1e-9


@pytest.mark.parametrize("when", ["after_fill", "volume_after_decision"])
def test_candidate_features_do_not_depend_on_bars_after_the_cut(built, rule, monkeypatch, when):
    """Scramble the day from a cut-off on and rebuild. `after_fill`: prices AND volumes after the
    candidate's fill are noise, yet it must come back with the SAME numbers. `volume_after_decision`:
    only volumes after the decision bar are noise (prices, hence fills, are untouched), so no
    volume-based feature (RVOL, bar volume ratio, price/volume slope) may have moved."""
    cands, _ = built
    rng = np.random.default_rng(7)
    real_load = base._load
    checked = 0
    for _, row in cands.groupby("symbol").head(1).head(14).iterrows():
        cut = int(row["fill_min"]) + 1 if when == "after_fill" else int(row["decision_min"])
        df = _scrambled(real_load(row["symbol"], YEAR, rule.market), row["date"], cut, rng, prices=when == "after_fill")
        monkeypatch.setattr(base, "_load", lambda s, y, m="NSE", _d=df: _d if y == YEAR else real_load(s, y, m))
        again = pd.DataFrame(base.build_symbol(row["symbol"], YEAR, rule)["cands"])
        monkeypatch.undo()
        hit = again[(again["date"] == row["date"]) & (again["pattern"] == row["pattern"])
                    & (again["decision_min"] == row["decision_min"])] if len(again) else again
        assert len(hit) == 1, f'{row["symbol"]} {row["date"]} {row["pattern"]} vanished'
        if len(hit) == 1:
            for col in FEATURES + ["fill", "fill_min"] * (when == "after_fill"):
                assert _same(row[col], hit.iloc[0][col]), \
                    f'{row["symbol"]} {row["date"]} {row["pattern"]}: {col} {row[col]} -> {hit.iloc[0][col]}'
            checked += 1
    assert checked >= 5, "not enough candidates survived to test"


def test_base_trades_match_an_independent_replay_on_raw_bars(built, rule):
    cands, days = built
    cfg = runner.RunConfig(years=[YEAR], rule=rule, risk_inr=rule.mk.risk, max_notional_inr=rule.mk.max_notional)
    trades = runner.run(cfg, cands, days)
    assert len(trades) > 10
    for _, t in trades.sample(min(40, len(trades)), random_state=1).iterrows():
        raw = base._load(t["symbol"], YEAR, rule.market)
        d = raw[raw.index.normalize() == pd.Timestamp(t["date"], tz=raw.index.tz)]
        mins = (d.index.hour * 60 + d.index.minute).to_numpy()
        hh, mm = t["entry_time"].split(":")
        k0 = int(np.where(mins == int(hh) * 60 + int(mm))[0][0])
        o, h, lo, c = (d[x].to_numpy() for x in ("open", "high", "low", "close"))
        entry, stop, target = t["entry"], t["stop"], t["target"]
        # the buy-stop really could fill on the entry bar, and not on the minute before it
        assert h[k0] >= t["trigger"]
        assert entry >= t["trigger"] - 1e-9 and (entry == o[k0] or entry == pytest.approx(t["trigger"] * 1.0003, abs=0.006))
        exit_px, why = None, None
        for k in range(k0, len(d)):
            if lo[k] <= stop:
                exit_px, why = (stop if k == k0 else min(stop, o[k])), "stop"
                break
            if k > k0 and h[k] >= target:
                exit_px, why = max(target, o[k]), "target"
                break
            if mins[k] >= rule.mk.eod_min:
                exit_px, why = c[k], "eod"
                break
        if why is None:
            exit_px, why = c[-1], "eod"
        assert (why, float(exit_px)) == (t["exit_reason"], pytest.approx(t["exit"], abs=0.006)), \
            f'{t["symbol"]} {t["date"]} {t["entry_time"]}'
        # a trade never starts before the pattern that triggered it had closed
        assert t["trigger_time"] <= t["entry_time"]
