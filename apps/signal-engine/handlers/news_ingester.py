"""Lambda: news-ingester — EventBridge every 5 min during market hours.

Fetches RSS feeds + NSE/BSE announcements, deduplicates via topic_hash unique
index, persists new articles to nt_news_raw, and enqueues each new article ID
to the news-raw SQS queue for the classifier.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import boto3
from pymongo.errors import BulkWriteError

from src.config import Settings
from src.db.client import get_db
from src.news_trader.db import ensure_indexes, news_raw
from src.news_trader.market_calendar import is_trading_day
from src.scrapers.bse import fetch_bse_announcements
from src.scrapers.nse import fetch_nse_announcements
from src.scrapers.rss import fetch_all_rss

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

_IST_OFFSET = 5.5 * 3600  # seconds


def _is_market_hours(bypass_holiday: bool = False) -> bool:
    """True if current IST time is 09:00–15:35 on a trading day (no weekends, no NSE holidays).

    bypass_holiday=True skips the holiday check but never bypasses weekends.
    """
    now_ist = datetime.fromtimestamp(
        datetime.now(tz=timezone.utc).timestamp() + _IST_OFFSET
    )
    if now_ist.weekday() >= 5:
        return False
    if not bypass_holiday and not is_trading_day(now_ist.date()):
        return False
    total_minutes = now_ist.hour * 60 + now_ist.minute
    return 9 * 60 <= total_minutes <= 15 * 60 + 35


async def _run(settings: Settings, run_id: str | None = None) -> dict[str, Any]:
    if not _is_market_hours(bypass_holiday=settings.nt_bypass_market_holiday):
        if settings.nt_bypass_market_hours:
            logger.info("[INGESTER] market hours check bypassed (NT_BYPASS_MARKET_HOURS=true)")
        else:
            logger.info("[INGESTER] skipped — outside market hours")
            return {"skipped": "outside_market_hours"}
    elif settings.nt_bypass_market_holiday:
        logger.info("[INGESTER] holiday check bypassed (NT_BYPASS_MARKET_HOLIDAY=true)")

    logger.info("[INGESTER] starting run")
    db = get_db()
    await ensure_indexes(db)

    logger.info("[INGESTER] fetching RSS, NSE and BSE concurrently...")
    (rss_articles, feed_health), nse_articles, bse_articles = await asyncio.gather(
        fetch_all_rss(),
        fetch_nse_announcements(),
        fetch_bse_announcements(),
    )
    logger.info(
        "[INGESTER] fetch done — rss=%d (%d feeds, %d failed) nse=%d bse=%d",
        len(rss_articles), len(feed_health),
        sum(1 for ok in feed_health.values() if not ok),
        len(nse_articles), len(bse_articles),
    )

    all_articles = rss_articles + nse_articles + bse_articles
    logger.info("[INGESTER] total fetched=%d (rss=%d nse=%d bse=%d) — deduplicating...",
                len(all_articles), len(rss_articles), len(nse_articles), len(bse_articles))

    now = datetime.now(tz=timezone.utc)
    for a in all_articles:
        a["ingested_at"] = now
        a["classified"] = False

    if not all_articles:
        logger.info("[INGESTER] done — 0 articles fetched from all sources")
        return {"new_articles": 0, "feed_health": feed_health}

    # Bulk insert — ignore duplicates (unique index on topic_hash)
    inserted_ids: list[str] = []
    try:
        result = await news_raw(db).insert_many(all_articles, ordered=False)
        inserted_ids = [str(i) for i in result.inserted_ids]
        logger.info("[INGESTER] inserted %d new articles (0 duplicates)", len(inserted_ids))
    except BulkWriteError as bwe:
        n_inserted = bwe.details.get("nInserted", 0)
        n_dupes = len(bwe.details.get("writeErrors", []))
        logger.info("[INGESTER] inserted %d new articles, %d duplicates skipped", n_inserted, n_dupes)
        if n_inserted > 0:
            cursor = news_raw(db).find(
                {"ingested_at": now, "classified": False},
                {"_id": 1},
            )
            inserted_ids = [str(doc["_id"]) async for doc in cursor]

    if not inserted_ids:
        logger.info("[INGESTER] done — all %d articles were duplicates, nothing new to enqueue",
                    len(all_articles))
        return {"new_articles": 0}

    # Enqueue to SQS — send_message_batch cuts N HTTP calls to ceil(N/10)
    if settings.news_raw_queue_url:
        logger.info("[INGESTER] enqueuing %d new articles to SQS...", len(inserted_ids))
        sqs = boto3.client("sqs")
        total_sent = 0
        for batch_start in range(0, len(inserted_ids), 10):
            batch = inserted_ids[batch_start:batch_start + 10]
            entries: list[dict[str, Any]] = []
            for i, news_id in enumerate(batch):
                entry: dict[str, Any] = {
                    "Id": str(i),
                    "MessageBody": json.dumps({"news_id": news_id}),
                }
                if run_id:
                    entry["MessageAttributes"] = {
                        "run_id": {"DataType": "String", "StringValue": run_id}
                    }
                entries.append(entry)
            resp = sqs.send_message_batch(
                QueueUrl=settings.news_raw_queue_url, Entries=entries
            )
            total_sent += len(resp.get("Successful", []))
            for failure in resp.get("Failed", []):
                logger.warning(
                    "[INGESTER] SQS batch failure Id=%s Code=%s Message=%s",
                    failure["Id"], failure["Code"], failure["Message"],
                )
        logger.info("[INGESTER] enqueued %d messages", total_sent)
    else:
        logger.warning("[INGESTER] NEWS_RAW_QUEUE_URL not set — skipping SQS enqueue")

    logger.info("[INGESTER] done — new=%d", len(inserted_ids))
    return {"new_articles": len(inserted_ids), "feed_health": feed_health}


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    settings = Settings()
    run_id: str | None = event.get("run_id")

    # EventBridge invocations carry no run_id — create our own so the status panel
    # and history tab track scheduled runs just like manual ones.
    # Skip creating a record if we're clearly outside market hours to avoid DB noise
    # from the 5-min EventBridge tick running all day.
    if not run_id:
        will_process = (
            _is_market_hours(bypass_holiday=settings.nt_bypass_market_holiday)
            or settings.nt_bypass_market_hours
        )
        if will_process:
            from src.news_trader.pipeline_lifecycle import insert_run
            run_id = asyncio.run(insert_run("scheduled"))

    try:
        result = asyncio.run(_run(settings, run_id=run_id))
        if run_id:
            from src.news_trader.pipeline_lifecycle import delete_run, finalise_run, stamp_processing_done
            if result.get("skipped"):
                asyncio.run(delete_run(run_id))
            else:
                asyncio.run(stamp_processing_done(run_id, result.get("new_articles", 0)))
                if result.get("new_articles", 0) == 0:
                    # No articles to enqueue — trade_decision will never run, finalize here
                    asyncio.run(finalise_run(run_id, result))
        return result
    except Exception as exc:
        if run_id:
            from src.news_trader.pipeline_lifecycle import fail_run
            asyncio.run(fail_run(run_id, str(exc)))
        raise
