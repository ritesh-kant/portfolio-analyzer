"""MongoDB collections for the news-trader module.

Collections
-----------
nt_news_raw   — raw ingested articles (TTL 90 days); deduplicated on topic_hash
nt_signals    — classifier output (every signal, actionable or not)
nt_positions  — open + closed paper/live positions with trailing SL state
"""

from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorCollection


def news_raw(db: AsyncIOMotorDatabase) -> AsyncIOMotorCollection:  # type: ignore[type-arg]
    return db["nt_news_raw"]


def signals(db: AsyncIOMotorDatabase) -> AsyncIOMotorCollection:  # type: ignore[type-arg]
    return db["nt_signals"]


def positions(db: AsyncIOMotorDatabase) -> AsyncIOMotorCollection:  # type: ignore[type-arg]
    return db["nt_positions"]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:  # type: ignore[type-arg]
    # Unique dedup on topic_hash so concurrent ingester invocations are safe
    await news_raw(db).create_index("topic_hash", unique=True, background=True)
    # TTL: expire raw articles after 90 days (long enough to replay classifier on old signals)
    # NOTE: changing this on an existing collection requires dropping and recreating the index.
    await news_raw(db).create_index(
        "ingested_at", expireAfterSeconds=90 * 24 * 3600, background=True
    )
    await signals(db).create_index("news_id", background=True)
    await signals(db).create_index("created_at", background=True)
    await positions(db).create_index(
        [("symbol", 1), ("status", 1)], background=True
    )
    await positions(db).create_index("status", background=True)
    await positions(db).create_index("entry_at", background=True)
