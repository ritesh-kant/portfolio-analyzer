"""Orchestration: build jobs, Apply, and rebuilding a run's trades on demand.

`apply` is the one entry point the UI uses. It validates the request, makes sure
the base data for the chosen years exists, runs the indicators, ALSO runs (or
reuses) the plain base trade for the same window so every result is shown
against it, and saves everything.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, replace

import pandas as pd

from . import LAB_VERSION
from . import base as base_mod
from . import markets, metrics, plugins as P, runner, store
from .paths import cached_symbols, cached_years


class NeedBuild(Exception):
    def __init__(self, years: list[int]):
        super().__init__(f"base data not built for {years}")
        self.years = years


def parse_request(body: dict) -> dict:
    """Request -> canonical config dict (validated, defaults filled in)."""
    rule_in = body.get("base") or {}
    market = str(body.get("market") or rule_in.get("market") or "NSE").upper()
    if market not in markets.MARKETS:
        raise P.PluginError(f"unknown market {market!r}")
    mk = markets.get(market)
    avail = cached_years(market)
    years = sorted({int(y) for y in body.get("years") or avail[-1:]})
    bad = [y for y in years if y not in avail]
    if bad or not years:
        raise P.PluginError(f"no cached {mk.id} bars for {bad or 'any year'}")
    given = {k: type(getattr(base_mod.BaseRule, k))(rule_in[k])
             for k in asdict(base_mod.BaseRule()) if k in rule_in and k != "market"}
    rule = base_mod.BaseRule.for_market(market, **given)
    if not 0 < rule.day_chg_min <= rule.day_chg_max <= 1000:
        raise P.PluginError("day change band must satisfy 0 < min <= max <= 1000")
    if rule.pattern_tf not in base_mod.TIMEFRAMES:
        raise P.PluginError(f"candle timeframe must be one of {base_mod.TIMEFRAMES}, not {rule.pattern_tf!r}")
    if rule.entry_mode not in base_mod.ENTRY_MODES:
        raise P.PluginError(f"entry trigger must be one of {base_mod.ENTRY_MODES}, not {rule.entry_mode!r}")
    if rule.stop_levels not in base_mod.STOP_LEVELS:
        raise P.PluginError(f"stop levels must be one of {base_mod.STOP_LEVELS}, not {rule.stop_levels!r}")
    patterns = body.get("patterns") or list(base_mod.BULLISH_PATTERNS)
    unknown = [p for p in patterns if p not in base_mod.BULLISH_PATTERNS]
    if unknown:
        raise P.PluginError(f"not a bullish pattern: {unknown}")
    plugins = P.normalise(body.get("plugins") or [])
    if P.news_bar_trigger(plugins) and rule.entry_mode != "market":
        rule = replace(rule, entry_mode="market")            # the news bar IS the trigger: no candlestick, market buy
    return {
        "years": years, "base": asdict(rule), "patterns": sorted(patterns),
        "risk_inr": min(max(float(body.get("risk_inr", mk.risk)), mk.risk / 10), mk.risk * 100),
        "max_notional_inr": min(max(float(body.get("max_notional_inr", mk.max_notional)),
                                    mk.max_notional / 10), mk.max_notional * 100),
        "max_trades": min(max(int(body.get("max_trades", 0)), 0), 20),
        "max_positions": min(max(int(body.get("max_positions", 0)), 0), 50),
        "plugins": plugins,
    }


def _check_no_pattern(plugins: list[dict]) -> None:
    """Without a pattern nothing but an indicator decides WHEN to buy, so one must be chosen."""
    if not any(P.REGISTRY[x["id"]].group == "entry" for x in plugins if x["id"] in P.REGISTRY):
        raise P.PluginError("With the pattern trigger off, an entry indicator has to decide when to buy "
                            "(for example News and Market reaction); otherwise it would buy on every bar that "
                            "passes the momentum screen.")
    if any(x["id"] == "pattern_strength" for x in plugins):
        raise P.PluginError("'Pattern strength' needs a candlestick pattern; it cannot be used with the pattern trigger off.")


def _run_cfg(cfg: dict) -> runner.RunConfig:
    return runner.RunConfig(years=cfg["years"], rule=base_mod.BaseRule(**cfg["base"]),
                            patterns=cfg["patterns"], risk_inr=cfg["risk_inr"],
                            max_notional_inr=cfg["max_notional_inr"],
                            max_trades=cfg["max_trades"], max_positions=cfg.get("max_positions", 0),
                            plugins=cfg["plugins"])


def missing_years(cfg: dict) -> list[int]:
    rule = base_mod.BaseRule(**cfg["base"])
    return [y for y in cfg["years"] if not base_mod.year_status(y, rule)["built"]]


class NeedNews(Exception):
    """A selected indicator reads NSE filings that were never fetched for some of the symbols."""

    def __init__(self, symbols: int, total: int, market: str = "NSE"):
        super().__init__(f"filings not fetched for {symbols} of {total} symbols")
        self.symbols, self.total, self.market = symbols, total, market


def _uses_news(cfg: dict) -> bool:
    return any(x["id"] in P.NEWS_IDS for x in cfg["plugins"])


def news_spans(cfg: dict) -> dict:
    from . import news
    rc = _run_cfg(cfg)
    cands, _days = runner.load_candidates(rc.years, rc.rule)
    return news.candidate_spans(runner.pool(cands, rc)) if not cands.empty else {}


def compute(cfg: dict) -> pd.DataFrame:
    need = missing_years(cfg)
    if need:
        raise NeedBuild(need)
    rc = _run_cfg(cfg)
    cands, days = runner.load_candidates(rc.years, rc.rule)
    if _uses_news(cfg) and cfg.get("news_data_key"):
        from . import news
        spans = news.candidate_spans(runner.pool(cands, rc)) if not cands.empty else {}
        if news.cache_key(spans, rc.rule.market) != cfg["news_data_key"]:
            raise P.PluginError("The filing cache has changed since this run was saved. Restore its original "
                                "trades.csv, or Apply as a new experiment; saved metrics are preserved.")
    return runner.run(rc, cands, days)


def apply(body: dict, name: str = "", notes: str = "") -> dict:
    cfg = parse_request(body)
    if cfg["base"].get("entry_mode") == "market":
        _check_no_pattern(cfg["plugins"])
    done = None if _uses_news(cfg) else store.find_by_key(store.config_key(cfg))
    if done:
        return {**done, "cached": True, "base": _base_of(done)}
    need = missing_years(cfg)
    if need:
        raise NeedBuild(need)
    base_cfg = {**cfg, "plugins": []}
    if _uses_news(cfg):
        from . import news
        mid = cfg["base"].get("market", "NSE")
        spans = news_spans(cfg)
        unknown = news.unmapped_symbols(spans, mid)
        if unknown:
            raise P.PluginError("SEC coverage is unknown for unmapped ticker(s): "
                                + ", ".join(unknown[:10])
                                + ". Supply their historical CIKs in the SEC cache and fetch again; "
                                  "missing coverage cannot be treated as no news.")
        todo = news.missing_symbols(spans, mid)
        if todo:
            raise NeedNews(len(todo), len(spans), mid)
        cfg["news_data_key"] = news.cache_key(spans, mid)
        done = store.find_by_key(store.config_key(cfg))
        if done:
            return {**done, "cached": True, "base": _base_of(done)}
    if not cfg["plugins"]:
        tr = compute(base_cfg)
        return {**store.save_run(base_cfg, tr, metrics.summarize(tr), {}, None),
                "cached": False, "base": None}
    base_rec = store.find_by_key(store.config_key(base_cfg))
    if base_rec is None:
        base_tr = compute(base_cfg)
        base_rec = store.save_run(base_cfg, base_tr, metrics.summarize(base_tr), {}, None)
    else:
        base_tr = ensure_trades(base_rec["id"])
    tr = compute(cfg)
    vs = {"base_id": base_rec["id"], **metrics.compare(base_tr, tr)}
    rec = store.save_run(cfg, tr, metrics.summarize(tr), vs, base_rec["id"], name=name, notes=notes)
    return {**rec, "cached": False, "base": base_rec}


def _base_of(rec: dict) -> dict | None:
    bid = rec.get("base_id")
    return store.get_run(bid) if bid else None


def ensure_trades(run_id: str) -> pd.DataFrame:
    """The run's trades; recomputed from its saved configuration if the CSV is gone."""
    rec = store.get_run(run_id)
    if rec is None:
        raise KeyError(run_id)
    path = store.trades_path(run_id)
    if path.exists():
        return pd.read_csv(path)
    error = store.rebuild_error(rec)
    if error:
        raise P.PluginError(error)
    tr = compute(rec["config"])
    tr.to_csv(path, index=False)
    return tr


