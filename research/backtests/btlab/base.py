"""The frozen BASE trade and the candidate table every indicator is applied to.

Base rule (decided 2026-10-05, then frozen — change it and every saved run
becomes incomparable, so a change means a new `LAB_VERSION`):

  universe   momentum stocks: day change +4..+8% at the decision bar, time-of-day
             RVOL >= 3, price Rs60-2000, 20-day turnover Rs3-50cr (bt17's own bands)
  trigger    a bullish candlestick pattern from `candles.py` (the v3 detector,
             unchanged) completes on a 5-minute bar
  entry      resting buy-stop one tick above the pattern's high, live for 15
             minutes, cancelled if price trades under the pattern's low first;
             fills at max(trigger, open) (+0.03% slip when it fills at the trigger)
  stop       the nearest STRUCTURAL support below the trigger, 0.10% under it;
             no support, or a stop outside 0.3-3% of the entry, means no trade
  target     the nearest STRUCTURAL resistance above the trigger (prior sessions'
             highs included), 0.15% under it; no resistance means no trade
  time exit  the bar starting 15:14 closes the position at its close

This module builds the CANDIDATE table: every pattern that completed on a
momentum stock and could have been entered, with every indicator value stamped
AS OF the decision bar. Indicators are then applied to this table (plugins.py)
without touching any bar again, which is what makes Apply take seconds.

No look-ahead: indicator series are causal (EWMs, cumulative sums) and are read
at the decision bar only; levels and patterns are recomputed from bars up to and
including the decision bar; the fill search starts at the first minute AFTER it.
"""

from __future__ import annotations

import hashlib
import json
import math
import pickle
import time as _time
from collections import Counter
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from . import LAB_VERSION
from .paths import LAB_CACHE, cached_symbols, upstox_1m_dir

from src.momentum_trader import quality, universe  # noqa: E402
from src.momentum_trader.candles import PATTERN_RULES_VERSION, completed_pattern_matches  # noqa: E402
from src.momentum_trader.engine import (  # noqa: E402
    build_cum_volume_profile,
    build_session_levels,
    resample_5m,
)
from src.momentum_trader.indicators import (  # noqa: E402
    atr,
    ema,
    macd,
    price_volume_slopes,
    session_vwap,
    volume_ratio,
)
from src.momentum_trader.levels import (  # noqa: E402
    SESSION_LEVEL_SESSIONS,
    TARGET_BUFFER_PCT,
    TICK,
    derive_levels,
    is_structural,
    resistance_target,
    swing_pivot_positions,
)
from src.momentum_trader.pullback import pullback_ordinal  # noqa: E402
from src.momentum_trader.risk import MAX_STOP_PCT, MIN_STOP_PCT  # noqa: E402

EOD_CLOSE_MIN = 15 * 60 + 14          # the bar STARTING 15:14 is the last one; exit at its close
MIN_PROFILE_DAYS = 10
PROFILE_DAYS = 20
WARM_SESSIONS = 5                      # prior sessions that warm up EMA200 / MACD

# Bullish formations of the v3 detector. Neutral names (doji, spinning tops) and
# bearish ones are never a long trigger.
BULLISH_PATTERNS = (
    "hammer", "inverted_hammer", "dragonfly_doji", "bullish_engulfing", "tweezer_bottom",
    "morning_star", "morning_doji_star", "three_white_soldiers", "rising_three",
)


@dataclass(frozen=True)
class BaseRule:
    day_chg_min: float = 4.0
    day_chg_max: float = 8.0
    rvol_min: float = 3.0
    fill_valid_minutes: int = 15
    entry_slip_pct: float = 0.03
    stop_buffer_pct: float = 0.10
    target_buffer_pct: float = TARGET_BUFFER_PCT
    session_level_sessions: int = SESSION_LEVEL_SESSIONS
    entry_cutoff_min: int = 14 * 60 + 30
    pattern_tf: str = "5m"

    def key(self) -> str:
        blob = json.dumps({**asdict(self), "v": LAB_VERSION,
                           "patterns": PATTERN_RULES_VERSION}, sort_keys=True)
        return hashlib.sha1(blob.encode()).hexdigest()[:10]


