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

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import time

import pandas as pd

from src.news_trader.trailing_sl import calc_costs

from . import exits, location, quality
from .candles import PATTERN_RULES_VERSION, candle_tags, completed_pattern_matches
from .indicators import (
    atr,
    cumulative_session_volume,
    day_change_pct,
    ema,
    price_volume_slopes,
    session_vwap,
    volume_ratio,
)
from .levels import NEAR_PCT, derive_levels, nearest_structural_resistance
from .pullback import pullback_ordinal
from .risk import DEFAULT_RR, TradePlan, plan_trade
from .setups import (
    CHASE_MAX_EXT_PCT,
    MICRO_PAUSE_MAX_BARS,
    Setup,
    false_break,
    micro_pullback,
    scan_setups,
)
from .volume_confirmation import volume_confirmation_evidence

# ── frozen scan parameters (spec §1) ─────────────────────────────────────────
DAY_CHG_MIN_PCT = 4.0
DAY_CHG_MAX_PCT = 8.0
RVOL_MIN = 3.0
ENTRY_CUTOFF = time(14, 30)    # no new entries from 14:30 IST (bar start)
EOD_CLOSE = time(15, 14)       # bar starting 15:14 closes at 15:15 → exit at its close
STRESS_SLIP = 0.0040           # +40 bps/side cost stress (hypothesis Gate 0)
COST_STOP_TICK_SIZE = 0.05  # NSE equity tick size

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
# A buy-stop already resting in the book at the trigger. It fills the instant the
# level is touched, so (a) the fill is the trigger and (b) the chase guard cannot
# apply — nothing was chased. That second part is what separates it from
# FILL_TRIGGER, which keeps the guard and so takes exactly the next_open arm's
# trades. Arming lasts one bar: the order is placed when the setup's break bar is
# recognised and cancelled at that bar's close, so selection is otherwise
# unchanged and the two arms stay comparable.
FILL_RESTING = "resting"
# Same order, but the stop-sanity band is judged on the price actually paid (the
# trigger) rather than the baseline arm's next-open. That is what a live resting
# order must do — it cannot see the next open — but it breaks the trade-for-trade
# comparability contract in plan_trade's docstring, so the two are separate modes
# and the difference between them is a measurement, not a detail.
FILL_RESTING_SIZED = "resting_sized"
FILL_MODES = (FILL_NEXT_OPEN, FILL_TRIGGER, FILL_FUTURE_TRIGGER,
              FILL_RESTING, FILL_RESTING_SIZED)

# ── two-stage attention strategy ─────────────────────────────────────────────
# These values are deliberately softer than the legacy entry gates. They only
# promote a symbol into the attention queue; they never authorize a trade by
# themselves. See research/hypotheses/2026-09-09-attention-1m-forward.md.
ATTENTION_DAY_CHG_MIN_PCT = 1.5
ATTENTION_RVOL_MIN = 1.5
ATTENTION_CONFIRM_VOL_RATIO = 2.5
ATTENTION_PENDING_MINUTES = 3
PRICE_VOLUME_TREND_BARS = 4
ATTENTION_SETUP = "attention_1m_confirmation"
ATTENTION_FALSE_BREAK_RECLAIM_SETUP = "attention_false_break_reclaim"
ONE_MINUTE_SETUPS = ("micro_pullback", ATTENTION_SETUP, ATTENTION_FALSE_BREAK_RECLAIM_SETUP)

# ── one-minute agreement gate ────────────────────────────────────────────────
# "Do not take a five-minute entry the one-minute chart disagrees with."
# Every threshold here is REUSED from a rule already frozen in this module, so
# the gate introduces no new tunable number:
#   * EMA9 > EMA20        — the trend test `_attention_context` applies on 5m;
#   * close in the top 40% of the bar and volume ≥ ATTENTION_CONFIRM_VOL_RATIO
#                         — the two tests `_attention_confirmation` applies on 1m.
# See research/hypotheses/2026-09-12-one-minute-agreement.md.
ONE_MIN_CLOSE_POSITION_MIN = 0.60
ONE_MIN_EMA_FAST = 9
ONE_MIN_EMA_SLOW = 20

