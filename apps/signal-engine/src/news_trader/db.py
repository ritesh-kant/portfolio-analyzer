"""MongoDB collections for the news-trader module.

Collections
-----------
nt_news_raw   — raw ingested articles (TTL 90 days); deduplicated on topic_hash
                (one row per source, preserving source-count as a future feature)
nt_signals    — classifier output; deduplicated on (story_hash, window_bucket) so
                the same story from multiple feeds produces exactly one signal per
                hour-bucket. source_count tracks how many raw articles fed the signal.
nt_positions  — open + closed paper/live positions with trailing SL state
"""

from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorCollection


def news_raw(db: AsyncIOMotorDatabase) -> AsyncIOMotorCollection:
    return db["nt_news_raw"]


def signals(db: AsyncIOMotorDatabase) -> AsyncIOMotorCollection:
    return db["nt_signals"]


def positions(db: AsyncIOMotorDatabase) -> AsyncIOMotorCollection:
    return db["nt_positions"]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    # Unique dedup on topic_hash so concurrent ingester invocations are safe
    await news_raw(db).create_index("topic_hash", unique=True, background=True)
    # TTL: expire raw articles after 90 days (long enough to replay classifier on old signals)
    # NOTE: changing this on an existing collection requires dropping and recreating the index.
    await news_raw(db).create_index(
        "ingested_at", expireAfterSeconds=90 * 24 * 3600, background=True
    )
    # Cross-source dedup: one signal per (story, hour-bucket). Unique so the
    # classifier can rely on an upsert race-free even with concurrent SQS workers.
    await signals(db).create_index(
        [("story_hash", 1), ("window_bucket", 1)],
        unique=True,
        background=True,
    )
    # Reverse lookup: find which signal absorbed a given raw news doc.
    await signals(db).create_index("news_ids", background=True)
    await signals(db).create_index("created_at", background=True)
    await positions(db).create_index(
        [("symbol", 1), ("status", 1)], background=True
    )
    await positions(db).create_index("status", background=True)
    await positions(db).create_index("entry_at", background=True)
