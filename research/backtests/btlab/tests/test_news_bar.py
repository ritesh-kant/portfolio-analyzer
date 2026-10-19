"""The news-bar trigger: the first bar after a filing that clears the ticked tests, one per stock per day."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from btlab import news, plugins as P, runner, service, store
from btlab.tests.test_news_config import filing, isolated, news_params, open_day, tape, write_news  # noqa: F401
from btlab.tests.test_runner import KEY, cand, cfg
from btlab.tests.test_sim import FLAT, mk

BAR = [{"id": "news", "params": {"trigger": "news_bar", "bar_window_min": 15}}, {"id": "market_reaction", "params": {"min_move_pct": 2.0, "min_rvol": 1.0}}]


def test_the_trigger_is_offered_only_by_the_news_indicator():
    sel = P.normalise([{"id": "news", "params": {"trigger": "news_bar"}}])
    assert sel[0]["params"] == {"lookback_h": 24.0, "scope": "material", "trigger": "news_bar", "bar_window_min": 30, "bar_min_move_pct": 0.5}
    with pytest.raises(P.PluginError, match="has no implementation"):
        news_params(trigger="news_bar")                      # the superseded combined indicator is pattern-only


def test_a_pattern_trigger_keeps_the_params_and_so_the_key_it_always_had():
    sel = P.normalise([{"id": "news", "params": {"bar_window_min": 5}}])
    assert sel[0]["params"] == {"lookback_h": 24.0, "scope": "material", "trigger": "pattern"}
    assert P.news_params(sel)["trigger"] == "pattern"


def test_reaction_clock_runs_from_the_filing_or_from_the_open_when_the_filing_came_before_it(isolated):
    write_news([filing(at="09:50:00")])
    df = pd.DataFrame([cand(close5=105, decision_min=600)])
    assert news._stamp_reaction(df, {KEY: tape()}, 24, "material", "NSE")["news_delay_min"][0] == 10
    write_news([filing(at="08:28:40")])                          # before the open: counted from 09:15
    assert news._stamp_reaction(df, {KEY: tape()}, 24, "material", "NSE")["news_delay_min"][0] == 600 - 555


def test_the_window_drops_a_bar_that_is_too_late_after_the_filing():
    df = pd.DataFrame([cand(news_move_pct=3.0, news_delay_min=10), cand(news_move_pct=3.0, news_delay_min=16),
                       cand(news_move_pct=float("nan"), news_delay_min=float("nan"))])
    assert P.entry_mask(df, P.normalise(BAR)).tolist() == [True, False, False]
    plain = P.entry_mask(df, P.normalise([{"id": "news"}]))       # the pattern trigger has no window
    assert plain.tolist() == [True, True, False]


def test_a_bar_that_is_down_or_flat_on_the_filing_is_never_the_news_bar():
    df = pd.DataFrame([cand(news_move_pct=m, news_delay_min=5) for m in (-1.3, 0.0, 0.4, 0.5, 2.0)])
    news_only = [{"id": "news", "params": {"trigger": "news_bar"}}]
    assert P.entry_mask(df, P.normalise(news_only)).tolist() == [False, False, False, True, True]
    loose = [{"id": "news", "params": {"trigger": "news_bar", "bar_min_move_pct": 0}}]
    assert P.entry_mask(df, P.normalise(loose)).tolist() == [False, True, True, True, True]
    assert P.entry_mask(df, P.normalise([{"id": "news"}])).all()           # the pattern trigger is unchanged


def test_a_saved_news_bar_run_from_before_the_floor_is_not_silently_rebuilt():
    old = cfg([{"id": "news", "params": {"lookback_h": 2.0, "scope": "material", "trigger": "news_bar", "bar_window_min": 30}}])
    assert "predates" in store.rebuild_error({"config": old})
    new = cfg([{"id": "news", "params": P.normalise([{"id": "news", "params": {"trigger": "news_bar"}}])[0]["params"]}])
    assert store.rebuild_error({"config": new}) is None or "predates" not in store.rebuild_error({"config": new})


def test_only_the_first_qualifying_bar_per_stock_per_day_is_the_trade():
    df = pd.DataFrame([cand(decision_min=640, fill_min=641), cand(decision_min=630, fill_min=631),
                       cand(decision_min=650, fill_min=651), cand(symbol="BBB", decision_min=660, fill_min=661),
                       cand(date="2026-03-03", decision_min=700, fill_min=701)])
    got = P.first_news_bar(df, P.normalise(BAR))
    assert sorted(zip(got["date"], got["symbol"], got["decision_min"])) == [
        ("2026-03-02", "AAA", 630), ("2026-03-02", "BBB", 660), ("2026-03-03", "AAA", 700)]
    assert len(P.first_news_bar(df, P.normalise([{"id": "news"}]))) == len(df)      # pattern trigger: untouched


def test_run_takes_one_trade_even_when_the_first_one_has_already_exited(monkeypatch):
    # the first trade is stopped out at 10:03, which frees the stock; a later bar still passes every test
    tape_ = mk([FLAT] * 3 + [(100.0, 100.0, 98.5, 99.0)] + [FLAT] * 40, start=600)
    first = cand(decision_min=599, fill_min=600, fill_k=0, news_move_pct=3.0, news_delay_min=5.0)
    later = cand(decision_min=619, fill_min=620, fill_k=20, news_move_pct=3.0, news_delay_min=25.0)
    monkeypatch.setattr(news, "stamp_candidates", lambda df, days, params, market="NSE": df)
    rule = service.base_mod.BaseRule(entry_mode="market")
    table = pd.DataFrame([first, later])

    def trades(trigger):
        plugins = P.normalise([{"id": "news", "params": {"trigger": trigger, "bar_window_min": 30}},
                               {"id": "market_reaction", "params": {"min_move_pct": 2.0, "min_rvol": 1.0}}])
        return runner.run(runner.RunConfig(years=[2026], rule=rule, plugins=plugins), table, {KEY: tape_})

    assert list(trades("pattern")["exit_reason"]) == ["stop", "eod"]         # without the trigger it re-enters
    only = trades("news_bar")
    assert list(only["exit_reason"]) == ["stop"] and list(only["entry_time"]) == ["10:00"]


def test_apply_switches_the_base_to_the_no_pattern_trigger():
    cfg_ = service.parse_request({"years": [2026], "plugins": [{"id": "news", "params": {"trigger": "news_bar"}}],
                                  "base": {"entry_mode": "hybrid"}})
    assert cfg_["base"]["entry_mode"] == "market"
    assert service.parse_request({"years": [2026], "plugins": [{"id": "news"}],
                                  "base": {"entry_mode": "hybrid"}})["base"]["entry_mode"] == "hybrid"


def test_a_news_bar_run_has_its_own_saved_key():
    a = cfg([{"id": "news", "params": P.normalise([{"id": "news"}])[0]["params"]}])
    b = cfg([{"id": "news", "params": P.normalise([{"id": "news", "params": {"trigger": "news_bar"}}])[0]["params"]}])
    assert store.config_key(a) != store.config_key(b)


# ------------------------------------------------------------------ pullback number

def peaked_tape():
    """20 five-minute bars from 09:15; flat at 100 except swing highs at bars 5 and 12 (no peak before the filing)."""
    n = 20
    high = np.full(n, 100.0)
    high[5], high[12] = 110.0, 108.0
    arr = tape()
    arr.update({"m5_min": np.arange(555, 555 + 5 * n, 5), "m5_o": np.full(n, 99.0), "m5_h": high,
                "m5_l": np.full(n, 98.0), "m5_c": np.full(n, 99.5), "m5_v": np.ones(n)})
    return arr


def decision(last_closed_bar):
    return 555 + 5 * (last_closed_bar + 1)


@pytest.mark.parametrize("bar, want", [(7, 0), (8, 1), (14, 1), (15, 2), (19, 2)])
def test_news_pullback_counts_confirmed_swing_highs_since_the_filing(isolated, bar, want):
    write_news([filing(at="09:25:00")])                             # inside bar 2, before both peaks
    df = pd.DataFrame([cand(close5=105, decision_min=decision(bar))])
    got = news._stamp_reaction(df, {KEY: peaked_tape()}, 24, "material", "NSE")["news_pullback_ord"][0]
    assert got == want                                              # a peak counts only once 3 bars have printed after it


def test_a_peak_before_the_filing_is_not_a_pullback_of_the_news(isolated):
    write_news([filing(at="09:57:00")])                             # inside bar 8: bar 5's peak came first
    df = pd.DataFrame([cand(close5=105, decision_min=decision(19))])
    assert news._stamp_reaction(df, {KEY: peaked_tape()}, 24, "material", "NSE")["news_pullback_ord"][0] == 1


def test_pullback_range_selects_only_the_chosen_pullbacks():
    df = pd.DataFrame([cand(pullback_ord=n, news_pullback_ord=n, news_pullback_ord_1m=9) for n in (0, 1, 2, 3, 4)])

    def picks(pid, **params):
        return P.entry_mask(df, P.normalise([{"id": pid, "params": params}, {"id": "news"}])).tolist()

    assert picks("pullback_ordinal", min_ordinal=2, max_ordinal=2) == [False, False, True, False, False]   # only the 2nd
    assert picks("pullback_ordinal", min_ordinal=2, max_ordinal=3) == [False, False, True, True, False]    # the 2nd and 3rd
    assert picks("pullback_ordinal") == [False, True, True, False, False]                                  # the old default: 1st and 2nd
    assert picks("news_pullback") == [False, True, False, False, False]                                    # first pullback after the news
    assert picks("news_pullback", min_ordinal=2, max_ordinal=3) == [False, False, True, True, False]


def test_pullback_validation_and_saved_keys():
    for pid in ("pullback_ordinal", "news_pullback"):
        with pytest.raises(P.PluginError, match="must not be above"):
            P.normalise([{"id": pid, "params": {"min_ordinal": 3, "max_ordinal": 2}}, {"id": "news"}])
    with pytest.raises(P.PluginError, match="also tick News"):
        P.normalise([{"id": "news_pullback"}])
    assert P.normalise([{"id": "pullback_ordinal", "params": {"max_ordinal": 2}}])[0]["params"] == {"max_ordinal": 2}
    assert {c["id"] for c in P.catalog()} >= {"pullback_ordinal", "news_pullback"}


def test_a_bar_that_is_not_inside_a_pullback_never_passes_whatever_the_count_says():
    df = pd.DataFrame([cand(news_pullback_ord=1, news_in_pullback=ok) for ok in (False, True)])
    assert P.entry_mask(df, P.normalise([{"id": "news_pullback"}, {"id": "news"}])).tolist() == [False, True]


def test_the_timeframe_option_chooses_which_count_is_read():
    df = pd.DataFrame([cand(news_pullback_ord=1, news_pullback_ord_1m=3, news_in_pullback=True, news_in_pullback_1m=True)])
    sel = lambda tf, n: P.normalise([{"id": "news_pullback", "params": {"timeframe": tf, "min_ordinal": n, "max_ordinal": n}}, {"id": "news"}])
    assert P.entry_mask(df, sel("5m", 1)).iloc[0] and not P.entry_mask(df, sel("5m", 3)).iloc[0]
    assert P.entry_mask(df, sel("1m", 3)).iloc[0] and not P.entry_mask(df, sel("1m", 1)).iloc[0]


def test_the_bar_is_in_a_pullback_only_while_the_latest_confirmed_peak_has_not_been_exceeded(isolated):
    write_news([filing(at="09:25:00")])
    t = peaked_tape()
    t["m5_h"][9] = 111.0                                            # a higher high after the peak at bar 5 (110), confirmed at bar 12
    stamp = lambda tape_, bar: news._stamp_reaction(pd.DataFrame([cand(close5=105, decision_min=decision(bar))]),
                                                    {KEY: tape_}, 24, "material", "NSE")
    assert not stamp(t, 7)["news_in_pullback"][0]                    # no peak confirmed yet: the first leg is running
    assert stamp(peaked_tape(), 8)["news_in_pullback"][0]            # peak 5 confirmed and nothing above it since
    assert not stamp(t, 11)["news_in_pullback"][0]                   # 111 traded above peak 5 before the next peak confirmed
    after = stamp(t, 12)
    assert after["news_pullback_ord"][0] == 2 and after["news_in_pullback"][0]    # the 111 peak is confirmed: pullback 2


def test_a_one_minute_high_above_the_peak_ends_the_pullback_before_a_new_peak_is_confirmed(isolated):
    write_news([filing(at="09:25:00")])
    t = peaked_tape()
    n = len(t["m1_min"])
    m1 = np.full(n, 99.0)
    m1[(5 * 5 + 4)] = 110.0                                         # the 1-minute high inside bar 5 that is its peak
    m1[(5 * 9) + 1] = 112.0                                         # bar 9, minute 1: above 110
    t.update({"m1_h": m1, "m1_o": np.full(n, 99.0), "m1_l": np.full(n, 98.0), "m1_c": np.full(n, 99.0)})
    one = lambda bar: news._stamp_reaction(pd.DataFrame([cand(close5=105, decision_min=decision(bar))]), {KEY: t}, 24, "material", "NSE")["news_in_pullback"][0]
    assert one(8)
    assert not one(11)


def test_one_minute_swing_highs_count_from_the_filing(isolated):
    write_news([filing(at="09:25:00")])
    arr = tape()
    n = len(arr["m1_min"])
    high = np.full(n, 100.0)
    high[30] = 110.0                                                 # 09:45
    arr.update({"m1_h": high, "m1_l": np.full(n, 99.0), "m1_c": np.full(n, 99.5)})
    one = lambda dec: news._stamp_reaction(pd.DataFrame([cand(close5=105, decision_min=dec)]), {KEY: arr}, 24, "material", "NSE")["news_pullback_ord_1m"][0]
    assert one(555 + 33) == 0 and one(555 + 34) == 1                 # confirmed 3 one-minute bars after the peak, not before
