"""Decision engine shared by the live scanner and the pool backtest.

One symbol-day at a time. `step()` is called once per CLOSED 1-minute bar with
all of today's 1-minute bars so far. It owns the whole lifecycle:

    scan → pending entry → fill (see FILL_MODES) → exits → closed trade

Because the backtest (bt17) and the Fargate scanner call the exact same
function, the backtest measures what the scanner would have done, not an
approximation of it. Everything here is pure: no I/O, no clocks. Callers supply
bars, the previous close, a cumulative-volume profile for RVOL, and a catalyst
lookup.

Timing conventions (Upstox): a bar's index timestamp is its START. The 1-min
bar starting 10:29 closes at 10:30. A 5-min bar is complete once the 1-min bar
whose start minute ends in 4 or 9 has closed (session opens 09:15, so the
5-minute grid is aligned).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import time

import pandas as pd

from src.news_trader.trailing_sl import calc_costs

from . import exits, location, quality
from .candles import candle_tags, completed_pattern_matches
from .indicators import cumulative_session_volume, day_change_pct, ema, session_vwap, volume_ratio
from .levels import NEAR_PCT
from .pullback import pullback_ordinal
from .risk import DEFAULT_RR, TradePlan, plan_trade
from .setups import CHASE_MAX_EXT_PCT, Setup, false_break, scan_setups

# ── frozen scan parameters (spec §1) ─────────────────────────────────────────
DAY_CHG_MIN_PCT = 4.0
DAY_CHG_MAX_PCT = 8.0
RVOL_MIN = 3.0
ENTRY_CUTOFF = time(14, 30)    # no new entries from 14:30 IST (bar start)
EOD_CLOSE = time(15, 14)       # bar starting 15:14 closes at 15:15 → exit at its close
STRESS_SLIP = 0.0040           # +40 bps/side cost stress (hypothesis Gate 0)

# ── fill modes (entry-fill-latency hypothesis) ───────────────────────────────
# next_open: what BT17 measured — the level break is detected when the trigger
#   bar closes, and the position fills at the open of the following 1-min bar.
# trigger:   a resting buy-stop at the trigger level, filled the instant price
#   touches it, with ZERO slippage. Every setup only fires once its bar has
#   traded through the trigger, so this price really printed — but a real stop
#   order fills at the level or worse, never better, which is why this arm is a
#   CEILING on what closing the entry lag could ever be worth, not an estimate.
# In `trigger` mode the position is still stamped with the NEXT bar's timestamp
# and exits are only checked from that bar onward, so no stop or target can
# resolve inside the trigger bar itself — the arm gains a better price without
# gaining any intra-bar look-ahead.
# future_trigger: live-paper buy-stop. Only a quote observed strictly after the
#   decision can fill, never a prior/next open below the trigger.
FILL_NEXT_OPEN = "next_open"
FILL_TRIGGER = "trigger"
FILL_FUTURE_TRIGGER = "future_trigger"
FILL_MODES = (FILL_NEXT_OPEN, FILL_TRIGGER, FILL_FUTURE_TRIGGER)

# ── two-stage attention strategy ─────────────────────────────────────────────
# These values are deliberately softer than the legacy entry gates. They only
# promote a symbol into the attention queue; they never authorize a trade by
# themselves. See research/hypotheses/2026-09-09-attention-1m-forward.md.
ATTENTION_DAY_CHG_MIN_PCT = 1.5
ATTENTION_RVOL_MIN = 1.5
ATTENTION_CONFIRM_VOL_RATIO = 2.5
ATTENTION_PENDING_MINUTES = 3
ATTENTION_SETUP = "attention_1m_confirmation"
ONE_MINUTE_SETUPS = ("micro_pullback", ATTENTION_SETUP)
ATTENTION_TAGS = frozenset({
    "dragonfly_doji", "hammer", "bullish_engulfing", "tweezer_bottom", "morning_star",
    "three_white_soldiers",
})
PROMOTION_TAGS = ATTENTION_TAGS | {"doji", "spinning_top", "inverted_hammer"}

CatalystLookup = Callable[[str, pd.Timestamp], tuple[int, str]]


@dataclass
class EngineConfig:
    risk_inr: float = 500.0
    max_notional_inr: float = 50_000.0
    rr: float = DEFAULT_RR
    day_chg_min: float = DAY_CHG_MIN_PCT
    day_chg_max: float = DAY_CHG_MAX_PCT
    rvol_min: float = RVOL_MIN
    entry_cutoff: time = ENTRY_CUTOFF
    eod_close: time = EOD_CLOSE
    stress_slip: float = 0.0           # 0 in live paper (costs are real); bt17 passes STRESS_SLIP
    one_trade_per_day: bool = True
    # Selectivity filters F1/F2/F3 — trade rarely, only the good ones. Off by
    # default so every prior backtest is reproducible.
    # research/hypotheses/2026-09-06-quality-selectivity.md
    require_quality: bool = False
    # The operator's strict "maximize the move" checklist.  It is deliberately
    # opt-in: historic strategies stay reproducible and the checklist can be
    # evaluated as one pre-registered bundle.
    require_max_move: bool = False
    # A refused first signal must not make room for a later replacement signal.
    # This makes a filtered arm an exact subset of its control arm, which is
    # necessary for the random-subset anti-test.
    first_candidate_only: bool = False
    # A forward playbook may name its exact setup and pullback ordinal. Empty
    # tuples preserve the full frozen Warrior setup list.
    allowed_setups: tuple[str, ...] = ()
    allowed_pullback_ordinals: tuple[int, ...] = ()
    fill_mode: str = FILL_NEXT_OPEN    # next_open | trigger | future_trigger
    exit_mode: str = exits.MODE_FIXED  # fixed_2r | trend_min | trend_full (see exits.py)
    # New paper-only path: soft thresholds promote a symbol, 5-minute context
    # defines the setup, and a high-volume 1-minute candle confirms it.
    use_attention_entries: bool = False
    attention_day_chg_min: float = ATTENTION_DAY_CHG_MIN_PCT
    attention_rvol_min: float = ATTENTION_RVOL_MIN
    attention_confirm_vol_ratio: float = ATTENTION_CONFIRM_VOL_RATIO
    attention_pending_minutes: int = ATTENTION_PENDING_MINUTES
    exit_cfg: exits.ExitConfig = field(init=False)

    def __post_init__(self) -> None:
        if self.fill_mode not in FILL_MODES:
            raise ValueError(f"unknown fill mode {self.fill_mode!r}; expected one of {FILL_MODES}")
        if self.attention_day_chg_min < 0.0:
            raise ValueError("attention_day_chg_min must be non-negative")
        if self.attention_rvol_min <= 0.0:
            raise ValueError("attention_rvol_min must be positive")
        if self.attention_confirm_vol_ratio <= 0.0:
            raise ValueError("attention_confirm_vol_ratio must be positive")
        if self.attention_pending_minutes <= 0:
            raise ValueError("attention_pending_minutes must be positive")
        self.exit_cfg = exits.ExitConfig.for_mode(self.exit_mode)


@dataclass
class Candidate:
    """A setup that fired on a qualifying mover — logged whether or not it was traded."""
    symbol: str
    time: pd.Timestamp
    setup: Setup
    day_chg_pct: float
    rvol: float
    catalyst: int
    event_type: str
    candle_tags: list[str]
    # Strict, fully closed multi-candle formations, including their exact
    # time span and trade levels.  Informational only: it cannot alter entry.
    pattern_matches: list[dict[str, object]] = field(default_factory=list)
    prev_day_gainer: bool = False
    # Which pullback of the day's move this entry sits on (1 = first after the
    # day's first sharp advance; None = no advance to anchor to). RECORDED ONLY —
    # nothing gates on it, so the trade set is unchanged. See pullback.py and
    # research/hypotheses/2026-09-06-pullback-ordinal.md.
    pullback_ord: int | None = None
    # Why the selectivity filters passed or refused this setup ("ok" when they
    # were not applied). Recorded on EVERY candidate, including refused ones, so
    # pass rates and refusal reasons are measurable.
    quality_reason: str = "ok"
    # ── RECORDED ONLY, nothing gates on either ───────────────────────────────
    # research/hypotheses/2026-09-06-volatility-scaled-entry.md §3. Keeping the
    # engine blind to both is what makes the derived arms exact SUBSETS of the
    # run, which the anti-test depends on.
    # atr_pct : 20-day daily ATR as % of prev close, from PRIOR sessions only.
    # macd_hist: MACD(12/26/9) histogram on the 5-min frame at the trigger bar,
    #   warmed from prior sessions. Read on the 5-min frame for EVERY setup
    #   (micro_pullback included) so the number means the same thing per row.
    atr_pct: float | None = None
    macd_hist: float | None = None
    # Entry-location metrics, also RECORDED ONLY.
    # research/hypotheses/2026-09-06-entry-location.md — see location.py.
    dist_to_round_pct: float | None = None   # % to the NEAREST 0.50 rupee mark (the gate)
    round_head_pct: float | None = None      # % up to the next mark (exploratory)
    resist_head_pct: float | None = None     # % up to nearest derived resistance
    support_drop_pct: float | None = None    # % down to nearest derived support


@dataclass
class Pending:
    cand: Candidate
    decision_time: pd.Timestamp | None = None
    expires_at: pd.Timestamp | None = None


@dataclass(frozen=True)
class AttentionEvent:
    symbol: str
    time: pd.Timestamp
    day_chg_pct: float
    rvol: float
    reason: str
    candle_tags: tuple[str, ...]


@dataclass(frozen=True)
class Rejection:
    symbol: str
    time: pd.Timestamp
    reason: str
    setup: str
    trigger: float
    observed_price: float | None = None


@dataclass
class Position:
    cand: Candidate
    entry_time: pd.Timestamp
    plan: TradePlan
    highest: float
    exit_state: exits.ExitState


@dataclass
class ClosedTrade:
    cand: Candidate
    entry_time: pd.Timestamp
    entry: float
    exit_time: pd.Timestamp
    exit: float
    exit_reason: str
    qty: int
    gross_inr: float
    costs_inr: float
    net_inr: float

    @property
    def gross_pct(self) -> float:
        return (self.exit / self.entry - 1.0) * 100.0

    @property
    def net_pct(self) -> float:
        return self.net_inr / (self.entry * self.qty) * 100.0


@dataclass
class DayState:
    symbol: str
    prev_close: float
    cum_vol_profile: pd.Series | None      # index: datetime.time → avg cumulative volume
    prev_day_gainer: bool = False
    # prior-session bars, used ONLY to warm up exit indicators (entries untouched)
    warmup_1m: pd.DataFrame | None = None
    warmup_5m: pd.DataFrame | None = None
    prev_day: dict[str, float] | None = None
    # F2 is judged on PRIOR sessions and F1's daily leg on the prior daily close,
    # so both are supplied by the caller and cannot see today.
    chart_quality: quality.ChartQuality | None = None
    daily_sma20: float | None = None
    # 20-day daily ATR as % of prev close, prior sessions only. Recorded onto
    # every candidate; nothing gates on it.
    daily_atr_pct: float | None = None
    pending: Pending | None = None
    position: Position | None = None
    closed: list[ClosedTrade] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    traded_today: bool = False
    candidate_seen: bool = False
    attention: bool = False
    attention_since: pd.Timestamp | None = None
    attention_patterns: list[dict[str, object]] = field(default_factory=list)
    attention_events: list[AttentionEvent] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)


def _bars_5m(bars_1m: pd.DataFrame, full_5m: pd.DataFrame | None) -> pd.DataFrame:
    """Completed 5-min bars as of the last 1-min bar. If the caller precomputed the
    whole day's 5-min frame (backtest), slice it instead of resampling each step."""
    if full_5m is None:
        return resample_5m(bars_1m)
    last_start = bars_1m.index[-1]
    # a 5-min bar starting at S is complete once the 1-min bar starting S+4 has closed
    return full_5m[full_5m.index <= last_start - pd.Timedelta(minutes=4)]


