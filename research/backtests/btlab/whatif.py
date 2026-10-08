"""'What if' for ONE trade of a saved run: add entry / exit indicators to this stock only.

It answers, for the trade as it was taken: would this entry indicator have let it
through, and where would this exit indicator have got out? Each chosen indicator is
tried alone and all together, next to the base trade (no indicators), so the effect
of each one on this single trade is visible.

This is a single-trade illustration, NOT evidence: one trade says nothing about
whether an indicator helps. Use Apply for that.
Entry indicators only say yes/no for this trade (a vetoed trade would be replaced by
the next candidate of the day, which is not simulated here). The base, stop and
sizing come from the run's own configuration.
"""

from __future__ import annotations

from dataclasses import replace

import json

import pandas as pd

from . import base as base_mod
from . import plugins as P
from . import runner, service, store

_cands: dict[str, tuple[pd.DataFrame, dict]] = {}


def _load(cfg: dict) -> tuple[pd.DataFrame, dict]:
    key = json.dumps({"y": cfg["years"], "b": cfg["base"]}, sort_keys=True)
    if key not in _cands:
        if len(_cands) >= 2:
            _cands.pop(next(iter(_cands)))
        rc = service._run_cfg(cfg)
        _cands[key] = runner.load_candidates(rc.years, rc.rule)
    return _cands[key]


def _one(c, days: dict, rc: runner.RunConfig, selected: list[dict]) -> dict:
    """The trade this candidate becomes with `selected`'s exit indicators."""
    mk = rc.rule.mk
    ecfg = replace(P.exit_cfg(selected), market=mk.id)
    stop, stop_source = runner.exit_stop(c, ecfg, mk)
    if stop <= 0:
        return {"error": "stop would be below zero"}
    qty = runner.plan_qty(c.fill, stop, rc.risk_inr, rc.max_notional_inr)
    if qty < 1:
        return {"error": "position size would be 0 with this stop"}
    ex = runner.simulate(days[c.day_key], int(c.fill_k), float(c.fill), float(stop), float(c.target),
                         float(c.resistance), qty, ecfg)
    r = runner._trade_row(c, ex, qty, stop, stop_source, ecfg, rc)
    return {k: r[k] for k in ("exit_time", "exit", "exit_reason", "gross_pct", "net_real_inr",
                              "stop", "qty", "mfe_r", "mae_r")}


def evaluate(run_id: str, i: int, plugins: list[dict]) -> dict:
    rec = store.get_run(run_id)
    if rec is None:
        raise KeyError(run_id)
    tr = service.ensure_trades(run_id)
    if not 0 <= i < len(tr):
        raise KeyError(i)
    row = tr.iloc[i]
    cfg = rec["config"]
    rc = service._run_cfg(cfg)
    cands, days = _load(cfg)
    fm = P.hhmm(row["entry_time"])
    m = cands[(cands["date"].astype(str) == str(row["date"])[:10]) & (cands["symbol"] == row["symbol"])
              & (cands["fill_min"] == fm) & (cands["pattern"] == row["setup"])]
    if m.empty:
        raise LookupError("this trade's candidate is no longer in the base data (rebuild the years)")
    one = m.iloc[[0]]
    selected = P.normalise(plugins)
    if any(s["id"] == "vwap_cross" for s in selected):
        one = one.assign(vwap_cross_bars=runner.stamp_vwap_cross(one, days))
    nr = next((s for s in selected if s["id"] == "news_reaction"), None)
    if nr is not None and rc.rule.mk.id != "NSE":
        raise P.PluginError("News + market reaction needs NSE filings; it is not available for US runs")
    if nr is not None:                                 # reads the filings cache only (no NSE / EDGAR call)
        from . import news
        one = one.assign(news_move_pct=news.stamp_reaction(one, days, nr["params"]["lookback_h"],
                                                           nr["params"]["scope"], rc.rule.mk.id))
    c = next(one.itertuples(index=False))
    entry_sel = [s for s in selected if P.REGISTRY[s["id"]].group == "entry"]
    exit_sel = [s for s in selected if P.REGISTRY[s["id"]].group == "exit"]

    def passes(sel: list[dict]) -> bool:
        return bool(P.entry_mask(one, sel).iloc[0]) if sel else True

    def label(s: dict) -> str:
        pl = P.REGISTRY[s["id"]]
        prm = list(s["params"].values())
        return pl.name + (f" ({' / '.join(map(str, prm))})" if prm else "")

    base_trade = _one(c, days, rc, [])
    sc = [{"label": "Base trade (no indicators)", "kind": "base", "entry_ok": True, "trade": base_trade}]
    for s in entry_sel:
        sc.append({"label": label(s), "kind": "entry", "entry_ok": passes([s]), "trade": base_trade})
    for s in exit_sel:
        sc.append({"label": label(s), "kind": "exit", "entry_ok": True, "trade": _one(c, days, rc, [s])})
    if len(selected) > 1:
        sc.append({"label": "All chosen together", "kind": "combined", "entry_ok": passes(entry_sel),
                   "trade": _one(c, days, rc, exit_sel)})
    saved = {k: (None if pd.isna(row.get(k)) else row.get(k)) for k in
             ("exit_time", "exit", "exit_reason", "gross_pct", "net_real_inr", "stop", "qty")}
    saved = {k: (v.item() if hasattr(v, "item") else v) for k, v in saved.items()}
    return {"saved": saved, "scenarios": sc,
            "note": "One trade is an illustration, not evidence. A vetoed entry would be replaced by the "
                    "day's next candidate (not simulated here)."}
