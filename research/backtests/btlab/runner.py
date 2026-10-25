"""Apply a plugin selection to the candidate table and replay the chosen trades.

Selection (as live would behave):
  1. entry plugins keep the candidates that pass;
  2. candidates are taken in the order their buy-stop FILLS, one position at a time per stock;
  3. after a trade closes the next candidate must fill later than the exit;
  4. at most `max_trades` per symbol per day (default 0 = unlimited; a new entry must fill after the last exit);
  5. at most `max_positions` positions open at once across all stocks (default 0 = unlimited): a candidate
     that would fill while that many are already open is skipped, so it takes no trade and does not count
     against `max_trades`.

A candidate an indicator vetoes is simply not there, so the next one steps up:
this is a replay, not a row deletion, which is why a filtered run can contain
trades the base run never took.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from . import base as base_mod
from . import hybrid
from . import plugins as P
from .sim import CHECKPOINT_RR, simulate
from src.momentum_trader.levels import tap_high_from_arrays

STRESS_SLIP = 0.0040          # bt17's +40 bps/side stress; the CSV's net_inr/net_pct carry it


@dataclass
class RunConfig:
    years: list[int]
    rule: base_mod.BaseRule = field(default_factory=base_mod.BaseRule)
    patterns: list[str] = field(default_factory=lambda: list(base_mod.BULLISH_PATTERNS))
    risk_inr: float = 500.0
    max_notional_inr: float = 50_000.0
    max_trades: int = 0            # 0 = no limit: re-enter whenever a new candidate fills after the last exit
    max_positions: int = 0         # 0 = no limit: positions open at the same time, across all stocks
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


def pool(cands: pd.DataFrame, cfg: RunConfig) -> pd.DataFrame:
    """The candidates a run may take: the chosen patterns, or all of them when no pattern is required."""
    if cfg.rule.entry_mode == "market" or cands.empty:
        return cands
    if cfg.rule.entry_mode == "hybrid":
        return hybrid.pool(cands, cfg.patterns)
    return cands[cands["pattern"].isin(cfg.patterns)]


def plan_qty(entry: float, stop: float, risk_inr: float, max_notional: float) -> int:
    per_share = entry - stop
    if per_share <= 0:
        return 0
    return max(0, min(int(risk_inr // per_share), int(max_notional // entry)))


def stamp_vwap_cross(df: pd.DataFrame, days: dict) -> np.ndarray:
    """5-minute bars since the close last crossed from under VWAP to at/above it, as of the decision bar.

    0 = the decision bar itself crossed. NaN when the close is under VWAP at the decision bar, or
    when it has been above since the first bar of the day (there was no cross to measure).
    Reads only bars up to and including the decision bar.
    """
    out = np.full(len(df), np.nan)
    for i, (key, dec) in enumerate(zip(df["day_key"], df["decision_min"])):
        arr = days[key]
        mins = arr["m5_min"]
        want = base_mod.last5_start(dec)                      # the last 5-minute bar closed at the decision
        j = int(np.searchsorted(mins, want))
        if j >= len(mins) or int(mins[j]) != want:
            continue
        above = arr["m5_c"][: j + 1] >= arr["m5_vwap"][: j + 1]      # NaN VWAP compares False
        if not above[j]:
            continue
        k = j
        while k > 0 and above[k - 1]:
            k -= 1
        if k > 0:
            out[i] = j - k
    return out


def run(cfg: RunConfig, cands: pd.DataFrame, days: dict) -> pd.DataFrame:
    """The closed trades for `cfg`, one row each, in the dashboard's trade-CSV schema."""
    if cands.empty:
        return pd.DataFrame()
    selected = P.normalise(cfg.plugins)
    mk = cfg.rule.mk
    ecfg = replace(P.exit_cfg(selected), market=mk.id)
    df = pool(cands, cfg)
    nparams = P.news_params(selected)
    if any(x["id"] == "vwap_cross" for x in selected):
        df = df.assign(vwap_cross_bars=stamp_vwap_cross(df, days))
    if nparams is not None:
        from . import news
        df = news.stamp_candidates(df, days, nparams, mk.id)
    flow = {x["id"]: x["params"] for x in selected if x["id"] in ("cvd_divergence", "delta_divergence")}
    if flow and not df.empty:
        from . import orderflow
        df = orderflow.stamp_candidates(df, cfg.rule, flow.get("cvd_divergence"), flow.get("delta_divergence"))
    df = P.first_news_bar(df[P.entry_mask(df, selected)], selected)
    rows: list[dict] = []
    order: list[tuple] = []
    state: dict[tuple, tuple[int, int]] = {}                # (day, symbol) -> (minute the last trade exited, trades taken)
    open_exits: dict[str, list[int]] = {}                   # day -> exit minute of every position taken that day
    # Chronological across stocks, so "how many positions are open at this fill" is answerable.
    for c in df.sort_values(["date", "fill_min", "decision_min", "symbol"]).itertuples(index=False):
        free_at, taken = state.get((c.date, c.symbol), (-1, 0))
        if cfg.max_trades and taken >= cfg.max_trades:
            continue
        if c.fill_min <= free_at:
            continue
        exits = open_exits.setdefault(c.date, [])
        if cfg.max_positions and sum(1 for e in exits if e >= c.fill_min) >= cfg.max_positions:
            continue
        arr = days[c.day_key]
        c = with_tap_ceiling(c, arr, ecfg, cfg.rule)
        stop, stop_source = exit_stop(c, ecfg, mk)
        if stop <= 0:
            continue
        qty = plan_qty(c.fill, stop, cfg.risk_inr, cfg.max_notional_inr)
        if qty < 1:
            continue
        ex = simulate(arr, int(c.fill_k), float(c.fill), float(stop), float(c.target),
                      float(c.resistance), qty, ecfg)
        rows.append(_trade_row(c, ex, qty, stop, stop_source, ecfg, cfg))
        order.append((c.date, c.symbol, c.fill_min, c.decision_min))
        state[(c.date, c.symbol)] = (ex.minute, taken + 1)
        exits.append(ex.minute)
    rows = [r for _, r in sorted(zip(order, rows), key=lambda x: x[0])]      # the dashboard's order: day, stock, fill
    return pd.DataFrame(rows)


