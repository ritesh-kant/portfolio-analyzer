"""US session loop — the NSE scanner's counterpart, sharing none of its clock.

`scanner.py` is 873 lines and NSE-coupled at two dozen points (IST, Upstox
instrument keys, circuit bands, the 15:15 MIS square-off). Parameterising it was
rejected: it runs live every weekday, and the change would have been a rewrite
of the file with the largest blast radius in the repo for no benefit to the arm
it already serves. This is a separate loop over the same shared, market-neutral
pieces (`setups`, `levels`, `indicators`, `exits`).

What is deliberately NOT here
-----------------------------
A data feed. `Feed` is a Protocol, so this loop and its halt handling are
testable today and an IBKR client drops in later without touching this file.
Nothing in this module reaches the network.

Two US-only problems the NSE loop never had to solve
----------------------------------------------------
**Halts.** NSE circuit bands LOCK a price: a band-locked name has no sellers,
so it is excluded upstream and never becomes a position. US LULD pauses trading
for five minutes and then RESUMES, so a halt happens to a position you already
hold. `HaltState` treats it as a state rather than an exclusion. This matters
more here than anywhere else in the codebase, because the screen selects for it:
"up 10%+, under 10M float, 5x volume" is close to a definition of a stock that
will trip a LULD band.

**Early closes.** Three US sessions a year end at 13:00 ET. Holding to 16:00 on
one of them marks the position at a price that never traded.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import Protocol

import pandas as pd

from .market import US, MarketProfile
from .us_calendar import is_early_close
from .us_screener import ScreenSummary, USQuote, screen, watchlist_doc
from .us_universe import USNameFacts, USUniverseConfig

logger = logging.getLogger(__name__)

EARLY_CLOSE_EOD = time(12, 54)      # bar starting 12:54 closes at 12:55
EARLY_CLOSE_SWEEP = (12, 56)
EARLY_CLOSE_CUTOFF = time(12, 10)   # same 44-minute margin as a full session

HALT_COOLDOWN = timedelta(minutes=5)
"""How long after a resume before a new entry may be armed.

A LULD pause reopens through an auction. The first prints after it are a price
discovery process, not a breakout, and a buy-stop resting across the auction
fills at whatever that auction clears at — which is precisely the gap the stop
was supposed to protect against. Five minutes is the length of the pause itself:
chosen for symmetry and stated as chosen, not measured.
"""


class Feed(Protocol):
    """Everything the loop needs from a market-data provider.

    Written as the smallest surface that supports the screen, not as a mirror of
    any vendor's API, so the IBKR / replay / fixture implementations are all
    honest about what they do and do not supply.
    """

    def snapshot(self, now: pd.Timestamp) -> list[USQuote]:
        """Every name the provider considers a mover right now."""

    def bars_1m(self, symbol: str, now: pd.Timestamp) -> pd.DataFrame:
        """Closed 1-minute bars for the session so far."""

    def facts(self, symbols: list[str]) -> dict[str, USNameFacts]:
        """Reference data. A provider with no float figure returns the name with
        `float_shares=None` rather than omitting it — the screen must be able to
        tell 'too many shares' from 'nobody told me'."""

    def halted(self, now: pd.Timestamp) -> set[str]:
        """Symbols currently in a trading pause."""


@dataclass
class HaltState:
    """Which symbols may not be entered, and why.

    Kept out of the engine on purpose: an engine that has never seen a halt
    should not grow a halt branch that only one market can exercise.
    """

    halted: set[str] = field(default_factory=set)
    resumed_at: dict[str, pd.Timestamp] = field(default_factory=dict)
    halt_count: dict[str, int] = field(default_factory=dict)
    held_through_halt: set[str] = field(default_factory=set)
    """Positions that were open when their symbol halted. Their stop was not
    honoured while trading was paused and the reopen can gap straight through
    it, so the realised loss on these is unbounded by the plan. Flagged so the
    forward log can be read with and without them instead of pooling a risk the
    strategy never actually took."""

    def update(
        self, now: pd.Timestamp, currently_halted: set[str], open_symbols: set[str]
    ) -> tuple[set[str], set[str]]:
        """Fold in this cycle's halt set. Returns (newly halted, newly resumed)."""
        newly_halted = currently_halted - self.halted
        newly_resumed = self.halted - currently_halted

        for symbol in newly_halted:
            self.halt_count[symbol] = self.halt_count.get(symbol, 0) + 1
            if symbol in open_symbols:
                self.held_through_halt.add(symbol)
                logger.warning(
                    "%s halted while a position was open — the stop is not "
                    "protecting it and the reopen may gap through it", symbol
                )
        for symbol in newly_resumed:
            self.resumed_at[symbol] = now

        self.halted = set(currently_halted)
        return newly_halted, newly_resumed

    def blocks_entry(self, symbol: str, now: pd.Timestamp) -> str | None:
        """Reason this symbol may not be entered, or None."""
        if symbol in self.halted:
            return "halted"
        resumed = self.resumed_at.get(symbol)
        if resumed is not None and now - resumed < HALT_COOLDOWN:
            return "halt_cooldown"
        return None


