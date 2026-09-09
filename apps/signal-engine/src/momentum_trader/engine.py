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
from math import isfinite

import pandas as pd

from src.news_trader.trailing_sl import calc_costs

from . import exits, location, quality
from .candles import candle_tags
from .indicators import cumulative_session_volume, day_change_pct
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
ENGINE_VERSION = "2026-09-09.1"

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
FILL_NEXT_OPEN = "next_open"
FILL_TRIGGER = "trigger"
FILL_OBSERVED_QUOTE = "observed_quote"
# Feed-safety bounds, not setup/strategy thresholds. A signal expires one
# minute after its bar closes; repeated receipt of an old trade is not a quote.
MAX_QUOTE_AGE = pd.Timedelta(seconds=5)
MAX_SIGNAL_AGE = pd.Timedelta(minutes=1)
# Historical research arms; live observed-quote is intentionally not included
# so existing replay comparisons cannot silently acquire a third arm.
FILL_MODES = (FILL_NEXT_OPEN, FILL_TRIGGER)


@dataclass(frozen=True)
class CatalystResult:
    """Point-in-time catalyst outcome without treating data failure as absence."""

    status: str  # present | absent | unknown
    event_type: str = ""
    source_id: str = ""
    published_at: pd.Timestamp | None = None
    ingested_at: pd.Timestamp | None = None
    classified_at: pd.Timestamp | None = None

    @property
    def value(self) -> int:
        return int(self.status == "present")


CatalystLookup = Callable[[str, pd.Timestamp], CatalystResult | tuple[int, str]]


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
    fill_mode: str = FILL_NEXT_OPEN    # next_open | trigger (see FILL_MODES above)
    exit_mode: str = exits.MODE_FIXED  # fixed_2r | trend_min | trend_full (see exits.py)
    exit_cfg: exits.ExitConfig = field(init=False)

    def __post_init__(self) -> None:
        if self.fill_mode not in (*FILL_MODES, FILL_OBSERVED_QUOTE):
            raise ValueError(f"unknown fill mode {self.fill_mode!r}")
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
    catalyst_status: str = "absent"
    catalyst_source_id: str = ""
    catalyst_published_at: pd.Timestamp | None = None
    catalyst_ingested_at: pd.Timestamp | None = None
    catalyst_classified_at: pd.Timestamp | None = None
    decision_time: pd.Timestamp | None = None
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


@dataclass
class Position:
    cand: Candidate
    entry_time: pd.Timestamp
    plan: TradePlan
    highest: float
    exit_state: exits.ExitState
    fill_source: str = "replay_next_open"
    decision_time: pd.Timestamp | None = None
    fill_exchange_time: pd.Timestamp | None = None
    fill_receipt_time: pd.Timestamp | None = None
    has_target: bool = True
    last_quote_time: pd.Timestamp | None = None
    pending_exit: tuple[str, pd.Timestamp] | None = None


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
    # The no-target trend modes must not be represented as if their risk plan
    # were an active fixed 2R order.
    target: float | None = None
    fill_source: str = "replay_next_open"
    decision_time: pd.Timestamp | None = None
    fill_exchange_time: pd.Timestamp | None = None
    fill_receipt_time: pd.Timestamp | None = None

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
    # The live scanner advances the engine one closed bar at a time. Keeping the
    # cursor in state prevents a quiet/stale feed from replaying the same bar;
    # Phase 2 persists and fences this cursor for restart safety.
    last_processed_bar: pd.Timestamp | None = None
    unresolved_reason: str | None = None


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
        target=pos.plan.target if cfg.exit_cfg.has_target else None,
        fill_source=pos.fill_source, decision_time=pos.decision_time,
        fill_exchange_time=pos.fill_exchange_time, fill_receipt_time=pos.fill_receipt_time,
    ))
    state.position = None


