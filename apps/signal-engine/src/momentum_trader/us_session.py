"""One US paper-trading session, end to end. The Fargate entrypoint.

    python -m src.momentum_trader.us_session            # the scheduled task
    python -m src.momentum_trader.us_session --once     # one cycle now, then exit
    python -m src.momentum_trader.us_session --dry-run  # no Mongo, no Telegram

What a session does
-------------------
09:20 ET  wake (EventBridge Scheduler, America/New_York — so DST cannot shift it)
          exit at once on a US holiday
09:31 →   every minute, 22 s after each 1-minute bar closes (REST settle;
          ~5 s with MT_US_STREAM_BARS=true and a healthy stream):
            1. screen: the five criteria (us_universe) over Yahoo's movers
            2. a stock that newly passes is DISCOVERED: its prior 29 days of
               1-minute bars build the volume profile and warm the indicators
            3. every watched stock is stepped through the SAME engine and the
               SAME rules as the deployed NSE arm (`warrior_strict`) — only the
               market profile (clock, costs, sizing) differs
          between bars, armed entries are checked against every new trade
          from Yahoo's push stream (`yahoo_stream`), ~1-2 s after it prints;
          if the stream is down, against a REST quote every ~10 s
15:10     entry cutoff: no new entries after this. (The guide's 10:00
          peak-hours deadline applies only with MT_US_PEAK_HOURS_ONLY=true.)
15:54     the engine's own end-of-day exit; 15:56 wall-clock sweep backstop
16:20     exit

Two rules that exist to stop FAKE trades
----------------------------------------
* A stock discovered at 10:12 starts from its NEXT bar. Its earlier bars are
  indicator history only. Replaying them would let the engine enter at 09:50 a
  stock the screen had not yet found — a fill that could never have happened.
* An entry fills only from a price observed strictly after the decision,
  exactly like the NSE quote path, and only from a trade printed after it. The
  stream is conflated (~1 message a second at most), so it can still MISS a
  spike through the trigger that lived under a second; it cannot invent one.

Short side (MT_US_ENABLE_SHORTS, default off)
---------------------------------------------
A second screen on the same feed finds LOSERS (down >= MT_US_SHORT_DAY_CHG_MIN,
default 4%) and each is stepped through a `short_side.ShortBook` - the same
engine on the reflected tape. Positions, the day's guardrails and the position
cap are shared with the long side, and a symbol never holds both. Two US-only
rules: SEC Rule 201 (SSR) refuses a short entry once the stock has traded 10%
below the prior close (or carried SSR from yesterday), and every paper short
ASSUMES a locate (`locate_verified: False` on the position).

Same strategy, different market
-------------------------------
The engine config is DERIVED from the NSE `_strategy_config` for `MT_STRATEGY`
and then given the US profile, rather than written out again here. A copy would
drift the first time the NSE checklist changed, and the US arm would quietly be
testing a different strategy from the one it is compared against.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

import pandas as pd

from src.config import Settings
from src.news_trader import telegram

from .alerts import (
    US_TAG,
    attention_message,
    entry_message,
    eod_message,
    exit_message,
    watchlist_message,
)
from .discipline import DayDiscipline, DisciplineConfig
from .engine import (
    FILL_FUTURE_TRIGGER,
    ClosedTrade,
    DayState,
    EngineConfig,
    Position,
    Rejection,
    build_cum_volume_profile,
    fill_pending_quote,
    force_close,
    resample_5m,
    step,
)
from .exits import MODE_FIXED
from .market import US
from .short_side import ShortBook, ShortEvents, ssr_carried_from
from .us_ledger import USPaperLedger
from .us_scanner import Feed, USScanner, session_times
from .us_screener import USScreenRow
from .us_universe import USUniverseConfig

logger = logging.getLogger(__name__)

PROFILE_DAYS = 20           # same as the NSE scanner
WARMUP_SESSIONS = 5         # same as the NSE scanner
NO_DATA_GRACE = (9, 45)     # a real session has printed SPY by now
REFERENCE_SYMBOL = "SPY"    # the "is the market actually open?" canary


class TradingFeed(Feed, Protocol):
    """What a session needs beyond the screen."""

    settle_seconds: int
    prev_close_of: dict[str, float]
    exchange_of: dict[str, str]
    quote_source: dict[str, str]

    def history_1m(self, symbol: str, now: pd.Timestamp, days: int = 29) -> pd.DataFrame: ...
    def daily(self, symbol: str, now: pd.Timestamp) -> pd.DataFrame: ...
    def last_price(self, symbol: str, now: pd.Timestamp) -> float | None: ...
    def fresh_prices(self, symbol: str, now: pd.Timestamp) -> list[tuple[pd.Timestamp, float]]: ...
    def watch(self, symbols: list[str]) -> None: ...
    def live_bars(self, symbol: str, now: pd.Timestamp) -> pd.DataFrame: ...


def build_engine_config(settings: Settings, session_date: date,
                        ucfg: USUniverseConfig) -> EngineConfig:
    """The deployed NSE strategy, moved onto the US profile.

    Only four kinds of value change, and each is a market fact, not a tuning:
    the market profile (costs + sizing), the clock (cutoff, EOD, and the peak
    window — off by default, see `mt_us_peak_hours_only` — which shortens on
    early-close days), the money scale (dollars), and
    the attention day-change floor, which becomes criterion 2's +10% instead of
    NSE's +1.5%. Everything the checklist says is inherited unchanged.
    """
    from .scanner import _apply_env_overrides, _parse_hhmm, _strategy_config

    base = _apply_env_overrides(_strategy_config(settings), settings)
    cutoff, eod_close, _ = session_times(session_date)
    return dataclasses.replace(
        base,
        market=US,
        risk_inr=settings.mt_us_risk_usd,              # dollars, see EngineConfig.market
        max_notional_inr=settings.mt_us_max_notional_usd,
        entry_cutoff=cutoff,
        eod_close=eod_close,
        peak_hours_only=settings.mt_us_peak_hours_only,
        peak_hours_end=min(_parse_hhmm(settings.mt_us_peak_hours_end), cutoff),
        attention_day_chg_min=ucfg.day_chg_min_pct,
        # Every name the engine sees has already passed 5x naive RVOL, which
        # implies a time-of-day RVOL of at least 5x, so NSE's 1.5x floor is
        # inherited unchanged and never binds.
        attention_rvol_min=settings.mt_attention_rvol_min,
        one_trade_per_day=settings.mt_one_trade_per_day,
    )


def _recent_sessions(hist: pd.DataFrame, sessions: int) -> pd.DataFrame | None:
    if hist.empty:
        return None
    days = sorted(set(hist.index.normalize()))[-sessions:]
    recent = hist[hist.index.normalize().isin(days)]
    return None if recent.empty else recent


@dataclass
class Watch:
    """A stock the screen has found today."""

    state: DayState
    discovered_at: pd.Timestamp
    last_bar: pd.Timestamp | None
    has_profile: bool
    first_passed_at: pd.Timestamp | None = None
    row: USScreenRow | None = None


@dataclass
class ShortWatch:
    """A stock the LOSERS screen has found today (short side)."""

    book: ShortBook
    discovered_at: pd.Timestamp
    last_bar: pd.Timestamp | None
    has_profile: bool
    first_passed_at: pd.Timestamp | None = None


@dataclass
class CycleReport:
    at: pd.Timestamp
    screened: int
    passed: int
    discovered: list[str] = field(default_factory=list)
    opened: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)


class USSession:
    def __init__(
        self,
        feed: TradingFeed,
        cfg: EngineConfig,
        ledger: USPaperLedger,
        *,
        ucfg: USUniverseConfig | None = None,
        max_positions: int = 10,
        discipline: DayDiscipline | None = None,
        notify: Callable[[str], None] = lambda _t: None,
        short_scanner: USScanner | None = None,
    ) -> None:
        if cfg.market is not US:
            raise ValueError("USSession needs an EngineConfig with market=US")
        if cfg.fill_mode != FILL_FUTURE_TRIGGER:
            raise ValueError("the US arm fills from observed quotes only (FILL_FUTURE_TRIGGER)")
        self.feed = feed
        self.cfg = cfg
        self.ucfg = ucfg or USUniverseConfig()
        self.ledger = ledger
        self.max_positions = max_positions
        self.notify = notify
        self.discipline = discipline or DayDiscipline(DisciplineConfig())
        self._full_risk = cfg.risk_inr
        self.scanner = USScanner(feed, self.ucfg, US)
        self.watch: dict[str, Watch] = {}
        self.bars: dict[str, pd.DataFrame] = {}
        self.rows: dict[str, USScreenRow] = {}
        self.first_seen: dict[str, pd.Timestamp] = {}
        # Last bar already persisted per watched name, so an unchanged series is
        # not rewritten every cycle.
        self._bars_saved: dict[str, pd.Timestamp] = {}
        self.closed_trades: list[ClosedTrade] = []
        # Day totals for the EOD message; the per-cycle lists on each watch's
        # state are cleared once written to the ledger.
        self.attention_count = 0
        self.candidate_count = 0
        self.rejection_reasons: list[str] = []
        # Short side: its own screen, its own watch list, and its own engine
        # config - identical to the long one except the attention floor, which
        # is the losers screen's (the engine sees the reflected, i.e. positive,
        # day change).
        self.short_scanner = short_scanner
        self.short_watch: dict[str, ShortWatch] = {}
        self.short_rows: dict[str, USScreenRow] = {}
        self.short_cfg = (dataclasses.replace(
            cfg, attention_day_chg_min=short_scanner.cfg.day_chg_min_pct)
            if short_scanner is not None else cfg)

    # ── helpers ─────────────────────────────────────────────────────────────
    def _open_count(self) -> int:
        longs = sum(1 for w in self.watch.values() if w.state.position is not None)
        return longs + sum(1 for w in self.short_watch.values() if w.book.has_position)

    def has_pending(self) -> bool:
        return (any(w.state.pending is not None for w in self.watch.values())
                or any(w.book.has_pending for w in self.short_watch.values()))

    def _sync_risk(self) -> None:
        self.cfg.risk_inr = self.discipline.risk_inr(self._full_risk)
        self.short_cfg.risk_inr = self.cfg.risk_inr

    def _short_block_reason(self, sym: str) -> str | None:
        """Why an armed short may not proceed right now, or None."""
        allowed, reason = self.discipline.can_trade()
        if not allowed:
            return f"halted:{reason}"
        if self._open_count() >= self.max_positions:
            return "max_positions"
        w = self.watch.get(sym)
        if w is not None and (w.state.position is not None or w.state.pending is not None):
            return "opposite_side_open"
        return None

    def _cancel_pending(self, st: DayState, when: pd.Timestamp, reason: str) -> None:
        pending = st.pending
        if pending is None:
            return
        st.rejections.append(Rejection(
            symbol=st.symbol, time=when, reason=reason,
            setup=pending.cand.setup.name, trigger=pending.cand.setup.trigger,
        ))
        st.pending = None

    # ── discovery ───────────────────────────────────────────────────────────
    def discover(self, symbol: str, now: pd.Timestamp) -> Watch | None:
        """Build a DayState the moment a stock first passes the screen."""
        prev_close = self.feed.prev_close_of.get(symbol)
        if not prev_close:
            logger.warning("%s passed the screen but has no previous close — skipped", symbol)
            return None
        # Stream it from now on: its fills, and its bars once a full minute
        # has been seen (the minute it is subscribed in is never complete).
        self.feed.watch([symbol])
        hist = self.feed.history_1m(symbol, now)
        profile = build_cum_volume_profile(hist, PROFILE_DAYS) if not hist.empty else None
        if profile is not None and profile.empty:
            profile = None
        warm_1m = _recent_sessions(hist, WARMUP_SESSIONS)
        prev_day = None
        daily = self.feed.daily(symbol, now)
        if not daily.empty:
            prev_day = {"high": float(daily["high"].iloc[-1]),
                        "low": float(daily["low"].iloc[-1]), "close": prev_close}
        st = DayState(
            symbol=symbol, prev_close=prev_close, cum_vol_profile=profile,
            prev_day=prev_day, warmup_1m=warm_1m,
            warmup_5m=resample_5m(warm_1m) if warm_1m is not None else None,
        )
        bars = self.feed.bars_1m(symbol, now)
        self.bars[symbol] = bars
        w = Watch(state=st, discovered_at=now, has_profile=profile is not None,
                  # Start from the NEXT bar: see the module docstring.
                  last_bar=bars.index[-1] if not bars.empty else None)
        self.watch[symbol] = w
        if profile is None:
            # The attention path refuses every entry without a profile. Say so
            # now rather than let the stock sit on the watchlist doing nothing.
            logger.warning("%s: no 1-minute history, so no volume profile — "
                           "it is watched but the engine cannot enter it", symbol)
        return w

    def discover_short(self, symbol: str, now: pd.Timestamp) -> ShortWatch | None:
        """Build a ShortBook the moment a stock first passes the losers screen."""
        prev_close = self.feed.prev_close_of.get(symbol)
        if not prev_close:
            logger.warning("%s passed the short screen but has no previous close", symbol)
            return None
        self.feed.watch([symbol])        # stream its prints, as `discover` does
        hist = self.feed.history_1m(symbol, now)
        profile = build_cum_volume_profile(hist, PROFILE_DAYS) if not hist.empty else None
        if profile is not None and profile.empty:
            profile = None
        daily = self.feed.daily(symbol, now)
        prev_day = None
        carried = False
        if not daily.empty:
            prev_day = {"high": float(daily["high"].iloc[-1]),
                        "low": float(daily["low"].iloc[-1]), "close": prev_close}
            if len(daily) >= 2:
                carried = ssr_carried_from(float(daily["low"].iloc[-1]),
                                           float(daily["close"].iloc[-2]))
        book = ShortBook(symbol, prev_close, profile, prev_day=prev_day,
                         warmup_1m=_recent_sessions(hist, WARMUP_SESSIONS),
                         ssr_rule=True, ssr_carried=carried)
        bars = self.feed.bars_1m(symbol, now)
        self.bars[symbol] = bars
        w = ShortWatch(book=book, discovered_at=now, has_profile=profile is not None,
                       last_bar=bars.index[-1] if not bars.empty else None)
        self.short_watch[symbol] = w
        return w

    def _cycle_shorts(self, now: pd.Timestamp, report: CycleReport,
                      entries: list[Position], closed: list[ClosedTrade]) -> ShortEvents:
        """The short twin of the long loop in `cycle`."""
        assert self.short_scanner is not None
        events = ShortEvents()
        open_syms = {s for s, w in self.short_watch.items() if w.book.has_position}
        result = self.short_scanner.step(now, open_syms)
        for row in result.summary.rows:
            self.short_rows[row.symbol] = row
        for sym in result.tradeable:
            if sym not in self.short_watch and self.discover_short(sym, now) is not None:
                report.discovered.append(sym)
            if sym in self.short_watch:
                sw = self.short_watch[sym]
                sw.first_passed_at = sw.first_passed_at or now

        def absorb(ev: ShortEvents) -> None:
            events.attention.extend(ev.attention)
            events.candidates.extend(ev.candidates)
            events.rejections.extend(ev.rejections)
            closed.extend(ev.closed)
            if ev.opened is not None:
                entries.append(ev.opened)

        for sym, w in self.short_watch.items():
            book = w.book
            bars = self.feed.bars_1m(sym, now)
            if not bars.empty:
                self.bars[sym] = bars
            may_arm = sym in result.tradeable
            if book.has_pending and not may_arm:
                rej = book.cancel_pending(now, result.blocked.get(sym, "left_screen"))
                if rej is not None:
                    events.rejections.append(rej)
            if w.last_bar is not None:
                new = bars[bars.index > w.last_bar]
            else:
                new = bars[bars.index > w.discovered_at - pd.Timedelta(seconds=60)]
            for ts in new.index:
                w.last_bar = ts
                if not book.has_position and not book.has_pending and not may_arm:
                    continue
                window = bars.loc[:ts]
                if book.pending_expired(now):
                    absorb(book.fill_quote(now, float(window["close"].iloc[-1]),
                                           self.short_cfg, window))
                had_pending = book.has_pending
                absorb(book.step(window, self.short_cfg, lambda _s, _t: (0, ""),
                                 allow_replay_fill=False))
                if not had_pending and book.has_pending:
                    book.stamp_decision(now, self.short_cfg.attention_pending_minutes)
                    blocked = self._short_block_reason(sym)
                    if blocked is not None:
                        rej = book.cancel_pending(now, blocked)
                        if rej is not None:
                            events.rejections.append(rej)
        return events

    # ── one minute ──────────────────────────────────────────────────────────
    def cycle(self, now: pd.Timestamp) -> CycleReport:
        open_syms = {s for s, w in self.watch.items() if w.state.position is not None}
        result = self.scanner.step(now, open_syms)
        report = CycleReport(now, result.summary.considered, result.summary.passed)

        for row in result.summary.rows:
            self.rows[row.symbol] = row
            self.first_seen.setdefault(row.symbol, now)
        for sym in result.tradeable:
            if sym not in self.watch and self.discover(sym, now) is not None:
                report.discovered.append(sym)
            if sym in self.watch:
                w = self.watch[sym]
                w.row = self.rows.get(sym)
                w.first_passed_at = w.first_passed_at or now

        entries: list[Position] = []
        closed: list[ClosedTrade] = []
        for sym, w in self.watch.items():
            st = w.state
            bars = self.feed.bars_1m(sym, now)
            if not bars.empty:
                self.bars[sym] = bars
            may_arm = sym in result.tradeable
            if st.pending is not None and not may_arm:
                self._cancel_pending(st, now, result.blocked.get(sym, "left_screen"))
            if w.last_bar is not None:
                new = bars[bars.index > w.last_bar]
            else:
                # Discovered while its bars could not be read: a bar is only
                # new if it CLOSED after discovery (start > discovered - 60s).
                new = bars[bars.index > w.discovered_at - pd.Timedelta(seconds=60)]
            n_x = len(st.closed)
            had_pos = st.position
            for ts in new.index:
                w.last_bar = ts
                if st.position is None and st.pending is None and not may_arm:
                    continue            # nothing to manage and not allowed to arm
                window = bars.loc[:ts]
                if (st.pending is not None and st.pending.expires_at is not None
                        and now > st.pending.expires_at):
                    fill_pending_quote(st, now, float(window["close"].iloc[-1]), self.cfg, window)
                had_pending = st.pending
                step(st, window, self.cfg, lambda _s, _t: (0, ""), allow_replay_fill=False)
                if had_pending is None and st.pending is not None:
                    # The decision happened now, on the wall clock, exactly as
                    # the NSE live path stamps it.
                    st.pending.decision_time = now
                    st.pending.expires_at = now + pd.Timedelta(
                        minutes=self.cfg.attention_pending_minutes)
                    self._guard(st, now)
                    if st.pending is not None and self._open_count() >= self.max_positions:
                        self._cancel_pending(st, now, "max_positions")
                    sw = self.short_watch.get(sym)
                    if st.pending is not None and sw is not None and (
                            sw.book.has_position or sw.book.has_pending):
                        self._cancel_pending(st, now, "opposite_side_open")
            if st.position is not None and had_pos is None:
                entries.append(st.position)
            closed.extend(st.closed[n_x:])

        short_events = (self._cycle_shorts(now, report, entries, closed)
                        if self.short_scanner is not None else None)
        closed.extend(self._eod_sweep(now))
        self._record(entries, closed, report, short_events)
        self._write_watchlist(now)
        return report

    def poll_pending(self, now: pd.Timestamp) -> list[str]:
        """Between bars: check every armed entry against the latest price."""
        opened: list[Position] = []
        for sym, w in self.watch.items():
            st = w.state
            if st.pending is None:
                continue
            if self.scanner.halts.blocks_entry(sym, now) is not None:
                continue
            if self._guard(st, now):
                continue
            had_pos = st.position
            bars = self.bars.get(sym, pd.DataFrame())
            # Every print since the last look, in order, at its own time: the
            # first one through the trigger is the fill, as a resting stop
            # would have it. Nothing between prints is invented.
            for when, price in self.feed.fresh_prices(sym, now):
                fill_pending_quote(st, when, price, self.cfg, bars)
                if st.pending is None:
                    break
            if st.position is not None and had_pos is None:
                opened.append(st.position)
        short_events = ShortEvents()
        for sym, sw in self.short_watch.items():
            book = sw.book
            if not book.has_pending or self.short_scanner is None:
                continue
            if self.short_scanner.halts.blocks_entry(sym, now) is not None:
                continue
            blocked = self._short_block_reason(sym)
            if blocked is not None:
                rej = book.cancel_pending(now, blocked)
                if rej is not None:
                    short_events.rejections.append(rej)
                continue
            bars = self.bars.get(sym, pd.DataFrame())
            # Same print-by-print rule as the long side: the first trade at or
            # below the sell-stop fills it.
            for when, price in self.feed.fresh_prices(sym, now):
                ev = book.fill_quote(when, price, self.short_cfg, bars)
                short_events.rejections.extend(ev.rejections)
                if ev.opened is not None:
                    opened.append(ev.opened)
                if not book.has_pending:
                    break
        report = CycleReport(now, 0, 0)
        self._record(opened, [], report, short_events)
        return report.opened

    def _guard(self, st: DayState, when: pd.Timestamp) -> bool:
        allowed, reason = self.discipline.can_trade()
        if allowed or st.pending is None:
            return False
        self._cancel_pending(st, when, f"halted:{reason}")
        return True

    def _eod_sweep(self, now: pd.Timestamp) -> list[ClosedTrade]:
        _, _, sweep = session_times(now.date())
        if (now.hour, now.minute) < sweep:
            return []
        swept: list[ClosedTrade] = []
        for sym, w in self.watch.items():
            if w.state.position is None:
                continue
            bars = self.bars.get(sym, pd.DataFrame())
            if bars.empty:
                logger.error("eod_sweep: %s open with no price to mark — reconcile by hand", sym)
                continue
            trade = force_close(w.state, now, float(bars["close"].iloc[-1]), self.cfg)
            if trade is not None:
                logger.warning("eod_sweep closed %s at %.2f — it stopped printing before "
                               "the close", sym, trade.exit)
                swept.append(trade)
        for sym, sw in self.short_watch.items():
            if not sw.book.has_position:
                continue
            bars = self.bars.get(sym, pd.DataFrame())
            if bars.empty:
                logger.error("eod_sweep: SHORT %s open with no price to mark — "
                             "reconcile by hand", sym)
                continue
            short_trade = sw.book.force_close(now, float(bars["close"].iloc[-1]), self.short_cfg)
            if short_trade is not None:
                logger.warning("eod_sweep covered short %s at %.2f", sym, short_trade.exit)
                swept.append(short_trade)
        return swept

    # ── output ──────────────────────────────────────────────────────────────
    def _record(self, entries: list[Position], closed: list[ClosedTrade],
                report: CycleReport, short_events: ShortEvents | None = None) -> None:
        for t in sorted(closed, key=lambda x: x.exit_time):
            if self.discipline.cfg.enabled:
                was = self.discipline.halted
                self.discipline.record(t.net_inr)
                self._sync_risk()
                if self.discipline.halted and not was:
                    self.notify(f"🛑 HALTED <b>{self.discipline.halted_reason}</b>")
        for sym in report.discovered:
            # A name the LOSERS screen found is in short_watch, not watch; the
            # long screen may still hold a (rejected) row for it.
            short = sym not in self.watch and sym in self.short_watch
            row = self.short_rows.get(sym) if short else self.rows.get(sym)
            if row is not None:
                can_enter = (self.short_watch[sym].has_profile if short
                             else self.watch[sym].has_profile)
                self.notify(watchlist_message(
                    sym, price=row.price, day_chg_pct=row.day_chg_pct, rvol=row.rvol,
                    float_shares=row.float_shares, can_enter=can_enter,
                    side="short" if short else "long"))
        for w in self.watch.values():
            st = w.state
            for ev in st.attention_events:
                self.ledger.attention(ev)
                self.notify(attention_message(ev))
            for c in st.candidates:
                self.ledger.candidate(c)
            for r in st.rejections:
                self.ledger.rejected(r)
            self.attention_count += len(st.attention_events)
            self.candidate_count += len(st.candidates)
            self.rejection_reasons.extend(r.reason for r in st.rejections)
            st.attention_events.clear()
            st.candidates.clear()
            st.rejections.clear()
        if short_events is not None:
            # Short books return their events instead of accumulating them on a
            # state, so there is nothing to clear afterwards.
            for ev in short_events.attention:
                self.ledger.attention(ev)
                self.notify(attention_message(ev))
            for c in short_events.candidates:
                self.ledger.candidate(c)
            for r in short_events.rejections:
                self.ledger.rejected(r)
            self.attention_count += len(short_events.attention)
            self.candidate_count += len(short_events.candidates)
            self.rejection_reasons.extend(r.reason for r in short_events.rejections)
        for p in entries:
            sym = p.cand.symbol
            row = self.short_rows.get(sym) if p.cand.side == "short" else self.rows.get(sym)
            self.ledger.opened(p, row, self.feed.exchange_of.get(sym, ""),
                               self.feed.quote_source.get(sym, ""))
            report.opened.append(sym)
            self.notify(entry_message(p, fixed_exit=self.cfg.exit_mode == MODE_FIXED, cur="$"))
        for t in closed:
            sym = t.cand.symbol
            halts = (self.short_scanner.halts
                     if t.side == "short" and self.short_scanner is not None
                     else self.scanner.halts)
            self.ledger.closed(t, self.bars.get(sym),
                               held_through_halt=sym in halts.held_through_halt)
            self.closed_trades.append(t)
            report.closed.append(sym)
            self.notify(exit_message(t, cur="$"))

    def _write_watchlist(self, now: pd.Timestamp) -> None:
        """The DAY's funnel: every stock seen, its latest verdict, and whether
        it EVER passed — a stock that qualified at 09:40 and faded by 11:00 is
        still a stock the screen found."""
        doc = self.scanner.watchlist_document(now)
        if doc is None:
            return
        for sym, bars in self.bars.items():
            watched = sym in self.watch or sym in self.short_watch
            if watched and not bars.empty and self._bars_saved.get(sym) != bars.index[-1]:
                self.ledger.watch_bars(doc["date"], sym, bars)
                self._bars_saved[sym] = bars.index[-1]
                live = self.feed.live_bars(sym, now)
                if not live.empty:
                    self.ledger.stream_bars(doc["date"], sym, live)
        names = []
        for sym, row in self.rows.items():
            w = self.watch.get(sym)
            names.append({
                "symbol": sym, "passed": w is not None, "reason": row.reason,
                "screen_complete": row.screen_complete, "price": row.price,
                "day_chg_pct": row.day_chg_pct, "rvol": row.rvol,
                "float_shares": row.float_shares, "flags": row.flags,
                "observed_at": row.observed_at,
                "first_seen": self.first_seen[sym].isoformat(),
                "first_passed_at": (w.first_passed_at.isoformat()
                                    if w and w.first_passed_at else None),
                "has_volume_profile": w.has_profile if w else None,
            })
        never = [n for n in names if not n["passed"]]
        rejected: dict[str, int] = {}
        for n in never:
            rejected[n["reason"]] = rejected.get(n["reason"], 0) + 1
        doc.update(
            considered=len(names), passed=len(self.watch),
            complete=sum(1 for n in names if n["passed"] and n["screen_complete"]),
            rejected_by=rejected, names=names, updated_at=now.isoformat(),
            open_positions=self._open_count(), closed_today=len(self.closed_trades),
            net_usd_today=round(sum(t.net_inr for t in self.closed_trades), 2),
        )
        if self.short_scanner is not None:
            # The losers screen, kept apart so the long funnel's counts above
            # keep meaning what they always meant.
            doc["short"] = {
                "day_chg_max_pct": -self.short_scanner.cfg.day_chg_min_pct,
                "considered": len(self.short_rows), "passed": len(self.short_watch),
                "names": [{
                    "symbol": sym, "side": "short", "passed": sym in self.short_watch,
                    "reason": row.reason, "price": row.price,
                    "day_chg_pct": row.day_chg_pct, "rvol": row.rvol,
                    "float_shares": row.float_shares,
                    "ssr_carried": (self.short_watch[sym].book.ssr_carried
                                    if sym in self.short_watch else None),
                } for sym, row in self.short_rows.items()],
            }
        self.ledger.watchlist(doc)

    def summary(self) -> str:
        net = sum(t.net_inr for t in self.closed_trades)
        wins = sum(1 for t in self.closed_trades if t.net_inr > 0)
        return (f"{len(self.rows)} screened, {len(self.watch)} passed, "
                f"{len(self.closed_trades)} trades ({wins} won), net ${net:,.2f}")

    def eod_message(self, day: date) -> str:
        return eod_message(
            day=day, trades=self.closed_trades, attention=self.attention_count,
            candidates=self.candidate_count, rejection_reasons=self.rejection_reasons,
            cur="$",
            extra=[f"🧮 Screen: {len(self.rows)} screened → {len(self.watch)} passed"],
        )


# ── the scheduled task ──────────────────────────────────────────────────────
def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz=US.timezone)


def _at(day: pd.Timestamp, hhmm: tuple[int, int]) -> pd.Timestamp:
    return day.normalize() + pd.Timedelta(hours=hhmm[0], minutes=hhmm[1])


def _sleep_until(target: pd.Timestamp, clock: Callable[[], pd.Timestamp]) -> None:
    while (left := (target - clock()).total_seconds()) > 0:
        _time.sleep(min(left, 30.0))


def run(settings: Settings, *, once: bool = False, dry_run: bool = False,
        clock: Callable[[], pd.Timestamp] = _now) -> int:
    from .yahoo_feed import YahooFeed

    def tg(text: str) -> None:
        if not dry_run:
            telegram._send(settings.telegram_bot_token, settings.telegram_chat_id,
                           f"{US_TAG} {text}")

    now = clock()
    today = now.date()
    if not US.is_trading_day(today) and not settings.mt_us_bypass_market_hours:
        logger.info("%s is not a US trading day — exiting", today)
        tg(f"{today} is a US market holiday — no session")
        return 0

    db: Any = None
    if not dry_run:
        try:
            from pymongo import MongoClient
            db = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)[
                settings.mongodb_db_name]
            db.list_collection_names()
        except Exception as exc:  # noqa: BLE001
            logger.error("Mongo unavailable (%s) — nothing will be recorded", exc)
            tg(f"⚠️ Mongo unavailable ({exc}) — session runs unrecorded")
            db = None

    ucfg = USUniverseConfig()
    stream = None
    if settings.mt_us_stream:
        from .yahoo_stream import YahooStream

        stream = YahooStream(settle_seconds=settings.mt_us_stream_settle_seconds)
        stream.start()
    feed = YahooFeed(cache_dir=_cache_dir(settings), cfg=ucfg,
                     block_delayed=settings.mt_us_block_delayed_quotes,
                     stream=stream, stream_bars=settings.mt_us_stream_bars,
                     rest_poll_seconds=settings.mt_us_quote_poll_seconds)
    cfg = build_engine_config(settings, today, ucfg)
    short_scanner = None
    if settings.mt_us_enable_shorts:
        short_ucfg = USUniverseConfig(side="short",
                                      day_chg_min_pct=settings.mt_us_short_day_chg_min)
        short_scanner = USScanner(feed, short_ucfg, US,
                                  snapshot_fn=lambda t: feed.snapshot(t, short_ucfg))
    session = USSession(
        feed, cfg, USPaperLedger(db, strategy=f"us_{settings.mt_strategy}"),
        ucfg=ucfg, max_positions=settings.mt_us_max_positions,
        discipline=DayDiscipline(DisciplineConfig(
            enabled=settings.mt_discipline, giveback_halt=settings.mt_giveback_halt)),
        notify=tg, short_scanner=short_scanner,
    )
    session._sync_risk()

    try:
        return _loop(settings, session, feed, stream, cfg, today, once, tg, clock)
    finally:
        if stream is not None:
            stream.stop()


def _loop(settings: Settings, session: USSession, feed: Any, stream: Any, cfg: EngineConfig,
          today: date, once: bool, tg: Callable[[str], None],
          clock: Callable[[], pd.Timestamp]) -> int:
    if once:
        report = session.cycle(clock())
        print(f"{report.at:%H:%M:%S} ET  screened {report.screened}, passed {report.passed}, "
              f"discovered {report.discovered}")
        print(session.summary())
        return 0

    day = pd.Timestamp(today, tz=US.timezone)
    start = _at(day, (US.session_start.hour, US.session_start.minute)) + pd.Timedelta(minutes=1)
    _, _, sweep = session_times(today)
    finish = _at(day, sweep) + pd.Timedelta(minutes=2)
    exit_at = _at(day, US.process_end)
    tg(f"🔔 ready {today}: entries {US.session_start:%H:%M}–{cfg.entry_deadline:%H:%M} ET, "
       f"risk ${settings.mt_us_risk_usd:.0f}/trade"
       + (" · early close" if session_times(today)[1] != US.eod_close else ""))
    if not settings.mt_us_bypass_market_hours:
        _sleep_until(start, clock)

    grace = _at(day, NO_DATA_GRACE)
    grace_checked = False
    failures = 0
    while clock() < finish:
        now = clock()
        try:
            session.cycle(now)
            failures = 0
        except Exception as exc:  # noqa: BLE001
            # One bad Yahoo response must not end the session: open positions
            # still need their stops and the end-of-day exit. Log and go on.
            failures += 1
            logger.exception("cycle failed at %s (%d in a row)", now, failures)
            if failures in (1, 5, 30):
                tg(f"⚠️ cycle failed {failures}× in a row: {type(exc).__name__}: {exc}")
        if not grace_checked and now >= grace:
            grace_checked = True
            if feed.bars_1m(REFERENCE_SYMBOL, now).empty:
                # Unscheduled closures are in no calendar. On any real session
                # SPY has printed by 09:45; if it has not, the market is shut.
                logger.error("no %s bars by %s — market looks shut; exiting",
                             REFERENCE_SYMBOL, now)
                tg(f"🟡 no {REFERENCE_SYMBOL} bars by {now:%H:%M} ET — "
                   "market looks closed, exiting")
                return 0
        # The next decision: as soon as the minute's bar can be read — 20 s
        # after it closes from REST, ~3 s from the stream if stream bars are on.
        next_bar = now.floor("1min") + pd.Timedelta(seconds=60 + feed.ready_seconds() + 2)
        while (t := clock()) < next_bar:
            armed = session.has_pending()     # a long OR a short armed
            if armed:
                try:
                    session.poll_pending(t)
                except Exception:  # noqa: BLE001
                    logger.exception("pending poll failed at %s", t)
            left = max(0.0, (next_bar - clock()).total_seconds())
            if armed and stream is not None and stream.healthy():
                # Wake on the next print (prices come from memory, not the
                # network), so an armed entry sees every trade as it lands.
                stream.wait_for_trade(min(0.5, left))
            else:
                _time.sleep(min(settings.mt_us_quote_poll_seconds, left))

    tg(session.eod_message(today))
    if clock() < exit_at:
        _sleep_until(exit_at, clock)
    return 0


def _cache_dir(settings: Settings) -> Any:
    from pathlib import Path

    path = Path(settings.mt_us_cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="US momentum paper session")
    ap.add_argument("--once", action="store_true", help="one cycle now, then exit")
    ap.add_argument("--dry-run", action="store_true", help="no Mongo writes, no Telegram")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logger.info("US session start %s", datetime.now().isoformat(timespec="seconds"))
    # Same bootstrap as the NSE entrypoint: SSM secrets into the environment,
    # THEN Settings(), so MONGODB_URI and the Telegram keys are present.
    if os.getenv("AWS_SECRETS_ENABLED", "").lower() == "true":
        from src.secrets import bootstrap_secrets
        bootstrap_secrets(stage=os.getenv("STAGE", "dev"))
    return run(Settings(), once=args.once, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
