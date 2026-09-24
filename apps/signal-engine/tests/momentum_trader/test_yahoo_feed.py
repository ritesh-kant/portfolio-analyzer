"""Yahoo + Nasdaq halts adapter. Every network call is faked — no internet."""

from __future__ import annotations

import pandas as pd
import pytest

from src.momentum_trader import yahoo_feed as yf_mod
from src.momentum_trader.us_scanner import USScanner
from src.momentum_trader.us_universe import USUniverseConfig
from src.momentum_trader.yahoo_feed import YahooFeed, build_query, map_exchange, naive_rvol, parse_halts

ET = "America/New_York"


def et(day: str, hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"{day} {hhmm}", tz=ET)


def stamp(ts: pd.Timestamp) -> int:
    return int(ts.tz_convert("UTC").timestamp())


NOW = et("2026-09-24", "10:05")


def quote(sym, price=5.0, chg=15.0, vol=5_000_000, avg=500_000, exch="NCM", at=None, src="Nasdaq Real Time Price"):
    return {
        "symbol": sym, "regularMarketPrice": price, "regularMarketChangePercent": chg,
        "regularMarketVolume": vol, "averageDailyVolume3Month": avg, "exchange": exch,
        "regularMarketTime": stamp(at or NOW - pd.Timedelta(seconds=30)), "quoteSourceName": src,
    }


RSS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:ndaq="http://www.nasdaqtrader.com/"><channel>
<item><ndaq:HaltDate>09/24/2026</ndaq:HaltDate><ndaq:HaltTime>09:58:11.430</ndaq:HaltTime>
 <ndaq:IssueSymbol>HALTD</ndaq:IssueSymbol><ndaq:Market>NASDAQ</ndaq:Market><ndaq:ReasonCode>LUDP</ndaq:ReasonCode>
 <ndaq:ResumptionDate /><ndaq:ResumptionTradeTime /></item>
<item><ndaq:HaltDate>09/24/2026</ndaq:HaltDate><ndaq:HaltTime>09:40:00.000</ndaq:HaltTime>
 <ndaq:IssueSymbol>BACK</ndaq:IssueSymbol><ndaq:Market>NYSE</ndaq:Market><ndaq:ReasonCode>LUDP</ndaq:ReasonCode>
 <ndaq:ResumptionDate>09/24/2026</ndaq:ResumptionDate><ndaq:ResumptionTradeTime>09:45:00</ndaq:ResumptionTradeTime></item>
<item><ndaq:HaltDate>09/24/2026</ndaq:HaltDate><ndaq:HaltTime>10:30:00.000</ndaq:HaltTime>
 <ndaq:IssueSymbol>LATER</ndaq:IssueSymbol><ndaq:Market>NASDAQ</ndaq:Market><ndaq:ReasonCode>LUDP</ndaq:ReasonCode>
 <ndaq:ResumptionDate /><ndaq:ResumptionTradeTime /></item>
<item><ndaq:HaltDate>09/24/2026</ndaq:HaltDate><ndaq:HaltTime>08:00:00.000</ndaq:HaltTime>
 <ndaq:IssueSymbol>NEWSY</ndaq:IssueSymbol><ndaq:Market>NASDAQ</ndaq:Market><ndaq:ReasonCode>T1</ndaq:ReasonCode>
 <ndaq:ResumptionDate /><ndaq:ResumptionTradeTime /></item>
