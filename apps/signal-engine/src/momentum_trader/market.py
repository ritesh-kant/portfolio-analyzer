"""One place that holds everything which differs between NSE and US.

Why this exists
---------------
The strategy itself is market-neutral: `setups.py`, `levels.py`, `indicators.py`
and `exits.py` are arithmetic on open/high/low/close/volume and would run
unchanged on any exchange. What is NOT neutral is a scattered set of constants
and helper choices — session clocks, tick size, the currency a number is in,
which cost function applies, what a "halt" even means. Before this file those
lived inline in `engine.py`, `scanner.py`, `setups.py` and `bars.py`, which made
two failure modes possible:

  1. a US path silently inheriting an NSE constant (a 15:15 close, a 0.3%
     stop floor, a rupee cost model), and
  2. an NSE constant being "generalised" during US work and quietly changing
     the behaviour of a system that is trading live.

Both are avoided by stating the two profiles side by side and proving the NSE
one still matches the constants the live code actually uses. See
`tests/momentum_trader/test_market.py`, which asserts exactly that — this module
is a seam, not a rewrite, and introducing it changes no NSE behaviour.

⚠️ Adding a field here means deciding its value for BOTH markets. That is the
point: a field that only makes sense for one market is a field whose absence
from the other is now visible rather than assumed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, time
from typing import Literal

from ..news_trader.market_calendar import is_trading_day as nse_is_trading_day
from ..news_trader.trailing_sl import calc_costs as nse_calc_costs
from . import us_costs, us_risk
from .risk import plan_trade as nse_plan_trade
from .us_calendar import is_trading_day as us_is_trading_day

HaltStyle = Literal["circuit_band", "luld"]


@dataclass(frozen=True)
class MarketProfile:
    """Everything the shared engine needs to know about one exchange."""

    # ── identity ────────────────────────────────────────────────────────────
    code: str
    name: str
    timezone: str
    currency_symbol: str
    currency_code: str

    # ── session clock, all in `timezone` ────────────────────────────────────
    process_start: tuple[int, int]
    """When the scanner process wakes, before the session, to build its universe."""

    session_start: time
    """First bar of the continuous session. `setups.SESSION_OPEN` on NSE."""

    session_end: time
    """Last bar of the continuous session."""

    process_end: tuple[int, int]
    """When the scanner process exits."""

    entry_cutoff: time
    """No NEW entries from this bar onward."""

    eod_close: time
    """Bar-driven forced exit: the bar STARTING at this time is the last one."""

    eod_sweep: tuple[int, int]
    """Wall-clock backstop for `eod_close`. A symbol that stops printing before
    the close is invisible to the bar-driven path and would otherwise carry
    overnight — the bug found and fixed on 2026-09-13."""

    peak_hours_end: time
    """Optional gate: no new entries after this. See `peak_hours_start`."""

    peak_hours_start: time | None
    """Start of the guide's peak window, or None to mean 'from the open'.
    Only the US has a value, because only the US has a continuous pre-market."""

    trades_premarket: bool
    """Whether this arm acts on bars before `session_start`."""

    # ── microstructure ──────────────────────────────────────────────────────
    tick_size: float
    halt_style: HaltStyle
    """`circuit_band` = price LOCKS and there are no sellers, so a band-locked
    name cannot be filled at all (NSE). `luld` = trading PAUSES for five minutes
    and then resumes, so the position still exists and still has risk (US).
    These need opposite handling: one is an exclusion, the other is a state."""

    orb_minutes: int

    # ── money ───────────────────────────────────────────────────────────────
    round_trip_cost: Callable[[float, float, int], float]
    """(entry, exit, qty) -> total round-trip cost in `currency_code`."""

    plan_trade: Callable[..., object]
    """Market's own risk planner. The two differ in more than currency: NSE
    gates on a percentage stop band, the US on cost-over-risk. See us_risk."""

    # ── data ────────────────────────────────────────────────────────────────
    is_trading_day: Callable[[date | None], bool]
    positions_collection: str
    candidates_collection: str
    watchlist_collection: str

    @property
    def money_suffix(self) -> str:
        """Field suffix used on stored documents: `inr` or `usd`. Kept explicit
        so a US document can never be summed into a rupee total by accident."""
        return self.currency_code.lower()


def _nse_round_trip(entry: float, exit_px: float, qty: int) -> float:
    return float(nse_calc_costs(entry, exit_px, qty, direction="long")["total"])


def _us_round_trip(entry: float, exit_px: float, qty: int) -> float:
    """The US model prices a round trip at one level (see us_costs.calc_costs);
    `exit_px` is accepted for signature parity and deliberately unused."""
    del exit_px
    return us_costs.calc_costs(entry, qty).total


# The NSE values below are COPIES of the constants the live code already uses.
# They are not a new configuration and must not be edited to "improve" anything;
# test_market.py fails if they drift from engine.py / scanner.py / setups.py.
NSE = MarketProfile(
    code="NSE",
    name="National Stock Exchange of India",
    timezone="Asia/Kolkata",
    currency_symbol="₹",
    currency_code="INR",
    process_start=(9, 5),
    session_start=time(9, 15),
    session_end=time(15, 30),
    process_end=(15, 35),
    entry_cutoff=time(14, 30),
    eod_close=time(15, 14),
    eod_sweep=(15, 16),
    peak_hours_end=time(11, 0),
    peak_hours_start=None,
    trades_premarket=False,     # the 09:00-09:08 pre-open is a single auction print
    tick_size=0.05,
    halt_style="circuit_band",
    orb_minutes=15,
    round_trip_cost=_nse_round_trip,
    plan_trade=nse_plan_trade,  # the pre-existing planner, not a rewrite
    is_trading_day=nse_is_trading_day,
    positions_collection="mt_positions",
    candidates_collection="mt_candidates",
    watchlist_collection="mt_universe",
)

US = MarketProfile(
    code="US",
    name="US equities (NASDAQ / NYSE / AMEX)",
    timezone="America/New_York",
    currency_symbol="$",
    currency_code="USD",
    process_start=(9, 20),
    session_start=time(9, 30),
    session_end=time(16, 0),
    process_end=(16, 20),
    # NSE stops new entries 45 minutes before its forced close. The same margin
    # against a 15:55 exit puts the US cutoff at 15:10. Nothing about the US
    # requires an intraday close at all (there is no MIS square-off), but this
    # arm is intraday by construction, so it keeps the rule and states it.
    entry_cutoff=time(15, 10),
    eod_close=time(15, 54),     # bar starting 15:54 closes at 15:55
    eod_sweep=(15, 56),
    # The guide's stated window is 07:00-10:00, i.e. two and a half hours of
    # pre-market plus the first 30 minutes of the regular session. This is the
    # ONE constant the NSE arm could not translate at all (engine.py:125 records
    # why: NSE has no continuous pre-market). It is recorded literally here, but
    # `trades_premarket` is False for v1 — acting on pre-market bars is a
    # separate decision with its own liquidity, routing and LULD differences,
    # and taking it silently as part of a refactor would be exactly the kind of
    # untested change this file exists to prevent.
    peak_hours_start=time(7, 0),
    peak_hours_end=time(10, 0),
    trades_premarket=False,
    tick_size=0.01,
    halt_style="luld",
    orb_minutes=15,
    round_trip_cost=_us_round_trip,
    plan_trade=us_risk.plan_trade,
    is_trading_day=us_is_trading_day,
    positions_collection="mt_us_positions",
    candidates_collection="mt_us_candidates",
    watchlist_collection="mt_us_watchlist",
)



PROFILES: dict[str, MarketProfile] = {NSE.code: NSE, US.code: US}


def profile(code: str) -> MarketProfile:
    """Look up a profile by code. Raises rather than defaulting to NSE — a
    typo'd market must not quietly trade Indian rules on US names."""
    try:
        return PROFILES[code.upper()]
    except KeyError:
        raise ValueError(
            f"unknown market {code!r}; expected one of {sorted(PROFILES)}"
        ) from None