def resample_5m(bars_1m: pd.DataFrame) -> pd.DataFrame:
    """1-min → 5-min bars (label = bar start), dropping the incomplete last bar."""
    if bars_1m.empty:
        return bars_1m
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = bars_1m.resample("5min", label="left", closed="left").agg(agg).dropna(subset=["open"])
    last_start = bars_1m.index[-1]
    if last_start.minute % 5 != 4:       # the current 5-min bucket is still open
        out = out.iloc[:-1] if len(out) and out.index[-1] <= last_start else out
    return out


def rvol_now(bars_1m: pd.DataFrame, profile: pd.Series | None) -> float | None:
    """Time-of-day RVOL: today's cumulative volume ÷ profile value at this clock time."""
    if profile is None or profile.empty or bars_1m.empty:
        return None
    t = bars_1m.index[-1].time()
    # profile is indexed by the START time of the 1-min bar
    if t not in profile.index:
        return None
    base = float(profile.loc[t])
    if base <= 0:
        return None
    return float(cumulative_session_volume(bars_1m).iloc[-1]) / base


def _exit(state: DayState, when: pd.Timestamp, px: float, reason: str, cfg: EngineConfig) -> None:
    pos = state.position
    assert pos is not None
    qty = pos.plan.qty
    entry = pos.plan.entry
    gross = (px - entry) * qty
    costs = calc_costs(entry, px, qty, direction="long")["total"]
    costs += (entry + px) * qty * cfg.stress_slip
    state.closed.append(ClosedTrade(
        cand=pos.cand, entry_time=pos.entry_time, entry=entry, exit_time=when, exit=px,
        exit_reason=reason, qty=qty, gross_inr=gross, costs_inr=costs, net_inr=gross - costs,
    ))
    state.position = None