</channel></rss>"""


def feed(tmp_path, quotes=None, info=None, halts=RSS, **kw):
    info = info if info is not None else {}
    return YahooFeed(
        cache_dir=tmp_path,
        screen_fn=lambda q: {"quotes": quotes or [], "total": len(quotes or [])},
        info_fn=lambda s: info[s] if not isinstance(info.get(s), Exception) else (_ for _ in ()).throw(info[s]),
        halts_fetch_fn=(lambda: halts) if not isinstance(halts, Exception) else (lambda: (_ for _ in ()).throw(halts)),
        **kw,
    )


# ── exchange mapping ────────────────────────────────────────────────────────
@pytest.mark.parametrize("code,venue", [
    ("NMS", "NASDAQ"), ("NGM", "NASDAQ"), ("NCM", "NASDAQ"), ("NYQ", "NYSE"),
    ("ASE", "AMEX"), ("PCX", "ARCA"), ("OID", "OTC"), ("PNK", "OTC"),
])
def test_yahoo_codes_map_to_the_names_the_screen_accepts(code, venue):
    """Without this mapping every listed name was rejected as `exchange:NMS`."""
    assert map_exchange(code) == venue


def test_unknown_code_passes_through_so_the_funnel_can_show_it():
    assert map_exchange("XYZ") == "XYZ"


# ── the source query mirrors the screen's own thresholds ────────────────────
def test_query_is_built_from_the_screen_config():
    ops = str(build_query(USUniverseConfig(day_chg_min_pct=12.0, price_max=15.0)).to_dict())
    assert "'percentchange', 12.0" in ops
    assert "15.0" in ops and "'NCM'" in ops and "'OID'" not in ops


# ── RVOL: same tap on both halves ───────────────────────────────────────────
def test_rvol_is_todays_volume_over_the_average_daily_volume():
    assert naive_rvol(5_000_000, 500_000) == pytest.approx(10.0)


@pytest.mark.parametrize("vol,avg", [(1000, 0), (None, 500), (1000, None)])
def test_rvol_unknown_rather_than_invented(vol, avg):
    assert naive_rvol(vol, avg) is None


# ── snapshot ────────────────────────────────────────────────────────────────
def test_snapshot_maps_yahoo_fields(tmp_path):
    q = feed(tmp_path, quotes=[quote("AAAA", price=4.2, chg=31.5)]).snapshot(NOW)
    assert len(q) == 1
    assert (q[0].symbol, q[0].price, q[0].day_chg_pct, q[0].rvol) == ("AAAA", 4.2, 31.5, 10.0)
    assert q[0].has_catalyst is None     # criterion 3 is not sourced from Yahoo


def test_yesterdays_quote_is_dropped_before_the_name_trades_today(tmp_path):
    """Pre-market Yahoo still reports yesterday's change. A +190% name from
    yesterday must not look like a +190% mover at 09:31."""
    yesterday = et("2026-09-23", "16:00")
    f = feed(tmp_path, quotes=[quote("STALE", chg=190.0, at=yesterday), quote("FRESH")])
    got = f.snapshot(NOW)
    assert [x.symbol for x in got] == ["FRESH"]
    assert f.stale_dropped == 1


def test_premarket_stamp_is_also_stale(tmp_path):
    f = feed(tmp_path, quotes=[quote("EARLY", at=et("2026-09-24", "09:10"))])
    assert f.snapshot(NOW) == [] and f.stale_dropped == 1


def test_truncated_screen_is_recorded(tmp_path):
    f = feed(tmp_path)
    f.screen_fn = lambda q: {"quotes": [quote("AAAA")], "total": 400}
    f.snapshot(NOW)
    assert f.describe()["screen_truncated"] is True


def test_delayed_quotes_are_named_in_the_description(tmp_path):
    f = feed(tmp_path, quotes=[quote("NYSE1", exch="NYQ", src="Delayed Quote"), quote("NASD1")])
    f.snapshot(NOW)
    assert f.describe()["delayed_quotes"] == ["NYSE1"]


# ── facts ───────────────────────────────────────────────────────────────────
def test_facts_supply_float_and_the_mapped_venue(tmp_path):
    f = feed(tmp_path, quotes=[quote("AAAA", exch="NMS")], info={"AAAA": {"floatShares": 4e6}})
    f.snapshot(NOW)
    facts = f.facts(["AAAA"])["AAAA"]
    assert facts.float_shares == 4e6 and facts.exchange == "NASDAQ" and facts.as_of


def test_facts_fetched_once_per_symbol_per_day(tmp_path):
    calls = []
    f = feed(tmp_path)
    f.info_fn = lambda s: calls.append(s) or {"floatShares": 1e6}
    f.facts(["AAAA"]); f.facts(["AAAA"])
    assert calls == ["AAAA"]


def test_facts_cache_survives_a_restart(tmp_path):
    """The scanner restarts; it must not refetch 90 names at 10:00."""
    feed(tmp_path, info={"AAAA": {"floatShares": 2e6}}).facts(["AAAA"])
    reborn = feed(tmp_path, info={})         # would KeyError if it fetched
    assert reborn.facts(["AAAA"])["AAAA"].float_shares == 2e6


def test_missing_float_is_none_so_the_screen_skips_and_flags(tmp_path):
    assert feed(tmp_path, info={"AAAA": {}}).facts(["AAAA"])["AAAA"].float_shares is None


def test_one_failing_name_does_not_stop_the_rest(tmp_path):
    f = feed(tmp_path, info={"BAD": RuntimeError("429"), "GOOD": {"floatShares": 3e6}})
    got = f.facts(["BAD", "GOOD"])
    assert got["BAD"].float_shares is None and got["GOOD"].float_shares == 3e6


def test_corrupt_cache_means_refetch_not_crash(tmp_path):
    """The 2026-09-14 NSE crash was a half-written cache file."""
    day = pd.Timestamp.now(tz=ET).date().isoformat()
    (tmp_path / f"yahoo_facts_{day}.json").write_text("{not json")
    assert feed(tmp_path, info={"AAAA": {"floatShares": 1e6}}).facts(["AAAA"])["AAAA"].float_shares == 1e6


# ── bars ────────────────────────────────────────────────────────────────────
def _raw_bars(start: str, n: int) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="1min", tz=ET)
    return pd.DataFrame({"Open": 1.0, "High": 1.1, "Low": 0.9, "Close": 1.05, "Volume": 100,
                         "Dividends": 0, "Stock Splits": 0}, index=idx)


def test_bars_are_normalised_to_the_engine_shape(tmp_path):
    f = feed(tmp_path, history_fn=lambda s: _raw_bars("2026-09-24 09:30", 10), settle_seconds=0)
    bars = f.bars_1m("AAAA", et("2026-09-24", "09:45"))
    assert list(bars.columns) == yf_mod.COLS
    assert str(bars.index.tz) == ET


def test_the_forming_minute_and_the_settle_window_are_held_back(tmp_path):
    """At 09:40:10 the 09:39 bar closed 10s ago — inside a 20s settle window,
    so it is not final yet and must not be read."""
    f = feed(tmp_path, history_fn=lambda s: _raw_bars("2026-09-24 09:30", 11), settle_seconds=20)
    bars = f.bars_1m("AAAA", pd.Timestamp("2026-09-24 09:40:10", tz=ET))
    assert bars.index[-1] == et("2026-09-24", "09:38")


def test_no_bars_is_an_empty_frame_not_an_error(tmp_path):
    assert feed(tmp_path, history_fn=lambda s: pd.DataFrame()).bars_1m("AAAA", NOW).empty


# ── halts ───────────────────────────────────────────────────────────────────
def test_parse_reads_symbol_reason_and_eastern_times():
    rows = {r["symbol"]: r for r in parse_halts(RSS)}
    assert rows["HALTD"]["reason"] == "LUDP"
    assert rows["HALTD"]["halted_at"] == pd.Timestamp("2026-09-24 09:58:11", tz=ET)
    assert rows["BACK"]["resumed_at"] == pd.Timestamp("2026-09-24 09:45:00", tz=ET)


def test_halted_now_excludes_resumed_and_not_yet_halted(tmp_path):
    assert feed(tmp_path).halted(NOW) == {"HALTD", "NEWSY"}


def test_news_pending_halt_is_exposed_as_a_hard_event(tmp_path):
    f = feed(tmp_path)
    f.halted(NOW)
    assert f.news_pending == {"NEWSY"}


def test_halt_feed_outage_keeps_the_last_known_set(tmp_path):
    """An empty set on failure would quietly allow entries into halted names."""
    f = feed(tmp_path)
    assert f.halted(NOW) == {"HALTD", "NEWSY"}
    f.halts_fetch_fn = lambda: (_ for _ in ()).throw(ConnectionError("down"))
    f._halts_at = 0.0                         # expire the 60s cache
    assert f.halted(NOW) == {"HALTD", "NEWSY"}
    assert f.describe()["halts_feed_ok"] is False


# ── news: audit only, and never from the future ─────────────────────────────
def test_recent_news_excludes_items_the_decision_could_not_have_seen(tmp_path):
    items = [{"content": {"pubDate": "2026-09-24T13:00:00Z", "title": "before", "provider": {"displayName": "X"}}},
             {"content": {"pubDate": "2026-09-24T15:00:00Z", "title": "after", "provider": {"displayName": "X"}}}]
    f = feed(tmp_path, news_fn=lambda s: items)
    got = f.recent_news("AAAA", since=et("2026-09-23", "16:00"), now=NOW)   # NOW = 14:05 UTC
    assert [n["title"] for n in got] == ["before"]


# ── end to end through the US loop ──────────────────────────────────────────
def test_us_scanner_runs_the_screen_on_yahoo_data(tmp_path):
    f = feed(
        tmp_path,
        quotes=[quote("TIGHT", exch="NCM"), quote("LOOSE", exch="NMS"), quote("HALTD", exch="NCM"),
                quote("QUIET", vol=600_000)],
        info={"TIGHT": {"floatShares": 4e6}, "LOOSE": {"floatShares": 9e7},
              "HALTD": {"floatShares": 2e6}, "QUIET": {"floatShares": 3e6}},
    )
    result = USScanner(f).step(NOW)
    assert result.tradeable == ["TIGHT"]
    assert result.blocked == {"HALTD": "halted"}
    assert result.summary.rejected_by == {"float": 1, "rvol": 1}   # LOOSE, QUIET (1.2x)
    doc = USScanner(f).watchlist_document(NOW)                     # no screen yet on a fresh loop
    assert doc is None
    scanner = USScanner(f)
    scanner.step(NOW)
    stored = scanner.watchlist_document(NOW)
    assert stored["feed"]["source"] == "yahoo+nasdaq_halts"
    assert "criterion_3" in stored["feed"]


# ── quiet minutes in prior-session history ──────────────────────────────────
def _sparse_day(day: str, minutes: dict[str, float]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([et(day, m) for m in minutes])
    px = list(minutes.values())
    return pd.DataFrame({"open": px, "high": px, "low": px, "close": px,
                         "volume": [500.0] * len(px)}, index=idx)


def test_quiet_minutes_become_flat_zero_volume_bars_at_the_last_price():
    raw = _sparse_day("2026-09-23", {"09:30": 2.0, "09:33": 2.2, "15:59": 2.1})
    out = yf_mod.fill_quiet_minutes(raw)
    assert len(out) == 390 and out.index.is_unique
    gap = out.loc[et("2026-09-23", "09:31")]
    assert (gap["open"], gap["high"], gap["low"], gap["close"], gap["volume"]) == (2.0, 2.0, 2.0, 2.0, 0)
    assert out.loc[et("2026-09-23", "09:33"), "volume"] == 500      # real bars untouched
    assert out["volume"].sum() == 1500                               # no volume invented


def test_a_quiet_open_carries_the_previous_sessions_close():
    raw = pd.concat([_sparse_day("2026-09-22", {"15:59": 3.0}),
                     _sparse_day("2026-09-23", {"10:05": 3.4})])
    out = yf_mod.fill_quiet_minutes(raw)
    assert out.loc[et("2026-09-23", "09:30"), "close"] == 3.0
    # nothing before the first trade in the window has a price, so it is left out
    assert out.index[0] == et("2026-09-22", "15:59")


def test_early_close_days_stop_at_13_00():
    raw = _sparse_day("2026-11-27", {"09:30": 1.0})
    out = yf_mod.fill_quiet_minutes(raw)
    assert out.index[-1] == et("2026-11-27", "12:59") and len(out) == 210


def test_filled_history_gives_a_thin_name_its_ema_warm_up():
    """2026-09-24 GCTK: 13% of prior minutes present -> 3 complete 5m bars,
    so the 20-bar EMA could not warm before the entry window closed."""
    from src.momentum_trader.engine import resample_5m
    mins = {f"{9 + (30 + i) // 60:02d}:{(30 + i) % 60:02d}": 2.0 + i / 1000
            for i in range(0, 390, 8)}                                # 1 minute in 8
    raw = _sparse_day("2026-09-23", mins)
    assert len(resample_5m(raw)) == 0
    assert len(resample_5m(yf_mod.fill_quiet_minutes(raw))) == 78


def test_history_is_filled_but_todays_live_bars_are_not(tmp_path):
    day = pd.concat([_sparse_day("2026-09-23", {"09:30": 1.0, "09:40": 1.1}),
                     _sparse_day("2026-09-24", {"09:30": 1.2, "09:40": 1.3})])
    raw = day.rename(columns=str.capitalize)
    f = feed(tmp_path, history_fn=lambda s: raw, history_range_fn=lambda s, a, b: raw,
             settle_seconds=0)
    now = et("2026-09-24", "09:45")
    assert len(f.history_1m("AAAA", now)) == 390
    live = f.bars_1m("AAAA", now)
    assert len(live[live.index >= et("2026-09-24", "09:30")]) == 2      # gaps kept
    assert len(live) == 4                                                # raw, unfilled