# ── the transcript's own entry checklist ─────────────────────────────────────
# Rules the Warrior guide states but which nothing in this engine enforced until
# 2026-09-15. Each is a separate EngineConfig switch, all default OFF, so the
# forward arms recorded before that date replay unchanged.
#
# PEAK_HOURS_END is the one genuinely NEW number here, and it is a judgement
# call, so it is stated plainly rather than buried: the guide trades 07:00–10:00
# EST, a window that ends 30 minutes after the 09:30 US open and is mostly
# pre-market. NSE has no continuous pre-market — the 09:00–09:08 pre-open is a
# single auction print — so the guide's window has no literal translation and
# the whole move it describes must happen after 09:15. 11:00 IST is the first
# 105 minutes of the session, which is the NSE morning volume peak and the
# closest honest analogue of "trade while volume, momentum and liquidity are
# highest; avoid low-volume midday". It is configurable (`MT_PEAK_HOURS_END`)
# precisely because it was chosen from session-volume shape, not from returns.
PEAK_HOURS_END = time(11, 0)
# "Focus strictly on the first and second pullbacks of a trend; third and fourth
# pullbacks carry significantly higher risk."
GUIDE_PULLBACK_ORDINALS = (1, 2)
# Bars of prior-session volume needed before a 1-minute volume ratio is trusted.
# This is the floor already written into `indicators.volume_ratio`'s own default
# expression, not a new threshold; see that docstring for why trading the open
# requires lowering it.
VOL_BASELINE_MIN_BARS = 3
# "Keep charts clean using the 9 EMA, 20 EMA, 200 EMA, VWAP, volume bars and a
# 1-minute MACD." The 200 is a chart reference in the guide, not an entry rule,
# so it is RECORDED on every entry and drawn on the review chart — never gated.
TREND_EMA_SPAN = 200

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
    # True = at most one trade per SYMBOL per day. False = multi-entry: once a
    # position closes, a later qualifying confirmation on the same symbol can
    # open a new one. Never overlapping — DayState holds a single `position`, so
    # re-entry is sequential, not pyramiding.
    # Default stays True so every prior backtest reproduces; the LIVE scanner
    # overrides it from MT_ONE_TRADE_PER_DAY (default False since 2026-09-13),
    # against the BT counter-evidence recorded in Settings.mt_one_trade_per_day.
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
    # Refuse a five-minute entry whose own one-minute chart is not in favour.
    # OFF by default, switchable per deployment via MT_REQUIRE_1M_AGREEMENT.
    # BT30 (2026-09-13) KILLED it as a FILTER: -0.0003 pp on mean gross,
    # anti-strategy p = 0.526, and the trades it removes are as good as the ones
    # it keeps (+0.0520% vs +0.0513%). Its only measured effect is FREQUENCY: it
    # cuts trade count 43%, which shrinks total loss while per-trade expectancy
    # is negative (-₹138.6k -> -₹83.1k on 2022-23) but costs slightly more per
    # trade (-₹81 vs -₹76; a random 43% cut loses less, -₹78.7k). Turn it on
    # only with that reading, and revisit it the moment expectancy turns
    # positive — it would then remove 43% of the profit.
    # research/hypotheses/2026-09-12-one-minute-agreement.md §5, §6
    require_1m_agreement: bool = False

    # BT33 variant of the local-resistance rule, OFF by default so the shipped
    # behaviour is unchanged. Three changes measured together (attribution
    # forfeited on purpose - the 5m merge was measured inert at 7.3%, so the
    # live parts are the other two):
    #   * no 5m level merge;
    #   * round-number marks excluded from the headroom test - a price grid is
    #     not supply, and round-number entry rules were already killed here;
    #   * a refusal ends the day instead of freeing it for a later entry.
    resistance_veto_v2: bool = False

    # Add volume-by-price shelves to the level set used by the headroom test.
    # A pivot needs `k` lower bars on each side, so it cannot see supply built
    # inside a fast move: one impulse bar blinds the detector for `k` bars
    # either side, which is where the sellers that stopped the run actually
    # are. Measured on EIHOTEL 2026-09-16 the gate's nearest structural
    # resistance above the trigger was a round number ₹300 - 3.3% away - while
    # 27% of the session's volume had already traded between the trigger and
    # the 2R target. OFF by default: this changes which trades exist, so every
    # recorded run keeps the level set it was measured with.
    volume_shelf_levels: bool = False
    # A forward playbook may name its exact setup and pullback ordinal. Empty
    # tuples preserve the full frozen Warrior setup list.
    allowed_setups: tuple[str, ...] = ()
    allowed_pullback_ordinals: tuple[int, ...] = ()
    fill_mode: str = FILL_NEXT_OPEN    # next_open | trigger | future_trigger
    # Entry slippage applied to RESTING fills only, in percent of the trigger.
    # A live buy-stop is filled by the first quote at or above the level, not at
    # the level itself, so a faithful replay prices it slightly worse. 0.0 keeps
    # every existing run byte-identical; the live-measured figure is ~0.03%.
    entry_slip_pct: float = 0.0
    exit_mode: str = exits.MODE_FIXED  # see exits.py MODES
    # New paper-only path: soft thresholds promote a symbol, 5-minute context
    # defines the setup, and a high-volume 1-minute candle confirms it.
    use_attention_entries: bool = False
    attention_day_chg_min: float = ATTENTION_DAY_CHG_MIN_PCT
    attention_rvol_min: float = ATTENTION_RVOL_MIN
    attention_confirm_vol_ratio: float = ATTENTION_CONFIRM_VOL_RATIO
    attention_pending_minutes: int = ATTENTION_PENDING_MINUTES
    # Experimental overlay for the familiar four-quadrant price/volume chart.
    # A long entry needs both close and total-volume slopes to be positive over
    # the last four completed one-minute bars. OFF by default: BT31 found a real
    # but too-small volume effect, and this switch exists for isolated replay,
    # not as a claim that OHLCV contains true buy/sell volume.
    require_rising_price_volume: bool = False
    # The resistance-state arm never enters with <1R of room to a structural
    # ceiling.  A later high-volume close through that ceiling creates a fresh
    # confirmation instead.
    require_resistance_breakout: bool = False
    # Historical-replay-only retry. Production strategy selection never enables
    # it because structural support now replaces false-break exits.
    allow_false_break_reentry: bool = False
    # At fill, freeze the nearest
    # structural support and resistance known from completed bars. A close below
    # the support replaces the two-close false-break exit; a nearer resistance
    # caps a live fixed target. This is the production default.
    use_structural_exit_levels: bool = True

    # ── the Warrior transcript's stated entry checklist (all default OFF) ────
    # Each maps to one line of the guide and each REFUSES trades, so turning any
    # of them on makes the arm a strict subset of the arm without it.
    # "Wait for the pullback ... buy at the exact moment the first candle makes
    # a new high above the high of the previous candle." Without this the
    # attention lane enters on any strong candle, pullback or not.
    require_micro_pullback: bool = False
    # "Both Volume Profile (light volume on pullbacks) and MACD ... must give
    # positive signals before entering" — the first of the two yeses.
    require_light_pullback_volume: bool = False
    # ... and the second: "MACD (positive and open)", read on the 1-minute frame
    # the guide specifies. Flat counts as a no ("DON'T trade ... if the MACD is
    # negative or flat").
    require_macd_positive_open: bool = False
    # "DO trade during peak volatility hours ... DON'T trade during low-volume
    # midday hours." Caps the entry deadline at `peak_hours_end`.
    peak_hours_only: bool = False
    peak_hours_end: time = PEAK_HOURS_END
    # Prior-session bars warm the 5-minute trend context, so a stock can be
    # promoted before 10:55. Without this the 20-bar EMA warm-up silently makes
    # `peak_hours_only` unsatisfiable: the two rules together would take zero
    # trades. Prior sessions are not look-ahead; this is the same warm-up the
    # exit rules have used since 2026-09-05.
    warm_context: bool = False
    # Bars required before a 1-minute volume ratio is produced. None = the
    # frozen default (first reading 09:25, i.e. blind to the whole open).
    vol_baseline_min_bars: int | None = None
    # Add the 2:1 target to a trend exit mode, so a position closes on whichever
    # of target / stop / trend-break comes first. The guide keeps both.
    use_fixed_target: bool = False
    # Where the protective stop is lifted to the ENTRY price, in multiples of
    # the initial risk (1R = the entry-to-stop distance). None = the frozen
    # BREAKEVEN_AT_R = 1.0. Raising it leaves the original hard stop in place
    # for longer - the trade can give back more - in exchange for not being
    # scratched out by ordinary wobble just above 1R, which is what produces
    # ~22% of all exits today (`trail_stop`, 1.3% gross win rate).
    # research/hypotheses/2026-09-20-breakeven-at-1p5r.md
    breakeven_at_r: float | None = None
    # Backtest-reproduction switch ONLY. True restores the pre-2026-09-20
    # behaviour in which a single minute could both justify the breakeven lift
    # (off its high) and fill it (off its low) - an order nobody could place,
    # firing on 4.6% of 2023-24 trades and understating P&L by 13.34 INR/trade.
    # The cost-aware stop always deferred; now the plain lock does too.
    # research/hypotheses/2026-09-20-breakeven-at-1p5r.md
    legacy_same_bar_breakeven: bool = False
    # Research-only subtractions. `no_trailing_stops` removes every stop that
    # MOVES after entry (the breakeven lift and the swing-low ratchet), leaving
    # only the hard stop decided at entry. `no_trend_exits` removes the five
    # indicator exits. Neither touches the false-break exit or the 15:15 close.
    no_trailing_stops: bool = False
    no_trend_exits: bool = False
    # Experimental management overlay: once a trade reaches 1R, lift its stop
    # to a price that covers round-trip costs and adds a small tick buffer.
    # Disabled by default so every existing strategy/replay remains unchanged.
    # The breakeven price is computed from REAL round-trip costs only; the
    # research stress slip is a P&L accounting overlay, not a cost the trader
    # pays, and folding it in would place the stop near the 2R target.
    cost_aware_breakeven: bool = False
    cost_stop_extra_ticks: int = 1
    # Buy-side half of the same idea: once the first tranche is protected at the
    # cost-aware breakeven, the trade has proved itself, so add a SECOND tranche
    # and let the winner carry more size. Implies `cost_aware_breakeven` — adding
    # to a position whose stop is still the original hard stop would double the
    # rupee risk, which is a different (and worse) experiment.
    #
    # Zero new tunables. The trigger is the frozen BREAKEVEN_AT_R = 1.0, the
    # add-on's rupee risk is the same `risk_inr` the first tranche used, its
    # stop is the cost-aware stop both tranches now share, and it is capped by
    # the same `max_notional_inr`. Because the cap applies per tranche, total
    # exposure can reach 2 x max_notional — which is why the pre-registered
    # primary criterion is the ADD-ON TRANCHE'S OWN net P&L, a number that
    # cannot be flattered by simply deploying more capital.
    # research/hypotheses/2026-09-19-add-to-winner-at-1r.md
    pyramid_add_at_1r: bool = False
    # Scales the FIRST tranche: both its rupee risk and its share of the
    # notional cap. Scaling risk alone would be a no-op on every trade where the
    # notional cap is the binding constraint, and that is a large minority of
    # them. 1.0 is every arm ever run.
    # 0.5 with `pyramid_add_at_1r` is the "half now, half once it proves itself"
    # variant: same maximum exposure as today, but half the money at risk during
    # the first minutes, which is where the measured loss is concentrated
    # (BT17: ~39% of trades stop out within five minutes).
    initial_risk_fraction: float = 1.0
    # Backtest controls only, split so each half of the 2026-09-18 exit change
    # can be attributed on its own. Defaults reproduce the current behavior:
    # a false break needs two consecutive closes, and it is judged only after
    # the executable stop/target orders.
    legacy_single_close_false_break: bool = False
    legacy_false_break_before_stop: bool = False
    exit_cfg: exits.ExitConfig = field(init=False)

    def __post_init__(self) -> None:
        if self.fill_mode not in FILL_MODES:
            raise ValueError(f"unknown fill mode {self.fill_mode!r}; expected one of {FILL_MODES}")
        if self.entry_slip_pct < 0.0:
            raise ValueError("entry_slip_pct must be non-negative")
        if self.attention_day_chg_min < 0.0:
            raise ValueError("attention_day_chg_min must be non-negative")
        if self.attention_rvol_min <= 0.0:
            raise ValueError("attention_rvol_min must be positive")
        if self.attention_confirm_vol_ratio <= 0.0:
            raise ValueError("attention_confirm_vol_ratio must be positive")
        if self.attention_pending_minutes <= 0:
            raise ValueError("attention_pending_minutes must be positive")
        if self.vol_baseline_min_bars is not None and self.vol_baseline_min_bars < 1:
            raise ValueError("vol_baseline_min_bars must be positive")
        if self.cost_stop_extra_ticks < 0:
            raise ValueError("cost_stop_extra_ticks must not be negative")
        if self.breakeven_at_r is not None and self.breakeven_at_r <= 0.0:
            raise ValueError("breakeven_at_r must be positive")
        if self.no_trailing_stops and self.breakeven_at_r is not None:
            # There is no breakeven lift left to place, so a threshold for it
            # would be silently inert.
            raise ValueError("breakeven_at_r is meaningless with no_trailing_stops")
        if self.no_trailing_stops and self.cost_aware_breakeven:
            raise ValueError("cost_aware_breakeven is a trailing stop; "
                             "it cannot be combined with no_trailing_stops")
        if self.breakeven_at_r is not None and self.cost_aware_breakeven:
            # The cost stop arms off its own literal 1R test in the bar loop.
            # Allowing both would leave two different definitions of "reached
            # 1R" running against the same position, so refuse the combination
            # rather than silently pick one.
            raise ValueError("breakeven_at_r cannot be combined with "
                             "cost_aware_breakeven")
        if not 0.0 < self.initial_risk_fraction <= 1.0:
            raise ValueError("initial_risk_fraction must be in (0, 1]")
        if self.initial_risk_fraction != 1.0 and not self.pyramid_add_at_1r:
            # Starting small without ever adding is just a smaller strategy; it
            # would change every rupee figure while testing nothing about
            # scaling in.
            raise ValueError("initial_risk_fraction < 1 needs pyramid_add_at_1r")
        if self.pyramid_add_at_1r and not self.cost_aware_breakeven:
            # Adding size while the first tranche still sits on its original
            # hard stop doubles the rupee risk instead of holding it constant.
            # That is a different experiment, and not the one registered.
            raise ValueError("pyramid_add_at_1r needs cost_aware_breakeven")
        if self.require_light_pullback_volume and not self.require_micro_pullback:
            # The light-volume test is a property OF the pullback, so there is
            # nothing to measure it on unless a pullback is required. Failing
            # loudly beats silently ignoring a switch the operator set.
            raise ValueError(
                "require_light_pullback_volume needs require_micro_pullback"
            )
        if self.use_structural_exit_levels and self.allow_false_break_reentry:
            raise ValueError(
                "use_structural_exit_levels replaces false-break exits and cannot "
                "be combined with allow_false_break_reentry"
            )
        if self.use_structural_exit_levels and (
            self.legacy_single_close_false_break or self.legacy_false_break_before_stop
        ):
            raise ValueError(
                "legacy false-break controls are incompatible with use_structural_exit_levels"
            )
        self.exit_cfg = exits.ExitConfig.for_mode(
            self.exit_mode, use_fixed_target=self.use_fixed_target,
            breakeven_at_r=self.breakeven_at_r,
            legacy_same_bar_breakeven=self.legacy_same_bar_breakeven,
            no_trailing_stops=self.no_trailing_stops,
            no_trend_exits=self.no_trend_exits,
        )

    @property
    def entry_deadline(self) -> time:
        """Latest bar-start that may still open a position.

        The peak-hours rule TIGHTENS the existing 14:30 cutoff and never relaxes
        it, so a deployment cannot accidentally extend its trading day by
        switching the guide's rule on.
        """
        if self.peak_hours_only:
            return min(self.entry_cutoff, self.peak_hours_end)
        return self.entry_cutoff


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
    # Context-checked formations, with exact timeframe, candles and rule version.
    pattern_matches: list[dict[str, object]] = field(default_factory=list)
    entry_evidence: dict[str, object] = field(default_factory=dict)
    pattern_rules_version: str = PATTERN_RULES_VERSION
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
    # The same two levels in rupees, and the price they were measured from.
    # The percentages are anchored to the TRIGGER; a reader that rebuilds them
    # from the fill is wrong by the whole entry slip. See location.Location.
    level_anchor_px: float | None = None
    resist_px: float | None = None
    support_px: float | None = None
    resist_kind: str = ""
    support_kind: str = ""


