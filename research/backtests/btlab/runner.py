"""Apply a plugin selection to the candidate table and replay the chosen trades.

Selection (per symbol per day, exactly as live would behave):
  1. entry plugins keep the candidates that pass;
  2. candidates are taken in the order their buy-stop FILLS, one position at a time;
  3. after a trade closes the next candidate must fill later than the exit;
  4. at most `max_trades` per symbol per day (default 0 = unlimited; a new entry must fill after the last exit).

A candidate an indicator vetoes is simply not there, so the next one steps up:
this is a replay, not a row deletion, which is why a filtered run can contain
trades the base run never took.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from . import base as base_mod
from . import plugins as P
from .sim import CHECKPOINT_RR, simulate

STRESS_SLIP = 0.0040          # bt17's +40 bps/side stress; the CSV's net_inr/net_pct carry it


@dataclass
class RunConfig:
    years: list[int]
    rule: base_mod.BaseRule = field(default_factory=base_mod.BaseRule)
    patterns: list[str] = field(default_factory=lambda: list(base_mod.BULLISH_PATTERNS))
    risk_inr: float = 500.0
    max_notional_inr: float = 50_000.0
    max_trades: int = 0            # 0 = no limit: re-enter whenever a new candidate fills after the last exit
    plugins: list[dict] = field(default_factory=list)


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def load_candidates(years: list[int], rule: base_mod.BaseRule) -> tuple[pd.DataFrame, dict]:
    frames, days = [], {}
    for y in years:
        got = base_mod.load_year(y, rule)
        if got is None:
            raise FileNotFoundError(f"base data for {y} is not built")
        frames.append(got[0])
        days.update(got[1])
    df = pd.concat([f for f in frames if not f.empty], ignore_index=True) if frames else pd.DataFrame()
    return df, days


def plan_qty(entry: float, stop: float, risk_inr: float, max_notional: float) -> int:
    per_share = entry - stop
    if per_share <= 0:
        return 0
    return max(0, min(int(risk_inr // per_share), int(max_notional // entry)))


def run(cfg: RunConfig, cands: pd.DataFrame, days: dict) -> pd.DataFrame:
    """The closed trades for `cfg`, one row each, in the dashboard's trade-CSV schema."""
    if cands.empty:
        return pd.DataFrame()
    selected = P.normalise(cfg.plugins)
    mk = cfg.rule.mk
    ecfg = replace(P.exit_cfg(selected), market=mk.id)
    df = cands[cands["pattern"].isin(cfg.patterns)]
    df = df[P.entry_mask(df, selected)]
    rows: list[dict] = []
    for _, grp in df.sort_values(["date", "symbol", "fill_min", "decision_min"]).groupby(
            ["date", "symbol"], sort=False):
        arr = days[grp["day_key"].iloc[0]]
        free_at, taken = -1, 0
        for c in grp.itertuples(index=False):
            if cfg.max_trades and taken >= cfg.max_trades:
                break
            if c.fill_min <= free_at:
                continue
            stop, stop_source = c.stop, "support"
            if ecfg.stop_mode == "pattern_low":
                alt = base_mod.tick_down(c.invalidation - mk.tick, mk.tick)
                if base_mod.stop_ok(c.fill, alt):
                    stop, stop_source = alt, "pattern_low"
            qty = plan_qty(c.fill, stop, cfg.risk_inr, cfg.max_notional_inr)
            if qty < 1:
                continue
            ex = simulate(arr, int(c.fill_k), float(c.fill), float(stop), float(c.target),
                          float(c.resistance), qty, ecfg)
            rows.append(_trade_row(c, ex, qty, stop, stop_source, ecfg, cfg))
            free_at = ex.minute
            taken += 1
    return pd.DataFrame(rows)


def _trade_row(c, ex, qty: int, stop: float, stop_source: str, ecfg, cfg: RunConfig) -> dict:
    entry, px = float(c.fill), float(ex.price)
    gross = (px - entry) * qty
    costs = cfg.rule.mk.costs(entry, px, qty)
    stress = (entry + px) * qty * STRESS_SLIP
    notional = entry * qty
    risk = (entry - stop) * qty
    if ecfg.target_mode == "resistance":
        target, source = c.target, f"resistance:{c.resistance_kind}"
    elif ecfg.target_mode == "rr":
        target, source = entry + ecfg.target_rr * (entry - stop), "fixed_rr"
    elif ecfg.target_mode == "checkpoint":
        target = entry + CHECKPOINT_RR * (entry - stop)
        source = f"fixed_2r+checkpoint:{c.resistance_kind}"
    else:
        target, source = float("nan"), "none"
    return {
        "date": c.date, "year": int(str(c.date)[:4]), "symbol": c.symbol, "side": "long",
        "currency": cfg.rule.mk.currency,
        "setup": c.pattern, "trigger_time": _hhmm(c.decision_min), "entry_time": _hhmm(c.fill_min),
        "trigger": c.trigger, "entry": entry, "stop": stop,
        "target": round(float(target), 2) if target == target else "",
        "target_source": source,
        "checkpoint": c.target if ecfg.target_mode == "checkpoint" and c.target < target else "",
        "structural_support": c.support, "structural_support_kind": c.support_kind,
        "structural_resistance": c.resistance, "structural_resistance_kind": c.resistance_kind,
        "exit_time": _hhmm(ex.minute), "exit": px, "exit_reason": ex.reason, "qty": qty,
        "day_chg_pct": round(c.day_chg_pct, 3), "rvol": round(c.rvol, 2), "tags": c.pattern,
        "pullback_ord": c.pullback_ord, "stop_source": stop_source,
        "gross_pct": round((px / entry - 1.0) * 100.0, 4),
        "net_pct": round((gross - costs - stress) / notional * 100.0, 4),
        "gross_inr": round(gross, 2), "costs_inr": round(costs, 2),
        "net_inr": round(gross - costs - stress, 2),
        "net_real_inr": round(gross - costs, 2),
        "net_real_pct": round((gross - costs) / notional * 100.0, 4),
        "risk_inr": round(risk, 2), "notional_inr": round(notional, 2),
        "r_multiple": round((gross - costs) / risk, 3) if risk > 0 else 0.0,
        "rr_geom": round(float((c.target - entry) / (entry - stop)), 2),
        "mfe_r": ex.mfe_r, "mae_r": ex.mae_r,
        "strength": round(c.strength, 2),
    }