def tick_up(x: float) -> float:
    return round(math.ceil(x / TICK - 1e-9) * TICK, 2)


def tick_down(x: float) -> float:
    return round(math.floor(x / TICK + 1e-9) * TICK, 2)


# ------------------------------------------------------------------ fill search

def find_fill(
    o: np.ndarray, h: np.ndarray, lo: np.ndarray, mins: np.ndarray, start: int,
    trigger: float, invalidation: float, rule: BaseRule,
) -> tuple[int, float] | str:
    """The 1-minute bar a resting buy-stop at `trigger` fills on, or why it never does.

    The order is armed from bar `start` (the first minute AFTER the pattern's
    bar closed). If a bar trades under `invalidation` the setup is dead; that is
    tested BEFORE the trigger on the same bar, because one minute cannot tell us
    which came first and the pessimistic reading is the honest one.
    """
    last = min(len(h), start + rule.fill_valid_minutes)
    for k in range(start, last):
        if mins[k] >= rule.entry_cutoff_min or mins[k] >= EOD_CLOSE_MIN:
            return "cutoff"
        if lo[k] < invalidation:
            return "invalidated"
        if h[k] >= trigger:
            if o[k] > trigger:                     # gapped through it: filled at the open
                return k, float(o[k])
            return k, round(trigger * (1.0 + rule.entry_slip_pct / 100.0), 2)
    return "unfilled"


def support_stop(levels: list, trigger: float, rule: BaseRule) -> tuple[object | None, float]:
    """The nearest structural support that is a usable stop distance away.

    A support within 0.3% of the entry is not a stop, it is noise (`risk.py`'s own
    floor), so the search walks down to the first structural support that clears
    it. Returns (support, stop price); (None, 0) when there is none at all. The
    caller still checks the 3% ceiling.
    """
    below = sorted((x for x in levels if x.price < trigger and is_structural(x)),
                   key=lambda x: -x.price)
    for x in below:
        stop = tick_down(x.price * (1.0 - rule.stop_buffer_pct / 100.0))
        if (trigger - stop) / trigger >= MIN_STOP_PCT:
            return x, stop
    return None, 0.0


def stop_ok(entry: float, stop: float) -> bool:
    if entry <= 0 or stop <= 0 or stop >= entry:
        return False
    dist = (entry - stop) / entry
    return MIN_STOP_PCT <= dist <= MAX_STOP_PCT


# ------------------------------------------------------------------ one symbol

def _load(sym: str, year: int) -> pd.DataFrame | None:
    f = upstox_1m_dir() / f"{sym}_{year}.parquet"
    return pd.read_parquet(f) if f.exists() else None


def _daily(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(df.index.normalize())
    out = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
        "close": g["close"].last(), "volume": g["volume"].sum(),
    })
    out["turnover_cr"] = (df["close"] * df["volume"]).groupby(df.index.normalize()).sum() / 1e7
    return out


def _mins(index: pd.DatetimeIndex) -> np.ndarray:
    return (index.hour * 60 + index.minute).to_numpy(dtype=np.int16)


def day_arrays(day_bars: pd.DataFrame, tf5: pd.DataFrame, ind: dict) -> dict[str, np.ndarray]:
    """The compact per-day store the simulator replays exits on."""
    _, pl = swing_pivot_positions(tf5)
    piv = np.zeros(len(tf5), dtype=bool)
    piv[pl] = True
    return {
        "m1_min": _mins(day_bars.index),
        "m1_o": day_bars["open"].to_numpy(float), "m1_h": day_bars["high"].to_numpy(float),
        "m1_l": day_bars["low"].to_numpy(float), "m1_c": day_bars["close"].to_numpy(float),
        "m5_min": _mins(tf5.index),
        "m5_o": tf5["open"].to_numpy(float), "m5_h": tf5["high"].to_numpy(float),
        "m5_l": tf5["low"].to_numpy(float), "m5_c": tf5["close"].to_numpy(float),
        "m5_v": tf5["volume"].to_numpy(float),
        "m5_ema9": ind["ema9"], "m5_ema20": ind["ema20"], "m5_macd": ind["macd"],
        "m5_vwap": ind["vwap"], "m5_volratio": ind["volratio"], "m5_pivlow": piv,
    }


