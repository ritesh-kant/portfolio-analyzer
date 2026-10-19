"""News entry plumbing, coverage and causal post-publication volume."""

import json
from dataclasses import asdict
from datetime import date

import numpy as np
import pandas as pd
import pytest

from btlab import base, news, news_sec, plugins as P, runner, service, store, whatif
from btlab.tests.test_runner import KEY, cand, cfg, day


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(news, "CACHE", tmp_path / "nse")
    monkeypatch.setattr(news_sec, "CACHE", tmp_path / "sec")
    monkeypatch.setattr(store, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(store, "LEDGER", tmp_path / "runs" / "ledger.jsonl")
    news.CACHE.mkdir()
    news_sec.CACHE.mkdir()
    store._cache.clear()
    monkeypatch.setattr(service, "cached_years", lambda market: [2026])
    monkeypatch.setattr(service, "missing_years", lambda config: [])


def filing(at="09:50:00", headline="AAA: Financial Results"):
    return {"t": f"2026-03-02T{at}+05:30", "h": headline}


def write_news(rows):
    news._sym_file("AAA").write_text(json.dumps({"covered": [["2026-02-26", "2026-03-02"]], "rows": rows}))


def tape():
    return {"m1_min": np.arange(555, 660), "m1_o": np.full(105, 100.0)}


def volume_tape():
    dates = list(pd.bdate_range(end="2026-02-27", periods=12)) + [pd.Timestamp("2026-03-02"), pd.Timestamp("2026-03-03")]
    indexes = [pd.date_range(d.tz_localize("Asia/Kolkata") + pd.Timedelta(hours=9, minutes=15), periods=105, freq="min") for d in dates]
    index = indexes[0].append(indexes[1:])
    return pd.DataFrame({"volume": np.ones(len(index))}, index=index)


def news_params(**overrides):
    return P.resolve_params(P.REGISTRY["news_reaction"], overrides)


def test_news_spans_loads_candidates_and_respects_patterns(monkeypatch):
    candidates = pd.DataFrame([cand(), cand(symbol="BBB", pattern="morning_star")])
    monkeypatch.setattr(runner, "load_candidates", lambda *args: (candidates, {}))
    assert service.news_spans(cfg()) == {"AAA": (date(2026, 2, 26), date(2026, 3, 2))}
    monkeypatch.setattr(runner, "load_candidates", lambda *args: (pd.DataFrame(), {}))
    assert service.news_spans(cfg()) == {}


def test_apply_requires_news_then_saves_and_reuses_a_distinct_current_run(isolated, monkeypatch):
    monkeypatch.setattr(runner, "load_candidates", lambda *args: (pd.DataFrame([cand()]), {KEY: day()}))
    body = {"years": [2026], "plugins": [{"id": "news_reaction"}]}
    with pytest.raises(service.NeedNews) as exc:
        service.apply(body)
    assert exc.value.symbols == 1
    write_news([filing(at="08:00:00")])
    first = service.apply(body)
    assert first["metrics"]["n"] == 1
    assert first["config"]["news_data_key"]
    assert first["config"]["indicator_revisions"]["news_reaction"] == store.NEWS_REVISION
    assert service.apply(body)["id"] == first["id"]
    write_news([filing(at="08:01:00")])
    second = service.apply(body)
    assert second["id"] != first["id"]
    trades = pd.read_csv(store.trades_path(second["id"]))
    assert trades.iloc[0]["news_filing_at"].startswith("2026-03-02T08:01")
    assert trades.iloc[0]["news_reference"] == 95.0


@pytest.mark.parametrize("mode", ["pullback", "breakout"])
def test_unsupported_news_trigger_is_rejected_instead_of_discarded(mode):
    with pytest.raises(P.PluginError, match="has no implementation"):
        news_params(trigger=mode)


def test_unknown_news_parameter_is_rejected():
    with pytest.raises(P.PluginError, match="unsupported parameter"):
        news_params(min_mov_pct=5)


def test_legacy_metrics_and_csv_are_preserved_and_missing_csv_is_not_rebuilt(isolated):
    config = cfg([{"id": "news_reaction", "params": {}}])
    rec = store.save_run(config, pd.DataFrame([{"net_real_inr": 123}]), {"win_pct": 60}, {}, None)
    rec["config"]["indicator_revisions"]["news_reaction"] = 2
    path = store.RUNS_DIR / rec["id"] / "run.json"
    path.write_text(json.dumps(rec))
    store._cache.clear()
    original = path.read_bytes()
    assert service.ensure_trades(rec["id"])["net_real_inr"].iloc[0] == 123
    store.trades_path(rec["id"]).unlink()
    with pytest.raises(P.PluginError, match="older indicator revision"):
        service.ensure_trades(rec["id"])
    assert path.read_bytes() == original
    assert not store.trades_path(rec["id"]).exists()


def test_changed_filing_inputs_cannot_rebuild_saved_metrics(isolated, monkeypatch):
    write_news([filing()])
    monkeypatch.setattr(runner, "load_candidates", lambda *args: (pd.DataFrame([cand()]), {KEY: day()}))
    config = cfg([{"id": "news_reaction", "params": news_params()}])
    config["news_data_key"] = news.cache_key(service.news_spans(config))
    write_news([filing(at="09:51:00")])
    with pytest.raises(P.PluginError, match="filing cache has changed"):
        service.compute(config)


def test_filing_fingerprint_ignores_future_news_but_changes_for_input_news(isolated):
    spans = {"AAA": (date(2026, 2, 26), date(2026, 3, 2))}
    write_news([filing()])
    first = news.cache_key(spans)
    write_news([filing(), {"t": "2026-03-03T09:50:00+05:30", "h": "AAA: Financial Results"}])
    assert news.cache_key(spans) == first
    write_news([filing(at="09:51:00")])
    assert news.cache_key(spans) != first


@pytest.mark.parametrize("headline", ["Issue of Securities", "Qualified Institutional Placement", "Preferential issue", "Rights Issue"])
def test_nse_share_offerings_are_excluded_even_in_loose_scope(isolated, headline):
    write_news([filing(headline="AAA: " + headline)])
    assert news.tier(headline) == "offering"
    df = pd.DataFrame([cand(close5=110)])
    assert np.isnan(news.stamp_reaction(df, {KEY: tape()}, 24, "material+unclear")[0])
    assert news.entry_status("AAA", "2026-03-02", "10:00", "NSE")["status"] == "offering"


def test_unknown_us_issuer_is_not_cached_as_successfully_covered(isolated):
    result = news_sec.fetch_symbol(None, {}, "OLD", date(2025, 1, 1), date(2026, 9, 25))
    assert result["cik"] is None and result["covered"] == []
    news_sec.sym_file("AAA").write_text(json.dumps({"cik": None, "covered": [["2025-01-01", "2026-12-31"]], "rows": []}))
    assert news.entry_status("AAA", "2026-03-02", "10:00", "US")["status"] == "unknown"


def test_apply_blocks_unknown_us_coverage_without_refetch_loop(isolated, monkeypatch):
    news_sec.sym_file("AAA").write_text(json.dumps({"cik": None, "covered": [], "rows": []}))
    monkeypatch.setattr(runner, "load_candidates", lambda *args: (pd.DataFrame([cand()]), {KEY: day()}))
    with pytest.raises(P.PluginError, match="coverage is unknown"):
        service.apply({"market": "US", "years": [2026], "plugins": [{"id": "news_reaction"}]})


def test_fetch_retains_a_historical_cik_mapping(isolated, monkeypatch):
    news_sec.sym_file("OLD").write_text(json.dumps({"cik": 123, "covered": [], "rows": []}))
    monkeypatch.setattr(news_sec, "_get", lambda *args: {"filings": {"recent": {}, "files": []}})
    result = news_sec.fetch_symbol(None, {}, "OLD", date(2025, 1, 1), date(2026, 9, 25))
    assert result["cik"] == 123 and result["covered"]


def test_us_whatif_uses_the_us_filings_and_timezone(isolated, monkeypatch):
    news_sec.sym_file("AAA").write_text(json.dumps({"cik": 123, "covered": [["2026-02-26", "2026-03-02"]],
        "rows": [{"t": "2026-03-02T13:00:00+00:00", "f": "8-K", "i": "2.02", "u": ""}]}))
    config = cfg()
    config["base"] = asdict(base.BaseRule.for_market("US"))
    monkeypatch.setattr(store, "get_run", lambda run_id: {"config": config})
    monkeypatch.setattr(service, "ensure_trades", lambda run_id: pd.DataFrame([
        {"date": "2026-03-02", "symbol": "AAA", "entry_time": "10:02", "setup": "hammer"}]))
    monkeypatch.setattr(whatif, "_load", lambda config: (pd.DataFrame([cand()]), {KEY: day()}))
    result = whatif.evaluate("run", 0, [{"id": "news_reaction"}])
    assert result["scenarios"][1]["entry_ok"] is True


def test_post_news_volume_excludes_pre_news_surge_and_future_bars(isolated, monkeypatch):
    write_news([filing()])
    bars = volume_tape()
    today = bars.index.date == date(2026, 3, 2)
    minutes = base._mins(bars.index)
    bars.loc[today & (minutes < 590), "volume"] = 1000  # surge BEFORE filing
    bars.loc[today & (minutes >= 600), "volume"] = 100000  # after decision
    bars.loc[bars.index.date == date(2026, 3, 3), "volume"] = 100000
    monkeypatch.setattr(news, "_volume_bars", lambda *args: bars)
    candidates = pd.DataFrame([cand(close5=105, rvol=20)])
    session = news.stamp_candidates(candidates, {KEY: tape()}, news_params())
    post = news.stamp_candidates(candidates, {KEY: tape()}, news_params(volume_mode="post_news"))
    assert P.entry_mask(session, P.normalise([{"id": "news_reaction"}])).iloc[0]
    assert np.isnan(post.iloc[0]["news_move_pct"])
    assert not P.entry_mask(post, P.normalise([{"id": "news_reaction", "params": {"volume_mode": "post_news"}}])).iloc[0]
    bars.loc[today & (minutes >= 590) & (minutes < 600), "volume"] = 5
    post = news.stamp_candidates(candidates, {KEY: tape()}, news_params(volume_mode="post_news"))
    assert post.iloc[0]["news_rvol"] == 5


def test_post_news_move_and_volume_must_belong_to_the_same_filing(isolated, monkeypatch):
    write_news([filing(at="09:30:00"), filing()])
    bars = volume_tape()
    today = bars.index.date == date(2026, 3, 2)
    minutes = base._mins(bars.index)
    bars.loc[today & (minutes >= 590) & (minutes < 600), "volume"] = 5
    monkeypatch.setattr(news, "_volume_bars", lambda *args: bars)
    arr = tape()
    arr["m1_o"][35] = 101
    got = news.stamp_candidates(pd.DataFrame([cand(close5=105)]), {KEY: arr}, news_params(volume_mode="post_news"))
    assert got.iloc[0]["news_filing_at"].startswith("2026-03-02T09:50")
    assert got.iloc[0]["news_move_pct"] == pytest.approx((105 / 101 - 1) * 100)
    assert got.iloc[0]["news_rvol"] == 5


def test_post_news_volume_fails_closed_without_prior_volume_history(isolated, monkeypatch):
    write_news([filing()])
    monkeypatch.setattr(news, "_volume_bars", lambda *args: None)
    got = news.stamp_candidates(pd.DataFrame([cand(close5=105)]), {KEY: tape()}, news_params(volume_mode="post_news"))
    assert np.isnan(got.iloc[0]["news_move_pct"])


def test_missing_intraday_reference_does_not_count_the_earlier_price_gap(isolated):
    write_news([filing(at="09:20:00")])
    arr = {"m1_min": np.arange(590, 660), "m1_o": np.full(70, 105.0)}
    assert np.isnan(news.stamp_reaction(pd.DataFrame([cand(close5=105)]), {KEY: arr}, 24, "material")[0])