def with_tap_ceiling(c, arr: dict, ecfg, rule: base_mod.BaseRule):
    """`c` with its target capped at a `tap_high` ceiling, when that is the nearer one.

    BT61 (engine flag `--tap-levels N`): 5-minute highs that were rejected N+ times are added to
    the level set, so the nearest structural resistance can be one the pivot test never saw. Read
    from the 5-minute bars up to and including the decision bar only. The base candidate is
    untouched, which is why this is an exit-group indicator and needs no base rebuild.
    """
    if not ecfg.tap_touches:
        return c
    m5 = arr["m5_min"]
    want = base_mod.last5_start(c.decision_min)
    j = int(np.searchsorted(m5, want))
    if j >= len(m5) or int(m5[j]) != want:
        return c
    buf, tick = rule.target_buffer_pct, rule.mk.tick
    picks = []
    for x in tap_high_from_arrays(arr["m5_h"][: j + 1], arr["m5_c"][: j + 1], arr["m5_v"][: j + 1],
                                  ecfg.tap_touches):
        t = x.price if buf <= 0 else base_mod.tick_down(x.price * (1.0 - buf / 100.0), tick)
        if x.price > c.trigger and t > c.fill:
            picks.append((t, x))
    if not picks:
        return c
    t, x = min(picks, key=lambda p: (p[0], p[1].price))
    if t >= c.target:
        return c
    return c._replace(target=float(t), resistance=float(x.price), resistance_kind="tap_high")


def exit_stop(c, ecfg, mk) -> tuple[float, str]:
    """The stop this candidate trades with: base support, scaled by `stop_mult`.

    `stop_mult` multiplies the stop DISTANCE, so 1.0 is the frozen base stop and 2.0 puts it
    twice as far below the fill. R, the size and an R target all follow the scaled stop.
    """
    stop, source = c.stop, "support"
    if ecfg.stop_mult != 1.0:
        stop = base_mod.tick_down(c.fill - ecfg.stop_mult * (c.fill - stop), mk.tick)
        source += f"x{ecfg.stop_mult:g}"
    return float(stop), source


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
        **{name: getattr(c, name) for name in ("news_move_pct", "news_rvol", "news_filing_at",
                                              "news_headline", "news_reference", "cvd_div_kind") if hasattr(c, name)},
    }
