"""Entry SETUPS from the Warrior "Chart Pattern Study Guide", adapted to NSE.

Each detector looks at the LAST closed bar of a bar frame and returns a
`Setup` (trigger, stop, level) or None. They are deliberately literal
translations of the guide's rules — "buy the first candle to make a new high
after a pullback", "buy the first candle that breaks the flat top" — so the
forward test measures Warrior's rules, not a tuned variant. Thresholds are
frozen in research/specs/warrior-patterns-nse.md and must not be changed after
capture starts.

Long-only. Short setups (bear flag, VWAP fade, MA pop, halt short) are in the
spec as EXCLUDED: BT7 killed intraday shorts on NSE and the hypothesis is
long-only.

NSE adaptations (spec §2):
  * No continuous pre-market → "break of pre-market high" becomes the opening
    range breakout over the first 15 minutes (09:15–09:30 IST).
  * Whole/half-dollar → round-rupee grid (indicators.round_levels_above).
  * Halts → circuit bands; band-locked names are excluded upstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

import pandas as pd

from .indicators import ema, session_vwap, validate_bars

# ── frozen parameters ─────────────────────────────────────────────────────────
POLE_MIN_PCT = 2.0          # bull flag: pole must run ≥ 2% ...
POLE_MAX_BARS = 6           # ... within ≤ 6 bars (5-min: 30 minutes)
FLAG_MIN_BARS = 2           # flag: 2–5 pullback bars
FLAG_MAX_BARS = 5
FLAG_MAX_RETRACE = 0.50     # flag may give back ≤ 50% of the pole
FLAG_VOL_RATIO = 0.80       # flag bars average ≤ 80% of pole bars' volume (volume dries up)
FLAT_TOP_MIN_TOUCHES = 3    # ≥ 3 highs within tolerance form a "flat top"
FLAT_TOP_TOL = 0.003        # 0.3% band counts as the same level
FLAT_TOP_LOOKBACK = 12      # search window in bars
MA_TOUCH_TOL = 0.002        # price within 0.2% of the EMA counts as a touch
ORB_MINUTES = 15            # opening range = first 15 minutes of the session
SESSION_OPEN = time(9, 15)
MICRO_GREEN_RUN = 2         # micro pullback: ≥ 2 green bars, then 1 red/doji, then break
CHASE_MAX_EXT_PCT = 1.0     # trigger bar closes > 1% above the trigger level → chased, skip


@dataclass(frozen=True)
class Setup:
    name: str
    trigger: float            # price that confirms entry (buy-stop level)
    stop: float               # invalidation (pattern low)
    level: float | None = None  # the resistance / reference level broken, if any
    meta: dict[str, float] = field(default_factory=dict)


def _last(bars: pd.DataFrame, n: int) -> pd.DataFrame:
    return bars.iloc[-n:]


def _too_extended(bars: pd.DataFrame, trigger: float) -> bool:
    """Chase guard: refuse entries where the trigger bar has already run > 1%
    past the level it broke.

    Warrior's charts enter *at* the break, not after the vertical bar. The old
    news-trader's `entry-chase-cap` hypothesis found the same thing (entries
    > 0.5% past the signal price were the worst trades)."""
    if trigger <= 0:
        return False
    return (float(bars["close"].iloc[-1]) / trigger - 1.0) * 100.0 > CHASE_MAX_EXT_PCT


def _has_stop_room(trigger: float, stop: float) -> bool:
    """Degenerate-input guard: the pattern's stop must sit strictly BELOW its
    trigger.

    Every long setup here takes its trigger from a bar's high and its stop from
    a bar's low, so a zero-range bar (high == low — a minute in which one price
    traded) yields trigger == stop: a stop distance of exactly zero, which is
    not an invalidation level at all.

    Downstream this does not get caught, because `risk.plan_trade` judges the
    stop band against the *fill*, not the trigger. Under the default `next_open`
    fill the drift between trigger and fill manufactures a stop distance out of
    nothing — KAJARIACER 2024-11-25 11:35 had trigger == stop == 1217.35, filled
    at 1221.95, and so presented a 0.38% "stop" that passed the 0.3–3% band on a
    level with no relation to the pattern.

    This is a validity check, not a threshold: `risk.MIN_STOP_PCT` remains the
    sanity band and is deliberately NOT applied here (see the note in
    research/specs/warrior-patterns-nse.md §4)."""
    return trigger > 0.0 and stop > 0.0 and stop < trigger


# ── 1. bull flag ──────────────────────────────────────────────────────────────

def bull_flag(bars: pd.DataFrame) -> Setup | None:
    """Pole (sharp run on volume) → flag (2–5 quieter pullback bars, ≤50% retrace)
    → trigger = first bar whose high exceeds the previous bar's high.

    The guide: "Price above this candle's high is considered a buy, the close of
    the next candle would be confirmation." We return the trigger on the
    breaking bar; the scanner fills at the *next* bar (paper-fill rule)."""
    validate_bars(bars)
    need = POLE_MAX_BARS + FLAG_MAX_BARS + 1
    if len(bars) < need:
        return None
    cur = bars.iloc[-1]
    prev = bars.iloc[-2]
    if float(cur["high"]) <= float(prev["high"]):
        return None  # not a new high yet

    # Try each flag length; the flag is the bars strictly before `cur`.
    for flag_len in range(FLAG_MIN_BARS, FLAG_MAX_BARS + 1):
        flag = bars.iloc[-1 - flag_len : -1]
        pole = bars.iloc[-1 - flag_len - POLE_MAX_BARS : -1 - flag_len]
        if len(pole) < 2:
            continue
        pole_low = float(pole["low"].min())
        pole_high = float(pole["high"].max())
        pole_pct = (pole_high / pole_low - 1.0) * 100.0
        if pole_pct < POLE_MIN_PCT:
            continue
        # pole must actually end near its high (it is a run, not a range)
        if float(pole["close"].iloc[-1]) < pole_low + 0.6 * (pole_high - pole_low):
            continue
        flag_low = float(flag["low"].min())
        retrace = (pole_high - flag_low) / max(pole_high - pole_low, 1e-12)
        if retrace > FLAG_MAX_RETRACE or flag_low <= pole_low:
            continue
        if float(flag["high"].max()) > pole_high:
            continue  # flag made a new high → not a pullback
        if float(flag["volume"].mean()) > FLAG_VOL_RATIO * float(pole["volume"].mean()):
            continue  # volume did not dry up
        if not _has_stop_room(float(prev["high"]), flag_low):
            continue  # zero-range flag → no invalidation level; a longer flag may have one
        if _too_extended(bars, float(prev["high"])):
            return None
        return Setup(
            name="bull_flag",
            trigger=float(prev["high"]),
            stop=flag_low,
            level=pole_high,
            meta={"pole_pct": pole_pct, "retrace": retrace, "flag_bars": float(flag_len)},
        )
    return None


# ── 2. flat top breakout ──────────────────────────────────────────────────────

def flat_top_breakout(bars: pd.DataFrame) -> Setup | None:
    """≥3 highs within 0.3% of each other over the last 12 bars form a ceiling;
    trigger = the bar that closes above it. Stop = low of the consolidation.

    Also covers the guide's "double top at whole dollar, then third time it
    breaks": the same ceiling touched repeatedly, finally cleared."""
    validate_bars(bars)
    if len(bars) < FLAT_TOP_LOOKBACK + 1:
        return None
    cur = bars.iloc[-1]
    window = bars.iloc[-1 - FLAT_TOP_LOOKBACK : -1]
    level = float(window["high"].max())
    touches = int((window["high"] >= level * (1.0 - FLAT_TOP_TOL)).sum())
    if touches < FLAT_TOP_MIN_TOUCHES:
        return None
    if float(cur["close"]) <= level:
        return None  # must CLOSE through, a wick is not a break
    if _too_extended(bars, level):
        return None
    # stop = low of the bars that TESTED the ceiling (after the bar that first
    # reached it); that first bar is usually the pole, not the base
    touch_idx = window.index[window["high"] >= level * (1.0 - FLAT_TOP_TOL)]
    base = window.loc[touch_idx[0]:, "low"].iloc[1:]
    cons_low = float(base.min()) if len(base) else float(window.loc[touch_idx[0], "low"])
    if not _has_stop_room(level, cons_low):
        return None
    return Setup(
        name="flat_top_breakout",
        trigger=level,
        stop=cons_low,
        level=level,
        meta={"touches": float(touches)},
    )


