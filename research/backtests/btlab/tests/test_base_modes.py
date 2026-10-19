"""The candle-timeframe and entry-trigger options of the base trade (`pattern_tf`, `entry_mode`).

Pure-logic tests run anywhere; the real-data ones skip themselves without the bar cache.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest

from btlab import LAB_VERSION, base, runner, service, store
from btlab import plugins as P
from btlab.paths import cached_symbols
from btlab.tests.test_real_data import FEATURES, YEAR, _same, _scrambled
from btlab.tests.test_runner import KEY, cand, day

MARKET_RULE = base.BaseRule(entry_mode="market")


# ------------------------------------------------------------------ pure logic

@pytest.mark.parametrize("decision, want", [(600, 595), (605, 600), (601, 595), (604, 595), (606, 600), (570, 565)])
def test_last_closed_5m_bar_at_a_decision(decision, want):
    assert base.last5_start(decision) == want


def test_a_5m_decision_reads_its_own_bar_so_the_default_path_is_unchanged():
    for dec in range(570, 900, 5):
        assert base.last5_start(dec) == dec - 5


def test_default_rule_keeps_its_cache_key():
    d = asdict(base.BaseRule())
    del d["market"], d["entry_mode"], d["stop_levels"]     # the shape every key had before the options existed
    from src.momentum_trader.candles import PATTERN_RULES_VERSION
    legacy = hashlib.sha1(json.dumps({**d, "v": LAB_VERSION, "patterns": PATTERN_RULES_VERSION},
                                     sort_keys=True).encode()).hexdigest()[:10]
    assert base.BaseRule().key() == legacy


def test_each_option_gets_its_own_cache_key():
    keys = {base.BaseRule().key(), base.BaseRule(pattern_tf="1m").key(), MARKET_RULE.key(),
            base.BaseRule(pattern_tf="1m", entry_mode="market").key()}
    assert len(keys) == 4


def test_saved_run_keys_ignore_the_default_entry_mode_only():
    cfg = {"years": [2026], "patterns": ["hammer"], "risk_inr": 500, "max_notional_inr": 50000, "max_trades": 0,
           "plugins": [], "base": {"day_chg_min": 4.0, "pattern_tf": "5m"}}
    old = store.config_key(cfg)
    assert store.config_key({**cfg, "base": {**cfg["base"], "entry_mode": "pattern"}}) == old
    assert store.config_key({**cfg, "base": {**cfg["base"], "entry_mode": "market"}}) != old
    assert store.config_key({**cfg, "base": {**cfg["base"], "pattern_tf": "1m"}}) != old


def test_market_fill_is_the_next_minutes_open_plus_slip():
    o = np.array([100.0, 101.0, 102.0])
    mins = np.array([600, 601, 602])
    k, px = base.market_fill(o, mins, 1, base.BaseRule())
    assert k == 1 and px == round(101.0 * 1.0003, 2)


def test_market_fill_obeys_the_entry_cutoff():
    o, mins = np.array([100.0, 100.0]), np.array([14 * 60 + 29, 14 * 60 + 30])
    assert base.market_fill(o, mins, 0, base.BaseRule())[0] == 0
    assert base.market_fill(o, mins, 1, base.BaseRule()) == "cutoff"


def test_pool_ignores_the_pattern_list_only_without_a_pattern_trigger():
    df = pd.DataFrame([cand(pattern="market_entry"), cand(pattern="hammer")])
    pattern = runner.RunConfig(years=[2026], patterns=["hammer"])
    market = runner.RunConfig(years=[2026], rule=MARKET_RULE, patterns=["hammer"])
    assert list(runner.pool(df, pattern)["pattern"]) == ["hammer"]
    assert list(runner.pool(df, market)["pattern"]) == ["market_entry", "hammer"]


def test_a_market_entry_candidate_becomes_a_trade_and_ignores_the_pattern_chips():
    c = cand(pattern="market_entry", prior_trend="", strength=float("nan"), invalidation=float("nan"))
    cfg = runner.RunConfig(years=[2026], rule=MARKET_RULE, patterns=["hammer"])
    tr = runner.run(cfg, pd.DataFrame([c]), {KEY: day()})
    assert list(tr["setup"]) == ["market_entry"]
    assert runner.run(runner.RunConfig(years=[2026], patterns=["hammer"]), pd.DataFrame([c]), {KEY: day()}).empty


def test_vwap_cross_reads_the_last_closed_5m_bar_for_a_1m_decision():
    n = 8
    arr = {"m5_min": np.array([570 + 5 * i for i in range(n)]),
           "m5_c": np.array([9, 9, 9, 11, 11, 11, 11, 11], float), "m5_vwap": np.full(n, 10.0)}
    # decisions at 600 (5m bar 595 just closed) and 602 (595 still the last closed bar) cross at index 5 - 3 = 2 bars ago
    df = pd.DataFrame({"day_key": ["k", "k", "k"], "decision_min": [600, 602, 604]})
    out = runner.stamp_vwap_cross(df, {"k": arr})
    assert out[0] == out[1] == out[2] == 2.0


class _Cached:
    @staticmethod
    def years(market="NSE"):
        return [2026]


@pytest.fixture()
def cached(monkeypatch):
    monkeypatch.setattr(service, "cached_years", _Cached.years)


def test_requests_validate_the_new_settings(cached):
    ok = service.parse_request({"years": [2026], "base": {"pattern_tf": "1m", "entry_mode": "market"}})
    assert ok["base"]["pattern_tf"] == "1m" and ok["base"]["entry_mode"] == "market"
    assert service.parse_request({"years": [2026]})["base"]["entry_mode"] == "pattern"
    for bad in ({"pattern_tf": "3m"}, {"entry_mode": "none"}):
        with pytest.raises(P.PluginError):
            service.parse_request({"years": [2026], "base": bad})


def test_apply_without_a_pattern_needs_an_entry_indicator(cached):
    cfg = service.parse_request({"years": [2026], "base": {"entry_mode": "market"}, "plugins": []})
    with pytest.raises(P.PluginError, match="entry indicator"):
        service._check_no_pattern(cfg["plugins"])
    exit_only = service.parse_request({"years": [2026], "base": {"entry_mode": "market"},
                                       "plugins": [{"id": "swing_trail", "params": {}}]})
    with pytest.raises(P.PluginError, match="entry indicator"):
        service._check_no_pattern(exit_only["plugins"])
    with_news = service.parse_request({"years": [2026], "base": {"entry_mode": "market"},
                                       "plugins": [{"id": "news_reaction", "params": {}}]})
    service._check_no_pattern(with_news["plugins"])           # does not raise


def test_pattern_strength_cannot_be_used_without_a_pattern(cached):
    cfg = service.parse_request({"years": [2026], "base": {"entry_mode": "market"},
                                 "plugins": [{"id": "pattern_strength", "params": {}},
                                             {"id": "above_vwap", "params": {}}]})
    with pytest.raises(P.PluginError, match="needs a candlestick pattern"):
        service._check_no_pattern(cfg["plugins"])


def test_build_and_meta_requests_need_no_indicator(cached):
    """parse_request is also the build / meta path, which sends no indicators."""
    service.parse_request({"years": [2026], "base": {"entry_mode": "market"}, "plugins": []})


def test_rule_text_describes_the_chosen_trigger():
    text = " ".join(service._trigger_text(base.BaseRule(pattern_tf="1m")))
    assert "1-minute" in text and "buy-stop" in text
    text = " ".join(service._trigger_text(MARKET_RULE))
    assert "Trigger: none" in text and "buy-stop" not in text


# ------------------------------------------------------------------ real data

MODES = {"1m_pattern": {"pattern_tf": "1m"},
         "5m_market": {"entry_mode": "market"},
         "1m_market": {"pattern_tf": "1m", "entry_mode": "market"},
         "5m_hybrid": {"entry_mode": "hybrid"},
         "1m_hybrid": {"pattern_tf": "1m", "entry_mode": "hybrid"}}
# the US tape has no NSE-style momentum band in the test cache: widen it so a few symbols still produce candidates
US_BAND = {"day_chg_min": 5.0, "day_chg_max": 100.0, "rvol_min": 1.5}


def _rule(market: str, mode: str) -> base.BaseRule:
    return base.BaseRule(**MODES[mode]) if market == "NSE" else base.BaseRule.for_market("US", **US_BAND, **MODES[mode])


@pytest.fixture(scope="module", params=["NSE", "US"])
def market(request):
    return request.param


@pytest.fixture(scope="module")
def symbols(market):
    if market == "US":
        syms = cached_symbols(YEAR, "US")[:12]
        if not syms:
            pytest.skip("2026 US 1-minute bars are not cached")
        return syms
    got = base.load_year(YEAR, base.BaseRule())
    if got is None or got[0].empty:
        pytest.skip("2026 NSE base data is not built")
    return list(got[0].groupby("symbol").size().sort_values(ascending=False).index[:8])


@pytest.fixture(scope="module", params=list(MODES))
def built(request, market, symbols):
    rule = _rule(market, request.param)
    rows, days = [], {}
    for s in symbols:
        out = base.build_symbol(s, YEAR, rule)
        assert not out["error"], out["error"]
        rows += out["cands"]
        days.update(out["days"])
    df = pd.DataFrame(rows)
    if df.empty:
        pytest.skip(f"no candidates for {request.param}")
    return rule, df, days


def test_candidates_never_fill_before_their_decision_and_keep_a_sane_stop_and_target(built):
    rule, df, _ = built
    assert (df["fill_min"] >= df["decision_min"]).all()
    assert ((df["stop"] < df["fill"]) & (df["fill"] < df["target"])).all()
    if rule.entry_mode == "market":
        assert (df["pattern"] == base.MARKET_SETUP).all()
        assert (df["fill_min"] == df["decision_min"]).all()
        assert df["invalidation"].isna().all() and df["strength"].isna().all()
    else:
        assert (df["trigger"] <= df["fill"] + 1e-9).all()
    mk = rule.mk
    assert df["decision_min"].between(mk.open_min, mk.eod_min + 1).all()
    # prices the Lab computes sit on the market's own tick (a no-pattern "trigger" is just the bar close)
    for col in ("stop", "target") + (() if rule.entry_mode == "market" else ("trigger",)):
        assert (((df[col] / mk.tick) - (df[col] / mk.tick).round()).abs() < 1e-6).all(), col


def test_decision_bars_sit_on_the_chosen_grid(built):
    rule, df, _ = built
    if rule.pattern_tf == "5m":
        assert (df["decision_min"] % 5 == 0).all()
    else:
        assert (df["decision_min"] % 5 != 0).any()


def test_indicators_are_read_from_the_last_closed_5m_bar(built):
    rule, df, days = built
    for _, r in df.sample(min(60, len(df)), random_state=3).iterrows():
        arr = days[r["day_key"]]
        j = int(np.where(arr["m5_min"] == base.last5_start(r["decision_min"]))[0][0])
        assert r["close5"] == pytest.approx(arr["m5_c"][j])
        assert r["vwap5"] == pytest.approx(arr["m5_vwap"][j], nan_ok=True)
        assert r["close_dec"] == pytest.approx(arr["m1_c"][int(np.where(arr["m1_min"] == r["decision_min"] - 1)[0][0])])
        # the 5-minute bar must have CLOSED by the decision (never the one still forming)
        assert int(arr["m5_min"][j]) + 5 <= r["decision_min"]


@pytest.mark.parametrize("when", ["after_fill", "volume_after_decision"])
def test_new_modes_do_not_depend_on_bars_after_the_cut(built, monkeypatch, when):
    rule, df, _ = built
    rng = np.random.default_rng(11)
    real_load = base._load
    checked = 0
    for _, row in df.groupby("symbol").head(1).head(8).iterrows():
        cut = int(row["fill_min"]) + 1 if when == "after_fill" else int(row["decision_min"])
        scrambled = _scrambled(real_load(row["symbol"], YEAR, rule.market), row["date"], cut, rng, prices=when == "after_fill")
        monkeypatch.setattr(base, "_load", lambda s, y, m="NSE", _d=scrambled: _d if y == YEAR else real_load(s, y, m))
        again = pd.DataFrame(base.build_symbol(row["symbol"], YEAR, rule)["cands"])
        monkeypatch.undo()
        hit = again[(again["date"] == row["date"]) & (again["pattern"] == row["pattern"])
                    & (again["decision_min"] == row["decision_min"])] if len(again) else again
        assert len(hit) == 1, f'{row["symbol"]} {row["date"]} {row["pattern"]} {row["decision_min"]} vanished'
        for col in FEATURES + ["close_dec"] + ["fill", "fill_min"] * (when == "after_fill"):
            assert _same(row[col], hit.iloc[0][col]), f'{row["symbol"]} {col}: {row[col]} -> {hit.iloc[0][col]}'
        checked += 1
    assert checked >= 3


# ------------------------------------------------------------------ stop_levels

def _lv(price, kind, touches=1):
    from src.momentum_trader.levels import Level
    return Level(price=price, kind=kind, touches=touches, volume=0.0, strength=float(touches))


def test_local_stop_skips_earlier_sessions_even_under_another_name():
    # DEVYANI 2026-02-04: 120.01 was both the previous day's level and a prior-session high.
    levels = [_lv(120.01, "prev_day"), _lv(120.01, "session_high"), _lv(119.5, "session_pivot", 2),
              _lv(117.30, "pivot_low", 2)]
    allr, local = base.BaseRule(), base.BaseRule(stop_levels="local")
    assert base.support_stop(levels, 120.39, allr)[0].price == 120.01
    sup, stop = base.support_stop(levels, 120.39, local)
    assert sup.kind == "pivot_low" and stop == base.tick_down(117.30 * 0.999)


def test_local_stop_with_no_local_level_means_no_trade():
    levels = [_lv(120.01, "prev_day"), _lv(119.0, "session_high")]
    assert base.support_stop(levels, 120.39, base.BaseRule(stop_levels="local")) == (None, 0.0)


def test_stop_levels_default_keeps_keys_and_local_gets_its_own():
    assert base.BaseRule().key() != base.BaseRule(stop_levels="local").key()
    cfg = {"years": [2026], "patterns": ["hammer"], "risk_inr": 500, "max_notional_inr": 50000, "max_trades": 0,
           "plugins": [], "base": {"day_chg_min": 4.0}}
    old = store.config_key(cfg)
    assert store.config_key({**cfg, "base": {**cfg["base"], "stop_levels": "all"}}) == old
    assert store.config_key({**cfg, "base": {**cfg["base"], "stop_levels": "local"}}) != old


def test_request_rejects_an_unknown_stop_levels():
    with pytest.raises(P.PluginError):
        service.parse_request({"years": [2026], "base": {"stop_levels": "nearest"}})
    assert service.parse_request({"years": [2026], "base": {"stop_levels": "local"}})["base"]["stop_levels"] == "local"