def session_times(d: date, profile: MarketProfile = US) -> tuple[time, time, tuple[int, int]]:
    """(entry cutoff, eod close, eod sweep) for this date.

    Three sessions a year end at 13:00 ET. Returning the shortened set rather
    than the profile's own is the difference between exiting at the close and
    marking a position at a price that never traded.
    """
    if is_early_close(d):
        return EARLY_CLOSE_CUTOFF, EARLY_CLOSE_EOD, EARLY_CLOSE_SWEEP
    return profile.entry_cutoff, profile.eod_close, profile.eod_sweep


@dataclass
class CycleResult:
    """What one pass of the loop decided, and why."""

    at: pd.Timestamp
    summary: ScreenSummary
    tradeable: list[str]
    blocked: dict[str, str]
    """symbol -> reason it passed the screen but may not be entered."""

    session_open: bool
    eod: bool


class USScanner:
    """One US session. Owns the clock, the screen and the halt state."""

    def __init__(
        self,
        feed: Feed,
        cfg: USUniverseConfig | None = None,
        profile: MarketProfile = US,
        snapshot_fn: Callable[[pd.Timestamp], list[USQuote]] | None = None,
    ) -> None:
        self.feed = feed
        self.cfg = cfg or USUniverseConfig()
        # A second screen on the same feed (the short arm's losers) supplies
        # its own snapshot; the default is the feed's own screen.
        self.snapshot_fn = snapshot_fn or feed.snapshot
        self.profile = profile
        self.halts = HaltState()
        self.last_summary: ScreenSummary | None = None

    def in_session(self, now: pd.Timestamp) -> bool:
        """Regular session only. `profile.trades_premarket` is False for v1, so
        the guide's 07:00 pre-market window is recorded but not acted on."""
        if not self.profile.is_trading_day(now.date()):
            return False
        t = now.time()
        _, eod_close, _ = session_times(now.date(), self.profile)
        start = self.profile.session_start
        if self.profile.trades_premarket and self.profile.peak_hours_start:
            start = self.profile.peak_hours_start
        return bool(start <= t <= eod_close)

    def step(self, now: pd.Timestamp, open_symbols: set[str] | None = None) -> CycleResult:
        """Screen once and decide what is tradeable.

        `now` is passed in rather than read from the clock so a replay and a
        live session run the same code — the same reason `scanner.run_day` takes
        its bars rather than fetching them.
        """
        open_symbols = open_symbols or set()
        cutoff, eod_close, _ = session_times(now.date(), self.profile)
        session_open = self.in_session(now)
        eod = now.time() >= eod_close

        self.halts.update(now, self.feed.halted(now), open_symbols)

        if not session_open:
            empty = screen([], {}, self.cfg)
            return CycleResult(now, empty, [], {}, session_open=False, eod=eod)

        quotes = self.snapshot_fn(now)
        facts = self.feed.facts([q.symbol for q in quotes])
        summary = screen(quotes, facts, self.cfg)
        self.last_summary = summary

        tradeable: list[str] = []
        blocked: dict[str, str] = {}
        past_cutoff = now.time() >= cutoff

        for row in summary.rows:
            if not row.passed:
                continue
            if past_cutoff:
                blocked[row.symbol] = "entry_cutoff"
                continue
            reason = self.halts.blocks_entry(row.symbol, now)
            if reason is not None:
                blocked[row.symbol] = reason
                continue
            tradeable.append(row.symbol)

        return CycleResult(now, summary, tradeable, blocked, session_open=True, eod=eod)

    def watchlist_document(self, now: pd.Timestamp) -> dict | None:
        """The session's screen funnel, ready for `mt_us_watchlist`."""
        if self.last_summary is None:
            return None
        doc = watchlist_doc(self.last_summary, now.date().isoformat(), self.profile.code)
        doc["halted"] = sorted(self.halts.halted)
        doc["held_through_halt"] = sorted(self.halts.held_through_halt)
        doc["early_close"] = is_early_close(now.date())
        # What the feed covered and what it did not (prefilters, stale quotes
        # dropped, delayed quotes, whether the halt feed was reachable). A
        # watchlist that cannot say which screen produced it is ambiguous.
        describe = getattr(self.feed, "describe", None)
        doc["feed"] = describe() if callable(describe) else None
        return doc