def fill_pending_from_quote(
    state: DayState,
    *,
    exchange_time: pd.Timestamp,
    receipt_time: pd.Timestamp,
    price: float,
    cfg: EngineConfig,
    bars_1m: pd.DataFrame,
    full_5m: pd.DataFrame | None = None,
) -> Position | None:
    """Fill only from a quote received after the recorded decision.

    This is deliberately separate from historical `next_open` replay. The
    observation must follow the decision in both exchange and receipt time.
    This is a paper LTP mark, not a claim of executable bid/ask liquidity.
    """
    if state.pending is None or state.position is not None:
        return None
    cand = state.pending.cand
    expiry = cand.time + pd.Timedelta(minutes=1) + MAX_SIGNAL_AGE
    if (receipt_time >= expiry or receipt_time.date() != cand.time.date()
            or receipt_time.time() >= min(cfg.entry_cutoff, cfg.eod_close)):
        state.pending = None
        return None
    if (not quote_is_fresh(exchange_time, receipt_time, price)
            or cand.decision_time is None or exchange_time <= cand.decision_time
            or receipt_time <= cand.decision_time):
        return None
    state.pending = None
    chased = (price / cand.setup.trigger - 1.0) * 100.0 > CHASE_MAX_EXT_PCT
    plan = None if chased else plan_trade(
        price, cand.setup.stop, risk_inr=cfg.risk_inr,
        max_notional_inr=cfg.max_notional_inr, rr=cfg.rr, gate_entry=price,
    )
    if plan is None:
        return None
    is_1m = cand.setup.name == "micro_pullback"
    tf = bars_1m if is_1m else _bars_5m(bars_1m, full_5m)
    position = Position(
        cand=cand, entry_time=receipt_time, plan=plan, highest=price,
        exit_state=exits.initial_state(
            entry=price, hard_stop=cand.setup.stop, bars_tf=tf,
            prev_day=state.prev_day, with_levels=cfg.exit_cfg.use_resistance_reject,
        ),
        fill_source="observed_quote", decision_time=cand.decision_time,
        fill_exchange_time=exchange_time, fill_receipt_time=receipt_time,
        has_target=cfg.exit_cfg.has_target, last_quote_time=exchange_time,
    )
    state.position = position
    state.traded_today = True
    return position


def quote_is_fresh(exchange_time: pd.Timestamp, receipt_time: pd.Timestamp, price: float) -> bool:
    return (isfinite(price) and price > 0 and not pd.isna(exchange_time)
            and not pd.isna(receipt_time)
            and pd.Timedelta(0) <= receipt_time - exchange_time <= MAX_QUOTE_AGE)


def manage_position_from_quote(
    state: DayState, *, exchange_time: pd.Timestamp, receipt_time: pd.Timestamp,
    price: float, cfg: EngineConfig,
) -> None:
    """Paper mark at a fresh observed trade, never at a retroactive OHLC price.

    LTP is not a bid/ask execution guarantee. Closed-bar signals only request
    an exit; the next fresh post-decision observation supplies its paper price.
    """
    pos = state.position
    if (pos is None or not quote_is_fresh(exchange_time, receipt_time, price)
            or exchange_time <= (pos.last_quote_time or pos.entry_time)):
        return
    pos.last_quote_time = exchange_time
    reason = None
    if price <= pos.exit_state.stop:
        reason = "stop" if pos.exit_state.stop == pos.exit_state.hard_stop else "trail_stop"
    elif cfg.exit_cfg.has_target and price >= pos.plan.target:
        reason = "target"
    elif (pos.pending_exit is not None and exchange_time > pos.pending_exit[1]):
        reason = pos.pending_exit[0]
    elif receipt_time >= receipt_time.normalize() + pd.Timedelta(
        hours=cfg.eod_close.hour, minutes=cfg.eod_close.minute + 1
    ):
        reason = "eod_close"
    if reason is not None:
        _exit(state, receipt_time, price, reason, cfg)
        return
    pos.highest = max(pos.highest, price)
    exits.update_high(pos.exit_state, price, cfg.exit_cfg)