@dataclass
class Pending:
    cand: Candidate
    decision_time: pd.Timestamp | None = None
    expires_at: pd.Timestamp | None = None
    # True when the market had ALREADY traded through the trigger at the moment
    # this entry was armed (the legacy setups fire on `bar.high > trigger`), so a
    # buy-stop resting at the level would have been hit inside that same bar.
    # False for the attention path, whose trigger sits ABOVE price at decision
    # time — there the order genuinely waits, and a resting fill may never happen.
    trigger_reached: bool = False
    # Highest price the arming bar actually traded. A resting fill can never be
    # priced above it, so entry slippage cannot invent a price on a bar that only
    # just touched the level.
    armed_high: float = 0.0


@dataclass(frozen=True)
class AttentionEvent:
    symbol: str
    time: pd.Timestamp
    day_chg_pct: float
    rvol: float
    reason: str
    candle_tags: tuple[str, ...]
    evidence: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Rejection:
    symbol: str
    time: pd.Timestamp
    reason: str
    setup: str
    trigger: float
    observed_price: float | None = None
    evidence: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class FalseBreakReclaim:
    """One eligible retry after an attention breakout loses its level.

    ``level`` is frozen from the original candidate.  It is not recalculated
    from later candles, so the retry can only reclaim the level that actually
    failed and cannot quietly become a different setup.
    """
    level: float
    exit_time: pd.Timestamp


@dataclass
class Position:
    cand: Candidate
    entry_time: pd.Timestamp
    plan: TradePlan
    highest: float
    exit_state: exits.ExitState
    target_source: str = "fixed_2r"
    # Cost-aware breakeven bookkeeping. `armed` is set on the bar that first
    # reaches 1R; the stop is raised on the NEXT bar, matching the swing
    # trail's convention that a level decided by a bar cannot also fill on it.
    cost_stop_armed: bool = False
    cost_stop_applied: bool = False
    # Second tranche bought at the 1R add-on, if `pyramid_add_at_1r` is on.
    # Zero when no add was made, which is every position in every arm that has
    # the flag off — so the closed-trade maths below reduces to the original.
    add_qty: int = 0
    add_entry: float = 0.0
    add_time: pd.Timestamp | None = None


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
    target: float = 0.0
    target_source: str = "fixed_2r"
    structural_support: float | None = None
    structural_support_kind: str = ""
    structural_resistance: float | None = None
    structural_resistance_kind: str = ""
    # Add-on tranche attribution. `qty`/`entry` keep describing the FIRST
    # tranche so existing rows and live records are unchanged, while
    # `gross_inr`/`costs_inr`/`net_inr` are always the WHOLE position, because
    # that is the money the account actually made.
    add_qty: int = 0
    add_entry: float = 0.0
    add_time: pd.Timestamp | None = None
    base_net_inr: float = 0.0    # first tranche only
    add_net_inr: float = 0.0     # add-on tranche only

    @property
    def total_qty(self) -> int:
        return self.qty + self.add_qty

    @property
    def avg_entry(self) -> float:
        """Size-weighted entry across tranches; equals `entry` when no add."""
        if self.add_qty <= 0:
            return self.entry
        return ((self.entry * self.qty + self.add_entry * self.add_qty)
                / self.total_qty)

    @property
    def gross_pct(self) -> float:
        return (self.exit / self.avg_entry - 1.0) * 100.0

    @property
    def net_pct(self) -> float:
        return self.net_inr / (self.avg_entry * self.total_qty) * 100.0


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
    attention_evidence: dict[str, object] = field(default_factory=dict)
    attention_events: list[AttentionEvent] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    false_break_reclaim: FalseBreakReclaim | None = None
    false_break_reclaim_attempted: bool = False
    # Set when the resistance headroom test refuses an entry under
    # `resistance_veto_v2`: the day is finished, not merely postponed.
    resistance_refused: bool = False


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
    grouped = bars_1m.resample("5min", label="left", closed="left")
    out = grouped.agg(agg).dropna(subset=["open"])
    # Missing minutes must not fabricate a complete pattern candle. Reject
    # duplicate/off-minute input too; the chart uses this same clock contract.
    if (not bars_1m.index.is_unique
            or (bars_1m.index != bars_1m.index.floor("1min")).any()):
        return out.iloc[:0]
    out = out[grouped["close"].count().reindex(out.index) == 5]
    return out[out.index <= bars_1m.index[-1] - pd.Timedelta(minutes=4)]


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
    base_net = gross - costs
    # The add-on tranche is a second round trip: its own entry price, its own
    # brokerage and taxes. Charging it separately is what makes `add_net_inr`
    # an honest standalone answer to "did buying more pay for itself?".
    add_gross = add_costs = 0.0
    if pos.add_qty > 0:
        add_gross = (px - pos.add_entry) * pos.add_qty
        add_costs = calc_costs(pos.add_entry, px, pos.add_qty, direction="long")["total"]
        add_costs += (pos.add_entry + px) * pos.add_qty * cfg.stress_slip
        gross += add_gross
        costs += add_costs
    state.closed.append(ClosedTrade(
        cand=pos.cand, entry_time=pos.entry_time, entry=entry, exit_time=when, exit=px,
        exit_reason=reason, qty=qty, gross_inr=gross, costs_inr=costs, net_inr=gross - costs,
        target=pos.plan.target, target_source=pos.target_source,
        structural_support=(pos.exit_state.structural_support.price
                            if pos.exit_state.structural_support is not None else None),
        structural_support_kind=(pos.exit_state.structural_support.kind
                                 if pos.exit_state.structural_support is not None else ""),
        structural_resistance=(pos.exit_state.structural_resistance.price
                               if pos.exit_state.structural_resistance is not None else None),
        structural_resistance_kind=(pos.exit_state.structural_resistance.kind
                                    if pos.exit_state.structural_resistance is not None else ""),
        add_qty=pos.add_qty, add_entry=pos.add_entry, add_time=pos.add_time,
        base_net_inr=base_net, add_net_inr=add_gross - add_costs,
    ))
    # The control never reaches this branch because the option is disabled.
    # A retry is only possible after a genuine attention false break; a hard
    # stop, EMA exit, target, or a retry's own failure can never create another
    # opportunity.  This makes the new arm strictly one extra attempt.
    if (
        cfg.allow_false_break_reentry
        and reason == "false_break"
        and pos.cand.setup.name == ATTENTION_SETUP
        and pos.cand.setup.level is not None
        and not state.false_break_reclaim_attempted
    ):
        state.false_break_reclaim = FalseBreakReclaim(
            level=float(pos.cand.setup.level), exit_time=when
        )
    state.position = None


