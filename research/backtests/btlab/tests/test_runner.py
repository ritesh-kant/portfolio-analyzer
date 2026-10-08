"""Selection semantics, plugin plumbing and the saved-run store."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from btlab import plugins as P
from btlab import runner, store
from btlab.tests.test_sim import FLAT, mk

KEY = "AAA|2026-03-02"


def cand(**kw):
    base = dict(symbol="AAA", date="2026-03-02", pattern="hammer", prior_trend="down", strength=1.0,
                decision_min=600, fill_min=602, fill_k=2, trigger=100.0, invalidation=99.5, fill=100.0,
                stop=99.0, support=99.1, support_kind="pivot_low", target=102.0, resistance=102.2,
                resistance_kind="round", prev_close=95.0, day_chg_pct=5.0, rvol=4.0, close5=100.0,
                ema9_5=99.0, ema20_5=98.0, ema200_5=90.0, vwap5=99.5, macd5=0.3, macd5_prev=0.2,
                macd1=0.2, macd1_prev=0.1, vol_ratio5=2.0, pullback_ord=1, atr_pct=2.0,
                daily_uptrend=True, f1_uptrend=True, f2_chart=True, f3_surge=True, pv_rising=True,
                dist_round_pct=0.4, vwap_cross_bars=1.0, news_move_pct=3.0, day_key=KEY)
    return {**base, **kw}


def day(n=40):
    return mk([FLAT] * n)


def run_cfg(**kw):
    return runner.RunConfig(years=[2026], **kw)


def test_first_candidate_to_fill_wins_and_one_trade_per_day():
    early = cand(fill_min=605, fill_k=5, pattern="early")
    late = cand(fill_min=610, fill_k=10, pattern="late")
    tr = runner.run(run_cfg(patterns=["early", "late"]), pd.DataFrame([late, early]), {KEY: day()})
    assert list(tr["setup"]) == ["early"]


def test_a_vetoed_candidate_lets_the_next_one_step_up():
    a = cand(fill_min=605, fill_k=5, pattern="weak", vol_ratio5=0.5)
    b = cand(fill_min=610, fill_k=10, pattern="strong", vol_ratio5=3.0)
    cfg = run_cfg(patterns=["weak", "strong"], plugins=[{"id": "volume_surge", "params": {"min_ratio": 2.0}}])
    tr = runner.run(cfg, pd.DataFrame([a, b]), {KEY: day()})
    assert list(tr["setup"]) == ["strong"]


def test_second_entry_must_fill_after_the_first_exit():
    a = cand(fill_min=605, fill_k=5, pattern="a")
    # `a` runs to the end of the day (flat bars, no target/stop): nothing may fill inside it
    inside = cand(fill_min=630, fill_k=30, pattern="inside")
    cfg = run_cfg(max_trades=3, patterns=["a", "inside"])
    tr = runner.run(cfg, pd.DataFrame([a, inside]), {KEY: day(60)})
    assert list(tr["setup"]) == ["a"]


def test_pattern_subset_is_applied():
    tr = runner.run(run_cfg(patterns=["hammer"]), pd.DataFrame([cand(pattern="tweezer_bottom")]), {KEY: day()})
    assert tr.empty


def test_sizing_is_risk_based_and_capped():
    assert runner.plan_qty(100.0, 99.0, 500.0, 50_000.0) == 500
    assert runner.plan_qty(100.0, 99.9, 500.0, 50_000.0) == 500          # capped by notional
    assert runner.plan_qty(100.0, 100.0, 500.0, 50_000.0) == 0


def test_trade_row_has_the_dashboard_columns_and_stress_sits_on_top_of_real_costs():
    tr = runner.run(run_cfg(patterns=["hammer"]), pd.DataFrame([cand()]), {KEY: day()})
    need = {"date", "symbol", "entry_time", "exit_time", "entry", "stop", "exit", "exit_reason", "qty",
            "gross_pct", "net_pct", "gross_inr", "net_inr"}
    assert need <= set(tr.columns)
    r = tr.iloc[0]
    assert r["net_inr"] < r["net_real_inr"]
    assert r["net_real_inr"] == pytest.approx(r["gross_inr"] - r["costs_inr"], abs=0.01)


def test_pattern_low_stop_changes_the_stop_and_the_size():
    tr = runner.run(run_cfg(patterns=["hammer"], plugins=[{"id": "stop_pattern_low", "params": {}}]),
                    pd.DataFrame([cand(invalidation=99.6)]), {KEY: day()})
    assert tr.iloc[0]["stop"] == pytest.approx(99.55) and tr.iloc[0]["stop_source"] == "pattern_low"


def test_pattern_low_stop_reports_support_fallback_when_outside_allowed_range():
    from btlab import metrics
    tr = runner.run(run_cfg(patterns=["hammer"], plugins=[{"id": "stop_pattern_low"}]),
                    pd.DataFrame([cand(invalidation=90.0)]), {KEY: day()})
    assert tr.iloc[0]["stop"] == pytest.approx(99.0)
    assert tr.iloc[0]["stop_source"] == "support"
    assert metrics.summarize(tr)["stop_source_mix"] == {"support": 1}


# ------------------------------------------------------------------ plugins

def test_every_plugin_describes_itself_and_runs_on_a_row():
    df = pd.DataFrame([cand()])
    for p in P.REGISTRY.values():
        assert p.describe()["name"] and p.desc
        params = P.resolve_params(p, None)
        if p.entry:
            assert bool(p.entry(df, params).iloc[0]) in (True, False)
        else:
            P.exit_cfg([{"id": p.id, "params": params}])


def test_entry_plugins_filter_as_documented():
    df = pd.DataFrame([cand(), cand(close5=98.0), cand(macd1=0.1, macd1_prev=0.2)])
    assert list(P.entry_mask(df, P.normalise([{"id": "above_vwap"}]))) == [True, False, True]
    assert list(P.entry_mask(df, P.normalise([{"id": "above_vwap", "params": {"min_pct": 0.5}}]))) == [True, False, True]
    assert list(P.entry_mask(df, P.normalise([{"id": "above_vwap", "params": {"min_pct": 0.6}}]))) == [False, False, False]
    assert list(P.entry_mask(df, P.normalise([{"id": "above_vwap", "params": {"max_pct": 0.3}}]))) == [False, False, False]
    assert list(P.entry_mask(df, P.normalise([{"id": "macd1_open"}]))) == [True, True, False]
    assert P.entry_mask(df, P.normalise([{"id": "min_reward_risk", "params": {"min_rr": 2.0}}])).all()
    assert not P.entry_mask(df, P.normalise([{"id": "min_reward_risk", "params": {"min_rr": 2.5}}])).any()


def test_support_and_resistance_rules_apply_only_when_selected():
    df = pd.DataFrame([cand(), cand(support=99.8, support_kind="round"),
                       cand(resistance=100.5, resistance_kind="pivot_high")])
    assert P.entry_mask(df, []).all()                                     # unticked: nothing filtered
    sup = P.entry_mask(df, P.normalise([{"id": "support_rule"}]))
    assert list(sup) == [True, False, True]                               # 0.2% away is under the 0.3% floor
    assert list(P.entry_mask(df, P.normalise([{"id": "support_rule", "params": {"kind": "swing"}}]))) \
        == [True, False, True]
    assert list(P.entry_mask(df, P.normalise([{"id": "resistance_rule"}]))) == [True, True, False]
    assert list(P.entry_mask(df, P.normalise([{"id": "resistance_rule", "params": {"kind": "not_round"}}]))) \
        == [False, False, False]


def test_swing_level_rules_require_the_matching_pivot_direction():
    df = pd.DataFrame([cand(support_kind="pivot_low", resistance_kind="pivot_high"),
                       cand(support_kind="pivot_high", resistance_kind="pivot_low"),
                       cand(support_kind="session_pivot", resistance_kind="session_pivot")])
    for indicator in ("support_rule", "resistance_rule"):
        mask = P.entry_mask(df, P.normalise([{"id": indicator, "params": {"kind": "swing"}}]))
        assert mask.tolist() == [True, False, False]


def test_stronger_momentum_default_can_pass_us_base_candidates():
    from btlab.base import BaseRule
    rule = BaseRule.for_market("US")
    df = pd.DataFrame({"day_chg_pct": [rule.day_chg_min, rule.day_chg_max], "rvol": [5.0, 5.0]})
    assert P.entry_mask(df, P.normalise([{"id": "momentum_tighter"}])).tolist() == [True, True]


def test_time_window_uses_decision_and_fill_minutes():
    df = pd.DataFrame([cand(decision_min=600, fill_min=602), cand(decision_min=660, fill_min=662)])
    m = P.entry_mask(df, P.normalise([{"id": "time_window", "params": {"start": "09:30", "end": "11:00"}}]))
    assert list(m) == [True, False]


def test_validation_rejects_unknown_duplicate_and_clashing_plugins():
    with pytest.raises(P.PluginError):
        P.normalise([{"id": "nope"}])
    with pytest.raises(P.PluginError):
        P.normalise([{"id": "above_vwap"}, {"id": "above_vwap"}])
    with pytest.raises(P.PluginError):
        P.normalise([{"id": "target_none"}, {"id": "checkpoint_stop"}])
    out = P.normalise([{"id": "volume_surge", "params": {"min_ratio": 9999}}])
    assert out[0]["params"]["min_ratio"] == 20                           # clamped to the declared range
    with pytest.raises(P.PluginError):
        P.normalise([{"id": "breakeven", "params": {"to": "moon"}}])


# ------------------------------------------------------------------ store

@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(store, "LEDGER", tmp_path / "ledger.jsonl")
    store._cache.clear()
    return tmp_path


def cfg(plugins=()):
    return {"years": [2026], "base": {"day_chg_min": 4.0}, "patterns": ["hammer"], "risk_inr": 500,
            "max_notional_inr": 50000, "max_trades": 1, "plugins": list(plugins)}


def test_corrected_indicator_runs_get_new_config_keys():
    for indicator, params in (("news_reaction", {}), ("support_rule", {"kind": "swing"}),
                              ("resistance_rule", {"kind": "swing"})):
        canonical = store.canonical_config(cfg([{"id": indicator, "params": params}]))
        assert canonical["indicator_revisions"] == {indicator: 2}
    assert "indicator_revisions" not in store.canonical_config(cfg([{"id": "support_rule", "params": {"kind": "any"}}]))


def test_identical_config_is_not_saved_twice_and_ledger_counts_distinct_trials(tmp_store):
    tr = pd.DataFrame([{"a": 1}])
    plug = [{"id": "above_vwap", "params": {}}]
    r1 = store.save_run(cfg(plug), tr, {"n": 1}, {}, None)
    r2 = store.save_run(cfg(plug), tr, {"n": 1}, {}, None)
    assert r1["id"] == r2["id"] and len(store.list_runs()) == 1
    r3 = store.save_run(cfg([{"id": "macd5_positive", "params": {}}]), tr, {"n": 1}, {}, None)
    assert r3["trial_n"] == 2
    store.delete_run(r3["id"])
    r4 = store.save_run(cfg([{"id": "macd5_positive", "params": {}}]), tr, {"n": 1}, {}, None)
    assert r4["trial_n"] == 2                                           # deleting does not reset the counter
    base = store.save_run(cfg(), tr, {"n": 1}, {}, None)
    assert base["trial_n"] == 0 and base["is_base"]


def test_run_json_records_exactly_what_was_applied(tmp_store):
    plug = [{"id": "ema9_exit", "params": {}}, {"id": "volume_surge", "params": {"min_ratio": 3.0}}]
    r = store.save_run(cfg(plug), pd.DataFrame([{"a": 1}]), {"n": 7}, {"base_id": "x"}, "x", notes="hello")
    saved = json.loads((tmp_store / r["id"] / "run.json").read_text())
    assert sorted(p["id"] for p in saved["config"]["plugins"]) == ["ema9_exit", "volume_surge"]
    assert {p["id"]: p["params"] for p in saved["config"]["plugins"]}["volume_surge"] == {"min_ratio": 3.0}
    assert saved["notes"] == "hello" and saved["config"]["years"] == [2026] and saved["git"] is not None
    assert "Entry: Volume surge" in saved["name"] and "Exit: EMA9 break" in saved["name"]


def test_update_and_delete(tmp_store):
    r = store.save_run(cfg(), pd.DataFrame([{"a": 1}]), {"n": 1}, {}, None)
    assert store.update_run(r["id"], name="mine", starred=True)["starred"] is True
    assert store.get_run(r["id"])["name"] == "mine"
    assert store.delete_run(r["id"]) and store.get_run(r["id"]) is None
    with pytest.raises(ValueError):
        store.trades_path("../etc")


def test_a_cancelled_build_raises_and_writes_nothing(monkeypatch, tmp_path):
    import threading
    from btlab import base as B
    monkeypatch.setattr(B, "LAB_CACHE", tmp_path)
    monkeypatch.setattr(B, "cached_symbols", lambda year, market="NSE": ["AAA", "BBB", "CCC"])
    monkeypatch.setattr(B, "build_symbol", lambda *a, **k: {"cands": [], "days": {}, "funnel": {}, "error": "", "symbol": "X"})
    ev = threading.Event()
    ev.set()
    with pytest.raises(B.BuildCancelled):
        B.build_year(2099, B.BaseRule(), jobs=1, cancel=ev)
    assert not list(tmp_path.iterdir())


def test_vwap_cross_counts_bars_since_the_close_went_above_vwap():
    import numpy as np
    n = 8
    arr = {"m5_min": np.arange(n) * 5 + 570, "m5_vwap": np.full(n, 100.0),
           "m5_c": np.array([101, 99, 99, 101, 102, 101, 99, 101], dtype=float)}   # crosses at bars 3 and 7
    df = pd.DataFrame({"day_key": [KEY] * 5, "decision_min": [570 + 5 * (j + 1) for j in (0, 3, 5, 6, 7)]})
    got = runner.stamp_vwap_cross(df, {KEY: arr})
    assert np.isnan(got[0])      # above since the open: no cross to measure
    assert got[1] == 0           # the decision bar itself crossed
    assert got[2] == 2           # crossed two bars earlier, still above
    assert np.isnan(got[3])      # under VWAP now
    assert got[4] == 0


def test_whatif_can_evaluate_vwap_cross(monkeypatch):
    import numpy as np
    from dataclasses import asdict
    from btlab import base as B, whatif
    arr = day(20)
    arr["m5_c"][:2] = [99.0, 101.0]
    arr["m5_vwap"] = np.full(len(arr["m5_min"]), 100.0)
    candidate = cand(decision_min=610, fill_min=612, fill_k=12)
    config = cfg()
    config["base"] = asdict(B.BaseRule())
    monkeypatch.setattr(whatif.store, "get_run", lambda _id: {"config": config})
    monkeypatch.setattr(whatif.service, "ensure_trades", lambda _id: pd.DataFrame([
        {"date": candidate["date"], "symbol": "AAA", "entry_time": "10:12", "setup": "hammer"}]))
    monkeypatch.setattr(whatif, "_load", lambda _cfg: (pd.DataFrame([candidate]), {KEY: arr}))
    got = whatif.evaluate("run", 0, [{"id": "vwap_cross"}])
    assert got["scenarios"][1]["entry_ok"] is True


def test_news_reaction_measures_the_move_since_a_material_filing_as_of_the_decision_bar(tmp_path, monkeypatch):
    import json
    import numpy as np
    from btlab import news
    monkeypatch.setattr(news, "CACHE", tmp_path)
    prior = pd.DataFrame({"open": [95.0], "close": [95.0]},
                         index=pd.to_datetime(["2026-02-27T15:29:00+05:30"]))
    monkeypatch.setattr(news, "_reaction_bars", lambda symbol, year: prior)
    IST = "+05:30"
    rows = [
        {"t": f"2026-03-02T09:50:00{IST}", "h": "AAA: Bagging/Receiving of orders/contracts", "u": "", "b": ""},   # in-session, material
        {"t": f"2026-03-01T20:00:00{IST}", "h": "AAA: Financial Results", "u": "", "b": ""},                          # overnight, material
        {"t": f"2026-03-02T09:40:00{IST}", "h": "AAA: Clarification - Spurt in Volume", "u": "", "b": ""},            # exchange query: never
        {"t": f"2026-03-02T10:05:00{IST}", "h": "AAA: Financial Results", "u": "", "b": ""},                          # AFTER the decision bar
    ]
    (tmp_path / "AAA.json").write_text(json.dumps({"covered": [], "covered_text": [], "rows": rows}))
    n = 12
    arr = {"m1_min": np.arange(n * 5) + 555, "m1_o": np.full(n * 5, 98.0)}
    arr["m1_o"][35] = 99.0                                                    # the 09:50 minute opens at 99
    df = pd.DataFrame([dict(symbol="AAA", date="2026-03-02", day_key=KEY, decision_min=600, prev_close=95.0, close5=101.0)])
    got = news.stamp_reaction(df, {KEY: arr}, 24, "material")
    assert got[0] == pytest.approx((101 / 95 - 1) * 100)         # the overnight filing (ref = prev close) beats 09:50's +2.0%
    none = news.stamp_reaction(df, {KEY: arr}, 0.5, "material")  # window only reaches back to 09:30: only the query + 09:50 filing
    assert none[0] == pytest.approx((101 / 99 - 1) * 100)        # in-session ref = the first post-filing minute open
    assert np.isnan(news.stamp_reaction(df.assign(decision_min=580), {KEY: arr}, 0.5, "material")[0])   # 09:40: only the query


def test_news_reaction_uses_the_post_filing_open_on_a_prior_trading_day(tmp_path, monkeypatch):
    import numpy as np
    from btlab import news
    monkeypatch.setattr(news, "CACHE", tmp_path)
    (tmp_path / "AAA.json").write_text(json.dumps({"covered": [], "rows": [
        {"t": "2026-03-06T11:00:30+05:30", "h": "AAA: Financial Results"}]}))
    prior = pd.DataFrame({"open": [99.0, 100.0, 110.0], "close": [99.0, 100.0, 110.0]},
                         index=pd.to_datetime(["2026-03-06T11:00:00+05:30",
                                               "2026-03-06T11:01:00+05:30",
                                               "2026-03-06T15:29:00+05:30"]))
    monkeypatch.setattr(news, "_reaction_bars", lambda symbol, year: prior)
    key = "AAA|2026-03-09"
    arr = {"m1_min": np.arange(555, 605), "m1_o": np.full(50, 105.0)}
    df = pd.DataFrame([dict(symbol="AAA", date="2026-03-09", day_key=key,
                            decision_min=600, prev_close=110.0, close5=105.0)])
    assert news.stamp_reaction(df, {key: arr}, 72, "material")[0] == pytest.approx(5.0)


def test_news_reaction_excludes_the_price_move_before_an_intrabar_filing(tmp_path, monkeypatch):
    import json
    import numpy as np
    from btlab import news
    monkeypatch.setattr(news, "CACHE", tmp_path)
    (tmp_path / "AAA.json").write_text(json.dumps({"covered": [], "rows": [
        {"t": "2026-03-02T09:53:30+05:30", "h": "AAA: Financial Results", "u": "", "b": ""}]}))
    arr = {"m1_min": np.arange(555, 605), "m1_o": np.full(50, 100.0)}
    arr["m1_o"][39] = 103.0  # 09:54, after the filing; the earlier rise must not count
    df = pd.DataFrame([dict(symbol="AAA", date="2026-03-02", day_key=KEY,
                            decision_min=600, prev_close=95.0, close5=103.0)])
    assert news.stamp_reaction(df, {KEY: arr}, 24, "material")[0] == pytest.approx(0.0)
    assert np.isnan(news.stamp_reaction(df.assign(decision_min=594), {KEY: arr}, 24, "material")[0])


def test_news_reaction_plugin_needs_move_and_rvol():
    df = pd.DataFrame([cand(news_move_pct=3.0, rvol=4.0), cand(news_move_pct=1.0, rvol=4.0),
                       cand(news_move_pct=float("nan"), rvol=4.0), cand(news_move_pct=3.0, rvol=2.0)])
    p = P.REGISTRY["news_reaction"]
    got = p.entry(df, P.resolve_params(p, None)).tolist()
    assert got == [True, False, False, False]


def test_sec_tiers_separate_catalysts_from_dilution_and_paperwork():
    from btlab import news_sec as S
    assert S.sec_tier("8-K", "2.02,9.01") == "material"          # results
    assert S.sec_tier("8-K", "7.01,9.01") == "material"          # Reg FD: where small caps file press releases
    assert S.sec_tier("8-K", "5.02") == "unclear"                # officer change
    assert S.sec_tier("8-K", "3.02") == "offering"               # unregistered share sale
    assert S.sec_tier("424B5", "") == "offering"
    assert S.sec_tier("8-K", "9.01") == "routine"
    assert S.sec_tier("6-K", "") == "material"
    assert S.sec_tier("10-Q", "") == "unclear"
    assert S.sec_tier("DEF 14A", "") == "routine"
    assert S.headline("8-K", "2.02,9.01") == "8-K · 2.02 Results of operations"


def test_news_reaction_on_the_us_tape_reads_the_edgar_cache_in_new_york_time(tmp_path, monkeypatch):
    import json
    import numpy as np
    from btlab import news, news_sec
    monkeypatch.setattr(news_sec, "CACHE", tmp_path)
    rows = [
        {"t": "2026-03-02T12:30:00+00:00", "f": "8-K", "i": "7.01,9.01", "u": ""},   # 07:30 ET pre-market press release -> ref = previous close
        {"t": "2026-03-02T14:50:00+00:00", "f": "8-K", "i": "3.02", "u": ""},        # 09:50 ET share offering: never counts
        {"t": "2026-03-02T15:10:00+00:00", "f": "8-K", "i": "5.02", "u": ""},        # 10:10 ET officer change: unclear
    ]
    (tmp_path / "AAA.json").write_text(json.dumps({"cik": 1, "covered": [], "rows": rows}))
    n = 60
    m1 = np.arange(n) * 1 + 570                                                     # 1-minute bars from 09:30 New York
    o = np.full(n, 11.0)
    o[40] = 12.0                                                                    # the 10:10 bar opens at 12
    arr = {"m1_min": m1, "m1_o": o}
    df = pd.DataFrame([dict(symbol="AAA", date="2026-03-02", day_key=KEY, decision_min=620, prev_close=10.0, close5=13.0)])
    got = news.stamp_reaction(df, {KEY: arr}, 24, "material", "US")
    assert got[0] == pytest.approx(30.0)                                            # 13 vs the previous close of 10 (pre-market news)
    assert np.isnan(news.stamp_reaction(df, {KEY: arr}, 1.5, "material", "US")[0])  # the 07:30 release is outside a 1.5 h window ending 10:20
    loose = news.stamp_reaction(df, {KEY: arr}, 1.5, "material+unclear", "US")
    assert loose[0] == pytest.approx((13.0 / 12.0 - 1) * 100)                       # the 5.02 at 10:10 counts: reference = first open at or after it
    assert news.missing_symbols({"AAA": (pd.Timestamp("2026-03-01").date(), pd.Timestamp("2026-03-02").date())}, "US") == ["AAA"]   # cache has no covered range yet


def test_prefetch_for_the_us_refuses_without_an_sec_contact(monkeypatch):
    from btlab import news, news_sec
    monkeypatch.setattr(news_sec, "user_agent", lambda: "")
    r = news.prefetch_start({"AAA": (pd.Timestamp("2026-03-01").date(), pd.Timestamp("2026-03-02").date())}, "US")
    assert "SEC_USER_AGENT" in r["error"]


def test_chart_marks_for_a_us_trade_come_from_the_edgar_cache(tmp_path, monkeypatch):
    import json
    import bt32_strategy_report as bt32
    from btlab import news_sec
    monkeypatch.setattr(news_sec, "CACHE", tmp_path)
    rows = [{"t": "2026-03-02T12:30:00+00:00", "f": "8-K", "i": "7.01", "u": ""},      # 07:30 ET, before the open
            {"t": "2026-03-02T15:00:00+00:00", "f": "424B5", "i": "", "u": ""},        # 10:00 ET offering
            {"t": "2026-03-02T15:05:00+00:00", "f": "DEF 14A", "i": "", "u": ""},      # routine: left out
            {"t": "2026-02-20T15:00:00+00:00", "f": "8-K", "i": "2.02", "u": ""}]      # too old
    (tmp_path / "AAA.json").write_text(json.dumps({"cik": 1, "covered": [], "rows": rows}))
    got = bt32.news_marks("AAA", pd.Timestamp("2026-03-02"), True)
    assert [(g["t"], g["pre"], g["k"]) for g in got] == [("07:30", True, "material"), ("10:00", False, "offering")]
    assert got[0]["h"] == "8-K · 7.01 Reg FD disclosure"


def test_entry_status_badges_a_trade_from_the_cache_and_stays_silent_when_not_fetched(tmp_path, monkeypatch):
    import json
    from btlab import news, news_sec
    monkeypatch.setattr(news_sec, "CACHE", tmp_path)
    rows = [{"t": "2026-04-10T01:35:00+00:00", "f": "8-K", "i": "1.01,8.01", "u": ""}]       # 21:35 ET on 9 Apr
    (tmp_path / "MEDS.json").write_text(json.dumps({"cik": 1, "covered": [["2026-04-01", "2026-04-30"]], "rows": rows}))
    st = news.entry_status("MEDS", "2026-04-10", "13:09", "US")
    assert st["status"] == "news" and "1.01 Material agreement" in st["items"][0]
    assert news.entry_status("MEDS", "2026-04-10", "13:09", "US", hours=10)["status"] == "none"   # 21:35 is 15.5 h earlier
    assert news.entry_status("ZZZ", "2026-04-10", "13:09", "US") is None                          # never fetched: no false 'no filing'