def _quality_gate(state: DayState, tf5: pd.DataFrame, cfg: EngineConfig) -> tuple[bool, str]:
    """(ok, reason) for the F1/F2/F3 selectivity bundle. ("ok" when disabled).

    Order is cheapest-and-most-static first: chart shape is a property of the
    symbol, trend and surge are properties of today.
    """
    if not cfg.require_quality:
        return True, "ok"
    cq = state.chart_quality
    if cq is None:
        return False, "no_history"
    if not cq.ok:
        return False, cq.reason
    ok, reason = quality.uptrend_ok(tf5, state.prev_close, state.daily_sma20)
    if not ok:
        return False, reason
    return quality.surge_ok(tf5)


def _macd_hist_now(tf5: pd.DataFrame, warmup_5m: pd.DataFrame | None) -> float | None:
    """MACD histogram on the last completed 5-min bar, or None before warm-up.

    RECORDED ONLY (see `Candidate.macd_hist`). Reuses the exit module's frozen
    12/26/9 and its warm-up handling, so the entry-side reading and the
    exit-side reading are the same number computed the same way.
    """
    if tf5.empty:
        return None
    h = exits.indicator_frame(tf5, warmup_5m)["macd_hist"]
    if not len(h) or pd.isna(h.iloc[-1]):
        return None
    return float(h.iloc[-1])


