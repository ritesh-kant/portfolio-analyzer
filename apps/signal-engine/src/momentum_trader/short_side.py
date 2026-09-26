"""Short selling: the long engine, run on a reflected price tape.

Why a reflection instead of a second engine
-------------------------------------------
`engine.py` is ~2,100 lines of long-only rules - "close above VWAP", "EMA9 over
EMA20", "break of the pause bar's high", "stop under the swing low", "target at
the next resistance". A short is every one of those rules turned upside down.
Writing them all a second time would mean two engines that drift apart the
first time either is edited, and a long arm that is trading live would carry
the blast radius of every short-side change.

Instead the short side feeds the SAME engine a tape reflected about yesterday's
close K:

    p' = 2K - p        (open' = 2K - open, high' = 2K - low, low' = 2K - high)

A stock falling from 100 to 95 becomes a stock rising from 100 to 105. The
engine sees a gainer, finds its bull flag (which is the real chart's bear flag),
arms a buy-stop above the pause bar (the real sell-stop below it), puts the
stop under the swing low (the real stop above the swing high) and manages the
exit exactly as it would a long. This module maps everything back to real
prices on the way out. Long behaviour is untouched by construction: the long
path never enters this file.

What the reflection preserves EXACTLY (K = previous close):
  * rupee distances - so risk per share, the 2R target and position size;
  * the day-change percentage: (p' - K)/K = -(p - K)/K, so "up 4-8%" is
    precisely "down 4-8%";
  * the tick grid (K and every price sit on it, so 2K - p does too);
  * EMA, VWAP, MACD, ATR, pivots, prior-day / opening-range levels and volume
    shelves - all linear in price, so they reflect point for point;
  * volume and therefore RVOL.

What it only approximates, and by how much:
  * rules written as a PERCENTAGE OF PRICE (the 0.3-3% stop band, the 1% chase
    guard, the 0.35% "near a level" band) are measured against p' instead of p.
    The relative error on the threshold is |p' - p| / p = 2|day change|, i.e.
    8-16% of the threshold for a 4-8% mover. A 0.30% minimum stop becomes
    ~0.33% in real terms on a stock down 5%. Disclosed, not corrected: fixing
    it means editing the long rules.
  * round numbers are NOT linear. ₹300 reflected about K = 312.40 is ₹324.80,
    which nobody has an order resting at. `indicators.reflected` makes the
    engine's round-number grid answer in real prices instead (see there).

Money is recomputed here in REAL prices, with the short's own cost model: on
NSE, STT is charged on the SALE, which for a short is the entry, and stamp duty
on the buy-back. Quantity is re-derived from the real entry so the notional cap
applies to what is actually sold.

Market rules a long never meets
-------------------------------
NSE: intraday (MIS) short selling in the cash segment is allowed and must be
squared off the same day - which this arm already does at 15:15. There is no
borrow. T2T / BE-series and band-locked names cannot be shorted intraday, and
the universe already excludes them for longs. A stock locked at its UPPER band
cannot be covered; a short arm on losers is on the other side of that risk.

US: two rules. (1) A short needs a LOCATE - borrowable shares. Low-float
runners are often hard-to-borrow, and no free feed says which. Paper fills
here assume the locate exists; `locate_verified` is always False until a
broker check is wired. Intraday round trips pay no borrow fee at IBKR (it is
charged on positions held over settlement). (2) SEC Rule 201, the short-sale
restriction (SSR): once a stock trades 10% below the prior close, for the rest
of that day and all of the next, a short may only be sold ABOVE the national
best bid. A breakdown short - selling as price drops through a level - is
exactly the order SSR forbids, so on an SSR name this arm REFUSES the entry
(`ssr_active`) rather than inventing a fill no broker would give.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

import pandas as pd

from src.news_trader.trailing_sl import calc_costs

from . import engine as eng
from . import exits, location
from .candles import candle_tags, completed_pattern_matches
from .engine import (
    AttentionEvent,
    Candidate,
    CatalystLookup,
    ClosedTrade,
    DayState,
    EngineConfig,
    Position,
    Rejection,
    resample_5m,
)
from .indicators import reflected, round_levels_above
from .levels import Level
from .market import NSE, MarketProfile
from .risk import TradePlan
from .setups import Setup

SIDE_LONG = "long"
SIDE_SHORT = "short"
SIDES = (SIDE_LONG, SIDE_SHORT)

# SEC Rule 201: the restriction triggers at a 10% decline from the prior close.
SSR_TRIGGER_PCT = 10.0
SSR_REASON = "ssr_active"

# Names the reflection turns inside out. Everything not listed keeps its name:
# `stop`, `target`, `trail_stop`, `ema9_break`, `false_break`, `eod_close`, ...
# all mean "against the trade" or "for the trade" on either side.
_KIND_FLIP = {"pivot_high": "pivot_low", "pivot_low": "pivot_high",
              "session_high": "session_low"}
_REASON_FLIP = {"support_break": "resistance_break",
                "resistance_reject": "support_reject"}


def _flip_kind(kind: str) -> str:
    return _KIND_FLIP.get(kind, kind)


def _flip_target_source(source: str) -> str:
    # "structural_resistance:round" -> "structural_support:round"
    head, sep, kind = source.partition(":")
    if head == "structural_resistance":
        return f"structural_support{sep}{_flip_kind(kind)}"
    return source


@dataclass(frozen=True)
class Reflection:
    """p' = 2k - p. Its own inverse, so one method maps both ways."""

    k: float

    def px(self, p: float) -> float:
        return round(2.0 * self.k - p, 6)

    def opt(self, p: float | None) -> float | None:
        return None if p is None else self.px(p)

    def admits(self, bars: pd.DataFrame | None) -> bool:
        """False when a price at or above 2k would reflect to zero or below."""
        return bars is None or bars.empty or float(bars["high"].max()) < 2.0 * self.k

    def bars(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["open"] = (2.0 * self.k - df["open"]).round(6)
        out["high"] = (2.0 * self.k - df["low"]).round(6)
        out["low"] = (2.0 * self.k - df["high"]).round(6)
        out["close"] = (2.0 * self.k - df["close"]).round(6)
        return out

    def prev_day(self, d: dict[str, float] | None) -> dict[str, float] | None:
        if not d:
            return d
        out: dict[str, float] = {}
        if d.get("low"):
            out["high"] = self.px(float(d["low"]))
        if d.get("high"):
            out["low"] = self.px(float(d["high"]))
        if d.get("close"):
            out["close"] = self.px(float(d["close"]))
        return out

    def level(self, lvl: Level | None) -> Level | None:
        if lvl is None:
            return None
        return replace(lvl, price=self.px(lvl.price), kind=_flip_kind(lvl.kind))

    def setup(self, s: Setup) -> Setup:
        return Setup(name=s.name, trigger=self.px(s.trigger), stop=self.px(s.stop),
                     level=self.opt(s.level), meta=dict(s.meta))


# ── evidence documents ───────────────────────────────────────────────────────
# Engine evidence is nested dicts of indicator readings. Prices reflect, signed
# momentum readings change sign, and a pair of high/low swaps. Anything else
# (times, volumes, ratios, flags) is frame-free and passes through.
_PRICE_KEYS = frozenset({
    "open", "close", "ema9", "ema20", "ema200", "vwap", "trigger", "stop",
    "level", "price", "support", "resistance", "confirmation", "invalidation",
    "accepted_resistance", "prev_close",
})
_SIGNED_KEYS = frozenset({"macd_hist", "day_chg_pct", "macd", "macd_signal",
                          "trend_net_move"})


def _real_evidence(doc: Any, r: Reflection) -> Any:
    if isinstance(doc, dict):
        out: dict[str, Any] = {}
        for key, val in doc.items():
            num = isinstance(val, (int, float)) and not isinstance(val, bool)
            if key == "high" and num:
                out["low"] = r.px(float(val))
            elif key == "low" and num:
                out["high"] = r.px(float(val))
            elif key in _PRICE_KEYS and num:
                out[key] = r.px(float(val))
            elif key in _SIGNED_KEYS and num:
                out[key] = -float(val)
            elif key == "close_position" and num:
                out[key] = 1.0 - float(val)
            else:
                out[key] = _real_evidence(val, r)
        return out
    if isinstance(doc, list):
        return [_real_evidence(v, r) for v in doc]
    return doc


def _evidence(doc: dict[str, object], r: Reflection) -> dict[str, object]:
    if not doc:
        return {}
    out: dict[str, object] = _real_evidence(doc, r)
    out["side"] = SIDE_SHORT
    out["reflect_k"] = r.k
    return out


# ── money ────────────────────────────────────────────────────────────────────

def short_round_trip_cost(market: MarketProfile, entry: float, exit_px: float,
                          qty: int) -> float:
    """Real round-trip cost of selling `qty` at `entry` and buying back at `exit_px`.

    NSE: the same MIS model as the long side with its legs swapped - STT on the
    sale (the entry), stamp duty on the purchase (the exit).
    US: the per-share model prices both legs at one level, so it is symmetric.
    SEC fee + TAF fall on the sale, now the entry leg, for the same amount. No
    borrow fee: IBKR charges it on positions held over settlement, and this arm
    never holds overnight. A locate is not a fee but it can be unavailable -
    see the module docstring.
    """
    if market.code == NSE.code:
        return float(calc_costs(entry, exit_px, qty, direction="short")["total"])
    return float(market.round_trip_cost(entry, exit_px, qty))


def short_qty(entry: float, stop: float, cfg: EngineConfig) -> int:
    """Shares to sell so a stop-out loses ~`risk_inr`, capped at the notional.

    The long planner's rule with the stop above the entry. Distances are exact
    under the reflection, so the risk leg always agrees with the engine; the
    notional leg is re-applied to the REAL sale price.
    """
    per_share = stop - entry
    if per_share <= 0 or entry <= 0:
        return 0
    risk = cfg.risk_inr * cfg.initial_risk_fraction
    notional = cfg.max_notional_inr * cfg.initial_risk_fraction
    return max(0, min(int(risk // per_share), int(notional // entry)))


def ssr_active(bars_1m: pd.DataFrame, prev_close: float, carried: bool = False,
               quote: float | None = None) -> bool:
    """SEC Rule 201: restricted if carried from yesterday, or once any bar (or
    the live quote) today has traded 10% or more below the prior close."""
    if carried:
        return True
    if prev_close <= 0:
        return False
    floor = prev_close * (1.0 - SSR_TRIGGER_PCT / 100.0)
    if quote is not None and quote <= floor:
        return True
    return not bars_1m.empty and float(bars_1m["low"].min()) <= floor


def ssr_carried_from(prior_day_low: float | None, prior_prev_close: float | None) -> bool:
    """Yesterday's SSR carries into today. True when yesterday's low was 10%+
    under the close before it."""
    if not prior_day_low or not prior_prev_close or prior_prev_close <= 0:
        return False
    return prior_day_low <= prior_prev_close * (1.0 - SSR_TRIGGER_PCT / 100.0)


def next_round_level(entry: float, side: str) -> float:
    """The first round-number level in the trade's direction: above a long's
    entry, below a short's (where its buyers rest)."""
    if side != SIDE_SHORT:
        return round_levels_above(entry)[0]
    k = entry          # reflect about the entry itself: p' = entry, real grid
    with reflected(k):
        minor, _ = round_levels_above(entry)
    return 2.0 * k - minor


def check_config(cfg: EngineConfig) -> None:
    """Refuse the two management overlays that price costs inside the engine.

    Both compute a breakeven price from the LONG cost model on reflected
    prices, which is a different number from the short's real breakeven. They
    are research flags, off in every deployed arm.
    """
    if cfg.cost_aware_breakeven or cfg.pyramid_add_at_1r:
        raise ValueError("cost_aware_breakeven / pyramid_add_at_1r are not "
                         "supported on the short side")


# ── one symbol's short side ──────────────────────────────────────────────────

@dataclass
class ShortEvents:
    """What one step produced, all in real prices."""
    attention: list[AttentionEvent] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    opened: Position | None = None
    closed: list[ClosedTrade] = field(default_factory=list)


class ShortBook:
    """The short side of one symbol for one session.

    Holds an ordinary engine `DayState` that lives entirely in the reflected
    frame. Callers hand it REAL bars and REAL quotes and get REAL records back;
    nothing outside this class ever sees a reflected price.
    """

    def __init__(
        self,
        symbol: str,
        prev_close: float,
        cum_vol_profile: pd.Series | None,
        *,
        prev_day: dict[str, float] | None = None,
        warmup_1m: pd.DataFrame | None = None,
        prev_day_loser: bool = False,
        chart_quality: Any = None,
        daily_sma20: float | None = None,
        daily_atr_pct: float | None = None,
        ssr_rule: bool = False,
        ssr_carried: bool = False,
    ) -> None:
        if prev_close <= 0:
            raise ValueError("prev_close must be > 0")
        self.r = Reflection(prev_close)
        self.prev_day_real = prev_day
        self.ssr_rule = ssr_rule
        self.ssr_carried = ssr_carried
        warm = warmup_1m if self.r.admits(warmup_1m) else None
        warm_r = self.r.bars(warm) if warm is not None and not warm.empty else None
        # prev_close reflects to itself (2K - K = K), so the engine's day-change
        # is the exact negative of the real one.
        self.state = DayState(
            symbol=symbol, prev_close=prev_close, cum_vol_profile=cum_vol_profile,
            prev_day_gainer=prev_day_loser, warmup_1m=warm_r,
            warmup_5m=resample_5m(warm_r) if warm_r is not None else None,
            prev_day=self.r.prev_day(prev_day), chart_quality=chart_quality,
            daily_sma20=self.r.opt(daily_sma20), daily_atr_pct=daily_atr_pct,
        )
        self.closed: list[ClosedTrade] = []
        self.candidates: list[Candidate] = []
        self.rejections: list[Rejection] = []
        self.attention: list[AttentionEvent] = []
        self._real_cands: dict[int, Candidate] = {}
        self._open: Position | None = None
        self.out_of_domain = False

    # ── state the scanner needs to see ──────────────────────────────────────
    @property
    def symbol(self) -> str:
        return self.state.symbol

    @property
    def has_pending(self) -> bool:
        return self.state.pending is not None

    @property
    def has_position(self) -> bool:
        return self.state.position is not None

    @property
    def position(self) -> Position | None:
        """The open position in REAL prices (None when flat)."""
        return self._open if self.state.position is not None else None

    @property
    def pending_trigger(self) -> float | None:
        p = self.state.pending
        return None if p is None else self.r.px(p.cand.setup.trigger)

    def pending_expired(self, now: pd.Timestamp) -> bool:
        p = self.state.pending
        return p is not None and p.expires_at is not None and now > p.expires_at

    def stamp_decision(self, now: pd.Timestamp, minutes: int) -> None:
        """Replace the bar-end approximation with the live decision time, as
        the scanner does for a long's pending entry."""
        p = self.state.pending
        if p is not None:
            p.decision_time = now
            p.expires_at = now + pd.Timedelta(minutes=minutes)

    # ── driving ─────────────────────────────────────────────────────────────
    def step(
        self,
        bars_1m: pd.DataFrame,
        cfg: EngineConfig,
        catalyst: CatalystLookup,
        *,
        allow_replay_fill: bool = True,
        reflected_bars: pd.DataFrame | None = None,
        full_5m: pd.DataFrame | None = None,
    ) -> ShortEvents:
        """Advance one closed 1-min bar. `bars_1m` is today's REAL bars so far.

        `reflected_bars` / `full_5m` let a replay reflect the day once instead
        of on every bar; both must already be in the reflected frame.
        """
        if bars_1m.empty:
            return ShortEvents()
        if not self.r.admits(bars_1m):
            # The stock has doubled off yesterday's close; the reflection has no
            # positive price for it. Not a short candidate - just flatten.
            return self._leave_domain(bars_1m, cfg)
        check_config(cfg)
        refl = reflected_bars if reflected_bars is not None else self.r.bars(bars_1m)
        now, close = bars_1m.index[-1], float(bars_1m["close"].iloc[-1])
        before = self._marks()
        with reflected(self.r.k):
            # Before the step too: an order armed on the last bar must not fill
            # on a bar that itself took the stock through the SSR line. Which
            # came first inside the bar is unknowable, so the refusal wins.
            self._apply_ssr(bars_1m, now, close)
            eng.step(self.state, refl, cfg, catalyst, full_5m,
                     allow_replay_fill=allow_replay_fill)
            self._apply_ssr(bars_1m, now, close)
        return self._collect(before, bars_1m, cfg)

    def fill_quote(self, when: pd.Timestamp, price: float, cfg: EngineConfig,
                   bars_1m: pd.DataFrame) -> ShortEvents:
        """Offer a live REAL quote to an armed short (the sell-stop)."""
        if self.state.pending is None:
            return ShortEvents()
        check_config(cfg)
        before = self._marks()
        with reflected(self.r.k):
            if not self._apply_ssr(bars_1m, when, price, quote=price):
                eng.fill_pending_quote(self.state, when, self.r.px(price), cfg,
                                       self.r.bars(bars_1m) if not bars_1m.empty else bars_1m)
        return self._collect(before, bars_1m, cfg)

    def force_close(self, when: pd.Timestamp, price: float, cfg: EngineConfig,
                    reason: str = "eod_sweep") -> ClosedTrade | None:
        if self.state.position is None:
            return None
        n = len(self.state.closed)
        with reflected(self.r.k):
            eng.force_close(self.state, when, self.r.px(price), cfg, reason)
        closed = self._convert_closed(n, cfg)
        self._open = None
        return closed[0] if closed else None

    def cancel_pending(self, when: pd.Timestamp, reason: str,
                       observed: float | None = None) -> Rejection | None:
        """Drop an armed short with a logged reason (guardrails, max positions,
        opposite-side position). Returns the REAL rejection."""
        p = self.state.pending
        if p is None:
            return None
        self.state.pending = None
        rej = Rejection(symbol=self.symbol, time=when, reason=reason,
                        setup=p.cand.setup.name, trigger=self.r.px(p.cand.setup.trigger),
                        observed_price=observed, side=SIDE_SHORT)
        self.rejections.append(rej)
        # keep the reflected state's own log aligned so _marks() stays valid
        self.state.rejections.append(rej)
        return rej

    # ── internals ───────────────────────────────────────────────────────────
    def _marks(self) -> tuple[int, int, int, int, bool]:
        s = self.state
        return (len(s.attention_events), len(s.candidates), len(s.rejections),
                len(s.closed), s.position is not None)

    def _apply_ssr(self, bars_1m: pd.DataFrame, when: pd.Timestamp, observed: float,
                   quote: float | None = None) -> bool:
        """Refuse an armed entry while SSR is on. True = it was refused."""
        if not self.ssr_rule or self.state.pending is None:
            return False
        if not ssr_active(bars_1m, self.state.prev_close, self.ssr_carried, quote):
            return False
        p = self.state.pending
        self.state.pending = None
        # Stored in the reflected frame like every engine rejection; _collect
        # converts it with the rest.
        self.state.rejections.append(Rejection(
            symbol=self.symbol, time=when, reason=SSR_REASON, setup=p.cand.setup.name,
            trigger=p.cand.setup.trigger, observed_price=self.r.px(observed),
        ))
        return True

    def _collect(self, before: tuple[int, int, int, int, bool],
                 bars_1m: pd.DataFrame, cfg: EngineConfig) -> ShortEvents:
        n_a, n_c, n_r, n_x, had_pos = before
        s = self.state
        ev = ShortEvents()
        fresh = len(s.candidates) > n_c or len(s.attention_events) > n_a
        tf5 = resample_5m(bars_1m) if fresh else None
        for a in s.attention_events[n_a:]:
            real = self._real_attention(a, bars_1m)
            ev.attention.append(real)
        for c in s.candidates[n_c:]:
            real_c = self._real_candidate(c, bars_1m, tf5, cfg)
            ev.candidates.append(real_c)
        for rej in s.rejections[n_r:]:
            if rej.side == SIDE_SHORT:     # already real (cancel_pending)
                continue
            ev.rejections.append(self._real_rejection(rej))
        # Closed first: a single bar can close one trade and (multi-entry) the
        # position opened after it is what `s.position` now holds.
        ev.closed = self._convert_closed(n_x, cfg)
        if s.position is not None and (not had_pos or ev.closed):
            self._open = self._real_position(s.position, cfg)
            ev.opened = self._open
        elif s.position is None:
            self._open = None
        self.attention.extend(ev.attention)
        self.candidates.extend(ev.candidates)
        self.rejections.extend(ev.rejections)
        return ev

    def _leave_domain(self, bars_1m: pd.DataFrame, cfg: EngineConfig) -> ShortEvents:
        ev = ShortEvents()
        if not self.out_of_domain:
            self.out_of_domain = True
            if self.state.pending is not None:
                self.cancel_pending(bars_1m.index[-1], "reflection_domain")
            if self.state.position is not None:
                t = self.force_close(bars_1m.index[-1], float(bars_1m["close"].iloc[-1]),
                                     cfg, "reflection_domain")
                if t is not None:
                    ev.closed.append(t)
        return ev

    def _real_candidate(self, c: Candidate, bars_1m: pd.DataFrame,
                        tf5: pd.DataFrame | None, cfg: EngineConfig) -> Candidate:
        r = self.r
        setup = r.setup(c.setup)
        upto = bars_1m[bars_1m.index <= c.time]
        tf = resample_5m(upto) if tf5 is None else tf5[tf5.index <= c.time]
        # Location, candle tags and patterns are RECOMPUTED on the real chart:
        # they are recorded-only, and the detectors already name bearish
        # formations, which is what a short's chart shows.
        loc = location.measure(tf, setup.trigger, self.prev_day_real)
        use_5m = (not cfg.use_attention_entries and c.time.minute % 5 == 4
                  and not eng._is_one_minute_setup(c.setup.name))
        tags = candle_tags(tf if use_5m and not tf.empty else upto) if not upto.empty else []
        patterns = ([m.document() for m in completed_pattern_matches(tf, "5m")]
                    if not tf.empty else [])
        if cfg.use_attention_entries and not upto.empty:
            patterns += [m.document() for m in completed_pattern_matches(upto, "1m")]
        real = replace(
            c, setup=setup, day_chg_pct=-c.day_chg_pct, candle_tags=tags,
            pattern_matches=patterns, entry_evidence=_evidence(c.entry_evidence, r),
            macd_hist=None if c.macd_hist is None else -c.macd_hist,
            dist_to_round_pct=loc.dist_to_round_pct, round_head_pct=loc.round_head_pct,
            resist_head_pct=loc.resist_head_pct, support_drop_pct=loc.support_drop_pct,
            level_anchor_px=loc.anchor_px, resist_px=loc.resist_px, support_px=loc.support_px,
            resist_kind=loc.resist_kind, support_kind=loc.support_kind, side=SIDE_SHORT,
        )
        self._real_cands[id(c)] = real
        return real

    def _cand(self, c: Candidate, cfg: EngineConfig,
              bars_1m: pd.DataFrame | None = None) -> Candidate:
        real = self._real_cands.get(id(c))
        if real is None:     # not seen through _collect (defensive)
            real = self._real_candidate(c, bars_1m if bars_1m is not None else pd.DataFrame(
                columns=["open", "high", "low", "close", "volume"]), None, cfg)
        return real

    def _real_rejection(self, rej: Rejection) -> Rejection:
        return replace(rej, trigger=self.r.px(rej.trigger),
                       observed_price=self.r.opt(rej.observed_price),
                       evidence=_evidence(rej.evidence, self.r), side=SIDE_SHORT)

    def _real_attention(self, a: AttentionEvent, bars_1m: pd.DataFrame) -> AttentionEvent:
        upto = bars_1m[bars_1m.index <= a.time]
        tags = tuple(candle_tags(upto)) if not upto.empty else ()
        return replace(a, day_chg_pct=-a.day_chg_pct, candle_tags=tags,
                       evidence=_evidence(a.evidence, self.r), side=SIDE_SHORT)

    def _real_position(self, pos: Position, cfg: EngineConfig) -> Position:
        r = self.r
        cand = self._cand(pos.cand, cfg)
        entry = r.px(pos.plan.entry)
        stop = cand.setup.stop
        target = r.px(pos.plan.target)
        qty = short_qty(entry, stop, cfg) or pos.plan.qty
        plan = TradePlan(entry=entry, stop=stop, target=target, qty=qty,
                         risk_inr=(stop - entry) * qty, reward_inr=(entry - target) * qty,
                         notional_inr=entry * qty)
        es = pos.exit_state
        # For a short the stop-side level is ABOVE (the reflected support) and
        # the target-side level BELOW (the reflected resistance).
        real_es = exits.ExitState(
            entry=entry, hard_stop=stop, trail=r.px(es.trail) if math.isfinite(es.trail) else stop,
            highest=r.px(es.highest), structural_support=r.level(es.structural_resistance),
            structural_resistance=r.level(es.structural_support),
        )
        return Position(cand=cand, entry_time=pos.entry_time, plan=plan,
                        highest=r.px(pos.highest), exit_state=real_es,
                        target_source=_flip_target_source(pos.target_source))

    def _convert_closed(self, n_before: int, cfg: EngineConfig) -> list[ClosedTrade]:
        out: list[ClosedTrade] = []
        for t in self.state.closed[n_before:]:
            real = self._real_trade(t, cfg)
            out.append(real)
            self.closed.append(real)
        return out

    def _real_trade(self, t: ClosedTrade, cfg: EngineConfig) -> ClosedTrade:
        r = self.r
        cand = self._real_cands.get(id(t.cand)) or replace(
            t.cand, setup=r.setup(t.cand.setup), day_chg_pct=-t.cand.day_chg_pct,
            side=SIDE_SHORT)
        entry, exit_px = r.px(t.entry), r.px(t.exit)
        # The size decided at the fill (the live ladder may have moved risk_inr
        # since). A position opened and closed inside one bar was never seen
        # open, so it is sized from the same rule here.
        if self._open is not None and self._open.entry_time == t.entry_time:
            qty = self._open.plan.qty
        else:
            qty = short_qty(entry, cand.setup.stop, cfg) or t.qty
        gross = (entry - exit_px) * qty
        costs = short_round_trip_cost(cfg.market, entry, exit_px, qty)
        costs += (entry + exit_px) * qty * cfg.stress_slip
        net = gross - costs
        return ClosedTrade(
            cand=cand, entry_time=t.entry_time, entry=entry, exit_time=t.exit_time,
            exit=exit_px, exit_reason=_REASON_FLIP.get(t.exit_reason, t.exit_reason),
            qty=qty, gross_inr=gross, costs_inr=costs, net_inr=net,
            target=r.px(t.target) if t.target else 0.0,
            target_source=_flip_target_source(t.target_source),
            structural_support=r.opt(t.structural_resistance),
            structural_support_kind=_flip_kind(t.structural_resistance_kind),
            structural_resistance=r.opt(t.structural_support),
            structural_resistance_kind=_flip_kind(t.structural_support_kind),
            base_net_inr=net, side=SIDE_SHORT,
        )


def run_day_short(
    symbol: str,
    bars_1m_day: pd.DataFrame,
    prev_close: float,
    cum_vol_profile: pd.Series | None,
    cfg: EngineConfig,
    catalyst: CatalystLookup,
    prev_day_loser: bool = False,
    warmup_1m: pd.DataFrame | None = None,
    prev_day: dict[str, float] | None = None,
    chart_quality: Any = None,
    daily_sma20: float | None = None,
    daily_atr_pct: float | None = None,
    *,
    ssr_rule: bool = False,
    ssr_carried: bool = False,
) -> ShortBook:
    """Replay one session's SHORT side bar by bar - `engine.run_day`'s twin.

    Reflects the day once and steps the engine exactly as `run_day` does, so
    the long and short replays differ only in the tape they are given.
    """
    book = ShortBook(
        symbol, prev_close, cum_vol_profile, prev_day=prev_day, warmup_1m=warmup_1m,
        prev_day_loser=prev_day_loser, chart_quality=chart_quality,
        daily_sma20=daily_sma20, daily_atr_pct=daily_atr_pct,
        ssr_rule=ssr_rule, ssr_carried=ssr_carried,
    )
    check_config(cfg)
    if bars_1m_day.empty:
        return book
    # Reflected once for the whole day. Bars past a doubling (if any) reflect
    # to nonsense, but `step` leaves the domain before it ever reads them.
    refl = book.r.bars(bars_1m_day)
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    full_5m = (refl.resample("5min", label="left", closed="left")
               .agg(agg).dropna(subset=["open"]))
    for i in range(1, len(bars_1m_day) + 1):
        book.step(bars_1m_day.iloc[:i], cfg, catalyst, reflected_bars=refl.iloc[:i],
                  full_5m=full_5m)
        if book.out_of_domain:
            break
    if book.state.position is not None:
        last = bars_1m_day.iloc[-1]
        book.force_close(bars_1m_day.index[-1], float(last["close"]), cfg, "eod_close")
    return book