def _live_bar_exit(
    state: DayState, bars_1m: pd.DataFrame, cfg: EngineConfig,
    full_5m: pd.DataFrame | None, decision_time: pd.Timestamp,
) -> None:
    pos = state.position
    assert pos is not None
    is_1m = pos.cand.setup.name == "micro_pullback"
    tf = bars_1m if is_1m else _bars_5m(bars_1m, full_5m)
    # Never let an entry-minute low/high or a pre-entry trend bar manage a
    # position that did not exist for the full bar. Tick stops cover this gap.
    if (tf.empty or tf.index[-1] < pos.entry_time
            or tf.index[-1] == pos.exit_state.last_tf_seen or pos.pending_exit is not None):
        return
    pos.exit_state.last_tf_seen = tf.index[-1]
    lvl = pos.cand.setup.level
    if lvl is not None and len(tf) >= 2 and false_break(tf, lvl):
        pos.pending_exit = ("false_break", decision_time)
        return
    warmup = state.warmup_1m if is_1m else state.warmup_5m
    sig = exits.check_trend(pos.exit_state, tf, warmup, cfg.exit_cfg)
    exits.update_trail(pos.exit_state, tf, cfg.exit_cfg)
    if sig is not None:
        pos.pending_exit = (sig.reason, decision_time)


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


def _catalyst_result(value: CatalystResult | tuple[int, str]) -> CatalystResult:
    """Accept the legacy tuple in historical fixtures while preserving unknown."""
    if isinstance(value, CatalystResult):
        return value
    catalyst, event_type = value
    return CatalystResult(status="present" if catalyst else "absent", event_type=event_type)