def _max_move_gate(
    setup: Setup,
    macd_hist: float | None,
    dist_to_round_pct: float | None,
    resist_head_pct: float | None,
    support_drop_pct: float | None,
    cfg: EngineConfig,
) -> tuple[bool, str]:
    """The operator's entry-location checklist, frozen as one bundle.

    Trend, chart quality, and a volume-backed surge are evaluated separately by
    ``_quality_gate``.  This function adds the remaining requirements: no
    micro-pullback, positive MACD momentum, no nearby round-number congestion,
    room below the next resistance, and a nearby derived support.  Every value
    was available at the completed 5-minute trigger bar.
    """
    if not cfg.require_max_move:
        return True, "ok"
    if setup.name == "micro_pullback":
        return False, "micro_excluded"
    if macd_hist is None or macd_hist <= 0.0:
        return False, "macd_not_positive"
    # The closest half of a stated ₹0.50 interval is the round-number zone.
    # This is the same no-tuning median-free split documented in bt24.
    if dist_to_round_pct is not None and dist_to_round_pct <= 0.125 / setup.trigger * 100.0:
        return False, "near_round"
    if resist_head_pct is not None and resist_head_pct <= NEAR_PCT:
        return False, "near_resistance"
    if support_drop_pct is None or support_drop_pct > NEAR_PCT:
        return False, "not_near_support"
    return True, "ok"


def _playbook_gate(
    setup: Setup, pullback_ord: int | None, cfg: EngineConfig
) -> tuple[bool, str]:
    """Optional exact setup selection for a separately registered playbook."""
    if cfg.allowed_setups and setup.name not in cfg.allowed_setups:
        return False, "setup_not_allowed"
    if cfg.allowed_pullback_ordinals and pullback_ord not in cfg.allowed_pullback_ordinals:
        return False, "pullback_not_allowed"
    return True, "ok"


def _is_one_minute_setup(name: str) -> bool:
    return name in ONE_MINUTE_SETUPS


def _attention_context(tf5: pd.DataFrame) -> tuple[bool, str]:
    """Five-minute trend context for the attention queue.

    Promotion is intentionally not an entry. A neutral doji can draw attention,
    but a trade still needs a separate high-volume one-minute confirmation.
    """
    if len(tf5) < 20:
        return False, "ema_warmup"
    fast = ema(tf5["close"], 9)
    slow = ema(tf5["close"], 20)
    if pd.isna(fast.iloc[-1]) or pd.isna(slow.iloc[-1]):
        return False, "ema_warmup"
    if float(fast.iloc[-1]) <= float(slow.iloc[-1]):
        return False, "ema_down"
    vw = session_vwap(tf5)
    if pd.isna(vw.iloc[-1]) or float(tf5["close"].iloc[-1]) <= float(vw.iloc[-1]):
        return False, "below_vwap"
    return True, "ok"