def _add_to_winner(
    pos: Position, price: float, when: pd.Timestamp, cfg: EngineConfig,
) -> None:
    """Buy a second tranche once the trade has paid for itself.

    Called on the bar that binds the cost-aware stop, i.e. the bar AFTER the
    one that first reached 1R, and filled at that bar's open. Nothing later in
    the bar is consulted, so this cannot use information the trader would not
    have had at the moment of the add.

    Sizing repeats the first tranche's own rule exactly: the two tranches are
    equal-risk. It risks `risk_inr x initial_risk_fraction` against the stop the
    position now carries, under the same proportional share of the notional cap.
    No new number is introduced. So at `initial_risk_fraction` 1.0 the position
    ends up about twice today's size, and at 0.5 it ends up about today's size
    having spent the first leg of the move at half of it.

    If the shared stop is already at or above the add price — a gap that opens
    straight into the stop — there is no room to risk anything and no add is
    made.
    """
    stop = pos.exit_state.stop
    per_share_risk = price - stop
    if per_share_risk <= 0.0 or price <= 0.0:
        return
    qty = min(int(cfg.risk_inr * cfg.initial_risk_fraction // per_share_risk),
              int(cfg.max_notional_inr * cfg.initial_risk_fraction // price))
    if qty < 1:
        return
    pos.add_qty = qty
    pos.add_entry = price
    pos.add_time = when


def _cost_aware_breakeven_stop(entry: float, qty: int, extra_ticks: int) -> float:
    """Minimum sell stop that covers real round-trip costs, plus a tick buffer.

    Deliberately excludes `stress_slip`: that is a research stress applied to
    reported P&L, not a brokerage cost the trade actually pays. Including it
    would push this stop to roughly +1% of entry under bt17's 40 bps/side,
    which is about the 2R target, so the backtest would be measuring a
    different rule from the one live would run.
    """
    if qty < 1:
        raise ValueError("cost-aware stop needs at least one share")

    def net(price: float) -> float:
        costs = calc_costs(entry, price, qty, direction="long")["total"]
        return (price - entry) * qty - costs

    low, high = entry, entry + COST_STOP_TICK_SIZE
    while net(high) < 0.0:
        high += max(entry * 0.01, COST_STOP_TICK_SIZE)
    for _ in range(60):
        mid = (low + high) / 2.0
        if net(mid) < 0.0:
            low = mid
        else:
            high = mid
    return (math.ceil(high / COST_STOP_TICK_SIZE) + extra_ticks) * COST_STOP_TICK_SIZE


def _legacy_false_break(bars: pd.DataFrame, level: float) -> bool:
    """Pre-2026-09-18 one-close false-break condition, retained for backtests."""
    if len(bars) < 2 or level <= 0:
        return False
    previous, current = bars.iloc[-2], bars.iloc[-1]
    return float(previous["high"]) > level and float(current["close"]) < level


def _false_break_fired(cfg: EngineConfig, tf_bars: pd.DataFrame, level: float | None,
                       entry_floor: pd.Timestamp) -> bool:
    """Evaluate the configured false-break rule on the position's own timeframe.

    Every CLOSE used as evidence must come from a bar that closed after the
    fill. Only `index[-1]` was checked before, which is enough for the
    one-close rule but not for two: on the five-minute path `entry_floor` is
    floored to the bucket, so both confirming closes could pre-date the entry
    entirely and still exit the trade. The breakout bar is context, not
    evidence, and may legitimately pre-date the fill.

    Which predicate applies is a backtest control; live always uses two closes.
    """
    if level is None:
        return False
    closes = 1 if cfg.legacy_single_close_false_break else 2
    if len(tf_bars) < closes + 1 or tf_bars.index[-closes] < entry_floor:
        return False
    if cfg.legacy_single_close_false_break:
        return _legacy_false_break(tf_bars, level)
    return false_break(tf_bars, level, closes)


def force_close(
    state: DayState, when: pd.Timestamp, price: float, cfg: EngineConfig,
    reason: str = "eod_sweep",
) -> ClosedTrade | None:
    """Close an open position unconditionally, at `price`.

    `step()` can only exit a position on a bar that ARRIVES, and it reads the
    clock off the last bar's own timestamp. A live symbol that stops printing
    before 15:15 therefore never reaches the `eod_close` branch and would carry
    overnight — which this strategy must never do. The live scanner calls this
    from a wall-clock sweep; the backtest never needs it, because `run_day`
    always has the day's final bar in hand.

    Returns the trade it closed, or None when there was no open position.
    """
    if state.position is None:
        return None
    _exit(state, when, price, reason, cfg)
    return state.closed[-1]


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


def _macd_open_state(
    bars: pd.DataFrame, warmup: pd.DataFrame | None
) -> tuple[float, float] | None:
    """(histogram now, histogram on the previous bar), or None before warm-up.

    The guide's "MACD positive and open" is two readings, not one: *positive*
    means the histogram is above zero, *open* means the gap is still widening
    rather than converging. One value cannot express the second, which is why
    this returns a pair. Reuses `exits.indicator_frame`, so the entry reading
    and the `macd_fade` exit reading are the same number computed the same way.
    """
    if len(bars) < 2:
        return None
    h = exits.indicator_frame(bars, warmup)["macd_hist"]
    if len(h) < 2 or pd.isna(h.iloc[-1]) or pd.isna(h.iloc[-2]):
        return None
    return float(h.iloc[-1]), float(h.iloc[-2])


def _macd_1m_hist(bars_1m: pd.DataFrame, warmup_1m: pd.DataFrame | None) -> float | None:
    """1-minute MACD histogram for the record, None before warm-up."""
    state = _macd_open_state(bars_1m, warmup_1m)
    return None if state is None else state[0]


def _trend_ema(
    tf5: pd.DataFrame, warmup_5m: pd.DataFrame | None, span: int = TREND_EMA_SPAN
) -> float | None:
    """The guide's 200 EMA on the position's 5-minute frame. Recorded, not gated.

    200 five-minute bars is roughly 2.7 NSE sessions, so this is None for the
    whole day unless prior-session bars are supplied.
    """
    if tf5.empty:
        return None
    joined = tf5
    if warmup_5m is not None and not warmup_5m.empty:
        joined = pd.concat([warmup_5m.iloc[-exits.WARMUP_BARS:], tf5])
        joined = joined[~joined.index.duplicated(keep="last")].sort_index()
    v = ema(joined["close"], span)
    if not len(v) or pd.isna(v.iloc[-1]):
        return None
    return float(v.iloc[-1])


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


def _pullback_ordinal_gate(
    pullback_ord: int | None, cfg: EngineConfig
) -> tuple[bool, str]:
    """The ordinal half of `_playbook_gate`, for the attention lane.

    The attention setup is not one of the seven named Warrior setups, so the
    `allowed_setups` half of the playbook gate cannot apply to it; only the
    "first or second pullback" rule can. A missing ordinal is a REFUSAL rather
    than a pass: `None` means no sharp advance has been identified today, and a
    pullback with nothing to pull back from is not the setup the guide
    describes. This is the reading that makes a refused entry measurable — the
    live log separates `pullback_no_anchor` from `pullback_not_allowed`.
    """
    if not cfg.allowed_pullback_ordinals:
        return True, "ok"
    if pullback_ord is None:
        return False, "pullback_no_anchor"
    if pullback_ord not in cfg.allowed_pullback_ordinals:
        return False, "pullback_not_allowed"
    return True, "ok"


def _one_minute_gate(
    bars_1m: pd.DataFrame, setup: Setup, cfg: EngineConfig
) -> tuple[bool, str]:
    """(ok, reason) — is the one-minute chart in favour of this entry?

    Judged on the last CLOSED one-minute bar at the moment the five-minute
    setup is detected, so it can never see past the decision and it reads the
    same in every fill mode (the fill-latency comparison needs both arms to
    take identical trades).

    A setup that is itself decided on one-minute evidence passes untouched:
    its own confirmation already IS this test, and re-applying it would make
    the attention lane a different strategy rather than a gated one.
    """
    if not cfg.require_1m_agreement:
        return True, "ok"
    if _is_one_minute_setup(setup.name):
        return True, "ok"
    if len(bars_1m) < ONE_MIN_EMA_SLOW + 1:
        return False, "1m_warmup"
    fast = ema(bars_1m["close"], ONE_MIN_EMA_FAST)
    slow = ema(bars_1m["close"], ONE_MIN_EMA_SLOW)
    if pd.isna(fast.iloc[-1]) or pd.isna(slow.iloc[-1]):
        return False, "1m_warmup"
    if float(fast.iloc[-1]) <= float(slow.iloc[-1]):
        return False, "1m_trend_down"
    bar = bars_1m.iloc[-1]
    open_, high, low, close = map(float, (bar["open"], bar["high"], bar["low"], bar["close"]))
    rng = high - low
    if rng <= 0.0 or close <= open_:
        return False, "1m_red_or_flat"
    if (close - low) / rng < ONE_MIN_CLOSE_POSITION_MIN:
        return False, "1m_weak_close"
    vr = volume_ratio(bars_1m).iloc[-1]
    if pd.isna(vr) or float(vr) < cfg.attention_confirm_vol_ratio:
        return False, "1m_low_volume"
    return True, "ok"


def _is_one_minute_setup(name: str) -> bool:
    return name in ONE_MINUTE_SETUPS


def _attention_context(
    tf5: pd.DataFrame, warmup_5m: pd.DataFrame | None = None
) -> tuple[bool, str]:
    """Five-minute trend context for the attention queue.

    Promotion is intentionally not an entry. A neutral doji can draw attention,
    but a trade still needs a separate high-volume one-minute confirmation.

    With no `warmup_5m` the EMAs are computed on today alone and need 20
    completed 5-minute bars, so the earliest possible promotion is 10:55 IST —
    measured on the live log, the first attention event on both 2026-09-10 and
    2026-09-11 was 10:44 and nothing at all happened before it. That is the
    whole of the guide's peak window, skipped. Passing prior-session bars warms
    the same EMAs so a promotion can happen from the open. VWAP is deliberately
    NOT warmed: session VWAP resets daily by definition.
    """
    if tf5.empty:
        return False, "ema_warmup"
    warm = warmup_5m is not None and not warmup_5m.empty
    if not warm and len(tf5) < 20:
        return False, "ema_warmup"
    if warm:
        ind = exits.indicator_frame(tf5, warmup_5m)
        fast, slow = ind["ema_fast"], ind["ema_slow"]
    else:
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

    Both timeframes use the same contextual rules and preserve their own exact
    timestamps. One-minute evidence is never presented as a five-minute
    formation. Callers supply closed bars only: an evolving candle cannot
    establish a completed pattern.
    """
    patterns = [*completed_pattern_matches(tf5, "5m"),
                *completed_pattern_matches(bars_1m, "1m")]
    tags = list(dict.fromkeys(match.name for match in patterns))
    matches = [match.document() for match in patterns]
    # Neutral indecision can draw attention under the existing two-stage
    # strategy, but is never called a bullish reversal. Bearish patterns cannot
    # promote a long just because a geometry alias resembles a bullish shape.
    eligible = [match for match in patterns if match.direction == "bullish"
                or (match.direction == "neutral" and match.kind == "indecision")]
    if eligible:
        chosen = eligible[0]
        return f"pattern:{chosen.name}:{chosen.timeframe}", tags, matches
    # Detect structures even when the legacy +4% / 3x entry gate has not fired.
    found = scan_setups(tf5, bars_1m, None)
    if found:
        return f"setup:{found[0].name}", tags, matches
    return None, tags, matches


def _entry_evidence(state: DayState, bars_1m: pd.DataFrame,
                    tf5: pd.DataFrame, cfg: EngineConfig) -> dict[str, object]:
    """Freeze measured values; never substitute signal-time RVOL for promotion."""
    bar = bars_1m.iloc[-1]
    fast, slow, vw = ema(tf5["close"], 9), ema(tf5["close"], 20), session_vwap(tf5)
    return {
        "pattern_rules_version": PATTERN_RULES_VERSION,
        "promotion": dict(state.attention_evidence),
        "trend": {
            "timeframe": "5m", "bar_start": tf5.index[-1].isoformat(),
            "close": float(tf5["close"].iloc[-1]), "ema9": float(fast.iloc[-1]),
            "ema20": float(slow.iloc[-1]), "vwap": float(vw.iloc[-1]),
            # The guide's third moving average. Recorded, never gated — it is
            # listed as a chart indicator, not as an entry rule. None until
            # ~2.7 sessions of 5-minute bars exist, so it needs warm-up bars.
            "ema200": _trend_ema(tf5, state.warmup_5m),
        },
        "confirmation": {
            "timeframe": "1m", "bar_start": bars_1m.index[-1].isoformat(),
            "formed_at": (bars_1m.index[-1] + pd.Timedelta(minutes=1)).isoformat(),
            **{k: float(bar[k]) for k in ("open", "high", "low", "close", "volume")},
            "close_position": float((bar["close"] - bar["low"]) / (bar["high"] - bar["low"])),
            "minimum_close_position": ONE_MIN_CLOSE_POSITION_MIN,
            "volume_ratio": float(
                volume_ratio(bars_1m, min_periods=cfg.vol_baseline_min_bars).iloc[-1]
            ),
            "minimum_volume_ratio": cfg.attention_confirm_vol_ratio,
            # The guide reads MACD on the 1-minute chart. Recorded on every
            # entry whether or not `require_macd_positive_open` is gating, so
            # the rule can be measured against the trades it did NOT refuse.
            "macd_hist": _macd_1m_hist(bars_1m, state.warmup_1m),
        },
        "pending_minutes": cfg.attention_pending_minutes,
        "checklist": {
            "micro_pullback": cfg.require_micro_pullback,
            "light_pullback_volume": cfg.require_light_pullback_volume,
            "macd_positive_open": cfg.require_macd_positive_open,
            "peak_hours_only": cfg.peak_hours_only,
            "entry_deadline": cfg.entry_deadline.isoformat(),
            "pullback_ordinals": list(cfg.allowed_pullback_ordinals),
            "fixed_target_rr": cfg.rr if cfg.exit_cfg.has_target else None,
        },
    }


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


@dataclass(frozen=True)
class GuideGates:
    """Everything the transcript's entry checklist needs for one decision.

    Passed as a single object rather than as four arguments because three
    separate call sites produce an attention confirmation (plain, resistance-
    aware, false-break reclaim) and all three must apply the identical
    checklist. A `None` here means "the checklist is off", which is what every
    arm recorded before 2026-09-15 used.
    """

    cfg: EngineConfig
    warmup_1m: pd.DataFrame | None = None


def _guide_for(state: DayState, cfg: EngineConfig) -> GuideGates | None:
    """The checklist for this symbol, or None when no guide switch is set.

    Returning None rather than an all-off `GuideGates` keeps the disabled path
    byte-identical to the pre-2026-09-15 code: `_attention_confirmation` takes
    its original branch and even reads `volume_ratio` with its original
    arguments.
    """
    if not (cfg.require_micro_pullback or cfg.require_macd_positive_open
            or cfg.vol_baseline_min_bars is not None):
        return None
    return GuideGates(cfg=cfg, warmup_1m=state.warmup_1m)


def _attention_confirmation(
    bars_1m: pd.DataFrame,
    min_volume_ratio: float,
    guide: GuideGates | None = None,
    require_rising_price_volume: bool = False,
) -> tuple[Setup | None, str]:
    """Confirmed one-minute entry inside an already-promoted five-minute trend.

    A valid bar must be green, close in the upper 40% of its range, carry at
    least `min_volume_ratio` times recent one-minute volume. Candlestick shapes
    create the attention context; requiring a second named shape here would
    miss an ordinary strong breakout candle. The order is armed above the
    confirmation bar and is not filled until a future quote trades there.

    With a `guide`, the transcript's own checklist is added on top and the
    entry becomes the guide's entry rather than a strong-candle entry:

      * the candle must complete a **micro pullback** — 2+ green bars, a 1-2 bar
        red/doji pause, then the break — and the buy-stop moves DOWN from this
        candle's high to the pause high, which is where the guide actually buys
        ("the first candle makes a new high above the high of the previous
        candle"), with the stop at the pause low ("the low of the pullback");
      * the pause must have traded on **light volume** relative to the push;
      * the 1-minute **MACD must be positive and open**.

    Each refusal has its own reason string, so the forward log can say which of
    the checks is doing the rejecting rather than reporting one opaque count.
    """
    if len(bars_1m) < 3:
        return None, "attention_insufficient_bars"
    bar = bars_1m.iloc[-1]
    open_, high, low, close = map(float, (bar["open"], bar["high"], bar["low"], bar["close"]))
    rng = high - low
    if rng <= 0.0 or close <= open_:
        return None, "attention_red_or_flat"
    if (close - low) / rng < ONE_MIN_CLOSE_POSITION_MIN:
        return None, "attention_weak_close"
    cfg = guide.cfg if guide is not None else None
    vr = volume_ratio(
        bars_1m,
        min_periods=cfg.vol_baseline_min_bars if cfg is not None else None,
    )
    if pd.isna(vr.iloc[-1]) or float(vr.iloc[-1]) < min_volume_ratio:
        return None, "attention_low_1m_volume"

    pv_meta: dict[str, float] = {}
    if require_rising_price_volume:
        slopes = price_volume_slopes(bars_1m, PRICE_VOLUME_TREND_BARS)
        if slopes is None:
            return None, "attention_price_volume_insufficient"
        price_slope, volume_slope = slopes
        if price_slope <= 0.0 or volume_slope <= 0.0:
            return None, "attention_price_volume_not_confirmed"
        pv_meta = {
            "price_slope_pct_per_bar": price_slope * 100.0,
            "volume_slope_per_bar": volume_slope,
        }

    trigger = high
    stop = float(bars_1m.iloc[-3:]["low"].min())
    meta: dict[str, float] = {"volume_ratio": float(vr.iloc[-1]), **pv_meta}

    if cfg is not None and cfg.require_micro_pullback:
        # Tested in two steps so "there was no pullback" and "there was one but
        # sellers were in it" are distinguishable in the rejection log.
        pull = micro_pullback(bars_1m, max_pause_bars=MICRO_PAUSE_MAX_BARS)
        if pull is None:
            return None, "attention_no_micro_pullback"
        if cfg.require_light_pullback_volume:
            light = micro_pullback(
                bars_1m, max_pause_bars=MICRO_PAUSE_MAX_BARS, require_light_volume=True
            )
            if light is None:
                return None, "attention_heavy_pullback_volume"
            pull = light
        trigger, stop = pull.trigger, pull.stop
        meta.update(pull.meta)

    if cfg is not None and cfg.require_macd_positive_open:
        state = _macd_open_state(bars_1m, guide.warmup_1m if guide else None)
        if state is None:
            return None, "attention_macd_warmup"
        hist, prev_hist = state
        if hist <= 0.0:
            return None, "attention_macd_not_positive"
        if hist <= prev_hist:
            # "DON'T trade ... if the MACD is negative OR FLAT" — a histogram
            # that is no longer widening is the flat/converging case.
            return None, "attention_macd_not_open"
        meta["macd_hist_1m"] = hist

    if stop >= trigger:
        return None, "attention_invalid_stop"
    return (
        Setup(
            name=ATTENTION_SETUP,
            trigger=trigger,
            stop=stop,
            level=trigger,
            meta=meta,
        ),
        "confirmed",
    )


# Both branches of the headroom test refuse for the same reason: not enough
# room to the next ceiling. Kept as one set so a later rename cannot silently
# drop one of them from the v2 end-of-day rule.
_RESISTANCE_WAIT_REASONS = frozenset({
    "attention_wait_resistance_break",
    "attention_wait_next_resistance_break",
})


def _headroom_from(bars_1m: pd.DataFrame, setup: Setup) -> float:
    """The price the "room above" search starts from.

    A level the confirmation candle has already traded through is not supply any
    more — it is the break being bought. The search must therefore start at the
    higher of the buy-stop and where price actually is.

    This is invisible until the buy-stop sits BELOW the confirmation bar's
    close, which is exactly what `require_micro_pullback` does: it moves the
    trigger down to the pullback high, which is the guide's entry. Measuring
    headroom from there finds the level the breakout candle just cleared and
    calls it a ceiling — on a 2024 sample the strict arm took 0 trades against
    the deployed arm's 58, and every refusal was this. Before that change the
    trigger was the bar's own high, always at or above the close, so
    `max()` returns the trigger and every earlier arm is unaffected.
    """
    return max(setup.trigger, float(bars_1m["close"].iloc[-1]))


def _resistance_aware_attention_confirmation(
    bars_1m: pd.DataFrame,
    min_volume_ratio: float,
    prev_day: dict[str, float] | None,
    v2: bool = False,
    guide: GuideGates | None = None,
    shelves: bool = False,
    require_rising_price_volume: bool = False,
) -> tuple[Setup | None, str]:
    """Apply the unchanged 1-minute confirmation to structural resistance.

    The level set is built before the current confirmation bar.  If that bar
    closes through a structural ceiling, the setup's level becomes that ceiling
    so the existing false-break rule protects the new breakout.  Otherwise an
    entry with less than one initial-risk unit of room waits rather than buying
    into supply.
    """
    setup, reason = _attention_confirmation(
        bars_1m, min_volume_ratio, guide, require_rising_price_volume
    )
    if setup is None:
        return None, reason
    prior = bars_1m.iloc[:-1]
    if prior.empty:
        return setup, reason
    shelf_atr = (float(atr(prior).iloc[-1]) if shelves and len(prior) >= 15 else None)
    levels_before_confirmation = derive_levels(prior, prev_day,
                                               add_shelves=shelves, atr=shelf_atr)
    if not v2:
        # A one-minute pivot can sit immediately below a still-unbroken
        # five-minute ceiling.  Entry and charting must use the same
        # multi-timeframe view, or a local breakout can arm a trade directly
        # into the resistance shown to the operator.  Measured on 805
        # confirmations this changes the binding level 7.3% of the time, so v2
        # drops it and keeps the level set on one timeframe.
        prior_5m = resample_5m(prior)
        if not prior_5m.empty:
            levels_before_confirmation += derive_levels(
                prior_5m, prev_day, add_shelves=shelves, atr=None)

    # What counts as a ceiling for the headroom test. A round number is a
    # property of the price grid, not of supply, yet it is the binding level in
    # 31% of refusals; round-number entry rules were separately measured at
    # +0.022 pp and killed, so v2 refuses to spend a trade on one.
    headroom_levels = ([x for x in levels_before_confirmation if x.kind != "round"]
                       if v2 else levels_before_confirmation)

    # If the confirmation has just crossed the closest structural level, it is
    # the proper breakout confirmation—not an early signal below that level.
    crossed = nearest_structural_resistance(
        levels_before_confirmation, float(prior["close"].iloc[-1])
    )
    if crossed is not None and float(bars_1m["close"].iloc[-1]) > crossed.price:
        next_resistance = nearest_structural_resistance(
            headroom_levels, _headroom_from(bars_1m, setup)
        )
        initial_risk = setup.trigger - setup.stop
        if (next_resistance is not None
                and next_resistance.price - setup.trigger < initial_risk):
            return None, "attention_wait_next_resistance_break"
        return Setup(
            name=setup.name,
            trigger=setup.trigger,
            stop=setup.stop,
            level=crossed.price,
            meta={**setup.meta, "accepted_resistance": crossed.price},
        ), "confirmed_resistance_breakout"

    resistance = nearest_structural_resistance(
        headroom_levels, _headroom_from(bars_1m, setup)
    )
    if resistance is not None:
        headroom = resistance.price - setup.trigger
        initial_risk = setup.trigger - setup.stop
        if headroom < initial_risk:
            return None, "attention_wait_resistance_break"
        return setup, reason

    return setup, reason


def _false_break_reclaim_confirmation(
    state: DayState,
    bars_1m: pd.DataFrame,
    tf5: pd.DataFrame,
    cfg: EngineConfig,
    catalyst: CatalystLookup,
) -> bool:
    """Arm the one permitted retry only after the original level is reclaimed.

    The candidate must be a normal attention-quality one-minute candle, close
    above the *original* failed level, and retain the same completed five-minute
    trend context.  It is still a future-only buy-stop: this function never
    fills on the reclaim candle itself.
    """
    reclaim = state.false_break_reclaim
    if reclaim is None or bars_1m.empty:
        return False
    now = bars_1m.index[-1]
    # The exit candle has already proved the first break false.  Waiting for a
    # later completed candle prevents one bar from both exiting and re-entering.
    if now <= reclaim.exit_time:
        return False
    guide = _guide_for(state, cfg)
    context_ok, context_reason = _attention_context(
        tf5, state.warmup_5m if cfg.warm_context else None
    )
    if not context_ok:
        state.rejections.append(Rejection(
            symbol=state.symbol, time=now, reason=f"reclaim_context_lost:{context_reason}",
            setup=ATTENTION_FALSE_BREAK_RECLAIM_SETUP, trigger=reclaim.level,
            observed_price=float(bars_1m["close"].iloc[-1]),
        ))
        state.false_break_reclaim = None
        state.false_break_reclaim_attempted = True
        return False
    setup, reason = _attention_confirmation(
        bars_1m, cfg.attention_confirm_vol_ratio, guide,
        cfg.require_rising_price_volume,
    )
    if setup is None:
        state.rejections.append(Rejection(
            symbol=state.symbol, time=now, reason=f"reclaim_{reason}",
            setup=ATTENTION_FALSE_BREAK_RECLAIM_SETUP, trigger=reclaim.level,
            observed_price=float(bars_1m["close"].iloc[-1]),
            evidence=(volume_confirmation_evidence(bars_1m)
                      if reason.startswith("attention_price_volume_") else {}),
        ))
        return False
    if float(bars_1m["close"].iloc[-1]) <= reclaim.level:
        state.rejections.append(Rejection(
            symbol=state.symbol, time=now, reason="reclaim_below_failed_level",
            setup=ATTENTION_FALSE_BREAK_RECLAIM_SETUP, trigger=reclaim.level,
            observed_price=float(bars_1m["close"].iloc[-1]),
        ))
        return False

    ordinal = pullback_ordinal(tf5) if len(tf5) else None
    ord_ok, ord_reason = _pullback_ordinal_gate(ordinal, cfg)
    if not ord_ok:
        state.rejections.append(Rejection(
            symbol=state.symbol, time=now, reason=f"reclaim_{ord_reason}",
            setup=ATTENTION_FALSE_BREAK_RECLAIM_SETUP, trigger=reclaim.level,
            observed_price=float(bars_1m["close"].iloc[-1]),
        ))
        return False
    reentry_setup = Setup(
        name=ATTENTION_FALSE_BREAK_RECLAIM_SETUP,
        trigger=setup.trigger,
        stop=setup.stop,
        level=reclaim.level,
        meta={
            **setup.meta,
            "reclaim_level": reclaim.level,
            "original_exit_epoch": reclaim.exit_time.timestamp(),
        },
    )
    cat, ev = catalyst(state.symbol, now)
    loc = location.measure(tf5, reentry_setup.trigger, state.prev_day)
    dround, rh = loc.dist_to_round_pct, loc.round_head_pct
    res_head, sup_drop = loc.resist_head_pct, loc.support_drop_pct
    cand = Candidate(
        symbol=state.symbol, time=now, setup=reentry_setup,
        day_chg_pct=day_change_pct(bars_1m, state.prev_close),
        rvol=rvol_now(bars_1m, state.cum_vol_profile) or 0.0,
        catalyst=cat, event_type=ev, candle_tags=candle_tags(bars_1m),
        pattern_matches=_unique_pattern_documents(
            state.attention_patterns,
            [match.document() for match in completed_pattern_matches(tf5, "5m")],
            [match.document() for match in completed_pattern_matches(bars_1m, "1m")],
        ),
        entry_evidence=_entry_evidence(state, bars_1m, tf5, cfg),
        prev_day_gainer=state.prev_day_gainer,
        pullback_ord=ordinal,
        quality_reason="ok", atr_pct=state.daily_atr_pct,
        macd_hist=_macd_hist_now(tf5, state.warmup_5m),
        dist_to_round_pct=dround, round_head_pct=rh,
        resist_head_pct=res_head, support_drop_pct=sup_drop,
        level_anchor_px=loc.anchor_px,
        resist_px=loc.resist_px, support_px=loc.support_px,
        resist_kind=loc.resist_kind, support_kind=loc.support_kind,
    )
    state.candidates.append(cand)
    decision = now + pd.Timedelta(minutes=1)
    state.pending = Pending(
        cand, decision_time=decision,
        expires_at=decision + pd.Timedelta(minutes=cfg.attention_pending_minutes),
    )
    state.false_break_reclaim = None
    state.false_break_reclaim_attempted = True
    return True


def _open_position(state: DayState, cand: Candidate, when: pd.Timestamp, fill: float,
                   plan: TradePlan, cfg: EngineConfig, bars_1m: pd.DataFrame,
                   full_5m: pd.DataFrame | None) -> None:
    """Turn a filled plan into an open position (shared by every fill mode)."""
    ec = cfg.exit_cfg
    is_1m = _is_one_minute_setup(cand.setup.name)
    tf = bars_1m if is_1m else _bars_5m(bars_1m, full_5m)
    exit_state = exits.initial_state(
        entry=fill, hard_stop=cand.setup.stop, bars_tf=tf, prev_day=state.prev_day,
        with_levels=ec.use_resistance_reject or cfg.use_structural_exit_levels,
        add_shelves=cfg.volume_shelf_levels,
    )
    target_source = "fixed_2r" if ec.has_target else "disabled"
    resistance = exit_state.structural_resistance
    if (
        cfg.use_structural_exit_levels
        and ec.has_target
        and resistance is not None
        and fill < resistance.price < plan.target
    ):
        plan = replace(
            plan,
            target=resistance.price,
            reward_inr=(resistance.price - fill) * plan.qty,
        )
        target_source = f"structural_resistance:{resistance.kind}"
    state.position = Position(
        cand=cand, entry_time=when, plan=plan, highest=fill, exit_state=exit_state,
        target_source=target_source,
    )
    state.traded_today = True


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
        risk_inr=cfg.risk_inr * cfg.initial_risk_fraction,
        max_notional_inr=cfg.max_notional_inr * cfg.initial_risk_fraction,
        rr=cfg.rr, gate_entry=price,
    )
    if plan is None:
        _reject_pending(state, when, "stop_not_sane", price)
        return None
    cand = pending.cand
    state.pending = None
    _open_position(state, cand, when, price, plan, cfg, bars_1m, full_5m)
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

    # 1a. a resting buy-stop. The order sits in the book at the trigger, so it is
    # filled only when the market actually reaches the level, and a bar that opens
    # above it fills at that open instead — the mirror of `exits.check_stop`. The
    # chase cap and the stop-sanity band are both judged on the price actually
    # paid, exactly as the live quote path (`fill_pending_quote`) does.
    if state.pending is not None and cfg.fill_mode in (FILL_RESTING, FILL_RESTING_SIZED):
        pending = state.pending
        cand = pending.cand
        trigger = cand.setup.trigger
        if pending.expires_at is not None and now > pending.expires_at:
            _reject_pending(state, now, "pending_expired", float(bar["close"]))
            return
        # An entry armed while price was already through the level (the legacy
        # setups fire on `bar.high > trigger`) was hit inside its own signal bar.
        # One armed BELOW the level — the attention path — has to wait for it, and
        # filling before the market gets there would invent a price.
        if not pending.trigger_reached and float(bar["high"]) < trigger:
            return
        want = trigger * (1.0 + cfg.entry_slip_pct / 100.0)
        if pending.trigger_reached:
            # Hit inside its own signal bar, so THAT bar bounds the price — this
            # one cannot. Without the cap the slippage alone prices 3.8% of legacy
            # fills above anything that traded.
            fill = min(want, pending.armed_high) if pending.armed_high else want
        else:
            # A gap open above the level fills at the open, which is worse. But the
            # fill can never be above what actually traded: these triggers sit on
            # round numbers the bar often only just touches (high == trigger), so
            # without this cap the slippage alone invents a price — it priced 33.5%
            # of attention fills above their own bar's high.
            fill = min(max(want, float(bar["open"])), float(bar["high"]))
        if (fill / trigger - 1.0) * 100.0 > CHASE_MAX_EXT_PCT:
            _reject_pending(state, now, "chased", fill)
            return
        plan = plan_trade(
            fill, cand.setup.stop, risk_inr=cfg.risk_inr * cfg.initial_risk_fraction,
            max_notional_inr=cfg.max_notional_inr * cfg.initial_risk_fraction, rr=cfg.rr,
            gate_entry=fill if cfg.fill_mode == FILL_RESTING_SIZED else float(bar["open"]),
        )
        if plan is None:
            _reject_pending(state, now, "stop_not_sane", fill)
            return
        state.pending = None
        _open_position(state, cand, now, fill, plan, cfg, bars_1m, full_5m)
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
            fill, cand.setup.stop, risk_inr=cfg.risk_inr * cfg.initial_risk_fraction,
            max_notional_inr=cfg.max_notional_inr * cfg.initial_risk_fraction, rr=cfg.rr, gate_entry=next_open,
        )
        if plan is not None:
            _open_position(state, cand, now, fill, plan, cfg, bars_1m, full_5m)

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
        # A cost stop armed by an earlier bar binds from this one, before any
        # fill is checked. Raising it on the same bar that reached 1R would let
        # that bar's own high justify a stop its low could already have hit.
        if cfg.cost_aware_breakeven and pos.cost_stop_armed and not pos.cost_stop_applied:
            es.trail = max(
                es.trail,
                _cost_aware_breakeven_stop(
                    pos.plan.entry, pos.plan.qty, cfg.cost_stop_extra_ticks,
                ),
            )
            pos.cost_stop_applied = True
            # Buy side: the first tranche is now protected, so add a second one
            # on this same bar's open. Same bar as the stop lift by design —
            # both are decided by the previous bar reaching 1R, and neither may
            # use anything this bar does after its open.
            if cfg.pyramid_add_at_1r and pos.add_qty == 0:
                _add_to_winner(pos, float(bar["open"]), now, cfg)
        # A breakeven lift armed by an EARLIER bar binds here, before this
        # bar's low is tested — same ordering the cost stop above uses.
        exits.apply_pending_breakeven(es, ec)
        pos.highest = max(pos.highest, float(bar["high"]))
        exits.update_high(es, float(bar["high"]), ec)
        if (cfg.cost_aware_breakeven and not pos.cost_stop_applied
                and es.r_multiple(es.highest) >= 1.0):
            pos.cost_stop_armed = True
        tf_bars = bars_1m if is_1m else _bars_5m(bars_1m, full_5m)
        warmup = state.warmup_1m if is_1m else state.warmup_5m
        entry_floor = pos.entry_time if is_1m else pos.entry_time.floor("5min")

        lvl = pos.cand.setup.level
        if cfg.legacy_false_break_before_stop and _false_break_fired(cfg, tf_bars, lvl,
                                                                     entry_floor):
            _exit(state, now, float(bar["close"]), "false_break", cfg)
            return

        # stops and the fixed target fill intraday, so they are checked every minute
        sig = exits.check_stop(es, bar) or exits.check_target(bar, pos.plan.target, ec)
        if sig is not None:
            _exit(state, now, sig.price, sig.reason, cfg)
            return

        # Pattern failure is assessed only after executable stop/target orders.
        # A confirmation bar can cross the hard stop before it closes, so its
        # close must not replace the stop fill with a worse false-break price.
        if cfg.use_structural_exit_levels:
            support = es.structural_support
            if (
                support is not None
                and support.price > es.hard_stop
                and float(bar["close"]) < support.price
            ):
                _exit(state, now, float(bar["close"]), "support_break", cfg)
                return
        elif not cfg.legacy_false_break_before_stop and _false_break_fired(
            cfg, tf_bars, lvl, entry_floor
        ):
            _exit(state, now, float(bar["close"]), "false_break", cfg)
            return

        # trend signals are read off completed bars on the position's timeframe
        if len(tf_bars) and tf_bars.index[-1] != es.last_tf_seen:
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
    # `entry_deadline` is the 14:30 cutoff, tightened to the guide's peak-hours
    # end when that rule is on.
    if state.pending is not None or t >= cfg.entry_deadline or t >= cfg.eod_close:
        return
    # A reclaim is evaluated before the one-trade-per-day guard.  It can only
    # exist after a trade has exited as a false break, and is consumed the
    # instant its one allowed future-only buy-stop is armed.
    # Under multi-entry (one_trade_per_day=False) the two paths do not fight:
    # the reclaim governs the first re-entry after a false break because it is
    # the stricter rule (the lost level must be closed back through), and once
    # it is consumed or refused `false_break_reclaim_attempted` hands every
    # later re-entry back to the ordinary confirmation path below.
    if cfg.allow_false_break_reentry and state.false_break_reclaim is not None:
        tf5 = _bars_5m(bars_1m, full_5m)
        _false_break_reclaim_confirmation(state, bars_1m, tf5, cfg, catalyst)
        return
    if cfg.one_trade_per_day and state.traded_today:
        return
    if cfg.first_candidate_only and state.candidate_seen:
        return
    if state.resistance_refused:
        return
    chg = day_change_pct(bars_1m, state.prev_close)
    rv = rvol_now(bars_1m, state.cum_vol_profile)

    at_5m_close = now.minute % 5 == 4
    empty = pd.DataFrame(columns=bars_1m.columns)
    tf5 = _bars_5m(bars_1m, full_5m)

    if cfg.use_attention_entries:
        if rv is None:
            return
        guide = _guide_for(state, cfg)
        warm5 = state.warmup_5m if cfg.warm_context else None
        if not state.attention:
            if chg < cfg.attention_day_chg_min or rv < cfg.attention_rvol_min:
                return
            context_ok, _ = _attention_context(tf5, warm5)
            reason, tags, matches = _promotion_reason(tf5, bars_1m)
            if not context_ok or reason is None:
                return
            state.attention = True
            state.attention_since = now
            state.attention_patterns = matches
            state.attention_evidence = {
                "bar_start": now.isoformat(),
                "observed_at": (now + pd.Timedelta(minutes=1)).isoformat(),
                "day_chg_pct": chg, "rvol": rv,
                "minimum_day_chg_pct": cfg.attention_day_chg_min,
                "minimum_rvol": cfg.attention_rvol_min, "reason": reason,
                "pattern_matches": matches, "pattern_rules_version": PATTERN_RULES_VERSION,
            }
            state.attention_events.append(AttentionEvent(
                symbol=state.symbol, time=now, day_chg_pct=chg, rvol=rv,
                reason=reason, candle_tags=tuple(tags),
                evidence=dict(state.attention_evidence),
            ))
            return  # promotion is observation, never an entry on the same candle

        context_ok, context_reason = _attention_context(tf5, warm5)
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
            state.attention_evidence = {}
            return
        if cfg.require_resistance_breakout:
            setup, confirmation_reason = _resistance_aware_attention_confirmation(
                bars_1m, cfg.attention_confirm_vol_ratio, state.prev_day,
                cfg.resistance_veto_v2, guide, cfg.volume_shelf_levels,
                cfg.require_rising_price_volume,
            )
        else:
            setup, confirmation_reason = _attention_confirmation(
                bars_1m, cfg.attention_confirm_vol_ratio, guide,
                cfg.require_rising_price_volume,
            )
        if setup is None:
            # Under v2 a headroom refusal ends the day. Leaving it open lets a
            # later, worse confirmation take the slot the refused setup would
            # have used: those replacement entries were measured at -0.0072%
            # against +0.0362% for simply standing aside.
            if cfg.resistance_veto_v2 and confirmation_reason in _RESISTANCE_WAIT_REASONS:
                state.resistance_refused = True
            state.rejections.append(Rejection(
                symbol=state.symbol,
                time=now,
                reason=confirmation_reason,
                setup=ATTENTION_SETUP,
                trigger=float(bar["high"]),
                observed_price=float(bar["close"]),
                evidence=(volume_confirmation_evidence(bars_1m)
                          if confirmation_reason.startswith("attention_price_volume_") else {}),
            ))
            return
        ordinal = pullback_ordinal(tf5) if len(tf5) else None
        ord_ok, ord_reason = _pullback_ordinal_gate(ordinal, cfg)
        if not ord_ok:
            # Logged as a rejection rather than a dropped candidate so the
            # first-and-second-pullback rule's live refusal rate is measurable.
            state.rejections.append(Rejection(
                symbol=state.symbol,
                time=now,
                reason=ord_reason,
                setup=setup.name,
                trigger=setup.trigger,
                observed_price=float(bar["close"]),
            ))
            return
        cat, ev = catalyst(state.symbol, now)
        tags = candle_tags(bars_1m)
        loc = location.measure(tf5, setup.trigger, state.prev_day)
        dround, rh = loc.dist_to_round_pct, loc.round_head_pct
        res_head, sup_drop = loc.resist_head_pct, loc.support_drop_pct
        cand = Candidate(
            symbol=state.symbol, time=now, setup=setup, day_chg_pct=chg, rvol=rv,
            catalyst=cat, event_type=ev, candle_tags=tags,
            pattern_matches=_unique_pattern_documents(
                state.attention_patterns,
                [match.document() for match in completed_pattern_matches(tf5, "5m")],
                [match.document() for match in completed_pattern_matches(bars_1m, "1m")],
            ),
            entry_evidence=_entry_evidence(state, bars_1m, tf5, cfg),
            prev_day_gainer=state.prev_day_gainer,
            pullback_ord=ordinal,
            quality_reason="ok",
            atr_pct=state.daily_atr_pct,
            macd_hist=_macd_hist_now(tf5, state.warmup_5m),
            dist_to_round_pct=dround, round_head_pct=rh,
            resist_head_pct=res_head, support_drop_pct=sup_drop,
            level_anchor_px=loc.anchor_px,
            resist_px=loc.resist_px, support_px=loc.support_px,
            resist_kind=loc.resist_kind, support_kind=loc.support_kind,
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
    loc = location.measure(tf5, setup.trigger, state.prev_day)
    dround, rh = loc.dist_to_round_pct, loc.round_head_pct
    res_head, sup_drop = loc.resist_head_pct, loc.support_drop_pct
    macd_hist = _macd_hist_now(tf5, state.warmup_5m)
    m_ok, m_reason = _max_move_gate(setup, macd_hist, dround, res_head, sup_drop, cfg)
    ordinal = pullback_ordinal(tf5) if len(tf5) else None
    p_ok, p_reason = _playbook_gate(setup, ordinal, cfg)
    o_ok, o_reason = _one_minute_gate(bars_1m, setup, cfg)
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
        level_anchor_px=loc.anchor_px,
        resist_px=loc.resist_px, support_px=loc.support_px,
        resist_kind=loc.resist_kind, support_kind=loc.support_kind,
    )
    state.candidates.append(cand)   # refused setups are logged too, then dropped
    if not q_ok or not m_ok or not p_ok or not o_ok:
        if q_ok:
            cand.quality_reason = (
                m_reason if not m_ok else p_reason if not p_ok else o_reason
            )
        return
    armed_tf = bars_1m if _is_one_minute_setup(setup.name) else tf5
    state.pending = Pending(
        cand, trigger_reached=True,
        armed_high=float(armed_tf["high"].iloc[-1]) if len(armed_tf) else setup.trigger,
    )


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