def step(
    state: DayState,
    bars_1m: pd.DataFrame,
    cfg: EngineConfig,
    catalyst: CatalystLookup,
    full_5m: pd.DataFrame | None = None,
    decision_time: pd.Timestamp | None = None,
) -> None:
    """Advance one closed 1-min bar. Mutates `state`.

    `full_5m`: optional precomputed 5-min frame for the whole day (backtest speed)."""
    if bars_1m.empty:
        return
    bar = bars_1m.iloc[-1]
    now = bars_1m.index[-1]
    t = now.time()

    # 1. fill a pending entry (the bar after the trigger bar)
    if state.pending is not None and cfg.fill_mode != FILL_OBSERVED_QUOTE:
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
            is_1m = cand.setup.name == "micro_pullback"
            tf = bars_1m if is_1m else _bars_5m(bars_1m, full_5m)
            state.position = Position(
                cand=cand, entry_time=now, plan=plan, highest=fill,
                exit_state=exits.initial_state(
                    entry=fill, hard_stop=cand.setup.stop, bars_tf=tf,
                    prev_day=state.prev_day, with_levels=ec.use_resistance_reject,
                ),
                has_target=ec.has_target,
            )
            state.traded_today = True

    # 2. manage an open position on this bar
    pos = state.position
    if pos is not None:
        if cfg.fill_mode == FILL_OBSERVED_QUOTE:
            _live_bar_exit(state, bars_1m, cfg, full_5m,
                           decision_time or now + pd.Timedelta(minutes=1))
            return
        ec = cfg.exit_cfg
        es = pos.exit_state

        # A bar only supplies OHLC, not its intrabar path. Test the stop that
        # was active at the start of the bar before using its high to arm or
        # ratchet a new trail. This prevents a high that may have occurred
        # *after* the low from retroactively turning a hard-stop breach into a
        # breakeven/trail outcome. When the fixed target and stop are both hit
        # in one bar, the stop-first result is the deliberately conservative
        # OHLC convention.
        start_bar_stop = exits.check_stop(es, bar)
        if start_bar_stop is not None:
            _exit(state, now, start_bar_stop.price, start_bar_stop.reason, cfg)
            return

        target = exits.check_target(bar, pos.plan.target, ec)
        if target is not None:
            _exit(state, now, target.price, target.reason, cfg)
            return

        pos.highest = max(pos.highest, float(bar["high"]))
        exits.update_high(es, float(bar["high"]), ec)

        is_1m = pos.cand.setup.name == "micro_pullback"
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

        # The stop and target were resolved using the start-of-bar stop above.
        # Trend signals are read off completed bars on the position's timeframe.
        sig = None
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

    # 3. look for a new entry
    if cfg.fill_mode == FILL_OBSERVED_QUOTE:
        close_time = now + pd.Timedelta(minutes=1)
        if (decision_time is None or not close_time <= decision_time < close_time + MAX_SIGNAL_AGE
                or decision_time.time() >= min(cfg.entry_cutoff, cfg.eod_close)):
            return
    if state.pending is not None or t >= cfg.entry_cutoff or t >= cfg.eod_close:
        return
    if cfg.one_trade_per_day and state.traded_today:
        return
    if cfg.first_candidate_only and state.candidate_seen:
        return
    chg = day_change_pct(bars_1m, state.prev_close)
    if not (cfg.day_chg_min <= chg <= cfg.day_chg_max):
        return
    rv = rvol_now(bars_1m, state.cum_vol_profile)
    if rv is None or rv < cfg.rvol_min:
        return

    at_5m_close = now.minute % 5 == 4
    empty = pd.DataFrame(columns=bars_1m.columns)
    tf5 = _bars_5m(bars_1m, full_5m)
    bars_5m = tf5 if at_5m_close else empty
    found = scan_setups(bars_5m, bars_1m, state.prev_close if at_5m_close else None)
    if not found:
        return
    # Apply the registered setup eligibility before frozen priority. Otherwise a
    # disallowed earlier detector can hide an allowed MA9 setup on the same bar.
    eligible: list[tuple[Setup, int | None]] = []
    ordinal = pullback_ordinal(tf5) if len(tf5) else None
    for found_setup in found:
        allowed, _reason = _playbook_gate(found_setup, ordinal, cfg)
        if allowed:
            eligible.append((found_setup, ordinal))
    setup, ordinal = eligible[0] if eligible else (found[0], ordinal)
    state.candidate_seen = True
    q_ok, q_reason = _quality_gate(state, tf5, cfg)
    # Where the trigger sits relative to round numbers and derived S/R.
    # Measured on the 5-min frame for every setup so the numbers are comparable
    # across rows, and read at the trigger price the setup itself declared.
    dround, rh, res_head, sup_drop = location.measure(tf5, setup.trigger, state.prev_day)
    macd_hist = _macd_hist_now(tf5, state.warmup_5m)
    m_ok, m_reason = _max_move_gate(setup, macd_hist, dround, res_head, sup_drop, cfg)
    p_ok, p_reason = _playbook_gate(setup, ordinal, cfg)
    cat_result = _catalyst_result(catalyst(state.symbol, now))
    # Ordinal is read off the 5-min frame for EVERY setup (micro_pullback
    # included) so the number means the same thing on every row.
    cand = Candidate(
        symbol=state.symbol, time=now, setup=setup, day_chg_pct=chg, rvol=rv,
        catalyst=cat_result.value, event_type=cat_result.event_type,
        candle_tags=candle_tags(bars_5m if at_5m_close else bars_1m),
        catalyst_status=cat_result.status, catalyst_source_id=cat_result.source_id,
        catalyst_published_at=cat_result.published_at,
        catalyst_ingested_at=cat_result.ingested_at,
        catalyst_classified_at=cat_result.classified_at,
        decision_time=decision_time or now + pd.Timedelta(minutes=1),
        prev_day_gainer=state.prev_day_gainer,
        pullback_ord=ordinal,
        quality_reason=q_reason,
        atr_pct=state.daily_atr_pct,
        macd_hist=macd_hist,
        dist_to_round_pct=dround, round_head_pct=rh,
        resist_head_pct=res_head, support_drop_pct=sup_drop,
    )
    state.candidates.append(cand)   # refused setups are logged too, then dropped
    if cat_result.status == "unknown":
        cand.quality_reason = "catalyst_unknown"
        return
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