def _promotion_reason(
    tf5: pd.DataFrame, bars_1m: pd.DataFrame
) -> tuple[str | None, list[str], list[dict[str, object]]]:
    """Return auditable attention context without authorizing entry.

    Named formations are evaluated only from completed 5-minute bars.  One-
    minute tags remain micro-context, but are never presented as 5-minute
    pattern evidence.  Incomplete five-minute candles are intentionally not
    examined here: a pattern is not real until its final candle has closed.
    """
    tags_5m = candle_tags(tf5)
    tags_1m = candle_tags(bars_1m)
    tags = list(dict.fromkeys([*tags_5m, *tags_1m]))
    matches = [match.document() for match in completed_pattern_matches(tf5, "5m")]
    if matches:
        return f"pattern:{matches[0]['name']}", tags, matches
    if PROMOTION_TAGS.intersection(tags):
        return "candlestick_context", tags, matches
    # Detect structures even when the legacy +4% / 3x entry gate has not fired.
    found = scan_setups(tf5, bars_1m, None)
    if found:
        return f"setup:{found[0].name}", tags, matches
    return None, tags, matches


def _unique_pattern_documents(*groups: list[dict[str, object]]) -> list[dict[str, object]]:
    """Deduplicate stored formation evidence while preserving formation order."""
    seen: set[tuple[object, ...]] = set()
    out: list[dict[str, object]] = []
    for group in groups:
        for match in group:
            key = tuple(match.get(k) for k in ("name", "timeframe", "start", "end"))
            if key not in seen:
                seen.add(key)
                out.append(match)
    return out


def _attention_confirmation(
    bars_1m: pd.DataFrame, min_volume_ratio: float
) -> tuple[Setup | None, str]:
    """Confirmed one-minute entry inside an already-promoted five-minute trend.

    A valid bar must be green, close in the upper 40% of its range, carry at
    least `min_volume_ratio` times recent one-minute volume. Candlestick shapes
    create the attention context; requiring a second named shape here would
    miss an ordinary strong breakout candle. The order is armed above the
    confirmation bar and is not filled until a future quote trades there.
    """
    if len(bars_1m) < 3:
        return None, "attention_insufficient_bars"
    bar = bars_1m.iloc[-1]
    open_, high, low, close = map(float, (bar["open"], bar["high"], bar["low"], bar["close"]))
    rng = high - low
    if rng <= 0.0 or close <= open_:
        return None, "attention_red_or_flat"
    if (close - low) / rng < 0.60:
        return None, "attention_weak_close"
    vr = volume_ratio(bars_1m)
    if pd.isna(vr.iloc[-1]) or float(vr.iloc[-1]) < min_volume_ratio:
        return None, "attention_low_1m_volume"
    recent = bars_1m.iloc[-3:]
    stop = float(recent["low"].min())
    if stop >= high:
        return None, "attention_invalid_stop"
    return (
        Setup(
            name=ATTENTION_SETUP,
            trigger=high,
            stop=stop,
            level=high,
            meta={"volume_ratio": float(vr.iloc[-1])},
        ),
        "confirmed",
    )


def _reject_pending(
    state: DayState,
    when: pd.Timestamp,
    reason: str,
    observed_price: float | None = None,
) -> None:
    pending = state.pending
    if pending is None:
        return
    state.rejections.append(Rejection(
        symbol=state.symbol,
        time=when,
        reason=reason,
        setup=pending.cand.setup.name,
        trigger=pending.cand.setup.trigger,
        observed_price=observed_price,
    ))
    state.pending = None


def fill_pending_quote(
    state: DayState,
    when: pd.Timestamp,
    price: float,
    cfg: EngineConfig,
    bars_1m: pd.DataFrame,
    full_5m: pd.DataFrame | None = None,
) -> Position | None:
    """Fill an armed entry from a quote observed strictly after its decision.

    This is the live-safe counterpart to the replay approximation in `step`.
    A price below the buy-stop leaves the order pending; a quote more than the
    chase cap above it cancels the order. No lower next-open substitution is
    possible.
    """
    pending = state.pending
    if pending is None or cfg.fill_mode != FILL_FUTURE_TRIGGER:
        return None
    if pending.decision_time is not None and when <= pending.decision_time:
        return None
    if pending.expires_at is not None and when > pending.expires_at:
        _reject_pending(state, when, "pending_expired", price)
        return None
    trigger = pending.cand.setup.trigger
    if price < trigger:
        return None
    if (price / trigger - 1.0) * 100.0 > CHASE_MAX_EXT_PCT:
        _reject_pending(state, when, "chased", price)
        return None
    plan = plan_trade(
        price, pending.cand.setup.stop,
        risk_inr=cfg.risk_inr, max_notional_inr=cfg.max_notional_inr,
        rr=cfg.rr, gate_entry=price,
    )
    if plan is None:
        _reject_pending(state, when, "stop_not_sane", price)
        return None
    cand = pending.cand
    state.pending = None
    is_1m = _is_one_minute_setup(cand.setup.name)
    tf = bars_1m if is_1m else _bars_5m(bars_1m, full_5m)
    state.position = Position(
        cand=cand, entry_time=when, plan=plan, highest=price,
        exit_state=exits.initial_state(
            entry=price, hard_stop=cand.setup.stop, bars_tf=tf,
            prev_day=state.prev_day, with_levels=cfg.exit_cfg.use_resistance_reject,
        ),
    )
    state.traded_today = True
    return state.position


