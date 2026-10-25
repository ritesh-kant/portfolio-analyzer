"""The order-flow indicators: CVD / footprint delta maths, the two detectors, and no look-ahead on real bars.

Parity with the web chart's TypeScript (apps/web/lib/tv) was checked once on 120 real 2026 symbol-day cases
(5m and 15m, pivots 1-3): 2,562 divergences and 1,174 delta setups, 0 mismatches.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from btlab import base, orderflow as of
from btlab import plugins as P


def candles(lows, cvd_lows, highs=None, cvd_highs=None, day="2026-03-02"):
    n = len(lows)
    lows = np.asarray(lows, float)
    highs = np.asarray(highs if highs is not None else lows + 2.0, float)
    cl = np.asarray(cvd_lows, float)
    ch = np.asarray(cvd_highs if cvd_highs is not None else cl + 10.0, float)
    start = 555 + 15 * np.arange(n)
    return of.Candles(day=np.array([day] * n), start=start, end=start + 15, o=lows + 1, h=highs, l=lows,
                      c=lows + 1, v=np.full(n, 1000.0), cvd_h=ch, cvd_l=cl, delta=np.zeros(n), n=np.full(n, 15))


def test_signed_volume_follows_body_then_close_then_previous_sign():
    o = np.array([10, 10, 10, 10, 10.0])
    c = np.array([11, 10, 9, 9, 9.0])          # green · flat at a lower close · red · flat equal close · flat equal
    v = np.array([100, 50, 30, 20, 10.0])
    # the flat second minute closed under the first (11 -> 10): selling; the flat fourth equals the third: keeps -1
    assert of.signed_volumes(o, c, v).tolist() == [100, -50, -30, -20, -10]


def test_footprint_delta_is_volume_times_clamped_body_share():
    o, h, lo, c, v = (np.array([x]) for x in (100.0, 101.0, 99.0, 101.0, 1000.0))
    # body 1 of range 2: buy share 0.5 + 0.45 x 0.5 = 0.725, delta = 1000 x 0.45
    assert of.footprint_deltas(o, h, lo, c, v)[0] == pytest.approx(450.0)
    assert of.footprint_deltas(o, o, o, o, v)[0] == 0.0            # no range: half and half


def test_regular_bullish_divergence_is_known_pivot_candles_after_the_second_low():
    # swing lows at 3 (98) and 8 (97, a lower low); CVD lows -100 then -50 (a higher low)
    lows = [101, 100, 99, 98, 99, 100, 99, 98.5, 97, 98, 99, 100, 101]
    cvd = [0, -20, -40, -100, -60, -40, -45, -48, -50, -30, -20, -10, 0]
    got = [d for d in of.find_cvd_divergences(candles(lows, cvd), pivot=3, tol=0) if d.bullish]
    assert [(d.i1, d.i2, d.known, d.hidden) for d in got] == [(3, 8, 11, False)]
    # pivot 1 sees the same pair (and nothing else bullish), two candles sooner
    got1 = [d for d in of.find_cvd_divergences(candles(lows, cvd), pivot=1, tol=0) if d.bullish]
    assert [(d.i1, d.i2, d.known) for d in got1] == [(3, 8, 9)]


def test_hidden_bullish_and_no_pair_across_sessions():
    lows = [101, 100, 99, 98, 99, 100, 99.5, 99, 98.5, 99, 100, 101]       # higher low at 8 (98.5 > 98)
    cvd = [0, -20, -40, -50, -40, -30, -60, -80, -120, -90, -70, -60]     # CVD lower low
    got = [d for d in of.find_cvd_divergences(candles(lows, cvd), pivot=3, tol=0) if d.bullish]
    assert [(d.i2, d.hidden) for d in got] == [(8, True)]
    split = candles(lows, cvd)
    split.day[6:] = "2026-03-03"                                        # the second low is another session
    assert [d for d in of.find_cvd_divergences(split, pivot=3, tol=0) if d.bullish] == []


def test_sellers_trapped_setup_confirms_on_a_green_close_above_with_positive_delta():
    cd = candles([100.0] * 14, [0.0] * 14)
    cd.o[:], cd.h[:], cd.l[:], cd.c[:] = 100.0, 100.5, 99.5, 100.2
    cd.o[12], cd.h[12], cd.l[12], cd.c[12], cd.delta[12] = 100.0, 100.2, 98.0, 99.9, -300.0   # long lower wick, sold into
    cd.o[13], cd.h[13], cd.l[13], cd.c[13], cd.delta[13] = 100.0, 100.6, 99.9, 100.4, 200.0   # green, closes above 100.2
    got = [s for s in of.find_delta_setups(cd) if s.sellers]
    assert [(s.index, s.status, s.resolved) for s in got] == [(12, "confirmed", 13)]


def test_plugins_resolve_and_need_the_stamped_columns():
    sel = P.normalise([{"id": "cvd_divergence", "params": {"pivot": 1, "with_delta": "near"}},
                       {"id": "delta_divergence", "params": {}}])
    by = {x["id"]: x["params"] for x in sel}
    assert by["cvd_divergence"]["pivot"] == 1 and by["cvd_divergence"]["timeframe"] == "15m"
    df = pd.DataFrame({"cvd_div_ok": [True, False], "delta_div_ok": [True, True]})
    assert P.entry_mask(df, sel).tolist() == [True, False]


# ------------------------------------------------------------------ real bars


@pytest.fixture(scope="module")
def real_day():
    rule = base.BaseRule.for_market("NSE", entry_mode="market")
    got = base.load_year(2026, rule)
    if got is None or got[0].empty:
        pytest.skip("2026 NSE market-mode base data is not built")
    sym, day = sorted(got[0]["day_key"].unique())[len(got[0]["day_key"].unique()) // 2].split("|")
    bars = base._load(sym, 2026)
    norm = bars.index.normalize()
    sess = norm.unique()
    k = sess.searchsorted(pd.Timestamp(day, tz=sess.tz))
    return bars[(norm >= sess[max(0, k - of.WARM_SESSIONS)]) & (norm <= sess[k])], day


@pytest.mark.parametrize("pivot", [1, 3])
def test_no_look_ahead_cutting_the_day_keeps_every_event_known_by_the_cut(real_day, pivot):
    m1, day = real_day
    cvd_p = P.resolve_params(P.REGISTRY["cvd_divergence"], {"pivot": pivot, "with_delta": "near"})
    dd_p = P.resolve_params(P.REGISTRY["delta_divergence"], {})
    full = of._day_events(m1, day, cvd_p, dd_p, 555)
    today = m1.index.normalize() == m1.index.normalize()[-1]
    for cut in (600, 690, 780, 870):
        mins = m1.index.hour * 60 + m1.index.minute
        part = of._day_events(m1[~today | (mins < cut)], day, cvd_p, dd_p, 555)
        assert part["cvd"] == [e for e in full["cvd"] if e[0] <= cut]
        assert part["dd"] == [e for e in full["dd"] if e <= cut]
