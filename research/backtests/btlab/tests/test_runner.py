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
                dist_round_pct=0.4, day_key=KEY)
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
    assert list(P.entry_mask(df, P.normalise([{"id": "macd1_open"}]))) == [True, True, False]
    assert P.entry_mask(df, P.normalise([{"id": "min_reward_risk", "params": {"min_rr": 2.0}}])).all()
    assert not P.entry_mask(df, P.normalise([{"id": "min_reward_risk", "params": {"min_rr": 2.5}}])).any()


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
