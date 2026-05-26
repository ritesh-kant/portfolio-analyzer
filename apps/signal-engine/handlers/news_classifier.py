"""Lambda: news-classifier — SQS trigger from news-raw queue.

For each message:
  1. Load article from nt_news_raw
  2. Call Gemini 1.5 Flash for structured classification
  3. Save signal to nt_signals
  4. If confidence is high/medium: enqueue to news-signals queue with
     nt_news_delay_seconds delay so trade_decision fires 15 min later
  5. Mark article as classified
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import boto3
from bson import ObjectId

from src.config import Settings
from src.db.client import get_db
from src.news_trader.classifier import classify
from src.news_trader.db import ensure_indexes, news_raw, signals

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_ACTIONABLE_CONFIDENCE = {"high", "medium"}


async def _process_message(msg: dict, settings: Settings) -> bool:
    """Returns True if a signal was created and enqueued."""
    db = get_db()
    news_id = msg.get("news_id")
    if not news_id:
        logger.warning("classifier_bad_message msg=%s", msg)
        return False

    article = await news_raw(db).find_one({"_id": ObjectId(news_id)})
    if not article:
        logger.warning("classifier_article_not_found news_id=%s", news_id)
        return False

    if article.get("classified"):
        logger.debug("classifier_already_classified news_id=%s", news_id)
        return False

    raw_text = article.get("raw_text", article.get("headline", ""))
    result = classify(raw_text, settings.gemini_api_key, model=settings.gemini_model)

    if result is None:
        logger.warning("classifier_gemini_failed news_id=%s headline=%.80s",
                       news_id, article.get("headline", ""))
        await news_raw(db).update_one(
            {"_id": ObjectId(news_id)}, {"$set": {"classified": True}}
        )
        return False

    signal_doc = {
        "news_id": news_id,
        "headline": article.get("headline", ""),
        "source": article.get("source", ""),
        "sector": result["sector"],
        "signal": result["signal"],
        "magnitude": result["magnitude"],
        "stocks": result["stocks"],
        "confidence": result["confidence"],
        "reasoning": result["reasoning"],
        "created_at": datetime.now(tz=timezone.utc),
        "acted_on": False,
    }
    insert_result = await signals(db).insert_one(signal_doc)
    signal_id = str(insert_result.inserted_id)

    await news_raw(db).update_one(
        {"_id": ObjectId(news_id)}, {"$set": {"classified": True}}
    )

    logger.info(
        "classifier_done news_id=%s signal=%s confidence=%s stocks=%s",
        news_id, result["signal"], result["confidence"], result["stocks"],
    )

    # Only actionable signals go to the trade-decision queue
    if result["confidence"] not in _ACTIONABLE_CONFIDENCE:
        return False
    if result["signal"] == "neutral":
        return False
    if not result["stocks"]:
        return False

    if settings.news_signals_queue_url:
        sqs = boto3.client("sqs")
        sqs.send_message(
            QueueUrl=settings.news_signals_queue_url,
            MessageBody=json.dumps({"signal_id": signal_id}),
            DelaySeconds=settings.nt_news_delay_seconds,  # 15-min wait before entry
        )
        logger.info("classifier_enqueued signal_id=%s delay=%ds",
                    signal_id, settings.nt_news_delay_seconds)

    return True


async def _run(event: dict, settings: Settings) -> dict:
    db = get_db()
    await ensure_indexes(db)

    records = event.get("Records", [])
    acted = 0
    for record in records:
        try:
            msg = json.loads(record["body"])
            if await _process_message(msg, settings):
                acted += 1
        except Exception as exc:
            logger.error("classifier_record_error err=%s record=%.200s", exc, record)

    return {"processed": len(records), "acted_on": acted}


def handler(event: dict, context: object) -> dict:
    settings = Settings()
    return asyncio.run(_run(event, settings))
