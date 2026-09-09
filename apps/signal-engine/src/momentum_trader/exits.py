"""Exit logic. Two philosophies, selected by mode, measured against each other.

`fixed_2r` — the original: at entry, write down a stop and a target at twice the
stop distance, then wait. Nothing about the chart changes those two numbers.
This is what BT17 measured (1,876 trades, gross 0.00%, only 25% reached target).

`trend_min` / `trend_full` — no target at all. The position is held while the
trend is intact and closed when the chart says the move is over. "Over" is
defined by explicit, frozen rules rather than a price picked in advance:

  * the ratcheting stop rises under each confirmed swing low, so a winner keeps
    running for as long as it keeps making higher lows;
  * a close below the 9-period average ends the short-term trend (Warrior's own
    most-used exit);
  * a close below the 20-period average ends the trend outright;
  * MACD momentum rolling over says the move is decelerating before price shows
    it;
  * a bearish or indecisive candle printed into a derived resistance level says
    the move is running into supply;
  * a heavy-volume down bar says sellers arrived in size.

A hard stop and a 15:15 close always remain. A system with no disaster stop is
not a system.

Two timing rules keep this honest:

* **Stops are checked on every one-minute bar**, because a stop order sits in
  the market and fills whenever price reaches it. **Trend signals are checked
  only when a bar on the position's own timeframe closes**, because you read an
  indicator off a finished candle, not a half-formed one.
* **Trend rules are armed only once the trade is in profit** by `arm_at_r` of
  the initial risk. Before that the hard stop is the only exit. Without arming,
  ordinary noise right after entry closes every position immediately.

Indicators use prior-session warm-up bars, because MACD needs 26 bars and a
09:20 entry has three. Warm-up affects exits ONLY; entry logic is untouched, so
entries remain directly comparable with BT17.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .indicators import ema, macd, volume_ratio
from .levels import Level, derive_levels, nearest_resistance, swing_pivots

# ── frozen parameters ─────────────────────────────────────────────────────────
ARM_AT_R = 0.5             # arm trend exits once open profit ≥ 0.5 × initial risk
BREAKEVEN_AT_R = 1.0       # lift the stop to entry once open profit ≥ 1.0 × risk
SWING_BUFFER_PCT = 0.10    # trail this far below the confirmed swing low
EMA_FAST, EMA_SLOW = 9, 20
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
RESIST_NEAR_PCT = 0.35     # "into resistance" band
CLIMAX_VOL_RATIO = 2.5     # a down bar on ≥ 2.5× its recent average volume
WARMUP_BARS = 240          # prior-session bars fed to the indicators

MODE_FIXED = "fixed_2r"
MODE_TREND_MIN = "trend_min"
MODE_TREND_FULL = "trend_full"
MODES = (MODE_FIXED, MODE_TREND_MIN, MODE_TREND_FULL)


@dataclass(frozen=True)
class ExitConfig:
    mode: str = MODE_FIXED
    arm_at_r: float = ARM_AT_R
    breakeven_at_r: float = BREAKEVEN_AT_R
    swing_buffer_pct: float = SWING_BUFFER_PCT
    use_swing_trail: bool = True
    use_ema_fast_break: bool = True
    use_ema_slow_break: bool = True
    use_macd_fade: bool = False
    use_resistance_reject: bool = False
    use_volume_climax: bool = False

    @property
    def has_target(self) -> bool:
        return self.mode == MODE_FIXED

    @classmethod
    def for_mode(cls, mode: str) -> ExitConfig:
        if mode == MODE_FIXED:
            # No arming and no breakeven lock. Spec §4 lists exactly four exits
            # for the fixed mode — false_break, stop, target, 15:15 — and the
            # original BT17 run (2026-09-05, before this module existed) had zero
            # trail_stop exits. The lock is a trend-mode feature; letting it leak
            # in here turned ~24% of fixed_2r trades into breakeven scratches and
            # cut target hits from 25% to ~15%. Found 2026-09-06 while
            # decomposing losses. Every A/B since 09-05 carried the leak in BOTH
            # arms, so no verdict flips, but absolute fixed_2r numbers before
            # this fix are not the spec'd strategy.
            return cls(mode=mode, arm_at_r=float("inf"), breakeven_at_r=float("inf"),
                       use_swing_trail=False, use_ema_fast_break=False,
                       use_ema_slow_break=False)
        if mode == MODE_TREND_MIN:
            return cls(mode=mode)
        if mode == MODE_TREND_FULL:
            return cls(mode=mode, use_macd_fade=True, use_resistance_reject=True,
                       use_volume_climax=True)
        raise ValueError(f"unknown exit mode {mode!r}; expected one of {MODES}")


@dataclass
class ExitState:
    """Per-position mutable state. `hard_stop` never moves; `trail` only rises."""
    entry: float
    hard_stop: float
    trail: float
    highest: float
    armed: bool = False
    levels: list[Level] = field(default_factory=list)
    last_tf_seen: pd.Timestamp | None = None

    @property
    def stop(self) -> float:
        return max(self.hard_stop, self.trail)

    def r_multiple(self, price: float) -> float:
        risk = self.entry - self.hard_stop
        return (price - self.entry) / risk if risk > 0 else 0.0


@dataclass(frozen=True)
class ExitSignal:
    reason: str
    price: float


def initial_state(
    entry: float,
    hard_stop: float,
    bars_tf: pd.DataFrame | None = None,
    prev_day: dict[str, float] | None = None,
    orb: dict[str, float] | None = None,
    with_levels: bool = False,
) -> ExitState:
    levels: list[Level] = []
    if with_levels and bars_tf is not None and not bars_tf.empty:
        levels = derive_levels(bars_tf, prev_day, orb)
    return ExitState(entry=entry, hard_stop=hard_stop, trail=hard_stop, highest=entry,
                     levels=levels)


# ── per one-minute bar ────────────────────────────────────────────────────────

def update_high(st: ExitState, bar_high: float, cfg: ExitConfig) -> None:
    """Track the running high, arm the trend rules, lock breakeven at 1R."""
    st.highest = max(st.highest, bar_high)
    r = st.r_multiple(st.highest)
    if not st.armed and r >= cfg.arm_at_r:
        st.armed = True
    if r >= cfg.breakeven_at_r:
        st.trail = max(st.trail, st.entry)


def check_stop(st: ExitState, bar: pd.Series) -> ExitSignal | None:
    """Stops fill whenever price reaches them, so this runs on every 1-min bar.
    A bar that opens below the stop fills at the open, which is worse."""
    stop = st.stop
    if float(bar["low"]) <= stop:
        reason = "stop" if stop <= st.hard_stop else "trail_stop"
        return ExitSignal(reason, min(stop, float(bar["open"])))
    return None


def check_target(bar: pd.Series, target: float | None, cfg: ExitConfig) -> ExitSignal | None:
    if not cfg.has_target or target is None:
        return None
    if float(bar["high"]) >= target:
        return ExitSignal("target", max(target, float(bar["open"])))
    return None


# ── per closed bar on the position's own timeframe ────────────────────────────

def update_trail(st: ExitState, bars_tf: pd.DataFrame, cfg: ExitConfig) -> None:
    """Raise the stop under the most recent confirmed swing low."""
    if not cfg.use_swing_trail or bars_tf.empty:
        return
    _, lows = swing_pivots(bars_tf)
    if not lows:
        return
    candidate = lows[-1][0] * (1.0 - cfg.swing_buffer_pct / 100.0)
    if candidate < float(bars_tf["close"].iloc[-1]):   # never trail above price
        st.trail = max(st.trail, candidate)


def check_trend(
    st: ExitState,
    bars_tf: pd.DataFrame,
    warmup_tf: pd.DataFrame | None,
    cfg: ExitConfig,
) -> ExitSignal | None:
    """Trend-health rules, in frozen priority order. None means stay in."""
    if not st.armed or bars_tf.empty:
        return None
    bar = bars_tf.iloc[-1]
    open_, high, low, close = (float(bar["open"]), float(bar["high"]),
                               float(bar["low"]), float(bar["close"]))
    ind = indicator_frame(bars_tf, warmup_tf)

    if cfg.use_ema_fast_break:
        v = ind["ema_fast"]
        if len(v) and not pd.isna(v.iloc[-1]) and close < float(v.iloc[-1]):
            return ExitSignal("ema9_break", close)
    if cfg.use_ema_slow_break:
        v = ind["ema_slow"]
        if len(v) and not pd.isna(v.iloc[-1]) and close < float(v.iloc[-1]):
            return ExitSignal("ema20_break", close)
    if cfg.use_macd_fade:
        h = ind["macd_hist"]
        if len(h) >= 2 and not pd.isna(h.iloc[-1]) and not pd.isna(h.iloc[-2]):
            if float(h.iloc[-2]) > 0.0 >= float(h.iloc[-1]):
                return ExitSignal("macd_fade", close)
    if cfg.use_resistance_reject and st.levels:
        res = nearest_resistance(st.levels, st.entry)
        if res is not None and res.is_near(high, RESIST_NEAR_PCT):
            body, rng = abs(close - open_), max(high - low, 1e-9)
            if close < open_ or body <= 0.3 * rng:      # red bar, or indecision
                return ExitSignal("resistance_reject", close)
    if cfg.use_volume_climax and close < open_:
        vr = volume_ratio(bars_tf)
        if len(vr) and not pd.isna(vr.iloc[-1]) and float(vr.iloc[-1]) >= CLIMAX_VOL_RATIO:
            return ExitSignal("volume_climax", close)
    return None


def indicator_frame(
    bars_tf: pd.DataFrame, warmup_tf: pd.DataFrame | None
) -> dict[str, pd.Series]:
    """EMAs and MACD computed with prior-session warm-up, sliced back to today."""
    joined = bars_tf
    if warmup_tf is not None and not warmup_tf.empty:
        joined = pd.concat([warmup_tf.iloc[-WARMUP_BARS:], bars_tf])
        joined = joined[~joined.index.duplicated(keep="last")].sort_index()
    n = len(bars_tf)
    m = macd(joined["close"], MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    return {
        "ema_fast": ema(joined["close"], EMA_FAST).iloc[-n:],
        "ema_slow": ema(joined["close"], EMA_SLOW).iloc[-n:],
        "macd_hist": m.hist.iloc[-n:],
    }