# ----------------------------------------------------------------- build jobs

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_cancels: dict[str, threading.Event] = {}


def start_build(years: list[int], rule: base_mod.BaseRule) -> dict:
    """Build base data for `years` in a background thread; poll `jobs()`."""
    with _jobs_lock:
        for j in _jobs.values():
            if j["state"] == "running" and j["rule"] == rule.key() and set(years) & set(j["years"]):
                return j
        job = {"id": uuid.uuid4().hex[:8], "years": list(years), "rule": rule.key(),
               "state": "running", "done": 0, "total": 0, "current_year": None,
               "started": time.time(), "error": "", "finished_years": []}
        _jobs[job["id"]] = job
        cancel = _cancels[job["id"]] = threading.Event()

    def work() -> None:
        try:
            for y in years:
                job["current_year"], job["done"], job["total"] = y, 0, len(cached_symbols(y, rule.market))

                def prog(n: int, t: int, s: str) -> None:
                    job["done"], job["total"] = n, t

                base_mod.build_year(y, rule, progress=prog, cancel=cancel)
                job["finished_years"].append(y)
            job["state"] = "done"
        except base_mod.BuildCancelled:
            job["state"] = "cancelled"
        except Exception as exc:  # noqa: BLE001
            job["state"], job["error"] = "error", f"{type(exc).__name__}: {exc}"

    threading.Thread(target=work, daemon=True).start()
    return job