def step(
    state: DayState,
    bars_1m: pd.DataFrame,
    cfg: EngineConfig,
    catalyst: CatalystLookup,
    full_5m: pd.DataFrame | None = None,
    *,
    allow_replay_fill: bool = True,
) -> None:
    """Advance one closed 1-min bar. Mutates `state`.

    `full_5m`: optional precomputed 5-min frame for the whole day (backtest speed)."""
    if bars_1m.empty:
        return
    bar = bars_1m.iloc[-1]
    now = bars_1m.index[-1]
    t = now.time()

    # 1. fill a pending entry (the bar after the trigger bar)
    if state.pending is not None and cfg.fill_mode == FILL_FUTURE_TRIGGER:
        pending = state.pending
        if pending.expires_at is not None and now > pending.expires_at:
            _reject_pending(state, now, "pending_expired", float(bar["close"]))
        elif (
            allow_replay_fill
            and now > pending.cand.time
            and float(bar["close"]) >= pending.cand.setup.trigger
        ):
            # Replay has no quote chronology. Use only the later bar's close,
            # stamped at its close time; a high-only touch is not enough. Live
            # scanning instead consumes each post-decision quote.
            observed_at = now + pd.Timedelta(minutes=1)
            opened = fill_pending_quote(
                state, observed_at, float(bar["close"]), cfg, bars_1m, full_5m
            )
            if opened is not None:
                return  # OHLC cannot order the entry bar's later high and low honestly.
        if state.pending is not None:
            return

    if state.pending is not None:
        cand = state.pending.cand
        state.pending = None
        next_open = float(bar["open"])
        # Both entry gates — the chase guard and the stop-sanity band — are judged
        # on the next-bar open in EVERY fill mode, so the arms take exactly the
        # same trades and only the fill price differs (fill-latency hyp. §2).
        chased = (next_open / cand.setup.trigger - 1.0) * 100.0 > CHASE_MAX_EXT_PCT
        fill = next_open if cfg.fill_mode == FILL_NEXT_OPEN else cand.setup.trigger
        plan = None if chased else plan_trade(
            fill, cand.setup.stop, risk_inr=cfg.risk_inr,
            max_notional_inr=cfg.max_notional_inr, rr=cfg.rr, gate_entry=next_open,
        )
        if plan is not None:
            ec = cfg.exit_cfg
            is_1m = _is_one_minute_setup(cand.setup.name)
            tf = bars_1m if is_1m else _bars_5m(bars_1m, full_5m)
            state.position = Position(
                cand=cand, entry_time=now, plan=plan, highest=fill,
                exit_state=exits.initial_state(
                    entry=fill, hard_stop=cand.setup.stop, bars_tf=tf,
                    prev_day=state.prev_day, with_levels=ec.use_resistance_reject,
                ),
            )
            state.traded_today = True

    # 2. manage an open position on this bar
    pos = state.position
    if pos is not None:
        is_1m = _is_one_minute_setup(pos.cand.setup.name)
        # A live quote can fill partway through a one-minute candle. Its earlier
        # low/high is unknowable relative to the entry, so begin bar-based exit
        # checks with the first full candle that starts after the fill.
        if is_1m and now < pos.entry_time.ceil("1min"):
            return
        ec = cfg.exit_cfg
        es = pos.exit_state
        pos.highest = max(pos.highest, float(bar["high"]))
        exits.update_high(es, float(bar["high"]), ec)
        tf_bars = bars_1m if is_1m else _bars_5m(bars_1m, full_5m)
        warmup = state.warmup_1m if is_1m else state.warmup_5m
        entry_floor = pos.entry_time if is_1m else pos.entry_time.floor("5min")

        # pattern failure (both modes): the level that was broken gives way again.
        # Judged on the setup's own timeframe, never re-judging the trigger bar.
        lvl = pos.cand.setup.level
        if (lvl is not None and len(tf_bars) >= 2 and tf_bars.index[-1] >= entry_floor
                and false_break(tf_bars, lvl)):
            _exit(state, now, float(bar["close"]), "false_break", cfg)
            return

        # stops and the fixed target fill intraday, so they are checked every minute
        sig = exits.check_stop(es, bar) or exits.check_target(bar, pos.plan.target, ec)
        # trend signals are read off completed bars on the position's timeframe
        if sig is None and len(tf_bars) and tf_bars.index[-1] != es.last_tf_seen:
            es.last_tf_seen = tf_bars.index[-1]
            sig = exits.check_trend(es, tf_bars, warmup, ec)
            # raise the trail AFTER deciding, so it can only bind from the next bar
            exits.update_trail(es, tf_bars, ec)

        if sig is not None:
            _exit(state, now, sig.price, sig.reason, cfg)
        elif t >= cfg.eod_close:
            _exit(state, now, float(bar["close"]), "eod_close", cfg)
        return

    # 3. two-stage attention path: soft promotion, then 1-minute confirmation.
    if state.pending is not None or t >= cfg.entry_cutoff or t >= cfg.eod_close:
        return
    if cfg.one_trade_per_day and state.traded_today:
        return
    if cfg.first_candidate_only and state.candidate_seen:
        return
    chg = day_change_pct(bars_1m, state.prev_close)
    rv = rvol_now(bars_1m, state.cum_vol_profile)

    at_5m_close = now.minute % 5 == 4
    empty = pd.DataFrame(columns=bars_1m.columns)
    tf5 = _bars_5m(bars_1m, full_5m)

    if cfg.use_attention_entries:
        if rv is None:
            return
        if not state.attention:
            if chg < cfg.attention_day_chg_min or rv < cfg.attention_rvol_min:
                return
            context_ok, _ = _attention_context(tf5)
            reason, tags, matches = _promotion_reason(tf5, bars_1m)
            if not context_ok or reason is None:
                return
            state.attention = True
            state.attention_since = now
            state.attention_patterns = matches
            state.attention_events.append(AttentionEvent(
                symbol=state.symbol, time=now, day_chg_pct=chg, rvol=rv,
                reason=reason, candle_tags=tuple(tags),
            ))
            return  # promotion is observation, never an entry on the same candle

        context_ok, context_reason = _attention_context(tf5)
        if not context_ok:
            state.rejections.append(Rejection(
                symbol=state.symbol,
                time=now,
                reason=f"attention_removed:{context_reason}",
                setup="attention_watchlist",
                trigger=float(bar["high"]),
                observed_price=float(bar["close"]),
            ))
            state.attention = False
            state.attention_since = None
            state.attention_patterns = []
            return
        setup, confirmation_reason = _attention_confirmation(
            bars_1m, cfg.attention_confirm_vol_ratio
        )
        if setup is None:
            state.rejections.append(Rejection(
                symbol=state.symbol,
                time=now,
                reason=confirmation_reason,
                setup=ATTENTION_SETUP,
                trigger=float(bar["high"]),
                observed_price=float(bar["close"]),
            ))
            return
        cat, ev = catalyst(state.symbol, now)
        tags = candle_tags(bars_1m)
        dround, rh, res_head, sup_drop = location.measure(tf5, setup.trigger, state.prev_day)
        cand = Candidate(
            symbol=state.symbol, time=now, setup=setup, day_chg_pct=chg, rvol=rv,
            catalyst=cat, event_type=ev, candle_tags=tags,
            pattern_matches=_unique_pattern_documents(
                state.attention_patterns,
                [match.document() for match in completed_pattern_matches(tf5, "5m")],
            ),
            prev_day_gainer=state.prev_day_gainer,
            pullback_ord=pullback_ordinal(tf5) if len(tf5) else None,
            quality_reason="ok",
            atr_pct=state.daily_atr_pct,
            macd_hist=_macd_hist_now(tf5, state.warmup_5m),
            dist_to_round_pct=dround, round_head_pct=rh,
            resist_head_pct=res_head, support_drop_pct=sup_drop,
        )
        state.candidates.append(cand)
        state.candidate_seen = True
        decision = now + pd.Timedelta(minutes=1)
        state.pending = Pending(
            cand,
            decision_time=decision,
            expires_at=decision + pd.Timedelta(minutes=cfg.attention_pending_minutes),
        )
        return

    # 4. legacy entry path (kept unchanged for reproducible prior strategies).
    if not (cfg.day_chg_min <= chg <= cfg.day_chg_max):
        return
    if rv is None or rv < cfg.rvol_min:
        return

    bars_5m = tf5 if at_5m_close else empty
    found = scan_setups(bars_5m, bars_1m, state.prev_close if at_5m_close else None)
    if not found:
        return
    setup = found[0]
    state.candidate_seen = True
    q_ok, q_reason = _quality_gate(state, tf5, cfg)
    # Where the trigger sits relative to round numbers and derived S/R.
    # Measured on the 5-min frame for every setup so the numbers are comparable
    # across rows, and read at the trigger price the setup itself declared.
    dround, rh, res_head, sup_drop = location.measure(tf5, setup.trigger, state.prev_day)
    macd_hist = _macd_hist_now(tf5, state.warmup_5m)
    m_ok, m_reason = _max_move_gate(setup, macd_hist, dround, res_head, sup_drop, cfg)
    ordinal = pullback_ordinal(tf5) if len(tf5) else None
    p_ok, p_reason = _playbook_gate(setup, ordinal, cfg)
    cat, ev = catalyst(state.symbol, now)
    # Ordinal is read off the 5-min frame for EVERY setup (micro_pullback
    # included) so the number means the same thing on every row.
    cand = Candidate(
        symbol=state.symbol, time=now, setup=setup, day_chg_pct=chg, rvol=rv,
        catalyst=cat, event_type=ev, candle_tags=candle_tags(bars_5m if at_5m_close else bars_1m),
        pattern_matches=[match.document() for match in completed_pattern_matches(tf5, "5m")],
        prev_day_gainer=state.prev_day_gainer,
        pullback_ord=ordinal,
        quality_reason=q_reason,
        atr_pct=state.daily_atr_pct,
        macd_hist=macd_hist,
        dist_to_round_pct=dround, round_head_pct=rh,
        resist_head_pct=res_head, support_drop_pct=sup_drop,
    )
    state.candidates.append(cand)   # refused setups are logged too, then dropped
    if not q_ok or not m_ok or not p_ok:
        if q_ok:
            cand.quality_reason = m_reason if not m_ok else p_reason
        return
    state.pending = Pending(cand)