# ── 3. moving-average pullback ────────────────────────────────────────────────

def ma_pullback(bars: pd.DataFrame, span: int = 9) -> Setup | None:
    """Uptrend (EMA9 > EMA20), price pulls back to touch EMA`span` without
    closing below EMA20, then the first bar to make a new high triggers.
    Stop = pullback low. The guide's "1st / 2nd 5-min pullback"."""
    validate_bars(bars)
    if len(bars) < 25:
        return None
    e_fast = ema(bars["close"], span)
    e20 = ema(bars["close"], 20)
    if pd.isna(e_fast.iloc[-1]) or pd.isna(e20.iloc[-1]):
        return None
    if not float(e_fast.iloc[-2]) > float(e20.iloc[-2]):
        return None  # no uptrend at the pullback bar
    cur, prev = bars.iloc[-1], bars.iloc[-2]
    if float(cur["high"]) <= float(prev["high"]):
        return None
    # the pullback: previous 1–3 bars must include a touch of the fast EMA
    pb = bars.iloc[-4:-1]
    pb_ema = e_fast.iloc[-4:-1]
    touched = bool(((pb["low"] <= pb_ema * (1.0 + MA_TOUCH_TOL))).any())
    if not touched:
        return None
    if bool((pb["close"] < e20.iloc[-4:-1]).any()):
        return None  # closed below the 20 → trend broken, not a pullback
    if not _has_stop_room(float(prev["high"]), float(pb["low"].min())):
        return None
    if _too_extended(bars, float(prev["high"])):
        return None
    return Setup(
        name=f"ma{span}_pullback",
        trigger=float(prev["high"]),
        stop=float(pb["low"].min()),
        level=float(e_fast.iloc[-2]),
    )


