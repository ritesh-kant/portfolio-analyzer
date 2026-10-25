"""Order-flow entry indicators: price/CVD divergence and delta divergence (absorption), as the chart draws them.

Python ports of the web chart's detectors, so a Lab run tests the same marks a trader sees:

  apps/web/lib/tv/cvd.ts               CVD from 1-minute bars (each minute's volume signed by its own body)
  apps/web/lib/tv/cvd-divergence.ts    swing pairs of price vs CVD (regular / hidden, bullish / bearish)
  apps/web/lib/tv/footprint.ts         a candle's footprint delta (only the total; the per-row split is not needed)
  apps/web/lib/tv/delta-divergence.ts  trapped-side setups: dominant wick + delta into it, confirmed by a close beyond

Only the LONG side is used (the lab trades longs): bullish CVD divergences and sellers-trapped delta setups.

No look-ahead. Candles are built from the symbol's 1-minute bars over the trade day and two sessions before it,
detected once per day, and every event carries the minute it became KNOWN:
  * a CVD divergence is known when the candle `pivot` candles after its second swing CLOSES (cvd-divergence.ts
    `knownIndex`); detection on the full day equals detection on the prefix up to that candle (the ported loop only
    reads bars up to it), so keying by the known minute is causal;
  * a delta setup is known when its confirming candle closes.
A candidate takes a signal only when that minute is at or before its decision minute. The live chart can show a
divergence a candle early (it passes the still-forming candle to the detector); this module never does.

Stamped at Apply time (like `runner.stamp_vwap_cross`), so the base build and `LAB_VERSION` are untouched.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import base as base_mod

WARM_SESSIONS = 2                       # earlier sessions loaded for left-side pivots and the delta setup's volume baseline
DELTA_LOOKBACK = 20                     # delta-divergence.ts LOOKBACK / MIN_PRIOR
DELTA_MIN_PRIOR = 10
NEAR_WINDOW = 3                         # tv-chart.tsx: nearSetup(div, setups, 3)


# ------------------------------------------------------------------ candles


@dataclass
class Candles:
    """One row per chart candle (oldest first) for one symbol over a few sessions."""

    day: np.ndarray                     # 'YYYY-MM-DD' of the session (the CVD anchor key, session anchor)
    start: np.ndarray                   # minute of day the candle starts
    end: np.ndarray                     # minute of day it closes (start + tf)
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    cvd_h: np.ndarray                   # CVD candle high / low (the running total's extremes inside the candle)
    cvd_l: np.ndarray
    delta: np.ndarray                   # footprint delta of the candle
    n: np.ndarray                       # 1-minute bars under it


def signed_volumes(o: np.ndarray, c: np.ndarray, v: np.ndarray) -> np.ndarray:
    """cvd.ts `signedVolumes`: +volume for a green minute, -volume for a red one; a flat minute follows its close
    against the previous close, else keeps the previous sign."""
    out = np.zeros(len(v))
    sign, prev = 0, None
    for k in range(len(v)):
        if c[k] > o[k]:
            sign = 1
        elif c[k] < o[k]:
            sign = -1
        elif prev is not None and c[k] > prev:
            sign = 1
        elif prev is not None and c[k] < prev:
            sign = -1
        prev = c[k]
        out[k] = sign * v[k]
    return out


def footprint_deltas(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, v: np.ndarray) -> np.ndarray:
    """footprint.ts `spread`, summed: a minute's buy share is 0.5 + 0.45 x body / range (clamped 5-95%), so its
    delta (buys - sells) is volume x (2 x share - 1). The row weights sum to 1 and drop out of the total."""
    rng = h - lo
    with np.errstate(divide="ignore", invalid="ignore"):
        share = np.where(rng > 0, np.clip(0.5 + 0.45 * (c - o) / rng, 0.05, 0.95), 0.5)
    return np.where(v > 0, v * (2.0 * share - 1.0), 0.0)


def build_candles(m1: pd.DataFrame, tf: int, open_min: int) -> Candles:
    """`tf`-minute candles from 1-minute bars, aligned to the session open, with their CVD and footprint delta.

    The CVD restarts at zero each session (anchor = session, the chart's default).
    """
    mins = (m1.index.hour * 60 + m1.index.minute).to_numpy()
    days = np.array([str(x) for x in m1.index.date])
    o, h, lo, c, v = (m1[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    signed = signed_volumes(o, c, v)
    fpd = footprint_deltas(o, h, lo, c, v)
    slot = (mins - open_min) // tf
    rows: dict[str, list] = {k: [] for k in ("day", "start", "o", "h", "l", "c", "v", "cvd_h", "cvd_l", "delta", "n")}
    level, cur_day, k, n = 0.0, None, 0, len(mins)
    while k < n:
        if days[k] != cur_day:
            cur_day, level = days[k], 0.0
        j = k
        hi = lo_ = level
        while j < n and days[j] == days[k] and slot[j] == slot[k]:
            level += signed[j]
            hi, lo_ = max(hi, level), min(lo_, level)
            j += 1
        rows["day"].append(days[k])
        rows["start"].append(int(open_min + slot[k] * tf))
        rows["o"].append(o[k])
        rows["h"].append(h[k:j].max())
        rows["l"].append(lo[k:j].min())
        rows["c"].append(c[j - 1])
        rows["v"].append(v[k:j].sum())
        rows["cvd_h"].append(hi)
        rows["cvd_l"].append(lo_)
        rows["delta"].append(fpd[k:j].sum())
        rows["n"].append(j - k)
        k = j
    arr = {key: np.asarray(val) for key, val in rows.items()}
    return Candles(end=arr["start"] + tf, **arr)


# ------------------------------------------------------------------ CVD divergence (cvd-divergence.ts)


@dataclass(frozen=True)
class CvdDiv:
    i1: int
    i2: int
    known: int                          # candle index whose close makes it drawable (i2 + pivot)
    bullish: bool
    hidden: bool


def _is_pivot(x: np.ndarray, i: int, n: int, high: bool) -> bool:
    v = x[i]
    for k in range(1, n + 1):
        lf, r = x[i - k], x[i + k]
        if (lf > v or r >= v) if high else (lf < v or r <= v):
            return False
    return True


def find_cvd_divergences(cd: Candles, pivot: int = 3, max_gap: int = 30, tol: int = 1,
                         min_price_pct: float = 0.05, min_cvd_pct: float = 5.0, min_intrabars: int = 5) -> list[CvdDiv]:
    """cvd-divergence.ts `findCvdDivergences` (status / confirmation left out: they read later candles)."""
    n = max(1, int(pivot))
    m = len(cd.h)
    out: list[CvdDiv] = []
    if m < 2 * n + 2:
        return out
    usable = cd.n >= min_intrabars           # intrabarUsable: fewer intrabars = the candle's own colour, not order flow

    def cvd_at(i: int, high: bool):
        best = None
        for k in range(max(0, i - tol), min(m - 1, i + tol) + 1):
            if not usable[k] or cd.day[k] != cd.day[i]:
                continue
            val = cd.cvd_h[k] if high else cd.cvd_l[k]
            if best is None or (val > best if high else val < best):
                best = val
        return best

    for high in (True, False):
        px = cd.h if high else cd.l
        prev = None
        for i in range(n, m - n):
            if not usable[i] or not _is_pivot(px, i, n, high):
                continue
            cv = cvd_at(i, high)
            if cv is None:
                continue
            before, prev = prev, (i, float(px[i]), cv)
            if before is None or cd.day[before[0]] != cd.day[i]:
                continue
            b_i, b_px, b_cv = before
            if i - b_i > max_gap:
                continue
            d_price = float(px[i]) - b_px
            if abs(d_price) < abs(b_px) * min_price_pct / 100.0:
                continue
            d_cvd = cv - b_cv
            lo_, hi_ = cd.cvd_l[b_i:i + 1].min(), cd.cvd_h[b_i:i + 1].max()
            if not hi_ > lo_ or abs(d_cvd) < (hi_ - lo_) * min_cvd_pct / 100.0:
                continue
            price_up, cvd_up = d_price > 0, d_cvd > 0
            if price_up == cvd_up:
                continue
            hidden = (not price_up) if high else price_up
            out.append(CvdDiv(b_i, i, i + n, bullish=not high, hidden=hidden))
    return sorted(out, key=lambda d: d.i2)


# ------------------------------------------------------------------ delta divergence (delta-divergence.ts)


@dataclass(frozen=True)
class DeltaSetup:
    index: int
    sellers: bool                       # True = sellers trapped (a long setup)
    status: str                         # confirmed | missed | pending (as far as the loaded candles go)
    resolved: int | None                # candle that confirmed it, or that made it missed


def find_delta_setups(cd: Candles, wick: float = 40.0, dominance: float = 2.0, delta_pct: float = 5.0,
                      rvol: float = 1.0, within: int = 3, close_break: bool = True) -> list[DeltaSetup]:
    out: list[DeltaSetup] = []
    m = len(cd.c)
    for i in range(m):
        o, h, lo, c, v, d = cd.o[i], cd.h[i], cd.l[i], cd.c[i], cd.v[i], cd.delta[i]
        rng = h - lo
        if not (v > 0) or not (rng > 0):
            continue
        body = abs(c - o)
        upper, lower = h - max(o, c), min(o, c) - lo
        if upper >= dominance * lower and upper >= body and d > 0:
            sellers = False
        elif lower >= dominance * upper and lower >= body and d < 0:
            sellers = True
        else:
            continue
        if ((lower if sellers else upper) / rng) * 100.0 < wick:
            continue
        if abs(d) / v * 100.0 < delta_pct:
            continue
        frm = max(0, i - DELTA_LOOKBACK)
        if i - frm >= DELTA_MIN_PRIOR:
            mean = cd.v[frm:i].mean()
            if mean > 0 and v / mean < rvol:
                continue
        status, resolved = "pending", None
        for j in range(i + 1, min(m - 1, i + within) + 1):
            nc, no = cd.c[j], cd.o[j]
            if (nc < lo) if sellers else (nc > h):
                status, resolved = "missed", j
                break
            breaks = ((nc > h) if sellers else (nc < lo)) if close_break else ((cd.h[j] > h) if sellers else (cd.l[j] < lo))
            agrees = (nc > no and cd.delta[j] > 0) if sellers else (nc < no and cd.delta[j] < 0)
            if breaks and agrees:
                status, resolved = "confirmed", j
                break
        if status == "pending" and i + within <= m - 1:
            status, resolved = "missed", i + within
        out.append(DeltaSetup(i, sellers, status, resolved))
    return out


def near_long_setup(div: CvdDiv, setups: list[DeltaSetup]) -> bool:
    """tv-chart.tsx `nearSetup` for a bullish divergence, causal: a sellers-trapped setup whose signal or
    confirming candle is within 3 candles of the second swing, already closed when the divergence is known, and
    not already missed by then (the chart hides missed setups by default)."""
    for s in setups:
        if not s.sellers or s.index > div.known:
            continue
        if s.status == "missed" and s.resolved is not None and s.resolved <= div.known:
            continue
        at = [s.index] + ([s.resolved] if s.status == "confirmed" and s.resolved <= div.known else [])
        if any(abs(x - div.i2) <= NEAR_WINDOW for x in at):
            return True
    return False


# ------------------------------------------------------------------ stamping


TF_MIN = {"5m": 5, "15m": 15}


def _day_events(m1: pd.DataFrame, day: str, cvd_p: dict | None, dd_p: dict | None, open_min: int) -> dict:
    """Known-minute events for one trade day: bullish CVD divergences (with kind / near flag) and confirmed long
    delta setups. Candles of earlier sessions only feed pivots and baselines; events must be known on `day`."""
    out: dict = {"cvd": [], "dd": []}
    dd_cfg = dd_p or {}

    def setups_on(cd: Candles) -> list[DeltaSetup]:
        return find_delta_setups(cd, wick=dd_cfg.get("wick", 40.0), dominance=dd_cfg.get("dominance", 2.0),
                                 delta_pct=dd_cfg.get("delta_pct", 5.0), rvol=dd_cfg.get("rvol", 1.0),
                                 within=int(dd_cfg.get("within", 3)))

    if cvd_p is not None:
        cd = build_candles(m1, TF_MIN[cvd_p["timeframe"]], open_min)
        setups = setups_on(cd) if cvd_p["with_delta"] != "off" else []
        for d in find_cvd_divergences(cd, pivot=int(cvd_p["pivot"]), max_gap=int(cvd_p["max_gap"])):
            if d.bullish and cd.day[d.known] == day:
                out["cvd"].append((int(cd.end[d.known]), "hidden" if d.hidden else "regular",
                                   near_long_setup(d, setups)))
    if dd_p is not None:
        cd = build_candles(m1, TF_MIN[dd_p["timeframe"]], open_min)
        for s in setups_on(cd):
            if s.sellers and s.status == "confirmed" and cd.day[s.resolved] == day:
                out["dd"].append(int(cd.end[s.resolved]))
    return out


def stamp_candidates(df: pd.DataFrame, rule: base_mod.BaseRule, cvd_p: dict | None, dd_p: dict | None) -> pd.DataFrame:
    """`cvd_div_ok` / `delta_div_ok` per candidate: the signal became known within `within_min` minutes before (or
    at) the decision bar's close, with the plugin's parameters."""
    cvd_ok = np.zeros(len(df), dtype=bool)
    dd_ok = np.zeros(len(df), dtype=bool)
    cvd_kind = np.full(len(df), "", dtype=object)
    open_min = rule.mk.open_min
    pos = {idx: k for k, idx in enumerate(df.index)}
    for sym, g in df.groupby("symbol", sort=False):
        years = sorted({int(d[:4]) for d in g["date"]})
        frames = [base_mod._load(sym, y, rule.market) for y in [years[0] - 1, *years]]
        bars = pd.concat([f for f in frames if f is not None and not f.empty])
        bars = bars[~bars.index.duplicated(keep="last")].sort_index()
        norm = bars.index.normalize()
        sessions = norm.unique()
        for day, gd in g.groupby("date", sort=False):
            k = sessions.searchsorted(pd.Timestamp(day, tz=sessions.tz))
            if k >= len(sessions) or str(sessions[k].date()) != day:
                continue
            m1 = bars[(norm >= sessions[max(0, k - WARM_SESSIONS)]) & (norm <= sessions[k])]
            ev = _day_events(m1, day, cvd_p, dd_p, open_min)
            for idx, dec in zip(gd.index, gd["decision_min"]):
                r = pos[idx]
                if cvd_p is not None:
                    hits = [e for e in ev["cvd"] if 0 <= dec - e[0] <= cvd_p["within_min"]
                            and (cvd_p["kinds"] == "all" or e[1] == "regular")
                            and (cvd_p["with_delta"] == "off" or e[2])]
                    if hits:
                        cvd_ok[r], cvd_kind[r] = True, hits[-1][1]
                if dd_p is not None:
                    dd_ok[r] = any(0 <= dec - e <= dd_p["within_min"] for e in ev["dd"])
    return df.assign(cvd_div_ok=cvd_ok, cvd_div_kind=cvd_kind, delta_div_ok=dd_ok)
