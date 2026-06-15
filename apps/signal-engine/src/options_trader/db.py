"""MongoDB collections for the options-trader module.

All collection names use the opt_* prefix — completely separate from nt_*.

opt_chain_snapshots — option chain data logged every 3 min per open signal
opt_paper_positions — paper short-straddle positions
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase


def chain_snapshots(db: AsyncIOMotorDatabase) -> AsyncIOMotorCollection:
    return db["opt_chain_snapshots"]


async def latest_real_quote(
    db: AsyncIOMotorDatabase, symbol: str, max_age_minutes: int
) -> dict[str, Any] | None:
    """Most recent NSE (iv_source="nse") chain snapshot for ``symbol`` within
    ``max_age_minutes``, or None.

    This is the single source of real ATM IV (for entry pricing) and real
    bid/ask half-spread (for exit slippage). Returns None whenever the NSE chain
    fetch has not succeeded recently — which, as of 2026-06-15, is *always*: the
    fetch is blocked from Lambda and every snapshot logged so far is synthetic.
    Callers must degrade gracefully (baseline IV / fallback slippage rate).
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=max_age_minutes)
    return await chain_snapshots(db).find_one(
        {
            "symbol": symbol,
            "iv_source": "nse",
            "snapshot_at": {"$gte": cutoff},
            "ce_bid": {"$exists": True},
        },
        sort=[("snapshot_at", -1)],
    )


def paper_positions(db: AsyncIOMotorDatabase) -> AsyncIOMotorCollection:
    return db["opt_paper_positions"]


def eod_markers(db: AsyncIOMotorDatabase) -> AsyncIOMotorCollection:
    """Once-per-day EOD-summary guard. _id is the IST date string (natural
    uniqueness), so a duplicate-key insert means the summary already fired."""
    return db["opt_eod_markers"]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await chain_snapshots(db).create_index(
        [("signal_id", 1), ("snapshot_at", 1)], background=True
    )
    await chain_snapshots(db).create_index("symbol", background=True)
    # TTL: keep chain logs for 180 days (enough for one full backtest window).
    # This index also serves range queries on snapshot_at, so no separate
    # plain index is needed — a second index on the same key would conflict.
    await chain_snapshots(db).create_index(
        "snapshot_at", expireAfterSeconds=180 * 24 * 3600, background=True, name="ttl_snapshot_at"
    )
    await paper_positions(db).create_index(
        [("symbol", 1), ("status", 1)], background=True
    )
    await paper_positions(db).create_index("status", background=True)
    await paper_positions(db).create_index("entry_at", background=True)
    # Prevent duplicate positions for the same signal + symbol
    await paper_positions(db).create_index(
        [("signal_id", 1), ("symbol", 1)], unique=True, background=True
    )
    # Prevent two open straddles on the same symbol (e.g. two signals from one
    # news event). partialFilterExpression limits uniqueness to open docs only,
    # so historical closed positions accumulate freely.
    await paper_positions(db).create_index(
        [("symbol", 1)],
        unique=True,
        partialFilterExpression={"status": "open"},
        background=True,
        name="symbol_1_open_unique",
    )