# ── 4. VWAP reclaim (first pullback after break of VWAP) ──────────────────────

def vwap_reclaim(bars: pd.DataFrame) -> Setup | None:
    """Price crosses above session VWAP, the first pullback holds above VWAP,
    trigger = first new high after that hold. Stop = pullback low (≥ VWAP)."""
    validate_bars(bars)
    if len(bars) < 7:
        return None
    vw = session_vwap(bars)
    cur, prev = bars.iloc[-1], bars.iloc[-2]
    if float(cur["high"]) <= float(prev["high"]):
        return None
    recent = bars.iloc[-7:-1]
    rv = vw.iloc[-7:-1]
    # need: at least one bar that closed below VWAP followed by bars closing above
    below = recent["close"] < rv
    if not bool(below.any()):
        return None
    last_below = int(below.to_numpy().nonzero()[0][-1])
    after = recent.iloc[last_below + 1 :]
    if len(after) < 2:
        return None  # crossed but has not yet pulled back and held
    if bool((after["close"] < rv.iloc[last_below + 1 :]).any()):
        return None
    pullback = after.iloc[1:]  # bars after the reclaim bar itself
    pb_low = float(pullback["low"].min())
    if pb_low < float(rv.iloc[-2]) * (1.0 - MA_TOUCH_TOL):
        return None  # pullback pierced VWAP → not a hold
    if not _has_stop_room(float(prev["high"]), pb_low):
        return None
    if _too_extended(bars, float(prev["high"])):
        return None
    return Setup(
        name="vwap_reclaim",
        trigger=float(prev["high"]),
        stop=pb_low,
        level=float(rv.iloc[-2]),
    )


# ── 5. opening range breakout (NSE stand-in for pre-market high) ──────────────

def opening_range_breakout(bars: pd.DataFrame) -> Setup | None:
    """High of the first ORB_MINUTES after 09:15 IST is the level; trigger = first
    bar (after the range) that closes above it. Stop = opening-range low.

    NSE has no continuous pre-market, so the guide's "break of pre-market
    highs" and "break of pre-market pivot" both collapse into this."""
    validate_bars(bars)
    if len(bars) < 2:
        return None
    today = bars[bars.index.normalize() == bars.index[-1].normalize()]
    if today.empty:
        return None
    open_ts = today.index[0].replace(hour=SESSION_OPEN.hour, minute=SESSION_OPEN.minute,
                                     second=0, microsecond=0)
    range_end = open_ts + pd.Timedelta(minutes=ORB_MINUTES)
    orb = today[(today.index >= open_ts) & (today.index < range_end)]
    if orb.empty or bars.index[-1] < range_end:
        return None  # still inside the opening range
    level = float(orb["high"].max())
    cur = bars.iloc[-1]
    prev_after = today[(today.index >= range_end)].iloc[:-1]
    if not prev_after.empty and float(prev_after["close"].max()) > level:
        return None  # already broke out earlier — this is not the FIRST break
    if float(cur["close"]) <= level:
        return None
    if not _has_stop_room(level, float(orb["low"].min())):
        return None
    if _too_extended(bars, level):
        return None
    return Setup(
        name="orb15",
        trigger=level,
        stop=float(orb["low"].min()),
        level=level,
    )