def build_symbol(sym: str, year: int, rule: BaseRule) -> dict:
    """Every candidate for one symbol-year. Never raises into the pool."""
    out: dict = {"symbol": sym, "cands": [], "days": {}, "funnel": Counter(), "error": ""}
    try:
        _build_symbol(sym, year, rule, out)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    out["funnel"] = dict(out["funnel"])
    return out


def _build_symbol(sym: str, year: int, rule: BaseRule, out: dict) -> None:
    cur = _load(sym, year)
    if cur is None or cur.empty:
        return
    prev = _load(sym, year - 1)
    df = pd.concat([prev.iloc[-45 * 375:], cur]) if prev is not None and not prev.empty else cur
    df = df[~df.index.duplicated(keep="last")].sort_index()
    daily = _daily(df)
    days = list(daily.index)
    groups = {d: g for d, g in df.groupby(df.index.normalize())}
    fn: Counter = out["funnel"]

    for i, d in enumerate(days):
        if d.year != year or i < MIN_PROFILE_DAYS:
            continue
        prev_close = float(daily["close"].iloc[i - 1])
        if prev_close <= 0 or float(daily["high"].iloc[i]) / prev_close - 1.0 < rule.day_chg_min / 100.0:
            continue
        turnover = float((daily["turnover_cr"].iloc[max(0, i - 20):i]).mean())
        if not universe.passes_dynamic(prev_close, turnover)[0]:
            fn["days_universe_fail"] += 1
            continue
        day_bars = groups[d]
        if len(day_bars) < 60:
            continue
        fn["days_scanned"] += 1
        _scan_day(sym, d, i, days, groups, daily, day_bars, prev_close, rule, out)


