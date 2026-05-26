"""Lambda: news-ingester — EventBridge every 5 min during market hours.

Fetches RSS feeds + NSE/BSE announcements, deduplicates via topic_hash unique
index, persists new articles to nt_news_raw, and enqueues each new article ID
to the news-raw SQS queue for the classifier.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import boto3
from pymongo.errors import BulkWriteError

from src.config import Settings
from src.db.client import get_db
from src.news_trader.db import ensure_indexes, news_raw
from src.scrapers.bse import fetch_bse_announcements
from src.scrapers.nse import fetch_nse_announcements
from src.scrapers.rss import fetch_all_rss

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_IST_OFFSET = 5.5 * 3600  # seconds


def _is_market_hours() -> bool:
    """True if current IST time is 09:00–15:35 on a weekday."""
    now_ist = datetime.fromtimestamp(
        datetime.now(tz=timezone.utc).timestamp() + _IST_OFFSET
    )
    if now_ist.weekday() >= 5:
        return False
    total_minutes = now_ist.hour * 60 + now_ist.minute
    return 9 * 60 <= total_minutes <= 15 * 60 + 35


async def _run(settings: Settings) -> dict:
    if not _is_market_hours():
        logger.info("ingester_skipped outside_market_hours")
        return {"skipped": "outside_market_hours"}

    db = get_db()
    await ensure_indexes(db)

    rss_articles, feed_health = await fetch_all_rss()
    nse_articles = await fetch_nse_announcements()
    bse_articles = await fetch_bse_announcements()

    all_articles = rss_articles + nse_articles + bse_articles
    now = datetime.now(tz=timezone.utc)
    for a in all_articles:
        a["ingested_at"] = now
        a["classified"] = False

    if not all_articles:
        return {"new_articles": 0, "feed_health": feed_health}

    # Bulk insert — ignore duplicates (unique index on topic_hash)
    inserted_ids: list[str] = []
    try:
        result = await news_raw(db).insert_many(all_articles, ordered=False)
        inserted_ids = [str(i) for i in result.inserted_ids]
    except BulkWriteError as bwe:
        # Extract IDs that were actually inserted (not the duplicates)
        inserted_ids = [
            str(r["_id"]) for r in bwe.details.get("writeErrors", [])
            # writeErrors are the failures; reconstruct from details
        ]
        # Simpler: count from the error details
        n_inserted = bwe.details.get("nInserted", 0)
        logger.info("ingester_bulk_write new=%d duplicates_skipped=%d", n_inserted,
                    len(bwe.details.get("writeErrors", [])))
        # Re-query for the actually inserted IDs
        if n_inserted > 0:
            cursor = news_raw(db).find(
                {"ingested_at": now, "classified": False},
                {"_id": 1},
            )
            inserted_ids = [str(doc["_id"]) async for doc in cursor]

    if not inserted_ids:
        logger.info("ingester_done new=0 total_fetched=%d", len(all_articles))
        return {"new_articles": 0}

    # Enqueue to SQS
    if settings.news_raw_queue_url:
        sqs = boto3.client("sqs")
        for news_id in inserted_ids:
            sqs.send_message(
                QueueUrl=settings.news_raw_queue_url,
                MessageBody=json.dumps({"news_id": news_id}),
            )

    logger.info("ingester_done new=%d enqueued=%d", len(inserted_ids), len(inserted_ids))
    return {"new_articles": len(inserted_ids), "feed_health": feed_health}


def handler(event: dict, context: object) -> dict:
    settings = Settings()
    return asyncio.run(_run(settings))