def run_day(
    symbol: str,
    bars_1m_day: pd.DataFrame,
    prev_close: float,
    cum_vol_profile: pd.Series | None,
    cfg: EngineConfig,
    catalyst: CatalystLookup,
    prev_day_gainer: bool = False,
    warmup_1m: pd.DataFrame | None = None,
    prev_day: dict[str, float] | None = None,
    chart_quality: quality.ChartQuality | None = None,
    daily_sma20: float | None = None,
    daily_atr_pct: float | None = None,
) -> DayState:
    """Replay a full session bar by bar (backtest entry point)."""
    state = DayState(symbol=symbol, prev_close=prev_close, cum_vol_profile=cum_vol_profile,
                     prev_day_gainer=prev_day_gainer, warmup_1m=warmup_1m,
                     warmup_5m=resample_5m(warmup_1m) if warmup_1m is not None else None,
                     prev_day=prev_day, chart_quality=chart_quality,
                     daily_sma20=daily_sma20, daily_atr_pct=daily_atr_pct)
    # every 5-min bucket incl. the partial last one; _bars_5m only exposes complete ones
    full_5m: pd.DataFrame | None = None
    if len(bars_1m_day):
        agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        full_5m = (bars_1m_day.resample("5min", label="left", closed="left")
                   .agg(agg).dropna(subset=["open"]))
    for i in range(1, len(bars_1m_day) + 1):
        step(state, bars_1m_day.iloc[:i], cfg, catalyst, full_5m)
    # anything still open at the last bar (data ended before 15:15) closes at last close
    if state.position is not None:
        last = bars_1m_day.iloc[-1]
        _exit(state, bars_1m_day.index[-1], float(last["close"]), "eod_close", cfg)
    return state


def build_cum_volume_profile(history_1m: pd.DataFrame, lookback_days: int = 20) -> pd.Series:
    """Average cumulative session volume at each 1-min bar start time over the
    last `lookback_days` sessions. Used for time-of-day RVOL."""
    if history_1m.empty:
        return pd.Series(dtype=float)
    days = sorted(set(history_1m.index.normalize()))[-lookback_days:]
    h = history_1m[history_1m.index.normalize().isin(days)]
    cum = h["volume"].astype(float).groupby(h.index.normalize()).cumsum()
    prof = cum.groupby(h.index.time).mean()
    return prof.sort_index()