def _scan_day(sym: str, d: pd.Timestamp, i: int, days: list, groups: dict, daily: pd.DataFrame,
              day_bars: pd.DataFrame, prev_close: float, rule: BaseRule, out: dict) -> None:
    fn: Counter = out["funnel"]
    hist = pd.concat([groups[x] for x in days[max(0, i - PROFILE_DAYS):i]])
    warm = pd.concat([groups[x] for x in days[max(0, i - WARM_SESSIONS):i]])
    profile = build_cum_volume_profile(hist, PROFILE_DAYS)
    prior = days[max(0, i - rule.session_level_sessions):i]
    session_lv = build_session_levels(pd.concat([groups[x] for x in prior]),
                                      rule.session_level_sessions) if rule.session_level_sessions else []
    prev_row = daily.iloc[i - 1]
    prev_day = {"high": float(prev_row["high"]), "low": float(prev_row["low"]), "close": prev_close}

    joined1 = pd.concat([warm, day_bars])
    all5 = resample_5m(joined1)
    today5 = all5.index.normalize() == d
    tf5 = all5[today5]
    if len(tf5) < 12:
        return
    n5 = int(today5.sum())
    m = macd(all5["close"])
    ind = {
        "ema9": ema(all5["close"], 9).to_numpy()[-n5:],
        "ema20": ema(all5["close"], 20).to_numpy()[-n5:],
        "ema200": ema(all5["close"], 200).to_numpy()[-n5:],
        "macd": m.hist.to_numpy()[-n5:],
        "volratio": volume_ratio(all5).to_numpy()[-n5:],
        "vwap": session_vwap(tf5).to_numpy(),
    }
    m1h = macd(joined1["close"]).hist.to_numpy()[-len(day_bars):]

    mins1 = _mins(day_bars.index)
    pos1 = {int(v): k for k, v in enumerate(mins1)}
    o1, h1, l1, c1 = (day_bars[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    cumvol = day_bars["volume"].cumsum().to_numpy(float)
    base_vol = np.array([profile.get(t, np.nan) for t in day_bars.index.time], dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        rvol1 = np.where(base_vol > 0, cumvol / base_vol, np.nan)

    sma20 = float(daily["close"].iloc[i - 20:i].mean()) if i >= 20 else None
    atr_pct = float("nan")
    if i >= 20:
        a = atr(daily.iloc[max(0, i - 40):i], period=14)
        if len(a) and not pd.isna(a.iloc[-1]):
            atr_pct = float(a.iloc[-1]) / prev_close * 100.0
    cq = quality.chart_quality(hist)

    cands: list[dict] = []
    for j in range(len(tf5)):
        start5 = tf5.index[j]
        last1 = pos1.get(int(start5.hour * 60 + start5.minute) + 4)
        if last1 is None:
            continue
        chg = (float(tf5["close"].iloc[j]) / prev_close - 1.0) * 100.0
        rv = float(rvol1[last1])
        if not (rule.day_chg_min <= chg <= rule.day_chg_max) or not (rv >= rule.rvol_min):
            fn["bars_not_momentum"] += 1
            continue
        matches = [x for x in completed_pattern_matches(tf5.iloc[: j + 1], rule.pattern_tf)
                   if x.direction == "bullish"]
        if not matches:
            continue
        bars_now = tf5.iloc[: j + 1]
        levels = derive_levels(bars_now, prev_day) + session_lv
        dec_min = int(start5.hour * 60 + start5.minute) + 5
        for mt in matches:
            fn["patterns"] += 1
            trigger = tick_up(float(mt.confirmation) + TICK)
            sup, stop = support_stop(levels, trigger, rule)
            if sup is None:
                fn["no_support"] += 1
                continue
            if not stop_ok(trigger, stop):
                fn["stop_too_wide"] += 1
                continue
            tgt = resistance_target(levels, trigger, rule.target_buffer_pct)
            if tgt is None:
                fn["no_resistance"] += 1
                continue
            res, target = tgt
            start_k = pos1.get(dec_min)
            if start_k is None:
                fn["no_next_bar"] += 1
                continue
            fill = find_fill(o1, h1, l1, mins1, start_k, trigger, float(mt.invalidation), rule)
            if isinstance(fill, str):
                fn[f"fill_{fill}"] += 1
                continue
            k, fill_px = fill
            if not stop_ok(fill_px, stop) or target <= fill_px:
                fn["stop_not_sane_at_fill"] += 1
                continue
            fn["candidates"] += 1
            f1ok, _ = quality.uptrend_ok(bars_now, prev_close, sma20)
            f3ok, _ = quality.surge_ok(bars_now)
            slopes = price_volume_slopes(day_bars.iloc[: last1 + 1], 4)
            nearest_round = _dist_round(float(tf5["close"].iloc[j]))
            cands.append({
                "symbol": sym, "date": str(d.date()), "pattern": mt.name,
                "prior_trend": mt.prior_trend, "strength": float(mt.strength),
                "decision_min": dec_min, "fill_min": int(mins1[k]), "fill_k": k,
                "trigger": trigger, "invalidation": float(mt.invalidation), "fill": fill_px,
                "stop": stop, "support": float(sup.price), "support_kind": sup.kind,
                "target": target, "resistance": float(res.price), "resistance_kind": res.kind,
                "prev_close": prev_close,
                "day_chg_pct": chg, "rvol": rv,
                "close5": float(tf5["close"].iloc[j]),
                "ema9_5": float(ind["ema9"][j]), "ema20_5": float(ind["ema20"][j]),
                "ema200_5": float(ind["ema200"][j]), "vwap5": float(ind["vwap"][j]),
                "macd5": float(ind["macd"][j]),
                "macd5_prev": float(ind["macd"][j - 1]) if j else float("nan"),
                "macd1": float(m1h[last1]),
                "macd1_prev": float(m1h[last1 - 1]) if last1 else float("nan"),
                "vol_ratio5": float(ind["volratio"][j]),
                "pullback_ord": pullback_ordinal(bars_now) or 0,
                "atr_pct": atr_pct,
                "daily_uptrend": bool(sma20 is None or prev_close >= sma20),
                "f1_uptrend": bool(f1ok), "f2_chart": bool(cq is not None and cq.ok),
                "f3_surge": bool(f3ok),
                "pv_rising": bool(slopes is not None and slopes[0] > 0 and slopes[1] > 0),
                "dist_round_pct": nearest_round,
            })
    if cands:
        for c in cands:
            c["day_key"] = f"{sym}|{d.date()}"
        out["cands"].extend(cands)
        out["days"][f"{sym}|{d.date()}"] = day_arrays(day_bars, tf5, ind)


def _dist_round(price: float, step: float = 0.5) -> float:
    below = price - math.floor(price / step) * step
    return min(below, step - below) / price * 100.0


# ------------------------------------------------------------------ whole year

def _paths(year: int, rule: BaseRule) -> tuple[Path, Path, Path]:
    k = rule.key()
    return (LAB_CACHE / f"cands_{year}_{k}.parquet", LAB_CACHE / f"days_{year}_{k}.pkl",
            LAB_CACHE / f"meta_{year}_{k}.json")


def year_status(year: int, rule: BaseRule) -> dict:
    c, d, m = _paths(year, rule)
    if c.exists() and d.exists() and m.exists():
        meta = json.loads(m.read_text())
        return {"year": year, "built": True, **{k: meta[k] for k in
                ("built_at", "n_candidates", "n_days", "symbols", "elapsed_s") if k in meta}}
    return {"year": year, "built": False, "symbols": len(cached_symbols(year))}


def build_year(year: int, rule: BaseRule, jobs: int | None = None,
               progress: Callable[[int, int, str], None] | None = None) -> dict:
    """Build (or rebuild) the candidate table for one calendar year."""
    LAB_CACHE.mkdir(parents=True, exist_ok=True)
    jobs = jobs or max(2, (os.cpu_count() or 4) - 1)      # CPU-bound: processes, not threads (GIL)
    syms = cached_symbols(year)
    t0 = _time.time()
    rows: list[dict] = []
    days: dict[str, dict] = {}
    funnel: Counter = Counter()
    errors: list[str] = []
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futs = {pool.submit(build_symbol, s, year, rule): s for s in syms}
        for n, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            rows.extend(r["cands"])
            days.update(r["days"])
            funnel.update(r["funnel"])
            if r["error"]:
                errors.append(f'{r["symbol"]}: {r["error"]}')
            if progress:
                progress(n, len(syms), r["symbol"])
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["date", "symbol", "fill_min", "decision_min"]).reset_index(drop=True)
    c, d, m = _paths(year, rule)
    df.to_parquet(c)
    with open(d, "wb") as fh:
        pickle.dump(days, fh, protocol=pickle.HIGHEST_PROTOCOL)
    meta = {"year": year, "built_at": pd.Timestamp.now().isoformat(timespec="seconds"),
            "n_candidates": len(df), "n_days": len(days), "symbols": len(syms),
            "elapsed_s": round(_time.time() - t0, 1), "funnel": dict(funnel),
            "errors": errors[:20], "rule": asdict(rule), "lab_version": LAB_VERSION,
            "patterns_version": PATTERN_RULES_VERSION}
    m.write_text(json.dumps(meta, indent=1))
    return meta


_MEM: dict[tuple[int, str, float], tuple[pd.DataFrame, dict]] = {}


def load_year(year: int, rule: BaseRule) -> tuple[pd.DataFrame, dict] | None:
    """(candidates, per-day arrays) for a built year, memoised per file version."""
    c, d, m = _paths(year, rule)
    if not (c.exists() and d.exists() and m.exists()):
        return None
    key = (year, rule.key(), m.stat().st_mtime)
    if key not in _MEM:
        for old in [k for k in _MEM if k[:2] == key[:2]]:
            del _MEM[old]
        with open(d, "rb") as fh:
            _MEM[key] = (pd.read_parquet(c), pickle.load(fh))
    return _MEM[key]


def year_meta(year: int, rule: BaseRule) -> dict | None:
    m = _paths(year, rule)[2]
    return json.loads(m.read_text()) if m.exists() else None
