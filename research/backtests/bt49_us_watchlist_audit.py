"""BT49 — US watchlist audit: was every watchlisted stock really untradeable?

The US arm watchlisted 18 stocks on its first full session and took no trade.
The live log only says which check refused each minute, and it short-circuits:
a red candle is logged as `attention_red_or_flat` and nothing after it is
evaluated. That cannot answer "was there a trade the rules threw away?".

This script answers it from the outside:

  1. **Every guide-shaped setup in the day.** For every closed 1-minute bar
     09:30-15:10 ET it runs `setups.micro_pullback` — the engine's own
     definition of the guide's entry (2+ green bars, a 1-2 bar red/doji pause,
     a bar breaking the pause high) — WITHOUT any of the extra gates. Each
     distinct pause is one setup.
  2. **Every gate, evaluated independently** (no short-circuit), with the
     engine's own functions: day change, RVOL, 5-minute trend and VWAP
     context, whether the stock had been promoted to attention, the
     confirmation candle (green, strong close, 2.5x volume), light pullback
     volume, 1-minute MACD positive and rising, headroom to resistance,
     first/second pullback, and the US cost-over-risk gate.
  3. **What the trade would have done** had every gate been waived: the
     candidate is armed exactly as the live session arms one (decision at the
     bar's close, 3-minute expiry, 1% chase cap, filled from a quote strictly
     after the decision) and then managed bar by bar by `engine.step` — the
     real exits and the real US cost model (IBKR per-share fees + spread).

Everything is computed on bars up to the decision bar only. Descriptive audit
of ONE session — not a test, and not a reason to change a rule on its own.

Example (repository root):
  apps/signal-engine/.venv/bin/python research/backtests/bt49_us_watchlist_audit.py \
      --date 2026-09-24
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import os
import sys
from datetime import date as Date
from pathlib import Path
from typing import Any
from unittest import mock

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))

# The prod US task's environment (ecs-scanner.yml / task-def mt-us-scanner-prod),
# so Settings() builds the config the live session actually ran.
PROD_ENV = {
    "MT_STRATEGY": "warrior_strict", "MT_DISCIPLINE": "true",
    "MT_ONE_TRADE_PER_DAY": "false", "MT_MACD_OPEN_TOLERANCE": "0",
    "MT_GIVEBACK_HALT": "false", "MT_ATTENTION_RVOL_MIN": "1.5",
    "MT_US_PEAK_HOURS_ONLY": "false", "MT_US_RISK_USD": "50",
    "MT_US_MAX_NOTIONAL_USD": "5000", "MOMENTUM_SCANNER_ONLY": "true",
}
for _k, _v in PROD_ENV.items():
    os.environ.setdefault(_k, _v)

from src.config import Settings  # noqa: E402
from src.momentum_trader import engine as E  # noqa: E402
from src.momentum_trader import us_risk  # noqa: E402
from src.momentum_trader import us_session as S  # noqa: E402
from src.momentum_trader import exits  # noqa: E402
from src.momentum_trader.indicators import macd, volume_ratio  # noqa: E402
from src.momentum_trader.levels import derive_levels, is_structural  # noqa: E402
from src.momentum_trader.pullback import pullback_ordinal  # noqa: E402
from src.momentum_trader.setups import MICRO_PAUSE_MAX_BARS, Setup, micro_pullback  # noqa: E402
from src.momentum_trader.us_universe import USUniverseConfig  # noqa: E402
from src.momentum_trader.yahoo_feed import ET_TZ, YahooFeed, _normalise  # noqa: E402

logging.basicConfig(level=logging.ERROR)
_PLAN = us_risk.plan_trade
OUT_DIR = ROOT / "research" / "backtests"

# Gate keys in the order the engine applies them, with plain-English labels.
GATES: list[tuple[str, str]] = [
    ("day_chg", "up ≥10% on the day"),
    ("rvol", "time-of-day RVOL ≥1.5"),
    ("trend_5m", "5-min EMA9 above EMA20"),
    ("above_vwap", "price above VWAP"),
    ("promoted", "already promoted to attention"),
    ("green", "breakout candle is green"),
    ("strong_close", "closes in top 40% of its range"),
    ("volume", "breakout volume ≥2.5× recent"),
    ("light_pause", "pullback on light volume"),
    ("macd_pos", "1-min MACD histogram > 0"),
    ("macd_open", "1-min MACD histogram rising"),
    ("headroom", "room to next resistance"),
    ("ordinal", "1st or 2nd pullback"),
    ("cost", "costs ≤25% of $ at risk"),
]


def _mongo_db(name: str = "portfolio_analyzer") -> Any:
    from pymongo import MongoClient
    uri = os.getenv("MONGODB_URI") or Settings().mongodb_uri
    return MongoClient(uri, serverSelectionTimeoutMS=8_000)[name]


def load_watchlist(day: str) -> tuple[list[dict], dict]:
    db = _mongo_db()
    doc = db.mt_us_watchlist.find_one({"market": "US", "date": day})
    if doc is None:
        raise SystemExit(f"no mt_us_watchlist document for {day}")
    names = [n for n in doc["names"] if n.get("first_passed_at")]
    start = pd.Timestamp(day, tz=ET_TZ).tz_convert("UTC").to_pydatetime()
    live: dict[str, list[dict]] = {}
    for c in db.mt_us_candidates.find(
        {"market": "US", "time": {"$gte": start, "$lt": start + pd.Timedelta(days=1)}},
        {"symbol": 1, "time": 1, "kind": 1, "reason": 1, "_id": 0},
    ):
        t = pd.Timestamp(c["time"]).tz_localize("UTC").tz_convert(ET_TZ)
        live.setdefault(c["symbol"], []).append(
            {"t": t.strftime("%H:%M"), "kind": c["kind"], "reason": c["reason"]})
    meta = {k: doc.get(k) for k in ("considered", "passed", "updated_at", "rejected_by")}
    meta["live"] = live
    return names, meta


def session_bars(feed: YahooFeed, symbol: str, day: pd.Timestamp) -> pd.DataFrame:
    raw = _normalise(feed._history_range(symbol, day, day + pd.Timedelta(days=1)))
    t = raw.index.time
    return raw[(t >= pd.Timestamp("09:30").time()) & (t < pd.Timestamp("16:00").time())]


def base_state(feed: YahooFeed, symbol: str, day: pd.Timestamp) -> E.DayState | None:
    """The DayState `USSession.discover` would build, from prior sessions only."""
    daily = feed.daily(symbol, day + pd.Timedelta(hours=9, minutes=30))
    if daily.empty:
        return None
    prev = float(daily["close"].iloc[-1])
    hist = feed.history_1m(symbol, day + pd.Timedelta(hours=9, minutes=30))
    profile = E.build_cum_volume_profile(hist, S.PROFILE_DAYS) if not hist.empty else None
    if profile is not None and profile.empty:
        profile = None
    warm = S._recent_sessions(hist, S.WARMUP_SESSIONS)
    return E.DayState(
        symbol=symbol, prev_close=prev, cum_vol_profile=profile,
        prev_day={"high": float(daily["high"].iloc[-1]), "low": float(daily["low"].iloc[-1]),
                  "close": prev},
        warmup_1m=warm, warmup_5m=E.resample_5m(warm) if warm is not None else None,
    )


def fresh(st: E.DayState) -> E.DayState:
    return E.DayState(symbol=st.symbol, prev_close=st.prev_close,
                      cum_vol_profile=st.cum_vol_profile, prev_day=st.prev_day,
                      warmup_1m=st.warmup_1m, warmup_5m=st.warmup_5m)


def attention_timeline(st0: E.DayState, bars: pd.DataFrame, cfg: E.EngineConfig) -> dict:
    """Replay the real engine all day; record, per bar, whether the stock was
    ALREADY promoted when that bar closed (the precondition for an entry)."""
    st = fresh(st0)
    promoted: dict[pd.Timestamp, bool] = {}
    events: list[dict] = []
    for ts in bars.index:
        promoted[ts] = bool(st.attention)
        n = len(st.attention_events)
        E.step(st, bars.loc[:ts], cfg, lambda _s, _t: (0, ""), allow_replay_fill=False)
        for ev in st.attention_events[n:]:
            events.append({"t": ts.strftime("%H:%M"), "reason": ev.reason})
        st.pending = None      # never let an armed entry block later bars
    return {"promoted": promoted, "events": events}


def evaluate_gates(window: pd.DataFrame, setup: Setup, st: E.DayState,
                   cfg: E.EngineConfig, promoted: bool) -> dict[str, Any]:
    """Every entry gate at this bar, each judged on its own."""
    bar = window.iloc[-1]
    o, h, lo, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
    rng = h - lo
    chg = E.day_change_pct(window, st.prev_close)
    rv = E.rvol_now(window, st.cum_vol_profile)
    tf5 = E._bars_5m(window, None)
    ctx_ok, ctx_reason = E._attention_context(tf5, st.warmup_5m)
    vr = volume_ratio(window, min_periods=cfg.vol_baseline_min_bars).iloc[-1]
    light = micro_pullback(window, max_pause_bars=MICRO_PAUSE_MAX_BARS, require_light_volume=True)
    macd = E._macd_open_state(window, st.warmup_1m)
    ordinal = pullback_ordinal(tf5) if len(tf5) else None
    ord_ok, ord_reason = E._pullback_ordinal_gate(ordinal, cfg)

    # Headroom is only reachable in the engine after the candle checks pass, so
    # it is judged here by handing the resistance test this setup directly.
    fake = Setup(E.ATTENTION_SETUP, setup.trigger, setup.stop, level=setup.trigger)
    with mock.patch.object(E, "_attention_confirmation", return_value=(fake, "confirmed")):
        hr_setup, hr_reason = E._resistance_aware_attention_confirmation(
            window, cfg.attention_confirm_vol_ratio, st.prev_day, cfg.resistance_veto_v2,
            E._guide_for(st, cfg), cfg.volume_shelf_levels, cfg.require_rising_price_volume)
    plan = us_risk.plan_trade(setup.trigger, setup.stop, risk_usd=cfg.risk_inr,
                              max_notional_usd=cfg.max_notional_inr)
    cost_ok = isinstance(plan, us_risk.USTradePlan)

    g: dict[str, Any] = {
        "day_chg": chg >= cfg.attention_day_chg_min,
        "rvol": rv is not None and rv >= cfg.attention_rvol_min,
        "trend_5m": ctx_reason not in ("ema_down", "ema_warmup"),
        "above_vwap": ctx_ok or ctx_reason != "below_vwap",
        "promoted": promoted,
        "green": rng > 0 and c > o,
        "strong_close": rng > 0 and (c - lo) / rng >= E.ONE_MIN_CLOSE_POSITION_MIN,
        "volume": bool(pd.notna(vr) and float(vr) >= cfg.attention_confirm_vol_ratio),
        "light_pause": light is not None,
        "macd_pos": macd is not None and macd[0] > 0,
        "macd_open": macd is not None and macd[0] > 0
                     and not E._macd_closing(macd[0], macd[1], cfg.macd_open_tolerance),
        "headroom": hr_setup is not None,
        "ordinal": ord_ok,
        "cost": cost_ok,
    }
    # `above_vwap` is only knowable when the EMA test passed (same function).
    if ctx_reason in ("ema_down", "ema_warmup"):
        vw = E.session_vwap(tf5)
        g["above_vwap"] = bool(len(vw) and pd.notna(vw.iloc[-1])
                               and float(tf5["close"].iloc[-1]) > float(vw.iloc[-1]))
    detail = {
        "day_chg_pct": round(chg, 1), "rvol": None if rv is None else round(rv, 1),
        "context": ctx_reason, "vol_ratio": None if pd.isna(vr) else round(float(vr), 2),
        "close_pos": None if rng <= 0 else round((c - lo) / rng, 2),
        "macd_hist": None if macd is None else round(macd[0], 5),
        "headroom": hr_reason, "ordinal": ordinal, "ordinal_reason": ord_reason,
        "cost": ("ok (C/R %.2f)" % plan.cost_over_risk) if cost_ok else
                f"{plan.reason}: {plan.detail}",
    }
    return {"gates": g, "detail": detail,
            "failed": [k for k, _ in GATES if not g[k]]}


def simulate(st0: E.DayState, bars: pd.DataFrame, i: int, setup: Setup, chg: float,
             rv: float | None, cfg: E.EngineConfig) -> dict[str, Any]:
    """Arm this setup exactly as the live session would and let `engine.step`
    manage it — every gate waived, the fill and exit rules untouched."""
    st = fresh(st0)
    t = bars.index[i]
    cand = E.Candidate(symbol=st.symbol, time=t,
                       setup=Setup(E.ATTENTION_SETUP, setup.trigger, setup.stop,
                                   level=setup.trigger),
                       day_chg_pct=chg, rvol=rv or 0.0, catalyst=None, event_type="",
                       candle_tags=[])
    decision = t + pd.Timedelta(minutes=1)       # the bar has closed
    st.pending = E.Pending(cand, decision_time=decision,
                           expires_at=decision + pd.Timedelta(minutes=cfg.attention_pending_minutes))
    j0 = None
    for j in range(i + 1, len(bars)):
        ts, bar = bars.index[j], bars.iloc[j]
        if st.position is None:
            if st.pending is None:
                break
            if ts > st.pending.expires_at:
                return {"status": "not_filled", "why": "price never reached the trigger in 3 min"}
            # The first quote after the decision is the bar's open; if the open is
            # below the trigger, a 10-second poll catches the trigger when the bar
            # trades through it.
            o, hgh = float(bar["open"]), float(bar["high"])
            if o >= setup.trigger:
                price, when = o, ts + pd.Timedelta(seconds=1)
            elif hgh >= setup.trigger:
                price, when = setup.trigger, ts + pd.Timedelta(seconds=30)
            else:
                continue
            n_rej = len(st.rejections)
            E.fill_pending_quote(st, when, price, cfg, bars.iloc[:j])
            if st.position is None:
                why = st.rejections[n_rej].reason if len(st.rejections) > n_rej else "unfilled"
                return {"status": "refused_at_fill", "why": why, "price": round(price, 4)}
            j0 = j
            continue
        E.step(st, bars.iloc[: j + 1], cfg, lambda _s, _t: (0, ""), allow_replay_fill=False)
        if st.closed:
            break
    if st.position is not None and not st.closed:
        E.force_close(st, bars.index[-1], float(bars["close"].iloc[-1]), cfg)
    if not st.closed:
        return {"status": "not_filled", "why": "no fill before the close"}
    tr = st.closed[0]
    risk = tr.entry - tr.cand.setup.stop
    held = bars.iloc[j0 + 1: bars.index.get_loc(tr.exit_time) + 1] if j0 is not None else bars.iloc[0:0]
    mfe = (float(held["high"].max()) - tr.entry) / risk if len(held) and risk > 0 else 0.0
    return {
        "status": "traded",
        "entry_time": tr.entry_time.strftime("%H:%M:%S"), "entry": round(tr.entry, 4),
        "stop": round(tr.cand.setup.stop, 4), "target": round(tr.target, 4),
        "exit_time": tr.exit_time.strftime("%H:%M"), "exit": round(tr.exit, 4),
        "exit_reason": tr.exit_reason, "qty": tr.qty,
        "gross_usd": round(tr.gross_inr, 2), "costs_usd": round(tr.costs_inr, 2),
        "net_usd": round(tr.net_inr, 2),
        "r_multiple": round((tr.exit - tr.entry) / risk, 2) if risk > 0 else None,
        "mfe_r": round(mfe, 2),
    }


def macd_series(bars: pd.DataFrame, warm: pd.DataFrame | None) -> list[list[float | None]]:
    """The 1-minute MACD the entry gate reads (warmed with prior sessions),
    per bar. EMAs are causal, so the full-day series equals what each prefix saw."""
    joined = bars
    if warm is not None and not warm.empty:
        joined = pd.concat([warm.iloc[-exits.WARMUP_BARS:], bars])
        joined = joined[~joined.index.duplicated(keep="last")].sort_index()
    m = macd(joined["close"], exits.MACD_FAST, exits.MACD_SLOW, exits.MACD_SIGNAL)
    n = len(bars)
    ref = exits.indicator_frame(bars, warm)["macd_hist"]
    assert (m.hist.iloc[-n:] - ref).abs().max() < 1e-9, "must equal the gate's own number"

    def r(v: float) -> float | None:
        return None if pd.isna(v) else round(float(v), 6)
    return [[r(a), r(b), r(c)] for a, b, c in
            zip(m.line.iloc[-n:], m.signal.iloc[-n:], m.hist.iloc[-n:])]


def levels_as_of(window: pd.DataFrame, prev_day: dict | None, entry: float) -> list[dict]:
    """The levels the resistance test had: bars BEFORE the decision candle, on
    both the 1-minute and 5-minute frames (v1 multi-timeframe view)."""
    prior = window.iloc[:-1]
    if prior.empty:
        return []
    lv = derive_levels(prior, prev_day) + derive_levels(E.resample_5m(prior), prev_day)
    seen: set[tuple[float, str]] = set()
    out = []
    for x in sorted(lv, key=lambda z: -z.strength):
        key = (round(x.price, 3), x.kind)
        if key in seen:
            continue
        seen.add(key)
        out.append({"price": round(x.price, 4), "kind": x.kind, "touches": int(x.touches),
                    "strength": round(float(x.strength), 2), "structural": is_structural(x),
                    "side": "resistance" if x.price > entry else "support"})
    return out[:14]


def audit_symbol(feed: YahooFeed, row: dict, day: pd.Timestamp, cfg: E.EngineConfig,
                 live: list[dict]) -> dict[str, Any]:
    sym = row["symbol"]
    out: dict[str, Any] = {
        "symbol": sym, "first_passed_at": pd.Timestamp(row["first_passed_at"]).strftime("%H:%M"),
        "float_shares": row.get("float_shares"), "screen_rvol": row.get("rvol"),
        "is_warrant": sym.endswith(("W", "-WT", ".W", "WS")) and len(sym) >= 5,
        "live_events": live, "setups": [], "bars": [],
    }
    try:
        st0 = base_state(feed, sym, day)
        bars = session_bars(feed, sym, day)
    except Exception as exc:        # a delisted/warrant symbol Yahoo cannot serve
        out["error"] = f"no Yahoo data: {exc}"
        return out
    if st0 is None or bars.empty:
        out["error"] = "no Yahoo data (no daily or 1-minute bars)"
        return out
    out.update(prev_close=st0.prev_close, has_profile=st0.cum_vol_profile is not None,
               n_bars=len(bars))
    fp = pd.Timestamp(row["first_passed_at"]).tz_convert(ET_TZ)
    after = bars[bars.index >= fp.floor("1min")]
    out["day"] = {
        "open": float(bars["open"].iloc[0]), "high": float(bars["high"].max()),
        "hod_time": bars["high"].idxmax().strftime("%H:%M"),
        "close": float(bars["close"].iloc[-1]),
        "px_at_watch": float(after["open"].iloc[0]) if len(after) else None,
        "high_after_watch": float(after["high"].max()) if len(after) else None,
        "low_after_watch": float(after["low"].min()) if len(after) else None,
    }
    tl = attention_timeline(st0, bars, cfg)
    out["attention_replay"] = tl["events"]
    cutoff = cfg.entry_deadline
    seen: set[tuple[float, float]] = set()
    for i in range(len(bars)):
        ts = bars.index[i]
        if ts.time() >= cutoff:
            break
        window = bars.iloc[: i + 1]
        s = micro_pullback(window, max_pause_bars=MICRO_PAUSE_MAX_BARS)
        if s is None or (round(s.trigger, 4), round(s.stop, 4)) in seen:
            continue
        seen.add((round(s.trigger, 4), round(s.stop, 4)))
        ev = evaluate_gates(window, s, st0, cfg, tl["promoted"][ts])
        rv = ev["detail"]["rvol"]
        sim = simulate(st0, bars, i, s, ev["detail"]["day_chg_pct"], rv, cfg)
        # The US cost gate also runs at the FILL, so "every gate waived" needs it
        # lifted there too. Only these rows are re-run; the rest are unchanged.
        sim_nc = None
        if sim.get("why") == "cost_over_risk":
            with mock.patch.object(us_risk, "plan_trade",
                                   functools.partial(_PLAN, max_cost_over_risk=float("inf"))):
                sim_nc = simulate(st0, bars, i, s, ev["detail"]["day_chg_pct"], rv, cfg)
        n_pause = int(s.meta.get("pause_bars", 1))
        out["setups"].append({
            "time": ts.strftime("%H:%M"), "trigger": round(s.trigger, 4),
            "pause": [bars.index[i - k].strftime("%H:%M") for k in range(n_pause, 0, -1)],
            "levels": levels_as_of(window, st0.prev_day, s.trigger),
            "stop": round(s.stop, 4), "pause_bars": int(s.meta.get("pause_bars", 1)),
            "risk_pct": round(100 * (s.trigger - s.stop) / s.trigger, 2),
            "watched_live": ts >= fp.floor("1min"),
            **ev, "outcome": sim, "outcome_no_cost_gate": sim_nc,
        })
    out["eng"] = macd_series(bars, st0.warmup_1m)
    out["bars"] = [[ts.strftime("%H:%M"), float(r["open"]), float(r["high"]), float(r["low"]),
                    float(r["close"]), float(r["volume"])] for ts, r in bars.iterrows()]
    return out


def summarise(rows: list[dict]) -> dict[str, Any]:
    setups = [s for r in rows for s in r["setups"]]
    traded = [s["outcome"] for s in setups if s["outcome"]["status"] == "traded"]
    # every gate waived, the cost gate at the fill included
    all_in = traded + [s["outcome_no_cost_gate"] for s in setups
                       if (s.get("outcome_no_cost_gate") or {}).get("status") == "traded"]
    net_all = [t["net_usd"] for t in all_in]
    live = [s for s in setups if s["watched_live"]]
    fail_counts: dict[str, int] = {k: 0 for k, _ in GATES}
    for s in setups:
        for k in s["failed"]:
            fail_counts[k] += 1
    net = [t["net_usd"] for t in traded]
    return {
        "stocks": len(rows), "with_data": sum(1 for r in rows if "error" not in r),
        "setups": len(setups), "passed_all": sum(1 for s in setups if not s["failed"]),
        "one_gate_short": sum(1 for s in setups if len(s["failed"]) == 1),
        "would_fill": len(traded),
        "net_usd": round(sum(net), 2),
        "gross_usd": round(sum(t["gross_usd"] for t in traded), 2),
        "costs_usd": round(sum(t["costs_usd"] for t in traded), 2),
        "median_net_usd": round(float(pd.Series(net).median()), 2) if net else 0.0,
        "wins": sum(1 for x in net if x > 0),
        "all_waived": {"fills": len(all_in), "net_usd": round(sum(net_all), 2),
                       "gross_usd": round(sum(t["gross_usd"] for t in all_in), 2),
                       "costs_usd": round(sum(t["costs_usd"] for t in all_in), 2),
                       "wins": sum(1 for x in net_all if x > 0),
                       "median_net_usd": round(float(pd.Series(net_all).median()), 2)
                       if net_all else 0.0},
        "live_setups": len(live),
        "live_fills": sum(1 for s in live if s["outcome"]["status"] == "traded"),
        "live_net_usd": round(sum(s["outcome"]["net_usd"] for s in live
                                  if s["outcome"]["status"] == "traded"), 2),
        "min_failed": min((len(s["failed"]) for s in setups), default=None),
        "fail_counts": fail_counts,
        "gate_labels": dict(GATES),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-09-24")
    ap.add_argument("--cache", default=str(OUT_DIR / ".cache_yahoo_us"))
    ap.add_argument("--output", default=None, help="JSON path (default bt49_us_watchlist_<date>.json)")
    a = ap.parse_args()
    d = Date.fromisoformat(a.date)
    day = pd.Timestamp(a.date, tz=ET_TZ)
    names, meta = load_watchlist(a.date)
    cfg = S.build_engine_config(Settings(), d, USUniverseConfig())
    feed = YahooFeed(cache_dir=Path(a.cache))
    rows = []
    for n in names:
        r = audit_symbol(feed, n, day, cfg, meta["live"].get(n["symbol"], []))
        rows.append(r)
        s = r["setups"]
        print(f'{n["symbol"]:8s} setups={len(s):3d} all-pass={sum(1 for x in s if not x["failed"])} '
              f'1-short={sum(1 for x in s if len(x["failed"]) == 1)} {r.get("error", "")}')
    data = {"date": a.date, "watchlist": {k: v for k, v in meta.items() if k != "live"},
            "config": {"entry_deadline": str(cfg.entry_deadline), "eod_close": str(cfg.eod_close),
                       "risk_usd": cfg.risk_inr, "max_notional_usd": cfg.max_notional_inr,
                       "exit_mode": cfg.exit_mode, "fixed_target": cfg.use_fixed_target},
            "summary": summarise(rows), "stocks": rows}
    out = Path(a.output) if a.output else OUT_DIR / f"bt49_us_watchlist_{a.date}.json"
    out.write_text(json.dumps(data, default=str))
    print(json.dumps(data["summary"], indent=1))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