def cancel_build(job_id: str) -> bool:
    """Ask a running build to stop. Years already finished stay cached; the one in flight is dropped."""
    with _jobs_lock:
        ev, job = _cancels.get(job_id), _jobs.get(job_id)
        if ev is None or job is None or job["state"] != "running":
            return False
        ev.set()
        return True


def jobs() -> list[dict]:
    with _jobs_lock:
        return [dict(j) for j in _jobs.values()]


def _trigger_text(rule: base_mod.BaseRule) -> list[str]:
    tf = "1-minute" if rule.pattern_tf == "1m" else "5-minute"
    if rule.entry_mode == "market":
        return [f"Trigger: none. Every closed {tf} bar that passes the momentum screen is a candidate, and "
                "the entry indicators decide which one is taken",
                f"Entry: market order at the open of the next minute (+{rule.entry_slip_pct:g}% slip); no price line "
                "from a pattern, so no trigger or invalidation price. Levels are searched around the bar's close"]
    if rule.entry_mode == "hybrid":
        return [f"Trigger: a bullish candlestick pattern, or a doji / spinning top right after a down-move "
                f"(the five candles before it fell), completes on a {tf} bar",
                f"Entry: buy-stop 1 tick over the candle high, live {rule.fill_valid_minutes} min, "
                "cancelled if price trades under the candle low first"]
    return [f"Trigger: a bullish candlestick pattern completes on a {tf} bar",
            f"Entry: buy-stop 1 tick over the pattern high, live {rule.fill_valid_minutes} min, "
            "cancelled if price trades under the pattern low first"]


def meta(rule: base_mod.BaseRule) -> dict:
    mk = rule.mk
    eod = f"{mk.eod_min // 60:02d}:{mk.eod_min % 60:02d} " + ("ET" if mk.id == "US" else "IST")
    return {
        "lab_version": LAB_VERSION,
        "news_revision": store.NEWS_REVISION,
        "market": {"id": mk.id, "glyph": mk.glyph, "currency": mk.currency, "risk": mk.risk,
                   "max_notional": mk.max_notional},
        "markets": [{"id": m.id, "label": m.label, "glyph": m.glyph} for m in markets.MARKETS.values()],
        "years": [base_mod.year_status(y, rule) for y in cached_years(rule.market)],
        "plugins": P.catalog(),
        "patterns": list(base_mod.BULLISH_PATTERNS),
        "base": asdict(rule),
        "base_rule_text": [
            f"Momentum stocks: day change +{rule.day_chg_min:g}% to +{rule.day_chg_max:g}%, "
            f"time-of-day RVOL ≥ {rule.rvol_min:g}, {mk.universe_text}",
            *_trigger_text(rule),
            f"Stop: nearest structural support ≥0.3% below, {rule.stop_buffer_pct:g}% under it "
            + ("(today's own levels only: earlier sessions' highs and the previous day are skipped) "
               if rule.stop_levels == "local" else "")
            + "(a stop beyond 3% means no trade)",
            f"Target: nearest structural resistance, {rule.target_buffer_pct:g}% under it "
            "(earlier sessions' highs included)",
            f"Exit: stop · target · the last bar of the session ({eod}), whichever comes first",
        ],
        "jobs": jobs(),
    }
