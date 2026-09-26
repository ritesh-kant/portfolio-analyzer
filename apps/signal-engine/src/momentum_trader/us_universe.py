"""US universe filter — the Warrior guide's five criteria, applied literally.

This is deliberately SEPARATE from `universe.py` rather than a generalisation of
it. The NSE module encodes compromises (free-float mcap in crore, promoter
holding, circuit bands, ASM/GSM surveillance) that exist only because NSE could
not supply the guide's actual criteria. Folding both into one module would bury
that difference; keeping them apart keeps each one honest about what it is.

The guide's screen, unchanged:

    1. Demand: 5x relative volume
    2. Demand: already up 10% on the day
    3. Demand: a news event moving the stock higher
    4. Demand: price $1.00 - $20.00
    5. Supply: less than 10 million shares available to trade (float)

Why this module can take them literally where `universe.py` could not
---------------------------------------------------------------------
Per `research/specs/warrior-patterns-nse.md` §1, every NSE deviation was forced
by a market-structure fact, not by a research finding:

  * criterion 2 became "+4% to +8%" because most NSE names sit in a 10% or 20%
    circuit band, so +10% is locked or adjacent to locked. US equities have no
    price band — LULD pauses trading for five minutes but never locks a price —
    so +10% is directly usable.
  * criterion 5 was replaced wholesale. A live sample on 2026-09-05 found
    **0 of 120** NIFTY 500 names with under 10M shares outstanding in the
    Rs 60-2,000 band. The supply side of the thesis was not weakened on NSE, it
    was ABSENT; promoter holding was substituted as the nearest available lever.
  * criterion 3 has never had a data source (the catalyst gate has been dark
    since 2026-06-26 and is not testable retroactively).

So the NSE arm tested a five-criteria screen with one criterion missing, one
substituted and one bent. Whatever that arm measured, it was not this screen.

Two criteria still need data this repo does not yet have
-------------------------------------------------------
`float_shares` and `has_catalyst` both depend on external feeds. Following the
same convention as `universe.py`, a missing input does NOT silently drop names:
the filter is skipped and the decision is flagged, so the forward log can tell
the two regimes apart instead of pooling them.

⚠️ Point-in-time hazard: a float figure pulled from a "current snapshot" API
(IBKR `ReportSnapshot`, most screeners) is TODAY's float. Applying it to a past
date is look-ahead — a company that issued shares later will look tighter than
it was. For backtests the float must be as-of the trade date or the filter must
be declared inactive. Forward capture has no such problem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── the guide's numbers, verbatim ───────────────────────────────────────────
PRICE_MIN_USD, PRICE_MAX_USD = 1.00, 20.00
DAY_CHG_MIN_PCT = 10.0
FLOAT_MAX_SHARES = 10_000_000
RVOL_MIN = 5.0

# The guide says "5x relative volume" without defining the denominator. The NSE
# work established that a full-day average understates a mid-session spike by
# roughly 4x, so 5x naive ~= 3x on a time-of-day measure
# (`indicators.relative_volume_by_time`). That factor was calibrated on NSE's
# intraday volume shape and does NOT automatically transfer: the US session has
# a far heavier open and a real pre-market, so the curve is a different shape.
# `rvol_is_time_of_day` therefore states which measure the threshold refers to,
# and the equivalence must be re-derived on US bars before the two arms are
# compared. Getting this wrong silently changes the strictness of criterion 1.
RVOL_MIN_TIME_OF_DAY = 3.0


@dataclass(frozen=True)
class USUniverseConfig:
    price_min: float = PRICE_MIN_USD
    price_max: float = PRICE_MAX_USD
    day_chg_min_pct: float = DAY_CHG_MIN_PCT
    float_max_shares: int = FLOAT_MAX_SHARES
    rvol_min: float = RVOL_MIN
    rvol_is_time_of_day: bool = False
    require_catalyst: bool = False
    """Criterion 3 is OFF by default because no news feed is wired up yet.
    Turning it on without a source would reject every name."""

    allowed_exchanges: tuple[str, ...] = ("NASDAQ", "NYSE", "AMEX", "ARCA", "BATS")
    """OTC / pink sheets are excluded: they are where the guide's screen finds
    its worst fills, and IBKR routing and borrow there are a different problem."""

    side: str = "long"
    """"short" screens for WEAKNESS: down at least `day_chg_min_pct`. The short
    arm does not use the long's 10%: at -10% SEC Rule 201 (SSR) forbids the
    breakdown sale the short setups make - see short_side.py."""


@dataclass(frozen=True)
class USNameFacts:
    symbol: str
    float_shares: float | None = None
    exchange: str = ""
    as_of: str = ""
    """Date the float figure describes. Empty means unknown, which is the
    look-ahead hazard described in the module docstring."""


@dataclass
class ScreenResult:
    ok: bool
    reason: str
    flags: dict[str, int] = field(default_factory=dict)
    """`*_applied` = 1 when a criterion was actually evaluated, 0 when its input
    was missing and the criterion was skipped. Written to the forward log so a
    'passed' row is never ambiguous about WHICH screen it passed."""


def passes_static(facts: USNameFacts | None, cfg: USUniverseConfig) -> ScreenResult:
    """Criterion 5 (supply) and the exchange check.

    Known before the session opens, so this is what narrows the watchlist.
    """
    flags = {"float_filter_applied": 0, "exchange_filter_applied": 0}
    if facts is None:
        return ScreenResult(True, "no_facts", flags)

    if facts.exchange:
        flags["exchange_filter_applied"] = 1
        if facts.exchange.upper() not in cfg.allowed_exchanges:
            return ScreenResult(False, f"exchange:{facts.exchange}", flags)

    if facts.float_shares is not None:
        flags["float_filter_applied"] = 1
        if facts.float_shares >= cfg.float_max_shares:
            return ScreenResult(False, "float", flags)
    else:
        logger.debug("%s: no float figure — criterion 5 skipped", facts.symbol)

    return ScreenResult(True, "ok", flags)


def passes_intraday(
    price: float,
    day_chg_pct: float,
    rvol: float | None,
    cfg: USUniverseConfig,
    has_catalyst: bool | None = None,
) -> ScreenResult:
    """Criteria 1-4 (demand), evaluated DURING the session.

    This is the structural difference from `universe.passes_dynamic`, which runs
    once at startup against yesterday's close. Three of the guide's four demand
    criteria describe what a stock is doing *right now*, so a US name is not in
    or out of the universe for the day — it becomes eligible the moment it is up
    10% on 5x volume, and it can become eligible at 14:00.
    """
    flags = {"catalyst_filter_applied": 0, "rvol_filter_applied": 0}

    # 4 — price band
    if not (cfg.price_min <= price <= cfg.price_max):
        return ScreenResult(False, "price", flags)

    # 2 — already up 10% on the day. One-sided: the guide screens for strength,
    # and there is no upper bound, because a stock up 40% is MORE interesting to
    # it, not less. (The NSE arm had an upper bound only because of circuits.)
    # The short arm's mirror: down at least the floor.
    if cfg.side == "short":
        if day_chg_pct > -cfg.day_chg_min_pct:
            return ScreenResult(False, "day_chg", flags)
    elif day_chg_pct < cfg.day_chg_min_pct:
        return ScreenResult(False, "day_chg", flags)

    # 1 — relative volume
    if rvol is not None:
        flags["rvol_filter_applied"] = 1
        threshold = (
            RVOL_MIN_TIME_OF_DAY if cfg.rvol_is_time_of_day else cfg.rvol_min
        )
        if rvol < threshold:
            return ScreenResult(False, "rvol", flags)

    # 3 — news catalyst
    if cfg.require_catalyst:
        if has_catalyst is None:
            return ScreenResult(False, "catalyst_unavailable", flags)
        flags["catalyst_filter_applied"] = 1
        if not has_catalyst:
            return ScreenResult(False, "catalyst", flags)

    return ScreenResult(True, "ok", flags)


def screen_is_complete(static: ScreenResult, intraday: ScreenResult) -> bool:
    """True only when all five criteria were actually evaluated.

    A forward result built from rows where this is False is NOT a test of the
    guide's screen, which is the mistake the NSE arm made structurally.
    """
    return all(
        (
            static.flags.get("float_filter_applied") == 1,
            intraday.flags.get("rvol_filter_applied") == 1,
            intraday.flags.get("catalyst_filter_applied") == 1,
        )
    )