# ── 6. red-to-green ───────────────────────────────────────────────────────────

def red_to_green(bars: pd.DataFrame, prev_close: float) -> Setup | None:
    """Stock opened below yesterday's close (red on the day) and now closes above
    it (green). Trigger = prev_close; stop = low of the day so far."""
    validate_bars(bars)
    today = bars[bars.index.normalize() == bars.index[-1].normalize()]
    if len(today) < 2 or prev_close <= 0:
        return None
    day_open = float(today["open"].iloc[0])
    if day_open >= prev_close:
        return None
    cur, prev = today.iloc[-1], today.iloc[-2]
    if not (float(prev["close"]) <= prev_close < float(cur["close"])):
        return None
    if not _has_stop_room(prev_close, float(today["low"].min())):
        return None
    if _too_extended(bars, prev_close):
        return None
    return Setup(
        name="red_to_green",
        trigger=prev_close,
        stop=float(today["low"].min()),
        level=prev_close,
    )


# ── 7. micro pullback (1-min) ─────────────────────────────────────────────────

def micro_pullback(bars: pd.DataFrame) -> Setup | None:
    """≥2 green bars, one red/doji bar, then a bar that breaks that bar's high.
    Trigger = the pause bar's high; stop = the pause bar's low."""
    validate_bars(bars)
    if len(bars) < MICRO_GREEN_RUN + 2:
        return None
    cur = bars.iloc[-1]
    pause = bars.iloc[-2]
    run = bars.iloc[-2 - MICRO_GREEN_RUN : -2]
    if not bool((run["close"] > run["open"]).all()):
        return None
    pause_body = float(pause["close"]) - float(pause["open"])
    pause_rng = max(float(pause["high"]) - float(pause["low"]), 1e-12)
    if pause_body > 0.1 * pause_rng:
        return None  # pause bar must be red or a doji (a green body > 10% of range is not a pause)
    if float(cur["high"]) <= float(pause["high"]):
        return None
    if not _has_stop_room(float(pause["high"]), float(pause["low"])):
        return None  # zero-range pause bar: high == low, so trigger == stop
    if _too_extended(bars, float(pause["high"])):
        return None
    return Setup(
        name="micro_pullback",
        trigger=float(pause["high"]),
        stop=float(pause["low"]),
    )


# ── exits: bull-trap / false-break detection ──────────────────────────────────

def false_break(bars: pd.DataFrame, level: float) -> bool:
    """A breakout bar followed by a close back below the broken level.

    The guide's "flag pattern false break" / "bull trap": treat as an immediate
    exit signal for a position entered on that level, ahead of the stop."""
    if len(bars) < 2 or level <= 0:
        return False
    prev, cur = bars.iloc[-2], bars.iloc[-1]
    return float(prev["high"]) > level and float(cur["close"]) < level


# ── registry ──────────────────────────────────────────────────────────────────

def scan_setups(
    bars_5m: pd.DataFrame,
    bars_1m: pd.DataFrame | None = None,
    prev_close: float | None = None,
) -> list[Setup]:
    """Run every long setup on the latest closed bar. 5-min frame is primary;
    1-min frame (if given) adds the micro pullback. Returns all that fire, in
    frozen priority order (first is the one the scanner acts on)."""
    found: list[Setup] = []
    for fn in (bull_flag, flat_top_breakout, ma_pullback, vwap_reclaim, opening_range_breakout):
        s = fn(bars_5m)
        if s is not None:
            found.append(s)
    if prev_close is not None:
        s = red_to_green(bars_5m, prev_close)
        if s is not None:
            found.append(s)
    if bars_1m is not None:
        s = micro_pullback(bars_1m)
        if s is not None:
            found.append(s)
    return found
