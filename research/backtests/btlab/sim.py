"""Trade replay: one taken candidate -> one closed trade, on the day's 1-minute bars.

The base trade only has three ways out: the support stop, the resistance target
and the last bar of the session (15:14 NSE, 15:59 US). Exit indicators add more (EMA/MACD/VWAP breaks, a
swing-low trail, breakeven, a stale-trade timer) by editing an `ExitCfg`; this
module is the only place that reads it.

Ordering rules (each one is the pessimistic reading of a 1-minute bar):

* A bar that touches both the stop and the target is a STOP. A minute cannot say
  which came first.
* The bar a buy-stop fills on can stop the trade out but can never reach the
  target or arm anything: the part of the bar before the fill is not ours.
* A stop that an event lifts (breakeven, trail, checkpoint) takes effect on the
  NEXT bar. A bar may not both justify a stop and fill it (the BT46 same-bar
  look-ahead).
* Indicator exits are judged on a CLOSED 5-minute bar and fill at its close,
  the way you read an indicator off a finished candle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from src.momentum_trader.exits import ARM_AT_R, CLIMAX_VOL_RATIO, RESIST_NEAR_PCT, SWING_BUFFER_PCT
from src.momentum_trader.levels import PIVOT_K

from . import markets
from .base import tick_down

CHECKPOINT_BUFFER_PCT = 0.15     # BT52: the lifted stop sits this far under the checkpoint
CHECKPOINT_RR = 2.0              # BT52: the uncapped target stays at 2R


@dataclass
class ExitCfg:
    target_mode: str = "resistance"          # resistance | rr | none | checkpoint
    target_rr: float = 2.0
    ema9_exit: bool = False
    ema20_exit: bool = False
    macd_fade_exit: bool = False
    vwap_exit: bool = False
    volume_climax_exit: bool = False
    resistance_reject_exit: bool = False
    swing_trail: bool = False
    breakeven: bool = False
    breakeven_r: float = 1.0
    breakeven_to: str = "entry"              # entry | cost
    stale_exit: bool = False
    stale_minutes: int = 45
    stale_r: float = 0.5
    stop_mode: str = "support"               # support | pattern_low
    market: str = "NSE"                      # clock, tick and cost model of the tape being replayed
    notes: list[str] = field(default_factory=list)


@dataclass
class Exit:
    k: int
    price: float
    reason: str
    minute: int
    mfe_r: float
    mae_r: float


def cost_breakeven(entry: float, qty: int, market: str = "NSE") -> float:
    """Lowest sell price that covers the round-trip charges, rounded up a tick."""
    mk = markets.get(market)
    TICK = mk.tick

    def net(px: float) -> float:
        return (px - entry) * qty - mk.costs(entry, px, qty)

    lo, hi = entry, entry + TICK
    while net(hi) < 0.0:
        hi += max(entry * 0.01, TICK)
    for _ in range(50):
        mid = (lo + hi) / 2.0
        lo, hi = (mid, hi) if net(mid) < 0.0 else (lo, mid)
    return (math.ceil(hi / TICK) + 1) * TICK


def simulate(arr: dict[str, np.ndarray], k0: int, entry: float, stop0: float, target0: float,
             resistance: float, qty: int, cfg: ExitCfg) -> Exit:
    o, h, lo, c, mn = arr["m1_o"], arr["m1_h"], arr["m1_l"], arr["m1_c"], arr["m1_min"]
    n1 = len(c)
    mk = markets.get(cfg.market)
    eod, tick = mk.eod_min, mk.tick
    m5_min = arr["m5_min"]
    m5_idx = {int(v): i for i, v in enumerate(m5_min)}
    risk = entry - stop0
    stop, stop_kind = stop0, "stop"

    if cfg.target_mode == "resistance":
        target, checkpoint = target0, None
    elif cfg.target_mode == "rr":
        target, checkpoint = entry + cfg.target_rr * risk, None
    elif cfg.target_mode == "checkpoint":
        target = entry + CHECKPOINT_RR * risk
        checkpoint = target0 if target0 < target else None
    else:
        target, checkpoint = math.inf, None

    be_price = None
    if cfg.breakeven:
        be_price = entry if cfg.breakeven_to == "entry" else cost_breakeven(entry, qty, cfg.market)

    hi_water, lo_water = entry, entry
    armed = be_done = cp_done = False
    pending: tuple[float, str] | None = None
    trail = 0.0
    entry_min = int(mn[k0])

    def done(k: int, px: float, reason: str, minute: int) -> Exit:
        return Exit(k, round(px, 2), reason, minute,
                    round((hi_water - entry) / risk, 3), round((lo_water - entry) / risk, 3))

    for k in range(k0, n1):
        m = int(mn[k])
        if pending is not None:
            if pending[0] > stop:
                stop, stop_kind = pending
            pending = None
        if lo[k] <= stop:
            lo_water = min(lo_water, float(lo[k]))
            px = stop if k == k0 else min(stop, float(o[k]))
            return done(k, px, stop_kind, m)
        lo_water = min(lo_water, float(lo[k]))
        if k == k0:
            if m >= eod:
                return done(k, float(c[k]), "eod", m + 1)
            continue
        if h[k] >= target:
            hi_water = max(hi_water, float(h[k]))
            return done(k, max(target, float(o[k])), "target", m)
        hi_water = max(hi_water, float(h[k]))
        r_hi = (hi_water - entry) / risk

        if be_price is not None and not be_done and r_hi >= cfg.breakeven_r:
            be_done = True
            pending = (be_price, "breakeven_stop")
        if checkpoint is not None and not cp_done and h[k] >= checkpoint:
            cp_done = True
            lift = tick_down(checkpoint * (1.0 - CHECKPOINT_BUFFER_PCT / 100.0), tick)
            if lift > stop and (pending is None or lift > pending[0]):
                pending = (lift, "checkpoint_stop")
        if r_hi >= ARM_AT_R:
            armed = True

        if (m + 1) % 5 == 0 and (m - 4) in m5_idx:          # this minute closes a 5-minute bar
            j = m5_idx[m - 4]
            close5 = float(arr["m5_c"][j])
            reason = None
            if armed:
                e9, e20 = arr["m5_ema9"][j], arr["m5_ema20"][j]
                if cfg.ema9_exit and not np.isnan(e9) and close5 < e9:
                    reason = "ema9_break"
                elif cfg.ema20_exit and not np.isnan(e20) and close5 < e20:
                    reason = "ema20_break"
                elif cfg.macd_fade_exit and j >= 1 and arr["m5_macd"][j - 1] > 0.0 >= arr["m5_macd"][j]:
                    reason = "macd_fade"
                elif cfg.vwap_exit and not np.isnan(arr["m5_vwap"][j]) and close5 < arr["m5_vwap"][j]:
                    reason = "vwap_break"
                elif cfg.resistance_reject_exit and _rejects(arr, j, resistance):
                    reason = "resistance_reject"
                elif (cfg.volume_climax_exit and close5 < arr["m5_o"][j]
                      and arr["m5_volratio"][j] >= CLIMAX_VOL_RATIO):
                    reason = "volume_climax"
            if reason:
                return done(k, close5, reason, m + 1)
            if cfg.swing_trail:
                p = _last_pivot_low(arr["m5_pivlow"], arr["m5_l"], j)
                if p is not None:
                    cand = tick_down(p * (1.0 - SWING_BUFFER_PCT / 100.0), tick)
                    if cand < close5 and cand > trail:
                        trail = cand
                        if cand > stop and (pending is None or cand > pending[0]):
                            pending = (cand, "trail_stop")
        if cfg.stale_exit and m - entry_min >= cfg.stale_minutes and r_hi < cfg.stale_r:
            return done(k, float(c[k]), "stale", m + 1)
        if m >= eod:
            return done(k, float(c[k]), "eod", m + 1)
    return done(n1 - 1, float(c[n1 - 1]), "eod", int(mn[n1 - 1]) + 1)


def _last_pivot_low(flags: np.ndarray, lows: np.ndarray, j: int) -> float | None:
    """Latest swing low CONFIRMED by 5-minute bar j (needs PIVOT_K bars after it)."""
    i = j - PIVOT_K
    while i >= PIVOT_K:
        if flags[i]:
            return float(lows[i])
        i -= 1
    return None


def _rejects(arr: dict[str, np.ndarray], j: int, res: float) -> bool:
    """A red 5-minute bar that tested the resistance band and closed under it."""
    high, close, open_ = arr["m5_h"][j], arr["m5_c"][j], arr["m5_o"][j]
    near = abs(high - res) / res * 100.0 <= RESIST_NEAR_PCT
    return bool(near and close < open_ and close < res)
