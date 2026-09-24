"""Applies the five US criteria to a snapshot and records the funnel.

`us_universe` holds the rules; this holds the bookkeeping around them. It exists
so that whatever feed is wired up later (IBKR `reqScannerSubscription`, a REST
snapshot, a replay file) only has to produce `USQuote` rows, and so that the
answer to "why did nothing qualify today?" is data rather than a log line.

The funnel is the point
-----------------------
A screen that returns an empty list is ambiguous: it can mean the market was
quiet, or that one criterion is rejecting everything because its input is
broken. The NSE arm hit exactly this — criterion 5 was rejecting nothing
because it was never running, and that was only discovered by reading the spec
months later. `ScreenSummary.rejected_by` makes both failure modes visible on
the dashboard the same day.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .us_universe import (
    ScreenResult,
    USNameFacts,
    USUniverseConfig,
    passes_intraday,
    passes_static,
    screen_is_complete,
)


@dataclass(frozen=True)
class USQuote:
    """One name's state at one instant. Everything the demand criteria need."""

    symbol: str
    price: float
    day_chg_pct: float
    rvol: float | None = None
    has_catalyst: bool | None = None
    observed_at: str = ""


@dataclass(frozen=True)
class USScreenRow:
    symbol: str
    passed: bool
    reason: str
    """`ok`, or the first criterion that rejected it: price, day_chg, rvol,
    catalyst, catalyst_unavailable, float, exchange:<venue>."""

    screen_complete: bool
    """True only when all five criteria actually ran. A row that passed an
    incomplete screen has NOT passed the guide's screen."""

    flags: dict[str, int] = field(default_factory=dict)
    price: float = 0.0
    day_chg_pct: float = 0.0
    rvol: float | None = None
    float_shares: float | None = None
    observed_at: str = ""


@dataclass(frozen=True)
class ScreenSummary:
    considered: int
    passed: int
    complete: int
    """How many PASSING rows ran all five criteria. `complete` below `passed`
    means the arm is running a weaker screen than the guide's and any result
    from it has to say so."""

    rejected_by: dict[str, int]
    rows: list[USScreenRow]

    @property
    def missing_criteria(self) -> list[str]:
        """Which criteria never ran on any passing row — the honest header for
        a dashboard or a forward-capture note."""
        if not self.rows:
            return []
        names = {
            "float_filter_applied": "5. float under 10M",
            "rvol_filter_applied": "1. 5x relative volume",
            "catalyst_filter_applied": "3. news catalyst",
        }
        passing = [r for r in self.rows if r.passed]
        if not passing:
            return []
        return [
            label
            for key, label in names.items()
            if not any(r.flags.get(key) == 1 for r in passing)
        ]


def screen_one(
    quote: USQuote,
    facts: USNameFacts | None,
    cfg: USUniverseConfig,
) -> USScreenRow:
    """Static criteria first: they are the cheap ones and the ones that do not
    change during the session, so a name rejected on float never needs its
    intraday state evaluated again."""
    static: ScreenResult = passes_static(facts, cfg)
    if not static.ok:
        return USScreenRow(
            symbol=quote.symbol,
            passed=False,
            reason=static.reason,
            screen_complete=False,
            flags=dict(static.flags),
            price=quote.price,
            day_chg_pct=quote.day_chg_pct,
            rvol=quote.rvol,
            float_shares=facts.float_shares if facts else None,
            observed_at=quote.observed_at,
        )

    intraday = passes_intraday(
        quote.price, quote.day_chg_pct, quote.rvol, cfg, quote.has_catalyst
    )
    flags = {**static.flags, **intraday.flags}
    return USScreenRow(
        symbol=quote.symbol,
        passed=intraday.ok,
        reason=intraday.reason,
        screen_complete=intraday.ok and screen_is_complete(static, intraday),
        flags=flags,
        price=quote.price,
        day_chg_pct=quote.day_chg_pct,
        rvol=quote.rvol,
        float_shares=facts.float_shares if facts else None,
        observed_at=quote.observed_at,
    )


def screen(
    quotes: list[USQuote],
    facts: dict[str, USNameFacts] | None = None,
    cfg: USUniverseConfig | None = None,
) -> ScreenSummary:
    """Run the screen over a snapshot and keep every rejection."""
    cfg = cfg or USUniverseConfig()
    facts = facts or {}
    rows = [screen_one(q, facts.get(q.symbol), cfg) for q in quotes]
    rejected = Counter(r.reason for r in rows if not r.passed)
    return ScreenSummary(
        considered=len(rows),
        passed=sum(1 for r in rows if r.passed),
        complete=sum(1 for r in rows if r.passed and r.screen_complete),
        rejected_by=dict(rejected),
        rows=rows,
    )


def watchlist_doc(summary: ScreenSummary, session_date: str, market: str = "US") -> dict:
    """Mongo document for `mt_us_watchlist`, one per snapshot.

    Stores the rejections as well as the survivors. A watchlist that only keeps
    what passed cannot answer whether the screen was working.
    """
    return {
        "market": market,
        "date": session_date,
        "considered": summary.considered,
        "passed": summary.passed,
        "complete": summary.complete,
        "rejected_by": summary.rejected_by,
        "missing_criteria": summary.missing_criteria,
        "names": [
            {
                "symbol": r.symbol,
                "passed": r.passed,
                "reason": r.reason,
                "screen_complete": r.screen_complete,
                "price": r.price,
                "day_chg_pct": r.day_chg_pct,
                "rvol": r.rvol,
                "float_shares": r.float_shares,
                "flags": r.flags,
                "observed_at": r.observed_at,
            }
            for r in summary.rows
        ],
    }
